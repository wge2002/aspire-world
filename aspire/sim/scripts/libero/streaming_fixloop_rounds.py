"""Opt-in supervised repair study: three outer-reviewed development rounds.

Schema `aspire-supervised-repair-v1` (docs/streaming-supervised-repair.md). A
case without `supervised_repair` is untouched. The study is a NEW, separately
registered development phase of one cell: fresh ledger, identity and
once-recorded 50,400 s clock. Its own earlier development inputs are bound
read-only by a prior-input manifest (`aspire-repair-prior-input-v1`) for
provenance and for the solver to read; nothing from them is imported into the
new trials, budget or candidates.

Execution budget, enforced at admission:

* rounds 1..3; in round r every development seed 51-65 gets at most ONE charged
  attempt (smoke/initial/repair/diagnostic; timeouts, crashes and
  infrastructure errors stay charged; aliases and the snapshot are free). The
  per-seed total therefore never exceeds 3, and a failed seed cannot borrow
  the next round's attempt.
* a round is complete only when all 15 seeds have a resolved charged row of
  that round and nothing is running. The outer runner then closes it, records
  the per-seed public development summary and an immutable evidence digest,
  and (rounds 1 and 2) waits for a root-owned outer review bound to that
  digest. Round 3 closes development without a review.
* a review decides continue (next round), freeze (end development now) or stop
  (end the study, no held-out). A timeout is never a review.

Gate: the selected bundle must itself have at least one oracle success in this
study's own executions; another bundle's or a historical success does not
count. With no new success at all the cell is `development_failed` and no
held-out evaluation is started. The gate is enforced by the ledger (`select`,
`check`, `finalize`) and again by the held-out freeze.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from native_world_fixloop_state import RESOLVED_STATUSES, ProtocolError, code_hash

SCHEMA = "aspire-supervised-repair-v1"
PRIOR_SCHEMA = "aspire-repair-prior-input-v1"
INDEPENDENT_SCHEMA = "aspire-independent-round-start-v1"
REVIEW_SCHEMA = "aspire-supervised-repair-review-v1"
ROUNDS = 3
ATTEMPTS_PER_SEED_PER_ROUND = 1
DECISIONS = ("continue", "freeze", "stop")
CONTRACT_KEYS = {"schema", "study_id", "authorization", "prior_input", "rounds", "round_seeds",
                 "attempts_per_seed_per_round", "require_current_selected_success", "review_dir"}
OPTIONAL_KEYS = {"review_poll_seconds", "boundary_grace_seconds", "continuation", "autonomous_review",
                 "independent_start", "repair_start", "closeout"}
# Opt-in round closeout (docs/streaming-supervised-repair.md#round-closeout).
CLOSEOUT_SCHEMA = "aspire-round-closeout-v1"
CLOSEOUT_LIMITS = {"report_grace_seconds": (600, 7200), "session_timeout_seconds": (300, 7200),
                   "max_sessions": (1, 3)}
PRIOR_KEYS = {"manifest", "manifest_sha256"}
PRIOR_FIELDS = {"schema", "study_id", "cell", "suite", "task", "parent_cell", "parent_control",
                "historical_attempts", "new_budget_separate", "seed_partition",
                "no_heldout_inputs", "files"}
REVIEW_KEYS = {"schema", "study_id", "cell", "round", "decision", "analysis",
               "next_round_guidance", "evidence_sha256", "reviewer", "created_at"}
REVIEW_OWNER_UID = 0  # root; the solver's own user can never own an approval
DEFAULT_POLL, DEFAULT_GRACE = 30, 1800


class SupervisedRefused(ProtocolError):
    """The supervised repair contract, its prior input or a review is not admissible."""


def contract(case: dict) -> dict:
    return case["supervised_repair"]


def review_path(case: dict, round_: int) -> Path:
    return Path(contract(case)["review_dir"]).resolve() / f"round_{round_}.review.json"


def poll_seconds(case: dict) -> int:
    return int(contract(case).get("review_poll_seconds", DEFAULT_POLL))


def grace_seconds(case: dict) -> int:
    return int(contract(case).get("boundary_grace_seconds", DEFAULT_GRACE))


def closeout_config(case: dict) -> dict | None:
    """The explicit closeout block, or None: absent keeps the boundary-grace stop."""
    return (case.get("supervised_repair") or {}).get("closeout")


def closeout_errors(case: dict) -> list[str]:
    c = closeout_config(case)
    if c is None:
        return []
    if not isinstance(c, dict) or set(c) != {"schema", *CLOSEOUT_LIMITS}:
        return [f"supervised_repair.closeout must declare exactly {sorted({'schema', *CLOSEOUT_LIMITS})}"]
    errors = [] if c["schema"] == CLOSEOUT_SCHEMA else [f"supervised_repair.closeout.schema must be {CLOSEOUT_SCHEMA}"]
    for key, (low, high) in CLOSEOUT_LIMITS.items():
        if type(c[key]) is not int or not low <= c[key] <= high:
            errors.append(f"supervised_repair.closeout.{key} must be an integer in [{low}, {high}]")
    return errors


def contract_errors(case: dict, dev_seeds: list[int]) -> list[str]:
    """Static shape of the contract, checked with validate_case before anything is read."""
    c = case.get("supervised_repair")
    if not isinstance(c, dict) or not CONTRACT_KEYS <= set(c) or set(c) - CONTRACT_KEYS - OPTIONAL_KEYS:
        return [f"supervised_repair must declare exactly {sorted(CONTRACT_KEYS)} "
                f"(optional {sorted(OPTIONAL_KEYS)})"]
    errors = []
    if "independent_start" in c and c["independent_start"] is not True:
        errors.append("independent_start, when declared, must be true")
    if c.get("independent_start") and "continuation" in c:
        errors.append("independent_start cannot import a continuation")
    if "repair_start" in c:
        if c["repair_start"] is not True:
            errors.append("repair_start, when declared, must be true")
        if "continuation" in c or "independent_start" in c:
            errors.append("repair_start is separate from continuation and independent_start")
        if "autonomous_review" not in c:
            errors.append("repair_start requires autonomous_review")
    if "continuation" in case:
        errors.append("supervised_repair and continuation are separate studies; declare one")
    if c["schema"] != SCHEMA:
        errors.append(f"supervised_repair.schema must be {SCHEMA}")
    for key in ("study_id", "authorization"):
        if not isinstance(c[key], str) or not c[key].strip():
            errors.append(f"supervised_repair.{key} must be a nonempty string")
    if c["rounds"] != ROUNDS or c["round_seeds"] != dev_seeds:
        errors.append(f"supervised_repair runs exactly {ROUNDS} rounds over seeds 51-65")
    if c["attempts_per_seed_per_round"] != ATTEMPTS_PER_SEED_PER_ROUND:
        errors.append("supervised_repair allows exactly one charged attempt per seed per round")
    if c["require_current_selected_success"] is not True:
        errors.append("supervised_repair.require_current_selected_success must be true")
    if "continuation" in c or "autonomous_review" in c:
        import streaming_fixloop_autonomous as autonomous
        errors += autonomous.contract_errors(case)
    errors += closeout_errors(case)
    for key, low, high in (("review_poll_seconds", 1, 600), ("boundary_grace_seconds", 0, 7200)):
        value = c.get(key)
        if value is not None and (type(value) is not int or not low <= value <= high):
            errors.append(f"supervised_repair.{key} must be an integer in [{low}, {high}]")
    prior = c["prior_input"]
    if not isinstance(prior, dict) or set(prior) != PRIOR_KEYS:
        return errors + [f"supervised_repair.prior_input must declare exactly {sorted(PRIOR_KEYS)}"]
    if not re.fullmatch(r"[a-f0-9]{64}", str(prior["manifest_sha256"])):
        errors.append("prior_input.manifest_sha256 must be a lowercase sha256")
    control = Path(str(case.get("control", ""))).resolve()
    for name, raw in (("prior_input.manifest", prior["manifest"]), ("review_dir", c["review_dir"])):
        path = Path(str(raw))
        if not path.is_absolute():
            errors.append(f"supervised_repair.{name} must be absolute")
            continue
        path = path.resolve()
        if path.is_relative_to(control) or control.is_relative_to(path):
            errors.append(f"supervised_repair.{name} lives outside this cell's control directory")
        if any("heldout" in part.lower() for part in path.parts):
            errors.append(f"supervised_repair.{name} may not sit under a held-out path")
    review = Path(str(c["review_dir"])).resolve()
    manifest = Path(str(prior["manifest"])).resolve()
    if review == manifest.parent or manifest.is_relative_to(review):
        errors.append("the prior-input manifest and the outer review directory are separate")
    return errors


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_prior(case: dict, *, verify_files: bool) -> tuple[dict, str]:
    """Hash the bound prior-input manifest and check its declared scope.

    Only this cell's own manifest is opened. With `verify_files` (at `init`)
    every listed file is re-hashed; afterwards the manifest sha256 binds them.
    """
    prior = contract(case)["prior_input"]
    path = Path(prior["manifest"]).resolve()
    if not path.is_file():
        raise SupervisedRefused(f"prior-input manifest is missing: {path}")
    raw = path.read_bytes()
    if _sha(raw) != prior["manifest_sha256"]:
        raise SupervisedRefused("prior-input manifest does not match its bound sha256")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SupervisedRefused(f"prior-input manifest is not JSON: {exc}") from exc
    errors = []
    if not isinstance(data, dict) or set(data) != PRIOR_FIELDS:
        raise SupervisedRefused(f"prior-input manifest must declare exactly {sorted(PRIOR_FIELDS)}")
    independent = contract(case).get("independent_start") is True
    expected = {"schema": INDEPENDENT_SCHEMA if independent else PRIOR_SCHEMA,
                "study_id": contract(case)["study_id"], "cell": case["id"],
                "suite": case["suite"], "task": case["task"], "historical_attempts": 0 if independent else 45,
                "new_budget_separate": True, "seed_partition": "development51-65only",
                "no_heldout_inputs": True}
    errors += [f"prior manifest {k} must be {v!r}" for k, v in expected.items() if data.get(k) != v]
    for key in ("parent_cell", "parent_control"):
        if independent and data.get(key) is not None:
            errors.append(f"independent start may not bind {key}")
        elif not independent and (not isinstance(data.get(key), str) or not data[key].strip()):
            errors.append(f"prior manifest {key} must be a nonempty string")
    files = data.get("files")
    if independent and files != []:
        errors.append("independent start has no prior files")
        files = []
    elif not independent and (not isinstance(files, list) or not files):
        errors.append("prior manifest files must be a nonempty list")
        files = []
    root = path.parent
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "source", "sha256"}:
            errors.append("each prior file is exactly {path, source, sha256}")
            continue
        rel = Path(str(entry["path"]))
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            errors.append(f"prior file path must be relative to the manifest directory: {rel}")
            continue
        if any("heldout" in p.lower() or "EXPERIMENT_RESULTS" in p for p in rel.parts) \
                or "heldout" in str(entry["source"]).lower():
            errors.append(f"prior inputs exclude held-out and coordinator result files: {rel}")
            continue
        if not re.fullmatch(r"[a-f0-9]{64}", str(entry["sha256"])):
            errors.append(f"prior file {rel} needs a lowercase sha256")
            continue
        if verify_files:
            target = (root / rel).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                errors.append(f"prior file is missing or escapes the manifest directory: {rel}")
            elif file_sha256(target) != entry["sha256"]:
                errors.append(f"prior file {rel} does not match its recorded sha256")
    if not errors and contract(case).get("repair_start"):
        import streaming_fixloop_autonomous as autonomous
        errors += autonomous.repair_start_prior_errors(case, data, root, verify_files=verify_files)
    if errors:
        raise SupervisedRefused("; ".join(errors))
    return data, prior["manifest_sha256"]


def provenance(case: dict, *, verify_files: bool = False, identity: dict | None = None) -> dict:
    """The `supervised_repair` block of the identity; every resume reproduces it."""
    data, sha = read_prior(case, verify_files=verify_files)
    c = contract(case)
    result = {"schema": SCHEMA, "kind": "new supervised repair study (not a resume, no refund, "
                                      "no imported budget or candidates)",
            "study_id": c["study_id"], "authorization": c["authorization"],
            "rounds": ROUNDS, "round_seeds": c["round_seeds"],
            "attempts_per_seed_per_round": ATTEMPTS_PER_SEED_PER_ROUND,
            "require_current_selected_success": True,
            "review_dir": str(Path(c["review_dir"]).resolve()),
            "review_poll_seconds": poll_seconds(case),
            "boundary_grace_seconds": grace_seconds(case),
            "prior_input": {"manifest": str(Path(c["prior_input"]["manifest"]).resolve()),
                            "manifest_sha256": sha, "parent_cell": data["parent_cell"],
                            "historical_attempts": data["historical_attempts"],
                            "files": len(data["files"]), "imported_into_trials": False}}
    if "continuation" in c:
        import streaming_fixloop_autonomous as autonomous
        result.update(kind="explicit remaining-round repair phase; parent15 + at most30 additional",
                      continuation=autonomous.provenance(case, identity),
                      autonomous_review=c["autonomous_review"])
    elif c.get("independent_start"):
        result.update(kind="fresh independent three-round development; no prior evidence or imported attempts",
                      independent_start=True)
        if "autonomous_review" in c:
            result["autonomous_review"] = c["autonomous_review"]
    elif c.get("repair_start"):
        import streaming_fixloop_autonomous as autonomous
        result.update(autonomous.repair_start_provenance(case, data))
    if "closeout" in c:
        # Present only when opted in, so earlier identities reproduce exactly.
        result["closeout"] = {**c["closeout"], "method_difference": (
            "trial completion and closeout completion are separate round states; the round "
            "session may keep writing reports up to report_grace_seconds after its last trial; "
            "missing closeout material is completed by at most max_sessions restricted native "
            "sessions of this cell (no trial, no refund, no new policy) before close-round")}
    return result


# --- pure ledger views (shared by protocol, campaign, outer CLI) -------------------


def round_rows(data: dict, round_: int) -> list[dict]:
    return [r for r in data["trials"] if r.get("supervised_round") == round_ and r.get("spends_retry")]


def round_complete(data: dict, round_: int) -> bool:
    if any(r["status"] == "running" for r in data["trials"]):
        return False
    done = {r["seed"] for r in round_rows(data, round_) if r["status"] != "running"}
    return set(data["identity"]["dev_seeds"]) <= done


def passed_row(row: dict) -> bool:
    """A normal oracle success: complete, program exit 0, task completed; never diagnostic."""
    return (row.get("phase") in ("smoke", "initial", "repair") and row.get("status") == "complete"
            and row.get("sandbox_rc") == 0 and bool(row.get("task_completed"))
            and row.get("executed", True) and not row.get("alias_of")
            and not row.get("inherited_parent"))


def study_successes(data: dict) -> list[dict]:
    return [{"seed": r["seed"], "round": r.get("supervised_round"), "directory": r["directory"],
             "bundle_sha256": r["bundle_sha256"]} for r in data["trials"] if passed_row(r)]


def _attempt_files(task_dir: Path, directory: str) -> dict:
    root = task_dir / directory
    if not root.is_dir():
        return {}
    return {str(p.relative_to(root)): file_sha256(p) for p in sorted(root.rglob("*"))
            if p.is_file() and not p.is_symlink()}


def round_evidence(data: dict, task_dir: Path, round_: int) -> dict:
    """Per-seed public development summary plus immutable references for one round."""
    rows = sorted(round_rows(data, round_), key=lambda r: (r["seed"], r["directory"]))
    per_seed = {}
    for row in rows:
        per_seed[str(row["seed"])] = {
            "directory": row["directory"], "phase": row["phase"], "status": row["status"],
            "bundle_sha256": row["bundle_sha256"], "sandbox_rc": row.get("sandbox_rc"),
            "task_completed": row.get("task_completed"), "oracle_success": passed_row(row),
            "timed_out": bool(row.get("timed_out")), "error": (row.get("error") or "")[:500],
            "world_error_stage": (row.get("world_error") or {}).get("stage"),
            "graded": row["phase"] != "diagnostic" and row["status"] in RESOLVED_STATUSES}
    def evidence_root(row):
        if row.get("inherited_parent"):
            return Path(data["identity"]["supervised_repair"]["continuation"]["parent"]["ledger"]).parent
        return task_dir
    references = {row["directory"]: {"row_sha256": code_hash(json.dumps(row, sort_keys=True)),
                                     "files": _attempt_files(evidence_root(row), row["directory"])}
                  for row in rows}
    bound = {"cell": data["identity"]["cell"],
             "study_id": data["identity"]["supervised_repair"]["study_id"],
             "identity_sha256": code_hash(json.dumps(data["identity"], sort_keys=True)),
             "round": round_, "references": references}
    return {**bound, "evidence_sha256": code_hash(json.dumps(bound, sort_keys=True)),
            "per_seed": per_seed,
            "round_successes": [s for s in study_successes(data) if s["round"] == round_],
            "study_successes": study_successes(data),
            "selected": data.get("selected"),
            "charged_attempts_used": sum(1 for r in data["trials"] if r.get("spends_retry"))}


# --- outer review validation ------------------------------------------------------


def _owner_errors(path: Path, what: str, *, directory: bool) -> list[str]:
    try:
        info = path.lstat()
    except OSError as exc:
        return [f"{what} is unreadable: {exc}"]
    errors = []
    if stat.S_ISLNK(info.st_mode):
        errors.append(f"{what} may not be a symlink")
    if directory != stat.S_ISDIR(info.st_mode) or (not directory and not stat.S_ISREG(info.st_mode)):
        errors.append(f"{what} must be a {'directory' if directory else 'regular file'}")
    if info.st_uid != REVIEW_OWNER_UID:
        errors.append(f"{what} must be owned by the outer root user, not uid {info.st_uid}")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        errors.append(f"{what} must not be group- or world-writable")
    return errors


def review_errors(case: dict, data: dict, round_: int, path: Path) -> tuple[dict | None, str | None, list[str]]:
    """Is this the root-owned, solver-unwritable outer review of THIS cell's round?"""
    c = contract(case)
    path = Path(path)
    review_dir = Path(c["review_dir"]).resolve()
    errors = []
    if path.parent.resolve() != review_dir or path.name != f"round_{round_}.review.json":
        errors.append(f"the review of round {round_} is {review_path(case, round_)}")
    errors += _owner_errors(review_dir, "review directory", directory=True)
    errors += _owner_errors(review_dir.parent, "review directory parent", directory=True)
    errors += _owner_errors(path, "review file", directory=False)
    if os.geteuid() != REVIEW_OWNER_UID and any(os.access(p, os.W_OK) for p in (path, review_dir,
                                                                                  review_dir.parent)):
        errors.append("the review or its directory is writable by this (solver) user")
    if errors:
        return None, None, errors
    raw = path.read_bytes()
    try:
        review = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, _sha(raw), [f"review is not JSON: {exc}"]
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        return None, _sha(raw), [f"review must declare exactly {sorted(REVIEW_KEYS)}"]
    sup = data.get("supervised") or {}
    closed = next((r for r in sup.get("rounds", []) if r["round"] == round_), {})
    expected = {"schema": REVIEW_SCHEMA, "study_id": c["study_id"], "cell": case["id"],
                "round": round_, "evidence_sha256": closed.get("evidence_sha256")}
    errors += [f"review {k} is {review.get(k)!r}, expected {v!r}" for k, v in expected.items()
               if review.get(k) != v]
    if review.get("decision") not in DECISIONS:
        errors.append(f"review decision must be one of {DECISIONS}")
    if "continuation" in c and review.get("decision") == "freeze":
        errors.append("remaining rounds 2/3 must finish; early freeze is not authorized")
    if review.get("decision") == "continue" and round_ >= ROUNDS:
        errors.append("there is no round after the last one")
    for key in ("analysis", "next_round_guidance", "reviewer", "created_at"):
        if not isinstance(review.get(key), str) or not review[key].strip():
            errors.append(f"review {key} must be nonempty text")
    return review, _sha(raw), errors


class SupervisedMixin:
    """Round accounting and the current-success gate; mixed in before StreamingFixLoopState."""

    def __init__(self, task_dir: Path, identity: dict, *, resume: bool = False, clock=None, **kwargs):
        now = clock() if clock else None
        self._pending_supervised = None if resume else {
            "schema": SCHEMA, "study_id": identity["supervised_repair"]["study_id"],
            "state": "open", "current_round": 1, "closure": None,
            "rounds": [{"round": 1, "opened_at": now, "opened_by": "init"}]}
        super().__init__(task_dir, identity, resume=resume, clock=clock, **kwargs)
        self._pending_supervised = None
        sup = self.data.get("supervised")
        if not isinstance(sup, dict) or sup.get("schema") != SCHEMA or \
                sup.get("study_id") != identity["supervised_repair"]["study_id"]:
            raise SupervisedRefused("ledger has no supervised repair state for this study")

    def save(self) -> None:
        if getattr(self, "_pending_supervised", None) is not None:
            self.data.setdefault("supervised", self._pending_supervised)
        super().save()

    @property
    def sup(self) -> dict:
        return self.data["supervised"]

    def current_round(self) -> int:
        return self.sup["current_round"]

    # ---- admission -----------------------------------------------------------

    def validate_admission(self, phase: str, seed: int, digest: str) -> None:
        r, state = self.current_round(), self.sup["state"]
        if state == "awaiting_review":
            raise ProtocolError(f"round {r} is complete and awaits outer review; no attempt is "
                                "admitted until the outer runner opens the next round")
        if state != "open":
            raise ProtocolError("supervised development is closed; nothing more is admitted")
        if self.data["identity"]["supervised_repair"].get("closeout") and round_complete(self.data, r):
            raise ProtocolError(f"round {r} trials are complete; only report closeout and selection "
                                "remain, no snapshot or additional trial is admitted")
        if phase != "snapshot" and any(row["seed"] == seed for row in round_rows(self.data, r)):
            raise ProtocolError(
                f"seed {seed} already used its one charged attempt of round {r}; the next attempt "
                f"opens only in round {r + 1} after outer review (budget is never borrowed)")
        super().validate_admission(phase, seed, digest)

    def _resolve_blockers(self, phase, seed, digest, directory) -> None:
        # begin_trial appends the admitted row and saves right after this call:
        # tag it with its round inside that same atomic save.
        row = self.data["trials"][-1]
        if row.get("directory") == directory:
            row["supervised_round"] = self.current_round()
        super()._resolve_blockers(phase, seed, digest, directory)

    # ---- outer round transitions ----------------------------------------------

    def close_round(self) -> dict:
        r = self.current_round()
        if self.sup["state"] != "open":
            raise ProtocolError(f"round {r} is not open")
        if not round_complete(self.data, r):
            missing = sorted(set(self.seeds) - {row["seed"] for row in round_rows(self.data, r)})
            raise ProtocolError(f"round {r} is incomplete: seeds {missing} have no resolved attempt "
                                "or a row is still running")
        evidence = round_evidence(self.data, self.task_dir, r)
        record = self.sup["rounds"][r - 1]
        record.update(closed_at=self.clock(), evidence_sha256=evidence["evidence_sha256"],
                      summary_sha256=code_hash(json.dumps(evidence, sort_keys=True)),
                      directories=sorted(evidence["references"]),
                      successes=len(evidence["round_successes"]))
        if r >= ROUNDS:
            self.sup.update(state="closed", closure={"reason": "rounds_exhausted", "round": r,
                                                     "at": self.clock()})
        else:
            self.sup["state"] = "awaiting_review"
        self.save()
        return evidence

    def consume_review(self, case: dict, path: Path) -> dict:
        r = self.current_round()
        if self.sup["state"] != "awaiting_review":
            raise ProtocolError(f"round {r} is not awaiting review")
        record = self.sup["rounds"][r - 1]
        if record.get("review"):
            raise ProtocolError(f"round {r}'s review was already consumed")
        if round_evidence(self.data, self.task_dir, r)["evidence_sha256"] != record["evidence_sha256"]:
            raise SupervisedRefused(f"round {r} evidence changed after it was closed")
        review, sha, errors = review_errors(case, self.data, r, path)
        if errors:
            raise SupervisedRefused("; ".join(errors))
        record["review"] = {"path": str(path), "sha256": sha, "decision": review["decision"],
                            "reviewer": review["reviewer"], "consumed_at": self.clock()}
        if review["decision"] == "continue":
            self.sup["rounds"].append({"round": r + 1, "opened_at": self.clock(),
                                       "opened_by": f"outer review {sha}"})
            self.sup.update(current_round=r + 1, state="open")
        else:
            reason = {"freeze": "outer_freeze", "stop": "outer_stop"}[review["decision"]]
            self.sup.update(state="closed", closure={"reason": reason, "round": r,
                                                     "at": self.clock(), "review_sha256": sha})
        self.save()
        return record

    # ---- gate ------------------------------------------------------------------

    def evidence_errors(self, bundle: dict) -> list[str]:
        from native_world_fixloop_state import bundle_identity
        candidates = self.candidates()
        digest = bundle_identity(bundle)
        if digest not in candidates:
            return ["the selected bundle was never tested in this study; select an executed bundle"]
        if not candidates[digest]["passes"]:
            return ["require_current_selected_success: the selected bundle has no oracle success "
                    "of its own in this study; another bundle's or a historical success does not count"]
        return []

    def check_waivers(self) -> set[str]:
        """An outer freeze ends development with budget left: pending repair is not owed."""
        closure = self.sup.get("closure") or {}
        return {"seeds_pending_repair", "seeds_needing_initial"} \
            if closure.get("reason") == "outer_freeze" else set()

    def supervised_errors(self) -> list[str]:
        sup, errors = self.sup, []
        if sup["state"] != "closed":
            errors.append(f"supervised development is not closed (round {sup['current_round']} "
                          f"{sup['state']})")
        elif (sup.get("closure") or {}).get("reason") == "outer_stop":
            errors.append("the outer review stopped this study; nothing is frozen")
        if not study_successes(self.data):
            errors.append("development_failed: no normal oracle success in this study")
        ungraded = [s for s in self.seeds if not self.has_graded_evidence(s)]
        if ungraded:
            errors.append(f"seeds without any graded evidence in this study: {ungraded}")
        return errors

    def supervised_summary(self) -> dict:
        r = self.current_round()
        used = {row["seed"] for row in round_rows(self.data, r)}
        return {"schema": SCHEMA, "study_id": self.sup["study_id"], "state": self.sup["state"],
                "current_round": r, "rounds": self.sup["rounds"], "closure": self.sup.get("closure"),
                "round_seeds_remaining": [s for s in self.seeds if s not in used]
                if self.sup["state"] == "open" else [],
                "round_complete": round_complete(self.data, r),
                "study_successes": study_successes(self.data),
                "rule": "one charged attempt per seed per round; all 15 seeds per round; the "
                        "selected bundle needs its own oracle success in this study"}

    def progress(self) -> dict:
        progress = super().progress()
        progress["supervised"] = self.supervised_summary()
        return progress
