"""Append-only accounting for the user's one + fifteen + fifty world study.

No simulator or model imports. Source identities and terminal evidence, rather
than model-written progress statements, admit subsequent phases.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import time

MODEL = "claude-opus-4-6"
SUITE = "libero_goal_swap"
TASK = "put_the_bowl_on_the_plate"
PROTOCOL = {
    "schema": 1, "suite": SUITE, "task": TASK, "model": MODEL,
    "initial_seed": 51, "repair_seeds": list(range(51, 66)),
    "heldout_seeds": list(range(1, 51)), "revision_opportunities": 15,
    "max_tokens": 16000, "transport_attempts_per_slot": 3,
    "max_actions": 30, "query_budget": 4, "max_recovery": 1,
    "trial_timeout_seconds": 900, "tolerance": .03,
    "selection": "latest_noncrashing_else_latest_executed_valid_source",
}
TERMINALS = {"success", "task_failure", "program_error", "infrastructure_error"}

# Explicit, versioned mechanism-admission rule. Frozen into a NEW campaign's
# immutable identity settings at prepare time; PROTOCOL is deliberately
# untouched so existing campaigns still resume. A campaign identity that lacks
# the key keeps its historical selection behaviour; a campaign that carries it
# fails closed when the recorded evidence is absent.
MECHANISM_ADMISSION = {
    "version": 1,
    "rule": "measured_reference_plus_real_relation_query_attempt",
    "require_reference_established": True,
    "require_relation_query_attempt": True,
    "require_task_success": False,
    "require_support_verdict": False,
    "require_decisive_verdict": False,
    "evidence_fields": ["world_reference_established", "world_relation_measurement_attempts"],
}
MECHANISM_ADMISSION_VERSIONS = {1: MECHANISM_ADMISSION}

# The plain-policy condition has no world mechanism to exercise, so requiring
# mechanism evidence would reject every candidate it could ever produce. This is
# an explicit, separately named rule rather than the absence of a rule: the
# identity states positively that no world evidence exists to admit, which is a
# different claim from a historical campaign that simply predates the gate.
NO_MECHANISM_ADMISSION = {
    "version": 1,
    "rule": "no_world_mechanism_present",
    "require_reference_established": False,
    "require_relation_query_attempt": False,
    "require_task_success": False,
    "require_support_verdict": False,
    "require_decisive_verdict": False,
    "evidence_fields": [],
}
# Keyed by (rule name, version) so a weaker rule can never be reached by
# reusing a stronger rule's name, and so adding one does not renumber the other.
MECHANISM_ADMISSION_RULES = {
    (MECHANISM_ADMISSION["rule"], 1): MECHANISM_ADMISSION,
    (NO_MECHANISM_ADMISSION["rule"], 1): NO_MECHANISM_ADMISSION,
}

# Explicit, versioned per-condition source schema. A campaign identity that does
# not name one keeps the historical world/policy/inventory triple, so every r4
# campaign resumes and audits byte-for-byte as before. The matched A/B/C study
# needs a plain-policy condition whose generations carry no world program at all;
# naming the schema in the immutable settings is what makes that a frozen
# property of the cell rather than a runtime argument.
SOURCE_SCHEMAS = {
    "world_policy_inventory_v1": {"world": "world_program.py", "policy": "policy.py",
                                  "inventory": "inventory.json"},
    "policy_only_v1": {"policy": "policy.py"},
}
DEFAULT_SOURCE_SCHEMA = "world_policy_inventory_v1"

# The matched study's three conditions. Each entry states the whole bundle a cell
# is frozen with, so a condition name cannot be paired with another condition's
# schema, admission rule or document set. A campaign that names no condition is
# the historical case and keeps legacy world behaviour untouched.
#   A  original shared strategy documents, no world mechanism
#   B  the same documents, world mechanism present
#   C  no strategy documents, world mechanism present (the existing r4 shape)
CONDITIONS = {
    "A": {"world_model": False, "shared_skill_md": True,
          "source_schema": "policy_only_v1", "mechanism_admission": NO_MECHANISM_ADMISSION},
    "B": {"world_model": True, "shared_skill_md": True,
          "source_schema": DEFAULT_SOURCE_SCHEMA, "mechanism_admission": MECHANISM_ADMISSION},
    "C": {"world_model": True, "shared_skill_md": False,
          "source_schema": DEFAULT_SOURCE_SCHEMA, "mechanism_admission": MECHANISM_ADMISSION},
}
MAX_REPEAT = 9


class ProtocolError(ValueError):
    pass


def source_schema(settings):
    """Return {source key: filename} for this identity's frozen schema.

    An unknown name is an error, never a silent fallback to the default: a
    settings edit cannot quietly turn a plain-policy cell into a world cell.
    """
    name = settings.get("source_schema", DEFAULT_SOURCE_SCHEMA)
    if name not in SOURCE_SCHEMAS:
        raise ProtocolError("unknown source schema: " + str(name))
    return dict(SOURCE_SCHEMAS[name])


def condition(settings):
    """Return the frozen condition bundle, or None for a historical identity.

    An identity that names a condition must agree with that condition on every
    field of the bundle, so a settings edit cannot pair condition "A" with the
    world schema, nor condition "B" with the no-world admission rule. Absence is
    the legacy case: every r4 campaign predates the key and keeps world behaviour.
    """
    if "condition" not in settings:
        return None
    name = settings["condition"]
    bundle = CONDITIONS.get(name) if isinstance(name, str) else None
    if bundle is None:
        raise ProtocolError("unknown study condition: " + str(name))
    if settings.get("source_schema", DEFAULT_SOURCE_SCHEMA) != bundle["source_schema"]:
        raise ProtocolError("condition " + name + " requires source schema "
                            + bundle["source_schema"])
    if settings.get("mechanism_admission") != bundle["mechanism_admission"]:
        raise ProtocolError("condition " + name + " requires its own admission rule")
    repeat = settings.get("repeat")
    if type(repeat) is not int or isinstance(repeat, bool) or not 1 <= repeat <= MAX_REPEAT:
        raise ProtocolError("condition requires an integer repeat id in 1.." + str(MAX_REPEAT))
    if settings.get("cell") != name + str(repeat):
        raise ProtocolError("cell label must be the condition and repeat id")
    return {**bundle, "condition": name, "repeat": repeat, "cell": settings["cell"]}


def mechanism_admission(settings):
    """Return the validated admission rule, or None for a historical identity.

    A malformed or unrecognised value is an error, never a silent disable: a
    settings edit cannot turn the gate off by making it unreadable.
    """
    if "mechanism_admission" not in settings:
        return None
    rule = settings["mechanism_admission"]
    if not isinstance(rule, dict):
        raise ProtocolError("invalid mechanism admission rule")
    # Keyed by (rule, version): a rule must match a known entry exactly, so
    # neither a renamed weaker rule nor an edited field can be admitted.
    known = MECHANISM_ADMISSION_RULES.get((rule.get("rule"), rule.get("version")))
    if known is None or rule != known:
        raise ProtocolError("unknown or altered mechanism admission rule")
    return rule


def mechanism_eligibility(rule, result):
    """Judge one candidate's recorded trial evidence against the rule.

    Requires only that both legs of the mechanism were really exercised: a
    reference measurement that succeeded, and at least one relation query
    actually attempted. A relation attempt whose comparison was UNKNOWN counts;
    task success, SUPPORT and a decisive verdict are explicitly not required.
    """
    evidence = {name: result.get(name) for name in rule["evidence_fields"]}
    reasons = []
    if any(value is None for value in evidence.values()):
        reasons.append("mechanism_evidence_absent")
    else:
        if rule["require_reference_established"] and evidence["world_reference_established"] is not True:
            reasons.append("no_measured_reference")
        if (rule["require_relation_query_attempt"]
                and not (type(evidence["world_relation_measurement_attempts"]) is int
                         and evidence["world_relation_measurement_attempts"] > 0)):
            reasons.append("no_relation_query_attempt")
    return {"eligible": not reasons, "reasons": reasons, "evidence": evidence,
            "relation_verdicts": result.get("world_relation_verdicts"),
            "status": result.get("status")}


def sha(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _digest(str(path), (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))


@lru_cache(maxsize=8192)
def _digest(path, signature):
    # Rehash whenever file identity/content metadata changes. Repeated phase
    # admission otherwise rereads gigabytes of immutable image/response data.
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def put(path, value):
    """Create once; never replace an earlier attempt or receipt."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else canonical(value) + b"\n"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def read(path):
    return json.loads(Path(path).read_text())


class Ledger:
    def __init__(self, root, settings=None):
        self.root = Path(root).resolve()
        identity = self.root / "identity.json"
        if settings is not None:
            # Validate before any identity is written, so a malformed rule cannot
            # be sealed into a new campaign.
            mechanism_admission(settings)
            source_schema(settings)
            condition(settings)
            expected = {"protocol": PROTOCOL, "settings": settings}
            if identity.exists():
                if read(identity) != expected:
                    raise ProtocolError("resume identity differs")
            else:
                put(identity, expected)
                put(self.root / "identity.sha256", (sha(identity) + "\n").encode())
        if not identity.exists() or sha(identity) != (self.root / "identity.sha256").read_text().strip():
            raise ProtocolError("missing or changed campaign identity")
        self.identity = read(identity)
        if self.identity["protocol"] != PROTOCOL:
            raise ProtocolError("protocol differs from this runner")
        # Reject a malformed or unrecognised admission rule at open time, so it
        # can never be silently ignored later. Absence is the historical case.
        mechanism_admission(self.identity["settings"])
        # Same for the source schema: resolved once, from the sealed identity.
        self.source_files = source_schema(self.identity["settings"])
        # The condition bundle is likewise resolved once and cannot be a runtime
        # switch: an inconsistent condition/schema/admission triple never opens.
        self.condition = condition(self.identity["settings"])
        self.identity_sha = sha(identity)

    @contextmanager
    def lock(self):
        with (self.root / ".coordinator.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def refs(self, paths):
        result = {}
        for path in paths:
            path = Path(path).resolve()
            if not path.is_relative_to(self.root):
                raise ProtocolError("evidence outside campaign")
            result[str(path.relative_to(self.root))] = sha(path)
        return result

    def verify_refs(self, refs):
        if not isinstance(refs, dict) or not refs:
            raise ProtocolError("missing evidence hashes")
        for name, digest in refs.items():
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root) or not path.is_file() or sha(path) != digest:
                raise ProtocolError("changed or missing evidence: " + name)

    def runtime_amendment(self):
        """A sealed engineering correction preserves the original run identity.

        The deployment record is created explicitly while paused, before
        selection. It pins a separate runtime and the already consumed ledger;
        it cannot replace old attempts or increase the protocol's budgets.
        """
        path = self.root / "control/runtime-amendment.json"
        seal = path.with_suffix(".sha256")
        if not path.exists():
            if seal.exists():
                raise ProtocolError("missing runtime amendment")
            return None
        if not seal.exists() or sha(path) != seal.read_text().strip():
            raise ProtocolError("runtime amendment seal differs")
        amendment = read(path)
        if amendment.get("schema") != 1 or amendment.get("identity_sha256") != self.identity_sha:
            raise ProtocolError("runtime amendment identity differs")
        self.verify_refs(amendment["prior_evidence"])
        return amendment

    def runtime_provenance(self):
        if self.runtime_amendment() is None:
            return {}
        return {"runtime_amendment_sha256": sha(self.root / "control/runtime-amendment.json")}

    def verify_runtime_record(self, record):
        if ("runtime_amendment_sha256" in record and
                record["runtime_amendment_sha256"] != self.runtime_provenance().get("runtime_amendment_sha256")):
            raise ProtocolError("recorded runtime amendment differs")

    def verify_runtime(self):
        settings = self.identity["settings"]
        base = Path(settings["runtime_root"]).resolve()
        runtimes = [(base, settings["runtime_sha256"])]
        amendment = self.runtime_amendment()
        if amendment is not None:
            corrected = Path(amendment["runtime_root"]).resolve()
            changes = amendment["changed_sha256"]
            if corrected == base or not changes or not changes.keys() <= settings["runtime_sha256"].keys():
                raise ProtocolError("invalid runtime correction")
            runtimes.append((corrected, {**settings["runtime_sha256"], **changes}))
        for root, hashes in runtimes:
            for name, digest in hashes.items():
                path = (root / name).resolve()
                if not path.is_relative_to(root) or not path.is_file() or sha(path) != digest:
                    raise ProtocolError("runtime changed: " + str(path))

    def slot(self, index):
        if type(index) is not int or not 0 <= index <= 15:
            raise ProtocolError("generation index outside0..15")
        return self.root / "generations" / f"v{index:03d}"

    def generation(self, index):
        slot = self.slot(index)
        reservation = read(slot / "reserved.json")
        if reservation["identity_sha256"] != self.identity_sha or reservation["index"] != index:
            raise ProtocolError("generation reservation differs")
        result = read(slot / "result.json")
        self.verify_runtime_record(reservation)
        self.verify_runtime_record(result)
        if result["reservation_sha256"] != sha(slot / "reserved.json"):
            raise ProtocolError("generation reservation changed")
        self.verify_refs(result["evidence"])
        return result

    def sources(self, version):
        result = self.generation(version)
        if result["status"] != "valid":
            raise ProtocolError("version has no valid sources")
        return {name: self.slot(version) / filename
                for name, filename in self.source_files.items()}

    def _closed_slot(self, index):
        result = self.generation(index)
        if result["status"] == "infrastructure_error":
            raise ProtocolError("development infrastructure failure remains unresolved")
        if result["status"] == "valid":
            result = self.trial("initial" if index == 0 else "repair", index)
            if result["status"] == "infrastructure_error":
                raise ProtocolError("development trial infrastructure failure")

    def begin_generation(self, index, body):
        slot = self.slot(index)
        if (self.root / "selection.json").exists():
            raise ProtocolError("generation after selection forbidden")
        for previous in range(index):
            self._closed_slot(previous)
        if body.get("model") != MODEL or body.get("max_tokens") != PROTOCOL["max_tokens"]:
            raise ProtocolError("wrong experimental model or token budget")
        if slot.exists():
            raise ProtocolError("generation already reserved")
        slot.mkdir(parents=True)
        put(slot / "request.json", body)
        put(slot / "reserved.json", {"index": index, "identity_sha256": self.identity_sha,
            **self.runtime_provenance(),
            "request_sha256": sha(slot / "request.json"), "started_unix": time.time()})

    def begin_request(self, index):
        slot = self.slot(index)
        if (slot / "result.json").exists() or (self.root / "selection.json").exists():
            raise ProtocolError("request after generation/selection finished")
        reservation = read(slot / "reserved.json")
        self.verify_runtime_record(reservation)
        if reservation["request_sha256"] != sha(slot / "request.json"):
            raise ProtocolError("model request changed")
        attempts = sorted(slot.glob("request-*.reserved.json"))
        attempt = len(attempts)
        if attempt >= PROTOCOL["transport_attempts_per_slot"]:
            raise ProtocolError("transport request budget exhausted")
        put(slot / f"request-{attempt}.reserved.json", {
            "attempt": attempt, "started_unix": time.time(),
            **self.runtime_provenance(),
            "request_sha256": reservation["request_sha256"], "model": MODEL})
        return attempt

    def finish_generation(self, index, status, *, response_path=None, sources=None, error=None):
        slot = self.slot(index)
        if status not in {"valid", "content_error", "infrastructure_error"}:
            raise ProtocolError("unknown generation status")
        evidence = [slot / "request.json"] + list(slot.glob("request-*.json"))
        if response_path is not None:
            response_path = Path(response_path)
            response = read(response_path)
            if response.get("model") != MODEL or response.get("stop_reason") != "end_turn":
                raise ProtocolError("incomplete/wrong-model response")
            evidence.append(response_path)
        if status == "valid":
            if response_path is None or set(sources or {}) != set(self.source_files):
                raise ProtocolError("valid generation requires paired sources and response")
            for name, filename in self.source_files.items():
                value = sources[name].encode() if name != "inventory" else sources[name]
                put(slot / filename, value)
                evidence.append(slot / filename)
        put(slot / "result.json", {"status": status, "index": index,
            **self.runtime_provenance(),
            "reservation_sha256": sha(slot / "reserved.json"), "error": error,
            "evidence": self.refs(evidence), "finished_unix": time.time()})

    def trial_dir(self, phase, index):
        if phase in {"initial", "repair"}:
            if (phase == "initial") != (index == 0):
                raise ProtocolError("wrong development phase/index")
            return self.slot(index) / "trial"
        if phase != "heldout" or type(index) is not int or not 1 <= index <= 50:
            raise ProtocolError("wrong held-out index")
        return self.root / "heldout" / f"seed_{index:03d}"

    def begin_trial(self, phase, index):
        directory = self.trial_dir(phase, index)
        if phase == "heldout":
            selection = self.selection()
            version, seed = selection["version"], index
            for previous in range(1, index):
                if self.trial("heldout", previous, _selection=selection)["status"] == "infrastructure_error":
                    raise ProtocolError("earlier held-out infrastructure error")
        else:
            if (self.root / "selection.json").exists():
                raise ProtocolError("development after selection forbidden")
            for previous in range(index):
                self._closed_slot(previous)
            version, seed = index, 51 if index == 0 else 50 + index
        sources = self.sources(version)
        if directory.exists():
            raise ProtocolError("trial already reserved; reconcile, never replay silently")
        directory.mkdir(parents=True)
        record = {"phase": phase, "index": index, "version": version, "seed": seed,
            **self.runtime_provenance(),
            "identity_sha256": self.identity_sha, "source_evidence": self.refs(sources.values()),
            "started_unix": time.time()}
        put(directory / "reserved.json", record)
        return record

    def admission(self, phase, index, *, _selection=None):
        directory = self.trial_dir(phase, index)
        reservation = read(directory / "reserved.json")
        self.verify_runtime_record(reservation)
        if reservation["identity_sha256"] != self.identity_sha:
            raise ProtocolError("trial identity mismatch")
        seed = index if phase == "heldout" else 51 if index == 0 else 50 + index
        version = (_selection if _selection is not None else self.selection())["version"] if phase == "heldout" else index
        if (reservation["phase"], reservation["index"], reservation["seed"], reservation["version"]) != (phase, index, seed, version):
            raise ProtocolError("trial partition or selected version mismatch")
        self.verify_refs(reservation["source_evidence"])
        if reservation["source_evidence"] != self.refs(self.sources(version).values()):
            raise ProtocolError("trial source pair differs")
        return reservation

    def finish_trial(self, phase, index, outcome, evidence_paths):
        directory = self.trial_dir(phase, index)
        self.admission(phase, index)
        if outcome.get("status") not in TERMINALS:
            raise ProtocolError("unknown trial terminal")
        put(directory / "result.json", {**outcome,
            **self.runtime_provenance(),
            "reservation_sha256": sha(directory / "reserved.json"),
            "evidence": self.refs(evidence_paths), "finished_unix": time.time()})

    def trial(self, phase, index, *, _selection=None):
        directory = self.trial_dir(phase, index)
        self.admission(phase, index, _selection=_selection)
        result = read(directory / "result.json")
        self.verify_runtime_record(result)
        if result["status"] not in TERMINALS or result["reservation_sha256"] != sha(directory / "reserved.json"):
            raise ProtocolError("trial result/reservation differs")
        self.verify_refs(result["evidence"])
        return result

    def freeze(self):
        candidates, evidence = [], []
        for index in range(16):
            self._closed_slot(index)
            evidence += [self.slot(index) / "reserved.json", self.slot(index) / "result.json"]
            if self.generation(index)["status"] == "valid":
                phase = "initial" if index == 0 else "repair"
                result = self.trial(phase, index)
                candidates.append((index, result["status"], result))
                evidence += [self.trial_dir(phase, index) / name for name in ["reserved.json", "result.json"]]
        if not candidates:
            raise ProtocolError("no development-executed model source pair; cannot invent a final program")
        # Mechanism admission runs per candidate, BEFORE the latest-eligible rule.
        # An inert candidate is filtered out even when a valid one exists, so it
        # cannot be selected merely for being the latest noncrashing version.
        rule = mechanism_admission(self.identity["settings"])
        audit = None
        # A rule that states no world mechanism is present has nothing to admit,
        # so it filters nothing. It is still recorded in the selection receipt,
        # so the reader can tell "no mechanism to check" from "check skipped".
        if rule is not None and rule["evidence_fields"]:
            audit = {str(index): mechanism_eligibility(rule, result)
                     for index, _status, result in candidates}
            candidates = [c for c in candidates if audit[str(c[0])]["eligible"]]
            if not candidates:
                raise ProtocolError("no development candidate shows real world-mechanism "
                                    "evidence under mechanism admission rule v"
                                    + str(rule["version"]) + ": " + canonical(audit).decode())
        preferred = [i for i, status, _ in candidates if status in {"success", "task_failure"}]
        version = max(preferred) if preferred else candidates[-1][0]
        evidence += list(self.sources(version).values())
        if self.runtime_amendment() is not None:
            evidence += [self.root / "control/runtime-amendment.json", self.root / "control/runtime-amendment.sha256"]
        selection = {"version": version, "identity_sha256": self.identity_sha,
            **self.runtime_provenance(),
            "fallback_to_tested_program_error": not bool(preferred),
            "evidence": self.refs(evidence), "frozen_unix": time.time(),
            "selection_rule": PROTOCOL["selection"],
            "mechanism_admission": rule,
            "mechanism_admission_audit": audit}
        put(self.root / "selection.json", selection)
        put(self.root / "selection.sha256", (sha(self.root / "selection.json") + "\n").encode())
        return selection

    def selection(self):
        path = self.root / "selection.json"
        if not path.exists() or sha(path) != (self.root / "selection.sha256").read_text().strip():
            raise ProtocolError("held-out requires an immutable selection")
        result = read(path)
        if result["identity_sha256"] != self.identity_sha:
            raise ProtocolError("selection identity mismatch")
        if result.get("runtime_amendment_sha256") != self.runtime_provenance().get("runtime_amendment_sha256"):
            raise ProtocolError("runtime amendment changed after selection")
        self.verify_refs(result["evidence"])
        # Recheck raw development evidence, not just the small result receipts.
        for index in range(16):
            if self.generation(index)["status"] == "valid":
                self.trial("initial" if index == 0 else "repair", index)
        return result

    def summary(self):
        generations, development, heldout = [], [], []
        changes = {k: 0 for k in self.source_files if k != "inventory"}
        changes["both_unchanged"] = 0
        previous_sources = None
        for index in range(16):
            slot = self.slot(index)
            if (slot / "result.json").exists():
                generation = self.generation(index)
                generations.append(generation)
                if generation["status"] == "valid":
                    sources = {k: sha(p) for k, p in self.sources(index).items() if k != "inventory"}
                    if previous_sources is not None:
                        for k in sources:
                            changes[k] += sources[k] != previous_sources[k]
                        # "both" is the legacy key name; for a policy-only
                        # schema it means the single source was unchanged.
                        changes["both_unchanged"] += sources == previous_sources
                    previous_sources = sources
            phase = "initial" if index == 0 else "repair"
            if (self.trial_dir(phase, index) / "result.json").exists():
                development.append(self.trial(phase, index))
        selection = self.selection() if (self.root / "selection.json").exists() else None
        for seed in range(1, 51):
            if (self.trial_dir("heldout", seed) / "result.json").exists():
                heldout.append(self.trial("heldout", seed, _selection=selection))
        complete = len(heldout) == 50 and all(r["status"] != "infrastructure_error" for r in heldout)
        return {"status": "complete" if complete else "in_progress",
            "generation_opportunities_reserved": len(list((self.root / "generations").glob("v*/reserved.json"))),
            "generations_terminal": len(generations),
            "repair_opportunities_used": sum((self.slot(i) / "reserved.json").exists() for i in range(1, 16)),
            "model_requests_reserved": len(list((self.root / "generations").glob("v*/request-*.reserved.json"))),
            "revision_source_changes": changes,
            "content_invalid_generations": sum(g["status"] == "content_error" for g in generations),
            "development_executions": len(development), "heldout_executions": len(heldout),
            "development_valid_terminals": sum(r.get("valid") is True for r in development),
            "heldout_valid_terminals": sum(r.get("valid") is True for r in heldout),
            "development_program_errors": sum(r["status"] == "program_error" for r in development),
            "development_successes": sum(r["status"] == "success" for r in development),
            "heldout_successes": sum(r["status"] == "success" for r in heldout),
            "heldout_physical_task_successes": sum(r.get("task_completed") is True for r in heldout),
            "heldout_program_errors": sum(r["status"] == "program_error" for r in heldout)}
