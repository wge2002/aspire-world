"""Supplementary held-out: finish a preserved, interrupted sweep on its frozen bundle.

p4 CC_TASK_5 (2026-10-19). A held-out sweep that stopped early (its wall-clock
watchdog, or an infrastructure row ending the evaluator through
`heldout_stop_on_infra`) leaves `state_preserved: true`: the immutable manifest,
the frozen bundle under `heldout/frozen/`, and one ledger row per seed that ran.
The plain evaluator `--resume` cannot finish it: it skips every seed that has a
row, so the interrupted seed's `infrastructure_error` row would stay unusable,
and it refuses a seed directory that has no row.

This module is the bookkeeping half of the supported path; the driver
(`run_two_task_cell.py --heldout-supplement`) runs the evaluator in between:

1. `plan` (read-only) - kept rows (success/failure/crash) stay as they are;
   seeds whose row is `infrastructure_error` and seeds with no row are re-run.
2. `prepare` - before anything changes, copies the ledger, report and campaign
   state into `heldout/supplement/<n>/`, MOVES the re-run seeds' directories
   to `heldout/supplement/<n>/superseded/` and removes only their
   `infrastructure_error` rows from the ledger. Nothing is deleted.
3. the frozen evaluator runs with `--resume` from the cell's own runtime, so
   identity (bundle digests, config, max_steps, trial_timeout) is re-verified
   against the ledger and only the missing seeds run.
4. `annotate` - marks the final `heldout_result.json` with which seeds were
   supplementary, by which attempt, and what each one superseded.

`stage_entry` writes the queue entry a stager needs to run this inside a unit
job; the supervisor recognises it by `MARKER` and passes `--heldout-supplement`.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

HELDOUT_SEEDS = tuple(range(1, 51))
#: Statuses of a graded seed: its row is the result and is never re-run.
KEPT_STATUSES = ("success", "failure", "crash")
RERUN_STATUS = "infrastructure_error"
#: Driver status while a supplement runs; a supplement killed with its unit may
#: be supplemented again once the supervisor has verified its tree is gone.
RUNNING = "heldout_supplement_running"
#: `blocked.phase` values a held-out stop records (the driver's own `Blocked`
#: and its generic exception handler, which records the status it was in).
HELDOUT_PHASES = ("heldout", "heldout_running", RUNNING)
#: Queue entry beside a supplement cell's case.json in a job root.
MARKER = "heldout-supplement.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(temporary, path)


def eligibility(state: dict | None) -> list[str]:
    """Why this campaign state may NOT be supplemented (empty: it may)."""
    if not isinstance(state, dict):
        return ["no readable campaign_state.json"]
    status, blocked = state.get("status"), state.get("blocked") or {}
    if status == RUNNING:
        return []
    if status != "blocked":
        return [f"campaign status is {status!r}; only a blocked held-out (or an interrupted "
                f"supplement) is supplemented"]
    problems = []
    if blocked.get("phase") not in HELDOUT_PHASES:
        problems.append(f"blocked in phase {blocked.get('phase')!r}, not the held-out sweep")
    if blocked.get("state_preserved") is not True:
        problems.append("the block does not record state_preserved: true")
    if not (state.get("heldout_manifest") or {}).get("sha256"):
        problems.append("no frozen held-out manifest is recorded")
    return problems


def read_ledger(evaluation: Path) -> dict | None:
    path = Path(evaluation) / "heldout_state.json"
    return json.loads(path.read_text()) if path.is_file() else None


def seed_dir(evaluation: Path, seed: int) -> Path:
    return Path(evaluation) / f"seed_{seed:02d}"


def plan(evaluation: Path, seeds=HELDOUT_SEEDS) -> dict:
    """Read-only: which seeds keep their row and which run again."""
    evaluation = Path(evaluation)
    ledger = read_ledger(evaluation)
    rows = (ledger or {}).get("seeds") or {}
    unknown = sorted(int(k) for k in rows if int(k) not in seeds)
    kept, rerun, missing, invalid = [], [], [], {}
    for seed in seeds:
        row = rows.get(str(seed))
        if row is None:
            missing.append(seed)
        elif row.get("status") in KEPT_STATUSES:
            kept.append(seed)
        elif row.get("status") == RERUN_STATUS:
            rerun.append(seed)
        else:
            invalid[seed] = row.get("status")
    problems = []
    if ledger is not None and not ledger.get("identity"):
        problems.append("the held-out ledger has no identity")
    if unknown:
        problems.append(f"the ledger holds seeds outside the sweep: {unknown}")
    if invalid:
        problems.append(f"rows with statuses the evaluator never writes: {invalid}")
    return {"ledger_present": ledger is not None, "kept": kept,
            "rerun_infrastructure_error": rerun, "missing": missing,
            "orphan_dirs": [s for s in missing if seed_dir(evaluation, s).exists()],
            "seeds_to_run": sorted(rerun + missing),
            "kept_counts": {status: sum(rows[str(s)]["status"] == status for s in kept)
                            for status in KEPT_STATUSES},
            "problems": problems}


def new_attempt(evaluation: Path) -> Path:
    root = Path(evaluation) / "supplement"
    root.mkdir(parents=True, exist_ok=True)
    index = 1 + sum(1 for p in root.iterdir() if p.is_dir() and p.name.isdigit())
    while True:
        try:
            (root / str(index)).mkdir()
            return root / str(index)
        except FileExistsError:
            index += 1


def prepare(evaluation: Path, planned: dict, attempt: Path, campaign_state: Path) -> dict:
    """Snapshot, then supersede the re-run seeds. Copies and moves only.

    Order makes an interruption harmless: the record and snapshots exist before
    a directory moves, and the ledger is rewritten last, so a re-plan after a
    crash finds the same seeds (a moved directory is simply no longer there).
    """
    evaluation, attempt = Path(evaluation), Path(attempt)
    if planned["problems"]:
        raise ValueError(f"held-out ledger cannot be supplemented: {planned['problems']}")
    if not planned["seeds_to_run"]:
        raise ValueError("every held-out seed already has a graded row; nothing to supplement")
    snapshots = {}
    for name, source in (("heldout_state.json", evaluation / "heldout_state.json"),
                         ("heldout_result.json", evaluation / "heldout_result.json"),
                         ("campaign_state.json", Path(campaign_state))):
        if source.is_file():
            target = attempt / name.replace(".json", ".before.json")
            target.write_bytes(source.read_bytes())
            snapshots[name] = {"path": str(target), "sha256": digest(target)}
    ledger = read_ledger(evaluation)
    superseded = {str(s): ledger["seeds"][str(s)] for s in planned["rerun_infrastructure_error"]}
    moves = [s for s in planned["seeds_to_run"] if seed_dir(evaluation, s).exists()]
    record = {"attempt": attempt.name, "created_at": now(), "status": "prepared",
              "seeds_to_run": planned["seeds_to_run"],
              "rerun_infrastructure_error": planned["rerun_infrastructure_error"],
              "missing": planned["missing"], "kept": planned["kept"],
              "kept_counts": planned["kept_counts"], "superseded_rows": superseded,
              "moved_dirs": {str(s): str(attempt / "superseded" / seed_dir(evaluation, s).name)
                             for s in moves},
              "snapshots": snapshots}
    atomic_json(attempt / "supplement.json", record)
    if moves:
        (attempt / "superseded").mkdir()
    for seed in moves:
        seed_dir(evaluation, seed).rename(record["moved_dirs"][str(seed)])
    if superseded:
        for key in superseded:
            del ledger["seeds"][key]
        atomic_json(evaluation / "heldout_state.json", ledger)
    return record


def finish(attempt: Path, **fields) -> dict:
    record = json.loads((Path(attempt) / "supplement.json").read_text())
    record.update(fields, finished_at=now())
    atomic_json(Path(attempt) / "supplement.json", record)
    return record


def annotate(evaluation: Path, attempts: list[dict]) -> dict:
    """Mark the final report: which seeds ran in which supplement, what they replaced.

    Every seed an attempt was planned to run had no graded original row, so it
    is supplementary whichever attempt finally graded it; a later attempt that
    re-ran a seed (an earlier attempt stopped before or at it) owns it. The
    evaluator's own fields are left exactly as it wrote them.
    """
    path = Path(evaluation) / "heldout_result.json"
    report = json.loads(path.read_text())
    owner = {}
    for entry in attempts:
        for seed in entry["seeds_to_run"]:
            owner[seed] = entry["attempt"]
    report["supplementary_seeds"] = sorted(owner)
    report["original_seeds"] = [s for s in HELDOUT_SEEDS if s not in owner and
                                str(s) in (report.get("per_seed") or {})]
    report["supplement"] = {
        "attempt_of_seed": {str(s): owner[s] for s in sorted(owner)},
        "attempts": [{k: entry.get(k) for k in ("attempt", "record", "seeds_to_run",
                                                "rerun_infrastructure_error", "missing", "status",
                                                "deadline_seconds", "elapsed_seconds")}
                     for entry in attempts],
        "note": ("seeds listed in supplementary_seeds were evaluated by a supplementary run of "
                 "the same frozen bundle after the original sweep stopped; every other row is "
                 "the original sweep's, unchanged")}
    atomic_json(path, report)
    return report


def stage_entry(source_control: Path, cell_dir: Path) -> dict:
    """Write a job-queue entry that supplements an existing cell in place.

    The entry holds a byte copy of the source cell's staged `case.json` (its
    `control` still names the source cell, so the evaluator writes there) and
    `MARKER`. A stager calls this, then pins the case and runtime digests in
    its receipt like any other cell.
    """
    source_control, cell_dir = Path(source_control), Path(cell_dir)
    case_bytes = (source_control / "case.json").read_bytes()
    case = json.loads(case_bytes)
    if Path(case["control"]).resolve() != source_control.resolve():
        raise ValueError(f"{source_control}/case.json names control {case['control']}")
    state = json.loads((source_control / "campaign_state.json").read_text())
    problems = eligibility(state) + plan(source_control / "heldout")["problems"]
    if problems:
        raise ValueError(f"{case['id']} cannot be supplemented: {problems}")
    planned = plan(source_control / "heldout")
    cell_dir.mkdir(parents=True, exist_ok=False)
    (cell_dir / "case.json").write_bytes(case_bytes)
    marker = {"cell": case["id"], "source_control": str(source_control),
              "source_case_sha256": hashlib.sha256(case_bytes).hexdigest(),
              "source_campaign_state_sha256": digest(source_control / "campaign_state.json"),
              "heldout_manifest_sha256": state["heldout_manifest"]["sha256"],
              "seeds_to_run_at_staging": planned["seeds_to_run"], "staged_at": now()}
    atomic_json(cell_dir / MARKER, marker)
    return marker


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--control", type=Path, required=True, help="the cell's control directory")
    args = parser.parse_args(argv)
    state_path = args.control / "campaign_state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else None
    planned = plan(args.control / "heldout")
    planned["eligibility_problems"] = eligibility(state)
    planned["eligible"] = not (planned["eligibility_problems"] or planned["problems"]
                               or not planned["seeds_to_run"])
    print(json.dumps(planned, indent=2))
    return 0 if planned["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
