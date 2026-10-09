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

    def __init__(self, evidence_valid, binding="shadow", strict=False):
        self._valid = evidence_valid
        self.binding = binding
        # strict=True (closed-loop revision only): a current fact needs EVERY
        # evidence ID to postdate the last motion, so one fresh ID cannot
        # launder stale ones. False keeps the original any-fresh semantics.
        self.strict = bool(strict)
        self._layers = {"observed": {}, "predicted": {}, "rehearsal": {}}
        self.revision = 0

    @property
    def measured_layer(self):
        """Where this binding stores measurements: real or rehearsal observations."""
        return "rehearsal" if self.binding == "rehearsal" else "observed"

    def current(self, ids):
        """Every ID is a real observation taken after the most recent motion."""
        ids = list(ids)
        return (self._valid(ids) and bool(ids)
                and all(self._valid([i], fresh=True) for i in ids))

    def _fresh(self, ids):
        return self.current(ids) if self.strict else self._valid(ids, fresh=True)

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
        elif fresh and layer != "predicted" and not self._fresh(fact["evidence_ids"]):
            reason = "no post-action observation for this fact"
        if reason:
            fact.update(status="unknown", value=None, reason=reason)
        return fact

    def invalidate(self, name, reason, *, identity=None):
        """Replace a measured fact with `unknown`, without needing evidence.

        Missing or unusable perception must remove the observed fact rather than
        leave the previous value (or a prediction) standing in for it. An unknown
        fact claims nothing, so it needs no observation ID. Stored in this
        binding's measured layer; the predicted layer is never touched.
        """
        self.revision += 1
        fact = {"status": "unknown", "value": None, "evidence_ids": [],
                "reference_evidence_ids": [], "identity": clone(identity),
                "layer": self.measured_layer, "revision": self.revision, "reason": reason}
        self._layers[self.measured_layer][name] = fact
        return clone(fact)

    def confirm_identity(self, name, candidates, *, match, assumed=None, evidence_ids=()):
        """Adjudicate a target's identity against one current candidate set.

        Task-neutral contract. The caller supplies the candidates, a `match` test
        and, once a target has been acquired, the `assumed` identity to keep. The
        result is a known fact only when ALL of these hold:
          - every evidence ID is a real observation taken after the last motion;
          - exactly one candidate satisfies `match`;
          - that candidate declares a stable identity (see `identity_of`);
          - if `assumed` is given, that identity equals it.
        Otherwise the measured fact `name` is invalidated to `unknown`, its
        identity kept as `assumed`, and the reason says which condition failed.
        It never raises for missing evidence, never selects by position in an
        incomplete detection set, and never silently switches to a different
        target that happens to match uniquely. `assumed=None` is first
        acquisition and establishes the identity.
        """
        ids = list(evidence_ids) if isinstance(evidence_ids, (list, tuple)) else []
        if not self.current(ids):
            return self.invalidate(name, "identity needs a current observation after the last motion",
                                   identity=assumed)
        try:
            matches = [c for c in candidates if match(c)]
        except Exception as exc:
            return self.invalidate(name, f"identity match failed: {type(exc).__name__}: {exc}",
                                   identity=assumed)
        if len(matches) != 1:
            return self.invalidate(name, "no candidate matched the target" if not matches
                                   else f"ambiguous association: {len(matches)} candidates matched",
                                   identity=assumed)
        chosen = matches[0]
        found = self.identity_of(chosen)
        if found is None:
            return self.invalidate(name, "the matched candidate declares no stable identity",
                                   identity=assumed)
        if assumed is not None and found != assumed:
            return self.invalidate(name, f"identity changed: matched {found!r}, target is {assumed!r}",
                                   identity=assumed)
        return self.set(name, chosen, evidence_ids=ids, valid=True,
                        reason="identity confirmed by a unique current public match",
                        identity=found, layer="observed")

    @staticmethod
    def identity_of(candidate):
        """The stable identity a candidate declares, or None.

        Identity is what the public relation calls it: the first present, non-null
        `identity`, `name`, `label` or `id`. There is no fallback to position,
        because position is exactly the association this contract refuses.
        """
        if isinstance(candidate, dict):
            for key in ("identity", "name", "label", "id"):
                if candidate.get(key) is not None:
                    return clone(candidate[key])
        return None

    def observed_only(self, name, *, identity=None):
        """The control accessor: a value only if it is currently MEASURED.

        Always fresh and always strict: every evidence ID must postdate the last
        motion, so one fresh ID cannot launder stale ones. Reads this binding's
        measured layer (real observations, or rehearsal observations under the
        rehearsal binding); never the predicted layer. A prediction may still be
        read directly as a search hint, but it cannot arrive through here, so no
        grasp target, progress or goal claim is built on a guess.
        """
        fact = self.query(name, fresh=False, identity=identity, layer="observed")
        fact["binding"] = self.binding
        if fact["status"] == "known":
            if fact.get("layer") != self.measured_layer:
                fact.update(status="unknown", value=None, reason="not a measurement in this binding")
            elif not self.current(fact["evidence_ids"]) or (
                    fact.get("reference_evidence_ids") and not self._valid(fact["reference_evidence_ids"])):
                fact.update(status="unknown", value=None,
                            reason="every evidence ID must be a current observation")
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
            # numpy.bool_ is a valid predicate scalar; coerce it rather than
            # rejecting.  int, float, str, arrays, and arbitrary truthy objects
            # are authored errors and are refused unconditionally.
            _coerced = False
            try:
                import numpy as _np
                if isinstance(result, _np.bool_):
                    result = bool(result)
                    _coerced = True
            except ImportError:
                pass
            if not _coerced:
                raise TypeError(
                    "predicate must return a bool; missing evidence is handled before test")
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
