# SPDX-License-Identifier: MIT
"""Import the one real infrastructure diagnostic for bowldrawer_A into its ledger.

Adapted from code-world-qwen-full-20260924/support/full_import.py. The logic and
every accounting invariant are identical; only the source coordinates differ:

  SOURCE_DIR  — the bowldrawer_A preflight coordination dir (two-task study)
  SOURCE_REL  — provenance label, study-relative path used only in ledger records
  TRIAL_REL   — task-specific subpath inside the preflight tree
  PINNED_HASHES — seven sha256 hashes measured 2026-09-27 before this module ran

The diagnostic was executed on GPU 2 of DSW host dsw-829708-645b58b7f6-w8l4g,
seed 51, task open_the_top_drawer_and_put_the_bowl_inside. It ran native_cc_toolchain_
probe.py (observation → SAM3 → GraspNet → IK) with no task policy and no model
inference. Charging one of seed 51's three attempts makes remaining budget
51 → 2, 52-65 → 3.

All accounting rules from full_import.py apply unchanged:
  - "diagnostic" is in CHARGED_PHASES: consumes one attempt for seed 51.
  - candidates() skips phase == "diagnostic": never a tested bundle.
  - outcome() grades only non-diagnostic rows: cannot read as task success.
  - status == "complete" keeps it out of infrastructure_errors.
  - resolve_interrupted refuses to un-charge an admitted attempt.

Idempotence: a crash during import leaves a reserved row identified by
(phase, seed, bundle_digest). Re-running finishes that row; a second
different import is refused.
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

#: Absolute path to the preflight coordination directory.
SOURCE_DIR = Path("/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926"
                  "/coordination/dsw-preflight-bowldrawer-a")

#: Human-readable provenance label; stored in ledger records.
SOURCE_REL = "coordination/dsw-preflight-bowldrawer-a"

#: Trial subpath inside the preflight tree.
TRIAL_REL = (
    "results/libero_goal_swap/open_the_top_drawer_and_put_the_bowl_inside"
    "/infrastructure-rehearsal-no-inference/run"
    "/trial_51_sandboxrc_0_reward_0.000_taskcompleted_0"
)

#: Verified sha256 of every file whose content this import stands on.
#: All seven hashes measured 2026-09-27 from the preflight output before this
#: module was written. summary.json and toolchain.json differ from the Sep-24
#: full-study import because they are the bowldrawer_A / two-task run's own files.
PINNED_HASHES = {
    "summary.json":
        "c5b21fa2f259ac8e6c95f8515c81b4b204533627999cc696602b26a57592407e",
    "toolchain.json":
        "c0bcd42a02962229415790d5530fb40b0955b56fadde7f0fcd503b23f1d7d9a2",
    "source-sha256.json":
        "19f03dad008a5bf05517e2429d5d954fe299a13710f762c8af0f2451098a04ee",
    "replay.log":
        "5d1b936fbc8ef298780ef7bcf74bccc8f31f43bc67d449fefa125c7a596c5e62",
    f"{TRIAL_REL}/code.py":
        "8e5300bc22f1f39945eb2b88854b7cc26b5fbf96c5ba592181a9929fc5468ca6",
    f"{TRIAL_REL}/trace.json":
        "2ba6e35aae9a9742b91bbae94f47e5af0afea6312760d83a80468abe4ec45774",
    f"{TRIAL_REL}/summary.txt":
        "e74c18a06e5b3017719d895acc5cac8d1e574c7e6623429eb3e1303a0465ba1e",
}

POLICY_REL = f"{TRIAL_REL}/code.py"

#: What summary.json must say for this to be the diagnostic we describe.
#: purpose uses em-dash exactly as written by run-dsw-preflight-bowldrawer-a.py.
EXPECTED_SUMMARY = {
    "state": "passed",
    "seed": IMPORT_SEED,
    "charged": True,
    "task_policy_executed": False,
    "exit_code": 0,
    "purpose": "infrastructure-only observation/SAM3/GraspNet/IK — no model inference",
    "remaining_real_budget": {"51": 2, **{str(s): 3 for s in range(52, 66)}},
}

RESULT = {"sandbox_rc": 0, "reward": 0.0, "task_completed": 0}


class TwoTaskImportError(RuntimeError):
    """The import cannot be performed against this source tree or ledger."""


# Keep the canonical alias from full_import.py so the accounting logic below
# is byte-comparable.
PilotImportError = TwoTaskImportError
FullImportError = TwoTaskImportError


# ---- ledger module -----------------------------------------------------------

def load_state_module():
    try:
        return __import__("native_world_fixloop_state")
    except ModuleNotFoundError:
        pass
    import importlib.util as _ilu
    candidates = []
    if os.environ.get("ASPIRE_ROOT"):
        candidates.append(Path(os.environ["ASPIRE_ROOT"]))
    candidates.extend(Path(__file__).resolve().parents)
    for base in candidates:
        path = base / "scripts" / "libero" / "native_world_fixloop_state.py"
        if path.is_file():
            spec = _ilu.spec_from_file_location("native_world_fixloop_state", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["native_world_fixloop_state"] = module
            spec.loader.exec_module(module)
            return module
    raise TwoTaskImportError(
        "cannot locate scripts/libero/native_world_fixloop_state.py")


def open_state(task_dir: Path):
    module = load_state_module()
    path = Path(task_dir).resolve() / "development_state.json"
    if not path.is_file():
        raise TwoTaskImportError(
            f"no ledger to import into; run `protocol init` first: {path}")
    identity = json.loads(path.read_text())["identity"]
    return module.NativeWorldState(Path(task_dir), identity, resume=True)


# ---- source tree verification ------------------------------------------------

def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(hashes: dict) -> str:
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


PINNED_DIGEST = source_digest(PINNED_HASHES)


def verify_tree(source_dir: Path) -> dict:
    source = Path(source_dir)
    if not source.is_dir():
        raise TwoTaskImportError(
            f"import source is not a directory: {source}")
    measured, problems = {}, []
    for relative, expected in sorted(PINNED_HASHES.items()):
        path = source / relative
        if not path.is_file():
            problems.append(f"missing {relative}")
            continue
        actual = file_hash(path)
        measured[relative] = actual
        if actual != expected:
            problems.append(
                f"{relative}: expected {expected}, found {actual}")
    if problems:
        raise TwoTaskImportError(
            "import source does not match the pinned evidence: "
            + "; ".join(problems))
    return measured


def read_summary(source_dir: Path) -> dict:
    summary = json.loads((Path(source_dir) / "summary.json").read_text())
    mismatched = {key: summary.get(key)
                  for key, value in EXPECTED_SUMMARY.items()
                  if summary.get(key) != value}
    if mismatched:
        raise TwoTaskImportError(
            f"summary.json disagrees with the imported claim: {mismatched}")
    return summary


def provenance(source_dir: Path, summary: dict, measured: dict) -> dict:
    return {
        "kind": "dsw-infrastructure-rehearsal",
        "source_dir": str(Path(source_dir).resolve()),
        "source_rel": SOURCE_REL,
        "trial_rel": TRIAL_REL,
        "source_digest": source_digest(measured),
        "hashes": dict(sorted(measured.items())),
        "imported_by": "support/two_task_import.py:import_diagnostic",
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
        "note": (
            f"Real charged infrastructure diagnostic executed on seed "
            f"{IMPORT_SEED} before the solver started. It ran no task policy, "
            "is excluded from tested bundles and from task success, and "
            "cannot be refunded by a retry or resume."),
    }


# ---- import ------------------------------------------------------------------

def expected_bundle(measured: dict) -> dict:
    return {"policy": measured[POLICY_REL]}


def matching_rows(state, digest: str) -> list:
    return [r for r in state.data["trials"]
            if r["phase"] == IMPORT_PHASE
            and r["seed"] == IMPORT_SEED
            and r["bundle_sha256"] == digest]


def _reserved_row(state, digest: str):
    matches = matching_rows(state, digest)
    if len(matches) > 1:
        raise TwoTaskImportError(
            f"{len(matches)} rows already match the imported diagnostic "
            f"(phase {IMPORT_PHASE}, seed {IMPORT_SEED}, "
            f"bundle {digest[:12]}…); the cell charged it more than once "
            "and must be inspected by hand")
    return matches[0] if matches else None


def imported_rows(state) -> list:
    return [r for r in state.data["trials"] if r.get("imported_from")]


IMPORT_IDENTITY = {
    "phase": IMPORT_PHASE, "seed": IMPORT_SEED,
    "charged": True, "executed": True, "status": "complete",
}


def identity_errors(record: dict, digest: str) -> list:
    problems = [
        f"{key}={record.get(key)!r} (expected {value!r})"
        for key, value in IMPORT_IDENTITY.items()
        if record.get(key) != value
    ]
    if record.get("bundle_sha256") != digest:
        problems.append(
            f"bundle_sha256={record.get('bundle_sha256')!r} "
            f"(expected {digest!r})")
    if record.get("task_completed") != 0:
        problems.append(
            f"task_completed={record.get('task_completed')!r} "
            f"(expected 0)")
    if record.get("alias_of"):
        problems.append("the import row is an alias")
    return problems


def copy_evidence(source_dir: Path, destination: Path) -> dict:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(source_dir), destination, dirs_exist_ok=True)
    return verify_tree(destination)


def _normalize_sources(state, record: dict) -> dict:
    expected = str(
        Path(state.task_dir) / record["directory"] / POLICY_REL)
    if record.get("sources") != {"policy": expected}:
        record["sources"] = {"policy": expected}
        state.save()
    return record


def import_diagnostic(state, source_dir: Path) -> dict:
    """Charge the pre-executed diagnostic to seed 51, exactly once.

    Idempotent: a crash during import leaves a reserved row identified by
    (phase, seed, bundle_digest). Re-running finishes that row; a second,
    different import is refused.
    """
    measured = verify_tree(source_dir)
    summary = read_summary(source_dir)
    bundle = expected_bundle(measured)
    module = load_state_module()
    digest = module.bundle_identity(bundle)
    record_provenance = provenance(source_dir, summary, measured)

    reserved = _reserved_row(state, digest)

    existing = imported_rows(state)
    if len(existing) > 1:
        raise TwoTaskImportError(
            f"ledger already carries {len(existing)} imported rows; "
            "refusing to add another")
    if existing:
        previous = existing[0]["imported_from"]
        if previous.get("source_digest") != record_provenance["source_digest"]:
            raise TwoTaskImportError(
                "a different diagnostic was already imported into this cell "
                f"({previous.get('source_digest')} != "
                f"{record_provenance['source_digest']}); "
                "one charged import per cell")
        if existing[0] is not reserved:
            raise TwoTaskImportError(
                "the imported row does not match the pinned phase/seed/bundle; "
                "the ledger's import record is not the one this module wrote")
        problems = identity_errors(existing[0], digest)
        if problems:
            raise TwoTaskImportError(
                "the imported row is no longer the charged diagnostic it "
                "recorded: " + "; ".join(problems))
        _normalize_sources(state, existing[0])
        return existing[0]

    if IMPORT_SEED not in state.seeds:
        raise TwoTaskImportError(
            f"seed {IMPORT_SEED} carries the diagnostic but is outside this "
            f"cell's development partition {list(state.seeds)}")

    record = reserved
    if record is None:
        record = state.begin_trial(
            IMPORT_PHASE, IMPORT_SEED, bundle, {"policy": POLICY_REL})

    destination = Path(state.task_dir) / record["directory"]
    copy_evidence(source_dir, destination)

    if record["status"] != "complete":
        state.finish_trial(
            record, exit_code=0,
            result={**RESULT, "trial_dir": record["directory"]})
    _normalize_sources(state, record)
    record["imported_from"] = record_provenance
    state.save()
    return record


__all__ = [
    "TwoTaskImportError", "FullImportError", "PilotImportError",
    "IMPORT_IDENTITY", "IMPORT_PHASE", "IMPORT_SEED",
    "PINNED_DIGEST", "PINNED_HASHES", "POLICY_REL",
    "SOURCE_DIR", "SOURCE_REL", "TRIAL_REL",
    "copy_evidence", "expected_bundle", "file_hash", "identity_errors",
    "import_diagnostic", "imported_rows", "load_state_module",
    "matching_rows", "open_state", "provenance", "read_summary",
    "source_digest", "verify_tree",
]
