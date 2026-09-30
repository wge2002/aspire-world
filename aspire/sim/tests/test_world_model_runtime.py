"""Numerical fixtures test the opt-in monitor, not learned robot performance."""

from __future__ import annotations

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cap.world_model import (
    FrozenPythonProgram,
    ProgramExecutionError,
    ProgramTimeoutError,
    ReplayConfig,
    replay_tape,
)


# Explicitly handwritten fixtures. No method claim is based on these rules.
STATIC = '''
def initialize(context):
    measurement = context["measurement"]
    return {"position": measurement["values"], "updates": 0}

def advance(state, step):
    return state

def predict(state, step):
    return {"position": state["position"], "request_query": True}

def assimilate(state, evidence):
    measurement = evidence["measurement"]
    if "position" in measurement or "source" in measurement:
        raise ValueError("unreleased data in observation")
    if measurement["status"] == "ok" and state["position"] is not None:
        for axis, value in zip(measurement["axes"], measurement["values"]):
            state["position"][axis] = value
    state["updates"] += 1
    return state
'''

MEASURED_MOTION = '''
def initialize(context):
    return {"position": context["measurement"]["values"],
            "robot": context["step"]["robot_state"]["position"], "attached": False}

def advance(state, step):
    if step["action"]["api"] == "close_gripper":
        state["attached"] = True
    if step["action"]["api"] == "open_gripper":
        state["attached"] = False
    measured = step["robot_state"]["position"]
    if state["attached"] and state["position"] is not None:
        state["position"] = [p + r - old for p, r, old in
                             zip(state["position"], measured, state["robot"])]
    state["robot"] = measured
    return state

def predict(state, step):
    return {"position": state["position"], "request_query": False}

def assimilate(state, evidence):
    return state
'''


def frame(index, position=None, *, status="ok", robot=None, api="goto_pose", args=None):
    return {
        "schema_version": 1,
        "frame_id": f"frame-{index}",
        "action": {"api": api, "args": args or {}},
        "robot_state": {
            "position": robot or [0.0, 0.0, 0.0],
            "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
            "gripper": 0.0,
        },
        "measurement": {
            "object_id": "test_object",
            "frame": "world",
            "status": status,
            "position": position if status == "ok" else None,
            "reason": "occluded" if status == "unknown" else None,
            "source": {"camera": "synthetic", "private_path": "/not/given/to/program"},
        },
        "evaluator_only": {"never_release": True},
    }


def selected(result, kind):
    return [event for event in result["events"] if event["event"] == kind]


class WorldRuntimeTests(unittest.TestCase):
    def test_prediction_measurement_update_order_and_jsonl(self):
        tape = [frame(0, [0, 0, 0]), frame(1, [1, 0, 0]), frame(2, [2, 0, 0])]
        original = deepcopy(tape)
        output = io.StringIO()

        def sink(event):
            output.write(json.dumps(event) + "\n")
            event["event"] = "sink_mutation_must_not_affect_history"

        result = replay_tape(tape, FrozenPythonProgram(STATIC), ReplayConfig(2, fixed_indices=(1, 2)), sink)
        self.assertEqual(result["summary"]["status"], "complete")
        events = [event["event"] for event in result["events"]]
        self.assertLess(events.index("prediction_committed"), events.index("measurement_released"))
        self.assertLess(events.index("measurement_released"), events.index("comparison"))
        self.assertLess(events.index("comparison"), events.index("world_assimilated"))
        predictions = selected(result, "prediction_committed")
        # assimilate mutates the next state; it cannot rewrite either prediction.
        self.assertEqual(predictions[0]["prediction"]["position"], [0, 0, 0])
        self.assertEqual(predictions[1]["prediction"]["position"], [1, 0, 0])
        self.assertEqual([event["comparison"]["error_m"] for event in selected(result, "independent_probe")], [1.0, 1.0])
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()], result["events"])
        self.assertEqual(tape, original)

    def test_partial_query_releases_only_selected_values_and_probe_never_flows_back(self):
        program = STATIC.replace('"request_query": True', '"request_query": True, "query_axes": [2]')
        tape = [frame(0, [0, 0, 0]), frame(1, [99, 88, 1]), frame(2, [77, 66, 2])]
        result = replay_tape(tape, FrozenPythonProgram(program), ReplayConfig(1, schedule="adaptive"))
        released = selected(result, "measurement_released")
        self.assertEqual(len(released), 1)
        self.assertEqual(released[0]["measurement"]["axes"], [2])
        self.assertEqual(released[0]["measurement"]["values"], [1])
        predictions = selected(result, "prediction_committed")
        self.assertEqual(predictions[1]["prediction"]["position"], [0, 0, 1])
        self.assertGreater(selected(result, "independent_probe")[0]["comparison"]["error_m"], 100)
        self.assertEqual(result["summary"]["probe_valid_count"], 2)
        self.assertEqual(len(selected(result, "query_denied")), 1)

    def test_unknown_query_consumes_budget_and_anchor_is_separate(self):
        tape = [frame(0, [0, 0, 0]), frame(1, status="unknown"), frame(2, [4, 0, 0])]
        result = replay_tape(tape, FrozenPythonProgram(STATIC), ReplayConfig(1, schedule="adaptive"))
        summary = result["summary"]
        self.assertEqual(summary["anchor_attempts"], 1)
        self.assertEqual(summary["queries_used"], 1)
        self.assertEqual(summary["query_comparisons"]["UNKNOWN"], 1)
        self.assertEqual(summary["probe_comparisons"]["UNKNOWN"], 1)
        self.assertEqual(summary["probe_comparisons"]["CONTRADICT"], 1)
        self.assertEqual(selected(result, "prediction_committed")[1]["prediction"]["position"], [0, 0, 0])

    def test_budget_snapshot_tracks_failed_queries_without_exposing_next_measurement(self):
        program = STATIC.replace(
            'return {"position": state["position"], "request_query": True}',
            'return {"position": state["position"], "request_query": True, "hypotheses": step["budget"]}',
        ).replace(
            'measurement = context["measurement"]',
            'measurement = context["measurement"]\n    if context["budget"] != context["step"]["budget"]:\n        raise ValueError("inconsistent initial budget")',
        )
        result = replay_tape(
            [frame(0, [0, 0, 0]), frame(1, status="unknown"), frame(2, [99, 88, 77])],
            FrozenPythonProgram(program), ReplayConfig(1, schedule="adaptive"),
        )
        predictions = selected(result, "prediction_committed")
        self.assertEqual(predictions[0]["prediction"]["hypotheses"], {"limit": 1, "used": 0, "remaining": 1})
        self.assertEqual(predictions[1]["prediction"]["hypotheses"], {"limit": 1, "used": 1, "remaining": 0})
        self.assertEqual(predictions[1]["prediction"]["position"], [0, 0, 0])

    def test_probe_coverage_separates_abstention_from_sensor_unavailability(self):
        program = STATIC.replace(
            'return {"position": state["position"], "request_query": True}',
            'return {"position": None if step["index"] == 1 else state["position"], "request_query": False}',
        )
        result = replay_tape(
            [frame(0, [0, 0, 0]), frame(1, [100, 0, 0]), frame(2, [2, 0, 0]), frame(3, status="unknown")],
            FrozenPythonProgram(program), ReplayConfig(0),
        )
        summary = result["summary"]
        self.assertEqual(summary["probe_measurement_valid_count"], 2)
        self.assertEqual(summary["probe_measurement_unavailable_count"], 1)
        self.assertEqual(summary["probe_abstentions_on_valid_measurements"], 1)
        self.assertEqual(summary["probe_coverage_on_valid_measurements"], .5)
        self.assertEqual(summary["probe_mean_error_m"], 2.0)
        probes = selected(result, "independent_probe")
        self.assertEqual(probes[0]["comparison"]["reason"], "prediction_abstained")
        self.assertEqual(probes[2]["comparison"]["reason"], "measurement_unavailable")

    def test_unknown_anchor_is_not_imputed(self):
        result = replay_tape(
            [frame(0, status="unknown"), frame(1, [1, 2, 3])],
            FrozenPythonProgram(STATIC), ReplayConfig(0),
        )
        self.assertEqual(result["summary"]["anchor_valid"], 0)
        self.assertEqual(result["summary"]["probe_valid_count"], 0)
        self.assertIsNone(selected(result, "prediction_committed")[0]["prediction"]["position"])

    def test_fixed_schedule_ignores_request_and_uses_fixed_axes(self):
        program = STATIC.replace('"request_query": True', '"request_query": False, "query_axes": [0]')
        result = replay_tape(
            [frame(0, [0, 0, 0]), frame(1, [0, 0, 1]), frame(2, [0, 0, 2])],
            FrozenPythonProgram(program), ReplayConfig(3, fixed_indices=(2,), query_axes=(2,)),
        )
        released = selected(result, "measurement_released")
        self.assertEqual([event["index"] for event in released], [2])
        self.assertEqual(released[0]["measurement"]["axes"], [2])
        self.assertEqual(result["summary"]["queries_used"], 1)

    def test_actions_advance_without_queries_and_use_actual_ee_not_command(self):
        tape = [
            frame(0, [0, 0, 0], api="initial"),
            frame(1, [0, 0, 0], api="close_gripper"),
            frame(2, [0, 0, .2], robot=[0, 0, .2], args={"position": [10, 10, 20]}),
            frame(3, [0, 0, .2], robot=[0, 0, .2], api="open_gripper"),
            frame(4, [0, 0, .2], robot=[1, 1, 1], args={"position": [30, 30, 30]}),
        ]
        result = replay_tape(tape, FrozenPythonProgram(MEASURED_MOTION), ReplayConfig(0))
        self.assertEqual(result["summary"]["status"], "complete")
        self.assertEqual(result["summary"]["probe_mean_error_m"], 0.0)
        self.assertEqual(result["summary"]["queries_used"], 0)
        self.assertEqual(len(selected(result, "world_advanced")), 4)

    def test_predict_mutation_cannot_change_state_or_tolerance(self):
        program = STATIC.replace(
            'return {"position": state["position"], "request_query": True}',
            'state["position"][0] += 1\n    return {"position": state["position"], "request_query": False, "tolerance": 1000}',
        )
        result = replay_tape(
            [frame(0, [0, 0, 0]), frame(1, [0, 0, 0]), frame(2, [0, 0, 0])],
            FrozenPythonProgram(program), ReplayConfig(0, tolerance=.02),
        )
        predictions = selected(result, "prediction_committed")
        self.assertEqual([event["prediction"]["position"][0] for event in predictions], [1, 1])
        self.assertEqual(result["summary"]["probe_comparisons"]["CONTRADICT"], 2)
        self.assertEqual(result["summary"]["tolerance_m"], .02)

    def test_invalid_measurement_and_non_numeric_inputs_rejected(self):
        bad = frame(0, [float("nan"), 0, 0])
        with self.assertRaisesRegex(ValueError, "finite"):
            replay_tape([bad], FrozenPythonProgram(STATIC), ReplayConfig(0))
        with self.assertRaisesRegex(ValueError, "numeric"):
            replay_tape([frame(0, [0, 0, 0], args={"path": "/secret"})], FrozenPythonProgram(STATIC), ReplayConfig(0))
        duplicate = [frame(0, [0, 0, 0]), frame(0, [0, 0, 0])]
        with self.assertRaisesRegex(ValueError, "unique"):
            replay_tape(duplicate, FrozenPythonProgram(STATIC), ReplayConfig(0))

    def test_program_exception_and_invalid_output_reported(self):
        broken = STATIC.replace('return {"position": state["position"], "request_query": True}', 'raise ValueError("broken prediction")')
        result = replay_tape([frame(0, [0, 0, 0]), frame(1, [1, 0, 0])], FrozenPythonProgram(broken), ReplayConfig(1, fixed_indices=(1,)))
        self.assertEqual(result["summary"]["status"], "error")
        self.assertTrue(result["summary"]["incomplete"])
        self.assertEqual(result["summary"]["error"]["operation"], "predict")
        self.assertEqual(len(selected(result, "measurement_released")), 0)
        self.assertEqual(result["summary"]["probe_missing_predictions_on_valid_measurements"], 1)
        self.assertEqual(result["summary"]["probe_coverage_on_valid_measurements"], 0)
        malformed = STATIC.replace('"position": state["position"]', '"position": [1, 2]')
        result = replay_tape([frame(0, [0, 0, 0]), frame(1, [1, 0, 0])], FrozenPythonProgram(malformed), ReplayConfig(0))
        self.assertEqual(result["summary"]["status"], "error")

    def test_short_tape_keeps_unreached_schedule_and_assimilation_error_keeps_probe(self):
        tape = [frame(0, [0, 0, 0]), frame(1, [1, 0, 0])]
        result = replay_tape(tape, FrozenPythonProgram(STATIC), ReplayConfig(3, fixed_indices=(1, 3, 7)))
        self.assertEqual(result["summary"]["status"], "complete")
        self.assertEqual(result["summary"]["unreached_fixed_indices"], [3, 7])
        self.assertEqual(result["summary"]["queries_used"], 1)
        broken = STATIC.replace('measurement = evidence["measurement"]', 'raise ValueError("cannot update")')
        result = replay_tape(tape, FrozenPythonProgram(broken), ReplayConfig(1, fixed_indices=(1,)))
        self.assertEqual(result["summary"]["status"], "error")
        self.assertEqual(result["summary"]["probe_valid_count"], 1)
        self.assertEqual(result["summary"]["probe_missing_count"], 0)
        self.assertEqual(result["summary"]["probe_mean_error_m"], 1.0)

    def test_timeout_terminates_worker(self):
        started = time.monotonic()
        with self.assertRaises(ProgramTimeoutError):
            FrozenPythonProgram("def initialize(context):\n    while True:\n        pass\n", timeout_s=.15).call("initialize", {})
        self.assertLess(time.monotonic() - started, 3.0)

    def test_worker_import_boundary_and_user_file(self):
        with self.assertRaisesRegex(ProgramExecutionError, "only math"):
            FrozenPythonProgram("import os\ndef initialize(context):\n    return {}\n").call("initialize", {})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "numerical_program.py"
            path.write_text("import math\ndef initialize(context):\n    return math.sqrt(context['value'])\n")
            program = FrozenPythonProgram.from_file(path)
            self.assertEqual(program.call("initialize", {"value": 9}), 3.0)


if __name__ == "__main__":
    unittest.main()
