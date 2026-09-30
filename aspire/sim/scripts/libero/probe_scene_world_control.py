"""Inspect one frozen scene program after an explicitly manual grounding control.

Reuses two already saved public measurements. No model/perception/simulator calls.
Never replaces original experiment inputs, generated source, or results.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import runpy
import statistics
import time


def read(path):
    return json.loads(path.read_text())


def save(path, data):
    with path.open("x") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def distance(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def run(root, manual, worker_source, output):
    output.mkdir(parents=True, exist_ok=False)
    original = read(root / "scene-input.json")
    source = (root / "scene/world_program.py").read_text()
    manual_scene = read(manual)
    replacements = {e["id"]: e for e in manual_scene["entities"] if e["role"] in {"manipulated", "target"}}
    scene = copy.deepcopy(original)
    for entity in scene["entities"]:
        if entity["role"] in {"manipulated", "target"}:
            entity["measurement"] = copy.deepcopy(replacements[entity["id"]]["measurement"])
            assert entity["measurement"]["status"] == "ok"
    main = copy.deepcopy(scene)
    main["entities"] = [e for e in scene["entities"] if e["role"] in {"manipulated", "target"}]
    save(output / "scene-input.json", scene)
    save(output / "main-scene-input.json", main)
    program = runpy.run_path(str(worker_source))["FrozenPythonProgram"](source, timeout_s=3)
    timings = {"same_program_2_entities": [], "same_program_11_entities": []}
    for repeat in range(3):
        arms = [("same_program_2_entities", main), ("same_program_11_entities", scene)]
        if repeat % 2:
            arms.reverse()
        for label, context in arms:
            started = time.perf_counter()
            state = program.call("initialize", context)
            seconds = time.perf_counter() - started
            assert set(state["objects"]) == {e["id"] for e in context["entities"]}
            timings[label].append(seconds)

    near = copy.deepcopy(scene)
    bowl = next(e for e in near["entities"] if e["role"] == "manipulated")
    oid = bowl["id"]
    p0 = bowl["measurement"]["position"]
    near["robot_state"]["position"] = [x + d for x, d in zip(p0, [0, 0, 0.02])]
    initial = program.call("initialize", near)
    steps = []

    def record(label, state, step, evidence=None):
        pred = program.call("predict", state, step)
        steps.append({"label": label, "state": state, "step": step,
                      "prediction": pred, "evidence": evidence})
        return pred

    step = {"action": {"api": "close_gripper", "arguments": {}},
            "robot_state": near["robot_state"], "budget": {"remaining": 4}}
    closed = program.call("advance", initial, step)
    record("synthetic_close_near_bowl", closed, step)
    move = copy.deepcopy(step)
    move["action"] = {"api": "goto_pose", "arguments": {"position": [x + d for x, d in zip(p0, [0.3, 0, 0.5])]}}
    delta = [0.04, -0.02, 0.08]
    move["robot_state"]["position"] = [x + d for x, d in zip(near["robot_state"]["position"], delta)]
    moved = program.call("advance", closed, move)
    pred = record("synthetic_measured_motion", moved, move)
    conditional_position = pred["objects"][oid]["position"]
    offset_preserving = [x + d for x, d in zip(p0, delta)]

    unknown = {"object_id": oid, "status": "unknown", "position": None}
    unknown_state = program.call("assimilate", moved, unknown)
    record("unknown_evidence", unknown_state, move, unknown)
    free_evidence = {"object_id": oid, "status": "ok", "position": p0}
    fitted = program.call("assimilate", moved, free_evidence)
    fitted_pred = record("synthetic_object_stayed_at_original_position", fitted, move, free_evidence)

    next_move = copy.deepcopy(move)
    next_move["robot_state"]["position"][2] += 0.02
    moved_again = program.call("advance", fitted, next_move)
    record("motion_after_counterevidence", moved_again, next_move)
    opening = copy.deepcopy(next_move)
    opening["action"] = {"api": "open_gripper", "arguments": {}}
    opened = program.call("advance", moved_again, opening)
    record("opening_without_contact_observation", opened, opening)

    report = {
        "control": "Manual association of two previously measured entities, followed by synthetic action/evidence inputs. This is not the original end-to-end result.",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "manual_measurement_source": str(manual),
        "new_model_calls": 0, "new_perception_requests": 0, "new_simulator_trials": 0,
        "initialize_timing_host": "local coordinator; includes isolated worker startup, compilation, JSON transfer",
        "initialize_seconds": {name: {"values": values, "median": statistics.median(values)} for name, values in timings.items()},
        "findings": {
            "attachment_after_closure": closed["objects"][oid]["attachment"],
            "closure_claims_grasp_confirmed": closed["objects"][oid].get("attachment_uncertainty", {}).get("grasp_confirmed"),
            "predicted_position": conditional_position,
            "offset_preserving_prediction": offset_preserving,
            "offset_loss_m": distance(conditional_position, offset_preserving),
            "prediction_equals_measured_gripper_position": conditional_position == move["robot_state"]["position"],
            "position_changed_but_visible_bounds_unchanged": moved["objects"][oid]["position"] != initial["objects"][oid]["position"] and moved["objects"][oid]["visible_bounds"] == initial["objects"][oid]["visible_bounds"],
            "counterevidence_residual_m": distance(conditional_position, p0),
            "assimilated_readout_matches_counterevidence": fitted_pred["objects"][oid]["position"] == p0,
            "attachment_after_counterevidence": fitted["objects"][oid]["attachment"],
            "prediction_after_next_motion": moved_again["objects"][oid]["position"],
            "query_with_unknown_objects": pred["request_query"],
            "interpretation": "These are executable code-behavior diagnostics under declared synthetic inputs, not measured physical prediction errors or task success."
        },
        "steps": steps,
    }
    save(output / "control-report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "steps"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manual-scene", type=Path, required=True)
    parser.add_argument("--worker-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.manual_scene, args.worker_source, args.output)
