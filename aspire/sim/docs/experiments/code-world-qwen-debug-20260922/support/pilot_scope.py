"""Apply this pilot's seed scope, recorded budget and stop boundary to the render.

The frozen campaign renderer is not edited. It renders from the pristine
template, which hard-codes the 51-65 development partition and promises a Stage 2
held-out evaluation afterwards; the appended executable-world C text repeats both
("51-65 development budget ... and outer frozen evaluation"). This pilot runs
three development seeds and no held-out evaluation at all, so the rewrite is
applied to the COMPLETE final render — template body, C overlay text and the
per-arm executable-world block together — and leaves no contradictory held-out
instruction behind.

Every rewrite is exact-count checked. If the upstream template or the C overlay
text changes, rendering fails loudly here rather than quietly shipping a prompt
whose scope disagrees with the case.
"""
from __future__ import annotations

from pathlib import Path

# The single charged infrastructure diagnostic that is imported into the ledger
# before the solver starts (see pilot_import.py). It is real, it is charged, and
# it is neither an initial run nor a selectable candidate.
IMPORTED_SEED = 51
IMPORTED_ATTEMPTS = 1
ATTEMPT_LIMIT = 3
# `executable_world_profile.section()` points the worker at the C study's copy of
# the interface document. Only this pilot's own copy is staged and pinned in the
# runtime manifest, so the render must name the staged path.
C_STUDY_INTERFACE = "docs/experiments/code-world-c-opt-ablation-20260920/NATIVE_WORLD_INTERFACE.md"
PILOT_TERMINAL = "pilot_complete"

# Text that promises a held-out evaluation. None of it may survive the rewrite.
FORBIDDEN_AFTER = (
    "51–65", "51-65", "Stage 2 validation", "Held-out validation on",
    "outer frozen evaluation", "the frozen evaluation reads", C_STUDY_INTERFACE,
)


class ScopeError(RuntimeError):
    """The render did not contain what this rewrite is written against."""


def _replace(text: str, old: str, new: str, *, count: int = 1) -> str:
    found = text.count(old)
    if found != count:
        raise ScopeError(f"expected {count} occurrence(s) of {old[:60]!r}, found {found}")
    return text.replace(old, new)


def seed_labels(case: dict) -> tuple[list[int], str]:
    seeds = sorted(int(s) for s in case["dev_seeds"])
    if not seeds or any(s <= 50 for s in seeds) or len(set(seeds)) != len(seeds):
        raise ScopeError(f"invalid development partition for a pilot: {case['dev_seeds']}")
    if IMPORTED_SEED not in seeds:
        raise ScopeError(f"seed {IMPORTED_SEED} carries the imported diagnostic but is not in the partition")
    return seeds, ", ".join(str(s) for s in seeds)


def remaining_budget(case: dict) -> dict[str, int]:
    """The budget the prompt states, computed the same way the ledger charges it."""
    seeds, _ = seed_labels(case)
    return {str(s): ATTEMPT_LIMIT - (IMPORTED_ATTEMPTS if s == IMPORTED_SEED else 0)
            for s in seeds}


def interface_doc(case: dict) -> str:
    doc = case.get("pilot_interface_doc")
    if not doc:
        raise ScopeError("the case must name this pilot's staged interface document")
    return str(doc)


def budget_block(case: dict) -> str:
    seeds, listed = seed_labels(case)
    budget = remaining_budget(case)
    others = [s for s in seeds if s != IMPORTED_SEED]
    return f"""
---

## Recorded budget for this pilot

New simulator executions in this cell use development seeds {listed} only. The
declared prior C development evidence remains an allowed common input. The
per-seed cap is unchanged: **{ATTEMPT_LIMIT} TOTAL simulator replay attempts per seed**.

Seed {IMPORTED_SEED} already carries **one charged attempt** before you start. It is a real
infrastructure diagnostic — one observation, SAM3 segmentation, grasp planning and
IK, executed on seed {IMPORTED_SEED} in the recorded DSW preflight — imported with its source
and output hashes and an explicit provenance record. It ran no task policy. It is
recorded as a charged `diagnostic` attempt, so:

- seed {IMPORTED_SEED} has **{budget[str(IMPORTED_SEED)]}** real executions left, not {ATTEMPT_LIMIT};
- seeds {", ".join(str(s) for s in others)} have **{ATTEMPT_LIMIT}** each;
- it is **not** an initial run, **not** a task success and **not** a selectable
  candidate: the ledger excludes diagnostic phases from tested bundles, so seed {IMPORTED_SEED}
  still owes its own initial evidence out of its {budget[str(IMPORTED_SEED)]} remaining attempts;
- retries, resumes and recovery cannot refund it. An interrupted attempt keeps its
  slot; report it as a blocker rather than repeating it as if it never ran.

Ask the ledger instead of counting yourself:

  .venv-libero/bin/python3 scripts/libero/native_world_protocol.py status
"""


def boundary_block(case: dict) -> str:
    _, listed = seed_labels(case)
    return f"""
---

## Pilot boundary (where this cell stops)

This is a **development pilot**. It ends at development finalization.

- Development seeds {listed} only.
- Never run `evaluate.py`, `scripts/libero/native_world_heldout.py` or
  `scripts/libero/run_fix_loop_validation.py`; never run seeds 1-50; never open a
  held-out artifact. **No 50-seed benchmark is performed in this pilot**, by you,
  by the coordinator or by the outer driver, before or after you return.
- Nothing follows your finalization. When `select`, `check` and the outer
  finalization have succeeded, this cell is finished and its recorded terminal
  status is `{PILOT_TERMINAL}`.
- Everything else above still binds unchanged: the failure-by-failure Fix Loop, the
  per-seed cap, the forbidden-API list, tested-bundle selection, self-evaluation via
  `world.done()`, candidate rehearsal/replay, and `findings.md`.
"""


def scope_worker(text: str, case: dict, repo: Path | None = None) -> str:
    """Rewrite the complete rendered worker prompt to this pilot's actual scope."""
    _, listed = seed_labels(case)
    doc = interface_doc(case)
    if repo is not None and not (Path(repo) / doc).is_file():
        raise ScopeError(f"the staged interface document is missing: {doc}")

    # 1. Seed scope, in every place the template and the C overlay state it.
    text = _replace(text, "Stage 1 (debug development seeds 51–65) ONLY.",
                    f"Stage 1 (debug development seeds {listed}) ONLY.")
    text = _replace(
        text,
        "Then diagnose its failures on development seeds 51–65 and select the single "
        "best generalizable fix. (Held-out validation on seeds 1–50 happens later, run "
        "by the coordinator — not you.)",
        f"Then diagnose its failures on development seeds {listed} and select the single "
        "best generalizable fix. (No held-out validation on seeds 1–50 is performed in "
        "this pilot, by you or by anyone else.)")
    text = _replace(text, "## Stage 1: Debug Seeds 51–65",
                    f"## Stage 1: Debug Seeds {listed}")
    text = _replace(text, "For each seed 51–65, check the reward",
                    f"For each of seeds {listed}, check the reward")

    # 2. The promised held-out transition, which this pilot does not have.
    text = _replace(
        text,
        "The coordinator runs Stage 2 validation after you return. Running held-out "
        "seeds yourself violates the benchmark protocol.",
        "This development pilot has no Stage 2: no held-out evaluation runs at any "
        "point, before or after you return, by you or by the coordinator. Running "
        "held-out seeds yourself violates the benchmark protocol.")
    # 3. The executable-world C text repeats both claims in its closing line.
    text = _replace(
        text,
        "51–65 development budget, tested-bundle selection and outer frozen evaluation.",
        f"{listed} development budget and tested-bundle selection. This pilot performs no "
        "held-out evaluation or benchmark.")
    text = _replace(text, "← the frozen evaluation reads this exact path",
                    "← the outer protocol's finalization reads this exact path")

    # 4. The interface document this cell actually stages and pins.
    text = _replace(text, C_STUDY_INTERFACE, doc)

    text = text + budget_block(case) + boundary_block(case)
    _verify(text, case)
    return text


COORDINATOR_BLOCK = """
---

## Pilot scope (this cell)

Development seeds {listed}, and nothing else. The per-seed cap is {limit} total
simulator replay attempts, unchanged.

Seed {imported} starts with one charged attempt already in the ledger: a real
infrastructure diagnostic, imported with its hashes and provenance. It is not an
initial run, not a task success and not a selectable candidate. Remaining real
executions are {budget}. You cannot grant, reset or refund a budget, and neither can
the worker.

The prompt in `{prompt_path}` is frozen. Pass it **verbatim** as the `prompt`
parameter of your one `Agent` dispatch. A rewritten, shortened or summarized
primary assignment is rejected by a PreToolUse gate before any task execution, and
the simulator stays unreachable until a verbatim dispatch has been recorded, so a
rewrite cannot be worked around — it can only waste the cell. Helper agents you or
the worker may dispatch for ordinary reading and analysis are unaffected.

This is a development pilot. Never run `evaluate.py`,
`scripts/libero/native_world_heldout.py` or
`scripts/libero/run_fix_loop_validation.py`; no 50-seed benchmark is performed in
this pilot by anyone. After the worker returns, report its summary verbatim, run
the protocol `check`, and report the actual result. When development finalization
has succeeded this cell is complete and its terminal status is `{terminal}`.
"""


def scope_coordinator(text: str, case: dict) -> str:
    """Give the coordinator the same scope, budget and stop boundary."""
    seeds, listed = seed_labels(case)
    if "verbatim" not in text:
        raise ScopeError("the coordinator render no longer pins the assignment verbatim")
    budget = remaining_budget(case)
    text = text + COORDINATOR_BLOCK.format(
        listed=listed, limit=ATTEMPT_LIMIT, imported=IMPORTED_SEED,
        budget=", ".join(f"seed {s}: {budget[str(s)]}" for s in seeds),
        prompt_path=f"{case['control']}/worker-prompt.md", terminal=PILOT_TERMINAL)
    _verify(text, case)
    return text


def _verify(text: str, case: dict) -> None:
    """No contradictory scope may survive in a finished render."""
    import re
    leftovers = [marker for marker in FORBIDDEN_AFTER if marker in text]
    if leftovers:
        raise ScopeError(f"contradictory scope text survived the rewrite: {leftovers}")
    seeds, _ = seed_labels(case)
    allowed = {str(s) for s in seeds}
    stray = sorted({m.group() for m in re.finditer(r"(?<![\w.\-])(5[1-9]|6[0-5])(?![\w.\-])", text)}
                   - allowed)
    if stray:
        raise ScopeError(f"out-of-partition seed references survived the rewrite: {stray}")


def apply(campaign, case: dict):
    """Patch the imported campaign module so every render is scoped identically.

    Idempotent per cell: the prepare-time renderer and the driver both call it, and
    a second call must not append the pilot blocks twice.
    """
    if getattr(campaign, "_pilot_scope_cell", None) == case["id"]:
        return campaign
    if getattr(campaign, "_pilot_scope_cell", None) is not None:
        raise ScopeError("this campaign module is already scoped for another cell")
    original_worker = campaign.worker_prompt
    original_coordinator = campaign.coordinator_prompt

    def worker_prompt(inner_case, repo):
        return scope_worker(original_worker(inner_case, repo), inner_case, Path(repo))

    def coordinator_prompt(inner_case, prompt_path):
        return scope_coordinator(original_coordinator(inner_case, prompt_path), inner_case)

    campaign.worker_prompt = worker_prompt
    campaign.coordinator_prompt = coordinator_prompt
    campaign._pilot_scope_cell = case["id"]
    return campaign
