#!/usr/bin/env python3
"""Native original Fix Loop campaign for one cell (A1, B1 or C1).

Native Claude Code owns generation, tools, agent notifications and compaction.
There is no scripted LLM call loop here and the authenticated remote endpoint is
never routed through claude_with_local_model.sh: the native binary is invoked
directly with a per-cell CLAUDE_CONFIG_DIR.

The worker prompt is the pristine `.claude/libero/fix-loop/subagent-prompt.md`
template. Only mechanical edits are applied: command paths, the total-attempt
accounting wording, native scope, the A/B/C strategy-MD condition, and the
opt-in world interface. The exact rendered prompt and its diff against the
pristine template are saved for review.

An optional case `profile` refines only how a world condition is authored:
  (absent)   the legacy world adapter prompt: world program plus object inventory
  simple     the separate shortened ordinary code-world prompt
  judgment   this same full native template, with only the world contract, the
             companion filenames and the trial arguments changed
Judgment therefore keeps the complete development, repair and selection workflow;
the shortened simple prompt is never used for it.

A NEW judgment cell in condition C may set `world_use_revision: "r1"` to append
one short world-use clarification to its world section. Without that key every
rendered prompt is byte-identical to before.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
from native_cc_freeze import verify_runtime
from native_cc_guard import settings as guard_settings
from native_cc_runtime import ServiceFailure, atomic_json
from native_cc_stream import run_native_cc
from simple_world_profile import is_simple, worker_prompt as simple_worker_prompt

TEMPLATE_START = "<!-- ==================== TEMPLATE START ==================== -->"
TEMPLATE_END = "<!-- ==================== TEMPLATE END ==================== -->"
STRATEGY_MD = ("localize.md", "grasp.md", "transport.md", "manipulation.md")
WORLD_CONDITIONS = {"B", "C"}
ENDPOINT = "https://llmapi.roboscience.xyz:18443"
PROTOCOL = "scripts/libero/native_world_protocol.py"
JUDGMENT_PROFILE = "judgment"
# Opt-in second provider: an unauthenticated loopback vLLM Messages server.
# Absent `model_provider` the cell behaves exactly as before.
LOCAL_VLLM_PROVIDER = "local-vllm"
# The launcher's verified no-key placeholders. vLLM ignores the value but the
# native client requires both fields to be present.
LOCAL_VLLM_PLACEHOLDER = "local-vllm-no-key"
# Server root only: no /v1, no path, and never an off-host destination.
LOOPBACK_ROOT = re.compile(r"^http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}$")
# Provider selectors inherited from the launching shell. They survive the
# credential filter in native_environment but would send this cell's requests to
# Bedrock/Vertex/Foundry/Mantle instead of the loopback server.
INHERITED_PROVIDER_FLAGS = ("CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                            "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_MANTLE",
                            "CLAUDE_CODE_USE_ANTHROPIC_AWS")
# Every alias the native client may resolve a model through, including the two
# the remote branch never needed.
LOCAL_VLLM_MODEL_ALIASES = ("ANTHROPIC_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION",
                            "ANTHROPIC_DEFAULT_FABLE_MODEL",
                            "ANTHROPIC_DEFAULT_OPUS_MODEL",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL",
                            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                            "ANTHROPIC_SMALL_FAST_MODEL",
                            "CLAUDE_CODE_SUBAGENT_MODEL")
# The authorized pilot point. Any other combination is a configuration error,
# not something this branch quietly serves.
PILOT_MODEL = "qwen3.8-flash-next"
PILOT_EFFORT = "xhigh"
PILOT_CONTEXT_TOKENS = 1000000
PILOT_MAX_OUTPUT_TOKENS = 64000
# B/C read the authoritative interface schema from this path. Its bytes are
# pinned in the runtime manifest, so the worker cannot be handed a stale copy.
WORLD_INTERFACE_DOC = ("docs/experiments/world-native-fixloop-abc-opus46-bowl-20260914/"
                       "NATIVE_WORLD_INTERFACE.md")
# The judgment profile has its own short interface document in its own study, so
# the frozen legacy schema above is never handed to a judgment worker.
JUDGMENT_INTERFACE_DOC = ("docs/experiments/code-world-judgment-two-task-20260917/"
                          "NATIVE_WORLD_INTERFACE.md")
# The established harness allowlist. Permissions stay enforced; the task guard
# hook decides which paths and commands are actually reachable.
ALLOWED_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash",
                 "Agent", "TaskOutput", "SendMessage")


class ProtocolFailure(RuntimeError):
    """An outer sequence step did not pass. Recorded, never a completion."""


def is_judgment(case: dict) -> bool:
    """Judgment profile: policy plus a world the policy queries, and no inventory.

    Same spelling as `native_world_protocol.is_judgment`; kept local so rendering
    does not depend on the protocol module's import side effects.
    """
    return case.get("profile") == JUDGMENT_PROFILE


def worker_agent(case: dict, prompt_path: Path) -> dict:
    """The single fix-loop worker, preloaded with this cell's rendered protocol."""
    return {"fix-loop-worker": {
        "description": f"Execute cell {case['id']}'s approved original Fix Loop protocol.",
        "prompt": prompt_path.read_text(),
        "model": "inherit"}}


def pristine_template(repo: Path) -> str:
    text = (repo / ".claude/libero/fix-loop/subagent-prompt.md").read_text()
    return text.split(TEMPLATE_START)[1].split(TEMPLATE_END)[0].strip() + "\n"


def trial_command(phase: str, seed: str, extra: str = "") -> str:
    return (f'  .venv-libero/bin/python3 {PROTOCOL} trial \\\n'
            f'    --phase {phase} --seed {seed}{extra}\n')


JUDGMENT_WORLD_SECTION = f"""
---

## World Interface (this cell)

**Read `{JUDGMENT_INTERFACE_DOC}` before you write either program.** It is the
authoritative description of the three functions, the data-only boundary and the
recorded artifacts. Where this orientation and that document differ, it is correct.

Alongside the robot program you also author a **world program**, and the two are
one artifact: tested together, selected together, frozen together.

  `$TASK_DIR/initial_world_program.py` accompanies `$TASK_DIR/initial_code.py`
  `$TASK_DIR/fix_world_program.py`     accompanies `$TASK_DIR/fix_code.py`

The world is loaded once per episode as the module `world`; the policy reaches it
with `import world`. It defines three required module-level entry points;
additional helpers are allowed:

- `update(obs, last_action)` — the policy hands the world the public observations
  it gathered and its last action, whenever the policy needs to update state.
  The world chooses its internal representation.
- `query(name, **kwargs)` — the policy asks the world a question and receives the
  world's answer. Which questions exist, what they mean, and how the world
  expresses confidence or ignorance are yours to design.
- `snapshot()` — a pure function returning a JSON-serializable dict, for logging.

**The world owns interpretation and judgment; the policy owns sensing, motion and
the decision.** The policy senses through the public robot APIs, passes that data
to the world, and uses the world's answers to choose what to do next. The world
receives data only — never environment or API handles — and never senses or acts
itself.

`query()` and `snapshot()` must return finite JSON, with a dict from `snapshot()`.
Invalid outputs from these two functions are recorded as world-program faults.
The runtime does not constrain the return value of `update()`. Beyond that the framework fixes nothing: there is no
required predicate, no threshold, no verdict vocabulary, no mandatory
every-action gate, and no supplied object inventory. Query, action and recovery
are uncapped — the only budget is the per-seed simulator replay budget above.

Queries and errors are recorded with their arguments, their result and their call
site in `judgment_world/events.jsonl` beside each trial's results; read that trace
while diagnosing. Recording is evidence, not enforcement — the trace does not
check that you consumed an answer sensibly.

The forbidden-API list applies to the world program exactly as it applies to the
robot program: judgments come from your model and from allowed observations,
never from simulator internals.
"""


WORLD_USE_CLARIFICATION = """
`query()` returns the world's interpretation or estimate of the situation, and
the robot program is expected to read that return value and let it decide
something concrete: a target or argument it passes to a motion call, or a branch
that chooses between actions or between continuing and stopping. Updating the
world and then ignoring what it answers leaves the mechanism untested. Which
questions to ask, what the answers mean, and where they belong in your program
remain entirely your design.

After each recorded trial, the trial result carries a `world_use` report stating
what that trial's source and its own recorded query trace together support: no
query, answers only logged, a supported use candidate, or inconclusive. It is
read from your code and the trace, it is evidence about the mechanism rather than
a grade, and it never gates a trial, a selection or the completion of this cell.
"""


def is_sealed(case: dict) -> bool:
    """A cell whose development outcome is sealed behind a development gate.

    Same spelling as `native_world_protocol.sealed_gate`; local so rendering does
    not import the protocol module. An absent or `oracle` gate renders exactly as
    before.
    """
    return bool(case.get("development_gate")) and case["development_gate"] != "oracle"


#: Pristine-template sentences that read the task outcome off the replay
#: directory name. A sealed cell renders them against the recorded gate verdict
#: instead. Each anchor must occur exactly once or the template has drifted.
SEALED_OUTCOME_WORDING = (
    ("For each seed 51–65, check the reward in the replay output dir name: `_reward_1.000` = "
     "success, `_reward_0.000` = failure. List which seeds passed and which failed.",
     "For each seed 51–65, read the recorded trial result's `gate` (or `status` → "
     "`seeds_passing` and `tested_bundles.passes`): `gate.passed` true = passed the development "
     "gate, false = did not. This cell never shows the simulator's reward or task label. List "
     "which seeds passed and which failed."),
    ("  - `summary.txt` — stdout/stderr/reward",
     "  - `summary.txt` — stdout/stderr and the sandbox exit code (the task outcome is sealed)"),
    ("The recorded result names the reward. Check reward in output dir name: `_reward_1.000` = "
     "success, `_reward_0.000` = failure.",
     "The recorded result names the gate verdict: `gate.passed` true = passed the development "
     "gate. The output dir name carries only the sandbox exit code; no reward is shown in this cell."),
)


def seal_outcome_wording(text: str) -> str:
    for anchor, replacement in SEALED_OUTCOME_WORDING:
        if text.count(anchor) != 1:
            raise ProtocolFailure(f"sealed render anchor missing or duplicated: {anchor[:60]!r}")
        text = text.replace(anchor, replacement)
    return text


def world_use_enabled(case: dict) -> bool:
    """The opt-in NEW-C world-use revision: judgment profile, condition C, `r1`.

    Spelled locally for the same reason as `is_judgment`, and kept identical to
    `cap.world_model.world_use_audit.enabled` — a test pins the two together.
    """
    return (case.get("world_use_revision") == "r1" and is_judgment(case)
            and case.get("condition") == "C")


def world_section(case: dict) -> str:
    """Functional definitions only: no JSON call format, no caps, no selection rules.

    The exact schema is not paraphrased here. The worker is pointed at the
    authoritative interface document and required to read it, so nobody hands the
    model a vague or stale copy of the function signatures. Judgment gets its own
    contract; the legacy adapter's section below is unchanged.

    A flagged NEW-C cell appends one short use clarification. Unflagged A/B/C
    renderings are returned byte-for-byte as before.
    """
    if case.get("executable_world_revision"):
        from executable_world_profile import section
        return section(case)
    if is_judgment(case):
        if world_use_enabled(case):
            return JUDGMENT_WORLD_SECTION + WORLD_USE_CLARIFICATION
        return JUDGMENT_WORLD_SECTION
    return f"""
---

## World Interface (this cell)

**Read `{WORLD_INTERFACE_DOC}` before you write either program.** It is the
authoritative schema for the four world functions, the inventory, the
reference/relation timing and the policy-callable helpers. What follows is
orientation only; where the two differ, the document is correct.

Alongside the robot program you also author a **world program** and a **scene
inventory**, and they are part of the artifact that is tested and frozen:

  `$TASK_DIR/initial_world_program.py`, `$TASK_DIR/initial_inventory.json`
  `$TASK_DIR/fix_world_program.py`,     `$TASK_DIR/fix_inventory.json`

The world program defines exactly four module-level functions — `initialize`,
`advance`, `predict`, `assimilate` — and what they do, functionally:

- **State advancement.** `advance` is called *after* a motor action has been
  executed, and receives that action together with the measured post-action
  robot proprioception. It returns the updated scene state. It does not gate or
  precede the robot's motion.
- **Prediction before measurement.** `predict` commits a prediction for the
  current frame *before* any fresh measurement of the manipulated entity is
  taken. That ordering is the point: the comparison is only meaningful because
  the prediction was fixed first. `predict` is read-only — it receives a
  detached copy of the state and only its return value is used.
- **Comparison and assimilation.** When a prediction requests a query, the
  measurement is taken after it, the two are compared, and `assimilate` folds
  the resulting evidence — including the verdict `SUPPORT`, `CONTRADICT` or
  `UNKNOWN` — back into the state.
- **Verification.** Your robot program may call `world_verify()` for the current
  frame's verdict.

The inventory names the scene entities your world program reasons about. Each
entry carries an `id`, a human `label` used as the perception prompt, and a
`role`; exactly one entity has `role: "manipulated"` and exactly one has
`role: "target"`.

There are **no query, action or recovery limits** in this cell — the interface
is uncapped by construction. Use it as much as the task needs. The only budget
is the per-seed simulator replay budget above.

The forbidden-API list applies to the world program exactly as it applies to the
robot program: predictions come from your model and from allowed observations,
never from simulator internals.
"""


def worker_prompt(case: dict, repo: Path) -> str:
    # Only the `simple` profile uses the shortened prompt. Judgment renders from
    # the pristine full native template below, keeping the whole original
    # development/repair/selection workflow; its treatment differences are the
    # world section, the companion filenames and the trial arguments.
    if is_simple(case):
        return simple_worker_prompt(case, repo)
    text = pristine_template(repo)
    task_dir = f"outputs/libero_fix_loop/{case['suite']}/{case['task']}"

    # --- Task assignment: fill the three template variables. ---
    text = text.replace(
        "SUITE: <libero_goal_swap|libero_goal_task|libero_object_swap|"
        "libero_object_task|libero_spatial_swap|libero_spatial_task>",
        f"SUITE: {case['suite']}")
    text = text.replace("TASK:  <task_name_with_underscores>", f"TASK:  {case['task']}")
    text = text.replace("GPU:   <3|4|5|6|7>", f"GPU:   {case['gpu']}")
    text = text.replace("You own GPU $GPU exclusively. Run every replay with "
                        "`CUDA_VISIBLE_DEVICES=$GPU`. Never touch any other GPU.",
                        f"You own physical GPU {case['gpu']} exclusively. The recorded trial "
                        f"command sets the GPU for you; never launch a replay yourself.")

    # --- Mechanical: every simulator call goes through the recorded protocol. ---
    text = text.replace(
        "**Do NOT run held-out seeds 1–50. Do NOT run `run_fix_loop_validation.py`.**",
        "**Do NOT run held-out seeds 1–50. Do NOT run `run_fix_loop_validation.py` or "
        f"`replay_trial.py` directly.** Every simulator call goes through "
        f"`{PROTOCOL}`, which records it and refuses held-out seeds.")
    text = text.replace(
        "for p in 8114 8115 8116; do echo \"port $p: "
        "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:$p/health)\"; done",
        "for p in 8114 8115 8116; do echo \"port $p: "
        "$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:$p/health)\"; done"
        "\n  (already running for this campaign — check only, never restart)")
    return _rewrite_commands(text, case, task_dir)


SNAPSHOT_BLOCK = """```bash
TASK_DIR="{task_dir}"
mkdir -p "$TASK_DIR/attempts"
# The one observation-only scene snapshot. It is outside the per-seed replay
# budget and may be taken exactly once.
{snapshot}```
"""

INITIAL_BLOCK = """```bash
# One initial program on every development seed, one process per Bash call.
# Each of these spends one of that seed's three retries.
{initial}```

Wait for each call to return before starting the next; the tool result and the
native completion notification are how you learn the outcome.
"""


def _rewrite_commands(text: str, case: dict, task_dir: str) -> str:
    world_program_arg = " \\\n    --world-program \"$TASK_DIR/{stem}_world_program.py\""
    # Judgment trials are policy plus world only; the protocol rejects an
    # inventory outright, so the rendered command must not offer one.
    world_args = ("" if case["condition"] not in WORLD_CONDITIONS else
                  world_program_arg if is_judgment(case) else
                  world_program_arg
                  + " \\\n    --inventory \"$TASK_DIR/{stem}_inventory.json\"")

    # Stage 0 setup block -> snapshot through the protocol.
    text = re.sub(
        r"```bash\nTASK_DIR=.*?\n```\n",
        SNAPSHOT_BLOCK.format(task_dir=task_dir,
                              snapshot=trial_command("snapshot", "51")),
        text, count=1, flags=re.S)

    # Stage 0 initial loop -> one recorded call per seed (no shell loop).
    initial = trial_command(
        "initial", "<N>",
        ' \\\n    --code "$TASK_DIR/initial_code.py"'
        + world_args.format(stem="initial"))
    text = re.sub(r"```bash\nfor trial in \$\(seq 51 65\); do.*?\n```\n",
                  INITIAL_BLOCK.format(initial=initial), text, count=1, flags=re.S)
    text = text.replace(
        "Write `$TASK_DIR/initial_code.py` using only allowed APIs. Smoke-test seed 51 first "
        "and fix any crash before continuing.",
        "Write `$TASK_DIR/initial_code.py` using only allowed APIs. You may smoke-test it on "
        "seed 51 first with `--phase smoke`; a smoke spends one of that seed's retries like "
        "any other execution. If the smoke ran the identical program bundle you then submit "
        "as the initial run, record it as that seed's initial evidence with\n\n"
        f"  .venv-libero/bin/python3 {PROTOCOL} alias-smoke --seed 51\n\n"
        "rather than spending another attempt on the same inputs.")

    # Step 3 fix command -> recorded repair on the SAME seed.
    text = re.sub(
        r"Write a fix based on your diagnosis and test it on the failed seed:\n"
        r"(?:  .*\n|\n)*?Check reward",
        "Write a fix based on your diagnosis and re-test it on the SAME failed seed while that\n"
        "seed still has budget:\n\n"
        + trial_command("repair", "<N>",
                        ' \\\n    --code "$TASK_DIR/fix_code.py"'
                        + world_args.format(stem="fix"))
        + "\nThe recorded result names the reward. Check reward",
        text, count=1)

    # REPL retained, run through the protocol so the session is counted once.
    text = re.sub(
        r"```bash\nMUJOCO_GL=egl CUDA_VISIBLE_DEVICES=\$GPU.*?--args\.interactive.*?\n```\n",
        "```bash\n"
        "# Write the whole batch session to a file first, then run it once. Same API\n"
        "# execution semantics as before; it counts as ONE attempt for that seed.\n"
        "cat > \"$TASK_DIR/diagnostic_session.py\" << 'EOF'\n"
        "import numpy as np\n"
        "obs = get_observation()\n"
        "masks = segment_sam3_text_prompt(obs[\"agentview\"][\"images\"][\"rgb\"], \"<prompt>\")\n"
        "print(f\"num_masks={len(masks)}\", flush=True)\n"
        "EOF\n"
        + trial_command("diagnostic", "<N>",
                        ' \\\n    --code "$TASK_DIR/diagnostic_session.py"')
        + "```\n",
        text, count=1, flags=re.S)
    return _rewrite_accounting(text, case, task_dir)


ACCOUNTING = """**Hard limit: 3 TOTAL retries per seed** — every simulator replay is one retry, so
this is not three extra repairs. Smoke, initial, repair and interactive diagnostic
sessions all spend from the same per-seed retry count; the one observation-only
scene snapshot does not. Failures, timeouts and crashes still spend their retry. There is no cap on how many times you edit
code, and no global revision budget: static reading of the API reference,
sources, traces and images is free and unlimited.

The recorded ledger holds the count. Ask it rather than guessing:

  .venv-libero/bin/python3 {protocol} status

When a seed has spent 3 retries, write BLOCKED.md for it and continue with another
seed. An interrupted run that really executed keeps its retry spent; report it as a blocker
instead of retrying it as if it never happened. A seed whose retries were spent
during smoke has no initial run left, and that fact is recorded — do not try to
force one.

Write BLOCKED.md:
  $TASK_DIR/attempts/seed_<N>_BLOCKED.md
  Format:
    ## Root Cause: [Physical|Perception|Algorithmic]
    ## Details: <what exactly fails and why>
    ## What Was Tried: <list of approaches>
"""

SELECTION = """### Step 4 — Synthesize task-level fix

After working the seeds, synthesize ONE generalizable fix (not seed-specific).
Use evidence across seeds; do not assume that one successful seed generalizes.

Save to TWO locations (create `outputs/working_codes` first if missing):
  $TASK_DIR/fix_code.py                            ← the frozen evaluation reads this exact path
  outputs/working_codes/${{SUITE}}_${{TASK}}_fix.py  ← named copy
{world_files}
**The selected bundle must itself have been tested.** Select on development
success evidence — most development successes, then fewer crashes, then simpler
observation-driven behavior — never merely the last version that did not crash.
If your synthesis differs from anything already executed, spend a remaining
retry on it; otherwise select a bundle that was already tested. Then record the
choice and its reason:

  .venv-libero/bin/python3 {protocol} select --reason "<why this bundle>"
  .venv-libero/bin/python3 {protocol} check

{fallback}"""

# The baseline paragraph, byte for byte as the original template rendered it.
FALLBACK_BASELINE = """`check` lists what is still missing. If every single program crashed, a minimal
legal program (one `get_observation()` call) is the documented fallback — label it
as such in findings.md.
"""

# Judgment selects a policy and its world as one identity, so an unexecuted
# minimal program is not available as a fallback: the pair must have run.
FALLBACK_JUDGMENT = """`check` lists what is still missing. This cell has no minimal-program fallback: a
policy and world that never executed together in development cannot be selected.
If every candidate pair failed, keep the tested pair whose development evidence is
strongest and say plainly in findings.md that none of them succeeded.
"""


def _rewrite_accounting(text: str, case: dict, task_dir: str) -> str:
    text = re.sub(
        r"\*\*Hard limit: 3 replay attempts per seed\.\*\*.*?"
        r"## What Was Tried: <list of approaches>\n",
        ACCOUNTING.format(protocol=PROTOCOL), text, count=1, flags=re.S)
    if case["condition"] not in WORLD_CONDITIONS:
        world_files = ""
    elif is_judgment(case):
        # No inventory in this profile; the pair is still one frozen identity.
        world_files = ("  $TASK_DIR/fix_world_program.py"
                       "                       ← frozen with the policy, part of its identity\n")
    else:
        world_files = ("  $TASK_DIR/fix_world_program.py, $TASK_DIR/fix_inventory.json"
                       "   ← frozen with the program, part of its identity\n")
    fallback = FALLBACK_JUDGMENT if is_judgment(case) else FALLBACK_BASELINE
    text = re.sub(r"### Step 4 — Synthesize task-level fix\n.*?"
                  r"as fix_code\.py so Stage 2 can still run\.\n",
                  SELECTION.format(protocol=PROTOCOL, world_files=world_files,
                                   fallback=fallback),
                  text, count=1, flags=re.S)
    text = text.replace("The coordinator reads ONLY this file to promote your discoveries into "
                        "the shared skill library",
                        "This is the reviewable record of what you learned")
    if case["condition"] == "C":
        # C has no high-level strategy library and no promotion channel.
        text = text.replace(
            "Follow `.claude/libero/fix-loop/skills/task-exploration.md`, then read the relevant "
            "shared skill library in `.claude/libero/skills/`.",
            "Follow `.claude/libero/fix-loop/skills/task-exploration.md`. Read the skill API "
            "reference at `.claude/libero/api-reference.md` and the API source itself.")
        text = text.replace(
            "- Do NOT edit anything under `.claude/libero/skills/` — only the coordinator updates "
            "shared skills. Your channel for reusable knowledge is `findings.md` (Stage 1, "
            "Step 5).",
            "- Your channel for reusable knowledge is `findings.md` (Stage 1, Step 5).")
        text = text.replace("   - Target skill file: localize.md | grasp.md | transport.md | "
                            "manipulation.md\n", "")
    else:
        text = text.replace(
            "read the relevant shared skill library in `.claude/libero/skills/`.",
            f"read the four strategy files in `{case['skill_library_dir']}` "
            "(localize.md, grasp.md, transport.md, manipulation.md), the skill API reference at "
            "`.claude/libero/api-reference.md`, and the API source itself.")
    if case["condition"] in WORLD_CONDITIONS:
        text = text.replace("\n---\n\n## Stage 1: Debug Seeds 51–65",
                            world_section(case) + "\n---\n\n## Stage 1: Debug Seeds 51–65")
    if is_sealed(case):
        text = seal_outcome_wording(text)
    return text


COORDINATOR = """You are the coordinator for one cell of a LIBERO-Pro fix-loop experiment.

Cell: {cell}   suite: {suite}   task: {task}

Delegate EXACTLY ONE fix-loop worker with the Agent tool, `subagent_type:
"general-purpose"`, `run_in_background: True`, passing the prompt in
`{prompt_path}` verbatim as the `prompt` parameter. Do not shorten it, do not
add task strategy of your own, and do not write or debug any robot code
yourself. You have no other worker to dispatch.

While it runs, wait for its completion notification. Do not poll its output file.

{promotion}

You never run held-out seeds 1-50, never read another cell's outputs, and never
open held-out artifacts. When the worker returns, report its summary verbatim,
then run:

  .venv-libero/bin/python3 {protocol} check

and report the result as it is. If the worker is blocked, say so plainly with the
blocker; do not describe an incomplete cell as finished.
"""

PROMOTION_AB = """After the worker returns you may promote its Stage 1 findings into THIS cell's
strategy files at `{skills}` and nowhere else. Nothing crosses into another cell.
"""

PROMOTION_C = """This cell has no high-level strategy library and no promotion step. Do not
create one, and do not write strategy documents of your own.
"""


def coordinator_prompt(case: dict, prompt_path: Path) -> str:
    promotion = (PROMOTION_C if case["condition"] == "C"
                 else PROMOTION_AB.format(skills=case["skill_library_dir"]))
    return COORDINATOR.format(cell=case["id"], suite=case["suite"], task=case["task"],
                              prompt_path=prompt_path, promotion=promotion.strip(),
                              protocol=PROTOCOL)


def is_local_vllm(case: dict) -> bool:
    """Opt-in only. An absent or different `model_provider` keeps the old path."""
    return case.get("model_provider") == LOCAL_VLLM_PROVIDER


def local_vllm_endpoint(case: dict) -> str:
    """Validate and normalize the loopback server root for the local branch."""
    endpoint = str(case.get("inference_endpoint", "")).rstrip("/")
    if not LOOPBACK_ROOT.match(endpoint):
        raise ProtocolFailure(
            f"local-vllm needs an http loopback server root with a port, "
            f"without /v1 or any path; got {case.get('inference_endpoint')!r}")
    return endpoint


def local_vllm_pilot_check(case: dict) -> None:
    """The pilot point is fixed; a drifted cell file must not run silently."""
    mismatched = {key: case.get(key) for key, expected in (
        ("model_tag", PILOT_MODEL), ("expected_served_model", PILOT_MODEL),
        ("effort", PILOT_EFFORT), ("context_tokens", PILOT_CONTEXT_TOKENS),
        ("max_output_tokens", PILOT_MAX_OUTPUT_TOKENS))
        if case.get(key) != expected}
    if mismatched:
        raise ProtocolFailure({"local_vllm_pilot_mismatch": mismatched})


def local_vllm_settings(case: dict) -> dict:
    """Settings for the unauthenticated loopback vLLM server.

    No apiKeyHelper: there is no credential to resolve, so the field is absent
    rather than empty. The placeholders are the launcher's verified ones; vLLM
    does not check them and nothing real is written here. Every model alias is
    pinned so main, background, subagent and fable lookups all resolve to the
    served model, and effort forwarding is explicit because the native client
    only sends it when told to.
    """
    local_vllm_pilot_check(case)
    model = case["model_tag"]
    env = {
        "ANTHROPIC_BASE_URL": local_vllm_endpoint(case),
        "ANTHROPIC_API_KEY": LOCAL_VLLM_PLACEHOLDER,
        "ANTHROPIC_AUTH_TOKEN": LOCAL_VLLM_PLACEHOLDER,
        "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1",
        "CLAUDE_CODE_EFFORT_LEVEL": case["effort"],
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(case["context_tokens"]),
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(case["max_output_tokens"]),
        # vLLM answers an over-length request with a generic HTTP 400, which the
        # auto path does not recognize, so the window stays explicit here too.
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(case["context_tokens"]),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "ENABLE_TOOL_SEARCH": "false",
        "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "43200000",
        "BASH_DEFAULT_TIMEOUT_MS": str((case["trial_timeout"] + 120) * 1000),
        "BASH_MAX_TIMEOUT_MS": str((case["trial_timeout"] + 300) * 1000),
    }
    env.update({alias: model for alias in LOCAL_VLLM_MODEL_ALIASES})
    if case.get("disable_experimental_betas"):
        # Only when this cell file sets it; it is the remote service's own value.
        env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = case["disable_experimental_betas"]
    return {"env": env, "model": model, "effortLevel": case["effort"],
            "includeCoAuthoredBy": False}


def native_settings(case: dict, repo: Path, case_path: Path) -> dict:
    """Per-cell native settings.

    The existing apiKeyHelper is reused by reference through these settings. Its
    output is never read, printed, copied, or placed in any child environment.
    The opt-in local-vllm branch has no credential at all and is built
    separately; both branches keep the same guard hooks.
    """
    if is_local_vllm(case):
        settings = local_vllm_settings(case)
        settings.update(guard_settings(case_path, repo))
        return settings
    model = case["model_tag"]
    settings = {
        "apiKeyHelper": case["api_key_helper"],
        "env": {
            "ANTHROPIC_BASE_URL": ENDPOINT,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": model,
            "ANTHROPIC_MODEL": model,
            "CLAUDE_CODE_SUBAGENT_MODEL": model,
            "CLAUDE_CODE_EFFORT_LEVEL": case["effort"],
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(case["context_tokens"]),
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(case["max_output_tokens"]),
            # Native compaction stays on with an explicit window, as in the
            # verified harness. No thinking or MCP output caps are invented here.
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(case["context_tokens"]),
            # Print mode otherwise reaps a still-running background worker after
            # ten idle minutes. Match the cell watchdog.
            "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "43200000",
            # Reused from the existing service's own configured value, recorded in
            # control/existing-service-settings.json. Not a new knob.
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": case["disable_experimental_betas"],
            "BASH_DEFAULT_TIMEOUT_MS": str((case["trial_timeout"] + 120) * 1000),
            "BASH_MAX_TIMEOUT_MS": str((case["trial_timeout"] + 300) * 1000),
        },
        "model": model,
        "effortLevel": case["effort"],
        "includeCoAuthoredBy": False,
    }
    settings.update(guard_settings(case_path, repo))
    return settings


def perception_ready(case: dict) -> dict:
    """Reachability only. Services are shared and already running; never restart."""
    import urllib.error
    import urllib.request
    status = {}
    for port in case["service_ports"]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                status[str(port)] = r.status
        except urllib.error.HTTPError as exc:
            status[str(port)] = exc.code  # 404 means the server answered.
        except OSError as exc:
            status[str(port)] = f"unreachable: {exc}"
    unreachable = [p for p, v in status.items() if isinstance(v, str)]
    if unreachable:
        raise ServiceFailure({"unreachable_perception_ports": unreachable, "status": status})
    return status


def native_environment(case: dict, repo: Path, case_path: Path, config_dir: Path) -> dict:
    """The solver's environment. No credential material is placed here.

    HOME is left alone. The protected apiKeyHelper and the existing runtime
    authentication both resolve against the real home; repurposing HOME would
    break authentication rather than isolate anything. Per-cell isolation comes
    from CLAUDE_CONFIG_DIR plus explicit --settings.
    """
    env = {k: v for k, v in os.environ.items()
           if not re.search(r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|"
                            r"HF_TOKEN|HUGGING_FACE_HUB_TOKEN|ANTHROPIC", k)}
    env.update(CLAUDE_CONFIG_DIR=str(config_dir),
               CLAUDE_CODE_AUTO_CONNECT_IDE="false", API_TIMEOUT_MS="300000",
               ASPIRE_NATIVE_CASE=str(case_path), ASPIRE_ROOT=str(repo),
               CUDA_VISIBLE_DEVICES=str(case["gpu"]), MUJOCO_GL="egl",
               TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1",
               PYTHONPATH=str(Path(case["python_root"])))
    if is_local_vllm(case):
        # The filter above already drops inherited ANTHROPIC_*, API keys and
        # OAuth tokens. These provider selectors do not match it and would
        # redirect the client away from the loopback server, so drop them too.
        for flag in INHERITED_PROVIDER_FLAGS:
            env.pop(flag, None)
    if case.get("egl_vendor_config"):
        env["__EGL_VENDOR_LIBRARY_FILENAMES"] = case["egl_vendor_config"]
    return env


def native_command(case: dict, prompt: str, settings: dict,
                   agents: dict | None = None) -> list[str]:
    """Direct native invocation, following the established harness recipe.

    Never claude_with_local_model.sh: that script is for the unauthenticated
    loopback vLLM only and would overwrite this cell's authentication.

    Permissions stay enforced. `dontAsk` with an explicit tool allowlist and the
    task guard hook is the reviewed mode; --dangerously-skip-permissions would
    disable the very hook that keeps the framework, ledger and other cells
    immutable. Settings are passed explicitly because --setting-sources ""
    suppresses file-based settings discovery.
    """
    command = [case["claude_bin"], "--model", case["model_tag"], "--effort", case["effort"],
               "-p", prompt, "--output-format", "stream-json", "--verbose",
               "--forward-subagent-text", "--setting-sources", "",
               "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-chrome",
               "--permission-mode", "dontAsk", "--tools", "default",
               "--allowedTools", *ALLOWED_TOOLS,
               "--settings", json.dumps(settings)]
    if agents:
        command.extend(["--agents", json.dumps(agents)])
    return command


def probe(case: dict, control: Path, env: dict, settings: dict) -> dict:
    """Tiny isolated compatibility probe with no task content. Not a trial, and
    not a capacity stress test: it records what the service actually served."""
    stdout = control / "probe.stdout.jsonl"
    # run_native_cc returns the exit code itself, not a tuple.
    code = run_native_cc(native_command(case, "Reply with the single word: ready", settings),
                         cwd=Path(case["sim"]), env=env, stdout_path=stdout,
                         stderr_path=control / "probe.stderr.log", timeout=900)
    served, contexts, outputs = set(), set(), set()
    for line in stdout.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "assistant" and record.get("message", {}).get("model"):
            served.add(record["message"]["model"])
        for entry in (record.get("modelUsage") or {}).values():
            if isinstance(entry, dict):
                contexts.add(entry.get("contextWindow"))
                outputs.add(entry.get("maxOutputTokens"))
    result = {"exit_code": code, "model_served": sorted(served),
              "context_windows": sorted(c for c in contexts if c),
              "max_output_tokens": sorted(o for o in outputs if o),
              "configured": {"model_tag": case["model_tag"], "effort": case["effort"],
                             "context_tokens": case["context_tokens"],
                             "max_output_tokens": case["max_output_tokens"]}}
    atomic_json(control / "probe.json", result)
    # For a custom model alias, Claude Code reports the alias's built-in default
    # in modelUsage.maxOutputTokens, even when the actual Messages request uses
    # CLAUDE_CODE_MAX_OUTPUT_TOKENS. Keep checking the served context and the
    # frozen request setting; only the remote-model path can compare modelUsage.
    output_matches = (settings.get("env", {}).get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
                      == str(case["max_output_tokens"])) if is_local_vllm(case) else (
                          outputs == {case["max_output_tokens"]})
    if code or served != {case["expected_served_model"]} or (
            (is_simple(case) or is_judgment(case))
            and (contexts != {case["context_tokens"]}
                 or not output_matches)):
        raise ServiceFailure({"native_probe_failed": result})
    return result


def write_prompts(case: dict, repo: Path, control: Path) -> tuple[Path, Path]:
    """Save the exact rendered prompt and its diff against the pristine template."""
    rendered = worker_prompt(case, repo)
    prompt_path = control / "worker-prompt.md"
    prompt_path.write_text(rendered)
    pristine = pristine_template(repo)
    (control / "worker-prompt.diff").write_text("".join(difflib.unified_diff(
        pristine.splitlines(keepends=True), rendered.splitlines(keepends=True),
        fromfile=".claude/libero/fix-loop/subagent-prompt.md (pristine template)",
        tofile=f"{case['id']} rendered worker prompt")))
    coordinator_path = control / "coordinator-prompt.md"
    coordinator_path.write_text(coordinator_prompt(case, prompt_path))
    return prompt_path, coordinator_path


def strategy_md_state(case: dict, repo: Path) -> dict:
    """Which strategy MD this cell exposes, recorded for the A/B/C comparison."""
    if case["condition"] == "C":
        # C sees no high-level strategy library at all.
        return {"present": False, "files": {}}
    directory = repo / case["skill_library_dir"]
    return {"present": True,
            "files": {name: {"sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(),
                             "bytes": (directory / name).stat().st_size}
                      for name in STRATEGY_MD}}


def protocol(case: dict, repo: Path, env: dict, control: Path, name: str,
             *args: str) -> dict:
    """Run one deterministic outer protocol step and record its raw output.

    These steps belong to the outer driver, not the solver: the ledger is
    initialized before the solver starts, and check / finalize / heldout run
    after it exits.
    """
    command = [str(repo / ".venv-libero/bin/python3"), PROTOCOL, *args]
    if name == "heldout":
        command = [str(repo / ".venv-libero/bin/python3"),
                   "scripts/libero/native_world_heldout.py", *args]
    proc = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = None
    step = {"step": name, "command": command, "exit_code": proc.returncode,
            "result": payload, "stderr_tail": proc.stderr[-4000:]}
    atomic_json(control / f"outer_{name}.json", step)
    return step


def run(case_path: Path) -> int:
    case = json.loads(case_path.read_text())
    repo = Path(case["sim"]).resolve()
    control = Path(case["control"])
    control.mkdir(parents=True, exist_ok=True)
    config_dir = Path(case["claude_config_dir"])
    config_dir.mkdir(parents=True, exist_ok=True)
    state = control / "campaign_state.json"
    verify_runtime(case, repo)
    atomic_json(control / "runtime_manifest_verified.json",
                {"verified_at": datetime.now(timezone.utc).isoformat(), "cell": case["id"]})
    atomic_json(config_dir / "settings.json", native_settings(case, repo, case_path))
    atomic_json(control / "strategy_md.json", strategy_md_state(case, repo))
    env = native_environment(case, repo, case_path, config_dir)
    started = datetime.now(timezone.utc).isoformat()
    record = {"cell": case["id"], "condition": case["condition"], "started_at": started,
              "status": "running", "blocker": None}
    atomic_json(state, record)
    settings = native_settings(case, repo, case_path)
    try:
        record["perception"] = perception_ready(case)
        record["probe"] = probe(case, control, env, settings)
        prompt_path, coordinator_path = write_prompts(case, repo, control)
        record["prompts"] = {"worker": str(prompt_path), "coordinator": str(coordinator_path)}
        # The ledger exists before the solver runs, so no solver turn can create
        # or reset it.
        record["init"] = protocol(case, repo, env, control, "init", "init")
        if record["init"]["exit_code"]:
            raise ProtocolFailure(f"protocol init failed: {record['init']['stderr_tail']}")
        atomic_json(state, record)
        transcript = control / "coordinator.stdout.jsonl"
        code = run_native_cc(
            native_command(case, coordinator_path.read_text(), settings,
                           agents=worker_agent(case, prompt_path)),
            cwd=repo, env=env, stdout_path=transcript,
            stderr_path=control / "coordinator.stderr.log",
            timeout=case["campaign_timeout"])
        record["coordinator_exit_code"] = code
        verify_runtime(case, repo)
        record["status"] = "solver_finished" if code == 0 else "solver_failed"
        if code:
            record["blocker"] = f"native coordinator exited {code}"
            atomic_json(state, record)
            raise ProtocolFailure(record["blocker"])
        atomic_json(state, record)
        record["check"] = protocol(case, repo, env, control, "check", "check")
        if record["check"]["exit_code"]:
            record.update(status="stage1_incomplete",
                          blocker="stage 1 is not ready; resume the same worker")
            raise ProtocolFailure(record["blocker"])
        # Finalize with the actual transcript path, then hand off to held-out.
        record["finalize"] = protocol(case, repo, env, control, "finalize",
                                      "finalize", "--transcript", str(transcript))
        if record["finalize"]["exit_code"]:
            record.update(status="finalize_failed",
                          blocker=f"finalize failed: {record['finalize']['stderr_tail']}")
            raise ProtocolFailure(record["blocker"])
        record["status"] = "stage1_finalized"
        atomic_json(state, record)
        record["heldout"] = protocol(case, repo, env, control, "heldout",
                                     "--case", str(case_path))
        if record["heldout"]["exit_code"]:
            record.update(status="heldout_incomplete",
                          blocker=f"held-out evaluation incomplete: "
                                  f"{record['heldout']['stderr_tail']}")
            raise ProtocolFailure(record["blocker"])
        record["status"] = "complete"
    except ProtocolFailure:
        pass  # Already recorded above with its own status and blocker.
    except (ServiceFailure, OSError, ValueError) as exc:
        # A cell blocker is recorded and reported; it is never a completion.
        record.update(status="blocked", blocker=f"{type(exc).__name__}: {exc}")
    record["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(state, record)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    # Only the full sequence — init, solver, check, finalize, held-out — counts.
    return 0 if record["status"] == "complete" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--render-only", action="store_true",
                        help="write the prompts and diff, run nothing")
    args = parser.parse_args()
    if args.render_only:
        case = json.loads(args.case.read_text())
        control = Path(case["control"])
        control.mkdir(parents=True, exist_ok=True)
        prompt, coordinator = write_prompts(case, Path(case["sim"]).resolve(), control)
        print(json.dumps({"worker_prompt": str(prompt), "coordinator": str(coordinator),
                          "diff": str(control / "worker-prompt.diff")}, indent=2))
        return 0
    return run(args.case)


if __name__ == "__main__":
    raise SystemExit(main())



