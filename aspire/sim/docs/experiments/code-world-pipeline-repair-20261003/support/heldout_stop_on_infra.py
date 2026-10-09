"""Run the frozen held-out sweep, but stop after the first persisted infra row.

`native_world_heldout.main` writes an `infrastructure_error` row and continues to
the next seed. `run_replay` starts each replay in its OWN session, so that next
child is unreachable from the driver's process-group stop: stopping the sweep
from outside could orphan it. This entry therefore lets the evaluator stop
itself, one atomic ledger write later.

Nothing about the evaluation is reimplemented: the frozen pair, the identity and
resume rules, per-seed evaluation and the report shape all come from the module.
Only the loop ends early, and only after the row is on disk.
"""
import json
from pathlib import Path
import sys

STATUSES = ("success", "failure", "crash", "infrastructure_error")


class SweepStopped(Exception):
    """Raised after the ledger write that recorded an infrastructure error."""


def guard(heldout, ledger):
    """Wrap the module's own crash-safe ledger write; persist first, then stop."""
    write = heldout.atomic_json

    def guarded(path, data):
        write(path, data)
        if Path(path) == ledger:
            hit = [row for row in data["seeds"].values()
                   if row.get("status") == "infrastructure_error"]
            if hit:
                raise SweepStopped(json.dumps(hit[-1], default=str))
    heldout.atomic_json = guarded


def partial_report(heldout, case, ledger):
    """The module's report shape over the seeds that were actually recorded."""
    data = json.loads(ledger.read_text())
    records = [data["seeds"][key] for key in sorted(data["seeds"], key=int)]
    counts = {status: sum(row["status"] == status for row in records) for status in STATUSES}
    return {"schema_version": 1, "cell": case["id"], "condition": case["condition"],
            "identity": data["identity"], "seeds_evaluated": len(records),
            "all_seeds_accounted": len(records) == len(heldout.HELDOUT_SEEDS),
            "counts": counts, "success_rate": counts["success"] / len(heldout.HELDOUT_SEEDS),
            "per_seed": {str(row["seed"]): row["status"] for row in records},
            "unusable_seeds": [row["seed"] for row in records
                               if row["status"] == "infrastructure_error"],
            "stopped_after_infrastructure_error": True}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    case_path = Path(argv[argv.index("--case") + 1])
    case = json.loads(case_path.read_text())
    sys.path.insert(0, str(Path(case["sim"]) / "scripts/libero"))
    import native_world_heldout as heldout
    evaluation = Path(case["control"]) / "heldout"
    ledger = evaluation / "heldout_state.json"
    guard(heldout, ledger)
    sys.argv = ["native_world_heldout.py", *argv]
    try:
        return heldout.main()
    except SweepStopped as stop:
        report = partial_report(heldout, case, ledger)
        report["stopped_on"] = json.loads(str(stop))
        (evaluation / "heldout_result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
