"""Development-only calibration of the final self-evaluation, by frozen pair.

Runs after the simulator process has exited. No evaluator labels or aggregate
metrics are installed in the generated world's namespace.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path


def trial_feedback(directory, record):
    directory = Path(directory)
    report = {"revision": "r1", "seed": record["seed"], "phase": record["phase"],
              "policy_sha256": record.get("bundle", {}).get("policy"),
              "world_sha256": record.get("bundle", {}).get("world"),
              "status": "unavailable", "category": None,
              "scope": "development only; final judgment against final outcome"}
    if record["seed"] not in range(51, 66) or record["phase"] == "diagnostic":
        raise ValueError("calibration accepts development task trials only")
    try:
        config = json.loads((directory / "executable_world_config.json").read_text())
        manifest = json.loads((directory / "judgment_world/manifest.json").read_text())
        policy_sha = hashlib.sha256((directory / "code.py").read_bytes()).hexdigest()
        world_sha = hashlib.sha256((directory / "world_program.py").read_bytes()).hexdigest()
        report.update(policy_sha256=policy_sha, world_sha256=world_sha)
        if policy_sha != config["policy_sha256"] or world_sha != config["world_program_sha256"] or world_sha != manifest["world_sha256"]:
            raise ValueError("trial source/manifest identity mismatch")
        if record.get("bundle") and (record["bundle"].get("policy") != policy_sha or record["bundle"].get("world") != world_sha):
            raise ValueError("ledger bundle differs from executed sources")
        evaluations = manifest.get("self_evaluations", [])
        final = evaluations[-1] if evaluations else None
        report["final_self_evaluation"] = final
        if record.get("status") != "complete" or record.get("world_error") or record.get("sandbox_rc") != 0:
            report["reason"] = "incomplete or erroneous execution; excluded from calibration"
        elif final is None or final.get("binding") != "shadow":
            report["reason"] = "no final live self-evaluation"
        else:
            label = record.get("task_completed")
            if label not in (0, 1, False, True):
                raise ValueError("missing evaluator label")
            verdict = final["verdict"]
            category = "unknown" if verdict == "unknown" else {
                ("true", True): "true_positive", ("true", False): "false_positive",
                ("false", True): "false_negative", ("false", False): "true_negative",
            }[(verdict, bool(label))]
            report.update(status="audited", category=category, evaluator_success=bool(label))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["reason"] = str(exc)
    (directory / "foundation_calibration.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def aggregate(rows):
    groups = {}
    for row in rows:
        report = row.get("foundation_calibration")
        if not report:
            continue
        key = (report.get("policy_sha256"), report.get("world_sha256"))
        group = groups.setdefault(key, {"policy_sha256": key[0], "world_sha256": key[1],
                                       "counts": Counter(), "trials": []})
        group["counts"][report.get("category") or "unavailable"] += 1
        group["trials"].append({"seed": row["seed"], "directory": row["directory"],
                                "category": report.get("category")})
    return {"scope": "development only; repeated attempts are not independent samples",
            "by_bundle": list(groups.values())}
