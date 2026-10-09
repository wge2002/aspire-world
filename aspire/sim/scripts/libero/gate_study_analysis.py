#!/usr/bin/env python3
"""Outer analysis of a development-gate study: oracle vs self_eval vs vlm_judge.

Reads what each finished cell left behind and never changes it:

- the public development ledger (`development_state.json`) of an oracle cell,
  or the sealed ledger (`control/sealed/development_outcomes.jsonl`) of a sealed
  cell, for per-trial oracle / gate / self-evaluation agreement;
- `campaign_state.json` for development wall-clock and the frozen selection;
- `heldout/heldout_result.json` plus each held-out seed's world manifest for the
  environment outcome and the shadow self-evaluation on seeds 1-50;
- optional post-hoc judge verdicts under `heldout/judge/seed_NN.json`, which
  `--judge-heldout` produces by running the same VLM judge over every held-out
  trial's keyframes (this needs the served model and is the only step that
  contacts anything).

Per cell it reports held-out success, development retries and time, the gate's
false-accept and false-reject rates against the sealed oracle, and the 2x2
agreement of the self-evaluation (and judge, when present) with the environment.
Then it aggregates by gate and by task x gate. Development seeds and held-out
seeds are never pooled.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

TRIAL_PHASES = ("smoke", "initial", "repair")
HELDOUT_SEEDS = tuple(range(1, 51))


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def task_dir(case):
    return Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]


def final_verdict(trial_directory):
    from aspire.sim.cap.world_model.development_gate import final_self_evaluation
    final = final_self_evaluation(Path(trial_directory))
    return final.get("verdict") if final else None


def two_by_two(pairs):
    """(verdict, truth) pairs -> counts; verdict in true/false/success/failure/other."""
    counts = Counter()
    for verdict, truth in pairs:
        if truth is None:
            continue
        positive = verdict in ("true", "success")
        negative = verdict in ("false", "failure")
        if positive:
            counts["tp" if truth else "fp"] += 1
        elif negative:
            counts["fn" if truth else "tn"] += 1
        else:
            counts["undecided_on_success" if truth else "undecided_on_failure"] += 1
    decided = counts["tp"] + counts["fp"] + counts["fn"] + counts["tn"]
    total = decided + counts["undecided_on_success"] + counts["undecided_on_failure"]
    summary = dict(counts)
    summary["n"] = total
    summary["decided"] = decided
    summary["accuracy_on_decided"] = (counts["tp"] + counts["tn"]) / decided if decided else None
    positives = counts["tp"] + counts["fn"]
    negatives = counts["fp"] + counts["tn"]
    recall = counts["tp"] / positives if positives else None
    specificity = counts["tn"] / negatives if negatives else None
    summary["balanced_accuracy"] = ((recall + specificity) / 2
                                    if recall is not None and specificity is not None else None)
    summary["undecided_rate"] = (total - decided) / total if total else None
    return summary


# --- development -----------------------------------------------------------------


def development_rows(case, control):
    """Per graded development trial: oracle, gate, self-evaluation. Common shape."""
    gate = case.get("development_gate") or "oracle"
    rows = []
    if gate == "oracle":
        ledger = read_json(task_dir(case) / "development_state.json") or {}
        for r in ledger.get("trials", []):
            if r.get("phase") not in TRIAL_PHASES or r.get("status") != "complete":
                continue
            calibration = r.get("foundation_calibration") or {}
            final = calibration.get("final_self_evaluation") or {}
            oracle = bool(r.get("task_completed"))
            rows.append({"phase": r["phase"], "seed": r["seed"], "attempt": r.get("attempt"),
                         "sandbox_rc": r.get("sandbox_rc"), "oracle_success": oracle,
                         "gate_passed": oracle, "gate_verdict": "oracle",
                         "self_eval_verdict": final.get("verdict"), "bundle_sha256": r.get("bundle_sha256")})
    else:
        for r in (read_json_lines(control / "sealed/development_outcomes.jsonl")):
            if r.get("phase") not in TRIAL_PHASES or r.get("status") != "complete" or not r.get("oracle"):
                continue
            report = r.get("gate") or {}
            # For the vlm_judge gate the gate report *is* the judge's verdict; the
            # ledger's dedicated judge_verdict column was left null by the 2026-10-05
            # protocol, so derive it from the report rather than lose the column.
            judge_verdict = r.get("judge_verdict")
            if judge_verdict is None and report.get("source") == "vlm_judge":
                judge_verdict = report.get("verdict")
            rows.append({"phase": r["phase"], "seed": r["seed"], "attempt": r.get("attempt"),
                         "sandbox_rc": r["oracle"].get("sandbox_rc"),
                         "oracle_success": bool(r["oracle"].get("task_completed")),
                         "gate_passed": bool(report.get("passed")),
                         "gate_verdict": report.get("verdict"),
                         "self_eval_verdict": r.get("self_eval_verdict"),
                         "judge_verdict": judge_verdict, "bundle_sha256": r.get("bundle_sha256")})
    return rows


def read_json_lines(path):
    rows = []
    try:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return rows


def development_summary(case, control, rows):
    ledger = read_json(task_dir(case) / "development_state.json") or {}
    campaign = read_json(control / "campaign_state.json") or {}
    graded = [r for r in rows]
    accepted = [r for r in graded if r["gate_passed"]]
    rejected = [r for r in graded if not r["gate_passed"]]
    selected = (ledger.get("selected") or {}).get("bundle_sha256")
    selected_rows = [r for r in graded if r["bundle_sha256"] == selected]
    return {
        "graded_trials": len(graded),
        "retries_spent": sum(1 for r in ledger.get("trials", []) if r.get("spends_retry")),
        "development_seconds": campaign.get("development_seconds"),
        "status": campaign.get("status"),
        "oracle_success_rate_dev": (sum(r["oracle_success"] for r in graded) / len(graded)) if graded else None,
        "gate_pass_rate_dev": (len(accepted) / len(graded)) if graded else None,
        # Of the trials the gate accepted, how many the environment rejected.
        "gate_false_accept": sum(1 for r in accepted if not r["oracle_success"]),
        "gate_false_accept_rate": (sum(1 for r in accepted if not r["oracle_success"]) / len(accepted)) if accepted else None,
        # Of the trials the gate did not accept, how many the environment accepted.
        "gate_false_reject": sum(1 for r in rejected if r["oracle_success"]),
        "gate_false_reject_rate": (sum(1 for r in rejected if r["oracle_success"]) / len(rejected)) if rejected else None,
        "self_eval_vs_oracle_dev": two_by_two((r["self_eval_verdict"], r["oracle_success"]) for r in graded),
        # Only cells whose development trials were judged carry this (the vlm_judge gate).
        "judge_vs_oracle_dev": (two_by_two((r.get("judge_verdict"), r["oracle_success"]) for r in graded
                                           if r.get("judge_verdict") is not None)
                                if any(r.get("judge_verdict") is not None for r in graded) else None),
        "selected_bundle_sha256": selected,
        "selected_bundle_dev_trials": len(selected_rows),
        "selected_bundle_dev_oracle_successes": sum(r["oracle_success"] for r in selected_rows),
        "selected_bundle_dev_gate_passes": sum(r["gate_passed"] for r in selected_rows),
    }


# --- held-out ----------------------------------------------------------------------


def trial_truncated(trial_dir):
    """True when the replay's summary.txt says the episode hit its step limit."""
    if not trial_dir:
        return False
    try:
        return "Truncated: True" in (Path(trial_dir) / "summary.txt").read_text()
    except OSError:
        return False


def heldout_crashes(evaluation):
    """Secondary view of the frozen held-out ledger (heldout_state.json).

    The frozen rule counts any non-zero sandbox exit as a crash, even when the
    environment had already marked the task completed (the program kept acting
    after termination). These counts keep that visible without changing the rule."""
    state = read_json(evaluation / "heldout_state.json") or {}
    records = state.get("seeds") or {}
    if not records:
        return {"task_completed_any_rc": None, "task_completed_rate_any_rc": None, "crash_breakdown": None}
    completed = completed_then_acted = step_limit = exception = 0
    for record in records.values():
        result = record.get("result") or {}
        if result.get("task_completed"):
            completed += 1
        if record.get("status") == "crash":
            if result.get("task_completed"):
                completed_then_acted += 1
            elif trial_truncated(result.get("trial_dir")):
                step_limit += 1
            else:
                exception += 1
    return {"task_completed_any_rc": completed,
            "task_completed_rate_any_rc": completed / len(HELDOUT_SEEDS),
            "crash_breakdown": {"completed_then_acted": completed_then_acted,
                                "step_limit_exhausted": step_limit,
                                "program_exception": exception}}


def heldout_summary(case, control):
    evaluation = control / "heldout"
    report = read_json(evaluation / "heldout_result.json")
    if not report:
        return {"available": False}
    per_seed = report.get("per_seed") or {}
    self_eval, judge = [], []
    for seed in HELDOUT_SEEDS:
        status = per_seed.get(str(seed))
        if status not in ("success", "failure"):
            continue
        truth = status == "success"
        self_eval.append((final_verdict(evaluation / f"seed_{seed:02d}"), truth))
        verdict = read_json(evaluation / "judge" / f"seed_{seed:02d}.json") or {}
        if verdict.get("status") == "complete":
            judge.append((verdict.get("verdict"), truth))
    return {"available": True, "counts": report.get("counts"),
            "success_rate": report.get("success_rate"),
            "all_seeds_accounted": report.get("all_seeds_accounted"),
            "self_eval_vs_oracle": two_by_two(self_eval),
            "judge_vs_oracle": two_by_two(judge) if judge else None,
            "judged_seeds": len(judge), **heldout_crashes(evaluation)}


# --- cells and aggregation ---------------------------------------------------------


def analyze_cell(case_path):
    case = json.loads(Path(case_path).read_text())
    control = Path(case["control"])
    rows = development_rows(case, control)
    return {"cell": case["id"], "task": case["task"], "gate": case.get("development_gate") or "oracle",
            "repeat": case.get("repeat"), "state": cell_state(control),
            "development": development_summary(case, control, rows),
            "heldout": heldout_summary(case, control), "control": str(control)}


def mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return statistics.fmean(values) if values else None


def cell_state(control):
    """How the cell's queue run ended: full_complete, failed, blocked, or still running.

    The queue writes `queue-result.json` when it closes a cell; a cell that is still
    running has only `campaign_state.json`. A timed-out cell is `failed` here even
    though its development ledger is intact and its state was preserved."""
    result = read_json(Path(control) / "queue-result.json") or {}
    if result.get("state"):
        return result["state"]
    campaign = read_json(Path(control) / "campaign_state.json") or {}
    return campaign.get("status") or "unknown"


def is_complete(cell):
    """Only a cell whose development and held-out sweep both finished enters an aggregate."""
    return cell["development"].get("status") == "full_complete" and bool(cell["heldout"].get("available"))


def aggregate(cells):
    """Aggregates over complete cells only; running or failed cells are listed, not averaged.

    A partially written ledger would otherwise bias false-accept and retry means."""
    by_gate, by_task_gate = defaultdict(list), defaultdict(list)
    pending = [c["cell"] for c in cells if not is_complete(c)]
    failed = [c["cell"] for c in cells if not is_complete(c) and c.get("state") in ("failed", "blocked")]
    running = [c["cell"] for c in cells if not is_complete(c) and c.get("state") not in ("failed", "blocked")]
    for cell in cells:
        if not is_complete(cell):
            continue
        by_gate[cell["gate"]].append(cell)
        by_task_gate[(cell["task"], cell["gate"])].append(cell)

    def summarize(group):
        heldout = [c["heldout"].get("success_rate") for c in group if c["heldout"].get("available")]
        return {"cells": [c["cell"] for c in group], "n": len(group),
                "heldout_success_mean": mean(heldout),
                "heldout_success_per_cell": heldout,
                "gate_false_accept_rate_mean": mean(c["development"]["gate_false_accept_rate"] for c in group),
                "gate_false_reject_rate_mean": mean(c["development"]["gate_false_reject_rate"] for c in group),
                "retries_spent_mean": mean(c["development"]["retries_spent"] for c in group),
                "development_seconds_mean": mean(c["development"]["development_seconds"] for c in group),
                "self_eval_balanced_accuracy_heldout_mean": mean(
                    (c["heldout"].get("self_eval_vs_oracle") or {}).get("balanced_accuracy") for c in group),
                "judge_balanced_accuracy_heldout_mean": mean(
                    (c["heldout"].get("judge_vs_oracle") or {}).get("balanced_accuracy") for c in group)}
    return {"by_gate": {g: summarize(v) for g, v in sorted(by_gate.items())},
            "by_task_gate": {f"{t}|{g}": summarize(v) for (t, g), v in sorted(by_task_gate.items())},
            "complete_cells": sum(len(v) for v in by_gate.values()), "pending_cells": pending,
            "failed_cells": failed, "running_cells": running}


def fmt(value, digits=2):
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def markdown(cells, agg):
    lines = ["# Development-gate study: analysis", "",
             "Statistical unit is a cell (one independent development run). Development seeds "
             "(51–65) and held-out seeds (1–50) are reported separately and never pooled.", "",
             f"Complete cells in the aggregates: {agg.get('complete_cells', len(cells))} of {len(cells)}"
             + (f"; failed or blocked, no held-out: {', '.join(agg['failed_cells'])}"
                if agg.get("failed_cells") else "")
             + (f"; still running: {', '.join(agg['running_cells'])}"
                if agg.get("running_cells") else "") + ".", "",
             "## By gate", "",
             "| gate | cells | held-out success (mean) | per cell | gate false-accept rate | gate false-reject rate | retries | dev hours | self-eval bal.acc (held-out) | judge bal.acc (held-out) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for g, s in agg["by_gate"].items():
        hours = None if s["development_seconds_mean"] is None else s["development_seconds_mean"] / 3600
        lines.append(f"| {g} | {s['n']} | {fmt(s['heldout_success_mean'])} | "
                     f"{', '.join(fmt(v) for v in s['heldout_success_per_cell'])} | "
                     f"{fmt(s['gate_false_accept_rate_mean'])} | {fmt(s['gate_false_reject_rate_mean'])} | "
                     f"{fmt(s['retries_spent_mean'], 1)} | {fmt(hours, 1)} | "
                     f"{fmt(s['self_eval_balanced_accuracy_heldout_mean'])} | {fmt(s['judge_balanced_accuracy_heldout_mean'])} |")
    lines += ["", "## By task and gate", "",
              "| task | gate | cells | held-out success per cell | false-accept | false-reject |", "|---|---|---|---|---|---|"]
    for key, s in agg["by_task_gate"].items():
        task, g = key.split("|")
        lines.append(f"| {task} | {g} | {s['n']} | {', '.join(fmt(v) for v in s['heldout_success_per_cell'])} | "
                     f"{fmt(s['gate_false_accept_rate_mean'])} | {fmt(s['gate_false_reject_rate_mean'])} |")
    lines += ["", "## Per cell", "",
              "| cell | gate | status | graded dev trials | dev oracle success | gate pass | false-accept | false-reject | judge 2x2 (dev) | held-out | completed (any rc) | crash done/steps/exc | self-eval 2x2 (held-out) | judge 2x2 (held-out) |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in cells:
        d, h = c["development"], c["heldout"]
        def cell22(s):
            if not s:
                return "–"
            return f"tp{s.get('tp',0)} fp{s.get('fp',0)} fn{s.get('fn',0)} tn{s.get('tn',0)} und{s['n']-s['decided']}"
        crashes = h.get("crash_breakdown")
        crash_text = ("–" if not crashes else
                      f"{crashes['completed_then_acted']}/{crashes['step_limit_exhausted']}/{crashes['program_exception']}")
        lines.append(f"| {c['cell']} | {c['gate']} | {c.get('state') or d['status']} | {d['graded_trials']} | "
                     f"{fmt(d['oracle_success_rate_dev'])} | {fmt(d['gate_pass_rate_dev'])} | "
                     f"{d['gate_false_accept']} | {d['gate_false_reject']} | {cell22(d.get('judge_vs_oracle_dev'))} | "
                     f"{fmt(h.get('success_rate'))} | {fmt(h.get('task_completed_rate_any_rc'))} | {crash_text} | "
                     f"{cell22(h.get('self_eval_vs_oracle'))} | {cell22(h.get('judge_vs_oracle'))} |")
    lines += ["", "Reading guide: for the self_eval gate a false-accept is a seed the world's done() "
              "called true while the environment did not; a false-reject is the opposite. For the "
              "oracle gate both are zero by construction. 'judge 2x2 (dev)' exists only for the "
              "vlm_judge gate, whose development verdicts are the judge's. 'held-out' is the frozen "
              "rule (a non-zero sandbox exit is a crash even when the task was completed); 'completed "
              "(any rc)' counts task_completed regardless of the exit code; the crash column splits "
              "completed-then-kept-acting / step limit exhausted / program exception. Held-out 2x2 "
              "compares the shadow self-evaluation (and the post-hoc judge) with the environment on frozen seeds."]
    return "\n".join(lines) + "\n"


# --- optional post-hoc judging -------------------------------------------------------


def judge_heldout(case_path, *, endpoint=None, model=None, effort=None, resume=True):
    """Run the VLM judge over every graded held-out seed; writes heldout/judge/seed_NN.json."""
    from aspire.sim.cap.world_model.development_gate import judge_config
    from aspire.sim.cap.world_model.vlm_judge import judge
    case = json.loads(Path(case_path).read_text())
    config = judge_config(case)
    endpoint, model = endpoint or config["endpoint"], model or config["model"]
    evaluation = Path(case["control"]) / "heldout"
    report = read_json(evaluation / "heldout_result.json") or {}
    out = evaluation / "judge"
    out.mkdir(parents=True, exist_ok=True)
    done = 0
    for seed in HELDOUT_SEEDS:
        if report.get("per_seed", {}).get(str(seed)) not in ("success", "failure"):
            continue
        target = out / f"seed_{seed:02d}.json"
        if resume and target.is_file():
            continue
        trials = [p for p in (evaluation / f"seed_{seed:02d}" / "results").rglob("trial_*") if p.is_dir()]
        if len(trials) != 1:
            continue
        result = judge(endpoint, model, case["task"].replace("_", " "), trials[0],
                       effort=effort or config["effort"], max_tokens=config["max_tokens"], timeout=config["timeout"])
        target.write_text(json.dumps(result, indent=2) + "\n")
        done += 1
    return done


def discover(parent):
    return sorted(p for p in Path(parent).glob("*/case.json"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=Path, action="append", default=[], help="a cell's case.json (repeatable)")
    parser.add_argument("--parent", type=Path, help="experiment parent directory; every */case.json is a cell")
    parser.add_argument("--out", type=Path, help="directory for summary.json and summary.md")
    parser.add_argument("--judge-heldout", action="store_true", help="run the VLM judge post hoc over held-out trials first")
    parser.add_argument("--judge-endpoint")
    parser.add_argument("--judge-model")
    args = parser.parse_args(argv)
    cases = list(args.case) + (discover(args.parent) if args.parent else [])
    if not cases:
        parser.error("name at least one --case or a --parent")
    if args.judge_heldout:
        for path in cases:
            print(json.dumps({"judged": judge_heldout(path, endpoint=args.judge_endpoint, model=args.judge_model),
                              "case": str(path)}), flush=True)
    cells = [analyze_cell(path) for path in cases]
    agg = aggregate(cells)
    summary = {"cells": cells, "aggregate": agg}
    text = markdown(cells, agg)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (args.out / "summary.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
