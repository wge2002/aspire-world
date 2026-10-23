"""CC_TASK_14 M3(b): the world's own goal judgment against the simulator, per seed.

Development only. After every graded development trial the worker's `trial`
record carries `judgment_calibration`: this trial's agreement plus, for the
world program it ran, which development seeds the world judged done while the
simulator said failed (false accept) or the reverse (false reject). `status`
and `check` carry the same table for every world program. Nothing here grades,
gates or refunds anything, and no held-out or unseen seed is ever read.

The world's judgment is the framework's final shadow judgment recorded in the
trial's `judgment_world/manifest.json` (the `foundation_calibration` copy when
present). The simulator's outcome is the row's `task_completed`; for a
`world_program_error` row, whose graded outcome is a failure by rule, it is the
preserved raw simulator outcome (`raw_result`), labelled as such, because the
judgment is about the scene the trial ended in.
"""
from __future__ import annotations

import json
from pathlib import Path

PHASES = ("smoke", "initial", "repair", "sweep")
DEV_SEEDS = range(51, 66)
LIMIT = 15  # seeds listed per category in a note; the counts are always complete


def final_verdict(task_dir: Path, row: dict) -> str | None:
    """'true' / 'false' / 'unknown' from the final shadow judgment, None when absent."""
    final = ((row.get("foundation_calibration") or {}).get("final_self_evaluation"))
    if final is None:
        try:
            manifest = json.loads((Path(task_dir) / row["directory"] / "judgment_world/manifest.json").read_text())
        except (OSError, ValueError, KeyError):
            return None
        evaluations = [e for e in manifest.get("self_evaluations") or [] if isinstance(e, dict)]
        finals = [e for e in evaluations if e.get("origin") == "framework_final"]
        final = (finals or evaluations or [None])[-1]
    if not isinstance(final, dict) or final.get("verdict") not in ("true", "false", "unknown"):
        return None
    return final["verdict"]


def simulator_outcome(row: dict) -> tuple[bool | None, str]:
    if row.get("status") == "complete" and row.get("task_completed") in (0, 1, True, False):
        return bool(row["task_completed"]), "graded"
    raw = row.get("raw_result") or {}
    if row.get("status") == "world_program_error" and raw.get("task_completed") in (0, 1, True, False):
        return bool(raw["task_completed"]), "raw simulator outcome of a world_program_error row"
    return None, "no simulator outcome"


def classify(task_dir: Path, row: dict) -> dict:
    verdict = final_verdict(task_dir, row)
    outcome, source = simulator_outcome(row)
    if outcome is None:
        category = "unavailable"
    elif verdict is None:
        category = "no_judgment"
    elif verdict == "unknown":
        category = "unknown"
    elif verdict == "true":
        category = "agree_success" if outcome else "false_accept"
    else:
        category = "false_reject" if outcome else "agree_failure"
    return {"seed": row["seed"], "phase": row["phase"], "directory": row["directory"],
            "world_sha256": (row.get("bundle") or {}).get("world"), "world_verdict": verdict,
            "simulator_success": outcome, "outcome_source": source, "category": category}


def development_rows(state) -> list[dict]:
    return [r for r in state.data["trials"]
            if r.get("phase") in PHASES and r.get("seed") in DEV_SEEDS and r.get("executed", True)
            and not r.get("alias_of") and r.get("status") in ("complete", "world_program_error")]


def table(entries: list[dict]) -> dict:
    counts = {}
    for e in entries:
        counts[e["category"]] = counts.get(e["category"], 0) + 1
    said_done = counts.get("agree_success", 0) + counts.get("false_accept", 0)
    said_not = counts.get("agree_failure", 0) + counts.get("false_reject", 0)

    def seeds(category):
        return [{"seed": e["seed"], "phase": e["phase"], "directory": e["directory"]}
                for e in entries if e["category"] == category]
    return {"trials": len(entries), "counts": counts,
            "false_accept": seeds("false_accept"), "false_reject": seeds("false_reject"),
            "unknown_seeds": sorted({e["seed"] for e in entries if e["category"] == "unknown"}),
            "precision_when_world_says_done": (round(counts.get("agree_success", 0) / said_done, 3)
                                               if said_done else None),
            "precision_when_world_says_not_done": (round(counts.get("agree_failure", 0) / said_not, 3)
                                                   if said_not else None)}


def headline(world: dict) -> str:
    fa, fr = world["false_accept"], world["false_reject"]
    if not fa and not fr:
        return (f"world goal judgment agreed with the simulator on every judged development trial "
                f"({world['trials']} trials, {world['counts'].get('unknown', 0)} unknown)")
    parts = []
    if fa:
        parts.append("judged DONE but the simulator says FAILED on seeds "
                     + ", ".join(str(x["seed"]) for x in fa[:LIMIT]))
    if fr:
        parts.append("judged NOT done but the simulator says SUCCESS on seeds "
                     + ", ".join(str(x["seed"]) for x in fr[:LIMIT]))
    return ("your world's goal judgment " + "; ".join(parts)
            + ". Inspect those trials' keyframes and the clauses of the final self-evaluation, and "
              "correct the goal definition from what the observations actually showed")


def summary(state) -> dict:
    """Per world program, newest first: the calibration table with its seeds."""
    entries = [classify(state.task_dir, r) for r in development_rows(state)]
    worlds = list(dict.fromkeys(e["world_sha256"] for e in reversed(entries)))
    by_world = []
    for digest in worlds:
        world = table([e for e in entries if e["world_sha256"] == digest])
        by_world.append({"world_sha256": digest, **world, "headline": headline(world)})
    return {"revision": "cc14-r1",
            "scope": ("development trials only (seeds 51-65); the world's final goal judgment against the "
                      "simulator outcome; advisory, never a grade"),
            "all_trials": table(entries), "by_world": by_world}


def trial_note(state, record: dict) -> dict:
    """What the worker reads with a trial result: this trial, then its world's table."""
    try:
        this = classify(state.task_dir, record)
        world = table([classify(state.task_dir, r) for r in development_rows(state)
                       if (r.get("bundle") or {}).get("world") == this["world_sha256"]])
        return {"this_trial": {k: this[k] for k in ("world_verdict", "simulator_success",
                                                    "outcome_source", "category")},
                "this_world": world, "headline": headline(world)}
    except Exception as exc:  # Advisory feedback must never fail a settled trial.
        return {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
