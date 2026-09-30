"""Task-neutral, episode-local facts for executable worlds.

This stores authored interpretations of public observations, not simulator
truth. The runtime supplies observation identity and motion freshness checks.
Missing values replace old facts; predictions never overwrite observations.
"""
from __future__ import annotations

import copy
import json


def clone(value):
    return json.loads(json.dumps(value, allow_nan=False))


def conjunction(clauses):
    """Three-valued AND: a reliable false clause suffices to disprove a goal."""
    if not clauses:
        return {"verdict": "unknown", "evidence_ids": [], "reason": "no goal clauses", "clauses": []}
    false = [c for c in clauses if c["verdict"] == "false"]
    unknown = [c for c in clauses if c["verdict"] == "unknown"]
    verdict = "false" if false else "unknown" if unknown else "true"
    decisive = false if false else clauses
    return {"verdict": verdict,
            "evidence_ids": sorted({i for c in decisive for i in c.get("evidence_ids", [])}),
            "reference_evidence_ids": sorted({i for c in decisive for i in c.get("reference_evidence_ids", [])}),
            "reason": "; ".join(c.get("name", "clause") + ": " + c.get("reason", c["verdict"]) for c in clauses),
            "clauses": clone(clauses)}


class EvidenceState:
    """One authoritative store shared by update/query/snapshot/done.

    Query returns a copy with status and provenance, never a fallback coordinate.
    A caller must opt into historical reference reads with fresh=False. Goal
    predicates still require a fresh current observation in addition to references.
    """

    def __init__(self, evidence_valid, binding="shadow"):
        self._valid = evidence_valid
        self.binding = binding
        self._layers = {"observed": {}, "predicted": {}, "rehearsal": {}}
        self.revision = 0

    def set(self, name, value, *, evidence_ids=(), valid=True, reason="",
            identity=None, layer="observed", reference_evidence_ids=()):
        if layer not in self._layers:
            raise ValueError("unknown state layer")
        if self.binding == "rehearsal" and layer == "observed":
            layer = "rehearsal"
        ids, refs = list(evidence_ids), list(reference_evidence_ids)
        if layer != "predicted" and not self._valid(ids):
            raise ValueError("facts require actual observation IDs, not command receipts")
        if refs and not self._valid(refs):
            raise ValueError("reference evidence must also be real observations")
        known = bool(valid) and value is not None
        try:
            value = clone(value) if known else None
        except (TypeError, ValueError):
            known, value, reason = False, None, "non-finite or non-JSON measurement"
        self.revision += 1
        fact = {"status": "known" if known else "unknown", "value": value,
                "evidence_ids": ids, "reference_evidence_ids": refs,
                "identity": clone(identity), "layer": layer, "revision": self.revision,
                "reason": reason or ("observed" if known else "missing or unreliable perception")}
        self._layers[layer][name] = fact
        return clone(fact)

    def query(self, name, *, fresh=True, identity=None, layer="observed"):
        if self.binding == "rehearsal" and layer == "observed":
            layer = "rehearsal"
        fact = copy.deepcopy(self._layers[layer].get(name))
        if fact is None:
            return {"status": "unknown", "value": None, "evidence_ids": [],
                    "reference_evidence_ids": [], "identity": identity, "layer": layer,
                    "reason": "not measured"}
        reason = None
        if identity is not None and identity != fact["identity"]:
            reason = "object identity mismatch"
        elif fresh and layer != "predicted" and not self._valid(fact["evidence_ids"], fresh=True):
            reason = "no post-action observation for this fact"
        if reason:
            fact.update(status="unknown", value=None, reason=reason)
        return fact

    def predicate(self, name, test, keys, *, references=(), identity=None):
        """Evaluate an authored predicate only on known, fresh current facts.

        test receives values in keys order followed by historical references.
        Parameters and test semantics remain the generated world's responsibility.
        """
        facts = [self.query(k, identity=identity) for k in keys]
        refs = [self.query(k, fresh=False, identity=identity) for k in references]
        layer = "rehearsal" if self.binding == "rehearsal" else "observed"
        clause = {"name": name, "verdict": "unknown", "reason": "missing current evidence",
                  "evidence_ids": sorted({i for f in facts for i in f["evidence_ids"]}),
                  "reference_evidence_ids": sorted({i for f in refs for i in f["evidence_ids"]}),
                  "layer": layer, "facts": list(keys), "references": list(references)}
        if not facts or any(f["status"] != "known" for f in facts + refs):
            clause["reason"] = "; ".join(f["reason"] for f in facts + refs if f["status"] != "known") or clause["reason"]
            return clause
        result = test(*[f["value"] for f in facts + refs])
        if type(result) is not bool:
            raise TypeError("predicate must return a bool; missing evidence is handled before test")
        clause.update(verdict="true" if result else "false", reason="authored predicate on measured facts")
        return clause

    def snapshot(self):
        # Read through the same accessor, including current freshness, rather
        # than exposing raw storage whose stale status differs from query().
        return {"revision": self.revision, "layers": {
            layer: {name: self.query(name, layer=layer) for name in facts}
            for layer, facts in self._layers.items()}}

    @staticmethod
    def all_of(clauses):
        return conjunction(clauses)
