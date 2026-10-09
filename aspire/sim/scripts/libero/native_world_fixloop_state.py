# SPDX-License-Identifier: MIT
"""Per-seed evidence ledger for the native original Fix Loop.

The original protocol budget is THREE TOTAL retries per development seed, one
retry per simulator task replay. Smoke, initial, repair, and interactive
diagnostic sessions all spend from that one per-seed count; the single
observation-only scene snapshot is separate and spends no retry. There is no global code-edit count, no
1+15 revision protocol, and no numerical action/query/recovery cap here.

This is a protocol ledger, not a security sandbox. Only the runner writes
records, so a model-authored report cannot turn an incomplete cell into a
result. Infrastructure interruption is recorded as a spent retry with an
explicit blocker: it never resets a seed or grants a fresh budget.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

RETRY_LIMIT = 3
# Every phase that starts a simulator task replay spends a retry. `snapshot` is the
# one documented exception: it observes the scene and runs no task program.
RETRY_PHASES = ("smoke", "initial", "repair", "diagnostic")
PHASES = ("snapshot", *RETRY_PHASES)
BUNDLE_KEYS = {"policy", "world", "inventory"}
# A record whose outcome is settled: the attempt reached its own end and said
# what happened. Anything else is unresolved infrastructure evidence. These were
# four separate inline literals; `diagnostic_program_error` is added here once so
# an authored REPL failure cannot be read back as an infrastructure blocker.
# It is resolved, and spends a retry, but is never graded evidence: `candidates()`,
# `outcome()`'s graded set and `final_coverage()` all exclude the diagnostic
# phase, so an inspection session can never stand in for a development trial.
RESOLVED_STATUSES = frozenset({"complete", "world_program_error", "diagnostic_program_error"})
# Code-world profiles whose selected bundle is policy + world with no inventory,
# and which select only bundles that actually executed in development. The legacy
# adapter profile (identity carries no `profile` key) is unaffected.
CODE_WORLD_PROFILES = frozenset({"simple", "judgment"})


class ProtocolError(ValueError):
    pass


class TerminalBlocker(ProtocolError):
    """Irrecoverable stop: the batch cannot make forward progress without external action.

    Raised (not just reported) so callers that only catch ProtocolError still stop.
    Carries a structured payload so a machine-readable driver can report seeds and
    reasons without parsing the message string.

    ``recoverable`` is always False here; use plain ProtocolError for
    missing-report issues that don't need this distinction.
    """

    def __init__(self, reason: str, *, seeds: list[int], details: list[str]):
        super().__init__(reason)
        self.seeds = seeds
        self.details = details
        self.recoverable = False

    def as_dict(self) -> dict:
        return {"terminal": True, "recoverable": False,
                "reason": str(self), "seeds": self.seeds, "details": self.details}


def code_hash(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def valid_program(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(isinstance(node, ast.Call) for node in ast.walk(tree))


WORLD_FUNCTIONS = ("initialize", "advance", "predict", "assimilate")


def world_module_errors(source: str) -> list[str]:
    """Validate a world program by its four required definitions.

    A pure world module can be entirely definitions and returns, so the policy
    check for "contains some call expression" would wrongly reject a valid one.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"world program does not parse: {exc}"]
    defined = {node.name for node in tree.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = [name for name in WORLD_FUNCTIONS if name not in defined]
    return [f"world program must define {', '.join(missing)} at module level"] if missing else []


def inventory_errors(text: str) -> list[str]:
    """Validate the authored inventory before any process is admitted."""
    try:
        entities = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"inventory is not valid JSON: {exc}"]
    if not isinstance(entities, list) or not 2 <= len(entities) <= 12:
        return ["inventory must be a list of 2-12 scene entities"]
    errors = []
    for entry in entities:
        if not isinstance(entry, dict) or not all(
                isinstance(entry.get(key), str) and entry.get(key)
                for key in ("id", "label", "role")):
            return ["every inventory entity needs nonempty string id, label and role"]
    roles = [entry["role"] for entry in entities]
    if roles.count("manipulated") != 1:
        errors.append("exactly one inventory entity must have role 'manipulated'")
    if roles.count("target") != 1:
        errors.append("exactly one inventory entity must have role 'target'")
    if len({entry["id"] for entry in entities}) != len(entities):
        errors.append("inventory entity ids must be unique")
    return errors


def minimal_fallback(source: str) -> bool:
    """The runbook permits a single legal observation when every program crashed."""
    try:
        return ast.dump(ast.parse(source)) == ast.dump(ast.parse("get_observation()"))
    except SyntaxError:
        return False


def bundle_identity(bundle: dict) -> str:
    """Candidate identity covers the world program and inventory, not just code."""
    if not bundle or set(bundle) - BUNDLE_KEYS or "policy" not in bundle:
        raise ProtocolError("a candidate bundle requires policy and may add world/inventory")
    if any(not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for v in bundle.values()):
        raise ProtocolError("bundle entries must be lowercase sha256 digests")
    return code_hash(json.dumps(bundle, sort_keys=True, separators=(",", ":")))


def report_errors(path: Path, headings: tuple[str, ...]) -> list[str]:
    if not path.is_file():
        return [f"missing {path.name}"]
    text = path.read_text()
    if re.search(r"\bplaceholder\b|\blorem ipsum\b|evidence gathering in progress", text, re.I):
        return [f"{path.name} contains placeholder text"]
    errors = []
    for heading in headings:
        match = re.search(rf"^##\s+{re.escape(heading)}\s*:?(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
        if not match or not match.group(1).strip():
            errors.append(f"{path.name} needs a nonempty '{heading}' section (use 'none' if applicable)")
    return errors


class NativeWorldState:
    VERSION = 1
    RETRY_LIMIT = RETRY_LIMIT

    def __init__(self, task_dir: Path, identity: dict, *, resume: bool = False):
        self.task_dir = task_dir.resolve()
        self.path = self.task_dir / "development_state.json"
        self.seeds = identity["dev_seeds"]
        # The development gate is part of the identity. The absent key is the
        # legacy oracle gate: rows carry the simulator's `task_completed`. A
        # sealed gate stores a `gate` verdict per row instead, and never the label.
        self.gate = identity.get("development_gate") or "oracle"
        self.sealed = self.gate != "oracle"
        if not self.seeds or len(set(self.seeds)) != len(self.seeds) or any(s <= 50 for s in self.seeds):
            raise ProtocolError("development seeds must be distinct and outside held-out seeds 1–50")
        if self.path.exists():
            if not resume:
                raise ProtocolError(f"cell already exists; resume it explicitly: {self.path}")
            self.data = json.loads(self.path.read_text())
            if self.data.get("version") != self.VERSION or self.data.get("identity") != identity:
                raise ProtocolError("resume requires the same protocol, model, condition, and prompts")
        else:
            if resume:
                raise ProtocolError("no development ledger to resume; other cells cannot be adopted")
            if self.task_dir.exists() and any(self.task_dir.iterdir()):
                raise ProtocolError("use a fresh task directory; existing artifacts are preserved")
            self.task_dir.mkdir(parents=True, exist_ok=True)
            self.data = {
                "version": self.VERSION, "identity": identity, "trials": [],
                "rejected": [], "aliases": [], "model_served": {}, "usage": {},
                "selected": None, "stage1_complete": False,
            }
            self.save()

    def save(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(self.path)

    # ---- accounting ---------------------------------------------------------

    def passed(self, record: dict) -> bool:
        """Did this graded execution pass its development gate?

        Oracle cells read the simulator's label; sealed cells read the recorded
        gate verdict (the world's final done() or the independent judge). Every
        progress, triage and selection rule goes through here, so no rule can
        reach the label in a sealed cell by accident.
        """
        if self.sealed:
            return bool((record.get("gate") or {}).get("passed"))
        return bool(record.get("task_completed"))

    def failure_outcome(self, reason: str) -> dict:
        """Outcome fields for a resolved row that graded no success."""
        if self.sealed:
            return {"outcome": "sealed", "gate": {"source": self.gate, "verdict": "not_evaluated",
                                                  "passed": False, "reason": reason}}
        return {"reward": 0.0, "task_completed": 0}

    def records(self, phase: str | None = None, seed: int | None = None) -> list[dict]:
        return [r for r in self.data["trials"]
                if (phase is None or r["phase"] == phase) and (seed is None or r["seed"] == seed)]

    def retries(self, seed: int) -> list[dict]:
        """Every record that spent one of this seed's three retries."""
        return [r for r in self.data["trials"] if r["seed"] == seed and r["spends_retry"]]

    def retries_used(self, seed: int) -> int:
        return len(self.retries(seed))

    def retries_remaining(self, seed: int) -> int:
        return max(0, self.RETRY_LIMIT - self.retries_used(seed))

    def initial_bundle(self) -> str | None:
        initial = self.records("initial")
        return initial[0]["bundle_sha256"] if initial else None

    def has_initial_evidence(self, seed: int) -> bool:
        """A settled initial result on this seed — the only thing repair builds on.

        Deliberately NOT "some initial row exists" and NOT "budget is gone". An
        interrupted initial attempt leaves a row but no evidence, and spending
        the remaining attempts on diagnostic sessions produces no evidence at
        all. Either way this stays False and the seed stays owed.
        """
        return any(r["status"] in RESOLVED_STATUSES for r in self.records("initial", seed))

    def has_graded_evidence(self, seed: int) -> bool:
        """Did any execution on this seed actually grade the candidate?

        Initial coverage and graded coverage are different facts. Three crashing
        smokes are real graded executions of the frozen bundle on this seed: the
        seed is owed a blocker note, but the batch has seen it run. Diagnostic
        sessions grade nothing and infrastructure failures executed nothing, so
        neither produces graded evidence however many attempts they consumed.
        """
        return any(r["seed"] == seed for r in self.graded())

    def record_blocker(self, phase: str, seed: int, reason: str, sources: dict,
                       detail: dict | None = None, bundle: dict | None = None) -> dict:
        """An infrastructure failure that stopped a trial BEFORE it was admitted.

        Nothing executed and no retry is spent, so this is neither a retry-spending
        row nor a rejected revision: blaming the candidate for the infrastructure
        would make a transient failure look like an authored one. The record is
        kept so the blocker is attributable and the same candidate can be retried
        unchanged once the infrastructure recovers.

        `sources` are paths, and a later Edit of the same path changes what they
        mean, so the candidate is also identified by immutable digests: the
        bundle identity, and the screening identity (policy/world/tape hashes)
        the offline check already computed. That is what lets `_resolve_blockers`
        say which historical blocker a later admitted attempt actually cleared.
        """
        record = {"phase": phase, "seed": seed, "reason": reason, "sources": sources,
                  "status": "blocked", "spends_retry": False, "executed": False,
                  "retryable": True, "resolved": False,
                  **({"detail": detail} if detail else {})}
        if bundle:
            record.update(bundle=bundle, bundle_sha256=bundle_identity(bundle))
        if detail and detail.get("identity"):
            record["screening_identity"] = detail["identity"]
        self.data.setdefault("blockers", []).append(record)
        self.save()
        return record

    def _resolve_blockers(self, phase: str, seed: int, digest: str, directory: str) -> None:
        """Mark the blockers this admitted attempt cleared, without deleting them.

        A blocker is history, not a standing veto: once the identical candidate
        is admitted on the same phase and seed, whatever stopped the screening has
        recovered. Keeping the row but flipping `resolved` preserves the audit
        trail while letting a driver watch `unresolved_screening_blockers`
        instead of treating an old, cleared blocker as a permanent stop.
        """
        for blocker in self.data.get("blockers", []):
            if (not blocker.get("resolved") and blocker["phase"] == phase
                    and blocker["seed"] == seed
                    and blocker.get("bundle_sha256") in (None, digest)):
                blocker.update(resolved=True, resolved_by=directory)

    def unresolved_blockers(self) -> list[dict]:
        return [b for b in self.data.get("blockers", []) if not b.get("resolved")]

    def reject(self, phase: str, seed: int, reason: str, sources: dict) -> dict:
        """Record a revision refused before any simulator process started.

        An invalid generated revision is preserved as an attempted snapshot, and
        explicitly spends NO retry: nothing executed. Keeping it separate is what
        makes "invalid source" distinguishable from "executed and failed".

        The refused content is preserved by digest and by a bounded excerpt, not
        only by path: a later Edit of the same path must not erase the evidence of
        what was actually submitted.
        """
        snapshots = {}
        for key, name in sources.items():
            if not name:
                continue
            try:
                text = Path(name).read_text()
            except OSError as exc:
                snapshots[key] = {"path": str(name), "unreadable": str(exc)}
                continue
            snapshots[key] = {"path": str(name), "sha256": code_hash(text),
                              "bytes": len(text.encode()), "excerpt": text[:4000]}
        record = {"phase": phase, "seed": seed, "reason": reason,
                  "sources": sources, "submitted": snapshots,
                  "spends_retry": False, "executed": False}
        self.data["rejected"].append(record)
        self.save()
        return record

    def validate_admission(self, phase: str, seed: int, digest: str) -> None:
        """Non-mutating pre-admission check shared by begin_trial and _run_trial.

        Raises ProtocolError for recoverable stops and TerminalBlocker for
        irrecoverable ones (seeds exhausted without any graded evidence). Callers
        may invoke this before expensive offline screening so a candidate that
        would be refused at begin_trial never wastes screening time.
        """
        if self.data["stage1_complete"]:
            raise ProtocolError("this cell is frozen")
        if phase not in PHASES:
            raise ProtocolError(f"unknown phase: {phase}")
        if seed not in self.seeds:
            raise ProtocolError(f"seed {seed} is outside the development partition")
        if any(r["status"] == "running" for r in self.data["trials"]):
            raise ProtocolError("a trial has unresolved infrastructure evidence; resolve it before continuing")
        if phase == "snapshot":
            if seed != self.seeds[0] or self.records("snapshot"):
                raise ProtocolError("the observation-only snapshot runs once, on the first development seed")
        else:
            if self.retries_remaining(seed) <= 0:
                raise ProtocolError(
                    f"seed {seed} spent all {self.RETRY_LIMIT} retries; "
                    "record its blocker and continue with another seed")
        if phase == "smoke":
            if seed != self.seeds[0] or self.records("initial"):
                raise ProtocolError("smoke runs on the first development seed, before the initial batch")
            if any(r.get("sandbox_rc") == 0 for r in self.records("smoke")):
                raise ProtocolError("a smoke already ran without crashing; alias it or proceed to the initial batch")
        if phase == "initial":
            snapshot = self.records("snapshot")
            if not snapshot or snapshot[0]["status"] != "complete":
                raise ProtocolError("capture the scene snapshot before grading the initial program")
            if self.has_initial_evidence(seed):
                raise ProtocolError(f"initial seed {seed} already has a completed result")
            frozen = self.initial_bundle()
            if frozen is not None and frozen != digest:
                raise ProtocolError("one initial program: the initial bundle is frozen for the whole batch")
        if phase == "repair":
            # The original workflow triages the whole initial batch before
            # repairing. The gate is missing INITIAL EVIDENCE, never remaining
            # budget: the old `retries_remaining(s) > 0` term let the batch be
            # "finished" by spending a seed's attempts on diagnostic sessions,
            # which grade nothing. Inspection is still never gated this way.
            pending = [s for s in self.seeds if not self.has_initial_evidence(s)]
            runnable = [s for s in pending if self.retries_remaining(s) > 0]
            if runnable:
                raise ProtocolError(f"run the initial program on seeds {runnable} before repairs")
            ungraded = [s for s in pending if not self.has_graded_evidence(s)]
            if ungraded:
                # Exhausted with nothing to show: every attempt was a diagnostic
                # session or an infrastructure failure, so the batch has no graded
                # observation of this seed at all. Burning more budget cannot
                # create the missing evidence, so do not ask for it: record the
                # blocker. Irrecoverable — neither filing a note nor retrying a
                # different candidate can produce the missing graded evidence —
                # so this raises TerminalBlocker, not plain ProtocolError.
                details = [
                    f"seed {s}: {self.retries_used(s)} attempt(s), none graded"
                    for s in ungraded]
                raise TerminalBlocker(
                    f"seeds {ungraded} have no initial evidence and no attempts left; "
                    "their attempts produced no graded result. Record "
                    "attempts/seed_N_BLOCKED.md for each and report the blocker; "
                    "do not spend further attempts trying to unlock repair",
                    seeds=ungraded, details=details)
            # A seed whose budget went to real graded executions (three crashing
            # smokes) triaged nothing further to give. Repair of the OTHER seeds
            # proceeds, exactly as the original workflow allowed; the exhausted
            # seed keeps its missing-initial report and its blocker note.
        if phase == "diagnostic":
            # Reserve the last attempt on a seed that has never produced graded
            # evidence. An inspection session grades nothing; burning the final
            # slot on it would leave the seed permanently ungraded — the exact
            # failure mode that TerminalBlocker was added to surface. Crashing
            # smokes and post-grade diagnostics are unaffected: they already
            # have graded evidence and pass the has_graded_evidence check.
            if self.retries_remaining(seed) == 1 and not self.has_graded_evidence(seed):
                raise ProtocolError(
                    f"seed {seed} has one attempt remaining and no graded evidence; "
                    "reserve it for a real graded trial — an inspection session "
                    "cannot substitute for execution")

    def begin_trial(self, phase: str, seed: int, bundle: dict, sources: dict) -> dict:
        digest = bundle_identity(bundle)
        self.validate_admission(phase, seed, digest)
        attempt = self.retries_used(seed) + 1 if phase != "snapshot" else 1
        relative = Path("development") / phase / f"seed_{seed}" / f"attempt_{attempt}"
        directory = self.task_dir / relative
        directory.mkdir(parents=True, exist_ok=False)
        record = {"phase": phase, "seed": seed, "attempt": attempt, "status": "running",
                  "directory": str(relative), "spends_retry": phase in RETRY_PHASES,
                  "executed": True, "bundle_sha256": digest, "bundle": bundle,
                  "sources": sources}
        self.data["trials"].append(record)
        self._resolve_blockers(phase, seed, digest, str(relative))
        self.save()
        return record

    def finish_trial(self, record: dict, *, result: dict | None, exit_code: int | None,
                     error: str = "", world_error: dict | None = None,
                     raw_result: dict | None = None,
                     diagnostic_error: dict | None = None) -> None:
        record.update(process_exit_code=exit_code, error=error)
        if diagnostic_error and not error:
            # The REPL reached its own end and its authored code raised. The exit
            # code is 0 either way, so the artifact — not the exit status — says
            # so. The retry stays spent: a simulator process really ran. It
            # is never graded evidence; see RESOLVED_STATUSES.
            record.update(status="diagnostic_program_error",
                          diagnostic_error=diagnostic_error, sandbox_rc=1,
                          **self.failure_outcome("diagnostic session raised; grades nothing"),
                          trial_dir=record["directory"], session="diagnostic")
        elif result is not None and exit_code == 0:
            record.update(result, status="complete")
        elif world_error:
            # The authored world program or inventory failed while the simulator
            # itself worked. That is a retry-spending model/program failure with usable
            # feedback, not a permanent infrastructure blocker — and it is never a
            # silent B/C success, so the raw task outcome is kept separately.
            record.update(status="world_program_error", world_error=world_error,
                          sandbox_rc=1, **self.failure_outcome("authored world program error"),
                          trial_dir=(raw_result or {}).get("trial_dir", record["directory"]))
            if raw_result is not None:
                record["raw_result"] = raw_result
        else:
            # The retry stays spent: a simulator process really ran. This is a
            # blocker to report, never a score and never a reason to reset a seed.
            record["status"] = "infrastructure_error"
        self.save()

    def alias_smoke_as_initial(self, seed: int) -> dict:
        """Reuse a completed smoke as this seed's initial evidence, explicitly.

        Permitted only when the smoke ran the identical bundle that the initial
        batch froze, so no fictitious extra execution is invented.
        """
        smokes = [r for r in self.records("smoke", seed) if r["status"] == "complete"]
        if not smokes:
            raise ProtocolError("no completed smoke on this seed to alias")
        smoke = smokes[-1]
        frozen = self.initial_bundle()
        if frozen is not None and frozen != smoke["bundle_sha256"]:
            raise ProtocolError("alias requires the smoke bundle to equal the frozen initial bundle")
        if any(r["status"] == "complete" for r in self.records("initial", seed)):
            raise ProtocolError(f"seed {seed} already has completed initial evidence")
        alias = {k: smoke[k] for k in ("seed", "directory", "bundle_sha256", "bundle",
                                       "sandbox_rc", "reward", "task_completed", "trial_dir",
                                       "outcome", "gate") if k in smoke}
        alias.update(phase="initial", attempt=smoke["attempt"], status="complete",
                     spends_retry=False, executed=False, alias_of=smoke["directory"],
                     alias_reason="identical bundle already executed as smoke on this seed",
                     sources=smoke["sources"], process_exit_code=smoke["process_exit_code"],
                     error="")
        self.data["trials"].append(alias)
        self.data["aliases"].append({"seed": seed, "from": smoke["directory"], "as": "initial"})
        self.save()
        return alias

    # ---- evidence -----------------------------------------------------------

    def outcome(self, seed: int) -> dict:
        """What this seed actually demonstrates, across every executed phase."""
        executed = [r for r in self.executed() if r["seed"] == seed]
        initial = [r for r in self.records("initial", seed)
                   if r["status"] in RESOLVED_STATUSES]
        graded = [r for r in executed if r["phase"] != "diagnostic"]
        return {
            "seed": seed,
            "retries_used": self.retries_used(seed),
            "retries_remaining": self.retries_remaining(seed),
            "initial_passed": bool(initial and self.passed(initial[0])),
            "has_initial_evidence": bool(initial),
            # Separate and truthful: a seed can be graded (a crashing smoke) while
            # still owing initial coverage, and a seed can be out of budget with
            # no graded observation at all.
            "has_graded_evidence": bool(graded),
            "passed": any(self.passed(r) for r in graded),
            "passing_phases": sorted({r["phase"] for r in graded if self.passed(r)}),
            # A seed whose initial run failed and that has never been repaired,
            # while attempts remain, is unfinished fix-loop work — not a result.
            "needs_repair": bool(initial and not any(self.passed(r) for r in graded)
                                 and self.retries_remaining(seed) > 0),
            "world_program_errors": [r["directory"] for r in self.records(seed=seed)
                                     if r["status"] == "world_program_error"],
            "infrastructure_errors": [r["directory"] for r in self.records(seed=seed)
                                      if r["status"] not in RESOLVED_STATUSES],
            "diagnostic_program_errors": [r["directory"] for r in self.records(seed=seed)
                                          if r["status"] == "diagnostic_program_error"],
        }

    def candidates(self) -> dict[str, dict]:
        """Tested bundles: what each one actually demonstrated, by execution.

        Only real executions are counted. A smoke alias is an evidence reference
        to a run already counted here, so counting its row again would double the
        crashes and invent a second replay that never happened.
        """
        candidates: dict[str, dict] = {}
        for record in self.executed():
            if record["phase"] == "diagnostic":
                continue  # A diagnostic session grades nothing; it is inspection.
            row = candidates.setdefault(record["bundle_sha256"], {
                "bundle": record["bundle"], "passes": [], "crashes": 0, "trials": []})
            if self.passed(record) and record["seed"] not in row["passes"]:
                row["passes"].append(record["seed"])
            row["crashes"] += int(record["sandbox_rc"] != 0)
            row["trials"].append({**{k: record[k] for k in ("phase", "seed", "directory", "sandbox_rc")},
                                  "passed": self.passed(record)})
        for row in candidates.values():
            row["passes"].sort()
        return candidates

    def executed(self) -> list[dict]:
        """Completed records that really ran a simulator process, aliases excluded."""
        return [r for r in self.data["trials"]
                if r["phase"] in RETRY_PHASES and r["status"] in RESOLVED_STATUSES
                and r.get("executed", True) and not r.get("alias_of")]

    def graded(self) -> list[dict]:
        """Executions that actually graded the candidate on a development seed."""
        return [r for r in self.executed() if r["phase"] != "diagnostic"]

    def best_score(self, candidates: dict[str, dict]) -> tuple[int, int]:
        return max((len(row["passes"]), -row["crashes"]) for row in candidates.values())

    def progress(self) -> dict:
        seeds = [self.outcome(s) for s in self.seeds]
        candidates = self.candidates()
        return {
            "retry_limit": self.RETRY_LIMIT,
            "retry_phases": list(RETRY_PHASES),
            "snapshot_complete": bool(self.records("snapshot")
                                      and self.records("snapshot")[0]["status"] == "complete"),
            "seeds": seeds,
            "retries_used_per_seed": {str(s["seed"]): s["retries_used"] for s in seeds},
            "retries_remaining_per_seed": {str(s["seed"]): s["retries_remaining"] for s in seeds},
            "seeds_needing_initial": [s["seed"] for s in seeds
                                      if not s["has_initial_evidence"] and s["retries_remaining"] > 0],
            "seeds_exhausted_without_initial": [s["seed"] for s in seeds
                                                if not s["has_initial_evidence"] and not s["retries_remaining"]],
            # The subset with no graded observation whatsoever: every attempt was
            # a diagnostic session or an infrastructure failure. Reported apart
            # from the line above because only this one means the development work
            # never happened.
            "seeds_exhausted_without_graded_evidence": [
                s["seed"] for s in seeds
                if not s["has_graded_evidence"] and not s["retries_remaining"]],
            "seeds_passing": [s["seed"] for s in seeds if s["passed"]],
            # A failed development seed is finished only by a successful later
            # attempt or by an exhausted budget plus its recorded blocker.
            "seeds_pending_repair": [s["seed"] for s in seeds if s["needs_repair"]],
            "blocked_notes_required": [s["seed"] for s in seeds if self._needs_note(s)],
            "world_program_errors": [r["directory"] for r in self.data["trials"]
                                     if r["status"] == "world_program_error"],
            "infrastructure_errors": [r["directory"] for r in self.data["trials"]
                                      if r["status"] not in RESOLVED_STATUSES],
            "diagnostic_program_errors": [r["directory"] for r in self.data["trials"]
                                          if r["status"] == "diagnostic_program_error"],
            # Graded development executions: what any completeness claim rests on.
            # Diagnostic sessions are excluded by construction.
            "graded_executions": len(self.graded()),
            # Pre-admission infrastructure blockers. They spent no retry and are retryable, kept
            # attributable so a blocked screening is visible without being blamed
            # on the candidate. The full list is audit history and keeps every row
            # forever; only the unresolved subset is still standing in the way, so
            # a blocker that a later admitted attempt cleared cannot read as a
            # permanent stop.
            "screening_blockers": self.data.get("blockers", []),
            "unresolved_screening_blockers": self.unresolved_blockers(),
            "rejected_revisions": self.data["rejected"],
            "aliases": self.data["aliases"],
            "tested_bundles": {digest: {"passes": row["passes"], "crashes": row["crashes"],
                                        "trials": len(row["trials"]), "bundle": row["bundle"]}
                               for digest, row in candidates.items()},
            "latest_trial": self.data["trials"][-1] if self.data["trials"] else None,
            # Real simulator invocations, aliases excluded and reported separately.
            "replay_invocations": len([r for r in self.data["trials"]
                                       if r.get("executed", True) and not r.get("alias_of")]),
            "evidence_ledger": str(self.path),
        }

    def _needs_note(self, outcome: dict) -> bool:
        if outcome["passed"] or outcome["retries_remaining"]:
            return False
        path = self.task_dir / "attempts" / f"seed_{outcome['seed']}_BLOCKED.md"
        return bool(report_errors(path, ("Root Cause", "Details", "What Was Tried")))

    # ---- recovery -----------------------------------------------------------

    def resolve_interrupted(self, record: dict, *, result: dict | None, exit_code: int | None,
                            error: str, recovery: dict) -> None:
        """Close out an interrupted attempt without refunding or re-freezing it.

        The attempt keeps the retry it already spent, keeps its frozen
        inputs, and keeps its partial evidence. Recovery only records what the
        infrastructure observed afterwards. A recovery that had to run the
        simulator again must pass its own admitted record through begin_trial, so
        that second execution is visible and spends a retry like any other.
        """
        if record["status"] not in {"running", "infrastructure_error"}:
            raise ProtocolError("only an unresolved attempt can be recovered")
        if not record["spends_retry"] and record["phase"] in RETRY_PHASES:
            raise ProtocolError("an admitted simulator attempt cannot stop spending its retry")
        if recovery.get("bundle_sha256") not in (None, record["bundle_sha256"]):
            raise ProtocolError("recovery must reuse the same frozen inputs as the interrupted attempt")
        record["recovery"] = recovery
        if result is not None and exit_code == 0:
            record.update(result, status="complete", process_exit_code=exit_code, error=error)
        else:
            # No usable execution evidence: say so, keep the slot spent.
            record.update(status="infrastructure_error", process_exit_code=exit_code,
                          error=error or "recovery produced no usable trial evidence")
        self.save()

    def unresolved(self) -> list[dict]:
        return [r for r in self.data["trials"] if r["status"] == "running"]

    # ---- selection and freeze ----------------------------------------------

    def select(self, bundle: dict, reason: str) -> dict:
        """Freeze the reviewed selection with its bundle identity and reason."""
        digest = bundle_identity(bundle)
        self.data["selected"] = {"bundle_sha256": digest, "bundle": bundle, "reason": reason}
        self.save()
        return self.data["selected"]

    def evidence_errors(self, bundle: dict) -> list[str]:
        """The original selection contract, expressed over tested bundles only.

        A selected bundle must have been executed, and it must rest on development
        success evidence. When some bundle succeeded, the selection must be one
        that succeeded — a repaired synthesis tested on only the failing seeds is
        acceptable even though its raw pass count is smaller than the initial
        program's, so no always-maximum-pass-count gate is applied. Only when NO
        bundle succeeded does the original most-successes / fewer-crashes fallback
        decide, exactly as the pristine prompt words it.
        """
        digest = bundle_identity(bundle)
        candidates = self.candidates()
        if digest not in candidates:
            return ["the selected bundle was never tested; test the synthesis while a "
                    "slot remains, or select an already tested bundle"]
        selected = candidates[digest]
        if selected["passes"]:
            return []
        if any(row["passes"] for row in candidates.values()):
            return ["selection must use development success evidence: a bundle that "
                    "succeeded on a development seed exists — not the last noncrashing version"]
        if (0, -selected["crashes"]) < self.best_score(candidates):
            return ["with no successful bundle, select the one with fewer crashes and "
                    "simpler observation-driven behavior"]
        return []

    def observation_fallback(self, bundle: dict, source: str) -> bool:
        """Only the documented all-crashing policy fallback; world inputs stay tested."""
        if self.data["identity"].get("profile") in CODE_WORLD_PROFILES:
            return False  # These comparisons select only actually tested bundles.
        candidates = self.candidates()
        companions = {k: v for k, v in bundle.items() if k != "policy"}
        return (bool(candidates) and minimal_fallback(source)
                and all(row["crashes"] == len(row["trials"]) for row in candidates.values())
                and any(all(row["bundle"].get(k) == v for k, v in companions.items())
                        for row in candidates.values()))

    def selection_errors(self, bundle: dict, source: str) -> list[str]:
        """Was this exact bundle actually tested, and is it the best evidence?"""
        candidates = self.candidates()
        if not candidates:
            return ["no development execution evidence exists; nothing can be selected"]
        if self.observation_fallback(bundle, source):
            return []
        return self.evidence_errors(bundle)

    def completion_errors(self, *, bundle: dict, working_code: Path,
                          world_required: bool) -> list[str]:
        progress = self.progress()
        errors = [f"{key}: {progress[key]}" for key in (
            "seeds_needing_initial", "seeds_exhausted_without_graded_evidence",
            "seeds_pending_repair", "blocked_notes_required",
            "infrastructure_errors",
        ) if progress[key]]
        # `seeds_exhausted_without_*` was computed and reported but never checked,
        # so a seed that spent its budget without producing any result did not
        # block completion. What must block is missing GRADED evidence: attempts
        # spent on diagnostic sessions or lost to infrastructure observed nothing
        # and cannot finish development work. A seed whose budget went to real
        # graded executions is finished the ordinary way — its missing initial
        # coverage is still reported in `progress`, and `blocked_notes_required`
        # still demands its blocker note.
        if not progress["graded_executions"]:
            errors.append("no graded development execution exists; diagnostic "
                          "sessions are inspection and grade nothing")
        if not progress["snapshot_complete"]:
            errors.append("missing completed scene snapshot")
        analysis = self.task_dir / "task_analysis.md"
        if not analysis.is_file() or not analysis.read_text().strip():
            errors.append("missing task_analysis.md")
        errors += report_errors(self.task_dir / "findings.md", (
            "Root causes observed", "What fixed them", "Generalizable patterns", "Blocked seeds"))
        fix = self.task_dir / "fix_code.py"
        if not fix.is_file() or not valid_program(fix.read_text()):
            return errors + ["missing executable fix_code.py"]
        source = fix.read_text()
        if not working_code.is_file() or working_code.read_text() != source:
            errors.append("working_codes copy must match fix_code.py")
        if bundle.get("policy") != code_hash(source):
            errors.append("the selected bundle's policy digest must match fix_code.py")
        if world_required:
            companions = [("world", "fix_world_program.py")]
            if self.data["identity"].get("profile") not in CODE_WORLD_PROFILES:
                companions.append(("inventory", "fix_inventory.json"))
            for key, name in companions:
                path = self.task_dir / name
                if not path.is_file() or bundle.get(key) != code_hash(path.read_text()):
                    errors.append(f"world condition requires {name} matching the selected bundle")
        elif set(bundle) != {"policy"}:
            errors.append("the ordinary condition selects a policy-only bundle")
        candidates = self.candidates()
        if self.observation_fallback(bundle, source):
            return errors  # Documented single-observation fallback, explicitly labeled.
        if not candidates:
            return errors + ["no tested bundle exists; the final program must have been executed"]
        return errors + self.evidence_errors(bundle)

    def final_coverage(self, bundle: dict) -> list[dict]:
        """Real executions of the frozen bundle. An alias row is not a second run."""
        digest = bundle_identity(bundle)
        return [r for r in self.executed()
                if r["phase"] != "diagnostic" and r["bundle_sha256"] == digest]
