"""Ordinary episode-local world code, with observational logging only.

This is an experiment interface, not a Python security sandbox. The authored
module receives no API or environment handles. Policy owns sensing and motion.
"""
from __future__ import annotations

import ast
import functools
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import types

MODE = "opus46-simple-world"


class WorldProgramError(RuntimeError):
    pass


def module_errors(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"world syntax: {exc}"]
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    errors = [f"world must define {name}()" for name in ("update", "snapshot") if name not in names]
    tool_names = {"get_observation", "segment_sam3_text_prompt", "segment_sam3_point_prompt",
                  "plan_grasp", "solve_ik", "move_to_joints", "open_gripper", "close_gripper",
                  "goto_pose", "goto_home_joint_position", "point_prompt_molmo"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in {"env", "APIS"}:
            errors.append("world receives observations, not environment/API handles")
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
            if name in tool_names:
                errors.append(f"world may not call sensing/motion API {name}")
    return sorted(set(errors))


def summarize(value, depth=0):
    """Bound log size, not the program's observations or state representation."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if depth > 6:
        return {"type": type(value).__name__}
    if hasattr(value, "shape") and hasattr(value, "size"):
        return (summarize(value.tolist(), depth + 1) if value.size <= 32 else
                {"shape": list(value.shape), "dtype": str(value.dtype)})
    if isinstance(value, dict):
        return {str(k): summarize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            return {"length": len(value), "first": summarize(value[:4], depth + 1)}
        return [summarize(v, depth + 1) for v in value]
    return {"type": type(value).__name__}


class WorldSession:
    def __init__(self, source: Path, expected_sha256: str, output: Path):
        self.source, self.output = Path(source), Path(output)
        self.raw = self.source.read_bytes()
        self.sha256 = hashlib.sha256(self.raw).hexdigest()
        if self.sha256 != expected_sha256:
            raise ValueError("world source hash differs from the frozen trial bundle")
        self.output.mkdir(parents=True, exist_ok=False)
        self.stream = (self.output / "events.jsonl").open("x")
        self.module = types.ModuleType("world")
        self.module.__file__ = str(self.source)
        self.previous = None
        self.had_previous = False
        self.restores = []
        self.started = time.monotonic()
        self.sequence = 0
        self.api_calls = 0
        self.updates = 0
        self.errors = []
        self.result = None

    def emit(self, event, **data):
        row = {"sequence": self.sequence, "elapsed_seconds": time.monotonic() - self.started,
               "event": event, **data}
        self.sequence += 1
        self.stream.write(json.dumps(row, allow_nan=False) + "\n")
        self.stream.flush()

    def fault(self, operation, exc):
        error = {"operation": operation, "error": f"{type(exc).__name__}: {exc}"}
        self.errors.append(error)
        self.emit("world_error", **error)

    def snapshot(self):
        try:
            value = self.module.snapshot()
            if not isinstance(value, dict):
                raise TypeError("snapshot() must return a JSON-serializable dict")
            # Serialize immediately: subsequent updates must not mutate old evidence.
            return json.loads(json.dumps(value, allow_nan=False))
        except Exception as exc:
            self.fault("snapshot", exc)
            raise WorldProgramError(str(exc)) from exc

    def __enter__(self):
        self.had_previous = "world" in sys.modules
        self.previous = sys.modules.get("world")
        sys.modules["world"] = self.module
        try:
            # Execute the exact hashed bytes; never a potentially stale .pyc.
            exec(compile(self.raw, str(self.source), "exec"), self.module.__dict__)
            if not all(callable(getattr(self.module, n, None)) for n in ("update", "snapshot")):
                raise TypeError("world must expose update(obs, last_action) and snapshot()")
            original = self.module.update

            @functools.wraps(original)
            def update(*args, **kwargs):
                try:
                    result = original(*args, **kwargs)
                except Exception as exc:
                    self.fault("update", exc)
                    raise
                self.updates += 1
                self.emit("world_update", inputs=summarize({"args": args, "kwargs": kwargs}),
                          snapshot=self.snapshot())
                return result

            self.module.update = update
            self.emit("world_loaded", source_sha256=self.sha256, snapshot=self.snapshot())
            return self
        except Exception as exc:
            if not self.errors:
                self.fault("load", exc)
            self.close("program_error")
            raise WorldProgramError(str(exc)) from exc

    def bind_api(self, api):
        """Wrap public calls already requested by the policy; request nothing."""
        original_functions = api.functions
        logger = api.get_trace_logger() if hasattr(api, "get_trace_logger") else None
        wrapped = {}
        for name, fn in original_functions().items():
            def make_wrapper(name, fn):
                @functools.wraps(fn)
                def call(*args, **kwargs):
                    result, error = None, None
                    before = len(logger.entries) if logger is not None else 0
                    try:
                        result = fn(*args, **kwargs)
                        return result
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                        raise
                    finally:
                        self.api_calls += 1
                        self.emit("public_api", function=name,
                                  api_trace_step=(logger.entries[-1]["step"] if logger is not None and len(logger.entries) > before else None),
                                  args=summarize(args),
                                  kwargs=summarize(kwargs), result=summarize(result),
                                  error=error, snapshot=self.snapshot())
                return call
            wrapped[name] = make_wrapper(name, fn)
        api.functions = lambda: dict(wrapped)
        self.restores.append((api, original_functions))

    def attach(self, env):
        from aspire.sim.scripts.libero.replay_trial import _find_apis
        apis = _find_apis(env)
        if not apis:
            raise RuntimeError("simple world could not attach to public APIs")
        for api in apis.values():
            self.bind_api(api)

    def capture(self, phase, data):
        self.emit("episode", phase=phase, snapshot=self.snapshot())

    def complete(self, **result):
        self.result = result
        self.emit("complete", result=result, snapshot=self.snapshot())

    def close(self, status):
        for api, original in reversed(self.restores):
            api.functions = original
        if self.had_previous:
            sys.modules["world"] = self.previous
        else:
            sys.modules.pop("world", None)
        manifest = {"mode": MODE, "status": status, "world_sha256": self.sha256,
                    "api_calls": self.api_calls, "updates": self.updates,
                    "errors": self.errors, "result": self.result}
        (self.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.stream.close()

    def __exit__(self, kind, value, tb):
        self.close("program_error" if self.errors else "complete" if kind is None else "interrupted")


def run_simple_world(args):
    from aspire.sim.scripts.libero.replay_trial import _run_replay
    config_path = Path(args.world_model_config)
    config = json.loads(config_path.read_text())
    if config.get("mode") != MODE or config.get("task_gate") != {"suite": args.suite, "task": args.task}:
        raise ValueError("simple-world configuration/task mismatch")
    if not args.replay_code or args.interactive:
        raise ValueError("simple world requires a recorded policy replay")
    if hashlib.sha256(Path(args.replay_code).read_bytes()).hexdigest() != config["policy_sha256"]:
        raise ValueError("policy source differs from frozen trial bundle")
    source = config_path.parent / config["world_program"]
    with WorldSession(source, config["world_program_sha256"], config_path.parent / "simple_world") as session:
        _run_replay(args, _world_capture=session)
        if session.errors:
            raise WorldProgramError("world errors recorded in simple_world/events.jsonl")
