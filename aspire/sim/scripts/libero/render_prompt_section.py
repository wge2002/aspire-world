#!/usr/bin/env python3
"""Render a study's `SEARCH_CONTRACT` prompt section to a reproducible markdown file.

The p3 round produced `CC_TASK_2_PROMPT_SECTION.md` and `CC_TASK_3_PROMPT_SECTION.md`
by hand, so their line references drifted from the code they describe and neither
could be regenerated or diffed. This renders the same artifact from source:

  * the contract text is read back OUT of `profile.section()` for the fixture
    p3-search case, not retyped, so what the file contains is what the solver
    is actually sent;
  * the occurrence count is asserted, so a second copy of the section cannot
    slip into the prompt unnoticed;
  * the contract's sha256 is recorded, so a later diff says whether the text or
    only the prose around it changed.

Usage:
  scripts/libero/render_prompt_section.py --out <path> --task CC_TASK_1
  scripts/libero/render_prompt_section.py --out <path> --task CC_TASK_6 --budget

`--budget` renders the aspire-v1 development-budget variant (CC_TASK_6): the
search contract as a budget cell reads it plus `BUDGET_CONTRACT`, and the
worker / coordinator / rules texts the budget adds outside that section.

Read-only with respect to everything except `--out`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SIM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SIM.parents[1]))
sys.path.insert(0, str(SIM))
sys.path.insert(0, str(SIM / "scripts/libero"))
sys.path.insert(0, str(SIM / "tests"))

import executable_world_profile as profile  # noqa: E402
from test_prediction_contract import executable_case  # noqa: E402

#: The modifications this file records, newest last. Kept beside the renderer so
#: a regenerated file states its own provenance instead of relying on prose
#: written once by hand.
CHANGES = {
    "CC_TASK_1": [
        "Coverage before freezing (p4): a candidate is evidence only once its own bytes have run "
        "on all of development seeds 51-65; `coverage_gate` in check/select/finalize; a sweep the "
        "banked attempts cannot pay for is a BLOCKER, never a lowered gate; held-out stays sealed; "
        "selection reasons with an 'N of M' pass count are recomputed from the ledger.",
        "Triage the sweep by mode, then repair once (p4): failures grouped by trial signature into "
        "`coverage_triage.json`; repair the MODE, not one seed; one repair round, itself swept in full.",
        "Say WHY progress() returned None: `progress_reason()` -> `unobserved` (idle channel) or "
        "`unavailable` (broken channel); both excluded from N; `unavailable_classes` names a dead "
        "channel; recovery by re-observation and re-association.",
    ],
    "CC_TASK_2": [
        "Coverage sweep counted separately (design (b)): missing seeds are swept with "
        "`trial --phase sweep`, which spends no per-seed repair retry; once per seed per tested "
        "candidate, at most two candidates swept per cell (first sweep + the sweep after the single "
        "repair round). Replaces 'charged against the same per-seed budget / paid by the banked "
        "attempts', which made the gate unreachable once repair had spent every retry.",
        "`coverage_gate` numbers are the ledger's current availability (`sweep_runs_needed`, "
        "`sweepable`, `unsweepable`, `sweep_rounds_left`), not the early stop's banked snapshot; a "
        "gate no candidate can reach is `decision_type: terminal` with a structured "
        "`terminal_reasons` entry, and the cell stops as BLOCKED.",
    ],
    "CC_TASK_4": [
        "No cap on swept candidates: `SWEEP_ROUNDS` is removed. A sweep still spends no repair retry, "
        "still runs each seed at most once per candidate (an infrastructure-interrupted sweep may be "
        "re-run), held-out is still refused; total sweeping is bounded only by the development deadline. "
        "Prompt: sweep only the candidate you intend to freeze; never select one whose coverage is "
        "incomplete. `coverage_gate` drops `sweep_rounds_allowed`/`sweep_rounds_left` and reports "
        "`sweep_candidate_limit: null`.",
        "All-fail tie-break compares freezable candidates only (complete, or still sweepable). The "
        "selected bundle passes when no freezable candidate scores strictly better. A strictly better "
        "candidate that is still partially covered makes the finding ADVISORY (its crash count is a "
        "lower bound) when the selection itself is complete; a strictly better COMPLETE candidate keeps "
        "it SUBSTANTIVE. Advisories are reported under `advisories` and never block ready.",
        "Generic terminal guard: when `check` has substantive errors and `legal_actions` is empty (no "
        "charged retry, no pending repair, no sweepable candidate, no better freezable selection) and "
        "no error is authorable (reports, notes, selection record), `check` returns "
        "`decision_type: terminal` with `terminal_reasons[{code: no_legal_action}]`.",
    ],
    "CC_TASK_6": [
        "ASPIRE v1.0 development budget (`development_budget: \"aspire-v1\"`, opt-in per case, part of "
        "the identity): it replaces the per-seed 3-retry count in the cells that set it; every other "
        "render is byte-identical (SEARCH_CONTRACT's text and digest are unchanged).",
        "Phase A: until seed 51 actually passes, every attempt runs on seed 51, uncapped; no sweep, no "
        "select, no freeze; phase A is never terminal (only the development deadline ends it).",
        "Phase B: the bundle that first passed seed 51 is v0, swept free over 52-65; at most 8 versions, "
        "every version on all 15 seeds (first run `--phase repair --mode <mode> --mode-seeds <seeds>`, the "
        "rest `--phase sweep`) before the next one; at most 3 versions per mode, then the mode is blocked "
        "(`attempts/mode_<mode>_BLOCKED.md`); RETRY_LIMIT no longer applies.",
        "Freeze when the selection passes 15/15, all 8 versions are used, or every remaining failing seed "
        "is in a blocked mode; select most passes, then fewer crashes (program_ok=false), then simpler.",
        "Probes: `--phase probe`, an observation-only REPL (no motor command, no step(), env limited to "
        "task_language), never a version, spends nothing; refused statically when it names an action.",
        "Superseded p4 sentences replaced in the budget render only: the initial early stop / banked "
        "paragraph, 'none of the seed's three repair retries', 'sweep only the candidate you intend to "
        "freeze', the all-fail tie-break paragraph, 'One repair round is allowed', the p2 runtime "
        "contract's 'three total attempts per seed'; worker ACCOUNTING ('Hard limit: 3 TOTAL retries per "
        "seed') -> BUDGET_ACCOUNTING, SELECTION -> BUDGET_SELECTION, Stage 0/1 per-seed wording; "
        "coordinator gets the ASPIRE coordinator rules; search_control rules gain BUDGET_RULES.",
        "Reported fields: `status` / `check` / `finalize` carry `development_budget` (phase, phase_a, "
        "versions_used / versions_left / versions_max, versions, candidates, modes, mode_versions, "
        "blocked_modes, best_complete, freeze_conditions, incomplete_versions, probes, probe_violations, "
        "next); `legal_actions` gains `phase` and `versions_left`.",
    ],
}


def render(task: str, budget: bool = False) -> tuple[str, dict]:
    case = executable_case(prediction_contract="p3-search")
    if budget:
        case["development_budget"] = profile.BUDGET_REVISION
    section = profile.section(case)
    contract = profile.budget_search_contract() if budget else profile.SEARCH_CONTRACT
    occurrences = section.count(contract)
    if occurrences != 1:
        raise SystemExit(f"expected exactly one occurrence of SEARCH_CONTRACT, found {occurrences}")
    digest = hashlib.sha256(contract.encode()).hexdigest()
    lines = [
        f"# {task} · rendered SEARCH_CONTRACT prompt section (p3-search, p4 coverage/triage/progress reasons)",
        "",
        "Generated by `scripts/libero/render_prompt_section.py` — do not edit by hand; regenerate.",
        "",
        (f"- source: `scripts/libero/executable_world_profile.py` (`budget_search_contract()` = "
         f"`SEARCH_CONTRACT` with `BUDGET_SEARCH_EDITS` + `BUDGET_CONTRACT`, sha256 `{digest}`; "
         f"SEARCH_CONTRACT itself sha256 `{hashlib.sha256(profile.SEARCH_CONTRACT.encode()).hexdigest()}`)"
         if budget else
         f"- source: `scripts/libero/executable_world_profile.py` (`SEARCH_CONTRACT`, sha256 `{digest}`)"),
        "- appended to the p2 runtime contract only when `case['prediction_contract'] == 'p3-search'`",
        f"- occurrence count of the contract inside `profile.section(case)`: {occurrences} (asserted)",
        f"- fixture: `tests/test_prediction_contract.py::executable_case` (C / judgment / executable r1 / "
        f"foundation r1 / full / fresh / oracle), rendered p3-search",
        f"- rendered section length: {len(section)} chars",
        "",
        "## Changes recorded for this task",
        "",
    ]
    lines += [f"- {item}" for item in CHANGES.get(task, ["(none recorded)"])]
    lines += [
        "",
        "## Where the search_control summary enters the solver's context",
        "",
        "The native solver calls `native_world_protocol.py` through its shell tool and reads the printed JSON:",
        "",
        "- `native_world_protocol.py` `search_gate` -> `search_control` writes `search_control_report.json` "
        "before every charged repair and returns its summary; the trial record carries it as `search_control`.",
        "- `search_after_trial` refreshes the report after every graded p3 trial and attaches "
        "`search_control_next` with `this_trial` (`progress`, `progress_status`, decided object/gripper "
        "predictions, `observed`, `reason`) and a hint whose wording follows the reason: the "
        "`unobserved` hint says the attempt is not saturation evidence, the `unavailable` hint says the "
        "measurement channel is broken and must be repaired first.",
        "- `check` carries `coverage_gate` (`required_seeds`, `covered_seeds`, `missing`, `sweep_runs_needed`, "
        "`sweepable`, `unsweepable`, `reachability`), `terminal_reasons`, `legal_actions`, `advisories`, "
        "`selection_evidence` and `coverage_triage`; `finalize` writes `coverage_gate` and `advisories` "
        "into the result.",
        "- `coverage_triage.json` (written beside `search_control_report.json`) holds the sweep's failures "
        "grouped by signature: per bundle, mode -> seeds -> deciding field -> what the group did.",
        "",
        "## Rendered section",
        "",
        "```text",
        section.rstrip("\n"),
        "```",
        "",
    ]
    if budget:
        lines += budget_texts()
    return "\n".join(lines), {"sha256": digest, "occurrences": occurrences, "chars": len(section)}


def budget_texts() -> list[str]:
    """The aspire-v1 texts outside SEARCH_CONTRACT, read back from their sources."""
    import native_world_campaign as campaign
    from aspire.sim.cap.world_model import search_control
    protocol = "scripts/libero/runtime/native_world_protocol.py"
    lines = ["## Worker prompt: budget accounting (replaces 'Hard limit: 3 TOTAL retries per seed')", "",
             "```text", campaign.BUDGET_ACCOUNTING.format(protocol=protocol).rstrip("\n"), "```", "",
             "## Worker prompt: Step 4 selection (budget)", "", "```text",
             campaign.BUDGET_SELECTION.format(protocol=protocol, world_files="  <world files>\n",
                                              fallback=campaign.FALLBACK_BUDGET).rstrip("\n"), "```", "",
             "## Worker prompt: per-seed wording replaced in the budget render", ""]
    for old, new in campaign.BUDGET_WORDING:
        lines += ["- was: `" + old.replace("\n", "\\n") + "`", "  now: `" + new.replace("\n", "\\n") + "`"]
    lines += ["", "## Coordinator prompt: ASPIRE coordinator rules (budget)", "", "```text",
              campaign.BUDGET_COORDINATOR.rstrip("\n"), "```", "",
              "## search_control summary rules added for a budget ledger", ""]
    lines += [f"- {rule}" for rule in search_control.BUDGET_RULES]
    old, new = search_control.BUDGET_RULE_EDIT
    lines += [f"- RULES clause replaced: `{old}` -> `{new}`", ""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--task", default="CC_TASK_1")
    parser.add_argument("--budget", action="store_true", help="render the aspire-v1 budget variant")
    args = parser.parse_args()
    text, meta = render(args.task, budget=args.budget)
    args.out.write_text(text)
    print(json.dumps({"out": str(args.out), **meta}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
