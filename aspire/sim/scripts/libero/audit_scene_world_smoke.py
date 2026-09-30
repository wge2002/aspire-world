"""Audit saved scene-world output without model, perception, or simulator calls.

Synthetic action/evidence probes inspect code behavior, not physical accuracy.
Generated code still executes only in the existing isolated numeric worker.
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


def read(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    with Path(path).open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def close(a, b):
    return (isinstance(a, list) and isinstance(b, list) and len(a) == len(b)
            and all(math.isclose(x, y, abs_tol=1e-6, rel_tol=0) for x, y in zip(a, b)))


def extracted_source(response):
    source = "\n".join(block["text"] for block in response["content"]
                       if block.get("type") == "text").strip()
    if source.startswith("```"):
        source = source.split("\n", 1)[1].rsplit("```", 1)[0]
    return source + "\n"


def probe(arm, root, worker, output, scenario):
    source = (root / arm / "world_program.py").read_text()
    context = read(root / ("main-scene-input.json" if arm == "main" else "scene-input.json"))
    program = worker(source, timeout_s=3)
    expected = {entity["id"] for entity in context["entities"]}
    target = next(entity for entity in context["entities"] if entity["role"] == "manipulated")
    if scenario == "synthetic_near_object":
        position = target["measurement"].get("position")
        if position is None:
            result = {"status": "skipped", "reason": "manipulated object has no measured position"}
            save(output / (arm + "-" + scenario + "-probes.json"), {"result": result})
            return result
        context["robot_state"]["position"] = [v + d for v, d in zip(position, [0.0, 0.0, 0.02])]
    initial_step = {"action": {"api": "no_motion", "arguments": {}},
                    "robot_state": context["robot_state"], "budget": {"remaining": 4}}
    records = []

    def snapshot(label, state, step, **extra):
        prediction = program.call("predict", state, step)
        records.append({"label": label, "step": step, "state": state,
                        "prediction": prediction, **extra})
        return prediction

    try:
        state = program.call("initialize", context)
        snapshot("initial", state, initial_step)
        step = copy.deepcopy(initial_step)
        step["action"]["api"] = "close_gripper"
        state = program.call("advance", state, step)
        snapshot("closure_without_new_object_evidence", state, step)

        move = copy.deepcopy(step)
        move["action"] = {"api": "goto_pose", "arguments": {
            "position": [v + d for v, d in zip(context["robot_state"]["position"], [0.3, 0.0, 0.5])]}}
        move["robot_state"]["position"] = [v + d for v, d in zip(
            context["robot_state"]["position"], [0.04, -0.02, 0.08])]
        state = program.call("advance", state, move)
        snapshot("synthetic_measured_motion_differs_from_command", state, move)

        unknown = {"object_id": target["id"], "status": "unknown", "position": None}
        state_unknown = program.call("assimilate", state, unknown)
        snapshot("unknown_evidence", state_unknown, move, evidence=unknown)

        observed = target["measurement"].get("position")
        evidence_match = None
        if observed is not None:
            # Deliberately synthetic; this is not another observation of the scene.
            observed = [v + d for v, d in zip(observed, [0.01, 0.02, 0.03])]
            evidence = {"object_id": target["id"], "status": "ok", "position": observed}
            state = program.call("assimilate", state, evidence)
            pred = snapshot("synthetic_numeric_assimilation", state, move, evidence=evidence)
            evidence_match = close(pred["objects"][target["id"]]["position"], observed)

        opening = copy.deepcopy(move)
        opening["action"] = {"api": "open_gripper", "arguments": {}}
        state = program.call("advance", state, opening)
        snapshot("opening_without_new_object_evidence", state, opening)
        result = {"status": "executed", "snapshots": len(records),
                  "all_entity_ids_preserved": all(set(r["prediction"]["objects"]) == expected for r in records),
                  "assimilated_numeric_readout_matches": evidence_match,
                  "interpretation": "Synthetic interface probes only. Inspect conditional branches and evidence provenance; no physical validity or success claim."}
    except Exception as exc:
        result = {"status": "execution_error", "error_type": type(exc).__name__,
                  "message": str(exc), "snapshots_completed": len(records)}
    save(output / (arm + "-" + scenario + "-probes.json"),
         {"result": result, "scenario": scenario, "initialize_context": context, "records": records})
    return result


def run(root, worker_source, output):
    output.mkdir(parents=True, exist_ok=False)
    perception_summary = read(root / "perception-summary.json")
    inventory = read(root / "inventory.json")
    requests = sorted(root.glob("*-request.json"))
    receipts = {p.stem.removesuffix("-receipt"): read(p) for p in root.glob("*-receipt.json")}
    author_checks = []
    for arm in ["main", "scene"]:
        if not (root / arm / "world_program.py").exists():
            author_checks.append({"arm": arm, "status": "no_source_returned",
                                  "receipt": receipts.get("world-" + arm)})
            continue
        data = (root / arm / "world_program.py").read_bytes()
        response = read(root / ("world-" + arm + "-response.json"))
        result = read(root / arm / "result.json")
        author_checks.append({"arm": arm,
            "matches_raw_response_source": data == extracted_source(response).encode(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "matches_recorded_hash": hashlib.sha256(data).hexdigest() == result["source_sha256"],
            "response_model": response.get("model"), "stop_reason": response.get("stop_reason")})
    records = [read(p) for p in sorted(root.glob("r*-*/measurements.json"))]
    timing = {}
    geometry = {}
    for name in ["main_serial", "scene_serial", "scene_concurrent4"]:
        selected = [r for r in records if r["condition"] == name]
        seconds = [r["wall_seconds"] for r in selected]
        median = statistics.median(seconds)
        timing[name] = {"repeats": len(selected), "seconds": seconds, "median_seconds": median,
                       "summary_matches": math.isclose(median, perception_summary[name]["median_seconds"], abs_tol=1e-9),
                       "known_counts": [r["known_count"] for r in selected]}
    for entity in inventory["objects"]:
        rows = [m for r in records for m in r["measurements"] if m["object_id"] == entity["id"]]
        known = [m for m in rows if m["status"] == "ok"]
        geometry[entity["id"]] = {"observations": len(rows), "statuses": [m["status"] for m in rows],
                                 "known_positions_identical": len({json.dumps(m["position"]) for m in known}) <= 1,
                                 "known_point_counts_identical": len({m["point_count"] for m in known}) <= 1}
    serial = timing["scene_serial"]["median_seconds"]
    parallel = timing["scene_concurrent4"]["median_seconds"]
    audit = {"model_request_records": len(requests), "receipts": receipts,
             "original_batch_status": "complete" if (root / "summary.json").exists() else "incomplete",
             "original_failure": read(root / "failure.json") if (root / "failure.json").exists() else None,
             "request_cap_respected": len(requests) == 3,
             "source_provenance": author_checks, "timing": timing, "geometry_repeatability": geometry,
             "timed_batches": len(records), "timed_perception_requests": sum(len(r["measurements"]) for r in records),
             "warmup_requests": int((root / "warmup/measurement.json").exists()),
             "scene_speed_ratio": serial / parallel,
             "scene_wall_reduction_percent": 100 * (1 - parallel / serial),
             "parallel_scene_extra_over_main_seconds": parallel - timing["main_serial"]["median_seconds"],
             "new_model_calls_in_audit": 0, "new_perception_calls_in_audit": 0, "new_simulator_trials": 0,
             "worker_source_sha256": hashlib.sha256(worker_source.read_bytes()).hexdigest()}
    worker = runpy.run_path(str(worker_source))["FrozenPythonProgram"]
    audit["synthetic_probes"] = {arm: {
        scenario: probe(arm, root, worker, output, scenario)
        for scenario in ["saved_proprio", "synthetic_near_object"]}
        for arm in ["main", "scene"] if (root / arm / "world_program.py").exists()}
    save(output / "audit.json", audit)
    print(json.dumps({k: v for k, v in audit.items() if k not in {"receipts", "geometry_repeatability"}}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--worker-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.worker_source, args.output)
