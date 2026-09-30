# SPDX-License-Identifier: MIT
"""Charge both real seed-51 attempts of bowldrawer_C into its ledger.

Adapted from two_task_import.py. The logic and every accounting invariant are
identical; only the source coordinates and the charged-attempt count differ:

  SOURCE_DIR  — the bowldrawer_C preflight coordination dir (two-task study)
  SOURCE_REL  — provenance label, study-relative path used only in ledger records
  TRIAL_REL   — task-specific subpath inside the preflight tree (Run 2, rc=0)
  PINNED_HASHES — seven sha256 hashes measured 2026-09-27 from Run 2's output

Run history for seed 51 of bowldrawer_C (both are charged; neither is refundable):

  Run 1 — 2026-09-27, GPU 2, sandbox_rc=1. A simulator process really ran; the
           synthetic probe policy raised KeyError 'rgb' on its observation-key
           access before reaching SAM3. The output directory was removed by
           `rm -rf` before Run 2 was started, so nothing survives to hash.
           Evidence permanently LOST. Charged.

  Run 2 — 2026-09-27, GPU 2, sandbox_rc=0. Corrected policy; all four API calls
           succeeded (get_observation → SAM3 → GraspNet → IK). ExecutableSession
           lifecycle completed; judgment_world/manifest.json written with
           status=complete. Evidence preserved. Charged.

Why this module writes TWO ledger rows
--------------------------------------
There are two independent views of the budget and they have to agree:

  * the ledger's own view — `attempts_used(51)` counts rows with charged=True,
    and `begin_trial` refuses once `budget_remaining(51)` hits 0;
  * the case's declared view — `two_task_scope_r2.remaining_budget` subtracts
    `case["imported_charged_attempts"]` from ATTEMPT_LIMIT.

Importing only the surviving Run 2 evidence would leave the ledger believing one
attempt was used while the case declares two: the ledger would then admit three
further executions on seed 51, for five real trials against a limit of three. So
Run 1 gets an honest **charged tombstone** — a real row for a real execution,
whose evidence is explicitly marked LOST rather than reconstructed:

  * its bundle identity is `sha256` of a fixed sentence stating that the
    artifact is gone — kept verbatim in the row as `bundle_preimage`, so anyone
    can recompute it and see it is not, and never was, the hash of a policy
    file. No digest of a deleted or reconstructed artifact is invented;
  * it is finished through `finish_trial(diagnostic_error=…)`, so its status is
    `diagnostic_program_error` with sandbox_rc=1, reward=0.0, task_completed=0 —
    it can never read as a success;
  * `process_exit_code` is None because Run 1's summary.json was deleted and the
    true exit status is unknown. It is not guessed.

After both rows: `attempts_used(51) == 2`, `budget_remaining(51) == 1`, matching
`imported_charged_attempts: {"51": 2}` exactly. `prove_attempt_limit()` checks
this on a throwaway copy of the ledger and demonstrates that a third execution is
admitted and a fourth is refused.

All accounting rules from two_task_import.py apply unchanged to both rows:
  - "diagnostic" is in CHARGED_PHASES: each consumes one attempt for seed 51.
  - candidates() skips phase == "diagnostic": neither is ever a tested bundle.
  - outcome() grades only non-diagnostic rows: neither can read as task success.
  - resolve_interrupted refuses to un-charge an admitted attempt.

Idempotence: both rows are keyed by (phase, seed, bundle_digest). A crash during
import leaves a reserved row; re-running finishes it. A second, different import
is refused. Re-running the whole module is a no-op.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

IMPORT_SEED = 51
IMPORT_PHASE = "diagnostic"

#: Total real charged executions on seed 51 before the solver starts.
#: Must equal the case's `imported_charged_attempts["51"]`.
CHARGED_ATTEMPTS = 2

#: Absolute path to the preflight coordination directory.
SOURCE_DIR = Path("/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926"
                  "/coordination/dsw-preflight-bowldrawer-c")

#: Human-readable provenance label; stored in ledger records.
SOURCE_REL = "coordination/dsw-preflight-bowldrawer-c"

#: Trial subpath inside the preflight tree (Run 2, sandbox_rc=0).
TRIAL_REL = (
    "results/libero_goal_swap/open_the_top_drawer_and_put_the_bowl_inside"
    "/infrastructure-rehearsal-no-inference/run"
    "/trial_51_sandboxrc_0_reward_0.000_taskcompleted_0"
)

#: Verified sha256 of every file whose content this import stands on.
#: All seven hashes measured 2026-09-27 from Run 2's preflight output (the
#: passing run, sandbox_rc=0). Run 1 (sandbox_rc=1, KeyError rgb) was deleted
#: before Run 2; its evidence is lost. summary.json and toolchain.json differ
#: from the bowldrawer_A two-task import because they are the C runner's files.
#: summary.json reflects remaining_real_budget {"51": 2, ...}, which was the
#: budget at the START of Run 2 (after Run 1 consumed one attempt).
PINNED_HASHES = {
    "summary.json":
        "37e56810d2c554c28bef92f330f7fa729832d74a113b910031d29849874f9e52",
    "toolchain.json":
        "14243398b52e947c947b9d7a6e4dc84e8663d6cbed0b78bb86b88f7b4960fd56",
    "source-sha256.json":
        "d4603a03dd2774a5d4bd1e411c67109e9ef7ed427c55b2cb5d4421774660114c",
    "replay.log":
        "42358882d4a4f4f08807d1347e813efcf8f4d42f9617ecb157d58f7cdcb427af",
    f"{TRIAL_REL}/code.py":
        "3e1aa19bad00b7ff271063423f083de24a79448c222092dc00d1363326ec7648",
    f"{TRIAL_REL}/trace.json":
        "8c4f0581961c10c302aa13410a7269d309187f515e4e817714cec302f2efbe6c",
    f"{TRIAL_REL}/summary.txt":
        "56bbabcad4d30b58243f329a6731ccacd93ae2623218573f288b722166536b01",
}

POLICY_REL = f"{TRIAL_REL}/code.py"

#: What summary.json must say for this to be the diagnostic we describe.
#: remaining_real_budget reflects the budget at Run 2 start (Run 1 consumed one).
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


# ---- Run 1: the lost attempt -------------------------------------------------

#: Run 1's bundle identity. `bundle_identity` refuses anything that is not a
#: lowercase sha256, so the row cannot carry a literal `LOST:` string. It carries
#: the next most honest thing: the sha256 of the sentence below, which is a
#: *statement that the artifact is gone*, not the artifact. The preimage is
#: stored verbatim in the row's `lost_evidence` so anyone can recompute the
#: digest and confirm it is not, and never was, the hash of a policy file.
TOMBSTONE_PREIMAGE = (
    "LOST-EVIDENCE bowldrawer_C seed 51 attempt 1 (Run 1); "
    "sandbox_rc=1; KeyError: 'rgb'; "
    "output directory deleted before Run 2; no artifact survives to hash"
)
TOMBSTONE_POLICY_DIGEST = hashlib.sha256(TOMBSTONE_PREIMAGE.encode()).hexdigest()

TOMBSTONE_BUNDLE = {"policy": TOMBSTONE_POLICY_DIGEST}

TOMBSTONE_SOURCES = {
    "policy": "LOST: run 1 output directory deleted before run 2 was started",
}

#: What is known about Run 1, and explicitly what is not. Every field that would
#: normally carry measured evidence is None, not a guess.
TOMBSTONE_PROVENANCE = {
    "kind": "dsw-infrastructure-rehearsal",
    "run": 1,
    "date": "2026-09-27",
    "host": None,
    "gpu": 2,
    "seed": IMPORT_SEED,
    "cell": "bowldrawer_C",
    "charged": True,
    "refundable": False,
    "executed": True,
    "task_policy_executed": False,
    "sandbox_rc": 1,
    "error": "KeyError: 'rgb'",
    "error_site": (
        "synthetic probe policy observation-key access "
        "(obs[...][\"images\"][\"rgb\"]), before the SAM3 call"),
    "evidence": "LOST",
    "evidence_lost_reason": (
        "the run's output directory was removed by `rm -rf` before Run 2 was "
        "started; no file survives to hash"),
    "recoverable": False,
    "source_dir": None,
    "source_digest": None,
    "hashes": None,
    "bundle_preimage": TOMBSTONE_PREIMAGE,
    "bundle_preimage_note": (
        "bundle_sha256 is derived from sha256(bundle_preimage) — the hash of the "
        "sentence recording the loss, NOT of any policy file. Recompute it to "
        "confirm this row makes no claim about an artifact's contents."),
    "process_exit_code": None,
    "process_exit_code_note": (
        "unknown — Run 1's summary.json was deleted with the rest of its output; "
        "not guessed"),
    "imported_by": "support/two_task_import_c.py:import_tombstone",
    "note": (
        "A real simulator process ran on seed 51 and consumed one of its three "
        "attempts. This row exists so the ledger's own accounting matches that "
        "fact; it is a tombstone, not evidence. It is never a tested bundle, "
        "never graded, never a success, and not refundable by a retry or a "
        "resume."),
}

TOMBSTONE_DIAGNOSTIC_ERROR = {
    "sandbox_rc": 1,
    "error": "KeyError: 'rgb'",
    "evidence": "LOST",
    "note": TOMBSTONE_PROVENANCE["note"],
}

TOMBSTONE_README = """\
# LOST EVIDENCE — bowldrawer_C seed 51, attempt 1 (Run 1)

This directory is a **tombstone**. It holds no evidence, and it never will.

What happened
-------------
On 2026-09-27 a real DSW infrastructure-rehearsal trial ran on seed 51 of
`bowldrawer_C` (GPU 2, no model inference). The synthetic probe policy raised

    KeyError: 'rgb'

on its observation-key access, before reaching the SAM3 call. `sandbox_rc` was
1. The trial's output directory was then removed by `rm -rf` before Run 2 was
started, so every artifact of this attempt — summary.json, trace.json, code.py,
the trial directory — is permanently gone.

Why the row exists anyway
-------------------------
A simulator process really ran, so the attempt is charged. Seed 51 has three
attempts; this was the first. Leaving it out of the ledger would let three more
executions be admitted on a seed that has already spent one.

What this row is not
--------------------
* Not a success. Its status is `diagnostic_program_error`, with `sandbox_rc=1`,
  `reward=0.0`, `task_completed=0`.
* Not a tested bundle. `candidates()` skips `phase == "diagnostic"`.
* Not graded evidence. `graded()` excludes diagnostic rows.
* Not reconstructed. The bundle identity is derived from `sha256` of a fixed
  sentence recording the loss — stored verbatim as `lost_evidence.bundle_preimage`
  in the ledger — and not from any policy file. Recompute it to confirm this row
  makes no claim about an artifact's contents. `process_exit_code` is `None`,
  not a guess.

The surviving second attempt (Run 2, `sandbox_rc=0`) is imported by hash into
`attempt_2`, pinned by `support/two_task_import_c.py:PINNED_HASHES`.
"""


class TwoTaskImportError(RuntimeError):
    """The import cannot be performed against this source tree or ledger."""


# Keep the canonical alias so the accounting logic below is byte-comparable.
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
        "imported_by": "support/two_task_import_c.py:import_diagnostic",
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
            f"Real charged infrastructure diagnostic: two runs executed on "
            f"seed {IMPORT_SEED} before the solver started. Run 1 (sandbox_rc=1, "
            f"KeyError rgb) had its evidence deleted before Run 2 and is charged "
            f"as a tombstone in attempt 1; Run 2 (sandbox_rc=0) is the surviving "
            f"evidence imported here as attempt 2. Both runs are charged; "
            f"case declares imported_charged_attempts={{\"51\": 2}} and the "
            f"ledger independently reports attempts_used(51)=2, remaining=1. "
            f"This row is not a tested bundle, excluded from task success, "
            f"and not refundable by a retry or resume."),
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


def tombstone_rows(state) -> list:
    """Charged rows standing in for an execution whose evidence no longer exists."""
    return [r for r in state.data["trials"] if r.get("lost_evidence")]


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


# ---- Run 1: the charged tombstone --------------------------------------------

#: What the tombstone row must look like once written. `status` is deliberately
#: NOT "complete": Run 1 crashed, and a row that cannot be verified must not be
#: allowed to read as a clean one.
TOMBSTONE_IDENTITY = {
    "phase": IMPORT_PHASE, "seed": IMPORT_SEED,
    "charged": True, "executed": True,
    "status": "diagnostic_program_error",
    "sandbox_rc": 1, "reward": 0.0, "task_completed": 0,
}


def tombstone_errors(record: dict, digest: str) -> list:
    problems = [
        f"{key}={record.get(key)!r} (expected {value!r})"
        for key, value in TOMBSTONE_IDENTITY.items()
        if record.get(key) != value
    ]
    if record.get("bundle_sha256") != digest:
        problems.append(
            f"bundle_sha256={record.get('bundle_sha256')!r} "
            f"(expected {digest!r})")
    if record.get("lost_evidence", {}).get("evidence") != "LOST":
        problems.append("the tombstone no longer declares its evidence lost")
    if record.get("imported_from"):
        problems.append(
            "the tombstone carries imported_from; it imports nothing and must "
            "never be mistaken for the row that does")
    if record.get("alias_of"):
        problems.append("the tombstone is an alias")
    return problems


def import_tombstone(state) -> dict:
    """Charge Run 1 — the executed attempt whose evidence was deleted.

    Idempotent by (phase, seed, tombstone bundle digest). Writes no evidence,
    because there is none: the row's bundle is a literal `LOST:` marker and its
    directory holds only a note saying so.
    """
    module = load_state_module()
    digest = module.bundle_identity(TOMBSTONE_BUNDLE)

    matches = matching_rows(state, digest)
    if len(matches) > 1:
        raise TwoTaskImportError(
            f"{len(matches)} tombstone rows already match Run 1 "
            f"(bundle {digest[:12]}…); the cell charged it more than once and "
            "must be inspected by hand")
    if matches and matches[0]["status"] != "running":
        problems = tombstone_errors(matches[0], digest)
        if problems:
            raise TwoTaskImportError(
                "the Run 1 tombstone is no longer the charged lost attempt it "
                "recorded: " + "; ".join(problems))
        return matches[0]

    if IMPORT_SEED not in state.seeds:
        raise TwoTaskImportError(
            f"seed {IMPORT_SEED} carries the lost attempt but is outside this "
            f"cell's development partition {list(state.seeds)}")

    record = matches[0] if matches else state.begin_trial(
        IMPORT_PHASE, IMPORT_SEED, TOMBSTONE_BUNDLE, dict(TOMBSTONE_SOURCES))

    directory = Path(state.task_dir) / record["directory"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "LOST-EVIDENCE.md").write_text(TOMBSTONE_README)

    # `exit_code=None` is the honest value: Run 1's summary.json was deleted and
    # the process exit status is unknown. The diagnostic_error branch is what
    # sets sandbox_rc=1 / reward=0.0 / task_completed=0, so this row can never
    # be read as a success by any consumer of the ledger.
    state.finish_trial(record, result=None, exit_code=None,
                       diagnostic_error=dict(TOMBSTONE_DIAGNOSTIC_ERROR))
    record["lost_evidence"] = dict(TOMBSTONE_PROVENANCE)
    state.save()

    problems = tombstone_errors(record, digest)
    if problems:
        raise TwoTaskImportError(
            "the Run 1 tombstone was written incorrectly: " + "; ".join(problems))
    return record


def _normalize_sources(state, record: dict) -> dict:
    expected = str(
        Path(state.task_dir) / record["directory"] / POLICY_REL)
    if record.get("sources") != {"policy": expected}:
        record["sources"] = {"policy": expected}
        state.save()
    return record


def import_diagnostic(state, source_dir: Path) -> dict:
    """Charge both pre-executed attempts to seed 51, exactly once each.

    Run 1 is charged first, as a tombstone (`import_tombstone`), so it takes
    attempt 1 — the number it actually had. Run 2's surviving evidence is then
    imported as attempt 2. The tombstone is written unconditionally on every
    call, including the re-import no-op path, so no caller can obtain the
    imported row while skipping the accounting for the attempt that preceded it.

    Returns the Run 2 row. Idempotent: a crash during import leaves a reserved
    row identified by (phase, seed, bundle_digest). Re-running finishes that
    row; a second, different import is refused.
    """
    # Attempt 1 before attempt 2. Never conditional, never skipped.
    import_tombstone(state)

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


# ---- accounting proof --------------------------------------------------------

def verify_accounting(state) -> dict:
    """Prove the ledger's own view of seed 51 is two charged attempts, one left.

    Reads only what is on disk. Raises rather than returning a false negative:
    this is the check that stands between an under-counted ledger and a fourth
    real execution on a three-attempt seed.
    """
    module = load_state_module()
    problems = []

    tombstones = tombstone_rows(state)
    imported = imported_rows(state)
    charged = state.charged(IMPORT_SEED)

    if len(tombstones) != 1:
        problems.append(f"{len(tombstones)} tombstone rows (expected 1)")
    if len(imported) != 1:
        problems.append(f"{len(imported)} imported rows (expected 1)")
    if tombstones and imported and tombstones[0] is imported[0]:
        problems.append("the tombstone and the imported row are the same row")
    if len(charged) != CHARGED_ATTEMPTS:
        problems.append(
            f"seed {IMPORT_SEED} carries {len(charged)} charged rows "
            f"(expected {CHARGED_ATTEMPTS})")
    if {r.get("attempt") for r in charged} != {1, 2}:
        problems.append(
            f"charged attempt numbers are {sorted(r.get('attempt') for r in charged)} "
            "(expected 1 and 2, in the order the runs happened)")

    used = state.attempts_used(IMPORT_SEED)
    remaining = state.budget_remaining(IMPORT_SEED)
    if used != CHARGED_ATTEMPTS:
        problems.append(f"attempts_used({IMPORT_SEED}) == {used} "
                        f"(expected {CHARGED_ATTEMPTS})")
    expected_remaining = module.ATTEMPT_LIMIT - CHARGED_ATTEMPTS
    if remaining != expected_remaining:
        problems.append(f"budget_remaining({IMPORT_SEED}) == {remaining} "
                        f"(expected {expected_remaining})")

    untouched = {s: state.budget_remaining(s)
                 for s in state.seeds if s != IMPORT_SEED}
    spent = {s: v for s, v in untouched.items() if v != module.ATTEMPT_LIMIT}
    if spent:
        problems.append(f"seeds other than {IMPORT_SEED} already spent budget: {spent}")

    if state.candidates():
        problems.append("a diagnostic row leaked into the tested bundles")
    outcome = state.outcome(IMPORT_SEED)
    if outcome["passed"] or outcome["has_graded_evidence"]:
        problems.append(
            f"seed {IMPORT_SEED} reads as graded/passing off two diagnostic rows")

    if problems:
        raise TwoTaskImportError(
            "ledger accounting for seed "
            f"{IMPORT_SEED} is wrong: " + "; ".join(problems))

    return {
        "seed": IMPORT_SEED,
        "attempt_limit": module.ATTEMPT_LIMIT,
        "attempts_used": used,
        "budget_remaining": remaining,
        "charged_rows": [
            {k: r.get(k) for k in ("attempt", "phase", "status", "charged",
                                   "sandbox_rc", "task_completed",
                                   "bundle_sha256", "directory")}
            for r in sorted(charged, key=lambda r: r.get("attempt", 0))],
        "tombstone_attempt": tombstones[0]["attempt"],
        "imported_attempt": imported[0]["attempt"],
        "other_seeds_remaining": {str(s): v for s, v in sorted(untouched.items())},
        "diagnostic_in_tested_bundles": False,
        "reads_as_passing": False,
    }


def prove_attempt_limit(task_dir: Path) -> dict:
    """Demonstrate on a throwaway copy that a fourth execution on seed 51 is refused.

    The real ledger is never touched: only `development_state.json` is copied
    into a temporary directory, and the probe rows are created and discarded
    there. Returns what happened, including the exact refusal message.

    The claim being proved is not "the ledger says 1 remaining" — that is just
    arithmetic over its own rows. It is that `begin_trial`, the gate every real
    execution passes through, admits exactly one more attempt and then stops.
    """
    module = load_state_module()
    source = Path(task_dir).resolve() / "development_state.json"
    if not source.is_file():
        raise TwoTaskImportError(f"no ledger to probe: {source}")

    scratch = Path(tempfile.mkdtemp(prefix="two-task-attempt-limit-"))
    try:
        shutil.copy2(source, scratch / "development_state.json")
        probe = open_state(scratch)
        if probe.attempts_used(IMPORT_SEED) != CHARGED_ATTEMPTS:
            raise TwoTaskImportError(
                f"copy does not carry {CHARGED_ATTEMPTS} charged attempts; "
                f"found {probe.attempts_used(IMPORT_SEED)}")

        third = probe.begin_trial(
            IMPORT_PHASE, IMPORT_SEED,
            {"policy": hashlib.sha256(b"attempt-limit-probe-3").hexdigest()},
            {"policy": "probe"})
        probe.finish_trial(third, result=None, exit_code=1,
                           error="attempt-limit probe; never executed")
        third_admitted = {"attempt": third["attempt"],
                          "budget_after": probe.budget_remaining(IMPORT_SEED)}

        refusal = None
        try:
            probe.begin_trial(
                IMPORT_PHASE, IMPORT_SEED,
                {"policy": hashlib.sha256(b"attempt-limit-probe-4").hexdigest()},
                {"policy": "probe"})
        except module.ProtocolError as error:
            refusal = str(error)
        if refusal is None:
            raise TwoTaskImportError(
                "a FOURTH execution on seed 51 was admitted; the ledger is "
                "under-counting the two pre-solver attempts")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return {
        "probed_on": "throwaway copy of development_state.json",
        "real_ledger_modified": False,
        "attempts_used_before_probe": CHARGED_ATTEMPTS,
        "third_attempt_admitted": third_admitted,
        "fourth_attempt_refused": True,
        "refusal_message": refusal,
    }


__all__ = [
    "TwoTaskImportError", "FullImportError", "PilotImportError",
    "CHARGED_ATTEMPTS", "IMPORT_IDENTITY", "IMPORT_PHASE", "IMPORT_SEED",
    "PINNED_DIGEST", "PINNED_HASHES", "POLICY_REL",
    "SOURCE_DIR", "SOURCE_REL", "TRIAL_REL",
    "TOMBSTONE_BUNDLE", "TOMBSTONE_IDENTITY", "TOMBSTONE_PREIMAGE",
    "TOMBSTONE_POLICY_DIGEST", "TOMBSTONE_PROVENANCE",
    "copy_evidence", "expected_bundle", "file_hash", "identity_errors",
    "import_diagnostic", "import_tombstone", "imported_rows",
    "load_state_module", "matching_rows", "open_state", "prove_attempt_limit",
    "provenance", "read_summary", "source_digest", "tombstone_errors",
    "tombstone_rows", "verify_accounting", "verify_tree",
]
