"""Small prompt/config distinction for the ordinary code-world comparison."""
from pathlib import Path


def is_simple(case):
    return case.get("profile") == "simple"


def worker_prompt(case, repo: Path):
    task = f"outputs/libero_fix_loop/{case['suite']}/{case['task']}"
    protocol = ".venv-libero/bin/python3 scripts/libero/native_world_protocol.py"
    world = case["condition"] == "C"
    reading = ("Read the factual API reference `.claude/libero/api-reference.md` and "
               "the allowed API source for exact signatures.")
    if not world:
        reading += (f" Read the four strategy files in `{case['skill_library_dir']}`: "
                    "localize.md, grasp.md, transport.md, manipulation.md.")
    interface = ""
    extra = ""
    companion = ""
    if world:
        interface = """
Write an ordinary Python world module alongside the policy. It is loaded as
`world` once per episode; policy uses `import world`. World maintains state,
interprets policy-supplied public observations, and answers policy queries.
Policy owns sensing/motion and uses world answers to decide what to do.

Required module functions: `update(obs, last_action)` and a pure `snapshot()`
returning a JSON-serializable dict for logging. You choose observation/action
shapes, internal representation, predictions and additional query functions.
World receives data, never env/API handles; it does not itself sense or act.
There is no fixed grasp vocabulary, object inventory or judgment threshold.

Save initial_world_program.py with initial_code.py, and fix_world_program.py
with fix_code.py. These pairs are versioned, tested and selected together.
Trace `simple_world/events.jsonl` aligns public API calls and world snapshots,
including snapshots after update. Inspect it with trace.json, keyframes and
summary.txt during repair. Revise world and policy where the evidence calls for
it. The framework provides no semantic verdict or task-specific world code.
"""
        extra = ' --world-program "$TASK_DIR/initial_world_program.py"'
        companion = " and its matching fix_world_program.py"
    return f"""You are the single native Fix Loop worker for {case['id']}.
Suite: {case['suite']}
Task: {case['task']}
Working directory: {repo}
TASK_DIR={task}
GPU {case['gpu']} and services are prepared; authorization and preflight are complete.
{reading}
Use only public robot APIs and ordinary Python/numpy/scipy. Do not unwrap the
simulator, query ground-truth geometry/predicates, inspect asset files, change
framework/configuration, access credentials, other cells or earlier experiments.
`env.handle.task_language` is allowed for the actual instruction. Source
exploration is for public API contracts, not simulator internals or other solvers.
{interface}
1. Capture the one observation-only scene snapshot:
   {protocol} trial --phase snapshot --seed 51
   Read scene_snapshot.jpg and scene_snapshot_wrist.jpg in TASK_DIR and the
   task language. Write a short task_analysis.md, then initial_code.py{(' and initial_world_program.py' if world else '')}.

2. Run that initial bundle on all development seeds 51–65:
   {protocol} trial --phase initial --seed <N> --code "$TASK_DIR/initial_code.py"{extra}
   Set TASK_DIR in each shell call, or use the literal path above. Run one
   recorded trial per Bash call and await completion. No direct replay launcher.
   An optional --phase smoke on seed51 counts toward its budget; if identical
   to the initial bundle, `alias-smoke --seed 51` reuses that evidence.

3. After the initial batch, diagnose failures using each trial's logs, code and
   public observations. Improve a general task program and test failed seeds
   with --phase repair and --code pointing to the revised file{(' plus --world-program for its matching world' if world else '')}.
   The ledger permits THREE TOTAL simulator attempts per seed, including smoke,
   initial, repair and diagnostics. There are no extra action/query/recovery
   caps; the existing 900s/4000-step watchdogs apply. `status` reports remaining
   attempts. Keep any successful behavior while repairing other seeds.
   If needed, --phase diagnostic --code <diagnostic_session.py> runs the public
   API REPL once, charges one attempt, and provides inspection rather than a score.
   For an exhausted failing seed, write attempts/seed_<N>_BLOCKED.md with
   Root Cause, Details, What Was Tried. Never reset the ledger or rerun artifacts.

4. Select a general bundle with development success evidence. If none succeeded,
   use fewer crashes and simpler observation-driven behavior. The final choice
   MUST itself have executed in development; reserve a remaining attempt to
   test any new synthesis, or restore a tested version. Save fix_code.py{companion}
   in TASK_DIR, and an identical policy copy at
   outputs/working_codes/{case['suite']}_{case['task']}_fix.py.
   {protocol} select --reason "<development evidence for this choice>"

5. Write findings.md with headings Root causes observed, What fixed them,
   Generalizable patterns, Blocked seeds, citing actual development evidence.
   {protocol} check
   Complete missing protocol work while budgets permit, then report the result
   and selected files. Never run/read held-out seeds1–50: the outer evaluator
   freezes the selected bundle and evaluates it after you finish.
"""
