#!/usr/bin/env python3
"""Read-only C progress and mechanism accounting; never input to a solver."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def summarize_cell(control):
    case = read(control / "case.json")
    task = Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    ledger = read(task / "development_state.json", {})
    trials = ledger.get("trials", [])
    charged = [r for r in trials if r.get("charged")]
    groups = defaultdict(list)
    for row in charged:
        if row.get("executed") and row.get("status") != "running":
            groups[row["bundle_sha256"]].append(row)
    bundles = {key: {"executions": len(rows), "distinct_seeds": len({r["seed"] for r in rows}),
                     "pass_seeds": sorted({r["seed"] for r in rows if r.get("task_completed") and r.get("sandbox_rc") == 0}),
                     "statuses": dict(Counter(r["status"] for r in rows))} for key, rows in groups.items()}
    selected = ledger.get("selected") or {}
    counts = Counter(r["seed"] for r in charged)
    campaign = read(control / "campaign_state.json", {})
    report = {"id": case["id"], "arm": case["c_arm"], "gpu": case["gpu"],
              "campaign_status": campaign.get("status", "queued"), "blocker": campaign.get("blocker"),
              "development": {"charged": len(charged), "by_seed": dict(counts),
                              "statuses": dict(Counter(r["status"] for r in charged)),
                              "snapshots": sum(r["phase"] == "snapshot" for r in trials),
                              "budget_violations": {s: n for s, n in counts.items() if n > 3},
                              "bundle_evidence": bundles,
                              "selected_bundle": selected.get("bundle_sha256"),
                              "selected_evidence": bundles.get(selected.get("bundle_sha256"))}}
    checks = task / "attempts/offline/checks.jsonl"
    offline = [json.loads(line) for line in checks.read_text().splitlines() if line.strip()] if checks.exists() else []
    reports = [r for item in offline for r in item["reports"]]
    report["offline"] = {"candidate_scenario_checks": len(offline),
                         "rejected_before_simulation": sum(c["status"] == "rejected" for c in offline),
                         "statuses_by_binding": {mode: dict(Counter(r["status"] for r in reports if r["mode"] == mode))
                                                 for mode in ("rehearsal", "replay")},
                         "reported_compute_seconds": sum(r.get("elapsed_seconds", 0) for r in reports),
                         "cost_note": "Rejections are not automatically claims of avoided physical failures; unsupported replay is not a failure prediction."}
    evaluation = read(control / "heldout/heldout_state.json", {})
    rows = evaluation.get("seeds", {})
    matrix = Counter()
    verdicts = Counter()
    mismatches = []
    for key, row in rows.items():
        seed = int(key)
        manifest = read(control / "heldout" / f"seed_{seed:02d}" / "judgment_world/manifest.json", {})
        decisions = manifest.get("self_evaluations", [])
        verdict = decisions[-1]["verdict"] if decisions else "missing"
        verdicts[verdict] += 1
        label = (row.get("result") or {}).get("task_completed")
        if label is not None and verdict in {"true", "false"}:
            bucket = ("true_positive" if label else "false_positive") if verdict == "true" else ("false_negative" if label else "true_negative")
            matrix[bucket] += 1
        if manifest and manifest.get("arm") != case["c_arm"]:
            mismatches.append(seed)
    judged = sum(matrix.values())
    report["evaluation"] = {"evaluated_seeds": len(rows), "seed_set_complete": set(map(int, rows)) == set(range(1, 51)),
                            "statuses": dict(Counter(r["status"] for r in rows.values())),
                            "evaluation_set": "seen_regression_set",
                            "same_selected_bundle": (evaluation.get("identity", {}).get("bundle_sha256") == selected.get("bundle_sha256")) if rows else None,
                            "self_eval": None if case["c_arm"] == "no_self_eval" else {
                                "verdict_counts": dict(verdicts), "confusion": dict(matrix),
                                "coverage": judged / len(rows) if rows else None,
                                "accuracy_when_judged": (matrix["true_positive"] + matrix["true_negative"]) / judged if judged else None},
                            "ablation_violations": {"wrong_manifest_arm": mismatches,
                                "unexpected_predicate_calls_in_disabled_arm": sum(v for k, v in verdicts.items() if k != "missing") if case["c_arm"] == "no_self_eval" else 0}}
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("/mnt/home/gewang/experiments/code-world-c-opt-ablation-20260920-r2"))
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    receipt = read(a.root / "prepare-receipt.json")
    if receipt is None:
        raise ValueError("no prepared study at this path")
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "root": str(a.root),
              "cells": {cell: summarize_cell(a.root / cell) for cell in receipt["cells"]}}
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({name: {"state": r["campaign_status"], "dev_charged": r["development"]["charged"],
                             "eval": r["evaluation"]["statuses"], "blocker": r["blocker"]}
                      for name, r in result["cells"].items()}, indent=2))


if __name__ == "__main__":
    main()
