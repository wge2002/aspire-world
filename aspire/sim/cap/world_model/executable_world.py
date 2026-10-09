"""Opt-in C executable semantics, public evidence, and honest offline bindings.

No task predicates, geometry, learned thresholds, simulator handles or model
clients live here. The generated module owns those semantics. This is a replay
interface and reliability check, not a security sandbox for generated Python.
"""
from __future__ import annotations

import ast
import copy
import functools
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
import types

import numpy as np

from .simple_world import WorldSession, WorldProgramError, summarize, module_errors as simple_errors
from .evidence_state import EvidenceState, conjunction
from .judgment_world import caller_site
from . import prediction_contract as predictions

MODE = "opus46-executable-world-c-r1"
REVISION = "r1"
ARMS = {"full": (True, True), "no_self_eval": (False, True), "no_rehearsal": (True, False)}
REQUIRED = ("update", "query", "snapshot", "predict", "observe", "simulate", "done")
MOTION = frozenset({"goto_pose", "move_to_joints", "open_gripper", "close_gripper",
                    "goto_home_joint_position"})
SENSING = frozenset({"get_observation"})
PURE = frozenset({"rotation_matrix_to_quaternion", "decompose_transform", "depth_to_point_cloud",
                  "mask_to_world_points", "pixel_to_world_point", "transform_points",
                  "interpolate_segment", "normalize_vector", "select_top_down_grasp"})
API_NAMES = MOTION | SENSING | PURE | {
    "segment_sam3_text_prompt", "segment_sam3_point_prompt", "point_prompt_molmo",
    "plan_grasp", "plan_grasp_from_point_clouds", "subsample_point_cloud", "filter_noise",
    "solve_ik", "get_oriented_bounding_box_from_3d_points"}


class Unsupported(RuntimeError):
    """No supported model prediction or recorded counterfactual observation."""


class ReplayDivergence(Unsupported):
    pass


class SelfEvaluationDisabled(RuntimeError):
    pass


def flags(arm):
    if arm not in ARMS:
        raise ValueError(f"unknown C ablation arm: {arm}")
    return dict(zip(("self_eval", "rehearsal"), ARMS[arm]))


def module_errors(source):
    errors = simple_errors(source)
    try:
        names = {n.name for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    except SyntaxError:
        return errors
    return sorted(set(errors + [f"world must define {n}()" for n in REQUIRED if n not in names]))


def finite_json(value):
    return json.loads(json.dumps(value, allow_nan=False))


class TapeStore:
    """Lossless public values without pickle. Array references are content hashes."""

    def __init__(self, root):
        self.root = Path(root)

    def encode(self, value, save=False):
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, np.ndarray):
            if value.dtype.hasobject:
                return {"kind": "unsupported", "type": "object array"}
            value = np.ascontiguousarray(value)
            h = hashlib.sha256(value.dtype.str.encode() + repr(value.shape).encode() + value.tobytes()).hexdigest()
            if save:
                folder = self.root / "arrays"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / (h + ".npy")
                if not path.exists():
                    with path.open("xb") as stream:
                        np.save(stream, value, allow_pickle=False)
            return {"kind": "array", "sha256": h, "dtype": value.dtype.str, "shape": list(value.shape)}
        if value is None or isinstance(value, (str, bool, int)):
            return {"kind": "scalar", "value": value}
        if isinstance(value, float):
            return {"kind": "scalar", "value": value} if math.isfinite(value) else {"kind": "float", "value": repr(value)}
        if isinstance(value, (tuple, list)):
            return {"kind": "tuple" if isinstance(value, tuple) else "list",
                    "value": [self.encode(v, save) for v in value]}
        if isinstance(value, dict):
            return {"kind": "dict", "value": [[self.encode(k, save), self.encode(v, save)] for k, v in value.items()]}
        return {"kind": "unsupported", "type": type(value).__name__}

    def decode(self, value):
        kind = value["kind"]
        if kind == "scalar":
            return value["value"]
        if kind == "float":
            return float(value["value"])
        if kind == "array":
            h = value["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", h):
                raise ValueError("invalid array digest")
            array = np.load(self.root / "arrays" / (h + ".npy"), allow_pickle=False)
            if self.encode(array) != value:
                raise ValueError("public tape array changed")
            return array
        if kind in {"list", "tuple"}:
            items = [self.decode(v) for v in value["value"]]
            return tuple(items) if kind == "tuple" else items
        if kind == "dict":
            return {self.decode(k): self.decode(v) for k, v in value["value"]}
        raise Unsupported("tape cannot reconstruct " + value.get("type", kind))


class ExecutableSession(WorldSession):
    def __init__(self, source, expected_sha256, output, arm="full", binding="shadow",
                 closed_loop=False, prediction_contract=None):
        # Refuse an unknown contract before anything is created on disk.
        contract = predictions.contract(prediction_contract)
        super().__init__(source, expected_sha256, output)
        self.arm, self.binding = arm, binding
        self.features = flags(arm)
        # Opt-in only. With this False the session is byte-for-byte the r1
        # behaviour every previously staged study renders.
        self.closed_loop = bool(closed_loop)
        # Prediction contract p1, also opt-in: None (absent or "off") constructs
        # no ledger, so no event, manifest key or query interception exists.
        self.prediction_contract = contract
        self.predictions = (predictions.PredictionLedger(self.emit, lambda: self.last_observation)
                            if contract else None)
        self.prediction_checks = self.predictions.checks if contract else []
        self.final_judgment_active = False
        self.observations = set()
        self.last_motion = -1
        self.last_observation = None
        self.self_evaluations = []
        self.queries = 0
        self.store = TapeStore(self.output)
        self.tape = (self.output / "public_tape.jsonl").open("x")

    def __enter__(self):
        self.had_previous, self.previous = "world" in sys.modules, sys.modules.get("world")
        sys.modules["world"] = self.module
        self.module.Unsupported = Unsupported
        if self.predictions is not None:
            # Same EvidenceState API; it additionally reports measured writes.
            self.module.WorldState = lambda: self.predictions.state(
                self.evidence_valid, self.binding, strict=self.closed_loop)
        elif self.closed_loop:
            self.module.WorldState = lambda: EvidenceState(self.evidence_valid, self.binding, strict=True)
        else:
            self.module.WorldState = lambda: EvidenceState(self.evidence_valid, self.binding)
        try:
            exec(compile(self.raw, str(self.source), "exec"), self.module.__dict__)
            if not all(callable(getattr(self.module, n, None)) for n in REQUIRED):
                raise TypeError("executable world requires " + ", ".join(REQUIRED))
            self.original_done = self.module.done
            self.module.done = self.done
            self.module.evidence_id = lambda: self.last_observation
            self.module.self_eval_enabled = self.features["self_eval"]
            self.module.update = self.wrap_update(self.module.update)
            self.module.query = self.wrap_query(self.module.query)
            self.emit("world_loaded", source_sha256=self.sha256, arm=self.arm,
                      binding=self.binding, features=self.features, snapshot=self.snapshot())
            return self
        except (Exception, SystemExit) as exc:
            self.fault("load", exc)
            self.close("program_error")
            raise WorldProgramError(str(exc)) from exc

    def evidence_valid(self, ids, fresh=False):
        return (isinstance(ids, list) and bool(ids)
                and all(type(i) is int and i in self.observations for i in ids)
                and (not fresh or any(i > self.last_motion for i in ids)))

    def evidence_current(self, ids):
        """Closed-loop strictness: EVERY id is an observation after the last motion.

        The legacy any-fresh rule lets one new observation carry stale IDs along
        with it; an online stop or recovery must not rest on that.
        """
        return self.evidence_valid(ids) and all(i > self.last_motion for i in ids)

    def fresh_evidence(self, ids):
        return self.evidence_current(ids) if self.closed_loop else self.evidence_valid(ids, fresh=True)

    def wrap_update(self, fn):
        def update(obs, last_action=None):
            if not isinstance(obs, dict) or not self.evidence_valid(obs.get("evidence_ids")):
                raise WorldProgramError("update requires evidence_ids from actual get_observation calls; commands are not measurements")
            result = fn(copy.deepcopy(obs), last_action)
            self.updates += 1
            self.emit("world_update", inputs=summarize(obs), last_action=summarize(last_action), snapshot=self.snapshot())
            return result
        return update

    def wrap_query(self, fn):
        def query(name, **kwargs):
            if self.predictions is not None and name in predictions.RESERVED:
                # Reserved under p1 only; the author's query never sees these names.
                value = finite_json(self.predictions.answer(name, copy.deepcopy(kwargs)))
            else:
                value = finite_json(fn(name, **copy.deepcopy(kwargs)))
            self.queries += 1
            self.emit("world_query", name=name, kwargs=summarize(kwargs), result=value,
                      caller=caller_site(), api_calls_before=self.api_calls,
                      snapshot=self.snapshot())
            return value
        return query

    def done(self):
        if not self.features["self_eval"]:
            raise SelfEvaluationDisabled("this arm has no online self-evaluation; do not call done()")
        # Origin is fixed when the call starts, not inferred later from position:
        # complete() sets it for the framework's own final shadow judgment only.
        origin = "framework_final" if self.final_judgment_active else "policy"
        raw = finite_json(self.original_done())
        if not isinstance(raw, dict) or raw.get("verdict") not in {"true", "false", "unknown"}:
            raise WorldProgramError("done() must return verdict true/false/unknown, evidence_ids and reason")
        checked = dict(raw)
        # The closed-loop revision requires the foundation clause contract even if
        # a world forgot its declaration; collect_bundle also refuses that world.
        if self.closed_loop or getattr(self.module, "FOUNDATION_REVISION", None) == "r1":
            clauses = raw.get("clauses")
            if not isinstance(clauses, list) or not clauses:
                checked.update(verdict="unknown", reason="foundation goal requires explicit evidence-bearing clauses")
            else:
                validated = []
                for clause in clauses:
                    if not isinstance(clause, dict) or clause.get("verdict") not in {"true", "false", "unknown"}:
                        raise WorldProgramError("malformed goal clause")
                    clause = dict(clause)
                    expected_layer = "rehearsal" if self.binding == "rehearsal" else "observed"
                    refs = clause.get("reference_evidence_ids", [])
                    if clause["verdict"] != "unknown" and (
                            clause.get("layer") != expected_layer
                            or not self.fresh_evidence(clause.get("evidence_ids"))
                            or (refs and not self.evidence_valid(refs))):
                        clause.update(verdict="unknown", reason="missing, stale, or non-observed clause evidence")
                    validated.append(clause)
                checked = conjunction(validated)
                if checked["verdict"] != raw["verdict"]:
                    checked["authored_verdict"] = raw["verdict"]
        if checked["verdict"] != "unknown" and not self.fresh_evidence(checked.get("evidence_ids")):
            checked.update(verdict="unknown", reason="missing or stale post-action observation evidence", authored_verdict=raw["verdict"])
        if self.closed_loop:
            checked["origin"] = origin
            if origin == "policy":
                # The world names the branch; the framework only refuses one the
                # evidence cannot support. The final shadow judgment is not a
                # decision and is recorded with its verdict unadjudicated.
                from .decision_revision import adjudicate
                checked = adjudicate(checked, raw, self.evidence_current)
        checked.update(binding=self.binding, api_calls_before=self.api_calls, caller=caller_site())
        self.self_evaluations.append(checked)
        self.emit("model_goal" if self.binding == "rehearsal" else "self_evaluation", result=checked)
        return checked

    def final_judgment(self):
        """The framework's own post-program judgment, tagged as such."""
        self.final_judgment_active = True
        try:
            return self.done()
        finally:
            self.final_judgment_active = False

    def invoke(self, name, fn, args, kwargs):
        index = self.api_calls
        call = {"id": index, "function": name, "args": copy.deepcopy(args), "kwargs": copy.deepcopy(kwargs)}
        # Commands and predictions precede the response, and cannot be promoted
        # to measurement IDs. Prediction is never supplied the future response.
        declined = None
        try:
            prediction = self.module.predict(copy.deepcopy(call))
        except Unsupported as exc:
            prediction = {"supported": False, "reason": str(exc)}
            declined = str(exc)
        self.emit("prediction", api_call=index, function=name, prediction=summarize(prediction))
        if self.predictions is not None:
            # Committed before the call runs, so it can only be resolved by a later observation.
            self.predictions.record(index, prediction, declined)
        encoded_call = self.store.encode(call, save=True)
        result, error, unsupported = None, None, False
        try:
            result = fn(*args, **kwargs)
            return result
        except Unsupported as exc:
            unsupported = True
            self.emit("unsupported_call", api_call=index, function=name, reason=str(exc))
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if not unsupported:
                self.record_response(index, name, call, encoded_call, args, kwargs, result, error)

    def record_response(self, index, name, call, encoded_call, args, kwargs, result, error):
        self.api_calls += 1
        if name in MOTION:
            self.last_motion = index
        if name in SENSING and error is None:
            self.last_observation = index
            self.observations.add(index)
        event = {"id": index, "kind": "measurement" if name in SENSING else "command_receipt" if name in MOTION else "derived_public_result",
                 "binding": self.binding, "call": call,
                 "result": copy.deepcopy(result), "error": error,
                 "evidence_id": index if name in SENSING and error is None else None}
        self.tape.write(json.dumps({"call": encoded_call, "result": self.store.encode(result, save=True), "error": error}) + "\n")
        self.tape.flush()
        # The framework supplies only public returns, never task_completed.
        self.module.observe(event)
        self.emit("public_api", api_call=index, function=name, kind=event["kind"],
                  args=summarize(args), kwargs=summarize(kwargs), result=summarize(result),
                  error=error, evidence_id=event["evidence_id"], snapshot=self.snapshot())

    def bind_api(self, api):
        original = api.functions
        def wrap(name, fn):
            @functools.wraps(fn)
            def call(*args, **kwargs):
                try:
                    return self.invoke(name, fn, args, kwargs)
                except Unsupported:
                    raise
                except Exception as exc:
                    self.fault("public_call", exc)
                    raise
            return call
        wrapped = {n: wrap(n, fn) for n, fn in original().items()}
        api.functions = lambda: dict(wrapped)
        self.restores.append((api, original))

    def attach(self, env):
        from aspire.sim.scripts.libero.replay_trial import _find_apis
        apis = _find_apis(env)
        if not apis:
            raise RuntimeError("executable world could not attach to public APIs")
        for api in apis.values():
            self.bind_api(api)

    def complete(self, **result):
        # Score AFTER the program and before storing evaluator-only labels.
        # This is the framework's own shadow judgment, not a policy decision, so
        # it goes through final_judgment(): its recorded evaluation carries
        # origin="framework_final" and the closed-loop adjudication of an online
        # branch is skipped for it. Legacy arms have no origin key, so their
        # manifest is unchanged.
        if self.features["self_eval"]:
            try:
                self.final_judgment()
            except Exception as exc:
                self.fault("final_self_evaluation", exc)
        if self.predictions is not None:
            self.predictions.finalize()
        self.result = result
        self.emit("complete", result=result, snapshot=self.snapshot())

    def close(self, status):
        for api, original in reversed(self.restores):
            api.functions = original
        if self.had_previous:
            sys.modules["world"] = self.previous
        else:
            sys.modules.pop("world", None)
        manifest = {"mode": MODE, "status": status, "arm": self.arm, "binding": self.binding,
                    "world_sha256": self.sha256, "api_calls": self.api_calls,
                    "updates": self.updates, "queries": self.queries,
                    "features": self.features, "self_evaluations": self.self_evaluations,
                    "closed_loop": self.closed_loop, "errors": self.errors, "result": self.result}
        if self.predictions is not None:
            # Idempotent; covers an interrupted episode that never reached complete().
            self.predictions.finalize()
            manifest["prediction_checks"] = self.predictions.manifest()
        (self.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        self.tape.close()
        self.stream.close()


class ReplayBinding:
    def __init__(self, tape):
        self.path = Path(tape)
        self.store = TapeStore(self.path.parent)
        self.rows = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        self.index = 0

    def initial_prefix_only(self):
        for index, row in enumerate(self.rows):
            fields = {k["value"]: v for k, v in row["call"]["value"]}
            if fields["function"]["value"] in MOTION:
                self.rows = self.rows[:index]
                return

    def call(self, name, *args, **kwargs):
        if self.index >= len(self.rows):
            raise ReplayDivergence("recorded history exhausted; no counterfactual observation")
        row = self.rows[self.index]
        call = {"id": self.index, "function": name, "args": args, "kwargs": kwargs}
        if self.store.encode(call) != row["call"]:
            # Do not advance the tape or expose the unmatched future result.
            raise ReplayDivergence(f"first changed API call at index {self.index}: {name}")
        if row["error"]:
            raise Unsupported(f"recorded API raised at {self.index}: {row['error']}")
        result = self.store.decode(row["result"])
        self.index += 1
        return result


def pure_bindings(source):
    """Execute the exact existing pure math helpers, not approximate mock math."""
    from scipy.spatial.transform import Rotation
    tree = ast.parse(Path(source).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FrankaLiberoApiReducedSkillLibrary")
    cls.bases = []
    cls.decorator_list = []
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in PURE]
    module = ast.Module(body=[cls], type_ignores=[])
    namespace = {"np": np, "math": math, "Any": object, "SciRotation": Rotation}
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), namespace)
    instance = namespace[cls.name]()
    return {n: getattr(instance, n) for n in PURE if hasattr(instance, n)}


def write_offline_report(report, output, started):
    report["elapsed_seconds"] = time.monotonic() - started
    (output / "offline_result.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def run_offline(policy, world, output, *, arm="full", mode="rehearsal", tape=None,
                task_language="", pure_source=None, closed_loop=False, prediction_contract=None):
    started = time.monotonic()
    if mode not in {"rehearsal", "replay"}:
        raise ValueError("offline binding must be rehearsal or replay")
    contract = predictions.contract(prediction_contract)
    if not flags(arm)["rehearsal"]:
        raise ValueError("offline candidate screening is disabled in this ablation")
    if closed_loop:
        from .decision_revision import ARMS as _CL_ARMS
        if arm not in _CL_ARMS:
            raise ValueError("closed-loop screening is unsupported with this ablation arm")
    policy, world, output = Path(policy), Path(world), Path(output)
    report = {"mode": mode, "arm": arm, "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
              "world_sha256": hashlib.sha256(world.read_bytes()).hexdigest(),
              "tape": str(tape) if tape else None, "status": "running"}
    replay = ReplayBinding(tape) if mode == "replay" and tape else None
    prefix = ReplayBinding(tape) if mode == "rehearsal" and tape else None
    if prefix is not None:
        prefix.initial_prefix_only()
        report["scenario"] = "previous development tape initial public prefix only; later effects are generated predictions"
    if mode == "replay" and replay is None:
        raise ValueError("replay requires a public development tape")
    loaded = False
    try:
        with ExecutableSession(world, report["world_sha256"], output, arm, mode,
                               closed_loop=closed_loop, prediction_contract=contract) as session:
            loaded = True
            pure = pure_bindings(pure_source) if pure_source else {}
            def bound(name):
                def call(*args, **kwargs):
                    def execute(*a, **kw):
                        if replay is not None:
                            return replay.call(name, *a, **kw)
                        if prefix is not None and prefix.index < len(prefix.rows):
                            return prefix.call(name, *a, **kw)
                        if name in pure:
                            return pure[name](*a, **kw)
                        return session.module.simulate({"id": session.api_calls, "function": name,
                                                        "args": copy.deepcopy(a), "kwargs": copy.deepcopy(kw)})
                    return session.invoke(name, execute, args, kwargs)
                return call
            namespace = {name: bound(name) for name in API_NAMES}
            namespace.update(__name__="__main__", env=types.SimpleNamespace(handle=types.SimpleNamespace(task_language=task_language)))
            try:
                for block in re.split(r"^# Code block \d+\s*\n", policy.read_text(), flags=re.M):
                    if block.strip():
                        exec(compile(block, str(policy), "exec"), namespace)
                report.update(status="complete", api_calls=session.api_calls)
                if session.features["self_eval"]:
                    # The framework's post-program judgment, tagged as such; a
                    # policy decision would be a separate origin="policy" row.
                    report["goal"] = session.final_judgment()
            except SystemExit as exc:
                # Policy-level early termination must still leave a report.
                # A zero exit does not establish a modeled goal or a live task
                # outcome; keep it unknown and admissible for real evaluation.
                clean = exc.code is None or exc.code == 0
                report.update(status="unsupported" if clean else "program_error",
                              reason=f"policy raised SystemExit({exc.code!r}); no completion verdict",
                              termination="policy_exit", api_calls=session.api_calls)
                if not clean:
                    session.fault("offline_policy", exc)
            except Unsupported as exc:
                report.update(status="unsupported", reason=str(exc), api_calls=session.api_calls)
            except Exception as exc:
                report.update(status="program_error", reason=f"{type(exc).__name__}: {exc}", api_calls=session.api_calls)
                session.fault("offline_policy", exc)
            if replay is not None:
                report.update(matched_calls=replay.index, tape_calls=len(replay.rows),
                              scope="same recorded public call prefix only; no live-success claim")
            if session.predictions is not None:
                # Generated simulate() effects are what the predictions met here:
                # a rehearsal match is consistency between the world's two models,
                # not evidence about the real scene.
                session.predictions.finalize()
                report["prediction_checks"] = session.predictions.manifest()
    except WorldProgramError as exc:
        if loaded:
            raise
        # The world module raised while loading: a top-level statement, a syntax
        # error, or a missing required definition. That is the candidate's own
        # error, and __enter__ already closed the session — but it never wrote a
        # report, so the screening process used to exit with no artifact and the
        # authored error was read back as a retryable infrastructure blocker.
        cause = exc.__cause__ or exc
        report.update(status="program_error", stage="world_load", api_calls=0,
                      reason=f"{type(cause).__name__}: {cause}")
    return write_offline_report(report, output, started)


def run_executable_world(args):
    from aspire.sim.scripts.libero.replay_trial import _run_replay
    config_path = Path(args.world_model_config)
    config = json.loads(config_path.read_text())
    if config.get("mode") != MODE or config.get("task_gate") != {"suite": args.suite, "task": args.task}:
        raise ValueError("executable-world configuration/task mismatch")
    if not args.replay_code or args.interactive:
        raise ValueError("executable world requires a recorded policy")
    if hashlib.sha256(Path(args.replay_code).read_bytes()).hexdigest() != config["policy_sha256"]:
        raise ValueError("policy differs from frozen bundle")
    source = config_path.parent / config["world_program"]
    # The closed-loop revision is a property of the frozen trial config, so a
    # resumed replay reproduces the same online semantics it was recorded under.
    # An absent key keeps the r1 observational behaviour.
    closed_loop = bool(config.get("closed_loop"))
    if closed_loop:
        from .decision_revision import ARMS as _CL_ARMS
        if config.get("c_arm") not in _CL_ARMS:
            raise ValueError("closed-loop config carries an unsupported ablation arm")
    # Likewise frozen: an absent key is the contract-off behaviour.
    prediction_contract = predictions.contract(config.get("prediction_contract"))
    # Preserve the protocol's in-process artifact location for fault accounting.
    with ExecutableSession(source, config["world_program_sha256"], config_path.parent / "judgment_world",
                           config["c_arm"], closed_loop=closed_loop,
                           prediction_contract=prediction_contract) as session:
        _run_replay(args, _world_capture=session)
        if session.errors:
            raise WorldProgramError("executable world recorded errors")
