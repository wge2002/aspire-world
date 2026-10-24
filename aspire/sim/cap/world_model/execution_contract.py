# SPDX-License-Identifier: Apache-2.0
"""Opt-in checked-execution contract ``checked-motion-v1``.

A gripper command whose effect depends on where the arm is (grasp, release)
must not follow a pose action whose requested arrival failed, was modified
(workspace clip or orientation fallback), is unknown, or was interrupted.

Enforcement uses only trusted runtime facts:

* the driver's monotonic ``arm_motion_count`` (every commanded arm motion,
  whatever API path issued it);
* the last motion report produced by the contract's own checked motion;
* a driver gripper gate called before the commanded gripper fraction changes.

The contract never widens workspace bounds, tolerances or accepts a fallback
target as arrival. It establishes no grasp, contact or task success. It holds
no simulator state and is cleared on every episode reset.
"""
from __future__ import annotations

import copy

import numpy as np

CONTRACT_VERSION = "checked-motion-v1"
CONTRACT_STATES = ("initial", "in_flight", "verified", "not_reached", "target_modified",
                   "refused_target_modified", "ik_failed", "unknown", "unchecked_motion")


class ExecutionContractError(RuntimeError):
    """A gripper change was refused before it took effect; replan and re-check."""


def workspace_diagnostics(requested, low, high):
    """Per-axis relation of a requested TCP to the published software clip."""
    requested = np.asarray(requested, dtype=float)
    low, high = np.asarray(low, dtype=float), np.asarray(high, dtype=float)
    clipped = np.clip(requested, low, high)
    axes = [name for name, r, c in zip("xyz", requested, clipped) if r != c]
    return {"requested_inside_workspace": not axes, "violated_axes": axes,
            "clip_delta": (clipped - requested).tolist(),
            "workspace_low": low.tolist(), "workspace_high": high.tolist(),
            "meaning": "software clip in solve_ik, not a measured physical reach limit"}


def validate_contract_config(value):
    """Config value: absent/None (legacy) or {"version": "checked-motion-v1"}."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"version"} or value["version"] != CONTRACT_VERSION:
        raise ValueError(f'execution_contract must be {{"version": "{CONTRACT_VERSION}"}}')
    return dict(value)


class CheckedMotionContract:
    """Arrival evidence for dependent gripper commands within one episode.

    ``motion_count`` returns the driver's monotonic arm-motion count. Any arm
    motion after the evidence was recorded makes it ``unchecked_motion``.
    """

    def __init__(self, motion_count):
        if not callable(motion_count):
            raise TypeError("motion_count must be callable")
        self._motion_count = motion_count
        self.refusals = 0
        self.reset()

    def reset(self):
        self._state = {"state": "initial", "action_id": None,
                       "reason": "no arm motion since the episode started"}
        self._baseline = self._motion_count()

    def begin(self, action_id):
        """Invalidate before acting: an exception leaves the action in flight."""
        self._state = {"state": "in_flight", "action_id": action_id,
                       "reason": "checked motion started and has not completed"}

    def record(self, report):
        if report.get("status") == "refused_target_modified":
            state = "refused_target_modified"
        elif report.get("status") == "ik_failed":
            state = "ik_failed"
        elif report.get("target_modified") is True:
            state = "target_modified"
        elif report.get("reached") is True and report.get("target_modified") is False:
            state = "verified"
        elif report.get("reached") is False:
            state = "not_reached"
        else:
            state = "unknown"
        self._state = {"state": state, "action_id": report.get("action_id"),
                       "reason": report.get("reason") or report.get("status"),
                       "motion_count": self._motion_count()}

    def permission(self):
        state = copy.deepcopy(self._state)
        count = self._motion_count()
        if state["state"] == "initial" and count != self._baseline:
            state.update(state="unchecked_motion",
                         reason="the arm moved without a checked motion report")
        elif state["state"] == "verified" and count != state.get("motion_count"):
            state.update(state="unchecked_motion",
                         reason="the arm moved after the last verified checked motion")
        permitted = state["state"] in ("initial", "verified")
        state.update(version=CONTRACT_VERSION, gripper_permitted=permitted,
                     refusals=self.refusals,
                     required_action=None if permitted else
                     "replan and call goto_pose_report until reached=true and target_modified=false")
        return state

    def check_gripper(self, requested, current):
        """Raise before a gripper change unless current arrival evidence permits it.

        A command equal to the current commanded fraction changes nothing and
        is allowed (e.g. opening an already-open gripper).
        """
        if requested == current:
            return None
        state = self.permission()
        if state["gripper_permitted"]:
            return state
        self.refusals += 1
        state["refusals"] = self.refusals
        raise ExecutionContractError(
            f"{CONTRACT_VERSION}: gripper change refused before it took effect; last pose action "
            f"{state['action_id']!r} is {state['state']} ({state['reason']}). {state['required_action']}")
