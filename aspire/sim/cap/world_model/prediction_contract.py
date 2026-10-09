"""Opt-in prediction contract p1: score a world's predictions against later observations.

Under p1, `predict(call)` returns `{"facts": {name: value}, "tolerance": {name: number}}`
(tolerance optional) or raises Unsupported. The framework writes the predicted
facts into every WorldState's predicted layer, tagged with the call id, and
resolves each one against the first measured write of the same fact whose
evidence id is greater than that call id -- whether `observe` or `update` wrote
it. Each outcome is recorded as a `prediction_check`; the policy can read them
through two reserved queries.

This module scores and records; it never routes recovery, gates a trial, or
grades a prediction. An absent or "off" contract never constructs any of it.
"""
from __future__ import annotations

import json
import math

import numpy as np

from .evidence_state import EvidenceState

REVISION = "p1"
CONTRACTS = (None, "off", REVISION)
RESERVED = frozenset({"prediction_checks", "prediction_summary"})
DEFAULT_TOLERANCE = 1e-6
STATUSES = ("match", "mismatch", "unknown", "unresolved", "unsupported", "malformed")
ROW_KEYS = ("prediction_call", "fact", "predicted", "observed", "evidence_id",
            "tolerance", "residual", "status")


def contract(value):
    """The active contract, or None when off. Unknown values are refused."""
    if value not in CONTRACTS:
        raise ValueError(f"unknown prediction_contract {value!r}; expected one of {CONTRACTS}")
    return REVISION if value == REVISION else None


class Malformed(ValueError):
    pass


def plain(value):
    """numpy and tuples to plain JSON values; non-finite or non-JSON raises Malformed."""
    def convert(v):
        if isinstance(v, np.ndarray):
            return convert(v.tolist())
        if isinstance(v, np.generic):
            return v.item()
        if isinstance(v, (list, tuple)):
            return [convert(x) for x in v]
        if isinstance(v, dict):
            return {k: convert(x) for k, x in v.items()}
        return v
    try:
        return json.loads(json.dumps(convert(value), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise Malformed(f"not a finite JSON value: {exc}") from None


def parse(prediction):
    """(facts, tolerance) from one predict() return, or raise Malformed."""
    if not isinstance(prediction, dict):
        raise Malformed(f"predict() must return a dict with 'facts', got {type(prediction).__name__}")
    if "facts" not in prediction:
        raise Malformed("predict() result has no 'facts' key")
    facts = prediction["facts"]
    if not isinstance(facts, dict):
        raise Malformed("'facts' must be a dict of fact name to value")
    tolerance = prediction.get("tolerance")
    if tolerance is None:
        tolerance = {}
    if not isinstance(tolerance, dict):
        raise Malformed("'tolerance' must be a dict of fact name to number")
    parsed = {}
    for name, value in facts.items():
        if not isinstance(name, str) or not name:
            raise Malformed(f"fact names must be non-empty strings, got {name!r}")
        try:
            parsed[name] = plain(value)
        except Malformed as exc:
            raise Malformed(f"fact {name!r}: {exc}") from None
    bounds = {}
    for name, bound in tolerance.items():
        if isinstance(bound, np.generic):
            bound = bound.item()
        if (isinstance(bound, bool) or not isinstance(bound, (int, float))
                or not math.isfinite(bound) or bound < 0):
            raise Malformed(f"tolerance for {name!r} must be a finite non-negative number")
        bounds[name] = float(bound)
    return parsed, bounds


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_list(value):
    return isinstance(value, list) and all(_number(v) or _numeric_list(v) for v in value)


def compare(predicted, observed, tolerance=None):
    """(status, residual, tolerance used).

    Numeric scalars: |difference| within tolerance (default 1e-6). Numeric lists:
    the largest elementwise |difference|; a shape change is a mismatch. bool,
    str, None and anything else: equality, with bool never equal to a number.
    """
    bound = DEFAULT_TOLERANCE if tolerance is None else tolerance
    if _number(predicted) and _number(observed):
        residual = abs(float(predicted) - float(observed))
        return ("match" if residual <= bound else "mismatch"), residual, bound
    if _numeric_list(predicted) and _numeric_list(observed):
        try:
            a, b = np.asarray(predicted, dtype=float), np.asarray(observed, dtype=float)
        except ValueError:  # ragged nesting is not an array; compare by equality
            a = b = None
        if a is not None:
            if a.shape != b.shape:
                return "mismatch", None, bound
            residual = float(np.max(np.abs(a - b))) if a.size else 0.0
            return ("match" if residual <= bound else "mismatch"), residual, bound
    same = predicted == observed and isinstance(predicted, bool) == isinstance(observed, bool)
    return ("match" if same else "mismatch"), None, None


class TrackedState(EvidenceState):
    """EvidenceState that reports measured writes to the ledger. Same API for authors."""

    def __init__(self, evidence_valid, binding, strict, ledger):
        super().__init__(evidence_valid, binding, strict=strict)
        self._ledger = ledger

    def set(self, name, value, **kwargs):
        fact = super().set(name, value, **kwargs)
        if fact["layer"] == self.measured_layer:
            ids = [i for i in fact["evidence_ids"] if type(i) is int]
            self._ledger.observed(name, fact, max(ids) if ids else None)
        return fact

    def invalidate(self, name, reason, *, identity=None):
        fact = super().invalidate(name, reason, identity=identity)
        # An unknown fact cites no ID; it is a reading of the latest observation.
        self._ledger.observed(name, fact, self._ledger.last_observation())
        return fact

    def commit_prediction(self, name, value, call):
        # The base set: a prediction is not a measurement and must not resolve itself.
        EvidenceState.set(self, name, value, layer="predicted",
                          reason=f"framework prediction from call {call}")
        self._layers["predicted"][name]["prediction_call"] = call


class PredictionLedger:
    """Episode-local record of every prediction and how it resolved."""

    def __init__(self, emit, last_observation):
        self.emit = emit
        self.last_observation = last_observation
        self.states = []
        self.pending = []
        self.checks = []
        self.facts = set()
        self.counts = {"committed": 0, "supported": 0, "unsupported": 0, "malformed": 0,
                       "match": 0, "mismatch": 0, "unknown": 0, "unresolved": 0}
        self.policy_queries = 0
        self.finalized = False

    def state(self, evidence_valid, binding, strict=False):
        state = TrackedState(evidence_valid, binding, strict, self)
        self.states.append(state)
        return state

    def _row(self, call, status, *, fact=None, predicted=None, observed=None,
             evidence_id=None, tolerance=None, residual=None, **extra):
        row = {"prediction_call": call, "fact": fact, "predicted": predicted,
               "observed": observed, "evidence_id": evidence_id, "tolerance": tolerance,
               "residual": residual, "status": status, **extra}
        self.counts[status] += 1
        self.checks.append(row)
        self.emit("prediction_check", **row)
        return row

    def record(self, call, prediction, unsupported=None):
        """One predict() outcome for public API call `call`."""
        if unsupported is not None:
            return self._row(call, "unsupported", reason=unsupported)
        try:
            facts, bounds = parse(prediction)
        except Malformed as exc:
            return self._row(call, "malformed", reason=str(exc))
        self.counts["supported"] += 1
        for name, value in facts.items():
            self.counts["committed"] += 1
            self.facts.add(name)
            self.pending.append({"call": call, "fact": name, "predicted": value,
                                 "tolerance": bounds.get(name)})
            for state in self.states:
                state.commit_prediction(name, value, call)
        return None

    def observed(self, name, fact, evidence_id):
        """A measured write of `name`: resolve every earlier pending prediction of it."""
        if evidence_id is None:
            return
        due = [p for p in self.pending if p["fact"] == name and evidence_id > p["call"]]
        if not due:
            return
        self.pending = [p for p in self.pending if not any(p is d for d in due)]
        known = fact.get("status") == "known" and fact.get("value") is not None
        for p in due:
            if not known:
                self._row(p["call"], "unknown", fact=name, predicted=p["predicted"],
                          evidence_id=evidence_id, tolerance=p["tolerance"],
                          reason=fact.get("reason"))
                continue
            status, residual, bound = compare(p["predicted"], fact["value"], p["tolerance"])
            self._row(p["call"], status, fact=name, predicted=p["predicted"],
                      observed=fact["value"], evidence_id=evidence_id,
                      tolerance=bound, residual=residual)

    def finalize(self):
        """Episode end: whatever no observation reached stays unresolved. Idempotent."""
        if self.finalized:
            return
        self.finalized = True
        pending, self.pending = self.pending, []
        for p in pending:
            self._row(p["call"], "unresolved", fact=p["fact"], predicted=p["predicted"],
                      tolerance=p["tolerance"])

    def summary(self):
        counts = dict(self.counts)
        # Mid-episode, a prediction still awaiting its observation is unresolved.
        counts["unresolved"] += len(self.pending)
        latest = next((dict(r) for r in reversed(self.checks) if r["status"] == "mismatch"), None)
        return {**counts, "latest_mismatch": latest}

    def answer(self, name, kwargs):
        """The two reserved queries. Unknown keyword arguments are an authored error."""
        self.policy_queries += 1
        if name == "prediction_summary":
            if kwargs:
                raise TypeError("prediction_summary takes no arguments")
            return self.summary()
        unknown = set(kwargs) - {"since_call", "fact"}
        if unknown:
            raise TypeError(f"prediction_checks got unexpected arguments {sorted(unknown)}")
        since, fact = kwargs.get("since_call"), kwargs.get("fact")
        if since is not None and (isinstance(since, bool) or not isinstance(since, int)):
            raise TypeError("since_call must be an int API call id or None")
        return [dict(r) for r in self.checks
                if (since is None or r["prediction_call"] >= since)
                and (fact is None or r["fact"] == fact)]

    def manifest(self):
        counts = self.summary()
        counts.pop("latest_mismatch")
        return {"contract": REVISION, "counts": counts, "facts": sorted(self.facts),
                "policy_queries": self.policy_queries}
