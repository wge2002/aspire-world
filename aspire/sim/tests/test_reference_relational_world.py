"""Always-executed contracts for the CC reference relational world program.

These run the real source at ``cap/world_model/reference_relational_world.py``
through ``FrozenPythonProgram``, i.e. fresh credential-free subprocesses with
``math`` only. Nothing here is mocked or stubbed, and nothing here is a
model-generated artifact: the separately skipped ``GeneratedWorldContracts`` in
``test_relational_scene_diagnostic.py`` remains the only home for a true Opus 4.6
candidate. Synthetic inputs are not robot results.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cap.world_model.program import FrozenPythonProgram
from cap.world_model.relational_scene_broker import compare_grasp_hypotheses

SOURCE = ROOT / "cap" / "world_model" / "reference_relational_world.py"
BUDGET = {"limit": 4, "used": 0, "remaining": 4}


def measured(position, *, bounds=None, object_id="bowl", reason=None):
    return {"object_id": object_id, "status": "ok" if position is not None else "unknown",
            "position": position, "visible_bounds": bounds, "reason": reason}


class ReferenceWorldBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.program = FrozenPythonProgram(SOURCE.read_text(), timeout_s=10.0)

    def setUp(self):
        self.robot = {"position": [.5, .2, .3], "orientation_wxyz": [1, 0, 0, 0], "gripper": 0}
        self.position = [.5, .2, .18]  # realistic, nonzero hand-to-object offset
        self.bounds = [[.46, .16, .14], [.54, .24, .22]]
        self.context = {"entities": [
            {"id": "bowl", "role": "manipulated", "label": "bowl", "shape_prior": "unknown",
             "vendor_note": "unrecognized field must survive",
             "measurement": measured(self.position, bounds=self.bounds)},
            {"id": "plate", "role": "target", "label": "plate", "shape_prior": "unknown",
             "measurement": measured([.1, .1, 0], object_id="plate")},
            {"id": "background", "role": "context", "label": "unknown context",
             "shape_prior": "unknown", "measurement": measured(None, object_id="background")}],
            "robot_state": self.robot, "task": "put bowl on plate", "camera": {},
            "budget": dict(BUDGET)}
        self.state = self.program.call("initialize", self.context)
        self.frame = 0
        self.budget = dict(BUDGET)

    def advance(self, action, robot=None, *, error=None):
        self.frame += 1
        if robot is not None:
            self.robot = copy.deepcopy(robot)
        step = {"index": self.frame, "action": {"api": action, "arguments": {}},
                "robot_state": self.robot, "budget": dict(self.budget)}
        if error is not None:
            step["action_error"] = error
        self.state = self.program.call("advance", self.state, step)
        prediction = self.program.call("predict", self.state, step)
        self.assertEqual(set(prediction["objects"]), {"bowl", "plate", "background"})
        self.assertEqual(prediction["grasp_check"]["object_id"], "bowl")
        self.assertEqual(set(prediction["grasp_check"]),
                         {"object_id", "reference_frame", "attached_position", "free_position"})
        return prediction

    def assimilate(self, prediction, position, purpose, bounds=None, *,
                   object_id="bowl", robot=None, frame=None):
        frame = self.frame if frame is None else frame
        observation = measured(position, bounds=bounds, object_id=object_id)
        comparison = compare_grasp_hypotheses(
            prediction["grasp_check"], observation, frame, .03, purpose)
        evidence = {**observation, "purpose": purpose, "frame": frame,
                    "robot_state": self.robot if robot is None else robot,
                    "comparison": comparison}
        self.state = self.program.call("assimilate", self.state, evidence)
        return comparison

    def reference(self, position=None):
        prediction = self.advance("close_gripper")
        self.assertEqual(prediction["query_purpose"], "reference")
        self.assertTrue(prediction["request_query"])
        self.assimilate(prediction, self.position if position is None else position,
                        "reference", self.bounds)
        self.assertEqual(self.state["objects"]["bowl"]["attachment"], "candidate")
        return prediction

    def bowl(self):
        return self.state["objects"]["bowl"]


class ReferenceWorldRelationContracts(ReferenceWorldBase):
    """The behaviours the frozen schema3 program got wrong, now under test."""

    def test_relative_offset_and_release_do_not_teleport_to_hand(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        for value, want in zip(prediction["grasp_check"]["attached_position"], [.5, .2, .23]):
            self.assertAlmostEqual(value, want)
        self.assertEqual(self.assimilate(prediction, [.5, .2, .23], "relation")["status"], "SUPPORT")
        self.assertEqual(self.bowl()["attachment"], "attached")
        self.advance("open_gripper")
        released = self.bowl()["position"]
        self.assertAlmostEqual(released[2], .23)
        self.assertEqual(self.bowl()["attachment"], "released")
        robot["position"][2] += .1
        prediction = self.advance("goto_pose", robot)
        self.assertEqual(self.bowl()["position"], released)
        self.assertEqual(prediction["objects"]["bowl"]["position"], released)
        self.assertFalse(prediction["request_query"])

    def test_rotation_uses_wxyz_and_measured_pose(self):
        self.position = [.62, .2, .3]
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["orientation_wxyz"] = [math.sqrt(2) / 2, 0, 0, math.sqrt(2) / 2]
        prediction = self.advance("goto_pose", robot)
        for value, want in zip(prediction["grasp_check"]["attached_position"], [.5, .32, .3]):
            self.assertAlmostEqual(value, want)
        self.assertEqual(self.assimilate(prediction, [.5, .32, .3], "relation")["status"], "SUPPORT")

    def test_commanded_target_is_never_used_as_measured_motion(self):
        self.reference()
        commanded = copy.deepcopy(self.robot)
        commanded["position"][2] += .5
        self.frame += 1
        step = {"index": self.frame, "budget": dict(BUDGET),
                "action": {"api": "goto_pose", "arguments": {"position": commanded["position"]}},
                "robot_state": self.robot}  # proprioception did not move
        self.state = self.program.call("advance", self.state, step)
        prediction = self.program.call("predict", self.state, step)
        for value, want in zip(prediction["grasp_check"]["attached_position"], self.position):
            self.assertAlmostEqual(value, want)

    def test_stationary_object_refutes_attachment_then_reclose_rebinds(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assertEqual(self.assimilate(prediction, self.position, "relation")["status"], "CONTRADICT")
        self.assertEqual(self.bowl()["attachment"], "free")
        self.assertIsNone(self.state["grasp"]["reference"])
        stale = self.advance("goto_pose", robot)
        self.assertIsNone(stale["grasp_check"]["reference_frame"])
        self.assertFalse(stale["request_query"])
        self.advance("open_gripper")
        self.reference([.7, .4, .2])
        robot = copy.deepcopy(self.robot)
        robot["position"][0] += .1
        prediction = self.advance("goto_pose", robot)
        for value, want in zip(prediction["grasp_check"]["attached_position"], [.8, .4, .2]):
            self.assertAlmostEqual(value, want)
        self.assertEqual(prediction["query_purpose"], "relation")

    def test_mismatched_readout_is_unmodeled_and_updates_visible_measurement(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        new_bounds = [[.75, .75, .75], [.85, .85, .85]]
        comparison = self.assimilate(prediction, [.8, .8, .8], "relation", new_bounds)
        self.assertEqual(comparison["reason"], "both_inconsistent")
        self.assertEqual(self.bowl()["attachment"], "unmodeled")
        self.assertEqual(self.bowl()["visible_bounds"], new_bounds)
        self.assertEqual(self.bowl()["position"], [.8, .8, .8])
        after = self.advance("goto_pose", robot)
        self.assertNotEqual(after["objects"]["bowl"]["attachment"], "attached")
        self.assertEqual(after["conditions"][0]["status"], "unknown")

    def test_background_unknown_and_missing_reference_cannot_confirm_grasp(self):
        self.assertFalse(self.advance("open_gripper")["request_query"])
        prediction = self.advance("close_gripper")
        self.assimilate(prediction, None, "reference")
        self.assertNotEqual(self.bowl()["attachment"], "attached")
        self.assertIsNone(self.state["grasp"]["reference"])
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assertIsNone(prediction["grasp_check"]["reference_frame"])
        self.assertFalse(prediction["request_query"])
        self.assertIsNone(prediction["objects"]["background"]["position"])
        self.assertEqual(prediction["objects"]["background"]["attachment"], "unmodeled")


class ReferenceWorldRepresentationContracts(ReferenceWorldBase):
    """Scene preservation, observed-versus-propagated, provenance, unknowns."""

    def test_every_entity_and_unknown_field_is_preserved(self):
        objects = self.state["objects"]
        self.assertEqual(set(objects), {"bowl", "plate", "background"})
        self.assertEqual(objects["bowl"]["metadata"]["vendor_note"],
                         "unrecognized field must survive")
        self.assertEqual(objects["plate"]["position"], [.1, .1, 0])
        self.assertEqual(objects["background"]["position_source"], "unknown")
        for record in objects.values():
            self.assertEqual(record["uncertainty"]["mass"], "unknown_not_estimated")
            self.assertEqual(record["uncertainty"]["contact"],
                             "unknown_no_force_or_tactile_evidence")
            self.assertEqual(record["uncertainty"]["hidden_geometry"],
                             "unknown_visible_surface_only")

    def test_observed_and_propagated_positions_are_distinguished(self):
        self.reference()
        self.assertEqual(self.bowl()["position_source"], "observed")
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assimilate(prediction, [.5, .2, .23], "relation")
        robot["position"][0] += .07
        prediction = self.advance("goto_pose", robot)
        self.assertEqual(self.bowl()["position_source"], "propagated_attached")
        self.assertAlmostEqual(self.bowl()["position"][0], .57)
        self.assertEqual(prediction["objects"]["bowl"]["position_source"], "propagated_attached")

    def test_bounds_are_reported_as_last_observed_with_their_frame(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assimilate(prediction, [.5, .2, .23], "relation")
        robot["position"][0] += .07
        prediction = self.advance("goto_pose", robot)
        reported = prediction["objects"]["bowl"]
        self.assertEqual(reported["bounds_status"],
                         "last_observed_visible_surface_not_current")
        # frame 1 is where the box was actually measured; the relation evidence
        # at frame 2 carried no bounds, so nothing may claim a newer geometry.
        self.assertEqual(reported["bounds_frame"], 1)
        self.assertEqual(reported["visible_bounds_last_observed"], self.bounds)
        self.assertNotIn("visible_bounds", reported)
        hull = reported["derived_bounds"]
        self.assertAlmostEqual(hull[0][0], .53)  # last-observed box carried rigidly
        self.assertAlmostEqual(hull[1][2], .27)

    def test_provenance_and_transport_condition_are_explicit(self):
        prediction = self.reference()
        entry = self.state["evidence_log"][-1]
        self.assertEqual(entry["evidence_id"], 1)
        self.assertEqual(entry["purpose"], "reference")
        self.assertEqual(entry["outcome"], "reference_bound")
        self.assertEqual(entry["representation_version"], "cc-relational-reference-2")
        self.assertEqual(self.state["grasp"]["attached_hypothesis_id"], "H-attached-1")
        self.assertEqual(self.state["grasp"]["free_hypothesis_id"], "H-free-1")
        self.assertEqual(self.state["grasp"]["reference"]["semantics"],
                         "calibration_of_relative_offset_not_evidence_of_grasp")
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assertEqual(prediction["conditions"][0]["status"], "unknown")
        self.assimilate(prediction, [.5, .2, .23], "relation")
        condition = self.state["conditions"][0]
        self.assertEqual(condition["name"], "transport_requires_attached_relation")
        self.assertEqual(condition["object_id"], "bowl")
        self.assertEqual(condition["status"], "supported")
        self.assertEqual(condition["depends_on"]["reference_evidence_id"], 1)
        self.assertEqual(condition["depends_on"]["relation_evidence_id"], 2)
        self.assertEqual(condition["depends_on"]["attached_hypothesis_id"], "H-attached-1")
        self.assertIn("placement_support_at_target", condition["not_established"])
        self.assertIn("placement_support_verification", prediction["deferred"])
        entry = self.state["evidence_log"][-1]
        self.assertEqual(entry["pre_update_attachment"], "candidate")
        self.assertEqual(entry["pre_update_object_position"], self.position)
        for value, want in zip(entry["pre_update_attached_prediction"], [.5, .2, .23]):
            self.assertAlmostEqual(value, want)
        self.assertEqual(entry["pre_update_free_prediction"], self.position)

    def test_rotation_beyond_verified_scope_reopens_the_condition(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assimilate(prediction, [.5, .2, .23], "relation")
        self.assertEqual(self.state["conditions"][0]["status"], "supported")
        robot["orientation_wxyz"] = [math.sqrt(2) / 2, 0, 0, math.sqrt(2) / 2]
        prediction = self.advance("goto_pose", robot)
        self.assertEqual(self.state["grasp"]["scope_status"], "outside_verified_rotation")
        self.assertEqual(prediction["conditions"][0]["status"], "unknown")
        self.assertEqual(prediction["query_purpose"], "relation")
        self.assertEqual(prediction["query_reason"], "recheck_outside_verified_scope")


class ReferenceWorldStaleSupportContracts(ReferenceWorldBase):
    """No stale support: failed actions, missing state, duplicates, scope."""

    def _support(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assimilate(prediction, [.5, .2, .23], "relation")
        return robot

    def test_failed_close_binds_no_reference_and_requests_no_query(self):
        prediction = self.advance("close_gripper", error="grasp failed")
        self.assertFalse(prediction["request_query"])
        self.assertEqual(prediction["query_purpose"], "none")
        self.assertIsNone(prediction["grasp_check"]["reference_frame"])
        self.assertIsNone(self.state["grasp"]["reference"])
        self.assertEqual(self.state["conditions"][0]["status"], "unknown")

    def test_failed_motion_invalidates_support_not_only_its_reason(self):
        """Review item 1: a failed motion must withdraw the supported transport
        condition, not merely relabel it."""
        robot = self._support()
        self.assertEqual(self.state["conditions"][0]["status"], "supported")
        robot["position"][0] += .09
        prediction = self.advance("goto_pose", robot, error="ik failure")
        self.assertFalse(prediction["request_query"])
        self.assertEqual(prediction["query_reason"], "action_failed_no_valid_evidence")
        self.assertEqual(self.state["grasp"]["scope_status"], "invalidated_failed_motion")
        self.assertEqual(prediction["conditions"][0]["status"], "unknown")
        self.assertEqual(self.state["conditions"][0]["status"], "unknown")
        self.assertNotEqual(self.bowl()["attachment"], "attached")
        self.assertNotEqual(prediction["objects"]["bowl"]["position_source"],
                            "propagated_attached")
        # A later successful motion cannot silently revive support on its own.
        robot["position"][0] += .04
        later = self.advance("goto_pose", robot)
        self.assertEqual(later["conditions"][0]["status"], "unknown")
        self.assertEqual(later["query_reason"], "recheck_after_scope_invalidated")
        self.assertNotEqual(later["objects"]["bowl"]["position_source"], "propagated_attached")

    def test_missing_proprioception_abstains_instead_of_reusing_the_last_pose(self):
        """Review item 2: an unavailable current pose is not a fresh measurement."""
        self._support()
        self.frame += 1
        step = {"index": self.frame, "action": {"api": "goto_pose", "arguments": {}},
                "robot_state": None, "budget": dict(BUDGET)}
        self.state = self.program.call("advance", self.state, step)
        prediction = self.program.call("predict", self.state, step)
        self.assertEqual(self.state["proprioception"],
                         "unavailable_last_pose_is_stale_not_current")
        self.assertIsNone(self.state["pose_frame"])
        self.assertEqual(self.state["grasp"]["scope_status"],
                         "invalidated_missing_proprioception")
        self.assertEqual(prediction["conditions"][0]["status"], "unknown")
        self.assertIsNone(prediction["grasp_check"]["attached_position"])
        self.assertIsNone(prediction["grasp_check"]["reference_frame"])
        self.assertEqual(prediction["hypotheses"]["basis"],
                         "current_pose_unavailable_no_hypothesis_evaluated")
        self.assertNotEqual(prediction["objects"]["bowl"]["position_source"],
                            "propagated_attached")
        self.assertFalse(prediction["request_query"])
        # The last measurement itself survives, clearly labelled as a measurement.
        self.assertEqual(self.bowl()["last_observation"]["position"], [.5, .2, .23])
        self.assertEqual(self.bowl()["last_observation"]["frame"], 2)
        # Restoring proprioception alone must not revive support either.
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        revived = self.advance("goto_pose", robot)
        self.assertEqual(revived["conditions"][0]["status"], "unknown")
        self.assertEqual(revived["query_reason"], "recheck_after_scope_invalidated")
        self.assertNotEqual(revived["objects"]["bowl"]["position_source"], "propagated_attached")
        # Only new valid relation evidence restores it.
        self.assimilate(revived, [.5, .2, .28], "relation")
        self.assertEqual(self.state["conditions"][0]["status"], "supported")

    def test_exhausted_budget_stops_requesting_queries(self):
        self.budget = {"limit": 4, "used": 4, "remaining": 0}
        prediction = self.advance("close_gripper")
        self.assertFalse(prediction["request_query"])
        self.assertEqual(prediction["query_reason"], "query_budget_exhausted")

    def test_repeated_evidence_is_not_independent_support(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assimilate(prediction, self.position, "relation")
        self.assertEqual(self.bowl()["attachment"], "free")
        self.assimilate(prediction, [.5, .2, .23], "relation")
        entry = self.state["evidence_log"][-1]
        self.assertEqual(entry["duplicate_of"], 2)
        self.assertEqual(entry["outcome"], "duplicate_evidence_no_relation_change")
        self.assertEqual(self.bowl()["attachment"], "free")

    def test_out_of_scope_evidence_cannot_touch_the_relation(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        check = dict(prediction["grasp_check"], object_id="plate")
        observation = measured([.5, .2, .23], object_id="plate")
        comparison = compare_grasp_hypotheses(check, observation, self.frame, .03, "relation")
        self.assertEqual(comparison["status"], "SUPPORT")
        evidence = {**observation, "purpose": "relation", "frame": self.frame,
                    "robot_state": self.robot, "comparison": comparison}
        self.state = self.program.call("assimilate", self.state, evidence)
        self.assertEqual(self.bowl()["attachment"], "candidate")
        self.assertEqual(self.state["grasp"]["relation_status"], "unverified")
        self.assertEqual(self.state["objects"]["plate"]["position"], [.5, .2, .23])
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "background_observation_recorded")

    def test_relation_evidence_before_the_reference_frame_is_rejected(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assimilate(prediction, [.5, .2, .23], "relation", frame=0)
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "relation_rejected_out_of_scope")
        self.assertEqual(self.bowl()["attachment"], "candidate")

    def test_delayed_relation_evidence_cannot_support_a_later_failed_frame(self):
        """Final review item: this is a synchronous protocol. A SUPPORT verdict
        formed at frame 2 and assimilated only after a frame-3 failed motion is a
        late answer to a superseded query. It is logged, but it must not make the
        transport condition applicable at frame 3, which the failure invalidated."""
        self.reference()
        lifted = copy.deepcopy(self.robot)
        lifted["position"][2] += .05
        prediction = self.advance("goto_pose", lifted)
        lift_frame = self.frame
        # A genuine, independently reproducible SUPPORT observation at frame 2 ...
        observation = measured([.5, .2, .23])
        comparison = compare_grasp_hypotheses(
            prediction["grasp_check"], observation, lift_frame, .03, "relation")
        self.assertEqual(comparison["status"], "SUPPORT")
        # ... but it is withheld while a later action fails.
        failed = self.advance("goto_pose", lifted, error="motion_failed")
        self.assertEqual(failed["conditions"][0]["status"], "unknown")
        evidence = {**observation, "purpose": "relation", "frame": lift_frame,
                    "robot_state": lifted, "comparison": comparison}
        self.state = self.program.call("assimilate", self.state, evidence)
        entry = self.state["evidence_log"][-1]
        self.assertEqual(entry["outcome"], "relation_rejected_out_of_scope")
        self.assertEqual(entry["frame"], lift_frame)  # still logged, verbatim
        self.assertEqual(self.state["grasp"]["relation_reason"],
                         "relation_evidence_stale_not_from_the_current_frame")
        self.assertNotEqual(self.state["grasp"]["relation_status"], "supported")
        self.assertNotEqual(self.bowl()["attachment"], "attached")
        self.assertEqual(self.state["conditions"][0]["status"], "unknown")
        # Only evidence for the frame the world is actually on can restore it.
        current = self.advance("goto_pose", lifted)
        self.assimilate(current, [.5, .2, .23], "relation")
        self.assertEqual(self.state["conditions"][0]["status"], "supported")

    def test_delayed_relation_evidence_is_rejected_after_a_missing_pose_frame(self):
        """The same rule with the other invalidating step: proprioception is lost
        at the intervening frame instead of the action failing."""
        self.reference()
        lifted = copy.deepcopy(self.robot)
        lifted["position"][2] += .05
        prediction = self.advance("goto_pose", lifted)
        lift_frame = self.frame
        observation = measured([.5, .2, .23])
        comparison = compare_grasp_hypotheses(
            prediction["grasp_check"], observation, lift_frame, .03, "relation")
        self.assertEqual(comparison["status"], "SUPPORT")
        self.frame += 1
        self.state = self.program.call("advance", self.state, {
            "index": self.frame, "action": {"api": "goto_pose", "arguments": {}},
            "robot_state": None, "budget": dict(BUDGET)})
        self.assertEqual(self.state["proprioception"],
                         "unavailable_last_pose_is_stale_not_current")
        evidence = {**observation, "purpose": "relation", "frame": lift_frame,
                    "robot_state": lifted, "comparison": comparison}
        self.state = self.program.call("assimilate", self.state, evidence)
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "relation_rejected_out_of_scope")
        self.assertNotEqual(self.state["grasp"]["relation_status"], "supported")
        self.assertEqual(self.state["conditions"][0]["status"], "unknown")

    def test_delayed_reference_evidence_cannot_calibrate_a_later_frame(self):
        """Reference scope obeys the same synchronous rule: an offset measured at
        the close frame and assimilated only after a later failed motion cannot
        become the calibration current control uses."""
        prediction = self.advance("close_gripper")
        close_frame = self.frame
        self.assertEqual(prediction["query_purpose"], "reference")
        moved = copy.deepcopy(self.robot)
        moved["position"][2] += .05
        self.advance("goto_pose", moved, error="motion_failed")
        self.assimilate(prediction, self.position, "reference", self.bounds,
                        frame=close_frame, robot={"position": [.5, .2, .3],
                                                  "orientation_wxyz": [1, 0, 0, 0],
                                                  "gripper": 0})
        entry = self.state["evidence_log"][-1]
        self.assertEqual(entry["outcome"], "reference_rejected_out_of_scope")
        self.assertEqual(entry["frame"], close_frame)
        self.assertIsNone(self.state["grasp"]["reference"])
        self.assertEqual(self.state["grasp"]["relation_reason"],
                         "reference_evidence_stale_not_from_the_current_frame")
        self.assertEqual(self.state["conditions"][0]["status"], "unknown")
        # A reference query raised for the current frame still binds normally.
        current = self.advance("close_gripper")
        self.assimilate(current, self.position, "reference", self.bounds)
        self.assertIsNotNone(self.state["grasp"]["reference"])
        self.assertEqual(self.state["grasp"]["reference"]["frame"], self.frame)

    def test_reclose_after_release_discards_the_stale_coordinate(self):
        robot = self._support()
        self.advance("open_gripper")
        robot["position"][0] += .2
        self.advance("goto_pose", robot)
        self.assertEqual(self.bowl()["attachment"], "released")
        prediction = self.advance("close_gripper")
        self.assertEqual(prediction["query_purpose"], "reference")
        self.assertIsNone(prediction["grasp_check"]["reference_frame"])
        self.assertEqual(self.state["grasp"]["attempt_id"], 2)
        self.assimilate(prediction, [.7, .4, .2], "reference")
        self.assertEqual(self.state["grasp"]["attached_hypothesis_id"], "H-attached-2")
        self.assertEqual(self.bowl()["position"], [.7, .4, .2])

    def test_support_needs_an_independently_reproducible_verdict(self):
        """Review 'also verify': a comparison field saying SUPPORT is not
        evidence. Calibration and verification stay distinct, and out-of-scope
        evidence must not refresh older support."""
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        # A SUPPORT verdict whose own measurement sits on the stationary
        # hypothesis cannot be accepted just because the field says SUPPORT.
        observation = measured(self.position)
        forged = {"status": "SUPPORT", "reason": "attached_only_consistent",
                  "attached_error_m": 0.0, "free_error_m": .05, "separation_m": .05}
        evidence = {**observation, "purpose": "relation", "frame": self.frame,
                    "robot_state": self.robot, "comparison": forged}
        self.state = self.program.call("assimilate", self.state, evidence)
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "relation_rejected_unverifiable_verdict")
        self.assertNotEqual(self.bowl()["attachment"], "attached")
        self.assertNotEqual(self.state["grasp"]["relation_status"], "supported")
        # A SUPPORT verdict with no available measurement is equally inadmissible.
        robot["position"][2] += .01
        self.advance("goto_pose", robot)
        blank = {**measured(None), "purpose": "relation", "frame": self.frame,
                 "robot_state": self.robot, "comparison": forged}
        self.state = self.program.call("assimilate", self.state, blank)
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "relation_rejected_unverifiable_verdict")
        self.assertNotEqual(self.bowl()["attachment"], "attached")
        # A SUPPORT verdict with no measured pose cannot be evaluated either.
        robot["position"][2] += .01
        self.advance("goto_pose", robot)
        poseless = {**measured([.5, .2, .25]), "purpose": "relation",
                    "frame": self.frame, "robot_state": None, "comparison": forged}
        self.state = self.program.call("assimilate", self.state, poseless)
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "relation_rejected_unverifiable_verdict")
        self.assertNotEqual(self.state["grasp"]["relation_status"], "supported")

    def test_reference_evidence_must_belong_to_the_current_close_attempt(self):
        """Review 'also verify': invalid, stale or future frames and failed or
        open attempts cannot establish a usable reference."""
        prediction = self.advance("close_gripper", error="grasp failed")
        self.assimilate(prediction, self.position, "reference")
        self.assertIsNone(self.state["grasp"]["reference"])
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "reference_rejected_out_of_scope")
        prediction = self.advance("close_gripper")
        attempt_frame = self.frame
        # a frame in the future of the world's own clock
        self.assimilate(prediction, self.position, "reference", frame=attempt_frame + 5)
        self.assertIsNone(self.state["grasp"]["reference"])
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "reference_rejected_out_of_scope")
        # a frame from before this close attempt
        self.assimilate(prediction, self.position, "reference", frame=attempt_frame - 1)
        self.assertIsNone(self.state["grasp"]["reference"])
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "reference_rejected_out_of_scope")
        # the current attempt binds normally, then an open invalidates it
        self.assimilate(prediction, self.position, "reference")
        self.assertIsNotNone(self.state["grasp"]["reference"])
        self.advance("open_gripper")
        self.assimilate(prediction, self.position, "reference")
        self.assertIsNone(self.state["grasp"]["reference"])
        self.assertEqual(self.state["evidence_log"][-1]["outcome"],
                         "reference_rejected_out_of_scope")

    def test_program_rejects_a_foreign_state_and_a_scene_without_manipulated(self):
        with self.assertRaises(Exception):
            self.program.call("advance", {"objects": {}, "grasp": {}},
                              {"index": 1, "action": {"api": "goto_pose"}})
        with self.assertRaises(Exception):
            self.program.call("initialize", {"entities": [
                {"id": "plate", "role": "target", "measurement": measured([.1, .1, 0])}]})


class ReferenceWorldGeometryProvenanceContracts(ReferenceWorldBase):
    """Review items 3 and 4: geometry travels with its own measured pose, and a
    measurement is retained separately from the propagated estimate."""

    def test_new_bounds_are_not_re_offset_by_the_earlier_translation(self):
        self.reference()  # bounds Z [.14, .22] measured at object Z .18
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        lifted = [[.44, .16, .19], [.54, .24, .27]]  # independently observed
        self.assertEqual(self.assimilate(prediction, [.5, .2, .23], "relation",
                                         lifted)["status"], "SUPPORT")
        again = self.program.call("predict", self.state, {
            "index": self.frame, "action": {"api": "goto_pose", "arguments": {}},
            "robot_state": self.robot, "budget": dict(BUDGET)})
        reported = again["objects"]["bowl"]
        self.assertEqual(reported["visible_bounds_last_observed"], lifted)
        self.assertEqual(reported["bounds_frame"], 2)
        # At the very pose the box was measured at, the derived hull is the
        # observed box itself: no second application of the .05 lift.
        for value, want in zip(reported["derived_bounds"][0], lifted[0]):
            self.assertAlmostEqual(value, want)
        for value, want in zip(reported["derived_bounds"][1], lifted[1]):
            self.assertAlmostEqual(value, want)

    def test_bounds_rotate_about_their_own_measured_centre(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        lifted = [[.44, .16, .19], [.54, .24, .27]]
        self.assimilate(prediction, [.5, .2, .23], "relation", lifted)
        robot["orientation_wxyz"] = [math.sqrt(2) / 2, 0, 0, math.sqrt(2) / 2]
        prediction = self.advance("goto_pose", robot)
        # rotating out of the verified scope suspends support, so no hull is
        # published until the relation is re-verified at the new orientation
        self.assertEqual(prediction["conditions"][0]["status"], "unknown")
        self.assertIsNone(prediction["objects"]["bowl"].get("derived_bounds"))
        self.assertEqual(self.assimilate(prediction, [.5, .2, .23], "relation")["status"],
                         "SUPPORT")
        again = self.program.call("predict", self.state, {
            "index": self.frame, "action": {"api": "goto_pose", "arguments": {}},
            "robot_state": self.robot, "budget": dict(BUDGET)})
        hull = again["objects"]["bowl"]["derived_bounds"]
        # 90 deg about Z maps the asymmetric X extent onto Y about centre [.5,.2,.23]
        for value, want in zip(hull[0], [.46, .14, .19]):
            self.assertAlmostEqual(value, want)
        for value, want in zip(hull[1], [.54, .24, .27]):
            self.assertAlmostEqual(value, want)
        self.assertEqual(again["objects"]["bowl"]["bounds_frame"], 2)

    def test_the_last_measurement_survives_propagation_over_it(self):
        self.reference()
        robot = copy.deepcopy(self.robot)
        robot["position"][2] += .05
        prediction = self.advance("goto_pose", robot)
        self.assimilate(prediction, [.5, .2, .23], "relation")
        measurement = copy.deepcopy(self.bowl()["last_observation"])
        self.assertEqual(measurement["position"], [.5, .2, .23])
        self.assertEqual(measurement["frame"], 2)
        self.assertEqual(measurement["evidence_id"], 2)
        self.assertEqual(measurement["evidence_source"], "relation")
        robot["position"][0] += .07
        prediction = self.advance("goto_pose", robot)
        self.assertEqual(self.bowl()["position_source"], "propagated_attached")
        self.assertAlmostEqual(self.bowl()["position"][0], .57)
        # the working estimate moved; the measurement did not
        self.assertEqual(self.bowl()["last_observation"], measurement)
        self.assertEqual(prediction["objects"]["bowl"]["last_observation"], measurement)
        self.assertEqual(prediction["objects"]["bowl"]["last_observation"]["frame"], 2)
        # the prior record is also carried into the next evidence entry
        self.assimilate(prediction, [.57, .2, .23], "relation")
        entry = self.state["evidence_log"][-1]
        self.assertEqual(entry["pre_update_last_observation"], measurement)
        self.assertEqual(entry["pre_update_scope_status"], "within_verified_scope")

    def test_source_measurement_metadata_is_preserved_at_initialize(self):
        entity = self.context["entities"][0]
        entity["measurement"] = dict(entity["measurement"], mask_score=.87,
                                     point_count=1840, eligible_count=1,
                                     geometry_semantics="visible_surface_quantiles")
        state = self.program.call("initialize", self.context)
        record = state["objects"]["bowl"]
        self.assertEqual(record["measurement_metadata"]["mask_score"], .87)
        self.assertEqual(record["measurement_metadata"]["point_count"], 1840)
        self.assertEqual(record["measurement_metadata"]["geometry_semantics"],
                         "visible_surface_quantiles")
        self.assertEqual(record["last_observation"]["position"], self.position)
        self.assertEqual(record["last_observation"]["visible_bounds"], self.bounds)
        self.assertEqual(record["last_observation"]["evidence_source"], "scene_anchor")
        self.assertEqual(record["bounds_origin"]["centre"], self.position)
        self.assertIsNone(state["objects"]["background"]["last_observation"])


if __name__ == "__main__":
    unittest.main()



