"""Opt-in repair continuation of one incomplete streaming Fix Loop cell.

Schema `aspire-streaming-repair-continuation-v1` (docs/streaming-repair-continuation.md).
A continuation is NOT an unchanged `--resume`: the parent ran under an older
runtime, its deadline and outcomes are immutable history, and the new phase has
its own identity, ledger and once-recorded clock. Only a case that declares
`continuation` gets any of this; every other case is untouched.

The case binds a read-only copy of the parent's OWN `development_state.json` by
sha256. The importer reads that one file and nothing else: no sibling cell, no
held-out directory, no scan. It refuses a wrong cell, changed seeds, budgets,
model or task, held-out use, running rows, retry overflow, a finalized parent
and nested continuations.

Accounting across the two phases:

* Charged attempts are cumulative per seed: every parent row with
  `spends_retry: true` counts once, whatever its status (diagnostic, crash,
  timeout, infrastructure error or interrupted stop). Aliases and the snapshot
  never did and still do not. Nothing is refunded.
* Parent initial and graded coverage establish past seed work; seeds the
  parent never ran initially are owed their initial run first, with the
  parent's own initial program (policy + authored config) under the new runtime.
* Parent successes stay visible as historical. They never make a bundle
  selectable and never mark a corrected-runtime success: candidates, selection,
  final coverage and the held-out freeze read new `trials` rows only.
"""
from __future__ import annotations

import json
import os
import re
import stat
from collections import Counter
from pathlib import Path

from native_world_fixloop_state import (PHASES, RESOLVED_STATUSES, RETRY_LIMIT, RETRY_PHASES,
                                       NativeWorldState, ProtocolError, code_hash)

SCHEMA = "aspire-streaming-repair-continuation-v1"
CONTRACT_KEYS = {"schema", "parent", "authorization"}
PARENT_KEYS = {"cell", "development_state", "development_state_sha256"}
# Identity fields a continuation must share with its parent. Paths, the protocol
# and the engineered runtime digests may (and normally do) differ; they are
# recorded as provenance instead.
SAME_AS_PARENT = ("harness", "harness_version", "profile", "protocol_id", "method",
                  "method_differences", "cell", "repeat", "suite", "task", "condition",
                  "development_gate", "strategy_md", "prearranged_inventory", "model", "effort",
                  "context_tokens", "max_output_tokens", "dev_seeds", "retry_limit", "max_steps",
                  "trial_timeout", "development_watchdog_seconds", "evaluation_watchdog_seconds",
                  "dependency_evidence", "environment_version", "camera")
# Compared identity records of which only a file LOCATION may move. Content
# hashes stay bound: the initial-state manifest sha256, and the model-service
# launch manifest sha256 with its verified endpoint, served model and capacity.
# The endpoint is therefore pinned too (the manifest records it), and with it
# the online review route.
RELOCATABLE = {"online_world_review": (), "initial_states": ("manifest",),
               "model_service": ("manifest.path",)}
HISTORY_KEYS = ("trials", "rejected", "aliases", "blockers", "selected", "model_served", "usage")


class ContinuationRefused(ProtocolError):
    """The declared continuation contract or its parent ledger is not admissible."""


def contract_errors(case: dict) -> list[str]:
    """Static shape checks, run with validate_case before anything is read."""
    contract = case.get("continuation")
    if not isinstance(contract, dict) or set(contract) != CONTRACT_KEYS:
        return [f"continuation must declare exactly {sorted(CONTRACT_KEYS)}"]
    errors = []
    if contract["schema"] != SCHEMA:
        errors.append(f"continuation.schema must be {SCHEMA}")
    if not isinstance(contract["authorization"], str) or not contract["authorization"].strip():
        errors.append("continuation.authorization must record who authorized it and why")
    parent = contract["parent"]
    if not isinstance(parent, dict) or set(parent) != PARENT_KEYS:
        return errors + [f"continuation.parent must declare exactly {sorted(PARENT_KEYS)}"]
    if parent["cell"] != case.get("id"):
        errors.append("a continuation continues this cell's OWN parent: parent.cell must equal id")
    if not re.fullmatch(r"[a-f0-9]{64}", str(parent["development_state_sha256"])):
        errors.append("parent.development_state_sha256 must be a lowercase sha256")
    ledger = Path(str(parent["development_state"]))
    control = Path(str(case.get("control", ""))).resolve()
    if not ledger.is_absolute() or ledger.name != "development_state.json":
        errors.append("parent.development_state must be an absolute path to a development_state.json")
    resolved = ledger.resolve()
    if not resolved.is_relative_to(control) or resolved.is_relative_to(control / "cell"):
        errors.append("the parent copy lives in this cell's own control directory, outside "
                      "the new task directory <control>/cell")
    if any("heldout" in part.lower() for part in resolved.parts) or resolved.is_relative_to("/root"):
        errors.append("the parent copy may not sit under a held-out or /root path")
    return errors


def parent_path(case: dict) -> Path:
    return Path(case["continuation"]["parent"]["development_state"]).resolve()


def read_parent(case: dict) -> tuple[dict, str]:
    """Read and hash the one declared parent ledger; nothing else is opened."""
    path = parent_path(case)
    if not path.is_file():
        raise ContinuationRefused(f"parent development ledger copy is missing: {path}")
    raw = path.read_bytes()
    actual = code_hash_bytes(raw)
    if actual != case["continuation"]["parent"]["development_state_sha256"]:
        raise ContinuationRefused("parent development ledger does not match its bound sha256")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContinuationRefused(f"parent development ledger is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ContinuationRefused("parent development ledger is not a JSON object")
    return data, actual


def code_hash_bytes(raw: bytes) -> str:
    import hashlib
    return hashlib.sha256(raw).hexdigest()


def import_permission_errors(case: dict, uid: int | None = None) -> list[str]:
    """At import: the copy is read-only and owned by this cell's own user."""
    info = parent_path(case).stat()
    uid = os.geteuid() if uid is None else uid
    errors = []
    if info.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        errors.append("the parent ledger copy must be read-only (no write permission bits)")
    if info.st_mode & stat.S_IROTH:
        errors.append("the parent ledger copy must not be world-readable; it is this cell's private copy")
    if info.st_uid != uid:
        errors.append("the parent ledger copy must be owned by this cell's own user")
    return errors


def _comparable(identity: dict, key: str):
    value = identity.get(key)
    if not isinstance(value, dict) or key not in RELOCATABLE:
        return value
    value = json.loads(json.dumps(value))
    for dotted in RELOCATABLE[key]:
        *parents, leaf = dotted.split(".")
        node = value
        for name in parents:
            node = node.get(name) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(leaf, None)
    return value


def parent_errors(parent: dict, identity: dict) -> list[str]:
    """Is this parent an incomplete development ledger of the same cell and settings?"""
    errors = []
    if parent.get("version") != NativeWorldState.VERSION:
        errors.append(f"parent ledger version must be {NativeWorldState.VERSION}")
    old = parent.get("identity")
    if not isinstance(old, dict):
        return errors + ["parent ledger has no identity"]
    if "continuation" in old:
        errors.append("the parent is itself a continuation; chained continuations are not supported")
    changed = [k for k in (*SAME_AS_PARENT, *RELOCATABLE)
               if _comparable(old, k) != _comparable(identity, k)]
    if changed:
        errors.append(f"parent cell, seeds, model or budget settings differ: {sorted(changed)}")
    if parent.get("stage1_complete") is not False:
        errors.append("the parent is finalized (or does not say it is not); only an incomplete "
                      "development phase can be continued")
    if not isinstance((parent.get("phase_clock") or {}).get("development"), dict):
        errors.append("the parent has no recorded development clock")
    if any("heldout" in str(key).lower() for key in parent):
        errors.append("the parent ledger records held-out use")
    trials = parent.get("trials")
    if not isinstance(trials, list):
        return errors + ["parent ledger has no trial list"]
    seeds = identity["dev_seeds"]
    directories = {r.get("directory") for r in trials if isinstance(r, dict)}
    for row in trials:
        where = f"parent row {row.get('directory') if isinstance(row, dict) else row!r}"
        if not isinstance(row, dict) or row.get("phase") not in PHASES:
            errors.append(f"{where} has no known phase")
            continue
        if row.get("seed") not in seeds:
            errors.append(f"{where} ran seed {row.get('seed')}, outside development seeds "
                          "(held-out seeds are never part of a continuation)")
        if row.get("status") == "running":
            errors.append(f"{where} is still running; settle it in the parent before copying")
        if not isinstance(row.get("spends_retry"), bool):
            errors.append(f"{where} does not say whether it spent a retry")
        elif row.get("alias_of"):
            if row["spends_retry"] or row.get("alias_of") not in directories:
                errors.append(f"{where} is an alias that spends a retry or names no parent row")
        elif row["phase"] in RETRY_PHASES and (not row["spends_retry"] or not row.get("executed", True)):
            errors.append(f"{where} is an admitted simulator attempt that does not spend its retry")
        elif row["phase"] == "snapshot" and row["spends_retry"]:
            errors.append(f"{where} is a snapshot that spends a retry")
    for key in ("rejected", "blockers"):
        if any(isinstance(r, dict) and r.get("seed") not in seeds for r in parent.get(key) or []):
            errors.append(f"parent {key} name a seed outside development seeds")
    charged = Counter(r["seed"] for r in trials if isinstance(r, dict) and r.get("spends_retry") is True)
    over = sorted(s for s, n in charged.items() if n > RETRY_LIMIT)
    if over:
        errors.append(f"parent seeds {over} already exceed {RETRY_LIMIT} charged attempts")
    return errors


def provenance(case: dict, identity: dict) -> dict:
    """The continuation block of the NEW identity: every resume must reproduce it."""
    parent, sha = read_parent(case)
    problems = parent_errors(parent, identity)
    if problems:
        raise ContinuationRefused("; ".join(problems))
    old = parent["identity"]
    contract = case["continuation"]
    old_world = (old.get("engineered_world") or {}).get("digest")
    return {"schema": SCHEMA, "kind": "repair continuation (not an unchanged resume)",
            "authorization": contract["authorization"], "parent_cell": old["cell"],
            "parent_development_state": str(parent_path(case)),
            "parent_development_state_sha256": sha,
            "parent_identity_sha256": code_hash(json.dumps(old, sort_keys=True)),
            "parent_engineered_world_digest": old_world,
            "parent_protocol_sha256": old.get("protocol_sha256"),
            "parent_runtime_manifest_sha256": old.get("runtime_manifest_sha256"),
            "parent_phase_clock": parent["phase_clock"],
            "runtime_changed": old_world != identity["engineered_world"]["digest"]}


def historical_record(parent: dict, sha: str) -> dict:
    """Parent evidence, verbatim, kept apart from the new phase's `trials`."""
    history = {"identity": parent["identity"], "phase_clock": parent["phase_clock"],
               **{k: parent.get(k, [] if k in ("trials", "rejected", "aliases", "blockers") else None)
                  for k in HISTORY_KEYS}}
    return {"parent_development_state_sha256": sha, "historical": history,
            "historical_sha256": code_hash(json.dumps(history, sort_keys=True))}


class ContinuationMixin:
    """Ledger rules for a continuation; mixed in before StreamingFixLoopState.

    Only seed accounting, coverage and historical reporting change. Candidates,
    `executed()`, `graded()`, selection and final coverage are inherited and read
    the new phase's `trials` only, so a parent run can never be a new candidate.
    """

    def __init__(self, task_dir: Path, identity: dict, *, parent: dict, parent_sha256: str,
                 resume: bool = False, **kwargs):
        imported = historical_record(parent, parent_sha256)
        # Written with the ledger's very first save, so no ledger exists without it.
        self._pending_import = None if resume else {
            "schema": SCHEMA, "provenance": identity["continuation"], **imported}
        super().__init__(task_dir, identity, resume=resume, **kwargs)
        self._pending_import = None
        block = self.data.get("continuation") or {}
        if not resume:
            block["imported_at"] = self.data["phase_clock"]["development"]["started_at"]
            self.save()
        elif (block.get("historical") != imported["historical"]
              or block.get("historical_sha256") != code_hash(json.dumps(block.get("historical"), sort_keys=True))
              or block.get("provenance") != identity["continuation"]):
            raise ContinuationRefused("the ledger's historical evidence differs from its bound parent")

    def save(self) -> None:
        if getattr(self, "_pending_import", None) is not None:
            self.data.setdefault("continuation", self._pending_import)
        super().save()

    # ---- historical view -----------------------------------------------------

    def historical_trials(self) -> list[dict]:
        block = self.data.get("continuation") or getattr(self, "_pending_import", None) or {}
        return (block.get("historical") or {}).get("trials", [])

    def historical_rows(self, phase: str | None = None, seed: int | None = None) -> list[dict]:
        return [r for r in self.historical_trials()
                if (phase is None or r["phase"] == phase) and (seed is None or r["seed"] == seed)]

    def historical_graded(self, seed: int | None = None) -> list[dict]:
        """Parent executions that graded a parent candidate (aliases and diagnostics excluded)."""
        return [r for r in self.historical_rows(seed=seed)
                if r["phase"] in RETRY_PHASES and r["phase"] != "diagnostic"
                and r["status"] in RESOLVED_STATUSES and r.get("executed", True)
                and not r.get("alias_of")]

    def historical_successes(self) -> list[dict]:
        return [{"seed": r["seed"], "phase": r["phase"], "directory": r["directory"],
                 "bundle_sha256": r.get("bundle_sha256"), "runtime": "parent",
                 "selection_evidence": False, "requires_corrected_runtime_revalidation": True}
                for r in self.historical_graded() if self.passed(r)]

    def historical_initial_anchor(self) -> dict | None:
        """The parent's own initial program: authored policy and streaming config digests."""
        rows = self.historical_rows("initial")
        if not rows:
            return None
        row = rows[0]
        if row.get("alias_of"):
            row = next(r for r in self.historical_trials() if r["directory"] == row["alias_of"])
        return {"directory": row["directory"], "policy": row["bundle"]["policy"],
                "authored_streaming_config_sha256": (row.get("bundle_components") or {}).get(
                    "authored_streaming_config_sha256")}

    # ---- accounting ----------------------------------------------------------

    def records(self, phase: str | None = None, seed: int | None = None) -> list[dict]:
        """New rows, except that the once-per-cell scene snapshot includes the parent's.

        The parent's public snapshot is this cell's snapshot: it satisfies the
        initial prerequisite and no second one is admitted or invented.
        """
        rows = super().records(phase, seed)
        if phase == "snapshot":
            rows = [*self.historical_rows("snapshot", seed), *rows]
        return rows

    def retries(self, seed: int) -> list[dict]:
        """Cumulative charged attempts: parent rows first, then this phase's."""
        return [*(r for r in self.historical_trials() if r["seed"] == seed and r["spends_retry"]),
                *super().retries(seed)]

    def has_initial_evidence(self, seed: int) -> bool:
        return super().has_initial_evidence(seed) or any(
            r["status"] in RESOLVED_STATUSES for r in self.historical_rows("initial", seed))

    def has_graded_evidence(self, seed: int) -> bool:
        return super().has_graded_evidence(seed) or bool(self.historical_graded(seed))

    def validate_admission(self, phase: str, seed: int, digest: str) -> None:
        super().validate_admission(phase, seed, digest)
        if phase == "smoke" and (self.historical_rows("initial") or any(
                r.get("sandbox_rc") == 0 for r in self.historical_rows("smoke"))):
            raise ProtocolError("the parent already ran its smoke/initial batch; owed seeds run "
                                "`initial`, covered seeds `repair`")

    def validate_bundle(self, phase: str, bundle: dict, components: dict) -> None:
        """Owed initial coverage runs the parent's initial program under the new runtime.

        Only the authored policy and authored streaming config are compared; the
        bundle's world digest is the corrected runtime's and is never compared
        with the parent's. The new initial bundle is then frozen as usual.
        """
        anchor = self.historical_initial_anchor() if phase == "initial" else None
        if anchor and (bundle["policy"] != anchor["policy"] or (
                anchor["authored_streaming_config_sha256"] is not None
                and components.get("authored_streaming_config_sha256")
                != anchor["authored_streaming_config_sha256"])):
            raise ProtocolError(
                "owed initial coverage must run the parent's own initial program (its policy and "
                f"authored streaming config, see {anchor['directory']}) under the corrected "
                "runtime; changes belong to `repair`")

    def alias_smoke_as_initial(self, seed: int) -> dict:
        if self.has_initial_evidence(seed):
            raise ProtocolError(f"seed {seed} already has initial evidence from the parent")
        return super().alias_smoke_as_initial(seed)

    # ---- evidence ------------------------------------------------------------

    def outcome(self, seed: int) -> dict:
        row = super().outcome(seed)
        history = self.historical_graded(seed)
        historical_pass = any(self.passed(r) for r in history)
        charged = sum(1 for r in self.historical_trials() if r["seed"] == seed and r["spends_retry"])
        initial = self.has_initial_evidence(seed)
        # `passed`, `passing_phases` and `initial_passed` stay corrected-runtime only.
        row.update(
            has_initial_evidence=initial, has_graded_evidence=self.has_graded_evidence(seed),
            needs_repair=bool(initial and not row["passed"] and row["retries_remaining"] > 0),
            new_retries_used=row["retries_used"] - charged,
            historical={"retries_used": charged,
                        "initial_evidence": any(r["status"] in RESOLVED_STATUSES
                                                for r in self.historical_rows("initial", seed)),
                        "graded_executions": len(history), "passed": historical_pass,
                        "passing_phases": sorted({r["phase"] for r in history if self.passed(r)}),
                        "statuses": dict(Counter(r["status"] for r in self.historical_rows(seed=seed)))},
            historical_success_requires_revalidation=historical_pass and not row["passed"])
        return row

    def continuation_summary(self) -> dict:
        block = self.data["continuation"]
        history = self.historical_trials()
        charged = [r for r in history if r["spends_retry"]]
        new = [r for r in self.data["trials"] if r["spends_retry"]]
        return {
            "schema": SCHEMA, "provenance": block["provenance"],
            # Read-only copy of the parent's own task directory: code, reports,
            # snapshot images and attempt evidence, all historical inputs.
            "parent_task_dir": str(Path(block["provenance"]["parent_development_state"]).parent),
            "historical_sha256": block["historical_sha256"], "imported_at": block.get("imported_at"),
            "historical_phase_clock": block["historical"]["phase_clock"],
            "historical_selected": block["historical"].get("selected"),
            "historical_successes": self.historical_successes(),
            "owed_initial_seeds": [s for s in self.seeds if not self.has_initial_evidence(s)],
            "initial_anchor": self.historical_initial_anchor(),
            "execution_totals": {
                "historical": {"charged_attempts": len(charged), "rows": len(history),
                               "graded_executions": len(self.historical_graded()),
                               "aliases": sum(bool(r.get("alias_of")) for r in history),
                               "by_status": dict(Counter(r["status"] for r in history))},
                "new": {"charged_attempts": len(new), "rows": len(self.data["trials"]),
                        "graded_executions": len(self.graded()),
                        "by_status": dict(Counter(r["status"] for r in self.data["trials"]))},
                "cumulative_charged_attempts": len(charged) + len(new),
                "charged_attempts_limit": RETRY_LIMIT * len(self.seeds)},
            "retries_used_per_seed": {str(s): {"historical": sum(r["seed"] == s for r in charged),
                                               "new": sum(r["seed"] == s for r in new)}
                                      for s in self.seeds},
            "selection_rule": "candidates, selection and final coverage use corrected-runtime "
                              "executions only; parent successes are historical"}

    def progress(self) -> dict:
        progress = super().progress()
        progress.update(continuation=self.continuation_summary(),
                        snapshot_source="parent (historical)" if not super().records("snapshot")
                        and progress["snapshot_complete"] else "this phase")
        return progress

    def budget(self) -> dict:
        budget = super().budget()
        totals = self.continuation_summary()["execution_totals"]
        budget.update(historical_charged_attempts=totals["historical"]["charged_attempts"],
                      new_charged_attempts=totals["new"]["charged_attempts"],
                      historical_phase_clock=self.data["continuation"]["historical"]["phase_clock"])
        return budget
