#!/usr/bin/env python3
"""Stage cell `bowldrawer_A_r2` fresh, then charge the one real seed-51 diagnostic.

Why a second cell instead of amending `bowldrawer_A`:

The frozen r1 package asserts `imported_charged_attempts == {}` in three places
that all have to agree — the case, `two_task_scope.remaining_budget` (which
*raises* on any declared import), and `staged-inputs.json`'s
`no_prior_inputs.imported_diagnostic = None`. The seed-51 DSW preflight is a
real charged simulator attempt, so leaving r1 alone and running it would make
all three a stale claim. r1 is therefore preserved byte-for-byte and this script
stages an independent cell that declares the import.

Why this script and not a copy of r1:

`native_cc_freeze.verify_runtime` recomputes `source_inventory(repo)` and
`dependency_bindings(repo)` from the staged tree on every protocol action and
compares them to the bound manifest. A manifest cannot be produced by rewriting
paths in r1's manifest text; it has to be built from the r2 tree. So r2 goes
through the same frozen staging path r1 did — `prepare-two-task.stage_cell`,
`legacy_stager.bind_manifest` — with exactly two deviations:

  1. `case["imported_charged_attempts"] = {"51": 1}`, and the matching honest
     `no_prior_inputs.imported_diagnostic` record.
  2. prompts are rendered through `support/two_task_render_r2.py`, which gates
     with `two_task_scope_r2` (identical except that `remaining_budget`
     subtracts a declared import rather than refusing it).

Condition A's rendered prompts are still required to match the independent
pristine Sep-14 oracle byte for byte.

Runs no model. Runs no simulator: the one charged attempt was already executed
on DSW and is imported by hash, not re-run.

NOT launch-complete. `support/run_two_task_cell.py` hardcodes
`import two_task_scope as scope`; an r2 run needs the same one-line
substitution in a `run_two_task_cell_r2.py`. This script stops after the
import and says so in the receipt.
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

CELL = "bowldrawer_A_r2"
CONDITION = "A"
IMPORT_SEED = "51"

PREFLIGHT = Path("/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926"
                 "/coordination/dsw-preflight-bowldrawer-a")

#: Support modules this cell adds on top of the r1 frozen set. They are pinned
#: by sha256 in the receipt rather than added to `base.SUPPORT_FILES`, so r2's
#: manifest keeps the same external_files shape as r1's.
R2_SUPPORT = ("two_task_scope_r2.py", "two_task_render_r2.py")


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
    importer = load("two_task_import", SUPPORT_SRC / "two_task_import.py")

    print(f"=== stage {CELL} ({CONDITION}) + charge the seed-{IMPORT_SEED} diagnostic ===")

    # 0. The import source must match its pinned evidence before anything is
    #    staged. Cheapest check, and the one that invalidates the whole run.
    print("  [0] verifying the preflight source pins...")
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

    # 2. Freeze the two r2 gate modules next to r1's frozen support.
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
    case["imported_charged_attempts"] = {IMPORT_SEED: 1}
    case["generation_context"] = (
        f"native Qwen {CONDITION} fresh generation, {case['task']}; "
        f"one charged DSW infrastructure diagnostic imported for seed {IMPORT_SEED}; "
        "dev51-65 then frozen heldout1-50")
    differences["no_prior_inputs"] = {
        "c_starter": None,
        "imported_diagnostic": {
            "source": str(PREFLIGHT),
            "source_digest": importer.PINNED_DIGEST,
            "phase": importer.IMPORT_PHASE,
            "seed": int(IMPORT_SEED),
            "charged_attempts": 1,
            "task_policy_executed": False,
            "note": "infrastructure-only observation/SAM3/GraspNet/IK executed on "
                    "DSW before the solver started; excluded from tested bundles "
                    "and from task success, and not refundable",
        },
        "imported_tape": None,
        "note": "fresh generation for the task program; the only prior input is the "
                "one charged infrastructure diagnostic recorded above",
    }

    # 5. Budget must render 51 -> 2 before the tree is bound.
    budget = scope_r2.remaining_budget(case)
    if budget[IMPORT_SEED] != 2 or any(budget[str(s)] != 3 for s in range(52, 66)):
        raise SystemExit(f"r2 budget is not 51->2 / 52-65->3: {budget}")
    print(f"  [4] declared budget: 51->{budget[IMPORT_SEED]}, 52-65->3")

    # 6. Build and bind the manifest from the r2 tree, and write case.json once.
    print("  [5] building the r2 runtime manifest...")
    case = base.bind_manifest(case)
    case_path = Path(case["control"]) / "case.json"
    print(f"      manifest {case['runtime_manifest_sha256'][:16]}...")
    print(f"      case     {base.digest(case_path)[:16]}...")

    # 7. Render through the r2 gate, then re-check the rendered bytes and hold
    #    condition A to the independent pristine oracle.
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
    print("      condition A matches the pristine Sep-14 oracle byte for byte")

    # 8. Record what was staged, before the ledger exists.
    base.record(Path(case["control"]) / "staged-inputs.json", {
        "cell": CELL, "condition": CONDITION, "profile": base.PROFILES[CONDITION],
        "task": case["task"], "staged_at": base.now(), "base_commit": base.BASE_COMMIT,
        "source_differences": differences,
        "r2_support_sha256": r2_support_sha,
        "external_inputs": [str(p) for p in prep.external_inputs(case)],
        "derived_from": {"cell": "bowldrawer_A", "relation": "independent restage",
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

    # 10. Charge the diagnostic. Idempotent by (phase, seed, bundle digest).
    #     `ASPIRE_ROOT` makes the importer resolve the ledger module from the
    #     staged tree rather than from the engineering checkout it was loaded
    #     from. The two are byte-identical here (the state module is part of
    #     COMMON_OVERLAY) but the staged copy is the one the case is bound to.
    print("  [8] importing the charged diagnostic...")
    os.environ["ASPIRE_ROOT"] = str(sim)
    state = importer.open_state(task_dir)
    record = importer.import_diagnostic(state, PREFLIGHT)
    print(f"      phase={record['phase']} seed={record['seed']} "
          f"status={record['status']} charged={record['charged']} "
          f"task_completed={record['task_completed']}")

    # 11. Verify against the ledger's own accounting, not the case's claim.
    print("  [9] verifying the ledger...")
    check = importer.open_state(task_dir)
    charged = [r for r in check.data["trials"] if r.get("charged")]
    imported = importer.imported_rows(check)
    if len(charged) != 1 or len(imported) != 1:
        raise SystemExit(f"expected exactly one charged imported row; "
                         f"charged={len(charged)} imported={len(imported)}")
    problems = importer.identity_errors(imported[0], record["bundle_sha256"])
    if problems:
        raise SystemExit("imported row is not the charged diagnostic: "
                         + "; ".join(problems))
    ledger_budget = {str(s): check.budget_remaining(s) for s in check.seeds}
    if ledger_budget != {IMPORT_SEED: 2, **{str(s): 3 for s in range(52, 66)}}:
        raise SystemExit(f"ledger budget disagrees with the case: {ledger_budget}")
    if ledger_budget != scope_r2.remaining_budget(case):
        raise SystemExit("the ledger and the case disagree about the budget")
    if check.records("diagnostic") and check.candidates():
        raise SystemExit("the diagnostic leaked into the tested bundles")
    print(f"      1 charged row; ledger budget 51->{ledger_budget[IMPORT_SEED]}, "
          "52-65->3; case and ledger agree")

    # 12. Re-import must be a no-op.
    print("  [10] idempotence...")
    again = importer.open_state(task_dir)
    importer.import_diagnostic(again, PREFLIGHT)
    after = importer.open_state(task_dir)
    if len([r for r in after.data["trials"] if r.get("charged")]) != 1:
        raise SystemExit("re-import added a second charged row")
    print("      re-import is a no-op")

    receipt = {
        "staged_at": base.now(),
        "cell": CELL, "condition": CONDITION, "profile": base.PROFILES[CONDITION],
        "task": case["task"], "suite": case["suite"],
        "control": str(control), "runtime": str(sim),
        "case": str(case_path), "case_sha256": base.digest(case_path),
        "runtime_manifest": case["runtime_manifest"],
        "runtime_manifest_sha256": case["runtime_manifest_sha256"],
        "prompt_sha256": prompts, "prompt_oracle": oracles.get(CELL),
        "render": render,
        "r2_support_sha256": r2_support_sha,
        "imported_charged_attempts": case["imported_charged_attempts"],
        "ledger": str(ledger),
        "ledger_remaining_budget": ledger_budget,
        "imported_row": {k: imported[0].get(k) for k in
                         ("phase", "seed", "status", "charged", "executed",
                          "task_completed", "bundle_sha256", "directory")},
        "import_source": str(PREFLIGHT),
        "import_source_digest": importer.PINNED_DIGEST,
        "r1_preserved": True,
        "launched": False,
        "launch_blocker": "support/run_two_task_cell.py hardcodes `import "
                          "two_task_scope as scope`; an r2 run needs the same "
                          "one-line substitution in run_two_task_cell_r2.py. "
                          "Not staged by this script.",
        "note": "no model and no simulator ran here; the one charged attempt was "
                "executed on DSW beforehand and is imported by hash. No held-out "
                "seed was read. r1 is untouched.",
    }
    receipt_path = base.PARENT / f"prepare-receipt-{CELL}.json"
    base.record(receipt_path, receipt)
    print(f"\n  receipt: {receipt_path}")
    print(json.dumps({k: receipt[k] for k in
                      ("cell", "case", "case_sha256", "runtime_manifest_sha256",
                       "imported_charged_attempts", "ledger_remaining_budget",
                       "r1_preserved", "launched", "launch_blocker")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
