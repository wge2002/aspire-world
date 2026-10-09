"""Gate the original native scope and append truthful current-study diagnostic accounting. No method/budget rewrite except the already spent seed51 preflight retry."""
from __future__ import annotations

from pathlib import Path

RETRY_LIMIT = 3
DEV_SEEDS = tuple(range(51, 66))
HELDOUT_SEEDS = tuple(range(1, 51))

TERMINAL = "full_complete"

#: Interface paths belonging to other studies. This study stages and pins its
#: own copy, so naming another study's path would point the worker at bytes no
#: manifest covers.
OTHER_STUDY_INTERFACES = (
    "docs/experiments/code-world-c-opt-ablation-20260920/NATIVE_WORLD_INTERFACE.md",
    "docs/experiments/code-world-qwen-full-20260924/NATIVE_WORLD_INTERFACE.md",
    "docs/experiments/world-native-fixloop-abc-opus46-bowl-20260914/NATIVE_WORLD_INTERFACE.md",
)

#: Text that must not reach either arm's render. The continuation grants and the
#: seed-51 budget sentences are the full study's, and repeating them here would
#: promise an input this study does not stage and a budget the ledger does not
#: hold. The pilot boundary strings are checked because a stale scope module is
#: the one way they could reappear.
FORBIDDEN = OTHER_STUDY_INTERFACES + (
    "CONTINUATION/REPAIR",
    "The sole permitted prior input",
    "prior_c",
    "already carries **one charged attempt**",
    "charged attempt",
    "Recorded budget for this study",
    "development pilot",
    "pilot_complete",
    "No 50-seed benchmark",
    "no held-out evaluation",
    "Pilot boundary",
)

#: The real two-stage boundary the frozen template already states, and which
#: this study performs unchanged.
REQUIRED_WORKER = (
    "51–65",
    "Held-out validation on seeds 1–50",
    "The coordinator runs Stage 2 validation after you return.",
)

#: Condition A's package: the four high-level strategy documents, no world. The
#: sentence naming them is built from the case, so it is checked per cell in
#: `verify_worker` rather than listed here.
STRATEGY_MD = ("localize.md", "grasp.md", "transport.md", "manipulation.md")
FORBIDDEN_A_WORKER = ("Executable C", "initial_world_program.py", "world.done()",
                      "executable world contract", "--world-program")

#: Condition C's package: the executable world, fresh, with no strategy library.
REQUIRED_C_WORKER = (
    "FRESH first generation for this task",
    "initial_world_program.py",
    "There is no object inventory or high-level MD library.",
    "Everything else below retains the original native failure-by-failure Fix Loop",
)


class ScopeError(RuntimeError):
    """The render disagrees with the case it was rendered from."""


def dev_seeds(case: dict) -> list[int]:
    seeds = sorted(int(s) for s in case["dev_seeds"])
    if seeds != list(DEV_SEEDS):
        raise ScopeError(f"this study's development partition is {list(DEV_SEEDS)}, got {seeds}")
    return seeds


def heldout_seeds(case: dict) -> list[int]:
    seeds = sorted(int(s) for s in case.get("heldout_seeds", HELDOUT_SEEDS))
    if seeds != list(HELDOUT_SEEDS):
        raise ScopeError(f"this study's held-out partition is 1-50, got {seeds}")
    return seeds


def remaining_budget(case: dict) -> dict[str, int]:
    """Computed, not asserted. Either nothing is imported (three retries on every
    seed) or exactly the one seed-51 preflight retry this study's diagnostic spends."""
    imported = case.get("imported_retries") or {}
    if imported not in ({}, {"51": 1}):
        raise ScopeError(f"this study imports at most one preflight retry on seed51: {imported}")
    return {str(seed): RETRY_LIMIT - imported.get(str(seed), 0) for seed in dev_seeds(case)}


#: The development gate a cell declares; the absent key is the legacy oracle.
GATES = ("oracle", "self_eval", "vlm_judge")


def development_gate(case: dict) -> str:
    value = case.get("development_gate") or "oracle"
    if value not in GATES:
        raise ScopeError(f"unknown development_gate {value!r}")
    return value


#: What a sealed render must say, and what it must not.
SEALED_REQUIRED = ("Development gate: {gate}", "`gate.passed`",
                   "graded by the environment, not", "trial_<seed>_sandboxrc_<rc>_sealed")
SEALED_FORBIDDEN = ("_reward_1.000", "stdout/stderr/reward",
                    "Read the per-trial development calibration feedback")
ORACLE_REQUIRED = ("_reward_1.000", "Read the per-trial development calibration feedback")

#: The heading `executable_world_profile.PREDICTION_CONTRACT` opens with. A render
#: carries it iff the case declares `prediction_contract: "p1"`; the off arm writes
#: no key and must render byte-identically to the gate study's oracle arm.
P1_HEADING = "### Prediction contract p1: predictions checked against the next real observation"


def prediction_contract(case: dict) -> str | None:
    value = case.get("prediction_contract")
    if value not in (None, "p1"):
        raise ScopeError(f"unknown prediction_contract {value!r}")
    return value




def interface_doc(case: dict) -> str | None:
    """The staged world interface document, for C only."""
    if case["condition"] != "C":
        return None
    doc = case.get("world_interface_doc")
    if not doc:
        raise ScopeError("a C cell must name this study's staged interface document")
    if doc in OTHER_STUDY_INTERFACES:
        raise ScopeError(f"the case names another study's interface document: {doc}")
    return str(doc)


def verify_worker(text: str, case: dict, repo: Path | None = None) -> None:
    dev_seeds(case)
    heldout_seeds(case)
    remaining_budget(case)
    problems = [f"forbidden text in the render: {marker!r}"
                for marker in FORBIDDEN if marker in text]
    problems += [f"the render lost required scope text: {marker!r}"
                 for marker in REQUIRED_WORKER if marker not in text]
    strategy_sentence = (f"read the four strategy files in `{case['skill_library_dir']}` "
                         "(" + ", ".join(STRATEGY_MD) + "), the skill API reference at")
    if case["condition"] == "A":
        if strategy_sentence not in text:
            problems.append("condition A render does not point at this cell's four strategy "
                            f"files under {case['skill_library_dir']}")
        problems += [f"condition A render carries C-only text: {marker!r}"
                     for marker in FORBIDDEN_A_WORKER if marker in text]
    else:
        doc = interface_doc(case)
        if "read the four strategy files in" in text:
            problems.append("condition C render offers a high-level strategy library")
        if "shared skill library in `.claude/libero/skills/`" in text:
            problems.append("condition C render still points at the shared skill library")
        problems += [f"condition C render is missing {marker!r}"
                     for marker in REQUIRED_C_WORKER if marker not in text]
        if f"Read `{doc}` in full before editing." not in text:
            problems.append(f"condition C render does not point at the staged {doc}")
        if repo is not None and not (Path(repo) / doc).is_file():
            problems.append(f"the staged interface document is missing: {doc}")
        gate = development_gate(case)
        if gate == "oracle":
            problems += [f"oracle render lost {marker!r}" for marker in ORACLE_REQUIRED if marker not in text]
            if "Development gate:" in text:
                problems.append("oracle render carries a sealed-gate contract")
        else:
            problems += [f"{gate} render is missing {marker.format(gate=gate)!r}"
                         for marker in SEALED_REQUIRED if marker.format(gate=gate) not in text]
            problems += [f"{gate} render still reads the outcome: {marker!r}"
                         for marker in SEALED_FORBIDDEN if marker in text]
    # The p1 section appears iff the case declares p1.
    p1 = prediction_contract(case) == "p1"
    if p1 and P1_HEADING not in text:
        problems.append("p1 render is missing the prediction-contract section")
    if not p1 and P1_HEADING in text:
        problems.append("render carries the p1 prediction-contract section but the case is not p1")
    # Both arms: the task string the case declares must be the one assigned.
    if f"TASK:  {case['task']}" not in text:
        problems.append(f"the render does not assign the case's task {case['task']!r}")
    if f"SUITE: {case['suite']}" not in text:
        problems.append(f"the render does not assign the case's suite {case['suite']!r}")
    if problems:
        raise ScopeError("; ".join(problems))


def verify_coordinator(text: str, case: dict) -> None:
    problems = [f"forbidden text in the render: {marker!r}"
                for marker in FORBIDDEN if marker in text]
    if "verbatim" not in text:
        problems.append("the coordinator render no longer pins the assignment verbatim")
    if case["task"] not in text:
        problems.append(f"the coordinator render does not name the task {case['task']!r}")
    if problems:
        raise ScopeError("; ".join(problems))


def report_handoff_worker(text: str) -> str:
    """CC 2.1.220 subagents return reports; their coordinator persists them.

    Do not disable native report restrictions or work around them with Bash.
    Program generation, selection, budgets and report contents are unchanged.
    """
    heading = "### Step 5 — Write findings.md (REQUIRED)"
    if text.count(heading) != 1:
        raise ScopeError("required findings handoff anchor changed")
    text = text.replace(heading, "### Step 5 — Return findings to the coordinator (REQUIRED)")
    anchor = "Write `$TASK_DIR/findings.md`."
    if text.count(anchor) != 1:
        raise ScopeError("required findings write instruction changed")
    text = text.replace(anchor, "Return the complete findings.md content as text in your final response, "
                        "enclosed by BEGIN_FINDINGS_MD and END_FINDINGS_MD on separate lines. "
                        "The coordinator writes it verbatim to `$TASK_DIR/findings.md`.")
    text = text.replace("  findings.md written: yes/no",
                        "  findings.md content returned for coordinator: yes/no")
    # Selection precedes Step 5, so its completion check cannot require a file
    # the coordinator has not received yet. The outer check remains mandatory.
    command = "  .venv-libero/bin/python3 scripts/libero/native_world_protocol.py check\n"
    if text.count(command) != 1:
        raise ScopeError("selection check anchor changed")
    text = text.replace(command, "  # The coordinator runs check after saving your returned findings.\n")
    text += ("\nNative report contract: do not Write/Edit findings.md or try to create it with Bash. "
             "Return the FULL four-section findings text, including honest all-failure or blocked "
             "evidence when applicable. Save task_analysis.md, programs, named copies and per-seed "
             "BLOCKED.md through their ordinary allowed file tools. Select an actually tested "
             "bundle before returning. A missing findings.md before your return is expected; "
             "it is not a reason to spend another trial or keep retrying file tools.\n")
    return text


def report_handoff_coordinator(text: str, case: dict) -> str:
    anchor = "When the worker returns, report its summary verbatim,\nthen run:"
    if text.count(anchor) != 1:
        raise ScopeError("coordinator report/check ordering anchor changed")
    task = (Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"])
    replacement = ("When the worker returns, first extract the full text between "
                   "BEGIN_FINDINGS_MD and END_FINDINGS_MD in its response and use your Write tool "
                   f"to save that text verbatim to `{task / 'findings.md'}`. "
                   "Do not write the marker lines, invent findings, add task strategy, or change "
                   "any robot program. If the report is absent or incomplete, ask the SAME worker "
                   "via SendMessage for the missing text, without new trials for report writing. "
                   "Then report its summary verbatim and run:")
    return text.replace(anchor, replacement)


def apply(campaign, case: dict):
    """Gate the frozen renderer. Returns the same bytes it was given.

    Idempotent per cell, because the prepare-time renderer and the driver both
    call it on the same module object.
    """
    if getattr(campaign, "_two_task_scope_cell", None) == case["id"]:
        return campaign
    if getattr(campaign, "_two_task_scope_cell", None) is not None:
        raise ScopeError("this campaign module is already scoped for another cell")
    for stale in ("_full_scope_cell", "_pilot_scope_cell"):
        if getattr(campaign, stale, None) is not None:
            raise ScopeError(f"this campaign module already carries {stale}")
    original_worker = campaign.worker_prompt
    original_coordinator = campaign.coordinator_prompt

    def worker_prompt(inner_case, repo):
        text = original_worker(inner_case, repo)
        text = report_handoff_worker(text)
        if (inner_case.get("imported_retries") or {}) == {"51": 1}:
            text += "\nPreflight accounting: seed 51 has already spent one of its three TOTAL retries on this cell's nonprivileged infrastructure diagnostic. It has TWO remaining; seeds 52-65 have THREE each. Read the imported ledger row; do not refund, rerun or count the diagnostic as task-success evidence. This changes no other Fix Loop budget.\n"
        verify_worker(text, inner_case, Path(repo))
        return text

    def coordinator_prompt(inner_case, prompt_path):
        text = original_coordinator(inner_case, prompt_path)
        text = report_handoff_coordinator(text, inner_case)
        verify_coordinator(text, inner_case)
        return text

    campaign.worker_prompt = worker_prompt
    campaign.coordinator_prompt = coordinator_prompt
    campaign._two_task_scope_cell = case["id"]
    return campaign
