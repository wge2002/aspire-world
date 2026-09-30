"""Apply this FULL study's budget and staged interface path to the render.

Difference from the pilot's `pilot_scope`: the frozen campaign renderer already
hard-codes the 51-65 development partition and already promises the Stage 2
held-out evaluation on seeds 1-50, and this study performs exactly both. So
nothing about seed scope or the held-out transition is rewritten — the pilot's
whole reason for rewriting them is gone, and every "no held-out evaluation",
`pilot_complete` and three-seed sentence it appended is absent here.

Two things still have to be applied:

1. The rendered text points at the C study's copy of the interface document;
   only this study's own copy is staged and pinned, so the path is corrected.
2. Seed 51 carries one charged real infrastructure diagnostic (the new DSW
   preflight, imported by `full_import`), so the prompt must state seed 51's
   remaining budget as 2 and every other development seed's as 3 rather than
   letting the worker infer a uniform three.

Every rewrite is exact-count checked, so a changed upstream template fails here
instead of shipping a prompt that disagrees with the case.
"""
from __future__ import annotations

from pathlib import Path

#: The single charged infrastructure diagnostic imported before the solver starts.
IMPORTED_SEED = 51
IMPORTED_ATTEMPTS = 1
ATTEMPT_LIMIT = 3
DEV_SEEDS = tuple(range(51, 66))
HELDOUT_SEEDS = tuple(range(1, 51))

#: `executable_world_profile.section()` points the worker at the C study's copy of
#: the interface document. Only this study's own copy is staged and pinned in the
#: runtime manifest, so the render must name the staged path.
C_STUDY_INTERFACE = "docs/experiments/code-world-c-opt-ablation-20260920/NATIVE_WORLD_INTERFACE.md"

TERMINAL = "full_complete"

#: Pilot-only text. None of it may appear in a full-run render, in either
#: direction: neither an unstaged interface path, nor any pilot boundary
#: sentence that a stale scope module could have appended.
FORBIDDEN_AFTER = (
    C_STUDY_INTERFACE,
    "development pilot",
    "pilot_complete",
    "No 50-seed benchmark",
    "no held-out evaluation",
    "Pilot boundary",
)

#: Text the full render must still contain: the real held-out transition this
#: study performs. A render that lost it is not a full-run render.
REQUIRED_AFTER = (
    "51–65",
    "Held-out validation on seeds 1–50",
    "The coordinator runs Stage 2 validation after you return.",
)


class ScopeError(RuntimeError):
    """The render did not contain what this rewrite is written against."""


def _replace(text: str, old: str, new: str, *, count: int = 1) -> str:
    found = text.count(old)
    if found != count:
        raise ScopeError(f"expected {count} occurrence(s) of {old[:60]!r}, found {found}")
    return text.replace(old, new)


def dev_seeds(case: dict) -> list[int]:
    seeds = sorted(int(s) for s in case["dev_seeds"])
    if seeds != list(DEV_SEEDS):
        raise ScopeError(
            f"the full study's development partition is {list(DEV_SEEDS)}, got {seeds}")
    return seeds


def remaining_budget(case: dict) -> dict[str, int]:
    """The budget the prompt states, computed the same way the ledger charges it."""
    return {str(s): ATTEMPT_LIMIT - (IMPORTED_ATTEMPTS if s == IMPORTED_SEED else 0)
            for s in dev_seeds(case)}


def interface_doc(case: dict) -> str:
    doc = case.get("full_interface_doc") or case.get("pilot_interface_doc")
    if not doc:
        raise ScopeError("the case must name this study's staged interface document")
    return str(doc)


def budget_block(case: dict) -> str:
    seeds = dev_seeds(case)
    budget = remaining_budget(case)
    others = [s for s in seeds if s != IMPORTED_SEED]
    return f"""
---

## Recorded budget for this study

New simulator executions in this cell use development seeds {seeds[0]}-{seeds[-1]} only. The
declared prior C development evidence remains an allowed common input. The
per-seed cap is unchanged: **{ATTEMPT_LIMIT} TOTAL simulator replay attempts per seed**.

Seed {IMPORTED_SEED} already carries **one charged attempt** before you start. It is a real
infrastructure diagnostic — one observation, SAM3 segmentation, grasp planning and
IK, executed on seed {IMPORTED_SEED} in this study's own recorded DSW preflight — imported
with its source and output hashes and an explicit provenance record. It ran no
task policy. It is recorded as a charged `diagnostic` attempt, so:

- seed {IMPORTED_SEED} has **{budget[str(IMPORTED_SEED)]}** real executions left, not {ATTEMPT_LIMIT};
- seeds {others[0]}-{others[-1]} have **{ATTEMPT_LIMIT}** each;
- it is **not** an initial run, **not** a task success and **not** a selectable
  candidate: the ledger excludes diagnostic phases from tested bundles, so seed {IMPORTED_SEED}
  still owes its own initial evidence out of its {budget[str(IMPORTED_SEED)]} remaining attempts;
- retries, resumes and recovery cannot refund it. An interrupted attempt keeps its
  slot; report it as a blocker rather than repeating it as if it never ran.

There is no global revision, turn or action ceiling on top of this per-seed cap.
Work failure by failure for as long as your own budget lasts.

Ask the ledger instead of counting yourself:

  .venv-libero/bin/python3 scripts/libero/native_world_protocol.py status
"""


def scope_worker(text: str, case: dict, repo: Path | None = None) -> str:
    """Correct the staged interface path and state the actual per-seed budget."""
    dev_seeds(case)
    doc = interface_doc(case)
    if repo is not None and not (Path(repo) / doc).is_file():
        raise ScopeError(f"the staged interface document is missing: {doc}")
    text = _replace(text, C_STUDY_INTERFACE, doc)
    text = text + budget_block(case)
    _verify(text, case, role="worker")
    return text


COORDINATOR_BLOCK = """
---

## Study scope (this cell)

Development seeds {first}-{last}, then the frozen held-out evaluation on seeds
1-50 that you run after the worker returns. The per-seed development cap is
{limit} total simulator replay attempts, unchanged, and there is no global
revision, turn or action ceiling on top of it.

Seed {imported} starts with one charged attempt already in the ledger: this study's own
real infrastructure diagnostic, imported with its hashes and provenance. It is not
an initial run, not a task success and not a selectable candidate. Remaining real
executions are {budget}. You cannot grant, reset or refund a budget, and neither can
the worker.

The prompt in `{prompt_path}` is frozen. Pass it **verbatim** as the `prompt`
parameter of your one `Agent` dispatch. A rewritten, shortened or summarized
primary assignment is rejected by a PreToolUse gate before any task execution, and
the simulator stays unreachable until a verbatim dispatch has been recorded, so a
rewrite cannot be worked around — it can only waste the cell. Helper agents you or
the worker may dispatch for ordinary reading and analysis are unaffected.

After the worker returns, report its summary verbatim and run the protocol
`check`. Development finalization and the frozen 1-50 held-out sweep are run by
the outer driver, from the selected and frozen bundle; never run held-out seeds
inside the solver session, and never report a held-out outcome back to the worker.
"""


def scope_coordinator(text: str, case: dict) -> str:
    """Give the coordinator the same budget and the real two-stage boundary."""
    seeds = dev_seeds(case)
    if "verbatim" not in text:
        raise ScopeError("the coordinator render no longer pins the assignment verbatim")
    budget = remaining_budget(case)
    text = text + COORDINATOR_BLOCK.format(
        first=seeds[0], last=seeds[-1], limit=ATTEMPT_LIMIT, imported=IMPORTED_SEED,
        budget=", ".join(f"seed {s}: {budget[str(s)]}" for s in seeds),
        prompt_path=f"{case['control']}/worker-prompt.md")
    _verify(text, case, role="coordinator")
    return text


def _verify(text: str, case: dict, *, role: str) -> None:
    """No pilot scope may survive, and the real held-out transition must remain."""
    leftovers = [marker for marker in FORBIDDEN_AFTER if marker in text]
    if leftovers:
        raise ScopeError(f"pilot-only scope text is present in a full render: {leftovers}")
    if role == "worker":
        missing = [marker for marker in REQUIRED_AFTER if marker not in text]
        if missing:
            raise ScopeError(f"the full worker render lost required scope text: {missing}")


def apply(campaign, case: dict):
    """Patch the imported campaign module so every render is scoped identically.

    Idempotent per cell: the prepare-time renderer and the driver both call it, and
    a second call must not append the budget block twice.
    """
    if getattr(campaign, "_full_scope_cell", None) == case["id"]:
        return campaign
    if getattr(campaign, "_full_scope_cell", None) is not None:
        raise ScopeError("this campaign module is already scoped for another cell")
    if getattr(campaign, "_pilot_scope_cell", None) is not None:
        raise ScopeError("this campaign module already carries the pilot scope patch")
    original_worker = campaign.worker_prompt
    original_coordinator = campaign.coordinator_prompt

    def worker_prompt(inner_case, repo):
        return scope_worker(original_worker(inner_case, repo), inner_case, Path(repo))

    def coordinator_prompt(inner_case, prompt_path):
        return scope_coordinator(original_coordinator(inner_case, prompt_path), inner_case)

    campaign.worker_prompt = worker_prompt
    campaign.coordinator_prompt = coordinator_prompt
    campaign._full_scope_cell = case["id"]
    return campaign
