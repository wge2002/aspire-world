"""Opt-in judgment world runtime: the authored world answers queries.

Same execution discipline as :mod:`simple_world` (exact hashed source, a fresh
same-process module per trial, public API wrappers, error accounting), with one
functional difference: the policy may *consume* the world through
``query(name, **kwargs)``, and every such call is recorded with its arguments,
its return value, and its position relative to the existing public API trace.

The runtime defines no relations, thresholds, or verdict vocabulary. Which
queries exist, what they mean, and how uncertainty is expressed are the world
author's decisions; the runtime only checks that a query returned a finite
JSON value and writes down what was asked and answered.

This is an experiment interface, not a Python security sandbox. The authored
module receives no API or environment handles. Policy owns sensing and motion.
"""
from __future__ import annotations

import ast
import functools
import hashlib
import json
from pathlib import Path
import sys

from . import simple_world
from .simple_world import WorldProgramError, summarize

MODE = "opus46-judgment-world"
REQUIRED = ("update", "query", "snapshot")


def module_errors(source: str) -> list[str]:
    """simple_world's checks, plus the query entry point this mode consumes."""
    errors = list(simple_world.module_errors(source))
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return errors
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "query" not in names:
        errors.append("world must define query()")
    return sorted(set(errors))


def json_value(value, label):
    """Accept any finite JSON value; reject NaN/Inf and unserializable objects."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{label} must be a finite JSON value: {exc}") from exc


def caller_site(depth=2):
    """Record where the consumer asked, so a reader can find the use site."""
    try:
        frame = sys._getframe(depth)
    except ValueError:  # pragma: no cover - shallower stack than expected
        return None
    return {"file": frame.f_code.co_filename, "line": frame.f_lineno,
            "function": frame.f_code.co_name}


class JudgmentSession(simple_world.WorldSession):
    """A simple_world session whose authored module is also queried."""

    def __init__(self, source: Path, expected_sha256: str, output: Path):
        super().__init__(source, expected_sha256, output)
        self.queries = 0
        self.query_names = []
        self.trace_logger = None

    def trace_step(self):
        logger = self.trace_logger
        if logger is None or not getattr(logger, "entries", None):
            return None
        return logger.entries[-1].get("step")

    def __enter__(self):
        self.had_previous = "world" in sys.modules
        self.previous = sys.modules.get("world")
        sys.modules["world"] = self.module
        try:
            # Execute the exact hashed bytes; never a potentially stale .pyc.
            exec(compile(self.raw, str(self.source), "exec"), self.module.__dict__)
            if not all(callable(getattr(self.module, n, None)) for n in REQUIRED):
                raise TypeError(
                    "world must expose update(obs, last_action), query(name, **kwargs), snapshot()")
            self.module.update = self._wrap_update(self.module.update)
            self.module.query = self._wrap_query(self.module.query)
            self.emit("world_loaded", source_sha256=self.sha256, snapshot=self.snapshot())
            return self
        except Exception as exc:
            if not self.errors:
                self.fault("load", exc)
            self.close("program_error")
            raise WorldProgramError(str(exc)) from exc

    def _wrap_update(self, original):
        @functools.wraps(original)
        def update(*args, **kwargs):
            try:
                result = original(*args, **kwargs)
            except Exception as exc:
                self.fault("update", exc)
                raise
            self.updates += 1
            self.emit("world_update", inputs=summarize({"args": args, "kwargs": kwargs}),
                      caller=caller_site(), api_calls_before=self.api_calls,
                      api_trace_step=self.trace_step(), snapshot=self.snapshot())
            return result
        return update

    def _wrap_query(self, original):
        @functools.wraps(original)
        def query(*args, **kwargs):
            name = args[0] if args else kwargs.get("name")
            index = self.queries
            self.queries += 1
            self.query_names.append(summarize(name))
            # Position in the existing public API trace is captured before the
            # call, so the log says what the policy already knew when it asked.
            site, before, step = caller_site(), self.api_calls, self.trace_step()

            def record(result=None, error=None):
                self.emit("world_query", query_index=index, name=summarize(name),
                          args=summarize(args[1:]), kwargs=summarize(kwargs),
                          result=result, error=error, caller=site,
                          api_calls_before=before, api_trace_step=step,
                          snapshot=self.snapshot())

            try:
                value = original(*args, **kwargs)
            except Exception as exc:
                record(error=f"{type(exc).__name__}: {exc}")
                self.fault("query", exc)
                raise
            try:
                checked = json_value(value, f"query({name!r})")
            except TypeError as exc:
                record(error=f"{type(exc).__name__}: {exc}")
                self.fault("query_output", exc)
                raise WorldProgramError(str(exc)) from exc
            record(result=checked)
            return value
        return query

    def bind_api(self, api):
        if self.trace_logger is None and hasattr(api, "get_trace_logger"):
            self.trace_logger = api.get_trace_logger()
        super().bind_api(api)

    def attach(self, env):
        from aspire.sim.scripts.libero.replay_trial import _find_apis
        apis = _find_apis(env)
        if not apis:
            raise RuntimeError("judgment world could not attach to public APIs")
        for api in apis.values():
            self.bind_api(api)

    def close(self, status):
        for api, original in reversed(self.restores):
            api.functions = original
        if self.had_previous:
            sys.modules["world"] = self.previous
        else:
            sys.modules.pop("world", None)
        manifest = {"mode": MODE, "status": status, "world_sha256": self.sha256,
                    "api_calls": self.api_calls, "updates": self.updates,
                    "queries": self.queries, "query_names": self.query_names,
                    "errors": self.errors, "result": self.result}
        (self.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.stream.close()


def run_judgment_world(args):
    from aspire.sim.scripts.libero.replay_trial import _run_replay
    config_path = Path(args.world_model_config)
    config = json.loads(config_path.read_text())
    if config.get("mode") != MODE or config.get("task_gate") != {"suite": args.suite, "task": args.task}:
        raise ValueError("judgment-world configuration/task mismatch")
    if not args.replay_code or args.interactive:
        raise ValueError("judgment world requires a recorded policy replay")
    if hashlib.sha256(Path(args.replay_code).read_bytes()).hexdigest() != config["policy_sha256"]:
        raise ValueError("policy source differs from frozen trial bundle")
    source = config_path.parent / config["world_program"]
    with JudgmentSession(source, config["world_program_sha256"],
                         config_path.parent / "judgment_world") as session:
        _run_replay(args, _world_capture=session)
        if session.errors:
            raise WorldProgramError("world errors recorded in judgment_world/events.jsonl")
