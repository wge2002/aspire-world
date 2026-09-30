"""Live world-model broker for closed-loop CaP with numeric verification.

Extends the passive capture with real-time world program execution and
verification callbacks. The world program runs in isolated subprocesses
via FrozenPythonProgram; this broker exposes world_verify/recovery_available
to the policy code's execution namespace.

Imported only when --args.world-model-config has mode="opus46-diagnostic".
No effect on default replay or existing capture mode.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import math
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from types import SimpleNamespace
from urllib.parse import urlsplit

from .program import FrozenPythonProgram, ProgramExecutionError
from .runtime import _copy


SUITE = "libero_goal_swap"
TASK = "put_the_bowl_on_the_plate"

# The only accepted wire value for "this budget has no cap". A profile must say
# so in these words: no large integer is ever allowed to stand in for it, and a
# missing or null field stays an error rather than silently uncapping a run.
UNLIMITED = "unlimited"

MOTOR_ACTIONS = {
    "move_to_joints", "goto_pose", "open_gripper", "close_gripper",
    "goto_home_joint_position",
}

_CHILD_ENV_NAMES = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "TMPDIR", "TMP", "TEMP", "ASPIRE_ROOT", "PYTHON_ROOT", "PYTHONPATH",
    "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH",
    "LIBRARY_PATH", "CUDA_HOME", "CUDA_PATH", "CUDA_VISIBLE_DEVICES",
    "MUJOCO_EGL_DEVICE_ID", "MUJOCO_GL", "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD",
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "XLA_PYTHON_CLIENT_PREALLOCATE",
    "XLA_PYTHON_CLIENT_MEM_FRACTION", "PYTHONUNBUFFERED", "PYTHONNOUSERSITE",
    "VIRTUAL_ENV", "CONDA_PREFIX", "LIBERO_CONFIG_PATH",
    "__EGL_VENDOR_LIBRARY_FILENAMES",
}
_SERVICE_ENV_NAMES = {"SAM3_SERVICE_URL", "GRASPNET_SERVICE_URL", "PYROKI_SERVICE_URL"}


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numeric(value: Any) -> Any:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)) and len(value) <= 64:
        return [_numeric(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _numeric(item) for key, item in value.items()}
    return None


def _number(v: Any) -> bool:
    return isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v)


def parse_budget(value: Any, label: str) -> int | None:
    """Return a nonnegative cap, or ``None`` meaning genuinely uncapped.

    ``UNLIMITED`` is the only value that uncaps a budget: it is a word, so a
    profile cannot reach the same state by writing a big number, and reading it
    back out of a manifest cannot be mistaken for a real count. Everything else
    must be a nonnegative, non-bool integer exactly as before, and ``None`` on
    the wire stays a hard error so a dropped field never silently uncaps a run.
    """
    if value == UNLIMITED and isinstance(value, str):
        return None
    if type(value) is not int or value < 0:
        raise ValueError(
            f"{label} must be a nonneg integer (not bool) or {UNLIMITED!r}")
    return value


def budget_remaining(limit: int | None, used: int) -> int | str:
    """Remaining quota for a snapshot: ``UNLIMITED`` never becomes a number."""
    return UNLIMITED if limit is None else limit - used


def budget_exhausted(limit: int | None, used: int) -> bool:
    """True when a further draw would exceed ``limit``. Uncapped never is."""
    return limit is not None and used >= limit


def _budget_field(limit: int | None) -> int | str:
    """Serialize a parsed budget back to its wire form for manifests/tapes."""
    return UNLIMITED if limit is None else limit


def _vector(v: Any, n: int, label: str) -> list[float]:
    if not isinstance(v, (tuple, list)) or len(v) != n or not all(_number(x) for x in v):
        raise ValueError(f"{label} must contain {n} finite numbers")
    return [float(x) for x in v]


def _validate_axes(v: Any) -> tuple[int, ...]:
    if not isinstance(v, (tuple, list)) or not v:
        raise ProgramExecutionError(
            "query_axes must be a nonempty subset of [0, 1, 2]")
    if (any(type(a) is not int or a not in (0, 1, 2) for a in v)
            or len(set(v)) != len(v)):
        raise ProgramExecutionError(
            "query_axes must be unique integers from {0, 1, 2}")
    return tuple(v)


class LiveBroker:
    """Runs a world program alongside a policy trial, exposing verify/recovery."""

    def __init__(self, directory: Path, config: dict, manifest: dict,
                 program_source: str):
        self.directory = directory
        self.config = config
        self.manifest = manifest
        self.program = FrozenPythonProgram(program_source, timeout_s=2.0)

        self.env = None
        self.api = None
        self._restorations: list[tuple[Any, bool, Any]] = []
        self._inside = False

        self._state = None
        self._world_version = 0
        self._frame = 0
        self._initialized = False
        self._initialization_attempted = False
        self._mechanism_failed = False
        self._costs = {}

        # Each of these is either an int cap or None for uncapped; see
        # parse_budget. Frozen profiles keep sending integers, so they keep
        # exactly the caps and the enforcement they had.
        self._query_budget = parse_budget(config["query_budget"], "query_budget")
        self._query_used = 0
        self._tolerance = float(config["tolerance"])
        self._max_actions = parse_budget(config["max_actions"], "max_actions")
        self._action_count = 0
        # Attempts include the calls the cap refused; _action_count counts only
        # the motor calls that were actually allowed to run. Keeping the two
        # apart is what lets a correctly enforced cap stay an ordinary, counted
        # budget failure instead of looking like a broken runtime.
        self._action_attempts = 0
        self._action_limit_denials = 0
        self._max_recovery = parse_budget(
            config.get("max_recovery", 1), "max_recovery")
        self._recovery_used = 0

        self._object_id = config["observation"]["object_id"]
        self._seg_prompt = config["observation"]["segmentation_prompt"]
        self._min_score = float(config["observation"].get("min_score", 0.5))

        self._last_verdict: dict[str, Any] = {
            "status": "unknown", "prediction": None, "observed": None,
            "error_m": None, "recovery_available": True, "action": None,
            "frame": 0,
        }

        self._tape = (directory / "live_tape.jsonl").open("x")
        self._events: list[dict] = []
        self._finished = False

    # -- events ----------------------------------------------------------

    def _emit(self, kind: str, **fields: Any) -> None:
        event = _copy({"event_id": len(self._events), "event": kind,
                        "frame": self._frame, **fields})
        self._events.append(event)
        try:
            self._tape.write(
                json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            self._tape.flush()
        except Exception:
            self.manifest.setdefault("broker_errors", []).append(
                f"event_write:frame_{self._frame}")

    # -- env attachment ---------------------------------------------------

    def attach(self, env: Any) -> None:
        self.env = env
        apis = getattr(env, "_apis", None)
        if not isinstance(apis, dict) or len(apis) != 1:
            raise ValueError("broker expects one reduced API on the environment")
        self.api = next(iter(apis.values()))
        for name in ("get_observation", "segment_sam3_text_prompt",
                      "mask_to_world_points"):
            if not callable(getattr(self.api, name, None)):
                raise ValueError(f"broker API is missing {name}")

        api = self.api
        original_functions = api.functions
        owned = "functions" in vars(api)
        self._restorations.append(
            (api, owned, vars(api).get("functions")))
        broker = self

        def functions():
            bindings = original_functions()
            wrapped = {}
            for name, fn in bindings.items():
                if name in MOTOR_ACTIONS:
                    wrapped[name] = broker._wrap(fn, name)
                else:
                    wrapped[name] = fn
            # Subclasses decide which extra callables the policy namespace gets.
            # A condition with no world mechanism exposes no world_verify, so it
            # is genuinely absent from that namespace rather than present and
            # inert. The recovery gates are execution-budget accounting, not
            # world inference, and stay available in every condition (see
            # ordinary_fix_loop_guard._policy_extras).
            wrapped.update(broker._policy_extras())
            return wrapped

        api.functions = functions

    def _policy_extras(self) -> dict:
        return {"world_verify": self.world_verify,
                "recovery_available": self.recovery_available,
                "use_recovery": self.use_recovery}

    def _wrap(self, fn: Any, name: str):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            if self._inside:
                return fn(*args, **kwargs)
            self._action_attempts += 1
            if budget_exhausted(self._max_actions, self._action_attempts - 1):
                # Refuse the call WITHOUT counting it as an executed action, so
                # the recorded action_count stays at the cap. A correctly
                # enforced limit is the policy's own counted budget failure;
                # inflating action_count past max_actions used to make the
                # parent's budget check read it as broken infrastructure and
                # fail the whole queue.
                self._action_limit_denials += 1
                self._emit("action_limit_denied", action=name,
                           max_actions=self._max_actions,
                           action_count=self._action_count,
                           attempted_actions=self._action_attempts,
                           denied_calls=self._action_limit_denials)
                raise RuntimeError(
                    f"Global motor-action limit ({self._max_actions}) exceeded")
            self._action_count += 1
            self._inside = True
            action_error = None
            try:
                return fn(*args, **kwargs)
            except BaseException as exc:
                action_error = type(exc).__name__
                raise
            finally:
                try:
                    arguments = {
                        f"arg{i}": _numeric(v) for i, v in enumerate(args)}
                    arguments.update(
                        {k: _numeric(v) for k, v in kwargs.items()})
                    self.capture(name, arguments, action_error=action_error)
                except Exception as exc:
                    self._invalidate(f"capture:{type(exc).__name__}", name)
                    self.manifest.setdefault("broker_errors", []).append(
                        f"capture:{type(exc).__name__}")
                finally:
                    self._inside = False
        return call

    # -- observation: separated into proprio-only and full query ----------

    def _sensor_call(self, name, *args):
        item = self._costs.setdefault(name, {"calls": 0, "errors": 0, "seconds": 0.0})
        item["calls"] += 1
        started = time.monotonic()
        try:
            return getattr(self.api, name)(*args)
        except BaseException:
            item["errors"] += 1
            raise
        finally:
            item["seconds"] += time.monotonic() - started

    def _invalidate(self, operation, action=None):
        self._mechanism_failed = True
        self._last_verdict = {
            "status": "unknown", "prediction": None, "observed": None,
            "error_m": None, "action": action, "frame": self._frame,
            "mechanism_error": operation,
            "recovery_available": self.recovery_available(),
        }

    def _read_proprio(self) -> dict | None:
        try:
            obs = copy.deepcopy(self._sensor_call("get_observation"))
            robot = _numeric(obs["robot_cartesian_pos"])
            if (isinstance(robot, list) and len(robot) == 8
                    and all(v is not None for v in robot)):
                return {
                    "position": robot[:3],
                    "orientation_wxyz": robot[3:7],
                    "gripper": robot[7],
                }
        except Exception:
            pass
        return None

    def _run_object_query(self) -> dict:
        measurement: dict[str, Any] = {
            "status": "unknown", "object_id": self._object_id,
            "position": None, "frame": "world",
            "reason": "query_not_attempted",
        }
        try:
            obs = copy.deepcopy(self._sensor_call("get_observation"))
            cam = obs["agentview"]
            masks = self._sensor_call("segment_sam3_text_prompt",
                cam["images"]["rgb"], self._seg_prompt)
            eligible = []
            for idx, item in enumerate(masks):
                score = float(item.get("score", 0.0))
                mask = item.get("mask")
                nonempty = mask is not None and (
                    bool(mask.any()) if hasattr(mask, "any")
                    else any(any(row) for row in mask))
                if (nonempty and math.isfinite(score)
                        and score >= self._min_score):
                    eligible.append((idx, item))
            if len(eligible) != 1:
                measurement["reason"] = (
                    "ambiguous_segmentation" if eligible
                    else "no_eligible_mask")
            else:
                _, selected = eligible[0]
                points = self._sensor_call("mask_to_world_points",
                    selected["mask"], cam["images"]["depth"],
                    cam["intrinsics"], cam["pose_mat"])
                if hasattr(points, "tolist"):
                    points = points.tolist()
                points = [
                    p for p in points
                    if len(p) == 3 and all(
                        math.isfinite(float(x)) for x in p)]
                if not points:
                    measurement["reason"] = "no_valid_depth_points"
                else:
                    position = [
                        float(statistics.median(p[a] for p in points))
                        for a in range(3)]
                    measurement.update(
                        status="ok", position=position, reason=None)
        except Exception as exc:
            measurement["reason"] = f"capture_error:{type(exc).__name__}"
        return measurement

    # -- world program execution -----------------------------------------

    def capture(self, action: str, arguments: dict, *,
                action_error: str | None = None) -> None:
        if self._mechanism_failed:
            self._invalidate("prior_mechanism_failure", action)
            self._emit("mechanism_unavailable", action=action)
            self._frame += 1
            return
        robot_state = self._read_proprio()
        if robot_state is None:
            self.manifest.setdefault("broker_errors", []).append(
                f"missing_proprio:frame_{self._frame}")
            self._emit("missing_proprio", action=action)
            self._last_verdict = {
                "status": "unknown", "prediction": None,
                "observed": None, "error_m": None,
                "recovery_available": self.recovery_available(),
                "action": action, "frame": self._frame,
                "mechanism_error": "missing_proprio",
            }
            self._frame += 1
            return

        step = {
            "index": self._frame,
            "frame_id": f"live_{self._frame}",
            "action": {"api": action, "args": arguments},
            "robot_state": robot_state,
        }
        if action_error is not None:
            step["action_error"] = action_error

        if not self._initialized:
            self._do_initialize(step)
        else:
            self._do_step(step, action)
        self._frame += 1

    def _budget_snapshot(self) -> dict[str, int | str]:
        # Uncapped reports limit/remaining as UNLIMITED rather than arithmetic
        # on None or a stand-in integer, so a world program can branch on the
        # word and never sees a countdown that does not exist.
        return {"limit": _budget_field(self._query_budget),
                "used": self._query_used,
                "remaining": budget_remaining(
                    self._query_budget, self._query_used)}

    def _do_initialize(self, step: dict) -> None:
        if self._initialization_attempted:
            self._invalidate("initialize_already_attempted")
            return
        self._initialization_attempted = True
        measurement = self._run_object_query()
        anchor = {
            "status": measurement["status"],
            "object_id": self._object_id, "frame": "world",
            "axes": [0, 1, 2],
            "values": measurement["position"],
            "reason": measurement.get("reason"),
        }
        budget = self._budget_snapshot()
        context = {
            "object_id": self._object_id, "frame": "world",
            "step": {**step, "budget": budget},
            "measurement": anchor, "budget": budget,
        }
        try:
            self._state = self.program.call("initialize", context)
            self._initialized = True
            self._emit("world_initialized", anchor=anchor)
        except ProgramExecutionError as exc:
            self._invalidate("initialize")
            self._emit("program_error", operation="initialize",
                        error=str(exc))

    def _do_step(self, step: dict, action: str) -> None:
        budget = self._budget_snapshot()
        full_step = {**step, "budget": budget}

        # advance
        try:
            self._state = self.program.call(
                "advance", _copy(self._state), _copy(full_step))
            self._world_version += 1
            self._emit("world_advanced", action=action,
                        world_version=self._world_version)
        except ProgramExecutionError as exc:
            self._mechanism_failed = True
            self._emit("program_error", operation="advance",
                        error=str(exc))
            self._last_verdict = {
                "status": "unknown", "prediction": None,
                "observed": None, "error_m": None,
                "recovery_available": not budget_exhausted(
                    self._max_recovery, self._recovery_used),
                "action": action, "frame": self._frame,
                "mechanism_error": "advance",
            }
            return

        # predict (committed blind before any measurement). Read-only: the state
        # argument is a detached copy and only the returned prediction is used,
        # so mutations inside predict are discarded. State persists only through
        # what advance and assimilate return.
        try:
            raw = self.program.call(
                "predict", _copy(self._state), _copy(full_step))
            prediction = self._validate_prediction(raw)
            prediction = _copy(prediction)
            self._emit("prediction_committed", prediction=prediction,
                        world_version=self._world_version)
        except ProgramExecutionError as exc:
            self._mechanism_failed = True
            self._emit("program_error", operation="predict",
                        error=str(exc))
            self._last_verdict = {
                "status": "unknown", "prediction": None,
                "observed": None, "error_m": None,
                "recovery_available": not budget_exhausted(
                    self._max_recovery, self._recovery_used),
                "action": action, "frame": self._frame,
                "mechanism_error": "predict",
            }
            return

        should_query = prediction.get("request_query", False)
        query_axes = tuple(prediction.get("query_axes", [0, 1, 2]))

        if should_query and not budget_exhausted(
                self._query_budget, self._query_used):
            self._query_used += 1
            measurement = self._run_object_query()
            released = self._release_axes(measurement, query_axes)
            comparison = self._compare(prediction, released, query_axes)
            self._emit("query_comparison", comparison=comparison,
                        measurement=released,
                        measurement_status=measurement["status"],
                        query_number=self._query_used,
                        requested_axes=list(query_axes))
            self._last_verdict = {
                "status": comparison["status"].lower(),
                "prediction": prediction.get("position"),
                "observed": released.get("values"),
                "observed_axes": list(query_axes),
                "error_m": comparison.get("error_m"),
                "recovery_available": self.recovery_available(),
                "action": action, "frame": self._frame,
            }
            evidence = {
                "index": self._frame,
                "frame_id": f"live_{self._frame}",
                "world_version": self._world_version,
                "measurement": released, "comparison": comparison,
            }
            try:
                self._state = self.program.call(
                    "assimilate", _copy(self._state), _copy(evidence))
                self._world_version += 1
                self._emit("world_assimilated",
                            world_version=self._world_version)
            except ProgramExecutionError as exc:
                self._mechanism_failed = True
                self._emit("program_error", operation="assimilate",
                            error=str(exc))
                self._last_verdict = {
                    "status": "unknown", "prediction": None,
                    "observed": None, "error_m": None,
                    "recovery_available": self.recovery_available(),
                    "action": action, "frame": self._frame,
                    "mechanism_error": "assimilate",
                }
        elif should_query:
            self._emit("query_denied", reason="budget_exhausted")
            self._last_verdict = {
                "status": "unknown",
                "prediction": prediction.get("position"),
                "observed": None, "error_m": None,
                "recovery_available": self.recovery_available(),
                "action": action, "frame": self._frame,
            }
        else:
            self._last_verdict = {
                "status": "unknown",
                "prediction": prediction.get("position"),
                "observed": None, "error_m": None,
                "recovery_available": self.recovery_available(),
                "action": action, "frame": self._frame,
            }

    @staticmethod
    def _validate_prediction(value: Any) -> dict:
        if not isinstance(value, dict) or "position" not in value:
            raise ProgramExecutionError(
                "predict must return dict with position")
        position = value["position"]
        if position is not None:
            try:
                position = _vector(position, 3, "prediction position")
            except ValueError as exc:
                raise ProgramExecutionError(str(exc)) from exc
        request = value.get("request_query", False)
        if type(request) is not bool:
            raise ProgramExecutionError("request_query must be boolean")
        axes = _validate_axes(value.get("query_axes", [0, 1, 2]))
        result: dict[str, Any] = {
            "position": position, "request_query": request,
            "query_axes": list(axes),
        }
        if "hypotheses" in value:
            result["hypotheses"] = _copy(value["hypotheses"])
        return result

    @staticmethod
    def _release_axes(measurement: dict, axes: tuple[int, ...]) -> dict:
        position = measurement.get("position")
        values = None
        if position is not None and measurement["status"] == "ok":
            values = [position[a] for a in axes]
        return {
            "status": measurement["status"],
            "object_id": measurement.get("object_id", "unknown"),
            "frame": "world",
            "axes": list(axes),
            "values": values,
            "reason": measurement.get("reason"),
        }

    def _compare(self, prediction: dict, released: dict,
                 axes: tuple[int, ...]) -> dict:
        if released["status"] != "ok":
            return {"status": "UNKNOWN", "residual": None,
                    "error_m": None, "reason": "measurement_unavailable"}
        if prediction["position"] is None:
            return {"status": "UNKNOWN", "residual": None,
                    "error_m": None, "reason": "prediction_abstained"}
        residual = [
            released["values"][i] - prediction["position"][axes[i]]
            for i in range(len(axes))]
        error = math.sqrt(sum(r * r for r in residual))
        status = "SUPPORT" if error <= self._tolerance else "CONTRADICT"
        return {"status": status, "residual": residual,
                "error_m": error, "reason": None}

    # -- policy-callable functions ----------------------------------------

    def world_verify(self) -> dict:
        value = _copy(self._last_verdict)
        self._emit("world_verify", verdict=value)
        return value

    def recovery_available(self) -> bool:
        return not budget_exhausted(self._max_recovery, self._recovery_used)

    def use_recovery(self) -> bool:
        """Consume the in-episode recovery; report whether it was granted.

        A refusal returns False without consuming budget and without raising,
        so a policy written as the contract specifies ("check its actual
        return") observes the refusal instead of losing its only recovery to a
        falsy return value.

        Under an uncapped ``max_recovery`` this never refuses: it keeps counting
        recoveries so the tape still records how many were taken, but a policy
        that calls it repeatedly is never told to stop. A policy need not call
        it at all to recover -- these helpers are accounting, not a gate on
        ordinary retry logic.
        """
        if budget_exhausted(self._max_recovery, self._recovery_used):
            self._emit("recovery_denied", reason="recovery_budget_exhausted",
                        recovery_used=self._recovery_used,
                        max_recovery=self._max_recovery)
            return False
        self._recovery_used += 1
        self._emit("recovery_invoked",
                    recovery_number=self._recovery_used)
        return True

    # -- lifecycle --------------------------------------------------------

    def complete(self, **result: Any) -> None:
        self._finished = True
        self.manifest["trial_result"] = result

    def close(self, error: str | None = None) -> None:
        for api, owned, old in reversed(self._restorations):
            if owned:
                api.functions = old
            else:
                vars(api).pop("functions", None)
        self._restorations.clear()
        self._tape.close()
        if self.env is not None and callable(
                getattr(self.env, "close", None)):
            try:
                self.env.close()
            except Exception as exc:
                self.manifest["cleanup_error"] = type(exc).__name__
        mechanism_errors = [
            e for e in self._events
            if e.get("event") == "program_error"]
        self.manifest.update(
            status=(
                "complete"
                if self._finished and error is None
                   and not self.manifest.get("broker_errors")
                   and not mechanism_errors
                else "failed"),
            frame_count=self._frame,
            action_count=self._action_count,
            max_actions=_budget_field(self._max_actions),
            attempted_actions=self._action_attempts,
            action_limit_denials=self._action_limit_denials,
            query_used=self._query_used,
            query_budget=_budget_field(self._query_budget),
            recovery_used=self._recovery_used,
            world_version=self._world_version,
            events_count=len(self._events),
            broker_sensor_costs=self._costs,
            anchor_attempts=int(self._initialization_attempted),
            tape_sha256=_sha256(self.directory / "live_tape.jsonl"),
        )
        if error:
            self.manifest["error"] = error
        _write_json(self.directory / "live_manifest.json", self.manifest)


# -- config validation ---------------------------------------------------

def load_live_config(args: Any) -> tuple[dict, dict]:
    if getattr(args, "api_key", None):
        raise ValueError(
            "live broker does not request a model; do not pass --args.api-key")
    if not args.replay_code or args.interactive:
        raise ValueError(
            "live mode requires --args.replay-code and noninteractive mode")
    if args.suite != SUITE or args.task != TASK:
        raise ValueError(
            f"opus46-diagnostic profile is restricted to {SUITE}/{TASK}")
    if not 51 <= args.trial <= 65:
        raise ValueError(
            "opus46-diagnostic permits development seeds 51-65 only")
    if not Path(args.replay_code).is_file():
        raise ValueError("frozen replay policy does not exist")
    config = json.loads(Path(args.world_model_config).read_text())
    if not isinstance(config, dict):
        raise ValueError("live config must be an object")
    if config.get("schema_version") != 2:
        raise ValueError("live config requires schema_version=2")
    if config.get("mode") != "opus46-diagnostic":
        raise ValueError("config mode must be opus46-diagnostic")
    if config.get("profile") != "bowl-on-plate-dev":
        raise ValueError("unrecognized diagnostic profile")
    allowed = {
        "schema_version", "mode", "profile", "task_gate", "dev_seeds",
        "world_program", "observation", "query_budget", "tolerance",
        "max_actions", "max_recovery", "trial_timeout_seconds",
        "output_root", "run_name", "model_provenance",
    }
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError(
            "live config contains unsupported keys; "
            "do not put credentials in it")
    gate = config.get("task_gate", {})
    if not isinstance(gate, dict) or set(gate) != {"suite", "task"}:
        raise ValueError("task_gate requires exactly suite and task")
    if gate.get("suite") != SUITE or gate.get("task") != TASK:
        raise ValueError("task_gate must match the diagnostic profile")
    seeds = config.get("dev_seeds", [51, 65])
    if seeds != [51, 65] or any(type(x) is not int for x in seeds):
        raise ValueError("diagnostic dev_seeds must be [51, 65]")
    if (not isinstance(seeds, list) or len(seeds) != 2
            or seeds[0] > args.trial or args.trial > seeds[1]):
        raise ValueError("trial seed is outside dev_seeds range")
    wp = config.get("world_program")
    if not isinstance(wp, str) or not wp.strip():
        raise ValueError("world_program path is required")
    wp_raw = Path(wp)
    if wp_raw.is_absolute():
        wp_path = wp_raw
    else:
        wp_path = Path(args.world_model_config).parent / wp
    if not wp_path.is_file():
        raise ValueError(f"world program not found: {wp_path}")
    obs = config.get("observation", {})
    if not isinstance(obs, dict) or set(obs) - {"object_id", "segmentation_prompt", "min_score"}:
        raise ValueError("unsupported observation keys")
    if obs.get("object_id") != "bowl" or obs.get("segmentation_prompt") != "bowl":
        raise ValueError("diagnostic observation must bind the bowl")
    if not _number(obs.get("min_score", .5)) or not 0 <= obs.get("min_score", .5) <= 1:
        raise ValueError("min_score must be finite in [0, 1]")
    for key in ("object_id", "segmentation_prompt"):
        if not isinstance(obs.get(key), str) or not obs[key].strip():
            raise ValueError(f"observation.{key} is required")
    for key in ("query_budget", "max_actions", "max_recovery"):
        v = config.get(key)
        if type(v) is not int or v < 0:
            raise ValueError(f"{key} must be a nonneg integer (not bool)")
    if config.get("query_budget", 0) > 4:
        raise ValueError("query_budget must be <= 4")
    if config.get("max_recovery", 0) > 1:
        raise ValueError("max_recovery must be <= 1")
    if config.get("max_actions", 0) > 30:
        raise ValueError("max_actions must be <= 30")
    if config["max_actions"] == 0:
        raise ValueError("max_actions must be positive")
    tol = config.get("tolerance")
    if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol <= 0:
        raise ValueError("tolerance must be a positive number")
    if not math.isfinite(tol):
        raise ValueError("tolerance must be finite")
    timeout = config.get("trial_timeout_seconds", 900)
    if (not isinstance(timeout, (float, int))
            or isinstance(timeout, bool) or timeout <= 0):
        raise ValueError("trial_timeout_seconds must be positive")
    if timeout > 900:
        raise ValueError("trial_timeout_seconds must be <= 900")
    if not math.isfinite(timeout):
        raise ValueError("trial_timeout_seconds must be finite")
    config["trial_timeout_seconds"] = timeout
    output_root = config.get("output_root", "./outputs/libero_live")
    if not isinstance(output_root, str) or not output_root.strip():
        raise ValueError("output_root must name an experiment directory")
    output_root = Path(output_root).expanduser().resolve()
    old_root = Path(args.output_dir).expanduser().resolve()
    if (output_root.is_relative_to(old_root)
            or old_root.is_relative_to(output_root)):
        raise ValueError(
            "live output_root must be separate from replay output tree")
    config["output_root"] = str(output_root)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*",
                        config.get("run_name", "")):
        raise ValueError("run_name must be a single nonempty path component")
    prov = config.get("model_provenance", {})
    if not isinstance(prov, dict):
        raise ValueError("model_provenance must be a dict")
    allowed_prov = {
        "policy_generator", "world_program_generator",
        "generation_context", "note", "model_id",
    }
    if set(prov) - allowed_prov:
        raise ValueError(
            f"model_provenance has unexpected keys: {set(prov) - allowed_prov}")
    for field in ("policy_generator", "world_program_generator"):
        if prov.get(field) != "claude-opus-4-6":
            raise ValueError("diagnostic provenance requires claude-opus-4-6")
    if prov.get("model_id", "claude-opus-4-6") != "claude-opus-4-6":
        raise ValueError("diagnostic model_id must be claude-opus-4-6")
    if any(not isinstance(value, str) for value in prov.values()):
        raise ValueError("provenance values must be strings")
    import yaml
    base = yaml.safe_load(Path(args.config).expanduser().read_text())
    cfg = base.get("env", {}).get("cfg", {})
    if (cfg.get("privileged", False)
            or cfg.get("low_level", {}).get("privileged", False)):
        raise ValueError("live broker refuses privileged configurations")
    apis = cfg.get("apis", [])
    if apis != ["FrankaLiberoApiReducedSkillLibraryTraced"]:
        raise ValueError(
            "live mode requires the nonprivileged reduced traced LIBERO API")
    return config, base


# -- parent entry: prepare directory and launch child --------------------

def _prepare_live(args: Any, config: dict) -> Path:
    directory = (
        Path(config["output_root"]) / config["run_name"]
        / f"seed_{args.trial}")
    directory.mkdir(parents=True, exist_ok=False)
    policy_path = directory / "frozen_policy.py"
    policy_path.write_bytes(Path(args.replay_code).read_bytes())
    yaml_path = directory / "source_config.yaml"
    yaml_path.write_bytes(Path(args.config).expanduser().read_bytes())
    wp_raw = Path(config["world_program"])
    wp_src = wp_raw if wp_raw.is_absolute() else Path(args.world_model_config).parent / config["world_program"]
    wp_dest = directory / "world_program.py"
    wp_dest.write_bytes(wp_src.read_bytes())
    _write_json(directory / "live_config.json", config)
    manifest = {
        "schema_version": 2, "mode": "opus46-diagnostic",
        "status": "running",
        "suite": args.suite, "task": args.task, "seed": args.trial,
        "partition": "development",
        "query_budget": config["query_budget"],
        "max_actions": config["max_actions"],
        "max_recovery": config.get("max_recovery", 1),
        "tolerance": config["tolerance"],
        "policy_sha256": _sha256(policy_path),
        "world_program_sha256": _sha256(wp_dest),
        "live_config_sha256": _sha256(directory / "live_config.json"),
        "yaml_sha256": _sha256(yaml_path),
        "model_provenance": config.get("model_provenance", {}),
        "frame_count": 0, "action_count": 0,
        "query_used": 0, "recovery_used": 0,
    }
    _write_json(directory / "live_manifest.json", manifest)
    request = {"schema_version": 2, "args": {
        "suite": args.suite, "task": args.task, "trial": args.trial,
        "world_model_config": None,
        "output_dir": str(directory / "replay"),
        "replay_code": str(policy_path), "config": str(yaml_path),
        "model": config.get("model_provenance", {}).get(
            "model_id", "claude-opus-4-6"),
        "record_video": bool(args.record_video),
        "debug": bool(args.debug), "interactive": False,
    }}
    request_path = directory / "child_request.json"
    _write_json(request_path, request)
    return request_path


def _child_environment(parent: dict[str, str]) -> dict[str, str]:
    child = {
        name: value for name, value in parent.items()
        if name in _CHILD_ENV_NAMES}
    for name in _SERVICE_ENV_NAMES:
        value = parent.get(name)
        if value:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"http", "https"}
                    or not parsed.hostname
                    or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise ValueError(
                    f"{name} must be an HTTP(S) URL without credentials")
            child[name] = value.rstrip("/")
    child["PYTHONUNBUFFERED"] = "1"
    child["AWS_EC2_METADATA_DISABLED"] = "true"
    return child


def _stop_group(process: subprocess.Popen,
                grace: float = 2.0) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _launch_child(request_path: Path, environment: dict[str, str],
                  timeout: float, *,
                  module: str = "cap.world_model.live_broker") -> dict:
    directory = request_path.parent
    receipt: dict[str, Any] = {
        "schema_version": 2, "status": "launching", "exit_code": None,
        "timeout_seconds": timeout, "manifest_valid": False,
        "stdout": "child_stdout.log", "stderr": "child_stderr.log",
    }
    started = time.monotonic()
    process = None
    try:
        with ((directory / receipt["stdout"]).open("x") as out,
              (directory / receipt["stderr"]).open("x") as err):
            process = subprocess.Popen(
                [sys.executable, "-m", module,
                 "--child-request", str(request_path)],
                env=environment, stdout=out, stderr=err,
                start_new_session=True)
            receipt["pid"] = process.pid
            try:
                receipt["process_start"] = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            except (OSError, IndexError):
                receipt["process_start"] = None
            receipt["status"] = "running"
            _write_json(directory / "child_exit.json", receipt)
            try:
                receipt["exit_code"] = process.wait(timeout=timeout)
                receipt["status"] = (
                    "completed" if process.returncode == 0
                    else "nonzero")
            except subprocess.TimeoutExpired:
                _stop_group(process)
                receipt.update(status="timeout",
                               exit_code=process.returncode)
    except BaseException as exc:
        if process is not None and process.poll() is None:
            _stop_group(process)
        receipt.update(
            status=("launch_error" if process is None
                    else "interrupted"),
            error=type(exc).__name__)
        raise
    finally:
        receipt["elapsed_seconds"] = time.monotonic() - started
        _write_json(directory / "child_exit.json", receipt)
    return receipt


def run_live(args: Any) -> None:
    config, _ = load_live_config(args)
    environment = _child_environment(dict(os.environ))
    request_path = _prepare_live(args, config)
    directory = request_path.parent
    manifest_path = directory / "live_manifest.json"
    prepared = json.loads(manifest_path.read_text())
    try:
        receipt = _launch_child(
            request_path, environment,
            config["trial_timeout_seconds"])
    except BaseException as exc:
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            manifest = prepared
        manifest.update(status="failed",
                        child_error=type(exc).__name__)
        _write_json(manifest_path, manifest)
        raise
    try:
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
    except (OSError, ValueError) as exc:
        manifest = {**prepared, "status": "failed",
                    "child_manifest_error": type(exc).__name__}
    valid = (
        manifest.get("status") == "complete"
        and manifest.get("suite") == args.suite
        and manifest.get("task") == args.task
        and manifest.get("seed") == args.trial
        and manifest.get("policy_sha256") == prepared["policy_sha256"] == _sha256(
            directory / "frozen_policy.py")
        and manifest.get("world_program_sha256") == prepared["world_program_sha256"] == _sha256(directory / "world_program.py")
        and manifest.get("live_config_sha256") == prepared["live_config_sha256"] == _sha256(directory / "live_config.json")
        and manifest.get("yaml_sha256") == prepared["yaml_sha256"] == _sha256(directory / "source_config.yaml")
        and (directory / "live_tape.jsonl").is_file()
        and manifest.get("tape_sha256") == _sha256(directory / "live_tape.jsonl"))
    receipt["manifest_valid"] = valid
    _write_json(directory / "child_exit.json", receipt)
    if receipt["status"] != "completed" or not valid:
        manifest.update(status="failed",
                        child_status=receipt["status"])
        _write_json(manifest_path, manifest)
        raise RuntimeError(
            f"live broker child {receipt['status']} or incomplete; "
            f"inspect {directory}")


# -- child entry ---------------------------------------------------------

def _run_child_request_live(request_path: Path,
                            replay_runner: Any) -> None:
    directory = request_path.parent
    request = json.loads(request_path.read_text())
    if request.get("schema_version") != 2:
        raise ValueError("unsupported live child request")
    config = json.loads((directory / "live_config.json").read_text())
    manifest = json.loads((directory / "live_manifest.json").read_text())
    program_source = (directory / "world_program.py").read_text()
    broker = LiveBroker(directory, config, manifest, program_source)
    error = None
    try:
        replay_runner(SimpleNamespace(**request["args"]),
                      _world_capture=broker)
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        broker.close(error)
    if broker.manifest["status"] != "complete":
        raise RuntimeError(
            "live broker evidence is incomplete; inspect "
            f"{directory / 'live_manifest.json'}")


def _child_main(request_path: Path) -> None:
    import importlib.util

    replay_path = (
        Path(__file__).resolve().parents[2]
        / "scripts/libero/replay_trial.py")
    spec = importlib.util.spec_from_file_location(
        "_live_broker_replay", replay_path)
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    if os.environ.get("SAM3_SERVICE_URL"):
        from aspire.sim.cap.integrations.vision import sam3
        sam3.SERVICE_URL = os.environ["SAM3_SERVICE_URL"]
    if os.environ.get("PYROKI_SERVICE_URL"):
        from aspire.sim.cap.integrations.motion import pyroki
        pyroki.init_pyroki.__defaults__ = (
            os.environ["PYROKI_SERVICE_URL"],)
    _run_child_request_live(request_path, replay._run_replay)


if __name__ == "__main__":
    import argparse
    import importlib

    _parser = argparse.ArgumentParser(
        description="Private live broker child")
    _parser.add_argument("--child-request", type=Path, required=True)
    _parsed = _parser.parse_args()

    _pkg = Path(__file__).resolve().parent
    _root = _pkg.parents[1]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    _self = importlib.import_module("cap.world_model.live_broker")
    _self._child_main(_parsed.child_request.resolve())
