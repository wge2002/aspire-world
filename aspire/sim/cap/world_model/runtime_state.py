"""Fixed, task-neutral observed template for opt-in p2-runtime worlds.

The generated adapter supplies associations and measurements from public APIs.
This template supplies no inventory, geometry, predicate, dynamics or tolerance.
Provenance is evidence bookkeeping, not a semantic guarantee about perception.
"""
from __future__ import annotations

from .evidence_state import EvidenceState, clone
from .prediction_contract import plain

SCHEMA = "world-state/p2-runtime"


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def key(entity, fact):
    return (text(entity, "entity"), text(fact, "fact"))


class RuntimeState(EvidenceState):
    """One observed store with entity-scoped facts and explicit reference semantics."""

    def __init__(self, evidence_valid, binding, ledger):
        super().__init__(evidence_valid, binding, strict=True)
        self.ledger = ledger
        self.entities = {}

    def entity(self, entity, *, label, evidence_ids, source, uncertainty):
        """Record an authored public association; never select by detector index."""
        text(entity, "entity")
        ids = list(evidence_ids)
        if not self.current(ids):
            raise ValueError("entity association needs current public observation IDs")
        self._metadata(source, uncertainty)
        previous = self.entities.get(entity)
        canonical_label = label
        if previous is not None and previous["label"] != label:
            uncertainty = {"status": "unknown", "reason": "entity label changed; association is unconfirmed"}
            canonical_label = previous["label"]
        record = plain(dict(id=entity, label=text(canonical_label, "label"), candidate_label=text(label, "label"), evidence_ids=ids,
                            source=source, uncertainty=uncertainty))
        self.entities[entity] = record
        self.revision += 1
        return clone(record)

    @staticmethod
    def _metadata(source, uncertainty):
        if not isinstance(source, dict):
            raise ValueError("source requires api and method")
        text(source.get("api"), "source.api")
        text(source.get("method"), "source.method")
        if not isinstance(uncertainty, dict) or uncertainty.get("status") not in {
                "measured", "estimated", "unknown"}:
            raise ValueError("uncertainty.status must be measured, estimated or unknown")
        plain(source)
        plain(uncertainty)

    def set_fact(self, entity, fact, value, *, frame, unit, evidence_ids,
                 source, uncertainty, kind="fact", related_entities=(),
                 valid=True, reason="", through_call=None):
        k = key(entity, fact)
        if entity not in self.entities:
            raise ValueError("declare entity from public evidence before writing its facts")
        text(frame, "frame")
        text(unit, "unit")
        if kind not in {"fact", "geometry", "relation"}:
            raise ValueError("kind must be fact, geometry or relation")
        related = list(related_entities)
        if any(e not in self.entities for e in related):
            raise ValueError("relations require declared entity identities")
        if through_call is not None and (type(through_call) is not int or through_call < 0):
            raise ValueError("through_call must be a nonnegative API call id")
        self._metadata(source, uncertainty)
        associations = [self.entities[e] for e in [entity] + related]
        association_known = all(a["uncertainty"]["status"] != "unknown" and
                                self.current(a["evidence_ids"]) for a in associations)
        # Unknown measurements invalidate the same scoped slot. They do not keep
        # a previous position or a prediction standing in for missing perception.
        record = super().set(k, plain(value), evidence_ids=evidence_ids,
                             valid=valid and uncertainty["status"] != "unknown" and association_known,
                             identity=entity, reason=reason if association_known else
                             "entity association is unknown or stale")
        record.update(entity=entity, fact=fact, frame=frame, unit=unit, kind=kind,
                      related_entities=related, source=plain(source),
                      association_evidence_ids=sorted({i for a in associations for i in a["evidence_ids"]}),
                      uncertainty=plain(uncertainty),
                      validity={"after_motion": "stale", "through_call": through_call})
        self._layers[self.measured_layer][k] = record
        self.ledger.observed(k, record)
        self.ledger.emit("state_write", record=clone(record))
        return clone(record)

    def set(self, *args, **kwargs):
        raise TypeError("p2-runtime uses set_fact(entity, fact, value, frame=..., unit=..., "
                        "evidence_ids=..., source=..., uncertainty=...)")

    def invalidate(self, *args, **kwargs):
        raise TypeError("p2-runtime uses invalidate_fact with scoped identity/reference/provenance")

    def invalidate_fact(self, entity, fact, *, frame, unit, evidence_ids,
                        source, reason, kind="fact", related_entities=()):
        return self.set_fact(entity, fact, None, frame=frame, unit=unit,
                             evidence_ids=evidence_ids, source=source,
                             uncertainty={"status": "unknown"}, valid=False,
                             reason=reason, kind=kind, related_entities=related_entities)

    def get_fact(self, entity, fact, *, frame=None, unit=None, fresh=True):
        k = key(entity, fact)
        record = super().query(k, fresh=fresh, identity=entity)
        record.update(entity=entity, fact=fact)
        reason = None
        if frame is not None and record.get("frame") != frame:
            reason = "frame mismatch"
        elif unit is not None and record.get("unit") != unit:
            reason = "unit mismatch"
        limit = record.get("validity", {}).get("through_call")
        if fresh and limit is not None and self.ledger.current_call > limit:
            reason = "fact validity expired"
        if fresh:
            associations = [self.entities.get(e) for e in [entity] + record.get("related_entities", [])]
            if any(a is None or a["uncertainty"]["status"] == "unknown" or
                   not self.current(a["evidence_ids"]) for a in associations):
                reason = "entity association is unknown or stale"
        if reason:
            record.update(status="unknown", value=None, reason=reason)
        record["freshness"] = {"current": record["status"] == "known" and
                               self.current(record["evidence_ids"]),
                               "at_call": self.ledger.current_call}
        return record

    def observed_only(self, name, *, identity=None):
        record = self.query(name, identity=identity)
        record["binding"] = self.binding
        return record

    def query(self, name, *, entity=None, identity=None, layer="observed", **kwargs):
        # Explicit scoped keys keep the substrate's predicate/observed_only
        # helpers useful without admitting unscoped legacy fact writes.
        if isinstance(name, (tuple, list)) and len(name) == 2 and entity is None:
            entity, name = name
        if entity is None:
            raise TypeError("p2-runtime fact reads require an entity identity")
        if layer not in {"observed", self.measured_layer}:
            raise ValueError("p2-runtime predictions are separate; inspect world_state, not measured reads")
        record = self.get_fact(entity, name, **kwargs)
        if identity is not None and identity != entity:
            record.update(status="unknown", value=None, reason="object identity mismatch")
        return record

    def snapshot(self):
        entities = {}
        for entity, association in self.entities.items():
            current = self.current(association["evidence_ids"])
            entities[entity] = {**clone(association), "freshness": {"current": current},
                                "status": "known" if current and association["uncertainty"]["status"] != "unknown"
                                else "unknown"}
        return {"schema": SCHEMA, "revision": self.revision,
                "binding": self.binding, "entities": entities,
                "layers": {self.measured_layer: [self.get_fact(*k)
                           for k in self._layers[self.measured_layer]],
                           "predicted": clone(self.ledger.pending)}}
