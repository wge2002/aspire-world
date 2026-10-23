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
# p4 sweep (CC_TASK_2 design (b)): a re-run of an already tested candidate's exact
# bytes on a development seed that candidate never ran. It is graded evidence for
# that candidate but spends NONE of the seed's three repair retries: the coverage
# gate must be payable by construction, and a repair round that used every retry
# would otherwise make it impossible. Its own limits are below.
SWEEP_PHASE = "sweep"
# CC_TASK_4: there is no cap on how many candidates a cell may sweep. The old
# SWEEP_ROUNDS = 2 let one stray single-seed sweep use up a slot, after which the
# repaired bundle could never be covered and so never frozen (drawer_p4_r1b). A
# sweep still spends no repair retry, still runs each seed at most once per
# candidate, still refuses held-out seeds; total sweeping is bounded only by the
# development wall-clock deadline.
# The contract under which the coverage gate (and therefore the sweep) applies.
COVERAGE_CONTRACT = "p3-search"
# The all-fail tie-break's message. `check` reclassifies exactly this error as
# advisory when it rests only on partially covered candidates (see `tie_break`).
TIE_BREAK_ERROR = ("with no successful bundle, select the one with fewer crashes and "
                   "simpler observation-driven behavior")
# CC_TASK_6 (p5): the ASPIRE v1.0 development budget, opt-in per case
# (`development_budget: "aspire-v1"`, part of the identity). Absent, every rule
# below is inert and the ledger behaves exactly as before.
#   phase A — until the first development seed actually passes: iterate on that
#             seed only, attempts there uncapped, no sweep, no select / freeze;
#   phase B — after it passes: at most MAX_VERSIONS new versions, each run on
#             every development seed (its admitted seed + uncharged sweeps), each
#             declaring the failure mode it targets, at most VERSIONS_PER_MODE per
#             mode; a mode still unfixed after that is blocked and gets nothing more.
#   probe   — an observation-only session (no motor command, enforced by the
#             replay's observation-only REPL): never a version, never graded,
#             spends nothing, in either phase.
BUDGET_REVISION = "aspire-v1"
MAX_VERSIONS = 8
VERSIONS_PER_MODE = 3
PROBE_PHASE = "probe"
#: A declared failure-mode name: short, stable, lower case.
MODE_NAME = re.compile(r"[a-z0-9][a-z0-9_.\-]{0,62}")
#: A blocked mode's note, like a per-seed BLOCKED.md: root cause and what was tried.
MODE_NOTE_HEADINGS = ("Root Cause", "Details", "What Was Tried")
#: A probe whose replay saw the robot move: it acted, so it was no probe.
PROBE_VIOLATION = "probe_violation"
# CC_TASK_14 (p6d, method switch `prefreeze_resweep`): the outer driver's second run
# of the near-best complete phase-B candidates on every development seed, after
# development ended and before the freeze. It is never a version, never a retry,
# never part of a candidate's coverage, and never a candidate by itself; when a
# candidate's re-sweep is complete, `best_complete` merges it with the sweep.
RESWEEP_PHASE = "resweep"
PHASES = ("snapshot", *RETRY_PHASES, SWEEP_PHASE, PROBE_PHASE, RESWEEP_PHASE)
# Phases whose settled rows really executed a candidate (diagnostic included;
# graded evidence excludes it separately). A probe is never a candidate.
EXECUTED_PHASES = (*RETRY_PHASES, SWEEP_PHASE)
BUNDLE_KEYS = {"policy", "world", "inventory"}
# A record whose outcome is settled: the attempt reached its own end and said
# what happened. Anything else is unresolved infrastructure evidence. These were
# four separate inline literals; `diagnostic_program_error` is added here once so
# an authored REPL failure cannot be read back as an infrastructure blocker.
# It is resolved, and spends a retry, but is never graded evidence: `candidates()`,
# `outcome()`'s graded set and `final_coverage()` all exclude the diagnostic
# phase, so an inspection session can never stand in for a development trial.
RESOLVED_STATUSES = frozenset({"complete", "world_program_error", "diagnostic_program_error"})
# p3-search only: an initial seed deferred by signature early stop. Nothing ran
# and no attempt was spent; the row is neither evidence nor infrastructure
# trouble. Ledgers of every other contract never contain it.
DEFERRED_STATUS = "deferred_initial"
# A probe that moved the robot settled too (it is a protocol finding, reported as
# its own substantive issue), so it is not unresolved infrastructure either.
NOT_INFRASTRUCTURE = RESOLVED_STATUSES | {DEFERRED_STATUS, "probe_violation", "development_interrupted"}
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

    def retries_remaining(self, seed: int) -> int | None:
        """Per-seed retries left; None under aspire-v1, which has no per-seed cap."""
        if self.budgeted():
            return None
        return max(0, self.RETRY_LIMIT - self.retries_used(seed))

    def budgeted(self) -> bool:
        """Does this cell run the ASPIRE v1.0 development budget (CC_TASK_6)?"""
        return self.data["identity"].get("development_budget") == BUDGET_REVISION

    def switch(self, name: str):
        """A CC_TASK_14 method switch from the identity (None when the cell has none)."""
        return (self.data["identity"].get("method_switches") or {}).get(name)

    def sweep_only(self) -> bool:
        """`version_score: sweep_only`: candidates are scored on their sweep rows alone."""
        return self.budgeted() and self.switch("version_score") == "sweep_only"

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

    def deferred_seeds(self) -> list[int]:
        """Initial seeds deferred by p3-search early stop; empty for every other ledger."""
        return [r["seed"] for r in self.data["trials"] if r["status"] == DEFERRED_STATUS]

    def defer_initial(self, stop: dict, digest: str) -> list[dict]:
        """Record the deferred initial seeds of a triggered early stop. Spends nothing.

        Each deferred seed keeps its own unspent attempts, now usable only by
        repair; `banked_attempts` counts them. Idempotent per seed.
        """
        done = set(self.deferred_seeds())
        rows = []
        for seed in stop["deferred_seeds"]:
            if seed in done:
                continue
            row = {"phase": "initial", "seed": seed, "attempt": 0, "status": DEFERRED_STATUS,
                   "directory": None, "spends_retry": False, "executed": False,
                   "bundle_sha256": digest, "banked_attempts": stop["banked"][seed],
                   "reason": (f"signature early stop: {stop['k']} identical failing initial "
                              f"signatures by attempt {stop['trigger_attempt']}"),
                   "trigger_attempt": stop["trigger_attempt"], "signature": stop["signature"]}
            self.data["trials"].append(row)
            rows.append(row)
        control = self.data.setdefault("search_control", {})
        control.update(early_stop=stop, banked_attempts=sum(
            r.get("banked_attempts", 0) for r in self.data["trials"] if r["status"] == DEFERRED_STATUS))
        self.save()
        return rows

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

    def validate_admission(self, phase: str, seed: int, digest: str,
                           declaration: dict | None = None, *,
                           in_flight: frozenset = frozenset()) -> dict | None:
        """Non-mutating pre-admission check shared by begin_trial and _run_trial.

        Raises ProtocolError for recoverable stops and TerminalBlocker for
        irrecoverable ones (seeds exhausted without any graded evidence). Callers
        may invoke this before expensive offline screening so a candidate that
        would be refused at begin_trial never wastes screening time.

        Under the aspire-v1 budget the rules are `_budget_admission`'s, and the
        return value is the row's budget metadata (phase, and for a phase-B
        version its number, mode and target seeds); otherwise None.
        `declaration` ({"mode", "target_seeds"}) is read only there.

        `in_flight` (CC_TASK_7) names the row directories a batch sweep holding
        the trial lock has begun and not yet settled: those rows are running by
        design, not lost evidence. Every other running row still refuses.
        """
        if self.data["stage1_complete"]:
            raise ProtocolError("this cell is frozen")
        if phase not in PHASES:
            raise ProtocolError(f"unknown phase: {phase}")
        if seed not in self.seeds:
            raise ProtocolError(f"seed {seed} is outside the development partition")
        if phase == RESWEEP_PHASE:
            return self._resweep_admission(seed, digest, in_flight)
        if self.data.get("development_end"):
            raise ProtocolError("development has ended; no further development trials")
        if any(r["status"] == "running" and r["directory"] not in in_flight for r in self.data["trials"]):
            raise ProtocolError("a trial has unresolved infrastructure evidence; resolve it before continuing")
        if self.budgeted():
            return self._budget_admission(phase, seed, digest, declaration)
        if phase == PROBE_PHASE:
            raise ProtocolError("the observation-only probe exists only under the aspire-v1 development budget")
        if declaration:
            raise ProtocolError("--mode / --mode-seeds exist only under the aspire-v1 development budget")
        if phase == "snapshot":
            if seed != self.seeds[0] or self.records("snapshot"):
                raise ProtocolError("the observation-only snapshot runs once, on the first development seed")
        elif phase != SWEEP_PHASE:  # A sweep is paid by its own count, never by these retries.
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
            if seed in self.deferred_seeds():
                raise ProtocolError(f"initial seed {seed} was deferred by signature early stop; "
                                    "its banked attempts are for repair")
            frozen = self.initial_bundle()
            if frozen is not None and frozen != digest:
                raise ProtocolError("one initial program: the initial bundle is frozen for the whole batch")
        if phase in ("repair", SWEEP_PHASE):
            # The original workflow triages the whole initial batch before
            # repairing. The gate is missing INITIAL EVIDENCE, never remaining
            # budget: the old `retries_remaining(s) > 0` term let the batch be
            # "finished" by spending a seed's attempts on diagnostic sessions,
            # which grade nothing. Inspection is still never gated this way.
            deferred = self.deferred_seeds()
            pending = [s for s in self.seeds if not self.has_initial_evidence(s) and s not in deferred]
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
        if phase == SWEEP_PHASE:
            self._sweep_admission(seed, digest)
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

    def _sweep_admission(self, seed: int, digest: str) -> None:
        """A sweep re-runs a TESTED candidate's exact bytes on a seed it never ran.

        Limits, all structural: the candidate must already have graded evidence
        (a sweep never tests new bytes — that is what repair is for), and each
        candidate runs each seed at most once (a settled sweep row covers the
        seed, so a second one is refused as already covered; an
        infrastructure-interrupted one executed nothing and may be re-run). There
        is no limit on how many candidates are swept (CC_TASK_4).
        """
        if not self.tested_seeds(digest):
            raise ProtocolError("a sweep re-runs an already tested candidate's exact bytes; "
                                "test new bytes with a charged repair")
        if seed in self.covered_seeds(digest):
            raise ProtocolError(f"seed {seed} is already covered by this candidate's own executions; "
                                "each candidate is swept at most once per seed")

    def _resweep_admission(self, seed: int, digest: str, in_flight: frozenset) -> dict:
        """The outer pre-freeze re-sweep (CC_TASK_14): planned candidates, once per seed.

        Only after development has ended (the outer `end-development` settled the
        ledger) and before the freeze; only the candidates `resweep_plan` fixed;
        each (candidate, seed) settles at most one re-sweep row. It spends nothing.
        """
        if not self.switch("prefreeze_resweep"):
            raise ProtocolError("this cell has no pre-freeze re-sweep (method switch prefreeze_resweep)")
        if not self.data.get("development_end"):
            raise ProtocolError("the pre-freeze re-sweep runs only after development has ended")
        if any(r["status"] == "running" and r["directory"] not in in_flight for r in self.data["trials"]):
            raise ProtocolError("a trial has unresolved infrastructure evidence; resolve it before continuing")
        plan = self.data.get("prefreeze_resweep") or {}
        if digest not in plan.get("candidates", []):
            raise ProtocolError("the pre-freeze re-sweep runs only the candidates its plan fixed")
        if any(r["seed"] == seed and r["bundle_sha256"] == digest and r["status"] in RESOLVED_STATUSES
               for r in self.records(RESWEEP_PHASE)):
            raise ProtocolError(f"seed {seed} is already re-swept for this candidate")
        return {"revision": BUDGET_REVISION, "phase": "B", "resweep": True}

    def swept_bundles(self) -> list[str]:
        """Candidates that have begun a sweep, in first-sweep order."""
        seen = []
        for row in self.records(SWEEP_PHASE):
            if row["bundle_sha256"] not in seen:
                seen.append(row["bundle_sha256"])
        return seen

    def covered_seeds(self, digest: str) -> list[int]:
        """Seeds a candidate's own settled graded executions cover.

        Under `version_score: sweep_only` (CC_TASK_14) only its sweep rows cover
        a seed, so the seed a version was admitted on is swept like every other.
        """
        return sorted({r["seed"] for r in self.score_rows(digest)})

    def tested_seeds(self, digest: str) -> list[int]:
        """Seeds any settled graded execution of these bytes ran (repair rows included)."""
        return sorted({r["seed"] for r in self.graded() if r["bundle_sha256"] == digest})

    def score_rows(self, digest: str) -> list[dict]:
        """The graded rows a candidate's coverage and score are computed from."""
        rows = [r for r in self.graded() if r["bundle_sha256"] == digest]
        if self.sweep_only():
            rows = [r for r in rows if r["phase"] == SWEEP_PHASE]
        return rows

    def begin_trial(self, phase: str, seed: int, bundle: dict, sources: dict,
                    declaration: dict | None = None, *, in_flight: frozenset = frozenset()) -> dict:
        digest = bundle_identity(bundle)
        budget = self.validate_admission(phase, seed, digest, declaration, in_flight=in_flight)
        if phase in (SWEEP_PHASE, PROBE_PHASE, RESWEEP_PHASE):
            # Sweep (and probe, re-sweep) rows number their own attempts, so they never
            # collide with a later charged repair directory on the same seed.
            attempt = len(self.records(phase, seed)) + 1
        else:
            attempt = self.retries_used(seed) + 1 if phase != "snapshot" else 1
        relative = Path("development") / phase / f"seed_{seed}" / f"attempt_{attempt}"
        directory = self.task_dir / relative
        directory.mkdir(parents=True, exist_ok=False)
        record = {"phase": phase, "seed": seed, "attempt": attempt, "status": "running",
                  "directory": str(relative), "spends_retry": phase in RETRY_PHASES,
                  "executed": True, "bundle_sha256": digest, "bundle": bundle,
                  "sources": sources}
        if budget is not None:
            record["budget"] = budget
        self.data["trials"].append(record)
        self._resolve_blockers(phase, seed, digest, str(relative))
        self.save()
        return record

    def finish_trial(self, record: dict, *, result: dict | None, exit_code: int | None,
                     error: str = "", world_error: dict | None = None,
                     raw_result: dict | None = None,
                     diagnostic_error: dict | None = None,
                     watchdog: dict | None = None) -> None:
        record.update(process_exit_code=exit_code, error=error)
        if watchdog:
            # CC_TASK_12: the trial watchdog fired; its verdict travels with the row.
            record["watchdog"] = {k: v for k, v in watchdog.items() if k != "result"}
        if diagnostic_error and not error:
            # The REPL reached its own end and its authored code raised. The exit
            # code is 0 either way, so the artifact — not the exit status — says
            # so. The retry stays spent: a simulator process really ran. It
            # is never graded evidence; see RESOLVED_STATUSES.
            record.update(status="diagnostic_program_error",
                          diagnostic_error=diagnostic_error, sandbox_rc=1,
                          **self.failure_outcome("diagnostic session raised; grades nothing"),
                          trial_dir=record["directory"],
                          session=PROBE_PHASE if record["phase"] == PROBE_PHASE else "diagnostic")
        elif result is not None and (exit_code == 0 or (watchdog or {}).get("settled")):
            # A settled watchdog verdict (CC_TASK_12): the outcome the replay had
            # recorded before a kill during artifact saving, or a policy that ran
            # out of its budget, which is a failed trial like any other.
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
                                 and not self.budgeted() and self.retries_remaining(seed) > 0),
            "world_program_errors": [r["directory"] for r in self.records(seed=seed)
                                     if r["status"] == "world_program_error"],
            "infrastructure_errors": [r["directory"] for r in self.records(seed=seed)
                                      if r["status"] not in NOT_INFRASTRUCTURE
                                      and r["phase"] != RESWEEP_PHASE],
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
                if r["phase"] in EXECUTED_PHASES and r["status"] in RESOLVED_STATUSES
                and r.get("executed", True) and not r.get("alias_of")]

    def graded(self) -> list[dict]:
        """Executions that actually graded the candidate on a development seed."""
        return [r for r in self.executed() if r["phase"] != "diagnostic"]

    def best_score(self, candidates: dict[str, dict]) -> tuple[int, int]:
        return max((len(row["passes"]), -row["crashes"]) for row in candidates.values())

    def progress(self) -> dict:
        seeds = [self.outcome(s) for s in self.seeds]
        candidates = self.candidates()
        deferred = self.deferred_seeds()
        # aspire-v1 has no per-seed cap, so the per-seed "needs initial /
        # exhausted / pending repair / BLOCKED note" lists do not apply and are
        # empty; the budget's own state is `development_budget` below.
        budget = self.budgeted()
        result = {
            "retry_limit": None if budget else self.RETRY_LIMIT,
            "retry_phases": list(RETRY_PHASES),
            "snapshot_complete": bool(self.records("snapshot")
                                      and self.records("snapshot")[0]["status"] == "complete"),
            "seeds": seeds,
            "retries_used_per_seed": {str(s["seed"]): s["retries_used"] for s in seeds},
            "retries_remaining_per_seed": {str(s["seed"]): s["retries_remaining"] for s in seeds},
            "seeds_needing_initial": [] if budget else [
                s["seed"] for s in seeds
                if not s["has_initial_evidence"] and s["retries_remaining"] > 0
                and s["seed"] not in deferred],
            # Present only after a p3-search early stop, so other ledgers are unchanged.
            **({"seeds_deferred_initial": deferred,
                "banked_attempts": self.data.get("search_control", {}).get("banked_attempts", 0)}
               if deferred else {}),
            "seeds_exhausted_without_initial": [] if budget else [
                s["seed"] for s in seeds
                if not s["has_initial_evidence"] and not s["retries_remaining"]],
            # The subset with no graded observation whatsoever: every attempt was
            # a diagnostic session or an infrastructure failure. Reported apart
            # from the line above because only this one means the development work
            # never happened.
            "seeds_exhausted_without_graded_evidence": [] if budget else [
                s["seed"] for s in seeds
                if not s["has_graded_evidence"] and not s["retries_remaining"]],
            "seeds_passing": [s["seed"] for s in seeds if s["passed"]],
            # A failed development seed is finished only by a successful later
            # attempt or by an exhausted budget plus its recorded blocker.
            "seeds_pending_repair": [s["seed"] for s in seeds if s["needs_repair"]],
            "blocked_notes_required": [] if budget else [s["seed"] for s in seeds if self._needs_note(s)],
            "world_program_errors": [r["directory"] for r in self.data["trials"]
                                     if r["status"] == "world_program_error"],
            "infrastructure_errors": [r["directory"] for r in self.data["trials"]
                                      if r["status"] not in NOT_INFRASTRUCTURE
                                      and r["phase"] != RESWEEP_PHASE],
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
        if budget:
            result["development_budget"] = self.budget_status()
        if self.data.get("prefreeze_resweep"):
            # CC_TASK_14: outer-only rows after development end. An unsettled one
            # only leaves its candidate out of the merged comparison; it blocks nothing.
            result["prefreeze_resweep"] = {
                "plan": self.data["prefreeze_resweep"], "scores": self.resweep_scores(),
                "infrastructure_errors": [r["directory"] for r in self.records(RESWEEP_PHASE)
                                          if r["status"] not in NOT_INFRASTRUCTURE]}
        return result

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
        # A sweep never spent a retry, so it has none to keep; it stays uncharged.
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
        """Freeze the reviewed selection with its bundle identity and reason.

        p4: the reason is re-derived against the ledger before it is stored, and
        any pass-count claim the ledger cannot evidence is kept as a warning on
        the row. Storing it silently is how p3's overclaim ("passed 12 of 15")
        reached the record for bytes that ran six.
        """
        digest = bundle_identity(bundle)
        self.data["selected"] = {"bundle_sha256": digest, "bundle": bundle, "reason": reason}
        evidence = self.reason_evidence(reason)
        self.data["selected"]["selection_evidence"] = evidence
        if evidence["warnings"]:
            self.data["selected"]["warnings"] = list(evidence["warnings"])
        self.save()
        return self.data["selected"]

    def coverage_applies(self) -> bool:
        """Is this a cell whose frozen bundle must cover the whole development set?"""
        return self.data["identity"].get("prediction_contract") == COVERAGE_CONTRACT

    def freezable(self, digest: str) -> dict:
        """Could this tested candidate be frozen, and is its record already final?

        Without the coverage gate every tested candidate is freezable and its
        record is final as it stands. Under it, a candidate is freezable when its
        coverage is complete or it can still be swept to completion, and its
        crash count is FINAL only once it covers every development seed; before
        that it is a lower bound — a sweep can still add crashes, never remove
        them.
        """
        if not self.coverage_applies():
            return {"freezable": True, "complete": True, "missing": []}
        gate = self._coverage(digest)
        return {"freezable": gate["complete"] or gate["payable"], "complete": gate["complete"],
                "missing": gate["missing"]}

    def tie_break(self, bundle: dict) -> dict:
        """The all-fail fallback, compared among FREEZABLE candidates only (CC_TASK_4).

        Applies only when no tested bundle succeeded. The selected bundle passes
        when no freezable candidate scores strictly better on (passes, -crashes);
        a tie is the best. A candidate that can never be frozen is not an
        alternative, so it can never make the rule unsatisfiable.

        Severity, decided structurally:
          * substantive — some strictly better candidate is COMPLETE (its record
            is final and `select` can switch to it now), or the selected bundle
            is itself still partially covered. Ready is withheld.
          * advisory — the selected bundle is complete, and every strictly better
            candidate is still partially covered: its smaller crash count is a
            lower bound over fewer seeds, compared against a final record over
            the whole set (goaltaskbowldrawer_p4n_r1's selection also had zero
            crashes until its own sweep found one). Reported beside the result;
            it does not block ready.
        """
        digest = bundle_identity(bundle)
        candidates = self.candidates()
        if digest not in candidates or any(row["passes"] for row in candidates.values()):
            return {"applies": False}
        score = {d: (len(row["passes"]), -row["crashes"]) for d, row in candidates.items()}
        status = {d: self.freezable(d) for d in candidates}
        better = [d for d in candidates if status[d]["freezable"] and score[d] > score[digest]]
        final = [d for d in better if status[d]["complete"]]
        selected_complete = status[digest]["complete"]
        if not better:
            severity = None
        elif final or not selected_complete:
            severity = "substantive"
        else:
            severity = "advisory"
        return {"applies": True, "severity": severity,
                "selected": {"bundle_sha256": digest, "crashes": candidates[digest]["crashes"],
                             "complete": selected_complete},
                "better_freezable": [{"bundle_sha256": d, "crashes": candidates[d]["crashes"],
                                      "complete": status[d]["complete"],
                                      "covered": len(self.seeds) - len(status[d]["missing"])}
                                     for d in better],
                "excluded_unfreezable": sorted(d for d in candidates if not status[d]["freezable"])}

    def evidence_issues(self, bundle: dict) -> list[dict]:
        """The original selection contract, expressed over tested bundles only.

        A selected bundle must have been executed, and it must rest on development
        success evidence. When some bundle succeeded, the selection must be one
        that succeeded — a repaired synthesis tested on only the failing seeds is
        acceptable even though its raw pass count is smaller than the initial
        program's, so no always-maximum-pass-count gate is applied. Only when NO
        bundle succeeded does the original most-successes / fewer-crashes fallback
        decide, exactly as the pristine prompt words it — among freezable
        candidates (`tie_break`). Each issue carries a `code` and a `severity`
        so callers never parse the message.
        """
        if self.budgeted():
            return self.budget_issues(bundle)
        digest = bundle_identity(bundle)
        candidates = self.candidates()
        if digest not in candidates:
            return [{"code": "selection_untested", "severity": "substantive",
                     "message": "the selected bundle was never tested; test the synthesis while a "
                                "slot remains, or select an already tested bundle"}]
        if candidates[digest]["passes"]:
            return []
        if any(row["passes"] for row in candidates.values()):
            return [{"code": "selection_without_success", "severity": "substantive",
                     "message": "selection must use development success evidence: a bundle that "
                                "succeeded on a development seed exists — not the last noncrashing version"}]
        rule = self.tie_break(bundle)
        if rule["severity"]:
            return [{"code": "all_fail_tie_break", "severity": rule["severity"],
                     "message": TIE_BREAK_ERROR, "tie_break": rule}]
        return []

    def evidence_errors(self, bundle: dict) -> list[str]:
        """Substantive selection errors only; an advisory tie-break never blocks."""
        return [i["message"] for i in self.evidence_issues(bundle) if i["severity"] == "substantive"]

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
        if self.budgeted():
            # aspire-v1: `select` itself refuses phase A, an incomplete or non-best
            # selection and an unmet freeze condition; report files wait for `check`.
            errors = [i["message"] for i in self.budget_issues(bundle)
                      if i["severity"] == "substantive" and i["code"] in self.SELECT_REFUSES]
            if not errors and self.budget_phase() == "B":
                errors += self.coverage_errors(bundle)
            return errors
        if self.observation_fallback(bundle, source):
            return []
        return self.evidence_errors(bundle)

    def completion_errors(self, *, bundle: dict, working_code: Path,
                          world_required: bool) -> list[str]:
        """Substantive completion errors (messages); see `completion_issues`."""
        return [i["message"] for i in self.completion_issues(
            bundle=bundle, working_code=working_code, world_required=world_required)
            if i["severity"] == "substantive"]

    def completion_issues(self, *, bundle: dict, working_code: Path,
                          world_required: bool) -> list[dict]:
        """Every completion issue as {code, severity, message}.

        `severity` is "substantive" (blocks ready) except an advisory all-fail
        tie-break (`tie_break`). `code` names the issue class so `check` decides
        terminal/ready from structure, never from message text.
        """
        issues = []

        def add(code: str, message: str) -> None:
            issues.append({"code": code, "severity": "substantive", "message": message})

        progress = self.progress()
        # CC_TASK_9: rows the outer development end judged to concern another
        # candidate than the frozen one are reported, not blocking (and only then).
        tolerated = {item.get("directory") for item in
                     (self.data.get("development_end") or {}).get("unresolved_not_blocking", [])}
        for key in ("seeds_needing_initial", "seeds_exhausted_without_graded_evidence",
                    "seeds_pending_repair", "blocked_notes_required", "infrastructure_errors"):
            if key == "infrastructure_errors" and progress[key] and set(progress[key]) <= tolerated:
                issues.append({"code": key, "severity": "advisory", "message": (
                    f"{key} on candidates other than the selected one, left unresolved at development "
                    f"end: {progress[key]}")})
            elif progress[key]:
                add(key, f"{key}: {progress[key]}")
        if self.budgeted() and progress["development_budget"]["probe_violations"]:
            # A probe that moved the robot was an unbudgeted experiment. Nothing
            # the solver can do afterwards turns it back into an observation.
            add("probe_violations", "probe sessions moved the robot (no motor command is allowed in a "
                f"probe): {progress['development_budget']['probe_violations']}")
        # `seeds_exhausted_without_*` was computed and reported but never checked,
        # so a seed that spent its budget without producing any result did not
        # block completion. What must block is missing GRADED evidence: attempts
        # spent on diagnostic sessions or lost to infrastructure observed nothing
        # and cannot finish development work. A seed whose budget went to real
        # graded executions is finished the ordinary way — its missing initial
        # coverage is still reported in `progress`, and `blocked_notes_required`
        # still demands its blocker note.
        if not progress["graded_executions"]:
            add("no_graded_execution", "no graded development execution exists; diagnostic "
                "sessions are inspection and grade nothing")
        if not progress["snapshot_complete"]:
            add("snapshot_missing", "missing completed scene snapshot")
        analysis = self.task_dir / "task_analysis.md"
        if not analysis.is_file() or not analysis.read_text().strip():
            add("report", "missing task_analysis.md")
        for message in report_errors(self.task_dir / "findings.md", (
                "Root causes observed", "What fixed them", "Generalizable patterns", "Blocked seeds")):
            issues.append({"code": "findings_format", "severity": ("advisory" if self.data.get("development_end")
                            else "substantive"), "message": message})
        fix = self.task_dir / "fix_code.py"
        if not fix.is_file() or not valid_program(fix.read_text()):
            add("report", "missing executable fix_code.py")
            return issues
        source = fix.read_text()
        if not working_code.is_file() or working_code.read_text() != source:
            add("report", "working_codes copy must match fix_code.py")
        if bundle.get("policy") != code_hash(source):
            add("report", "the selected bundle's policy digest must match fix_code.py")
        if world_required:
            companions = [("world", "fix_world_program.py")]
            if self.data["identity"].get("profile") not in CODE_WORLD_PROFILES:
                companions.append(("inventory", "fix_inventory.json"))
            for key, name in companions:
                path = self.task_dir / name
                if not path.is_file() or bundle.get(key) != code_hash(path.read_text()):
                    add("report", f"world condition requires {name} matching the selected bundle")
        elif set(bundle) != {"policy"}:
            add("report", "the ordinary condition selects a policy-only bundle")
        candidates = self.candidates()
        if self.observation_fallback(bundle, source):
            return issues  # Documented single-observation fallback, explicitly labeled.
        if not candidates:
            add("no_tested_bundle", "no tested bundle exists; the final program must have been executed")
            return issues
        return issues + self.evidence_issues(bundle)

    def final_coverage(self, bundle: dict) -> list[dict]:
        """Real executions of the frozen bundle. An alias row is not a second run."""
        digest = bundle_identity(bundle)
        return [r for r in self.executed()
                if r["phase"] != "diagnostic" and r["bundle_sha256"] == digest]

    # ---- p4 coverage gate ---------------------------------------------------

    def coverage_gate(self, bundle: dict) -> dict:
        """Which development seeds the candidate's OWN executed trials cover.

        The bundle that a cell freezes is only evidence once it has been run on
        every development seed: selection on partial coverage is how a
        development-fitted candidate reaches held-out with an unknown failure
        rate. Coverage is computed from the bundle's own executions, so a pass
        earned by a *different* bundle never counts for the frozen bytes.

        Missing seeds are swept (`trial --phase sweep`) with the exact bytes. A
        sweep is counted apart from the per-seed repair retries (CC_TASK_2 design
        (b)), so every number here is the CURRENT availability, read from the
        ledger now: `sweepable` are the missing seeds this bundle may sweep at
        this moment, `unsweepable` the ones it may not, each with the reason.
        `banked_attempts_at_early_stop` is the early stop's historical snapshot,
        kept only as a record; it pays for nothing.
        """
        return self._coverage(bundle_identity(bundle))

    def _coverage(self, digest: str) -> dict:
        covered = self.covered_seeds(digest)
        missing = [s for s in self.seeds if s not in covered]
        tested = bool(self.tested_seeds(digest))  # A candidate is exactly a digest with graded evidence.
        swept = self.swept_bundles()
        # The only structural reason a missing seed cannot be swept: the bytes were
        # never tested. There is no per-cell candidate cap (CC_TASK_4).
        blocked = None if tested else "untested bytes cannot be swept; test them with a charged repair first"
        if tested and self.budgeted():
            # aspire-v1: nothing is swept before the first seed passes, and only
            # v0 and the phase-B versions are swept after it (CC_TASK_6).
            if self.budget_phase() == "A":
                blocked = f"phase A: no sweep before seed {self.seeds[0]} has passed"
            elif digest not in self.phase_b_candidates():
                blocked = "phase B sweeps only v0 and the phase-B versions"
        unsweepable = {str(s): blocked for s in missing} if blocked else {}
        sweepable = [] if blocked else list(missing)
        return {"required_seeds": list(self.seeds), "covered_seeds": covered,
                "missing": missing,
                # Sweep runs needed; none of them is charged to a repair retry.
                "sweep_charged": len(missing), "sweep_runs_needed": len(missing),
                "sweep_accounting": "separate: a sweep run spends no per-seed repair retry",
                "sweepable": sweepable, "unsweepable": unsweepable,
                "payable": not missing or not blocked,
                "candidate_tested": tested, "swept_bundles": swept,
                "sweep_candidate_limit": None,
                "this_bundle_swept": digest in swept,
                "banked_attempts_at_early_stop": self.data.get("search_control", {}).get("banked_attempts", 0),
                "retries_remaining_per_missing": {str(s): self.retries_remaining(s) for s in missing},
                **({"budget_phase": self.budget_phase()} if self.budgeted() else {}),
                "complete": not missing}

    def coverage_errors(self, bundle: dict) -> list[str]:
        """The p4 freeze gate: refuse `selected` until the sweep covers 51–65.

        Two distinct failures, both loud:
          * the sweep is not finished (missing seeds that may still be swept);
          * this bundle CANNOT be swept, which names the reason and is never a
            reason to relax the gate. Whether the CELL can still become ready
            (another candidate, or none at all) is `coverage_reachability`.
        """
        gate = self.coverage_gate(bundle)
        if gate["complete"]:
            return []
        if not gate["payable"]:
            reason = next(iter(gate["unsweepable"].values()))
            return [f"coverage gate BLOCKED: seeds {gate['missing']} are missing from this bundle's own "
                    f"executions and it cannot sweep them ({reason}). Select a candidate that is covered "
                    "or can be swept; the gate is never lowered"]
        return [f"coverage gate: the frozen bundle has not run development seeds {gate['missing']} "
                f"({len(gate['covered_seeds'])}/{len(gate['required_seeds'])} covered); sweep them with "
                f"these exact bytes (`trial --phase sweep`, {gate['sweep_runs_needed']} run(s), none "
                "charged to the per-seed repair retries) before recording `selected`"]

    def coverage_reachability(self, bundle: dict) -> dict:
        """Can ANY candidate still satisfy the coverage gate? Structured, never prose.

        The gate is reachable while the on-disk bundle is complete or sweepable,
        or another tested candidate is, or a new candidate can still be tested
        (some seed has a repair retry left). Otherwise no amount of further work
        can make the cell ready, and `check` reports it as terminal.
        """
        own = self.coverage_gate(bundle)
        if self.budgeted():
            return self._budget_reachability(own)
        viable = []
        for digest in dict.fromkeys(r["bundle_sha256"] for r in self.graded()):
            gate = self._coverage(digest)
            if gate["complete"] or gate["payable"]:
                viable.append({"bundle_sha256": digest, "complete": gate["complete"],
                               "missing": gate["missing"]})
        retries_left = sum(self.retries_remaining(s) for s in self.seeds)
        reachable = bool(own["complete"] or own["payable"] or viable or retries_left)
        return {"reachable": reachable, "selected_complete": own["complete"],
                "selected_payable": own["payable"], "viable_candidates": viable,
                "retries_remaining_total": retries_left,
                "reason": None if reachable else (
                    "no tested candidate covers or can sweep the development set, and no seed has a "
                    "repair retry left to test a new one")}

    def _budget_reachability(self, own: dict) -> dict:
        """aspire-v1: phase A is always reachable (attempts on the first seed are
        uncapped); phase B while a phase-B candidate is complete or sweepable, or a
        version is left."""
        if self.budget_phase() == "A":
            return {"reachable": True, "selected_complete": own["complete"], "selected_payable": own["payable"],
                    "viable_candidates": [], "retries_remaining_total": None, "phase": "A",
                    "reason": None}
        viable = []
        for digest in self.phase_b_candidates():
            gate = self._coverage(digest)
            if gate["complete"] or gate["payable"]:
                viable.append({"bundle_sha256": digest, "complete": gate["complete"], "missing": gate["missing"]})
        left = max(0, MAX_VERSIONS - len(self.versions()))
        reachable = bool(own["complete"] or own["payable"] or viable or left)
        return {"reachable": reachable, "selected_complete": own["complete"],
                "selected_payable": own["payable"], "viable_candidates": viable,
                "retries_remaining_total": None, "versions_left": left, "phase": "B",
                "reason": None if reachable else "no phase-B candidate covers or can sweep the development "
                                                "set and no version is left"}

    def legal_actions(self, bundle: dict) -> dict:
        """Every protocol action that could still change this cell's `check` (CC_TASK_4).

        The generic terminal guard reads this: when `check` still has errors and
        every list here is empty, nothing the solver can do changes the outcome,
        and the cell is terminal rather than `incomplete`. Each entry is an
        action the ledger would admit right now:
          * charged_retries — seeds with a repair retry left (new bytes can be tested);
          * pending_repairs — seeds the protocol itself says still owe a repair;
          * sweeps          — tested candidates with missing development seeds
                              they may sweep (coverage-gate cells only);
          * better_selection — freezable candidates `select` could switch to that
                              would beat the selected bundle under the
                              all-fail tie-break.
        Report files (findings, BLOCKED notes, working copy) are not counted:
        they are always writable, so an error about them is never terminal.
        """
        if self.budgeted():
            return self._budget_legal_actions(bundle)
        progress = self.progress()
        retries = {str(s): self.retries_remaining(s) for s in self.seeds if self.retries_remaining(s)}
        sweeps = {}
        if self.coverage_applies():
            for digest in dict.fromkeys(r["bundle_sha256"] for r in self.graded()):
                gate = self._coverage(digest)
                if gate["sweepable"]:
                    sweeps[digest] = gate["sweepable"]
        try:
            rule = self.tie_break(bundle)
        except ProtocolError:
            rule = {"applies": False}
        better = [row["bundle_sha256"] for row in rule.get("better_freezable", [])]
        return {"charged_retries": retries, "pending_repairs": progress["seeds_pending_repair"],
                "sweeps": sweeps, "better_selection": better,
                "any": bool(retries or progress["seeds_pending_repair"] or sweeps or better)}

    # ---- p5 development budget (aspire-v1, CC_TASK_6) -----------------------

    def _graded_row(self, row: dict) -> bool:
        return (row["phase"] in EXECUTED_PHASES and row["phase"] != "diagnostic"
                and row["status"] in RESOLVED_STATUSES and row.get("executed", True)
                and not row.get("alias_of"))

    def first_pass(self) -> tuple[int, dict] | None:
        """(ledger index, row) of the first graded PASS on the first development seed."""
        first = self.seeds[0]
        for index, row in enumerate(self.data["trials"]):
            if row["seed"] == first and self._graded_row(row) and self.passed(row):
                return index, row
        return None

    def budget_phase(self) -> str:
        """"A" until the first development seed has actually passed, then "B"."""
        return "A" if self.first_pass() is None else "B"

    def versions(self) -> list[dict]:
        """Phase-B versions in admission order, from the metadata stored on their rows.

        A version is new bytes admitted in phase B. Re-admitting the same bytes
        after an infrastructure loss (no graded evidence yet) keeps its number,
        so a lost run never costs a second version.
        """
        out, seen = [], set()
        for row in self.data["trials"]:
            meta = row.get("budget") or {}
            if meta.get("version") and row["bundle_sha256"] not in seen:
                seen.add(row["bundle_sha256"])
                out.append({"version": meta["version"], "bundle_sha256": row["bundle_sha256"],
                            "mode": meta["mode"], "target_seeds": list(meta["target_seeds"]),
                            "first_seed": row["seed"], "directory": row["directory"]})
        return out

    def phase_b_candidates(self) -> list[str]:
        """v0 (the bundle that first passed the first seed), then every phase-B version."""
        first = self.first_pass()
        if first is None:
            return []
        out = [first[1]["bundle_sha256"]]
        out += [v["bundle_sha256"] for v in self.versions() if v["bundle_sha256"] not in out]
        return out

    def _candidate_record(self, digest: str, candidates: dict) -> dict:
        row = candidates.get(digest) or {"passes": [], "crashes": 0, "trials": []}
        if self.sweep_only():
            # CC_TASK_14 M2(a): the repair trial that made its target seed pass is
            # the worker's evidence, not the version's score; its sweep rows are.
            rows = self.score_rows(digest)
            row = {"passes": sorted({r["seed"] for r in rows if self.passed(r)}),
                   "crashes": sum(int(r["sandbox_rc"] != 0) for r in rows)}
        covered = self.covered_seeds(digest)
        return {"bundle_sha256": digest, "covered_seeds": covered,
                "missing": [s for s in self.seeds if s not in covered],
                "complete": len(covered) == len(self.seeds),
                "passes": list(row["passes"]), "crashes": row["crashes"],
                "failing_seeds": [s for s in covered if s not in row["passes"]],
                "score": [len(row["passes"]), -row["crashes"]]}

    def budget_candidates(self) -> list[dict]:
        """Every phase-B candidate with its own coverage and (passes, -crashes) record."""
        candidates = self.candidates()
        labels = {v["bundle_sha256"]: f"v{v['version']}" for v in self.versions()}
        out = []
        for index, digest in enumerate(self.phase_b_candidates()):
            label = "v0" if index == 0 else labels[digest]
            out.append({"label": label, **self._candidate_record(digest, candidates)})
        return out

    def best_complete(self) -> dict | None:
        """The selection rule: most passes, then fewer crashes (program_ok=false), among
        phase-B candidates that ran every development seed. Ties are listed together;
        between them the simpler one is the solver's documented choice."""
        complete = [c for c in self.budget_candidates() if c["complete"]]
        if not complete:
            return None
        merged = self.resweep_scores()
        pool = [c for c in complete if (merged.get(c["bundle_sha256"]) or {}).get("complete")]
        if pool:
            # CC_TASK_14 M2(b): once the pre-freeze re-sweep of a candidate is
            # complete, the comparison is over the re-swept candidates on both runs.
            score = {c["bundle_sha256"]: tuple(merged[c["bundle_sha256"]]["merged_score"]) for c in pool}
            top = max(score.values())
            return {"score": list(top), "bundle_sha256": [d for d, v in score.items() if v == top],
                    "evidence": "sweep + pre-freeze re-sweep",
                    "trials_per_candidate": 2 * len(self.seeds)}
        top = max(tuple(c["score"]) for c in complete)
        return {"score": list(top), "bundle_sha256": [c["bundle_sha256"] for c in complete
                                                       if tuple(c["score"]) == top]}

    # ---- pre-freeze re-sweep (CC_TASK_14 M2(b)) -----------------------------

    def resweep_plan(self) -> dict:
        """Which complete candidates the outer re-sweep runs, from the sweep-only record.

        Every complete phase-B candidate within `margin` passes of the best one,
        best first and, between equal scores, the earlier one first (it was found
        with less search); at most `max_candidates`. The candidate the ledger has
        selected is always included, replacing the last one if the cap is full.
        """
        settings = self.switch("prefreeze_resweep") or {}
        complete = [c for c in self.budget_candidates() if c["complete"]]
        if not settings or not complete:
            return {"candidates": [], "labels": {}, "settings": settings, "best_sweep_score": None,
                    "reason": "no complete phase-B candidate" if settings else "switch off"}
        order = {c["bundle_sha256"]: i for i, c in enumerate(complete)}
        best = max(tuple(c["score"]) for c in complete)
        near = [c for c in complete if c["score"][0] >= best[0] - settings["margin"]]
        near.sort(key=lambda c: (-c["score"][0], -c["score"][1], order[c["bundle_sha256"]]))
        chosen = [c["bundle_sha256"] for c in near[:settings["max_candidates"]]]
        selected = (self.data.get("selected") or {}).get("bundle_sha256")
        if selected in order and selected not in chosen:
            chosen[-1] = selected
        labels = {c["bundle_sha256"]: c["label"] for c in complete}
        return {"candidates": chosen, "labels": {d: labels[d] for d in chosen}, "settings": settings,
                "best_sweep_score": list(best),
                "sweep_scores": {d: next(c["score"] for c in complete if c["bundle_sha256"] == d)
                                 for d in chosen}}

    def resweep_scores(self) -> dict:
        """Per planned candidate: the re-sweep record and the merged (sweep + re-sweep) score."""
        plan = self.data.get("prefreeze_resweep") or {}
        if not plan.get("candidates"):
            return {}
        candidates = self.candidates()
        out = {}
        for digest in plan["candidates"]:
            sweep = self._candidate_record(digest, candidates)
            rows = [r for r in self.records(RESWEEP_PHASE)
                    if r["bundle_sha256"] == digest and r["status"] in RESOLVED_STATUSES]
            covered = sorted({r["seed"] for r in rows})
            passes = sorted({r["seed"] for r in rows if self.passed(r)})
            crashes = sum(int(r.get("sandbox_rc") != 0) for r in rows)
            agree = [s for s in covered if (s in passes) == (s in sweep["passes"])]
            out[digest] = {"label": plan.get("labels", {}).get(digest), "sweep_score": sweep["score"],
                           "resweep_passes": passes, "resweep_crashes": crashes,
                           "resweep_covered": covered,
                           "complete": len(covered) == len(self.seeds) and sweep["complete"],
                           "merged_score": [len(sweep["passes"]) + len(passes), sweep["score"][1] - crashes],
                           "seeds_agreeing": len(agree),
                           "flipped_seeds": [s for s in covered if s not in agree]}
        return out

    def mode_table(self, reference: str | None = None) -> dict:
        """Declared failure modes: versions, target seeds, exhausted / blocked.

        A mode is EXHAUSTED once VERSIONS_PER_MODE versions targeted it (no further
        version may), and BLOCKED when it is exhausted and still unfixed: some of
        its target seeds still fail under the reference bundle (the selection, or
        the best complete candidate). An exhausted mode the reference passes is
        fixed and needs no note.
        """
        failing = None
        if reference is not None:
            failing = set(self._candidate_record(reference, self.candidates())["failing_seeds"])
        modes = {}
        for v in self.versions():
            row = modes.setdefault(v["mode"], {"versions": [], "target_seeds": []})
            row["versions"].append(v["version"])
            row["target_seeds"] = sorted(set(row["target_seeds"]) | set(v["target_seeds"]))
        for name, row in modes.items():
            row["version_count"] = len(row["versions"])
            row["exhausted"] = row["version_count"] >= VERSIONS_PER_MODE
            row["unfixed_seeds"] = (sorted(set(row["target_seeds"]) & failing)
                                    if failing is not None else None)
            row["blocked"] = row["exhausted"] and (failing is None or bool(row["unfixed_seeds"]))
            row["note"] = str(self.mode_note(name))
        return modes

    def mode_note(self, mode: str) -> Path:
        return self.task_dir / "attempts" / f"mode_{mode}_BLOCKED.md"

    def budget_reference(self, digest: str | None = None) -> str | None:
        """What the freeze conditions are judged on: the given bundle, else the
        recorded selection, else the best complete phase-B candidate."""
        if digest is not None:
            return digest
        selected = (self.data.get("selected") or {}).get("bundle_sha256")
        if selected:
            return selected
        best = self.best_complete()
        return best["bundle_sha256"][0] if best else None

    def freeze_conditions(self, digest: str | None) -> dict:
        """When a phase-B cell may freeze (CC_TASK_6 rule 3).

        all_pass — the bundle ran all 15 development seeds and passed every one;
        versions_exhausted — all MAX_VERSIONS versions are used;
        all_remaining_blocked — every development seed the bundle still fails is
          a target seed of a blocked mode (exhausted, unfixed).
        development_ended — the outer driver ended phase B at deadline, solver
          exit, or no-progress; only a complete candidate can use that condition.
        Only a bundle whose own coverage is complete can satisfy any of them.
        """
        out = {"bundle_sha256": digest, "all_pass": False, "versions_exhausted": False,
               "all_remaining_blocked": False, "met": False, "failing_seeds": None,
               "unattributed_failing_seeds": None, "complete": False}
        if digest is None or self.budget_phase() != "B":
            return out
        record = self._candidate_record(digest, self.candidates())
        modes = self.mode_table(digest)
        blocked_seeds = set().union(*[set(m["target_seeds"]) for m in modes.values() if m["blocked"]] or [set()])
        failing = record["failing_seeds"]
        out.update(complete=record["complete"], failing_seeds=failing,
                   unattributed_failing_seeds=[s for s in failing if s not in blocked_seeds])
        if record["complete"]:
            out["all_pass"] = not failing
            out["versions_exhausted"] = len(self.versions()) >= MAX_VERSIONS
            out["all_remaining_blocked"] = bool(failing) and not out["unattributed_failing_seeds"]
        out["development_ended"] = bool(record["complete"] and self.data.get("development_end"))
        out["met"] = bool(out["all_pass"] or out["versions_exhausted"] or out["all_remaining_blocked"]
                          or out["development_ended"])
        return out

    def budget_status(self, digest: str | None = None) -> dict:
        """The budget block `status`, `check` and `finalize` report (CC_TASK_6 rule 5)."""
        first = self.first_pass()
        phase = "A" if first is None else "B"
        reference = self.budget_reference(digest) if phase == "B" else None
        versions = self.versions()
        candidates = self.budget_candidates()
        modes = self.mode_table(reference) if phase == "B" else {}
        first_seed = self.seeds[0]
        status = {
            "revision": BUDGET_REVISION, "phase": phase, "first_seed": first_seed,
            "phase_a": {"attempts_on_first_seed": len([r for i, r in enumerate(self.data["trials"])
                                                       if r["seed"] == first_seed and r["spends_retry"]
                                                       and (first is None or i <= first[0])]),
                        "attempt_cap": None,
                        "first_pass": (None if first is None else
                                       {k: first[1].get(k) for k in ("phase", "seed", "directory", "bundle_sha256")})},
            "versions_used": len(versions), "versions_max": MAX_VERSIONS,
            "versions_left": max(0, MAX_VERSIONS - len(versions)),
            "mode_version_limit": VERSIONS_PER_MODE,
            "versions": versions, "candidates": candidates,
            "modes": {name: {k: row[k] for k in ("versions", "version_count", "target_seeds", "exhausted",
                                                 "unfixed_seeds", "blocked", "note")}
                      for name, row in modes.items()},
            "mode_versions": {name: row["version_count"] for name, row in modes.items()},
            "blocked_modes": sorted(name for name, row in modes.items() if row["blocked"]),
            "best_complete": self.best_complete(),
            "reference_bundle": reference,
            "freeze_conditions": self.freeze_conditions(reference),
            "incomplete_versions": [{"label": c["label"], "bundle_sha256": c["bundle_sha256"],
                                     "missing": c["missing"]} for c in candidates if not c["complete"]],
            "probes": len(self.records(PROBE_PHASE)),
            "probe_violations": [r["directory"] for r in self.records(PROBE_PHASE)
                                 if r["status"] == PROBE_VIOLATION],
        }
        status["next"] = self._budget_hint(status)
        return status

    def _budget_hint(self, status: dict) -> str:
        if status["phase"] == "A":
            return (f"phase A: iterate on seed {status['first_seed']} until it passes; attempts there "
                    "are uncapped; select and freeze are refused until then")
        if status["incomplete_versions"] and not self.data.get("development_end"):
            pending = status["incomplete_versions"][0]
            return (f"sweep {pending['label']} on seeds {pending['missing']} (`trial --phase sweep`, no "
                    "version spent): every version runs on all development seeds")
        if status["freeze_conditions"]["met"]:
            return "a freeze condition holds: select the best complete version and finish the reports"
        return (f"write version {status['versions_used'] + 1} of {MAX_VERSIONS} for an open failure mode "
                "(`trial --phase repair --mode <mode> --mode-seeds <failing seeds>`)")

    def _budget_admission(self, phase: str, seed: int, digest: str, declaration: dict | None) -> dict:
        """Admission under aspire-v1. Returns the row's budget metadata."""
        first = self.seeds[0]
        phase_now = self.budget_phase()
        meta = {"revision": BUDGET_REVISION, "phase": phase_now}
        if declaration and phase != "repair":
            raise ProtocolError("--mode / --mode-seeds declare a phase-B version; only `--phase repair` takes them")
        if phase == PROBE_PHASE:
            return meta  # Any development seed, either phase; observation only, spends nothing.
        if phase == "snapshot":
            if seed != first or self.records("snapshot"):
                raise ProtocolError("the observation-only snapshot runs once, on the first development seed")
            return meta
        if phase_now == "A":
            if phase == SWEEP_PHASE:
                raise ProtocolError(f"phase A: no sweep before seed {first} has passed; keep iterating on "
                                    f"seed {first}")
            if seed != first:
                raise ProtocolError(f"phase A: every attempt runs on the first development seed {first} until "
                                    f"it actually passes (attempts there are uncapped); seed {seed} comes after")
            if phase == "smoke":
                if self.records("initial"):
                    raise ProtocolError("smoke runs on the first development seed, before the initial program")
                if any(r.get("sandbox_rc") == 0 for r in self.records("smoke")):
                    raise ProtocolError("a smoke already ran without crashing; alias it or proceed to the initial program")
            if phase == "initial":
                snapshot = self.records("snapshot")
                if not snapshot or snapshot[0]["status"] != "complete":
                    raise ProtocolError("capture the scene snapshot before grading the initial program")
                if self.has_initial_evidence(seed):
                    raise ProtocolError(f"initial seed {seed} already has a completed result; iterate with "
                                        "`--phase repair`")
            if phase == "repair" and not self.has_initial_evidence(first):
                raise ProtocolError(f"run the initial program on seed {first} before repairs")
            return meta
        # ---- phase B --------------------------------------------------------
        if phase in ("smoke", "initial"):
            raise ProtocolError(f"phase B: seed {first} has passed; the passing bundle (v0) covers the other "
                                "seeds by `--phase sweep`, and new bytes are a version (`--phase repair "
                                "--mode ... --mode-seeds ...`)")
        if phase == "diagnostic":
            raise ProtocolError("phase B budgets versions: an acting diagnostic session is not available; "
                                "inspect with `--phase probe` (observation only, spends nothing) or write a version")
        candidates = self.phase_b_candidates()
        if phase == SWEEP_PHASE:
            if digest not in candidates:
                raise ProtocolError("phase B sweeps only v0 (the bundle that first passed seed "
                                    f"{first}) and the phase-B versions; these bytes are neither")
            self._sweep_admission(seed, digest)
            return meta
        # phase == "repair": a version
        existing = {v["bundle_sha256"]: v for v in self.versions()}
        if digest in existing:
            if self.tested_seeds(digest):
                raise ProtocolError(f"these bytes are version v{existing[digest]['version']} and already ran; "
                                    "cover its other seeds with `--phase sweep`, or write new bytes")
            # Re-admission after an infrastructure loss: same version, no new count.
            version = existing[digest]
            return {**meta, "version": version["version"], "mode": version["mode"],
                    "target_seeds": version["target_seeds"], "readmission": True}
        if self.tested_seeds(digest):
            raise ProtocolError("these bytes already ran in this cell; a version is new bytes "
                                "(v0 and tested versions are covered by `--phase sweep`)")
        if len(existing) >= MAX_VERSIONS:
            raise ProtocolError(f"all {MAX_VERSIONS} versions are used; select the best complete version "
                                "and freeze with honest remaining failures")
        unswept = [c for c in self.budget_candidates() if not c["complete"]]
        if unswept:
            raise ProtocolError(f"every version runs on all development seeds: sweep {unswept[0]['label']} "
                                f"on {unswept[0]['missing']} (`--phase sweep`) before writing the next version")
        declaration = declaration or {}
        mode = (declaration.get("mode") or "").strip().lower()
        if not MODE_NAME.fullmatch(mode):
            raise ProtocolError("a phase-B version declares the failure mode it targets: `--mode <name>` "
                                "(lower case letters, digits, '_', '-', '.'; at most 63 characters)")
        used = [v for v in existing.values() if v["mode"] == mode]
        if len(used) >= VERSIONS_PER_MODE:
            raise ProtocolError(f"mode {mode!r} already had {VERSIONS_PER_MODE} versions "
                                f"({', '.join('v%d' % v['version'] for v in used)}): it is blocked. Write "
                                f"attempts/mode_{mode}_BLOCKED.md (root cause, what was tried) and spend "
                                "no more versions on it; a renamed mode is the same mode")
        targets = sorted({int(s) for s in declaration.get("target_seeds") or []})
        if not targets:
            raise ProtocolError("a phase-B version names the failing seeds of its mode: `--mode-seeds 53,57`")
        failing = set().union(*[set(c["failing_seeds"]) for c in self.budget_candidates() if c["complete"]])
        foreign = [s for s in targets if s not in failing]
        if foreign:
            raise ProtocolError(f"--mode-seeds {foreign} did not fail under any complete phase-B candidate "
                                f"(failing seeds: {sorted(failing)}); a mode is a group of observed failures")
        if seed not in targets:
            raise ProtocolError(f"run a version first on one of its mode's seeds {targets}, then sweep the rest")
        return {**meta, "version": len(existing) + 1, "mode": mode, "target_seeds": targets}

    def budget_issues(self, bundle: dict) -> list[dict]:
        """The selection and freeze contract under aspire-v1, as structured issues."""
        digest = bundle_identity(bundle)
        candidates = self.candidates()
        if digest not in candidates:
            return [{"code": "selection_untested", "severity": "substantive",
                     "message": "the selected bundle was never tested; select a tested phase-B version"}]
        status = self.budget_status(digest)
        first = status["first_seed"]
        if status["phase"] == "A":
            return [{"code": "budget_phase_a", "severity": "substantive",
                     "message": (f"phase A: seed {first} has not passed yet; keep iterating on seed {first} "
                                 "(attempts there are uncapped). select and freeze are refused until it "
                                 "actually passes"),
                     "development_budget": {k: status[k] for k in ("phase", "phase_a")}}]
        issues = []
        if digest not in self.phase_b_candidates():
            issues.append({"code": "selection_not_phase_b", "severity": "substantive",
                           "message": (f"select v0 (the bundle that first passed seed {first}) or a phase-B "
                                       "version; these bytes are neither")})
        if status["incomplete_versions"] and not self.data.get("development_end"):
            pending = status["incomplete_versions"]
            issues.append({"code": "version_coverage", "severity": "substantive",
                           "message": ("every version runs on all development seeds: "
                                       + "; ".join(f"{p['label']} misses {p['missing']}" for p in pending)
                                       + " (`trial --phase sweep`, no version spent)")})
        best = status["best_complete"]
        record = self._candidate_record(digest, candidates)
        if best and record["complete"] and digest not in best["bundle_sha256"]:
            issues.append({"code": "selection_not_best", "severity": "substantive",
                           "message": (f"select the complete version with the most passes, then fewer crashes "
                                       f"(best {best['score'][0]} passes / {-best['score'][1]} crashes: "
                                       f"{', '.join(d[:12] for d in best['bundle_sha256'])}); this one has "
                                       f"{len(record['passes'])} / {record['crashes']}")})
        freeze = status["freeze_conditions"]
        if not freeze["met"]:
            issues.append({"code": "freeze_condition", "severity": "substantive",
                           "message": (f"no freeze condition holds yet: {len(record['passes'])}/{len(self.seeds)} "
                                       f"pass, {status['versions_used']}/{MAX_VERSIONS} versions used, "
                                       f"failing seeds not in a blocked mode {freeze['unattributed_failing_seeds']}"
                                       "; write the next version for an open failure mode"),
                           "freeze_conditions": freeze})
        notes = [name for name in status["blocked_modes"]
                 if report_errors(self.mode_note(name), MODE_NOTE_HEADINGS)]
        if notes:
            issues.append({"code": "blocked_mode_notes_required", "severity": "substantive",
                           "message": ("blocked modes need a note with Root Cause, Details and What Was Tried: "
                                       + ", ".join(f"attempts/mode_{n}_BLOCKED.md" for n in notes))})
        return issues

    #: Budget issues `select` refuses on (report-type ones wait for `check`).
    SELECT_REFUSES = frozenset({"selection_untested", "budget_phase_a", "selection_not_phase_b",
                                "version_coverage", "selection_not_best", "freeze_condition"})

    def _budget_legal_actions(self, bundle: dict) -> dict:
        """aspire-v1 actions that could still change `check` (same keys plus the budget's)."""
        first = self.seeds[0]
        if self.budget_phase() == "A":
            return {"phase": "A", "charged_retries": {str(first): "uncapped"}, "pending_repairs": [],
                    "sweeps": {}, "better_selection": [], "versions_left": None, "any": True}
        sweeps = {}
        for digest in self.phase_b_candidates():
            gate = self._coverage(digest)
            if gate["sweepable"]:
                sweeps[digest] = gate["sweepable"]
        left = max(0, MAX_VERSIONS - len(self.versions()))
        try:
            digest = bundle_identity(bundle)
        except ProtocolError:
            digest = None
        best = self.best_complete()
        better = [] if not best or digest in best["bundle_sha256"] else list(best["bundle_sha256"])
        return {"phase": "B", "charged_retries": {}, "pending_repairs": [], "sweeps": sweeps,
                "better_selection": better, "versions_left": left,
                "any": bool(sweeps or better or left)}

    def reason_evidence(self, reason: str) -> dict:
        """Recompute any pass-count claim in a selection reason from the ledger.

        p3's overclaim ("passed 12 of 15 dev seeds" for bytes that ran six) slipped
        through because nothing re-derived the number the solver wrote. Every
        `N of M` phrase is checked against what the ledger can actually evidence:
        how many development seeds passed with the SELECTED bytes, out of how many
        seeds those bytes ran. A disagreement is recorded as a warning and the
        ledger's own figures are the ones reported.
        """
        from aspire.sim.cap.world_model.search_control import claimed_counts
        selection = self.data.get("selected") or {}
        bundle = selection.get("bundle") or {}
        digits = set(bundle.values())
        if not digits:
            return {"claims": [], "warnings": []}
        digest = selection.get("bundle_sha256")
        covered = sorted({r["seed"] for r in self.executed()
                          if r["phase"] != "diagnostic" and r["bundle_sha256"] == digest})
        passing = sorted({r["seed"] for r in self.executed()
                          if r["phase"] != "diagnostic" and r["bundle_sha256"] == digest
                          and self.passed(r)})
        claims, warnings = [], []
        for count, total in claimed_counts(reason):
            claim = {"claimed_passed": count, "claimed_tested": total,
                     "ledger_passed": len(passing), "ledger_tested": len(covered)}
            claims.append(claim)
            if count > len(passing) or total > len(covered):
                warnings.append(
                    f"selection reason claims {count} of {total} but the selected bundle's own "
                    f"executions give {len(passing)} passed of {len(covered)} tested "
                    f"(passing seeds {passing})")
        return {"claims": claims, "warnings": warnings,
                "ledger_passed": len(passing), "ledger_tested": len(covered),
                "passing_seeds": passing, "tested_seeds": covered}
