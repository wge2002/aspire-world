"""Original frozen evaluator, with persist-before-stop infrastructure handling.

Reference mode runs a supplied immutable historical bundle without claiming a
new development lineage. Every ordinary cell uses the original frozen_bundle.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from infra_guard import bind_child_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    repo = Path(case["sim"])
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_heldout as native
    original_env = native.runtime_env
    native.runtime_env = lambda c, r: bind_child_env(original_env(c, r), c)
    native.verify_runtime(case, repo)
    evaluation = Path(case["control"]) / "heldout"
    evaluation.mkdir(exist_ok=True)
    if args.reference:
        reference = json.loads(args.reference.read_text())
        bundle = reference["bundle"]
        kept = {role: Path(path) for role, path in reference["kept"].items()}
        native.assert_frozen(bundle, kept, 0)
        mode = "historical_frozen_bundle_reproduction"
    else:
        task = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
        bundle, kept = native.frozen_bundle(case, task, evaluation)
        mode = "fresh_native_fixloop"
    identity = {"cell": case["id"], "condition": case["condition"],
                "suite": case["suite"], "task": case["task"], "mode": mode,
                "seeds": list(range(1, 51)), "bundle": bundle,
                "bundle_sha256": native.bundle_identity(bundle),
                "case_sha256": hashlib.sha256(args.case.read_bytes()).hexdigest(),
                "evaluation_set": "seen_regression_set"}
    ledger = evaluation / "heldout_state.json"
    if ledger.exists():
        data = json.loads(ledger.read_text())
        if not args.resume or data["identity"] != identity:
            raise ValueError("Existing evaluation needs explicit resume and identical inputs")
    else:
        data = {"identity": identity, "seeds": {}}
    for seed in range(1, 51):
        if str(seed) in data["seeds"]:
            continue
        if (evaluation / f"seed_{seed:02d}").exists():
            raise ValueError(f"Seed {seed} has unaccounted artifacts; preserve and inspect")
        result = native.evaluate_seed(case, repo, evaluation, bundle, kept, seed)
        data["seeds"][str(seed)] = result
        native.atomic_json(ledger, data)
        print(json.dumps({"seed": seed, "status": result["status"]}), flush=True)
        if result["status"] == "infrastructure_error":
            raise RuntimeError(f"Infrastructure failed at seed {seed}; result preserved, sweep stopped")
    rows = list(data["seeds"].values())
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("success", "failure", "crash", "infrastructure_error")}
    report = {"identity": identity, "seeds_evaluated": len(rows),
              "all_seeds_accounted": len(rows) == 50, "counts": counts,
              "success_rate": counts["success"] / 50,
              "per_seed": {str(row["seed"]): row["status"] for row in rows},
              "unusable_seeds": [row["seed"] for row in rows if row["status"] == "infrastructure_error"]}
    native.atomic_json(evaluation / "heldout_result.json", report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
