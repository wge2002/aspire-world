"""Entity/frame scoped prediction windows; p1 is kept in prediction_contract.py.

Only measures and exposes discrepancies. No action routing or task geometry.
The generated policy owns both its control flow and optional decision records.
"""
from __future__ import annotations

import math

from .prediction_contract import Malformed, compare, plain
from .runtime_state import RuntimeState, key, text

REVISION = "p2-runtime"
RESERVED = frozenset({"state", "world_state", "prediction_feedback",
                      "prediction_checks", "prediction_summary"})
TERMINAL = ("match", "mismatch", "unknown", "superseded", "expired", "unresolved",
            "unsupported", "malformed")


def parse(prediction, call):
    """Validate the entire batch before committing any fact."""
    try:
        data = plain(prediction)
        if not isinstance(data, dict) or not isinstance(data.get("predictions"), list):
            raise ValueError("predict returns {'predictions': [scoped prediction, ...]}")
        if not data["predictions"]:
            raise ValueError("empty predictions are Unsupported, not a supported model")
        result, seen = [], set()
        for item in data["predictions"]:
            if not isinstance(item, dict):
                raise ValueError("each prediction must be a dict")
            k = key(item.get("entity"), item.get("fact"))
            if k in seen:
                raise ValueError("duplicate entity/fact in one prediction batch")
            seen.add(k)
            text(item.get("frame"), "frame")
            text(item.get("unit"), "unit")
            if "expected" not in item or item["expected"] is None:
                raise ValueError("expected must be known; unknown dynamics are Unsupported")
            target = item.get("target")
            if not isinstance(target, dict) or target.get("kind") not in {
                    "next_observation", "same_call", "checkpoint"}:
                raise ValueError("target.kind must be next_observation, same_call or checkpoint")
            if target["kind"] == "same_call" and call["function"] != "get_observation":
                raise ValueError("same_call is only valid for an actual observation API")
            if target["kind"] == "checkpoint":
                text(target.get("name"), "checkpoint name")
            if set(target) != ({"kind", "name"} if target["kind"] == "checkpoint" else {"kind"}):
                raise ValueError("target fields must match the declared target kind")
            lifetime = item.get("valid_for_calls")
            if type(lifetime) is not int or lifetime < 0:
                raise ValueError("valid_for_calls is a declared nonnegative API-call horizon")
            intervening = item.get("intervening_motion", "supersede")
            if intervening not in {"supersede", "retain"}:
                raise ValueError("intervening_motion must be supersede or retain")
            kind = item.get("kind", "fact")
            related = item.get("related_entities", [])
            if kind not in {"fact", "geometry", "relation"} or not isinstance(related, list):
                raise ValueError("kind and related_entities must follow the state template")
            for entity in related:
                text(entity, "related entity")
            if kind == "relation" and not related:
                raise ValueError("relation prediction needs related entity identities")
            tolerance = item.get("tolerance")
            # Use the old finite nonnegative tolerance validation, never invent
            # task thresholds or require predictions for pure math calls.
            if tolerance is not None:
                from .prediction_contract import parse as p1_parse
                p1_parse({"facts": {}, "tolerance": {"bound": tolerance}})
            result.append({**item, "kind": kind, "related_entities": related,
                           "target": target, "intervening_motion": intervening,
                           "tolerance": tolerance, "expires_call": call["id"] + lifetime})
        return result
    except (TypeError, ValueError, KeyError) as exc:
        raise Malformed(str(exc)) from None


class RuntimePredictionLedger:
    def __init__(self, emit, last_observation):
        self.emit, self.last_observation = emit, last_observation
        self.states, self.pending, self.checks = [], [], []
        self.current_call = -1
        self.call = None
        self.counts = {"committed": 0, "supported": 0, **{s: 0 for s in TERMINAL}}
        self.policy_queries = 0
        self.finalized = False
        self.reads = {}
        self.decisions = []
        self.next_decision = None
        self.observation_id = None
        self.feedback_revision = 0

    def state(self, evidence_valid, binding, strict=False):
        # One authoritative observed template per episode, also if the world
        # accidentally asks for WorldState() twice.
        if not self.states:
            self.states.append(RuntimeState(evidence_valid, binding, self))
        return self.states[0]

    def _row(self, prediction, status, *, reason="", observed=None, evidence_id=None,
             residual=None, tolerance=None, observation_revision=None):
        row = {**prediction, "status": status, "reason": reason,
               "observed": plain(observed), "evidence_id": evidence_id,
               "observation_revision": observation_revision,
               "residual": residual,
               "tolerance": prediction.get("tolerance") if tolerance is None else tolerance}
        self.counts[status] += 1
        self.feedback_revision += 1
        self.checks.append(row)
        self.emit("prediction_check", **row)
        return row

    def _settle(self, p, status, **kwargs):
        self.pending.remove(p)
        return self._row(p, status, **kwargs)

    def begin_call(self, call, motion=False):
        self.call = {"id": call["id"], "function": call["function"]}
        self.current_call = call["id"]
        # This happens before predict, so even Unsupported on a subsequent
        # motor command invalidates predictions of the earlier state.
        for p in list(self.pending):
            if call["id"] > p["expires_call"]:
                self._settle(p, "expired", reason="declared API-call horizon elapsed")
            elif motion and p["intervening_motion"] == "supersede":
                self._settle(p, "superseded", reason="intervening motor command changed the state window")
            elif call["function"] == "get_observation" and p.get("target_evidence_id") is not None:
                self._settle(p, "unknown", evidence_id=p["target_evidence_id"],
                             reason="target observation contained no admissible matching fact")
        if self.next_decision is not None:
            from .simple_world import summarize
            self.emit("policy_decision_action", decision_id=self.next_decision,
                      api_call=call["id"], function=call["function"],
                      args=summarize(call["args"]), kwargs=summarize(call["kwargs"]))
            self.next_decision = None

    def record(self, call, prediction, unsupported=None):
        base = {"prediction_call": call, "source_function": self.call["function"],
                "entity": None, "fact": None, "frame": None, "unit": None,
                "expected": None}
        if unsupported is not None:
            return self._row(base, "unsupported", reason=unsupported)
        try:
            items = parse(prediction, self.call)
        except Malformed as exc:
            return self._row(base, "malformed", reason=str(exc))
        self.counts["supported"] += 1
        for item in items:
            k = key(item["entity"], item["fact"])
            for p in list(self.pending):
                if key(p["entity"], p["fact"]) == k:
                    self._settle(p, "superseded", reason="new prediction for the same entity/fact")
            p = {**item, "prediction_call": call,
                 "source_function": self.call["function"], "target_evidence_id": None}
            self.counts["committed"] += 1
            self.pending.append(p)
            self.feedback_revision += 1
            self.emit("prediction_committed", **p)

    def begin_observation(self, evidence_id):
        self.observation_id = evidence_id
        for p in self.pending:
            kind = p["target"]["kind"]
            if ((kind == "same_call" and evidence_id == p["prediction_call"]) or
                    (kind == "next_observation" and evidence_id > p["prediction_call"])):
                if p["target_evidence_id"] is None:
                    p["target_evidence_id"] = evidence_id

    def observed(self, k, fact):
        for p in list(self.pending):
            if key(p["entity"], p["fact"]) != k or p.get("target_evidence_id") is None:
                continue
            self._compare(p, fact)

    def _compare(self, p, fact):
        target = p["target_evidence_id"]
        ids = fact.get("evidence_ids", [])
        # All current evidence must be the target frame. A new ID cannot launder
        # a stale one, and a historical reference isn't a current measurement.
        if not ids or any(i != target for i in ids):
            return  # A stale or unrelated write does not consume the target.
        reason = None
        if fact.get("identity") != p["entity"]:
            reason = "identity mismatch"
        elif fact.get("frame") != p["frame"]:
            reason = "frame mismatch"
        elif fact.get("unit") != p["unit"]:
            reason = "unit mismatch"
        elif fact.get("kind") != p["kind"] or fact.get("related_entities", []) != p["related_entities"]:
            reason = "fact kind or related entity identity mismatch"
        elif fact.get("status") != "known" or fact.get("value") is None:
            reason = fact.get("reason") or "unknown perception"
        elif not self.states[0].current(ids):
            reason = "stale evidence after an intervening motion"
        elif fact.get("validity", {}).get("through_call") is not None and self.current_call > fact["validity"]["through_call"]:
            reason = "observed fact validity expired"
        if reason:
            self._settle(p, "unknown", reason=reason, evidence_id=target,
                         observation_revision=fact.get("revision"))
        else:
            status, residual, tolerance = compare(p["expected"], fact["value"], p["tolerance"])
            if residual is not None and not math.isfinite(residual):
                residual = None  # finite extreme values can overflow their difference
            self._settle(p, status, observed=fact["value"], evidence_id=target,
                         residual=residual, tolerance=tolerance,
                         observation_revision=fact.get("revision"))

    def checkpoint(self, name):
        text(name, "checkpoint")
        for p in list(self.pending):
            if p["target"] != {"kind": "checkpoint", "name": name}:
                continue
            eid = self.last_observation()
            if eid is None or eid <= p["prediction_call"]:
                self._settle(p, "unknown", reason="checkpoint needs a post-source observation")
                continue
            p["target_evidence_id"] = eid
            fact = self.states[0].get_fact(p["entity"], p["fact"])
            self._compare(p, fact)
            if p in self.pending:
                self._settle(p, "unknown", evidence_id=eid,
                             reason="checkpoint has no admissible matching fact")
        self.emit("world_checkpoint", name=name, evidence_id=self.last_observation())

    def flush_targets(self, relevant=None):
        # Perception adapters may finish after observe() (SAM/point cloud +
        # update). Keep the target open until the policy requests feedback.
        for p in list(self.pending):
            if p.get("target_evidence_id") is not None and (relevant is None or relevant(p)):
                self._settle(p, "unknown", evidence_id=p["target_evidence_id"],
                             reason="target observation has no admissible matching fact")

    def finalize(self):
        if self.finalized:
            return
        self.finalized = True
        self.flush_targets()
        for p in list(self.pending):
            self._settle(p, "unresolved", reason="episode ended before the declared target")

    def summary(self):
        return {**self.counts, "unresolved": self.counts["unresolved"] + len(self.pending)}

    def _read(self, kind, answer):
        token = f"{kind}:{len(self.reads)}"
        value = plain({**answer, "read_id": token})
        self.reads[token] = {"kind": kind, "at_call": self.current_call, "value": value,
                             "state_revision": self.states[0].revision,
                             "feedback_revision": self.feedback_revision}
        return value

    def answer(self, name, kwargs):
        self.policy_queries += 1
        if name == "state":
            allowed = {"entity", "fact", "frame", "unit"}
            if set(kwargs) - allowed or not {"entity", "fact"} <= set(kwargs):
                raise TypeError("state requires entity/fact; optional frame/unit")
            return self._read("state", self.states[0].get_fact(**kwargs))
        if name == "world_state":
            if kwargs:
                raise TypeError("world_state takes no arguments")
            return self.states[0].snapshot()
        if name == "prediction_summary":
            if kwargs:
                raise TypeError("prediction_summary takes no arguments")
            return self.summary()
        allowed = {"entity", "fact", "source_call", "checkpoint"}
        if set(kwargs) - allowed:
            raise TypeError(f"{name} accepts entity, fact, source_call, checkpoint")
        for field in ("entity", "fact", "checkpoint"):
            if kwargs.get(field) is not None:
                text(kwargs[field], field)
        if kwargs.get("source_call") is not None and (
                type(kwargs["source_call"]) is not int or kwargs["source_call"] < 0):
            raise TypeError("source_call must be a nonnegative API call id")
        def relevant(p):
            return all((p.get("prediction_call") == v if k == "source_call" else
                        p.get("target", {}).get("name") == v if k == "checkpoint" else
                        p.get(k) == v) for k, v in kwargs.items() if v is not None)
        self.flush_targets(relevant)
        rows = [plain(r) for r in self.checks if relevant(r)]
        if name == "prediction_checks":
            return rows
        # Latest source window per entity/fact; historical outcomes can never
        # reappear as the current mismatch after supersession or expiration.
        candidates = rows + [{**plain(p), "status": "pending"} for p in self.pending if relevant(p)]
        latest = {}
        for row in candidates:
            if row.get("entity") is not None:
                k = key(row["entity"], row["fact"])
                if k not in latest or row["prediction_call"] >= latest[k]["prediction_call"]:
                    latest[k] = row
        current = list(latest.values())
        # A resolved result is historical after a later motion, even if its
        # original prediction had already left pending.
        for row in current:
            if row["status"] in {"match", "mismatch", "unknown"} and (
                    self.current_call > row["expires_call"] or not self.states[0].current(
                    [row["evidence_id"]] if row.get("evidence_id") is not None else [])):
                row.update(status="expired", reason="feedback evidence or declared horizon is stale")
            elif row["status"] in {"match", "mismatch", "unknown"}:
                fact = self.states[0].get_fact(row["entity"], row["fact"])
                if ((row.get("observation_revision") is not None and
                     fact.get("revision") != row["observation_revision"]) or
                        (row["status"] in {"match", "mismatch"} and fact["status"] != "known")):
                    row.update(status="expired", reason="observed interpretation or identity changed after check")
        return self._read("feedback", {"contract": REVISION, "at_call": self.current_call,
                                      "results": current})

    def decision(self, branch, *, feedback, facts, reason):
        """Record policy-authored dataflow; never choose or modify its action.

        This demonstrates declared consumption and the following actual API.
        A declaration alone is not causal proof; controlled fixtures can test
        the policy's branch under different feedback values.
        """
        text(branch, "branch")
        text(reason, "reason")
        if not isinstance(facts, list) or not facts:
            raise ValueError("decision needs state query results as well as feedback")
        for kind, value in [("feedback", feedback)] + [("state", f) for f in facts]:
            read = self.reads.get(value.get("read_id")) if isinstance(value, dict) else None
            if not read or read["kind"] != kind or read["value"] != plain(value):
                raise ValueError("decision must cite exact runtime query answers")
            if read["at_call"] != self.current_call:
                raise ValueError("decision query answers are stale; read current feedback/state")
            if ((kind in {"state", "feedback"} and read["state_revision"] != self.states[0].revision) or
                    (kind == "feedback" and read["feedback_revision"] != self.feedback_revision)):
                raise ValueError("decision query answers changed after an update/checkpoint; query again")
        if self.next_decision is not None:
            raise ValueError("previous decision has no following public call")
        record = {"decision_id": len(self.decisions), "branch": branch,
                  "feedback": plain(feedback), "facts": plain(facts), "reason": reason,
                  "at_call": self.current_call}
        self.decisions.append(record)
        self.next_decision = record["decision_id"]
        self.emit("policy_decision", **record)
        return record["decision_id"]

    def manifest(self):
        return {"contract": REVISION, "counts": self.summary(),
                "policy_queries": self.policy_queries,
                "state_schema": "world-state/p2-runtime", "decisions": len(self.decisions)}
