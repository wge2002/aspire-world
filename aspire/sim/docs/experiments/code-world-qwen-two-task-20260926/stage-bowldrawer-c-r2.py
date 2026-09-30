#!/usr/bin/env python3
"""Stage cell `bowldrawer_C_r2` fresh, then charge the surviving seed-51 diagnostic.

Why a second cell instead of amending `bowldrawer_C`:

The frozen r1 package asserts `imported_charged_attempts == {}` in three places
that all have to agree — the case, `two_task_scope.remaining_budget` (which
*raises* on any declared import), and `staged-inputs.json`'s
`no_prior_inputs.imported_diagnostic = None`. Two real seed-51 DSW preflight
attempts were executed before the solver started, so leaving r1 alone and
running it would make all three a stale claim. r1 is therefore preserved
byte-for-byte and this script stages an independent cell that declares both
charged attempts.

Why this script and not a copy of r1:

`native_cc_freeze.verify_runtime` recomputes `source_inventory(repo)` and
`dependency_bindings(repo)` from the staged tree on every protocol action and
compares them to the bound manifest. A manifest cannot be produced by rewriting
paths in r1's manifest text; it has to be built from the r2 tree. So r2 goes
through the same frozen staging path r1 did — `prepare-two-task.stage_cell`,
`legacy_stager.bind_manifest` — with exactly two deviations:

  1. `case["imported_charged_attempts"] = {"51": 2}`, and the matching honest
     `no_prior_inputs.imported_diagnostic` record.  Two runs of seed 51 were
     executed: Run 1 (sandbox_rc=1, KeyError rgb) evidence deleted; Run 2
     (sandbox_rc=0, all API calls passed) evidence preserved.
     `two_task_scope_r2.remaining_budget` returns 51 → 1 for this case.
  2. prompts are rendered through `support/two_task_render_r2.py`, which gates
     with `two_task_scope_r2` (identical except that `remaining_budget`
     subtracts a declared import rather than refusing it).

Two charged rows, not one:

The case's declared import and the ledger's own row count are independent views
of the same budget, and both gate execution — `two_task_scope_r2` gates the
solver, `NativeWorldState.begin_trial` gates the simulator. Importing only Run
2's surviving evidence would leave the ledger believing seed 51 had spent one
attempt while the case declares two, so the ledger would admit three further
executions for five real trials against a limit of three. `two_task_import_c`
therefore writes Run 1 as a charged **tombstone** at attempt 1 (status
`diagnostic_program_error`, sandbox_rc=1, evidence explicitly LOST, bundle
digest derived from a recorded sentence rather than any artifact) and imports
Run 2 as attempt 2. This script then requires the two views to agree and proves,
on a throwaway copy of the ledger, that a fourth execution is refused.

Condition C cells carry no A-oracle check; there is no pristine-A oracle for C.
The C worker prompt is verified against `two_task_scope_r2.verify_worker`'s
C-specific invariants (REQUIRED_C_WORKER, interface doc presence, etc.).

Runs no model. Runs no simulator: both charged attempts were already executed on
DSW. Neither is re-run, and neither is refunded.

NOT launch-complete. A C r2 run needs a `run_two_task_cell_c_r2.py` that
imports `two_task_import_c` instead of `two_task_import`. This script stops
after the import and says so in the receipt.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path("/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim")
STUDY_DIR = REPO / "docs/experiments/code-world-qwen-two-task-20260926"
SUPPORT_SRC = STUDY_DIR / "support"

CELL = "bowldrawer_C_r2"
CONDITION = "C"
IMPORT_SEED = "51"
IMPORTED_ATTEMPTS = 2   # Run 1 (deleted, failed) + Run 2 (surviving, passed)

PREFLIGHT = Path("/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926"
                 "/coordination/dsw-preflight-bowldrawer-c")

#: Support modules this cell adds on top of the r1 frozen set. They are pinned
#: by sha256 in the receipt rather than added to `base.SUPPORT_FILES`, so r2's
#: manifest keeps the same external_files shape as r1's.
R2_SUPPORT = ("two_task_scope_r2.py", "two_task_render_r2.py",
              "two_task_import_c.py")


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def render_r2(case: dict, base) -> dict:
    """prepare-two-task.render_prompts, pointed at the r2 gate."""
    sim = base.cell_sim(case["id"])
    renderer = base.PARENT / "support/two_task_render_r2.py"
    if not renderer.is_file():
        raise SystemExit(f"frozen r2 renderer missing: {renderer}")
    command = [str(sim / ".venv-libero/bin/python3"), str(renderer),
               "--case", str(Path(case["control"]) / "case.json")]
    proc = subprocess.run(command, cwd=sim, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: gated r2 prompt rendering failed:\n"
                         f"{proc.stderr[-4000:]}")
    render = json.loads(proc.stdout)
    render["command"] = command
    return render


def main() -> int:
    prep = load("prepare_two_task", STUDY_DIR / "prepare-two-task.py")
    base = prep.base
    scope_r2 = load("two_task_scope_r2", SUPPORT_SRC / "two_task_scope_r2.py")
    importer = load("two_task_import_c", SUPPORT_SRC / "two_task_import_c.py")

    print(f"=== stage {CELL} ({CONDITION}) + charge the seed-{IMPORT_SEED} diagnostic ===")
    print(f"    imported_charged_attempts: {{\"{IMPORT_SEED}\": {IMPORTED_ATTEMPTS}}} "
          f"(Run 1 deleted+failed; Run 2 surviving+passed; both charged)")

    # 0. The import source must match its pinned evidence before anything is
    #    staged. Cheapest check, and the one that invalidates the whole run.
    print("  [0] verifying the preflight source pins (Run 2 evidence)...")
    importer.verify_tree(PREFLIGHT)
    importer.read_summary(PREFLIGHT)
    print(f"      {len(importer.PINNED_HASHES)} hashes verified;"
          f" digest {importer.PINNED_DIGEST[:16]}...")

    # 1. r1 and the shared frozen roots must already exist; r2's own roots must
    #    not. `stage_cell` also mkdirs without exist_ok, so this is belt and
    #    braces, but it fails before any write instead of halfway through.
    control = base.PARENT / CELL
    runtime = base.cell_root(CELL)
    for path in (base.PARENT, base.RUNTIME, base.BASELINE_CONTRACT):
        if not path.exists():
            raise SystemExit(f"r1 staging is missing; stage it first: {path}")
    prep.refuse_existing(control, runtime)
    print(f"  [1] fresh r2 roots:\n      control {control}\n      runtime {runtime}")

    # 2. Freeze the r2 gate modules next to r1's frozen support.
    print("  [2] freezing r2 support modules...")
    r2_support_sha = {}
    for name in R2_SUPPORT:
        r2_support_sha[name] = base.freeze_bytes(SUPPORT_SRC / name,
                                                 base.PARENT / "support" / name)
        print(f"      {name}  {r2_support_sha[name][:16]}...")

    # 3. Stage the tree through the frozen path.
    print("  [3] staging the runtime tree...")
    contract = json.loads(base.BASELINE_CONTRACT.read_text())
    case, differences = prep.stage_cell(CELL, CONDITION, contract)

    # 4. The two deviations from r1, declared rather than inferred.
    case["imported_charged_attempts"] = {IMPORT_SEED: IMPORTED_ATTEMPTS}
    case["generation_context"] = (
        f"native Qwen {CONDITION} fresh generation, {case['task']}; "
        f"two charged DSW infrastructure attempts imported for seed {IMPORT_SEED} "
        f"(Run 1 deleted+failed, Run 2 surviving+passed); "
        "dev51-65 then frozen heldout1-50")
    differences["no_prior_inputs"] = {
        "c_starter": None,
        "imported_diagnostic": {
            "source": str(PREFLIGHT),
            "source_digest": importer.PINNED_DIGEST,
            "phase": importer.IMPORT_PHASE,
            "seed": int(IMPORT_SEED),
            "charged_attempts": IMPORTED_ATTEMPTS,
            "task_policy_executed": False,
            "note": (
                "Two infrastructure-only observation/SAM3/GraspNet/IK runs "
                "executed on DSW before the solver started. "
                "Run 1 (2026-09-27, sandbox_rc=1, KeyError rgb in synthetic policy): "
                "output directory deleted before Run 2; evidence permanently lost. "
                "Charged, and carried in the ledger as an explicit tombstone at "
                "attempt 1 — no artifact hash is claimed for it. "
                "Run 2 (2026-09-27, sandbox_rc=0, all API calls passed): "
                "evidence preserved; imported by hash at attempt 2. Charged. "
                "Both excluded from tested bundles and from task success, "
                "and not refundable."
            ),
        },
        "imported_tape": None,
        "note": (
            "fresh generation for the task program; the only prior inputs are the "
            "two charged infrastructure runs recorded above"
        ),
    }

    # 5. Budget must render 51 -> 1 before the tree is bound.
    #    (ATTEMPT_LIMIT=3, imported=2 → remaining=1)
    budget = scope_r2.remaining_budget(case)
    if budget[IMPORT_SEED] != 1 or any(budget[str(s)] != 3 for s in range(52, 66)):
        raise SystemExit(f"r2 budget is not 51->1 / 52-65->3: {budget}")
    print(f"  [4] declared budget: 51->{budget[IMPORT_SEED]}, 52-65->3")

    # 6. Build and bind the manifest from the r2 tree, and write case.json once.
    print("  [5] building the r2 runtime manifest...")
    case = base.bind_manifest(case)
    case_path = Path(case["control"]) / "case.json"
    print(f"      manifest {case['runtime_manifest_sha256'][:16]}...")
    print(f"      case     {base.digest(case_path)[:16]}...")

    # 7. Render through the r2 gate, then re-check the rendered bytes.
    #    Condition C has no A-oracle; verify against C's own r2 scope invariants.
    print("  [6] rendering prompts through the r2 gate...")
    render = render_r2(case, base)
    oracles: dict = {}
    prompts = prep.verify_prompts(case, oracles)
    worker = (Path(case["control"]) / "worker-prompt.md").read_text()
    scope_r2.verify_worker(worker, case, base.cell_sim(CELL))
    scope_r2.verify_coordinator(
        (Path(case["control"]) / "coordinator-prompt.md").read_text(), case)
    print(f"      worker      {prompts['worker-prompt.md'][:16]}...")
    print(f"      coordinator {prompts['coordinator-prompt.md'][:16]}...")
    print("      condition C scope invariants verified (no A-oracle for C)")

    # 8. Record what was staged, before the ledger exists.
    base.record(Path(case["control"]) / "staged-inputs.json", {
        "cell": CELL, "condition": CONDITION, "profile": base.PROFILES[CONDITION],
        "task": case["task"], "staged_at": base.now(), "base_commit": base.BASE_COMMIT,
        "source_differences": differences,
        "r2_support_sha256": r2_support_sha,
        "external_inputs": [str(p) for p in prep.external_inputs(case)],
        "derived_from": {"cell": "bowldrawer_C", "relation": "independent restage",
                         "r1_modified": False}})

    # 9. Initialize the ledger. `native_world_protocol.py init` owns the task
    #    directory and refuses one that already holds artifacts, so it is not
    #    pre-created here.
    sim = base.cell_sim(CELL)
    task_dir = sim / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    ledger = task_dir / "development_state.json"
    if ledger.is_file():
        print("  [7] ledger already exists; skipping init (resume path)")
    else:
        print("  [7] protocol init...")
        proc = subprocess.run(
            [str(sim / ".venv-libero/bin/python3"),
             "scripts/libero/native_world_protocol.py",
             "--case", str(case_path), "init"],
            cwd=sim, capture_output=True, text=True)
        if proc.returncode:
            raise SystemExit(f"protocol init failed (rc={proc.returncode}):\n"
                             f"{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
        print(f"      {ledger}")

    # 10. Charge both attempts. Idempotent by (phase, seed, bundle digest).
    #     `import_diagnostic` writes the Run 1 tombstone first (attempt 1) and
    #     then imports Run 2's surviving evidence (attempt 2), so the ledger's
    #     own accounting matches the case's declared import instead of quietly
    #     leaving three more executions available on a seed that has spent two.
    #     `ASPIRE_ROOT` makes the importer resolve the ledger module from the
    #     staged tree rather than from the engineering checkout it was loaded from.
    print("  [8] charging both seed-51 attempts (Run 1 tombstone + Run 2 import)...")
    os.environ["ASPIRE_ROOT"] = str(sim)
    state = importer.open_state(task_dir)
    record = importer.import_diagnostic(state, PREFLIGHT)
    tomb = importer.tombstone_rows(state)[0]
    print(f"      attempt 1  phase={tomb['phase']} status={tomb['status']} "
          f"charged={tomb['charged']} sandbox_rc={tomb['sandbox_rc']} "
          f"evidence={tomb['lost_evidence']['evidence']}")
    print(f"      attempt 2  phase={record['phase']} status={record['status']} "
          f"charged={record['charged']} task_completed={record['task_completed']}")

    # 11. Verify against the ledger's own accounting, not the case's claim, and
    #     then require the two to agree. A ledger that counts one charged row
    #     while the case declares two would admit a fourth real execution on a
    #     three-attempt seed, so the agreement is the check that matters.
    print("  [9] verifying the ledger...")
    check = importer.open_state(task_dir)
    charged = [r for r in check.data["trials"] if r.get("charged")]
    imported = importer.imported_rows(check)
    tombstones = importer.tombstone_rows(check)
    if len(charged) != IMPORTED_ATTEMPTS or len(imported) != 1 or len(tombstones) != 1:
        raise SystemExit(
            f"expected {IMPORTED_ATTEMPTS} charged rows (1 tombstone + 1 import); "
            f"charged={len(charged)} imported={len(imported)} "
            f"tombstones={len(tombstones)}")
    problems = importer.identity_errors(imported[0], record["bundle_sha256"])
    if problems:
        raise SystemExit("imported row is not the charged diagnostic: "
                         + "; ".join(problems))
    # The tombstone's digest is recomputed from TOMBSTONE_BUNDLE, not read back
    # off the row it is supposed to be checking.
    tomb_digest = importer.load_state_module().bundle_identity(
        importer.TOMBSTONE_BUNDLE)
    problems = importer.tombstone_errors(tombstones[0], tomb_digest)
    if problems:
        raise SystemExit("tombstone row is not the charged lost attempt: "
                         + "; ".join(problems))
    if tombstones[0]["status"] == "complete":
        raise SystemExit("the Run 1 tombstone reads as a completed run")

    accounting = importer.verify_accounting(check)
    ledger_budget = {str(s): check.budget_remaining(s) for s in check.seeds}
    case_budget = scope_r2.remaining_budget(case)
    expected = {IMPORT_SEED: 1, **{str(s): 3 for s in range(52, 66)}}
    if ledger_budget != expected:
        raise SystemExit(f"ledger budget is not 51->1 / 52-65->3: {ledger_budget}")
    if case_budget != expected:
        raise SystemExit(f"case budget is not 51->1 / 52-65->3: {case_budget}")
    if ledger_budget != case_budget:
        raise SystemExit(
            f"the ledger and the case disagree about remaining budget: "
            f"ledger={ledger_budget} case={case_budget}")
    if check.records("diagnostic") and check.candidates():
        raise SystemExit("the diagnostic leaked into the tested bundles")
    print(f"      {len(charged)} charged rows (attempt 1 tombstone, attempt 2 import); "
          f"attempts_used(51)={accounting['attempts_used']}; "
          f"ledger budget 51->{ledger_budget[IMPORT_SEED]} == "
          f"case budget 51->{case_budget[IMPORT_SEED]}; "
          "no diagnostic in tested bundles")

    # 12. Prove the gate, not just the arithmetic: `begin_trial` must admit the
    #     third attempt and refuse a fourth. Runs on a throwaway copy of
    #     development_state.json; the real ledger is not touched.
    print("  [10] proving the attempt limit on a throwaway ledger copy...")
    limit = importer.prove_attempt_limit(task_dir)
    print(f"       3rd admitted (attempt {limit['third_attempt_admitted']['attempt']}, "
          f"budget after = {limit['third_attempt_admitted']['budget_after']}); "
          f"4th refused: {limit['refusal_message']}")
    post = importer.open_state(task_dir)
    if len([r for r in post.data["trials"] if r.get("charged")]) != IMPORTED_ATTEMPTS:
        raise SystemExit("the attempt-limit probe modified the real ledger")

    # 13. Re-import must be a no-op.
    print("  [11] idempotence...")
    again = importer.open_state(task_dir)
    importer.import_diagnostic(again, PREFLIGHT)
    after = importer.open_state(task_dir)
    if len([r for r in after.data["trials"] if r.get("charged")]) != IMPORTED_ATTEMPTS:
        raise SystemExit("re-import changed the charged row count")
    importer.verify_accounting(after)
    print("      re-import is a no-op")

    receipt = {
        "staged_at": base.now(),
        "cell": CELL, "condition": CONDITION, "profile": base.PROFILES[CONDITION],
        "task": case["task"], "suite": case["suite"],
        "control": str(control), "runtime": str(sim),
        "case": str(case_path), "case_sha256": base.digest(case_path),
        "runtime_manifest": case["runtime_manifest"],
        "runtime_manifest_sha256": case["runtime_manifest_sha256"],
        "prompt_sha256": prompts,
        "render": render,
        "r2_support_sha256": r2_support_sha,
        "imported_charged_attempts": case["imported_charged_attempts"],
        "imported_attempts_detail": {
            "total": IMPORTED_ATTEMPTS,
            "run1": {
                "attempt": 1,
                "outcome": "sandbox_rc=1, KeyError: 'rgb' before the SAM3 call",
                "evidence": "LOST — output directory deleted before Run 2",
                "ledger_row": "charged tombstone, status=diagnostic_program_error",
                "bundle_preimage": importer.TOMBSTONE_PREIMAGE,
                "process_exit_code": None,
                "charged": True, "refundable": False,
            },
            "run2": {
                "attempt": 2,
                "outcome": "sandbox_rc=0, all four API calls passed",
                "evidence": f"preserved and pinned; digest {importer.PINNED_DIGEST}",
                "ledger_row": "charged import, status=complete, task_completed=0",
                "charged": True, "refundable": False,
            },
        },
        "ledger": str(ledger),
        "ledger_accounting": accounting,
        "attempt_limit_proof": limit,
        "ledger_remaining_budget": ledger_budget,
        "case_remaining_budget": case_budget,
        "budgets_agree": ledger_budget == case_budget,
        "imported_row": {k: imported[0].get(k) for k in
                         ("phase", "seed", "attempt", "status", "charged", "executed",
                          "task_completed", "bundle_sha256", "directory")},
        "tombstone_row": {k: tombstones[0].get(k) for k in
                          ("phase", "seed", "attempt", "status", "charged", "executed",
                           "sandbox_rc", "task_completed", "bundle_sha256",
                           "process_exit_code", "directory")},
        "import_source": str(PREFLIGHT),
        "import_source_digest": importer.PINNED_DIGEST,
        "r1_preserved": True,
        "launched": False,
        "launch_blocker": (
            "support/run_two_task_cell_r2.py hardcodes `import two_task_import`; "
            "a C r2 run needs `run_two_task_cell_c_r2.py` which imports "
            "`two_task_import_c` instead. Not staged by this script."
        ),
        "note": (
            "no model and no simulator ran here. Two real charged attempts were "
            "executed on seed 51 on DSW beforehand: Run 2's evidence survives and "
            "is imported by hash as attempt 2; Run 1's evidence was deleted before "
            "Run 2 ran and is charged as a tombstone at attempt 1, with its "
            "bundle digest derived from a recorded sentence rather than from any "
            "artifact. The ledger independently reports attempts_used(51)=2 and "
            "budget_remaining(51)=1, matching imported_charged_attempts, and "
            "begin_trial refuses a fourth execution. No held-out seed was read. "
            "r1 is untouched."
        ),
    }
    receipt_path = base.PARENT / f"prepare-receipt-{CELL}.json"
    base.record(receipt_path, receipt)
    print(f"\n  receipt: {receipt_path}")
    print(json.dumps({k: receipt[k] for k in
                      ("cell", "case", "case_sha256", "runtime_manifest_sha256",
                       "imported_charged_attempts", "ledger_remaining_budget",
                       "case_remaining_budget", "budgets_agree",
                       "attempt_limit_proof", "r1_preserved",
                       "launched", "launch_blocker")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
