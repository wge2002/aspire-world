#!/usr/bin/env python3
"""Derive `run_two_task_cell_c_r2.py` from `run_two_task_cell_r2.py` by exact patch.

Why a generator instead of a hand-written copy:

`run_two_task_cell_r2.py` is 37 KB of guards, hooks, locks, deadlines, ownership
checks, freeze and held-out logic, and it is the file currently running the paid
A job. A C fork must differ from it *only* in the accounting that differs — two
charged seed-51 attempts instead of one — and in nothing else. Hand-copying 37 KB
cannot prove that. Applying a fixed list of exact string replacements, each
asserted to match exactly once, can: anything this script does not name is
byte-identical by construction, and the receipt records both hashes plus the
unified diff so the claim is checkable after the fact.

The A driver is READ ONLY here. It is never written, and the frozen copy under
the live launch directory is never touched.

Run:  python3 make-c-r2-driver.py [--check]

`--check` regenerates in memory and compares against the existing C driver
without writing, so CI or a later session can confirm the file on disk is still
exactly this transform of the A driver.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path

STUDY = Path(__file__).resolve().parent
SUPPORT = STUDY / "support"
SOURCE = SUPPORT / "run_two_task_cell_r2.py"
TARGET = SUPPORT / "run_two_task_cell_c_r2.py"

#: The A driver this fork is derived from. Pinned so a later edit to the A
#: driver cannot silently change what the C driver claims to be a fork of.
SOURCE_SHA256 = "89856895b328425012573715b3aae50511721bdce407314af018b677eeffada2"

# ---------------------------------------------------------------------------
# The patch. Each entry is (label, old, new) and must match exactly once.
# ---------------------------------------------------------------------------

PATCHES: list[tuple[str, str, str]] = [

    # -- 1. the module docstring's fork note -------------------------------
    ("docstring: fork provenance",
     """Forked from `run_two_task_cell.py`, which is byte-identical to this file except
for three changes, all of them about one charged attempt that already exists:
""",
     """Forked from `run_two_task_cell_r2.py` by `make-c-r2-driver.py`, which applies a
fixed list of exact string replacements and asserts each matches once. Every
guard, hook, lock, deadline, ownership check, freeze, manifest and held-out path
is byte-identical to the A r2 driver by construction; only the seed-51
accounting below differs. That driver is in turn forked from
`run_two_task_cell.py`, which is byte-identical to it except for three changes,
all of them about charged attempts that already exist:
"""),

    # -- 2. the ledger-block docstring: one attempt becomes two ------------
    ("docstring: ledger block",
     """the ledger block
    r1 initializes a fresh ledger and refuses to find a charged row in it. This
    cell's seed-51 diagnostic was executed on DSW before the run and imported by
    hash, so `init` is NOT called here: it would refuse the existing cell, and a
    fresh ledger would silently refund that attempt. The pre-staged ledger is
    verified instead — exactly one charged row, which must be the imported
    diagnostic, at the pinned source digest and the bundle digest recomputed
    from the pinned policy hash, with the declared budget showing seed 51 -> 2.
""",
     """the ledger block
    r1 initializes a fresh ledger and refuses to find a charged row in it. This
    cell's seed-51 attempts were executed on DSW before the run, so `init` is
    NOT called here: it would refuse the existing cell, and a fresh ledger would
    silently refund them. The pre-staged ledger is verified instead.

    Condition C's seed 51 carries TWO real charged attempts, not one. Run 1
    (sandbox_rc=1, KeyError 'rgb') had its output directory deleted before Run 2
    was started, so its evidence is permanently lost and it is charged as an
    explicit tombstone; Run 2 (sandbox_rc=0) survives and is imported by hash.
    This driver therefore requires exactly two charged rows — one tombstone, one
    import, and they must not be the same row — with the declared budget showing
    seed 51 -> 1. Accepting one charged row here would hand back an attempt that
    was really spent and let a fourth execution run on a three-attempt seed.
"""),

    # -- 3. the import ---------------------------------------------------
    ("import: two_task_import_c",
     """import two_task_scope_r2 as scope
import two_task_import
""",
     """import two_task_scope_r2 as scope
# The C-specific importer. Bound to the same local name so every accounting call
# below is byte-identical to the A driver's; only the module behind it differs.
import two_task_import_c as two_task_import
"""),

    # -- 4. the recorded importer hash -----------------------------------
    ("campaign state: import_sha256",
     """             "import_sha256": digest(Path(__file__).with_name("two_task_import.py")),""",
     """             "import_sha256": digest(Path(__file__).with_name("two_task_import_c.py")),"""),

    # -- 5. the stager named in the ledger comment -----------------------
    ("comment: stager name",
     """        # by `stage-bowldrawer-a-r2.py` before the run, because the seed-51
        # diagnostic was executed on DSW and imported by hash. Calling `init`""",
     """        # by `stage-bowldrawer-c-r2.py` before the run, because both seed-51
        # attempts were executed on DSW beforehand. Calling `init`"""),

    # -- 6. the charged-row count ----------------------------------------
    ("ledger check: two charged rows",
     """        trials = json.loads(ledger_path.read_text())["trials"]
        charged = [row for row in trials if row.get("charged")]
        imported = [row for row in trials if row.get("imported_from")]
        if len(charged) != 1 or len(imported) != 1 or charged[0] is not imported[0]:
            raise Blocked("ledger", "the pre-staged ledger must hold exactly one charged "
                          "attempt and it must be the imported diagnostic",
                          detail={"charged": len(charged), "imported": len(imported),
                                  "charged_rows": charged})""",
     """        trials = json.loads(ledger_path.read_text())["trials"]
        charged = [row for row in trials if row.get("charged")]
        imported = [row for row in trials if row.get("imported_from")]
        tombstones = [row for row in trials if row.get("lost_evidence")]
        if len(charged) != two_task_import.CHARGED_ATTEMPTS or len(imported) != 1 \\
                or len(tombstones) != 1 or imported[0] is tombstones[0]:
            raise Blocked("ledger", "the pre-staged ledger must hold exactly two charged "
                          "seed-51 attempts: one tombstone for the run whose evidence was "
                          "lost, and one import of the run whose evidence survives",
                          detail={"charged": len(charged), "imported": len(imported),
                                  "tombstones": len(tombstones),
                                  "expected_charged": two_task_import.CHARGED_ATTEMPTS,
                                  "charged_rows": charged})
        # The tombstone is checked against the digest recomputed from
        # TOMBSTONE_BUNDLE, and must still declare its evidence lost and still
        # carry a non-success status. A tombstone that drifted to `complete`
        # would turn an unverifiable crash into apparent clean evidence.
        tombstone_problems = two_task_import.tombstone_errors(
            tombstones[0],
            two_task_import.load_state_module().bundle_identity(
                two_task_import.TOMBSTONE_BUNDLE))
        if tombstone_problems:
            raise Blocked("ledger", "the pre-staged tombstone is not the charged lost "
                          "seed-51 attempt this driver expects",
                          detail={"problems": tombstone_problems})"""),

    # -- 7. the declared budget ------------------------------------------
    ("ledger check: declared budget 51 -> 1",
     """        declared = scope.remaining_budget(case)
        if declared.get(str(two_task_import.IMPORT_SEED)) != 2 or \\
                any(value != 3 for seed, value in declared.items()
                    if seed != str(two_task_import.IMPORT_SEED)):
            raise Blocked("ledger", "the declared remaining budget is not seed 51 -> 2 with "
                          "every other development seed at 3",
                          detail={"remaining_real_budget": declared})""",
     """        declared = scope.remaining_budget(case)
        expected_remaining = (two_task_import.load_state_module().ATTEMPT_LIMIT
                              - two_task_import.CHARGED_ATTEMPTS)
        if declared.get(str(two_task_import.IMPORT_SEED)) != expected_remaining or \\
                any(value != 3 for seed, value in declared.items()
                    if seed != str(two_task_import.IMPORT_SEED)):
            raise Blocked("ledger", f"the declared remaining budget is not seed 51 -> "
                          f"{expected_remaining} with every other development seed at 3",
                          detail={"remaining_real_budget": declared,
                                  "expected_seed_51": expected_remaining})
        # The two views of the budget must agree. `scope.remaining_budget` reads
        # the case's declared import; the ledger counts its own charged rows and
        # is what `begin_trial` actually gates on. If they disagree the solver
        # would be shown one number while the simulator enforced another.
        ledger_used = len([row for row in charged
                           if row.get("seed") == two_task_import.IMPORT_SEED])
        if ledger_used != two_task_import.CHARGED_ATTEMPTS:
            raise Blocked("ledger", "the ledger's own charged-row count for seed 51 "
                          "disagrees with the declared import",
                          detail={"ledger_charged_rows": ledger_used,
                                  "declared_import": two_task_import.CHARGED_ATTEMPTS})"""),

    # -- 8. the recorded import summary ----------------------------------
    ("campaign state: diagnostic_import",
     """        state["diagnostic_import"] = {
            "imported": True,
            "reason": "one charged infrastructure diagnostic executed on DSW for seed "
                      "51 and imported by hash; not refundable and not gradeable",
            "phase": two_task_import.IMPORT_PHASE,
            "seed": two_task_import.IMPORT_SEED,
            "source_digest": two_task_import.PINNED_DIGEST,
            "bundle_sha256": imported[0].get("bundle_sha256"),
            "remaining_real_budget": declared,
            "ledger_dir": str(task_dir), "ledger": str(ledger_path)}""",
     """        state["diagnostic_import"] = {
            "imported": True,
            "charged_attempts": two_task_import.CHARGED_ATTEMPTS,
            "reason": "two charged infrastructure diagnostics executed on DSW for seed "
                      "51 before this run; neither is refundable and neither is "
                      "gradeable. Run 1's evidence was deleted before Run 2 started and "
                      "is carried as a tombstone; Run 2's evidence survives and is "
                      "imported by hash.",
            "phase": two_task_import.IMPORT_PHASE,
            "seed": two_task_import.IMPORT_SEED,
            "source_digest": two_task_import.PINNED_DIGEST,
            "imported_row": {
                "attempt": imported[0].get("attempt"),
                "status": imported[0].get("status"),
                "bundle_sha256": imported[0].get("bundle_sha256")},
            "tombstone_row": {
                "attempt": tombstones[0].get("attempt"),
                "status": tombstones[0].get("status"),
                "sandbox_rc": tombstones[0].get("sandbox_rc"),
                "process_exit_code": tombstones[0].get("process_exit_code"),
                "evidence": tombstones[0].get("lost_evidence", {}).get("evidence"),
                "bundle_sha256": tombstones[0].get("bundle_sha256")},
            "bundle_sha256": imported[0].get("bundle_sha256"),
            "remaining_real_budget": declared,
            "ledger_dir": str(task_dir), "ledger": str(ledger_path)}"""),
]


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build() -> tuple[str, str, list[dict]]:
    source = SOURCE.read_text()
    actual = sha256(source)
    if actual != SOURCE_SHA256:
        raise SystemExit(
            f"the A r2 driver is not the pinned fork base:\n"
            f"  expected {SOURCE_SHA256}\n  found    {actual}\n"
            f"Refusing to derive a C driver from an unrecognized source.")

    text = source
    applied = []
    for label, old, new in PATCHES:
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                f"patch {label!r} matched {count} times (expected exactly 1); "
                "the fork base changed shape and the patch must be re-read "
                "against it rather than forced")
        text = text.replace(old, new, 1)
        applied.append({"label": label,
                        "removed_lines": old.count("\n"),
                        "added_lines": new.count("\n")})
    return source, text, applied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="compare against the file on disk without writing")
    args = parser.parse_args()

    source, generated, applied = build()

    print(f"fork base : {SOURCE.name}  {sha256(source)}")
    print(f"generated : {TARGET.name}  {sha256(generated)}")
    for entry in applied:
        print(f"  patch  -{entry['removed_lines']:>3}/+{entry['added_lines']:<3} "
              f"{entry['label']}")

    diff = list(difflib.unified_diff(
        source.splitlines(keepends=True), generated.splitlines(keepends=True),
        fromfile=f"a/{SOURCE.name}", tofile=f"b/{TARGET.name}"))
    changed = sum(1 for line in diff
                  if line.startswith(("+", "-"))
                  and not line.startswith(("+++", "---")))
    print(f"  total changed lines: {changed} of {len(source.splitlines())}")

    if args.check:
        if not TARGET.is_file():
            print(f"\nFAIL: {TARGET} does not exist")
            return 1
        on_disk = TARGET.read_text()
        if on_disk != generated:
            print(f"\nFAIL: {TARGET.name} on disk is NOT this transform of "
                  f"{SOURCE.name}\n  on disk   {sha256(on_disk)}\n"
                  f"  generated {sha256(generated)}")
            return 1
        print(f"\nOK: {TARGET.name} on disk is exactly this transform.")
        return 0

    if TARGET.exists() and TARGET.read_text() == generated:
        print(f"\n{TARGET.name} already matches; nothing written.")
    else:
        TARGET.write_text(generated)
        print(f"\nwrote {TARGET}")

    receipt = STUDY / "c-r2-driver-derivation.json"
    receipt.write_text(json.dumps({
        "generated_by": "make-c-r2-driver.py",
        "fork_base": SOURCE.name,
        "fork_base_sha256": sha256(source),
        "derived": TARGET.name,
        "derived_sha256": sha256(generated),
        "patches": applied,
        "changed_lines": changed,
        "source_lines": len(source.splitlines()),
        "diff": "".join(diff),
        "note": ("Every byte this script does not name is identical to the fork "
                 "base by construction. Re-run with --check to confirm the file "
                 "on disk is still exactly this transform."),
    }, indent=2) + "\n")
    print(f"receipt: {receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
