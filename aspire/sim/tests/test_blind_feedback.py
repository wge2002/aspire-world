"""The blind-feedback hook changes only what the policy reads, never the audit."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

SIM = Path(__file__).resolve().parents[1]
for p in (SIM.parents[1], SIM / "scripts/libero", SIM / "scripts/common", SIM / "tests"):
    sys.path.insert(0, str(p))
BLIND = SIM / "docs/experiments/code-world-runtime-p2-20261011/blind-feedback"

from aspire.sim.cap.world_model import runtime_predictions as rp  # noqa: E402


def load_hook():
    spec = importlib.util.spec_from_file_location("blind_hook", BLIND / "sitecustomize.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Harness:
    """Same evidence bookkeeping as tests/test_runtime_world.py, without a simulator."""

    def __init__(self):
        self.ids, self.last_motion, self.last_obs, self.events = {0}, -1, 0, []
        self.ledger = rp.RuntimePredictionLedger(
            lambda event, **row: self.events.append({"event": event, **row}), lambda: self.last_obs)

        def valid(ids, fresh=False):
            return bool(ids) and all(i in self.ids for i in ids) and (
                not fresh or any(i > self.last_motion for i in ids))
        self.state = self.ledger.state(valid, "shadow")
        self.state.entity("a", label="a", evidence_ids=[0],
                          source={"api": "synthetic_public", "method": "unique association"},
                          uncertainty={"status": "estimated"})

    def call(self, index, name):
        motion = name in {"goto_pose", "goto_home_joint_position"}
        self.ledger.begin_call({"id": index, "function": name, "args": (), "kwargs": {}}, motion=motion)
        if motion:
            self.last_motion = index
        if name == "get_observation":
            self.ids.add(index)
            self.last_obs = index
            self.ledger.begin_observation(index)

    def predict_then_observe(self, observed):
        self.call(1, "goto_pose")
        self.ledger.record(1, {"predictions": [{
            "entity": "a", "fact": "position", "frame": "world", "unit": "m", "kind": "geometry",
            "expected": [1, 0, 0], "tolerance": 0.01, "target": {"kind": "next_observation"},
            "valid_for_calls": 10}]})
        self.call(2, "get_observation")
        self.state.entity("a", label="a", evidence_ids=[2],
                          source={"api": "synthetic_public", "method": "unique association"},
                          uncertainty={"status": "estimated"})
        return self.state.set_fact("a", "position", observed, frame="world", unit="m", evidence_ids=[2],
                                   source={"api": "synthetic_public", "method": "synthetic measurement"},
                                   uncertainty={"status": "estimated"}, valid=True, kind="geometry")


@pytest.fixture
def clean_ledger():
    original = rp.RuntimePredictionLedger._read
    yield
    rp.RuntimePredictionLedger._read = original
    if hasattr(rp.RuntimePredictionLedger, "_blind_feedback"):
        del rp.RuntimePredictionLedger._blind_feedback


def feedback_and_decision(h):
    fact = h.ledger.answer("state", {"entity": "a", "fact": "position"})
    fb = h.ledger.answer("prediction_feedback", {"entity": "a", "fact": "position"})
    h.ledger.decision("branch", feedback=fb, facts=[fact], reason="test")
    return fb


def test_unblinded_mismatch_reaches_policy(clean_ledger):
    h = Harness()
    h.predict_then_observe([1.5, 0, 0])
    fb = feedback_and_decision(h)
    assert [r["status"] for r in fb["results"]] == ["mismatch"]
    assert "blinded" not in fb
    assert h.ledger.counts["mismatch"] == 1


@pytest.mark.parametrize("mode", ["unknown", "match"])
def test_blinded_feedback_keeps_audit(clean_ledger, mode):
    hook = load_hook()
    hook.install(rp, mode)
    h = Harness()
    h.predict_then_observe([1.5, 0, 0])
    fb = feedback_and_decision(h)              # decision() accepts the blinded read
    assert [r["status"] for r in fb["results"]] == [mode]
    assert fb["blinded"] == {"mode": mode, "rows": 1}
    row = fb["results"][0]
    if mode == "match":
        assert row["observed"] == row["expected"] == [1, 0, 0] and row["residual"] == 0.0
    else:
        assert row["observed"] is None and row["residual"] is None
    # Audit tables keep the real outcome.
    assert h.ledger.counts["mismatch"] == 1 and h.ledger.counts["unknown"] == 0
    checks = h.ledger.answer("prediction_checks", {"entity": "a", "fact": "position"})
    assert [c["status"] for c in checks] == ["mismatch"]
    assert [e for e in h.events if e["event"] == "prediction_check"][0]["status"] == "mismatch"
    # The decision record carries exactly what the policy saw.
    assert h.ledger.decisions[0]["feedback"]["results"][0]["status"] == mode


def test_pending_rows_are_not_blinded(clean_ledger):
    hook = load_hook()
    hook.install(rp, "match")
    h = Harness()
    h.call(1, "goto_pose")
    h.ledger.record(1, {"predictions": [{
        "entity": "a", "fact": "position", "frame": "world", "unit": "m", "kind": "geometry",
        "expected": [1, 0, 0], "tolerance": 0.01, "target": {"kind": "next_observation"},
        "valid_for_calls": 10}]})
    fb = h.ledger.answer("prediction_feedback", {"entity": "a", "fact": "position"})
    assert [r["status"] for r in fb["results"]] == ["pending"]
    assert fb["blinded"]["rows"] == 0


def test_install_refuses_mode_change(clean_ledger):
    hook = load_hook()
    hook.install(rp, "unknown")
    hook.install(rp, "unknown")  # idempotent
    with pytest.raises(RuntimeError):
        hook.install(rp, "match")


def test_hook_loads_from_pythonpath_in_a_fresh_interpreter():
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{BLIND}{os.pathsep}{SIM.parents[1]}"
    probe = ("from aspire.sim.cap.world_model import runtime_predictions as rp; "
             "print(getattr(rp.RuntimePredictionLedger, '_blind_feedback', None))")
    for mode, expected in (("unknown", "unknown"), ("match", "match"), (None, "None")):
        env.pop("ASPIRE_BLIND_FEEDBACK", None)
        if mode:
            env["ASPIRE_BLIND_FEEDBACK"] = mode
        out = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, check=True)
        assert out.stdout.strip() == expected, (mode, out.stdout, out.stderr)
    env["ASPIRE_BLIND_FEEDBACK"] = "bogus"
    out = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True)
    assert out.returncode != 0 and "bogus" in out.stderr
