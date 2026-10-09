#!/usr/bin/env python3
"""Outer analysis of the prediction-contract base study: arm off vs arm p1.

Reads what each finished cell left behind and never changes it:

- the public development ledger (`development_state.json`) for every graded
  development trial and its oracle outcome (the study's gate is `oracle`);
- each such trial's `judgment_world/manifest.json` (`prediction_checks` counts),
  `judgment_world/events.jsonl` (`prediction_check` rows, for residuals) and
  `world_use_audit.json` (`prediction_use`);
- `campaign_state.json` for retries and development wall-clock;
- `heldout/heldout_result.json` plus each held-out seed's world manifest.

Per cell it reports prediction counts, the mismatch rate among resolved checks,
the rate of development trials whose audit found a corroborated consumer of a
reserved prediction query, a mismatch x outcome 2x2 and held-out success. Then it
aggregates by arm and by task x arm. Development seeds and held-out seeds are
never pooled. An `off` cell has no prediction data; its columns stay empty.
Nothing here grades a prediction: the study is judged by held-out success.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gate_study_analysis import (HELDOUT_SEEDS, TRIAL_PHASES, cell_state, discover, fmt,
                                 is_complete, mean, read_json, read_json_lines, task_dir)

COUNT_KEYS = ("committed", "supported", "unsupported", "malformed",
              "match", "mismatch", "unknown", "unresolved")
SUPPORTED_USE = "supported_use_candidate"


def arm_of(case):
    return "p1" if case.get("prediction_contract") == "p1" else "off"


def trial_directory(case, row):
    """A ledger row's trial directory: absolute, or relative to the task directory."""
    directory = row.get("directory")
    if not directory:
        return None
    path = Path(directory)
    return path if path.is_absolute() else task_dir(case) / path


def prediction_record(directory):
    """Prediction evidence of one recorded trial, or None when it carries none."""
    if directory is None:
        return None
    manifest = read_json(directory / "judgment_world/manifest.json") or {}
    checks = [row for row in read_json_lines(directory / "judgment_world/events.jsonl")
              if isinstance(row, dict) and row.get("event") == "prediction_check"]
    block = manifest.get("prediction_checks")
    if not isinstance(block, dict) and not checks:
        return None
    if isinstance(block, dict):
        counts = {key: int((block.get("counts") or {}).get(key) or 0) for key in COUNT_KEYS}
        policy_queries = block.get("policy_queries")
    else:
        # Manifest missing (e.g. an interrupted trial): rebuild what the rows say.
        counts = {key: sum(1 for row in checks if row.get("status") == key) for key in COUNT_KEYS}
        counts["supported"] = counts["committed"] = None
        policy_queries = None
    audit = read_json(directory / "world_use_audit.json") or {}
    use = (audit.get("prediction_use") or {}).get("status")
    return {"counts": counts, "policy_queries": policy_queries, "prediction_use": use,
            "residuals": [(row.get("fact"), row.get("residual")) for row in checks
                          if row.get("status") in ("match", "mismatch")
                          and isinstance(row.get("residual"), (int, float))]}


def add_counts(total, counts):
    for key in COUNT_KEYS:
        if counts.get(key) is not None:
            total[key] = (total.get(key) or 0) + counts[key]


def mismatch_rate(counts):
    resolved = (counts.get("match") or 0) + (counts.get("mismatch") or 0)
    return (counts.get("mismatch") or 0) / resolved if resolved else None


def mismatch_outcome(pairs):
    """(any mismatch, success) pairs -> the 2x2 plus the success rate in each row."""
    table = {"mismatch_success": 0, "mismatch_failure": 0,
             "no_mismatch_success": 0, "no_mismatch_failure": 0}
    for mismatch, success in pairs:
        key = ("mismatch" if mismatch else "no_mismatch") + ("_success" if success else "_failure")
        table[key] += 1
    with_m = table["mismatch_success"] + table["mismatch_failure"]
    without = table["no_mismatch_success"] + table["no_mismatch_failure"]
    table["n"] = with_m + without
    table["success_rate_with_mismatch"] = table["mismatch_success"] / with_m if with_m else None
    table["success_rate_without_mismatch"] = table["no_mismatch_success"] / without if without else None
    return table


def residual_summary(residuals):
    by_fact = defaultdict(list)
    for fact, value in residuals:
        by_fact[fact].append(float(value))
    return {fact: {"n": len(values), "min": min(values), "median": statistics.median(values),
                   "max": max(values)} for fact, values in sorted(by_fact.items())}


def summarize_records(records):
    """records: (prediction_record or None, success). One development or held-out set."""
    counts, residuals, pairs, uses = {}, [], [], []
    with_data = 0
    for record, success in records:
        if record is None:
            continue
        with_data += 1
        add_counts(counts, record["counts"])
        residuals += record["residuals"]
        pairs.append((bool(record["counts"].get("mismatch")), success))
        if record["prediction_use"] is not None:
            uses.append(record["prediction_use"] == SUPPORTED_USE)
    return {"trials": len(records), "trials_with_predictions": with_data,
            "counts": counts or None, "mismatch_rate": mismatch_rate(counts) if counts else None,
            "trials_with_prediction_audit": len(uses),
            "prediction_use_rate": sum(uses) / len(uses) if uses else None,
            "mismatch_x_outcome": mismatch_outcome(pairs) if pairs else None,
            "residuals_by_fact": residual_summary(residuals) if residuals else None}


def development(case, control):
    ledger = read_json(task_dir(case) / "development_state.json") or {}
    campaign = read_json(control / "campaign_state.json") or {}
    graded = [r for r in ledger.get("trials", [])
              if r.get("phase") in TRIAL_PHASES and r.get("status") == "complete"]
    records = [(prediction_record(trial_directory(case, r)), bool(r.get("task_completed")))
               for r in graded]
    return {"status": campaign.get("status"),
            "development_seconds": campaign.get("development_seconds"),
            "retries_spent": sum(1 for r in ledger.get("trials", []) if r.get("spends_retry")),
            "graded_trials": len(graded),
            "oracle_success_rate_dev": (sum(s for _, s in records) / len(records)) if records else None,
            **summarize_records(records)}


def heldout(control):
    evaluation = control / "heldout"
    report = read_json(evaluation / "heldout_result.json")
    if not report:
        return {"available": False}
    per_seed = report.get("per_seed") or {}
    records = [(prediction_record(evaluation / f"seed_{seed:02d}"), per_seed.get(str(seed)) == "success")
               for seed in HELDOUT_SEEDS if per_seed.get(str(seed)) in ("success", "failure")]
    return {"available": True, "counts": report.get("counts"),
            "success_rate": report.get("success_rate"),
            "all_seeds_accounted": report.get("all_seeds_accounted"),
            "predictions": summarize_records(records)}


def analyze_cell(case_path):
    case = json.loads(Path(case_path).read_text())
    control = Path(case["control"])
    return {"cell": case["id"], "task": case["task"], "arm": arm_of(case),
            "repeat": case.get("repeat"), "state": cell_state(control),
            "development": development(case, control), "heldout": heldout(control),
            "control": str(control)}


def aggregate(cells):
    """Aggregates over complete cells only; running or failed cells are listed, not averaged."""
    by_arm, by_task_arm = defaultdict(list), defaultdict(list)
    for cell in cells:
        if is_complete(cell):
            by_arm[cell["arm"]].append(cell)
            by_task_arm[(cell["task"], cell["arm"])].append(cell)

    def summarize(group):
        rates = [c["heldout"].get("success_rate") for c in group]
        counts = {}
        pairs = defaultdict(int)
        for c in group:
            add_counts(counts, c["development"].get("counts") or {})
            for key, value in (c["development"].get("mismatch_x_outcome") or {}).items():
                if key.startswith(("mismatch_", "no_mismatch_")) and "rate" not in key:
                    pairs[key] += value
        table = None
        if pairs:
            table = mismatch_outcome(
                [(True, True)] * pairs["mismatch_success"] + [(True, False)] * pairs["mismatch_failure"]
                + [(False, True)] * pairs["no_mismatch_success"]
                + [(False, False)] * pairs["no_mismatch_failure"])
        return {"cells": [c["cell"] for c in group], "n": len(group),
                "heldout_success_mean": mean(rates), "heldout_success_per_cell": rates,
                "retries_spent_mean": mean(c["development"]["retries_spent"] for c in group),
                "development_seconds_mean": mean(c["development"]["development_seconds"] for c in group),
                "dev_counts": counts or None,
                "dev_mismatch_rate": mismatch_rate(counts) if counts else None,
                "dev_prediction_use_rate_mean": mean(c["development"]["prediction_use_rate"] for c in group),
                "dev_mismatch_x_outcome": table}
    incomplete = [c for c in cells if not is_complete(c)]
    return {"by_arm": {a: summarize(v) for a, v in sorted(by_arm.items())},
            "by_task_arm": {f"{t}|{a}": summarize(v) for (t, a), v in sorted(by_task_arm.items())},
            "complete_cells": sum(len(v) for v in by_arm.values()),
            "pending_cells": [c["cell"] for c in incomplete],
            "failed_cells": [c["cell"] for c in incomplete if c.get("state") in ("failed", "blocked")],
            "running_cells": [c["cell"] for c in incomplete if c.get("state") not in ("failed", "blocked")]}


def table2x2(t):
    if not t:
        return "–"
    return (f"M+S{t['mismatch_success']} M+F{t['mismatch_failure']} "
            f"noM+S{t['no_mismatch_success']} noM+F{t['no_mismatch_failure']}")


def counts_text(c):
    if not c:
        return "–"
    return "/".join(fmt(c.get(k)) for k in ("committed", "match", "mismatch", "unknown",
                                           "unresolved", "unsupported", "malformed"))


def markdown(cells, agg):
    lines = ["# Prediction-contract base study: analysis", "",
             "Statistical unit is a cell (one independent development run). Development seeds "
             "(51–65) and held-out seeds (1–50) are reported separately and never pooled. "
             "Prediction columns exist only for p1 cells.", "",
             f"Complete cells in the aggregates: {agg['complete_cells']} of {len(cells)}"
             + (f"; failed or blocked, no held-out: {', '.join(agg['failed_cells'])}"
                if agg["failed_cells"] else "")
             + (f"; still running: {', '.join(agg['running_cells'])}"
                if agg["running_cells"] else "") + ".", "",
             "## By arm", "",
             "| arm | cells | held-out success (mean) | per cell | retries | dev hours | dev mismatch rate | dev prediction_use rate | dev mismatch x outcome |",
             "|---|---|---|---|---|---|---|---|---|"]
    for arm, s in agg["by_arm"].items():
        hours = None if s["development_seconds_mean"] is None else s["development_seconds_mean"] / 3600
        lines.append(f"| {arm} | {s['n']} | {fmt(s['heldout_success_mean'])} | "
                     f"{', '.join(fmt(v) for v in s['heldout_success_per_cell'])} | "
                     f"{fmt(s['retries_spent_mean'], 1)} | {fmt(hours, 1)} | {fmt(s['dev_mismatch_rate'])} | "
                     f"{fmt(s['dev_prediction_use_rate_mean'])} | {table2x2(s['dev_mismatch_x_outcome'])} |")
    lines += ["", "## By task and arm", "",
              "| task | arm | cells | held-out success per cell | dev mismatch rate | dev prediction_use rate |",
              "|---|---|---|---|---|---|"]
    for key, s in agg["by_task_arm"].items():
        task, arm = key.split("|")
        lines.append(f"| {task} | {arm} | {s['n']} | {', '.join(fmt(v) for v in s['heldout_success_per_cell'])} | "
                     f"{fmt(s['dev_mismatch_rate'])} | {fmt(s['dev_prediction_use_rate_mean'])} |")
    lines += ["", "## Per cell", "",
              "| cell | arm | status | graded dev trials | dev oracle success | dev counts c/m/mm/unk/unr/uns/mal | dev mismatch rate | dev prediction_use rate | dev mismatch x outcome | held-out | held-out mismatch rate | held-out mismatch x outcome |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        d, h = c["development"], c["heldout"]
        hp = h.get("predictions") or {}
        lines.append(f"| {c['cell']} | {c['arm']} | {c.get('state') or d['status']} | {d['graded_trials']} | "
                     f"{fmt(d['oracle_success_rate_dev'])} | {counts_text(d['counts'])} | "
                     f"{fmt(d['mismatch_rate'])} | {fmt(d['prediction_use_rate'])} | "
                     f"{table2x2(d['mismatch_x_outcome'])} | {fmt(h.get('success_rate'))} | "
                     f"{fmt(hp.get('mismatch_rate'))} | {table2x2(hp.get('mismatch_x_outcome'))} |")
    lines += ["", "Reading guide: mismatch rate is mismatch / (match + mismatch) over resolved "
              "checks; unknown, unresolved, unsupported and malformed are counted separately and "
              "never enter it. prediction_use rate is the share of audited development trials whose "
              "world_use_audit found a corroborated consumer (motion argument or acting branch) of a "
              "reserved prediction query; it is advisory static evidence, not proof that the branch "
              "ran. The 2x2 splits trials by whether any mismatch occurred (M / noM) and by the "
              "oracle outcome (S / F). It is descriptive: a mismatch is information, not a grade."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=Path, action="append", default=[], help="a cell's case.json (repeatable)")
    parser.add_argument("--parent", type=Path, help="experiment parent directory; every */case.json is a cell")
    parser.add_argument("--out", type=Path, help="directory for summary.json and summary.md")
    args = parser.parse_args(argv)
    cases = list(args.case) + (discover(args.parent) if args.parent else [])
    if not cases:
        parser.error("name at least one --case or a --parent")
    cells = [analyze_cell(path) for path in cases]
    agg = aggregate(cells)
    text = markdown(cells, agg)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(json.dumps({"cells": cells, "aggregate": agg}, indent=2) + "\n")
        (args.out / "summary.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
