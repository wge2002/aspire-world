"""Blind replay of numerical evidence tapes, without simulator or model imports.

Tape JSONL records have ``frame_id``, ``action={api,args}``,
``robot_state={position,orientation_wxyz,gripper}``, and evaluator-only
``measurement={status,object_id,position,frame,reason?,source?}``. Positions are
XYZ metres in frame ``world``. Status is ``ok`` or ``unknown``. Extra capture
metadata is allowed but never passed to the program.

Row zero supplies one full-XYZ anchor (even if unknown), counted separately.
For each remaining row, advance and predict see only index/frame_id, numeric
action arguments and measured robot state. The saved prediction precedes any
measurement release. A query releases only selected axes, including on UNKNOWN;
its attempt consumes budget. All-row XYZ probes score the *saved* prediction and
are never assimilated. This is information-release ordering, not a claim that
prediction predates camera acquisition in a recorded tape.

initialize context: ``{object_id, frame, step, measurement, budget}``.
assimilate evidence: ``{index, frame_id, world_version, measurement, comparison}``.
Context and each step include a read-only budget snapshot ``{limit, used,
remaining}``; mutating a program input never changes the runtime-owned budget.
Released measurement: ``{status, object_id, frame, axes, values, reason}``, with
values in axis order, or None when unknown. Comparison contains status
SUPPORT/CONTRADICT/UNKNOWN, residual in axis order, and error_m (Euclidean norm).
The default 0.02 m tolerance is an engineering default, not a calibration result.
Adaptive scheduling implements the program's historical state/action request;
no future-program decision-utility algorithm is supplied by this runtime.
Policy action arguments can already encode the common policy's visual inputs.
Costs here therefore describe the passive observer's *additional* queries, not
all visual information consumed by the policy/observer combination.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Callable, Iterable

from .program import FrozenPythonProgram, ProgramExecutionError


def _copy(value: Any) -> Any:
    """A JSON roundtrip both detaches mutable values and forbids NaN/Infinity."""
    return json.loads(json.dumps(value, allow_nan=False))


def _number(value: Any) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value: Any, size: int, name: str) -> list[float]:
    if not isinstance(value, (tuple, list)) or len(value) != size or not all(_number(x) for x in value):
        raise ValueError(f"{name} must contain {size} finite numbers")
    return [float(x) for x in value]


def _axes(value: Any) -> tuple[int, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError("query_axes must be a nonempty subset of [0, 1, 2]")
    if any(type(axis) is not int or axis not in (0, 1, 2) for axis in value) or len(set(value)) != len(value):
        raise ValueError("query_axes must be a nonempty subset of [0, 1, 2]")
    return tuple(value)


def _numeric_args(value: Any) -> bool:
    if value is None or isinstance(value, bool) or _number(value):
        return True
    if isinstance(value, list):
        return all(_numeric_args(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _numeric_args(item) for key, item in value.items())
    return False


@dataclass(frozen=True)
class ReplayConfig:
    query_budget: int
    schedule: str = "fixed"
    fixed_indices: tuple[int, ...] = ()
    query_axes: tuple[int, ...] = (0, 1, 2)
    tolerance: float = 0.02

    def __post_init__(self) -> None:
        if type(self.query_budget) is not int or self.query_budget < 0:
            raise ValueError("query_budget must be a nonnegative integer")
        if self.schedule not in {"fixed", "adaptive"}:
            raise ValueError("schedule must be fixed or adaptive")
        indices = tuple(self.fixed_indices)
        if any(type(i) is not int or i <= 0 for i in indices) or len(set(indices)) != len(indices):
            raise ValueError("fixed_indices must be unique positive row indices; row zero is the anchor")
        if self.schedule == "adaptive" and indices:
            raise ValueError("adaptive schedule does not use fixed_indices")
        object.__setattr__(self, "fixed_indices", indices)
        object.__setattr__(self, "query_axes", _axes(self.query_axes))
        if not _number(self.tolerance) or self.tolerance < 0:
            raise ValueError("tolerance must be a nonnegative finite number")


def _frame(record: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError(f"tape row {index} must be an object")
    frame_id = record.get("frame_id")
    if isinstance(frame_id, bool) or not isinstance(frame_id, (str, int)):
        raise ValueError(f"tape row {index} requires a string or integer frame_id")
    action = record.get("action")
    if not isinstance(action, dict) or not isinstance(action.get("api"), str):
        raise ValueError(f"tape row {index} requires action.api")
    args = action.get("args", {})
    if not _numeric_args(args):
        raise ValueError("action.args may contain only numeric, boolean, null and container values")
    robot = record.get("robot_state")
    if not isinstance(robot, dict) or not _number(robot.get("gripper")):
        raise ValueError(f"tape row {index} requires measured robot_state")
    robot = {
        "position": _vector(robot.get("position"), 3, "robot position"),
        "orientation_wxyz": _vector(robot.get("orientation_wxyz"), 4, "robot orientation"),
        "gripper": float(robot["gripper"]),
    }
    measurement = record.get("measurement")
    if not isinstance(measurement, dict) or measurement.get("status") not in {"ok", "unknown"}:
        raise ValueError(f"tape row {index} requires an ok/unknown measurement")
    if not isinstance(measurement.get("object_id"), str) or not measurement["object_id"]:
        raise ValueError("measurement.object_id must be nonempty")
    if measurement.get("frame") != "world":
        raise ValueError("v0 measurements must use the world frame")
    position = None
    if measurement["status"] == "ok":
        position = _vector(measurement.get("position"), 3, "measurement position")
    return {
        "step": {"index": index, "frame_id": frame_id, "action": {"api": action["api"], "args": _copy(args)}, "robot_state": robot},
        "measurement": {
            "status": measurement["status"], "object_id": measurement["object_id"],
            "frame": "world", "position": position,
            "reason": str(measurement.get("reason") or "unavailable") if position is None else None,
        },
    }


def _release(measurement: dict[str, Any], axes: tuple[int, ...]) -> dict[str, Any]:
    position = measurement["position"]
    return {
        "status": measurement["status"], "object_id": measurement["object_id"],
        "frame": measurement["frame"], "axes": list(axes),
        "values": [position[axis] for axis in axes] if position is not None else None,
        "reason": measurement["reason"],
    }


def _prediction(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or "position" not in value:
        raise ProgramExecutionError("predict must return an object with position")
    position = value["position"]
    try:
        if position is not None:
            position = _vector(position, 3, "prediction position")
        axes = _axes(value.get("query_axes", [0, 1, 2]))
    except ValueError as exc:
        raise ProgramExecutionError(str(exc)) from exc
    request = value.get("request_query", False)
    if type(request) is not bool:
        raise ProgramExecutionError("request_query must be boolean")
    result = {"position": position, "request_query": request, "query_axes": list(axes)}
    if "hypotheses" in value:
        result["hypotheses"] = _copy(value["hypotheses"])
    return result


def _compare(prediction: dict[str, Any], measurement: dict[str, Any], tolerance: float) -> dict[str, Any]:
    if measurement["status"] != "ok":
        return {"status": "UNKNOWN", "residual": None, "error_m": None, "reason": "measurement_unavailable"}
    if prediction["position"] is None:
        return {"status": "UNKNOWN", "residual": None, "error_m": None, "reason": "prediction_abstained"}
    residual = [value - prediction["position"][axis] for axis, value in zip(measurement["axes"], measurement["values"])]
    error = math.sqrt(sum(value * value for value in residual))
    return {"status": "SUPPORT" if error <= tolerance else "CONTRADICT", "residual": residual, "error_m": error, "reason": None}


def replay_tape(
    records: Iterable[dict[str, Any]],
    program: FrozenPythonProgram,
    config: ReplayConfig,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Return ``{events, summary}``; a program failure ends replay with status error.

    ``event_sink`` may write JSONL. It is evaluator-only and must not be exposed
    to the program. Invalid tape/configuration raises ValueError before running
    code. All measurements are evaluator-owned, including independent probes.
    """
    frames = [_frame(record, index) for index, record in enumerate(records)]
    if not frames:
        raise ValueError("tape must contain an anchor row")
    ids = [frame["step"]["frame_id"] for frame in frames]
    if len(set(ids)) != len(ids):
        raise ValueError("frame_id must be unique within a tape")
    object_id = frames[0]["measurement"]["object_id"]
    if any(frame["measurement"]["object_id"] != object_id for frame in frames):
        raise ValueError("v0 tape must track one consistent object_id")

    events: list[dict[str, Any]] = []
    query_used = 0
    query_statuses = {name: 0 for name in ("SUPPORT", "CONTRADICT", "UNKNOWN")}
    probe_statuses = {name: 0 for name in ("SUPPORT", "CONTRADICT", "UNKNOWN")}
    probe_errors: list[float] = []
    measurement_valid_total = sum(frame["measurement"]["status"] == "ok" for frame in frames[1:])
    valid_measurements_scored = 0
    prediction_available_total = 0
    abstentions_on_valid_measurements = 0
    version = 0
    completed = 0
    status = "complete"
    failure = None
    operation = "initialize"

    def budget() -> dict[str, int]:
        return {"limit": config.query_budget, "used": query_used, "remaining": config.query_budget - query_used}

    step = {**frames[0]["step"], "budget": budget()}

    def emit(kind: str, **fields: Any) -> None:
        event = _copy({"event_id": len(events), "event": kind, **fields})
        events.append(event)
        if event_sink is not None:
            event_sink(_copy(event))

    anchor = _release(frames[0]["measurement"], (0, 1, 2))
    emit("anchor_released", index=0, frame_id=step["frame_id"], measurement=anchor, cost=1)
    try:
        state = program.call("initialize", {"object_id": object_id, "frame": "world", "step": _copy(step), "measurement": _copy(anchor), "budget": budget()})
        emit("world_initialized", world_version=version)
        completed = 1
        for frame in frames[1:]:
            step = {**frame["step"], "budget": budget()}
            common = {"index": step["index"], "frame_id": step["frame_id"]}
            operation = "advance"
            state = program.call("advance", _copy(state), _copy(step))
            version += 1
            emit("world_advanced", **common, world_version=version)
            operation = "predict"
            prediction = _prediction(program.call("predict", _copy(state), _copy(step)))
            # This immutable JSON snapshot is the only input to both comparisons.
            prediction = _copy(prediction)
            emit("prediction_committed", **common, world_version=version, prediction=prediction)
            # Evaluator-only: score the prior even if later assimilation fails.
            probe = _compare(prediction, _release(frame["measurement"], (0, 1, 2)), config.tolerance)
            probe_statuses[probe["status"]] += 1
            prediction_available_total += int(prediction["position"] is not None)
            if frame["measurement"]["status"] == "ok":
                valid_measurements_scored += 1
                abstentions_on_valid_measurements += int(prediction["position"] is None)
            if probe["error_m"] is not None:
                probe_errors.append(probe["error_m"])
            emit("independent_probe", **common, comparison=probe, feedback=False,
                 measurement_status=frame["measurement"]["status"],
                 prediction_available=prediction["position"] is not None)
            requested = step["index"] in config.fixed_indices if config.schedule == "fixed" else prediction["request_query"]
            axes = config.query_axes if config.schedule == "fixed" else tuple(prediction["query_axes"])
            if requested:
                emit("query_requested", **common, axes=list(axes), schedule=config.schedule)
                if query_used >= config.query_budget:
                    emit("query_denied", **common, reason="budget_exhausted")
                else:
                    query_used += 1
                    measurement = _release(frame["measurement"], axes)
                    emit("measurement_released", **common, measurement=measurement, query_number=query_used, cost=1)
                    comparison = _compare(prediction, measurement, config.tolerance)
                    query_statuses[comparison["status"]] += 1
                    emit("comparison", **common, world_version=version, comparison=comparison)
                    evidence = {**common, "world_version": version, "measurement": measurement, "comparison": comparison}
                    operation = "assimilate"
                    state = program.call("assimilate", _copy(state), _copy(evidence))
                    version += 1
                    emit("world_assimilated", **common, world_version=version)
            completed += 1
    except (ProgramExecutionError, ValueError, TypeError) as exc:
        status = "error"
        failure = {"operation": operation, "type": type(exc).__name__, "message": str(exc)}
        emit("program_error", index=step["index"], frame_id=step["frame_id"], error=failure)

    summary = {
        "status": status, "incomplete": status != "complete", "error": failure, "frames_total": len(frames),
        "frames_completed": completed, "predictions": sum(probe_statuses.values()),
        "probe_expected_count": len(frames) - 1,
        "probe_missing_count": len(frames) - 1 - sum(probe_statuses.values()),
        "anchor_attempts": 1, "anchor_valid": int(anchor["status"] == "ok"),
        "query_budget": config.query_budget, "queries_used": query_used,
        "query_comparisons": query_statuses, "probe_comparisons": probe_statuses,
        "probe_mean_error_m": sum(probe_errors) / len(probe_errors) if probe_errors else None,
        "probe_mean_error_scope": "conditional on valid measurement and available prediction; compare with coverage and paired frame errors, never rank this mean alone",
        "probe_valid_count": len(probe_errors), "tolerance_m": config.tolerance,
        "probe_measurement_valid_count": measurement_valid_total,
        "probe_measurement_unavailable_count": len(frames) - 1 - measurement_valid_total,
        "probe_prediction_available_count": prediction_available_total,
        "probe_abstentions_on_valid_measurements": abstentions_on_valid_measurements,
        "probe_missing_predictions_on_valid_measurements": measurement_valid_total - valid_measurements_scored,
        "probe_coverage_on_valid_measurements": len(probe_errors) / measurement_valid_total if measurement_valid_total else None,
        "schedule": config.schedule,
        "unreached_fixed_indices": [index for index in config.fixed_indices if index >= len(frames)],
        "cost_scope": "passive observer incremental queries; common policy visual information is not metered here",
        "adaptive_scope": "generated history/action/state request; no future-program utility implementation",
        "probe_feedback": False, "world_version": version,
    }
    emit("replay_completed", summary=summary)
    return {"events": events, "summary": summary}
