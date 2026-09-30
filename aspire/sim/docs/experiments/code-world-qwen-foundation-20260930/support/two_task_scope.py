"""Gate the original native scope and append truthful current-study diagnostic accounting. No method/budget rewrite except the already charged seed51 preflight."""
from __future__ import annotations

from pathlib import Path

ATTEMPT_LIMIT = 3
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
    """Uniform, because nothing is imported. Computed, not asserted."""
    imported = case.get("imported_charged_attempts") or {}
    if imported != {"51": 1}:
        raise ScopeError(f"foundation study requires one charged preflight on seed51: {imported}")
    return {str(seed): ATTEMPT_LIMIT - imported.get(str(seed), 0) for seed in dev_seeds(case)}



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
        text += "\nFoundation preflight accounting: seed 51 has already used one of its three TOTAL attempts for this cell's nonprivileged infrastructure diagnostic. It has TWO remaining; seeds 52-65 have THREE each. Read the imported ledger row; do not refund, rerun or count the diagnostic as task-success evidence. This changes no other Fix Loop budget.\n"
        verify_worker(text, inner_case, Path(repo))
        return text

    def coordinator_prompt(inner_case, prompt_path):
        text = original_coordinator(inner_case, prompt_path)
        verify_coordinator(text, inner_case)
        return text

    campaign.worker_prompt = worker_prompt
    campaign.coordinator_prompt = coordinator_prompt
    campaign._two_task_scope_cell = case["id"]
    return campaign
