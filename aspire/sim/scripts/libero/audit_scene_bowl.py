"""Independent, read-only audit of the single-arm scene-world trial artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import statistics


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blocks(path):
    return [s.strip() for s in re.split(r"^# Code block \d+\s*\n", path.read_text(), flags=re.MULTILINE) if s.strip()]


def audit(root, output):
    manifest = read(root / "trials/scene_manifest.json")
    assert set(manifest["rows"]) == {str(i) for i in range(51, 66)}
    hashes = manifest["frozen_hashes"]
    assert digest(root / "frozen/world_program.py") == hashes["world"]
    assert digest(root / "frozen/shared_policy.py") == hashes["policy"]
    assert digest(root / "frozen/scene_config.json") == hashes["config"]
    config = read(root / "frozen/scene_config.json")
    expected_ids = {e["id"] for e in config["scene_inventory"]}
    rows = []
    for seed in range(51, 66):
        planned = manifest["rows"][str(seed)]
        record = {"seed": seed, "status": planned["status"], "attempted": planned["attempted"], "verified": False}
        directory = root / "trials/episodes" / f"seed_{seed}" / "scene"
        if not planned["attempted"]:
            rows.append(record)
            continue
        try:
            paths = list(directory.rglob("live_manifest.json"))
            assert len(paths) == 1, "world manifest count"
            live = paths[0].parent
            mf = read(paths[0])
            assert (mf["suite"], mf["task"], mf["seed"]) == ("libero_goal_swap", "put_the_bowl_on_the_plate", seed)
            assert mf["mode"] == "opus46-scene-diagnostic"
            assert mf["adapter_sha256"] == hashes["scene_broker"]
            for key, name in [("policy_sha256", "frozen_policy.py"), ("world_program_sha256", "world_program.py"),
                              ("live_config_sha256", "live_config.json"), ("yaml_sha256", "source_config.yaml"),
                              ("tape_sha256", "live_tape.jsonl")]:
                assert mf[key] == digest(live / name), "hash mismatch: " + name
            assert mf["policy_sha256"] == hashes["policy"] and mf["world_program_sha256"] == hashes["world"]
            assert mf["yaml_sha256"] == hashes["yaml"]
            terminal = [p for p in directory.rglob(f"trial_{seed:02d}_sandboxrc_*") if p.is_dir()]
            assert len(terminal) == 1, "terminal folder count"
            trial = terminal[0]
            match = re.fullmatch(rf"trial_{seed:02d}_sandboxrc_(-?\d+)_reward_(-?\d+\.\d+)_taskcompleted_([01])", trial.name)
            assert match is not None
            sandbox, reward, success = int(match[1]), float(match[2]), bool(int(match[3]))
            assert blocks(trial / "code.py") == blocks(root / "frozen/shared_policy.py"), "executed code changed"
            official = mf["trial_result"]
            assert official["task_completed"] is success and official["sandbox_rc"] == sandbox
            assert abs(official["reward"]-reward) <= .000501
            summary = (trial / "summary.txt").read_text()
            assert f"Trial {seed} — libero_goal_swap/put_the_bowl_on_the_plate" in summary
            assert f"  Task Completed: {success}\n" in summary and f"  Sandbox failed: {sandbox}\n" in summary
            trace = read(trial / "trace.json")
            assert isinstance(trace, list)
            tape = [json.loads(line) for line in (live / "live_tape.jsonl").read_text().splitlines()]
            assert [e["event_id"] for e in tape] == list(range(len(tape)))
            assert mf["events_count"] == len(tape)
            commits, comparisons, states = {}, [], {}
            anchors = [e for e in tape if e["event"] == "scene_anchor_committed"]
            assert len(anchors) == 1
            context = anchors[0]["context"]
            assert {e["id"] for e in context["entities"]} == expected_ids
            anchor = {e["id"]: e["measurement"] for e in context["entities"]}
            initial_bowl = anchor["bowl"].get("position")
            for event in tape:
                kind = event["event"]
                if kind == "world_advanced":
                    states[event["frame"]] = event["state"]
                elif kind == "prediction_committed":
                    assert set(event["prediction"]["objects"]) == expected_ids
                    commits[event["frame"]] = event
                elif kind == "query_comparison":
                    pred = commits[event["prediction_frame"]]
                    assert pred["event_id"] < event["event_id"], "evidence released before prediction"
                    assert pred["prediction"] == event["full_scene_prediction"]
                    assert event["query_number"] == len(comparisons)+1
                    position = pred["prediction"]["objects"]["bowl"]["position"]
                    measured = event["measurement"]
                    error = None
                    expected = "UNKNOWN"
                    if measured["status"] == "ok" and position is not None:
                        error = math.sqrt(sum((x-y)**2 for x, y in zip(measured["values"], position)))
                        assert math.isclose(error, event["comparison"]["error_m"], abs_tol=1e-9)
                        expected = "SUPPORT" if error <= config["tolerance"] else "CONTRADICT"
                    assert event["comparison"]["status"] == expected
                    subsequent = next((e for e in tape[event["event_id"]+1:] if e["event"] in {"world_assimilated", "program_error"}), None)
                    assert subsequent is not None
                    before = states[event["prediction_frame"]]["objects"]["bowl"]
                    robot = states[event["prediction_frame"]]["robot_state"].get("position")
                    after = subsequent.get("state", {}).get("objects", {}).get("bowl", {})
                    comparisons.append({"query": event["query_number"], "prediction_frame": event["prediction_frame"],
                        "status": expected, "error_m": error, "predicted": position, "observed": measured["values"],
                        "attachment_before": before.get("attachment"), "attachment_after": after.get("attachment"),
                        "robot_position": robot,
                        "predicted_equals_robot_position": position is not None and robot is not None and
                            all(math.isclose(x, y, abs_tol=1e-9) for x, y in zip(position, robot)),
                        "observed_z_change_from_anchor": measured["values"][2] - initial_bowl[2]
                            if measured["status"] == "ok" and initial_bowl is not None else None,
                        "bounds_changed_by_assimilation": before.get("visible_bounds") != after.get("visible_bounds"),
                        "position_after": after.get("position"), "query_event": event["event_id"],
                        "prediction_event": pred["event_id"]})
            assert len(comparisons) == mf["query_used"] <= 4
            assert len(commits) == mf["action_count"]
            assert 0 <= mf["action_count"] <= 30 and 0 <= mf["recovery_used"] <= 1
            assert sum(e["event"] == "recovery_invoked" for e in tape) == mf["recovery_used"]
            assert mf["anchor_attempts"] == 1
            assert mf["generation_model_requests_this_trial"] == 0
            policy_decisions = []
            for line in summary.split("\nEnvironment response:\n", 1)[-1].splitlines():
                value = line.strip()
                if value.startswith(("Stdout:", "Stderr:")):
                    value = value.split(":", 1)[1].strip()
                try:
                    item = json.loads(value)
                    if isinstance(item, dict) and "policy_event" in item:
                        policy_decisions.append(item)
                except ValueError:
                    pass
            record.update(verified=True, task_completed=success, reward=reward, sandbox_rc=sandbox,
                mechanism_status=mf["status"], duration_seconds=planned["elapsed_seconds"],
                motor_actions=mf["action_count"], recovery_used=mf["recovery_used"], queries_used=mf["query_used"],
                anchor_known=mf["scene_anchor_known"], anchor_unknown=mf["scene_anchor_unknown"],
                anchor_objects=mf["scene_anchor_objects"], comparisons=comparisons,
                policy_decisions=policy_decisions, broker_sensor_costs=mf["broker_sensor_costs"],
                recovery_logged_by_policy=planned["artifacts"].get("recovery_used"),
                anchor_measurements=anchor, initial_bowl_position=initial_bowl,
                policy_api_errors=[{"step": e["step"], "function": e["function"], "error": e["error"]}
                                   for e in trace if e.get("error")],
                prediction_count=len(commits),
                program_query_requests=sum(e["prediction"]["request_query"] for e in commits.values()),
                program_errors=[e for e in tape if e["event"] == "program_error"],
                trial_path=str(trial.relative_to(root)), live_path=str(live.relative_to(root)))
        except (AssertionError, OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
            record["audit_error"] = type(exc).__name__ + ": " + str(exc)
        rows.append(record)
    valid = [r for r in rows if r["verified"]]
    comparisons = [c for r in valid for c in r["comparisons"]]
    complete = len(valid) == 15 and all(r["status"] == "completed" and r["mechanism_status"] == "complete" for r in valid)
    result = {"complete": complete, "planned": 15, "attempted": sum(r["attempted"] for r in rows),
        "verified_terminal_results": len(valid), "successes": sum(r["task_completed"] for r in valid),
        "success_seeds": [r["seed"] for r in valid if r["task_completed"]],
        "mechanism_complete": sum(r["mechanism_status"] == "complete" for r in valid),
        "query_status_counts": dict(Counter(c["status"] for c in comparisons)),
        "queries": len(comparisons), "recoveries": sum(r["recovery_used"] for r in valid),
        "policy_logged_recoveries": sum(bool(r["recovery_logged_by_policy"]) for r in valid),
        "predicted_bowl_equals_robot_position": sum(c["predicted_equals_robot_position"] for c in comparisons),
        "support_without_attachment_hypothesis": sum(c["status"] == "SUPPORT" and c["attachment_before"] not in {"conditionally_attached", "attached"} for c in comparisons),
        "contradiction_without_attachment_revision": sum(c["status"] == "CONTRADICT" and c["attachment_before"] == c["attachment_after"] for c in comparisons),
        "median_episode_seconds": statistics.median(r["duration_seconds"] for r in valid) if valid else None,
        "new_model_calls": 0, "held_out_trials": 0, "frozen_hashes": hashes, "rows": rows}
    with output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({k: v for k, v in result.items() if k not in {"rows", "frozen_hashes"}}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit(args.evidence, args.output)
