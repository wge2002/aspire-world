#!/usr/bin/env python3
"""Read-only p4 retrospective over the frozen p3 search-control ledgers.

Four questions, per cell, answered from the ledger and the recorded trial
artifacts and nothing else:

  1. coverage   which development seeds the FROZEN bundle's own executions ran,
                and which are missing. Under the p4 gate those missing seeds
                would have had to be swept before the cell could freeze.
  2. sweep      how many attempts the sweep would cost, and whether the attempts
                the p3 early stop banked are enough to pay for it.
  3. modes      the cell's sweep failures grouped by `trial_signature`, so the
                next repair can be aimed at a named mode instead of a re-draw.
  4. channel    whether the cell's progress channel was broken or merely idle.

Nothing here writes to a p3 result root. The p3 protocol recorded only
`search_progress`, never a reason, so the p4 reason is RECOVERED from each
trial's own recorded object facts (`control.trace_progress_reason`): a value was
recorded -> observed; no value and an object fact left `never_known` /
`unknown_at_end` -> unavailable (the world could not resolve the fact); no value
with every object fact resolved -> unobserved. The recovery is labelled in the
output and never claimed as something p3 recorded.

`--cells` selects among the study's five cells; a cell whose ledger or frozen
bundle is absent is reported as unavailable rather than silently skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SIM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SIM.parents[1]))
from aspire.sim.cap.world_model import search_control as sc  # noqa: E402

#: The study's five cells, in the order PLAN.md's coverage table lists them.
CELLS = ("stove_p3_r1", "stove_p3_r2", "bowl_p3_r1", "drawer_p3_r1", "drawer_p3_r2")
#: Cells the p3 round froze through `select`; drawer_p3_r2's solver was killed by
#: the 14 h wall before selecting and its bundle was chosen by the coordinator.
COORDINATOR_CHOSEN = {"drawer_p3_r2": "heldout-supplementary-93265905"}
#: 15 development seeds x 3 attempts is the whole per-cell development budget.
DEV_SEEDS = tuple(range(51, 66))
ATTEMPTS_PER_SEED = 3


def ledger_path(cell_root: Path) -> Path | None:
    found = sorted(cell_root.glob("outputs/libero_fix_loop/*/*/development_state.json"))
    return found[0] if found else None


def frozen_bundle(cell_root: Path, ledger: dict) -> tuple[dict | None, str]:
    """The bundle the cell froze, and how it was arrived at.

    A solver-selected cell records it in the ledger. drawer_p3_r2 never reached
    `select`, so its bundle comes from the coordinator's supplementary provenance
    file, which is the record of what was actually evaluated at held-out.
    """
    selected = ledger.get("selected") or {}
    if selected.get("bundle_sha256") and selected.get("bundle"):
        return selected["bundle"], "ledger_selected"
    supplementary = COORDINATOR_CHOSEN.get(cell_root.name)
    if supplementary:
        produced = cell_root / supplementary / "provenance.json"
        if produced.is_file():
            data = json.loads(produced.read_text())
            if data.get("bundle"):
                return data["bundle"], "coordinator_chosen_supplementary"
    return None, "none"


def final_coverage(task_dir: Path, ledger: dict, bundle: dict) -> list[dict]:
    """Real executions of the frozen bundle, mirroring `final_coverage`.

    Recomputed here rather than through a ledger object so this script reads a
    p3 result root without constructing (or resuming) any cell state.
    """
    try:
        digest = sc_bundle_identity(bundle)
    except ValueError:
        return []
    out = []
    for row in ledger["trials"]:
        if row.get("phase") not in sc.CHARGED_PHASES or not row.get("spends_retry"):
            continue
        if row.get("phase") == "diagnostic" or row.get("status") == "running":
            continue
        if not row.get("executed", True) or row.get("alias_of"):
            continue
        if row.get("bundle_sha256") == digest:
            out.append(row)
    return out


def sc_bundle_identity(bundle: dict) -> str:
    import hashlib
    body = json.dumps(bundle, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def banked_attempts(ledger: dict) -> int:
    return int((ledger.get("search_control") or {}).get("banked_attempts") or 0)


def sweep_plan(task_dir: Path, ledger: dict, covered: list[int]) -> dict:
    """What the p4 gate would cost this cell: one attempt per missing seed.

    A missing seed is swept with the frozen bundle's exact bytes, so the cost is
    one replay each. The banked attempts the p3 early stop released are what pays
    for it; when they fall short, that is the blocker the gate would report.
    """
    spent = {}
    for row in ledger["trials"]:
        if row.get("spends_retry") and row.get("executed", True) and not row.get("alias_of"):
            spent[row["seed"]] = spent.get(row["seed"], 0) + 1
    missing = [s for s in DEV_SEEDS if s not in covered]
    remaining = {str(s): max(0, ATTEMPTS_PER_SEED - spent.get(s, 0)) for s in missing}
    affordable = [s for s in missing if remaining[str(s)] > 0]
    return {"missing": missing, "sweep_attempts_required": len(missing),
            "banked_attempts": banked_attempts(ledger),
            "shortfall": max(0, len(missing) - banked_attempts(ledger)),
            "seeds_with_budget": affordable,
            "needs_repair_round": len(covered) < len(DEV_SEEDS),
            "affordable_with_banked": len(missing) <= banked_attempts(ledger)}


def channel_counts(task_dir: Path, attempts: list[dict]) -> dict:
    """Observed / unobserved / unavailable attempts, with the reason recovered.

    p3 recorded no reason, so each graded attempt's reason is recovered from its
    own trace. The recovery is what makes drawer_p3_r1 legible: its world returns
    None whenever the handle association is not `known`, which is a dead channel,
    and under p3 that fact was indistinguishable from "this trial did not look".
    The reason is asked about the cell's own target entity (see
    `control.target_entities`), which is the entity `progress()` reads.
    """
    targets = sc.target_entities(attempts)
    counts = {"observed": 0, "unobserved": 0, "unavailable": 0}
    per_seed = {}
    for a in attempts:
        if not a["graded"]:
            continue
        value = sc.recorded_progress_value(task_dir / a["directory"])
        reason = sc.trace_progress_reason(value, a["signature"] or {}, targets)
        counts[reason] = counts.get(reason, 0) + 1
        per_seed.setdefault(str(a["seed"]), []).append(reason)
    total = sum(counts.values())
    return {**counts, "graded": total, "target_entities": sorted(targets),
            "unavailable_fraction": (counts["unavailable"] / total) if total else None,
            "per_seed": per_seed}


def analyse(cell_root: Path) -> dict:
    path = ledger_path(cell_root)
    if path is None:
        return {"cell": cell_root.name, "available": False, "reason": "no development ledger"}
    task_dir = path.parent
    ledger = json.loads(path.read_text())
    bundle, provenance = frozen_bundle(cell_root, ledger)
    attempts = sc.charged_attempts(ledger, task_dir)
    if bundle is None:
        return {"cell": cell_root.name, "available": True, "frozen_bundle": None,
                "frozen_bundle_provenance": provenance, "coverage": None,
                "sweep": None, "modes": None, "channel": channel_counts(task_dir, attempts),
                "reason": "no frozen bundle recorded; coverage cannot be assessed"}
    covered_rows = final_coverage(task_dir, ledger, bundle)
    covered = sorted({r["seed"] for r in covered_rows})
    pass_seeds = sorted({r["seed"] for r in covered_rows if r.get("task_completed")})
    modes = sc.failure_modes(attempts)
    frozen_digest = sc_bundle_identity(bundle)
    frozen_modes = [m for m in modes if any(
        a["bundle_sha256"] == frozen_digest for a in attempts if a["signature"]
        and a["signature"]["digest"] == m["signature"])]
    return {
        "cell": cell_root.name, "available": True, "ledger": str(path),
        "task": (ledger.get("identity") or {}).get("task"),
        "frozen_bundle": bundle, "frozen_bundle_provenance": provenance,
        "coverage": {"required_seeds": list(DEV_SEEDS), "covered_seeds": covered,
                     "covered_count": len(covered), "missing": [s for s in DEV_SEEDS
                                                               if s not in covered],
                     "passing_seeds": pass_seeds, "passes": len(pass_seeds)},
        "sweep": sweep_plan(task_dir, ledger, covered),
        "modes": {"mode_count": len(modes),
                  "family_count": len(sc.mode_families(modes)),
                  "families": sc.mode_families(modes),
                  "modes": [{"label": m["label"], "seeds": m["seeds"], "size": len(m["seeds"]),
                             "deciding_field": m["deciding_field"],
                             "signature": m["signature"],
                             "object_residuals": m["signature_definition"]["object_residuals"],
                             "world_facts": m["signature_definition"]["prediction_status"],
                             "outcome": m["signature_definition"]["outcome"]}
                            for m in modes],
                  "frozen_bundle_modes": [{"label": m["label"], "seeds": m["seeds"],
                                           "size": len(m["seeds"]),
                                           "signature": m["signature"]} for m in frozen_modes]},
        "channel": channel_counts(task_dir, attempts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--study-root", type=Path, required=True)
    parser.add_argument("--cells", default=",".join(CELLS))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cells = [c for c in args.cells.split(",") if c]
    results = [analyse(args.study_root / cell) for cell in cells]
    checked = [r for r in results if r["available"] and r["coverage"]]
    output = {
        "schema": "p4-coverage-gate-retro/1",
        "study_root": str(args.study_root),
        "read_only": True,
        "generated_by": "scripts/libero/coverage_gate_retro.py",
        "coverage_definition": "development seeds whose OWN executions used the frozen bundle's "
                               "exact bytes; an alias row is not a second run",
        "sweep_definition": "one attempt per missing seed, charged against the attempts the "
                            "p3 early stop banked; a shortfall is the gate's blocker",
        "signature_definition": "prediction-status counts per entity/fact + rounded object-level "
                                "residuals + outcome",
        "mode_vs_family": "a mode is an exact signature digest (the defensible grouping); a "
                          "family is the coarse behavior its residuals describe (the actionable "
                          "one). Both are reported.",
        "family_seed_overlap": "families are defined over FAILING ATTEMPTS, not seeds, so one seed "
                               "that failed several times in different ways appears in more than "
                               "one family. The seed sets are therefore not a partition.",
        "channel_recovery": "p3 recorded no progress reason, so each graded attempt's reason is "
                            "RECOVERED from its own recorded object facts: value present -> "
                            "observed; no value and an object fact never_known/unknown_at_end -> "
                            "unavailable; no value with every object fact resolved -> unobserved. "
                            "This is a recovery, not a p3-recorded field.",
        "cells": results,
        "summary": [{k: r.get(k) for k in ("cell", "available")} | {
            "covered": (r["coverage"]["covered_count"] if r["coverage"] else None),
            "missing": (r["coverage"]["missing"] if r["coverage"] else None),
            "passing": (r["coverage"]["passes"] if r["coverage"] else None),
            "sweep_attempts": (r["sweep"]["sweep_attempts_required"] if r["sweep"] else None),
            "banked": (r["sweep"]["banked_attempts"] if r["sweep"] else None),
            "affordable": (r["sweep"]["affordable_with_banked"] if r["sweep"] else None),
            "modes": (r["modes"]["mode_count"] if r["modes"] else None),
            "families": (r["modes"]["family_count"] if r["modes"] else None),
            "unavailable": r["channel"]["unavailable"],
            "unobserved": r["channel"]["unobserved"],
            "observed": r["channel"]["observed"]} for r in results],
    }
    args.out.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(output["summary"], indent=2))
    # A cell that could not be read at all is a real failure to report, not a
    # quiet omission from the table.
    return 0 if len(checked) == len(cells) else 1


if __name__ == "__main__":
    raise SystemExit(main())
