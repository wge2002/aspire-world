# SPDX-License-Identifier: MIT
"""Import the one real infrastructure diagnostic into this study's ledger.

This is the FULL study's own NEW preflight, executed and charged by root under
`docs/experiments/code-world-qwen-full-20260924/coordination/dsw-preflight`. It
is one attempt, not a second execution: the tree is imported through the ledger,
never re-run. The OLD pilot's diagnostic is a different source digest and is
refused by `import_diagnostic` if it is ever pointed at this ledger.

The diagnostic already ran on the DSW machine: one observation, SAM3
segmentation, GraspNet planning and IK on seed 51, with no task policy. It is a
charged simulator attempt, so the worker must start seed 51 with two real
executions left rather than three. This module records it through the existing
ledger (`NativeWorldState.begin_trial` / `finish_trial`) instead of hand-editing
`development_state.json`, so every accounting rule in the ledger applies to it
unchanged:

- `diagnostic` is in `CHARGED_PHASES`, so the row consumes one of seed 51's three
  attempts and `budget_remaining(51)` becomes 2;
- `candidates()` skips `phase == "diagnostic"`, so the import is never a tested
  bundle and can never be selected;
- `outcome()` grades only non-diagnostic executions, so the import can never read
  as an initial task success;
- `status == "complete"` keeps it out of `progress()["infrastructure_errors"]`, so
  it does not block `check`;
- `resolve_interrupted` refuses to un-charge an admitted attempt, so no retry,
  resume or recovery can refund it.

Idempotence covers a crash *during* the import. The reserved row is identified
deterministically (phase `diagnostic`, the import seed, and the bundle digest of
the pinned `code.py` hash), so a re-run finishes that same row and attaches the
provenance rather than allocating a second charged slot. A source tree whose
hashes disagree with the pinned values, or a second, different import, is
refused.

This is a provenance/accounting guard, not a security sandbox.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

IMPORT_SEED = 51
IMPORT_PHASE = "diagnostic"

#: The rehearsal tree, relative to the simulation repository root.
SOURCE_REL = "docs/experiments/code-world-qwen-full-20260924/coordination/dsw-preflight"

#: The trial directory inside that tree, relative to its root.
TRIAL_REL = ("results/libero_goal_swap/put_the_bowl_on_the_plate"
             "/infrastructure-rehearsal-no-inference/run"
             "/trial_51_sandboxrc_0_reward_0.000_taskcompleted_0")

#: Verified sha256 of every file whose content this import stands on. Re-checked
#: against the source tree on every import, and pinned into the ledger record.
PINNED_HASHES = {
    "summary.json": "061f81943002160784929f17c596113056e35813e613765fef31c399a5714837",
    "toolchain.json": "af925ccae32bb027c99b71b40008556c7fd059fd6a1b307499c588a260c5eefc",
    "source-sha256.json": "19f03dad008a5bf05517e2429d5d954fe299a13710f762c8af0f2451098a04ee",
    "replay.log": "2b7fbd3e4ef037c732cc01e87bdc594ce17dc1f119dc504e93527e368be113cf",
    f"{TRIAL_REL}/code.py": "8e5300bc22f1f39945eb2b88854b7cc26b5fbf96c5ba592181a9929fc5468ca6",
    f"{TRIAL_REL}/trace.json": "0961a2d0d4d0781218d746368f597d871a092671e2121f4212e56650140b48f8",
    f"{TRIAL_REL}/summary.txt": "21eb80daf93076058db142e9ecb3cfb555f92e319def7805ad04d83613c77556",
}

POLICY_REL = f"{TRIAL_REL}/code.py"

#: What `summary.json` must still say for this to be the diagnostic we describe.
EXPECTED_SUMMARY = {
    "state": "passed",
    "seed": IMPORT_SEED,
    "charged": True,
    "task_policy_executed": False,
    "exit_code": 0,
    "purpose": "infrastructure-only observation/SAM3/GraspNet/IK",
    "remaining_real_budget": {"51": 2, **{str(s): 3 for s in range(52, 66)}},
}

RESULT = {"sandbox_rc": 0, "reward": 0.0, "task_completed": 0}


class PilotImportError(RuntimeError):
    """The import cannot be performed against this source tree or ledger."""


#: Preferred name in this study; the pilot spelling stays as an alias so the
#: audited accounting code below is unchanged.
FullImportError = PilotImportError


# ---- the ledger module --------------------------------------------------------

def load_state_module():
    """Import `native_world_fixloop_state` however this process is laid out.

    The driver runs with `scripts/libero` on `sys.path`; tests and ad-hoc calls
    may not. Both resolve to the same file, so the ledger semantics are identical.
    """
    try:
        return importlib.import_module("native_world_fixloop_state")
    except ModuleNotFoundError:
        pass
    candidates = []
    if os.environ.get("ASPIRE_ROOT"):
        candidates.append(Path(os.environ["ASPIRE_ROOT"]))
    candidates.extend(Path(__file__).resolve().parents)
    for base in candidates:
        path = base / "scripts" / "libero" / "native_world_fixloop_state.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location("native_world_fixloop_state", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["native_world_fixloop_state"] = module
            spec.loader.exec_module(module)
            return module
    raise PilotImportError("cannot locate scripts/libero/native_world_fixloop_state.py")


def open_state(task_dir: Path):
    """Resume the ledger with the identity it was created with, byte-identical.

    `NativeWorldState.__init__` rejects a resume whose identity differs at all, so
    the identity is read back from the ledger rather than reconstructed.
    """
    module = load_state_module()
    path = Path(task_dir).resolve() / "development_state.json"
    if not path.is_file():
        raise PilotImportError(f"no ledger to import into; run `protocol init` first: {path}")
    identity = json.loads(path.read_text())["identity"]
    return module.NativeWorldState(Path(task_dir), identity, resume=True)


# ---- the source tree ----------------------------------------------------------

def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(hashes: dict[str, str]) -> str:
    """One deterministic identity for the whole imported evidence set."""
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


#: The digest of the pinned set; the reserved row is found by this plus the phase,
#: seed and bundle, without reading any provenance the model could write.
PINNED_DIGEST = source_digest(PINNED_HASHES)


def verify_tree(source_dir: Path) -> dict[str, str]:
    """Re-check every pinned hash. Returns the measured hashes (== pinned)."""
    source = Path(source_dir)
    if not source.is_dir():
        raise PilotImportError(f"import source is not a directory: {source}")
    measured, problems = {}, []
    for relative, expected in sorted(PINNED_HASHES.items()):
        path = source / relative
        if not path.is_file():
            problems.append(f"missing {relative}")
            continue
        actual = file_hash(path)
        measured[relative] = actual
        if actual != expected:
            problems.append(f"{relative}: expected {expected}, found {actual}")
    if problems:
        raise PilotImportError("import source does not match the pinned evidence: "
                               + "; ".join(problems))
    return measured


def read_summary(source_dir: Path) -> dict:
    """Load `summary.json` and refuse a tree that no longer says what we claim."""
    summary = json.loads((Path(source_dir) / "summary.json").read_text())
    mismatched = {key: summary.get(key) for key, value in EXPECTED_SUMMARY.items()
                  if summary.get(key) != value}
    if mismatched:
        raise PilotImportError(
            f"summary.json disagrees with the imported claim: {mismatched}")
    return summary


def provenance(source_dir: Path, summary: dict, measured: dict[str, str]) -> dict:
    """Everything needed to tell later readers what this charged row really is."""
    return {
        "kind": "dsw-infrastructure-rehearsal",
        "source_dir": str(Path(source_dir).resolve()),
        "source_rel": SOURCE_REL,
        "trial_rel": TRIAL_REL,
        "source_digest": source_digest(measured),
        "hashes": dict(sorted(measured.items())),
        "imported_by": "support/full_import.py:import_diagnostic",
        "host": summary.get("host"),
        "gpu": summary.get("gpu"),
        "seed": summary["seed"],
        "state": summary["state"],
        "charged": summary["charged"],
        "exit_code": summary["exit_code"],
        "purpose": summary["purpose"],
        "task_policy_executed": summary["task_policy_executed"],
        "remaining_real_budget": summary["remaining_real_budget"],
        "toolchain": summary.get("toolchain"),
        "note": ("Real charged infrastructure diagnostic executed on seed "
                 f"{IMPORT_SEED} before the solver started. It ran no task policy, "
                 "is excluded from tested bundles and from task success, and "
                 "cannot be refunded by a retry or resume."),
    }


# ---- the import ---------------------------------------------------------------

def expected_bundle(measured: dict[str, str]) -> dict[str, str]:
    return {"policy": measured[POLICY_REL]}


def matching_rows(state, digest: str) -> list[dict]:
    """Every row this import could have reserved, identified without provenance.

    A crash between `begin_trial` and the provenance write leaves a row with no
    `imported_from`; it is still *this* import's row, because the phase, seed and
    bundle digest are fully determined by the pinned source. Two such rows mean
    the seed was charged twice for one import, which this module must never do and
    cannot silently repair — so callers reject rather than pick the first.
    """
    return [r for r in state.data["trials"]
            if r["phase"] == IMPORT_PHASE and r["seed"] == IMPORT_SEED
            and r["bundle_sha256"] == digest]


def _reserved_row(state, digest: str) -> dict | None:
    """The single matching row, or None. Raises when the ledger holds several."""
    matches = matching_rows(state, digest)
    if len(matches) > 1:
        raise PilotImportError(
            f"{len(matches)} rows already match the imported diagnostic "
            f"(phase {IMPORT_PHASE}, seed {IMPORT_SEED}, bundle {digest[:12]}…); "
            "the cell charged it more than once and must be inspected by hand")
    return matches[0] if matches else None


def imported_rows(state) -> list[dict]:
    return [r for r in state.data["trials"] if r.get("imported_from")]


#: What a charged import row must still look like for it to be believed.
IMPORT_IDENTITY = {"phase": IMPORT_PHASE, "seed": IMPORT_SEED, "charged": True,
                   "executed": True, "status": "complete"}


def identity_errors(record: dict, digest: str) -> list[str]:
    """Ways a finished import row could have stopped being the row we wrote."""
    problems = [f"{key}={record.get(key)!r} (expected {value!r})"
                for key, value in IMPORT_IDENTITY.items() if record.get(key) != value]
    if record.get("bundle_sha256") != digest:
        problems.append(f"bundle_sha256={record.get('bundle_sha256')!r} (expected {digest!r})")
    if record.get("task_completed") != 0:
        problems.append(f"task_completed={record.get('task_completed')!r} (expected 0)")
    if record.get("alias_of"):
        problems.append("the import row is an alias")
    return problems


def import_diagnostic(state, source_dir: Path) -> dict:
    """Charge the pre-executed diagnostic to seed 51, exactly once.

    `state` is an already-initialized/resumed `NativeWorldState`; `source_dir` is
    the fixed `dsw-preflight` tree. Returns the ledger record. Safe to call again
    after success or after a crash at any point: the same charged row is reused.

    Raises `PilotImportError` on a mismatched source or a second, different
    import, and lets the ledger's own `ProtocolError` propagate when the seed is
    out of partition or its three attempts are already spent.
    """
    measured = verify_tree(source_dir)
    summary = read_summary(source_dir)
    bundle = expected_bundle(measured)
    module = load_state_module()
    digest = module.bundle_identity(bundle)
    record_provenance = provenance(source_dir, summary, measured)

    # `_reserved_row` refuses a ledger that already matches more than once, so a
    # marked row can never mask a second, unmarked charged row for the same import.
    reserved = _reserved_row(state, digest)

    existing = imported_rows(state)
    if len(existing) > 1:
        raise PilotImportError(
            f"ledger already carries {len(existing)} imported rows; refusing to add another")
    if existing:
        previous = existing[0]["imported_from"]
        if previous.get("source_digest") != record_provenance["source_digest"]:
            raise PilotImportError(
                "a different diagnostic was already imported into this cell "
                f"({previous.get('source_digest')} != {record_provenance['source_digest']}); "
                "one charged import per cell")
        if existing[0] is not reserved:
            raise PilotImportError(
                "the imported row does not match the pinned phase/seed/bundle; "
                "the ledger's import record is not the one this module wrote")
        problems = identity_errors(existing[0], digest)
        if problems:
            raise PilotImportError(
                "the imported row is no longer the charged diagnostic it recorded: "
                + "; ".join(problems))
        # Idempotent: a resume never re-imports or refunds. Only the recorded
        # source path is refreshed, which charges nothing.
        _normalize_sources(state, existing[0])
        return existing[0]

    if IMPORT_SEED not in state.seeds:
        raise PilotImportError(
            f"seed {IMPORT_SEED} carries the diagnostic but is outside this cell's "
            f"development partition {list(state.seeds)}")

    record = reserved
    if record is None:
        # Charges the slot and creates the trial directory; raises ProtocolError if
        # the seed is out of partition or its budget is already spent.
        record = state.begin_trial(IMPORT_PHASE, IMPORT_SEED, bundle, {"policy": POLICY_REL})

    destination = Path(state.task_dir) / record["directory"]
    copy_evidence(source_dir, destination)

    if record["status"] != "complete":
        state.finish_trial(record, exit_code=0,
                           result={**RESULT, "trial_dir": record["directory"]})
    # Newly allocated and recovered rows alike end up pointing at the copy that
    # now exists, not at the relative path inside the source tree.
    _normalize_sources(state, record)
    record["imported_from"] = record_provenance
    state.save()
    return record


def _normalize_sources(state, record: dict) -> dict:
    """Point `sources["policy"]` at the copied `code.py` under the trial directory.

    A crash immediately after `begin_trial` leaves the placeholder relative path
    behind, and only the row that allocated the slot used to be rewritten. This
    runs on every path, is idempotent, and never charges an attempt.
    """
    expected = str(Path(state.task_dir) / record["directory"] / POLICY_REL)
    if record.get("sources") != {"policy": expected}:
        record["sources"] = {"policy": expected}
        state.save()
    return record


def copy_evidence(source_dir: Path, destination: Path) -> dict[str, str]:
    """Copy the tree into the reserved trial directory and re-verify it there.

    `begin_trial` already created `destination`, and a resumed import may find a
    partial copy, so the copy is idempotent by overwrite.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(source_dir), destination, dirs_exist_ok=True)
    return verify_tree(destination)


__all__ = [
    "FullImportError", "IMPORT_IDENTITY", "IMPORT_PHASE", "IMPORT_SEED", "PINNED_DIGEST", "PINNED_HASHES",
    "POLICY_REL", "PilotImportError", "SOURCE_REL", "TRIAL_REL", "copy_evidence",
    "expected_bundle", "file_hash", "identity_errors", "import_diagnostic",
    "imported_rows", "load_state_module", "matching_rows", "open_state", "provenance",
    "read_summary", "source_digest", "verify_tree",
]
