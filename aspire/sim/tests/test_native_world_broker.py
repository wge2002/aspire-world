"""Offline protocol tests for the uncapped native world replay adapter.

No simulator, no model requests, no trials. The environment and reduced API here
are hand-built doubles; every fixture world program, policy, and inventory below
is synthetic and is never a run result.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM))

from cap.world_model import live_broker as lb
from cap.world_model import native_world_broker as nwb
from cap.world_model.relational_scene_broker import (SCHEDULE,
                                                     RelationalSceneBroker)

INVENTORY = [
    {"id": "bowl", "label": "bowl", "role": "manipulated",
     "confidence": "uncertain", "shape_prior": "unknown"},
    {"id": "plate", "label": "plate", "role": "target",
     "confidence": "uncertain", "shape_prior": "unknown"},
]

# Measurement the fake API always reports for the bowl.
MEASURED = [0.10, 0.20, 0.30]

# A world program that keeps a reference at the successful close_gripper frame
# and then asks for a relation check on every later frame, predicting the
# attached hypothesis exactly at the measured pose so the verdict is SUPPORT.
WORLD = f'''
MEASURED = {MEASURED!r}


def initialize(context):
    return {{"objects": {{e["id"]: {{"position": None, "attachment": "unknown"}}
                        for e in context["entities"]}},
            "reference_frame": None, "closed": False}}


def advance(state, step):
    if step["action"]["api"] == "close_gripper":
        state["closed"] = True
    return state


def predict(state, step):
    objects = {{k: {{"position": v["position"], "attachment": v["attachment"]}}
               for k, v in state["objects"].items()}}
    reference = state["reference_frame"]
    closing = step["action"]["api"] == "close_gripper"
    if closing and reference is None:
        purpose, request, check_reference = "reference", True, None
    elif reference is not None:
        purpose, request, check_reference = "relation", True, reference
    else:
        purpose, request, check_reference = "none", False, None
    return {{"objects": objects, "request_query": request,
            "query_purpose": purpose, "query_axes": [0, 1, 2],
            "grasp_check": {{"object_id": "bowl",
                            "reference_frame": check_reference,
                            "attached_position": list(MEASURED),
                            "free_position": [9.0, 9.0, 9.0]}}}}


def assimilate(state, evidence):
    status = evidence["comparison"]["status"]
    if evidence["purpose"] == "reference":
        if evidence["status"] == "ok":
            state["reference_frame"] = evidence["frame"]
        return state
    if status == "SUPPORT":
        state["objects"]["bowl"]["attachment"] = "attached"
    elif status == "CONTRADICT":
        state["objects"]["bowl"]["attachment"] = "free"
    return state
'''

POLICY = "decision = world_verify()\nprint(decision)\n"


class FakeApi:
    """Minimal reduced-API double: public observation only."""

    def __init__(self):
        self.calls = 0

    def get_observation(self):
        return {
            "agentview": {
                "images": {"rgb": [[0]], "depth": [[1.0]]},
                "intrinsics": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0],
                               [0.0, 0.0, 1.0]],
                "pose_mat": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
                             [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
            },
            "robot_cartesian_pos": [0.0, 0.0, 0.4, 1.0, 0.0, 0.0, 0.0, 0.04],
        }

    def segment_sam3_text_prompt(self, rgb, text):
        self.calls += 1
        return [{"score": 0.9, "mask": [[True]], "box": [0, 0, 1, 1]}]

    def mask_to_world_points(self, mask, depth, intrinsics, pose_mat):
        return [list(MEASURED) for _ in range(32)]

    def move_to_joints(self, joints):
        return True

    def close_gripper(self):
        return True

    def open_gripper(self):
        return True

    def functions(self):
        return {"move_to_joints": self.move_to_joints,
                "close_gripper": self.close_gripper,
                "open_gripper": self.open_gripper,
                "get_observation": self.get_observation}


class FakeEnv:
    def __init__(self, api):
        self._apis = {"reduced": api}

    def close(self):
        return None


def broker_config(**overrides):
    config = {
        "query_budget": lb.UNLIMITED,
        "tolerance": 0.03,
        "max_actions": lb.UNLIMITED,
        "max_recovery": lb.UNLIMITED,
        "observation": {"object_id": "bowl", "segmentation_prompt": "bowl",
                        "min_score": 0.5},
        "scene_inventory": [dict(e) for e in INVENTORY],
    }
    config.update(overrides)
    return config


def make_broker(directory, **overrides):
    api = FakeApi()
    broker = RelationalSceneBroker(
        Path(directory), broker_config(**overrides), {}, WORLD)
    broker.attach(FakeEnv(api))
    wrapped = broker.api.functions()
    broker.capture("initial", {})
    return broker, wrapped


def drive_relation_queries(broker, wrapped, count):
    """Establish the reference, then run `count` relation checks."""
    wrapped["close_gripper"]()
    verdicts = []
    for _ in range(count):
        wrapped["move_to_joints"]([0.0] * 7)
        verdicts.append(wrapped["world_verify"]())
    return verdicts


class NativeUncappedExecution(unittest.TestCase):
    """The uncapped path must actually exceed every old numeric cap."""

    def test_more_than_thirty_actions_are_allowed_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker, wrapped = make_broker(tmp)
            for _ in range(45):
                wrapped["move_to_joints"]([0.0] * 7)
            self.assertEqual(broker._action_count, 45)
            self.assertEqual(broker._action_attempts, 45)
            self.assertEqual(broker._action_limit_denials, 0)
            self.assertEqual(
                [e for e in broker._events
                 if e["event"] == "action_limit_denied"], [])
            broker.close()
            self.assertEqual(broker.manifest["action_count"], 45)
            self.assertEqual(broker.manifest["max_actions"], lb.UNLIMITED)

    def test_recovery_never_refused_and_still_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker, wrapped = make_broker(tmp)
            grants = [wrapped["use_recovery"]() for _ in range(5)]
            self.assertEqual(grants, [True] * 5)
            self.assertTrue(wrapped["recovery_available"]())
            self.assertEqual(broker._recovery_used, 5)
            self.assertEqual(
                [e for e in broker._events
                 if e["event"] == "recovery_denied"], [])
            broker.close()
            self.assertEqual(broker.manifest["recovery_used"], 5)

    def test_more_than_four_queries_are_sampled(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker, wrapped = make_broker(tmp)
            verdicts = drive_relation_queries(broker, wrapped, 6)
            self.assertEqual([v["status"] for v in verdicts], ["support"] * 6)
            # One reference query plus six relation queries: past the old 4.
            self.assertEqual(broker._query_used, 7)
            self.assertEqual(
                [e for e in broker._events
                 if e["event"] == "query_denied"], [])
            broker.close()
            self.assertEqual(broker.manifest["query_used"], 7)
            self.assertEqual(broker.manifest["query_budget"], lb.UNLIMITED)
            self.assertEqual(
                broker.manifest["query_purpose_counts"],
                {"reference": 1, "relation": 6})

    def test_unlimited_is_a_word_in_the_budget_the_program_sees(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker, _ = make_broker(tmp)
            self.assertEqual(
                broker._budget_snapshot(),
                {"limit": lb.UNLIMITED, "used": 0,
                 "remaining": lb.UNLIMITED})
            broker.close()

    def test_budget_helpers_reject_stand_in_numbers_for_unlimited(self):
        self.assertIsNone(lb.parse_budget(lb.UNLIMITED, "x"))
        self.assertEqual(lb.parse_budget(0, "x"), 0)
        self.assertEqual(lb.parse_budget(30, "x"), 30)
        for bad in (None, True, -1, 4.0, "999999", "Unlimited", "", [4]):
            with self.assertRaises(ValueError):
                lb.parse_budget(bad, "x")
        self.assertFalse(lb.budget_exhausted(None, 10 ** 9))
        self.assertTrue(lb.budget_exhausted(4, 4))
        self.assertFalse(lb.budget_exhausted(4, 3))
        self.assertEqual(lb.budget_remaining(None, 7), lb.UNLIMITED)
        self.assertEqual(lb.budget_remaining(4, 1), 3)


CAPPED = {"max_actions": 30, "max_recovery": 1, "query_budget": 4}


class FrozenCapsUnchanged(unittest.TestCase):
    """A numeric profile keeps exactly the caps and behavior it had."""

    def test_thirty_first_action_is_still_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker, wrapped = make_broker(tmp, **CAPPED)
            for _ in range(30):
                wrapped["move_to_joints"]([0.0] * 7)
            with self.assertRaises(RuntimeError):
                wrapped["move_to_joints"]([0.0] * 7)
            self.assertEqual(broker._action_count, 30)
            self.assertEqual(broker._action_attempts, 31)
            self.assertEqual(broker._action_limit_denials, 1)
            broker.close()
            self.assertEqual(broker.manifest["action_count"], 30)
            self.assertEqual(broker.manifest["max_actions"], 30)

    def test_second_recovery_is_still_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker, wrapped = make_broker(tmp, **CAPPED)
            self.assertTrue(wrapped["use_recovery"]())
            self.assertFalse(wrapped["recovery_available"]())
            self.assertFalse(wrapped["use_recovery"]())
            self.assertEqual(broker._recovery_used, 1)
            self.assertEqual(
                [e["reason"] for e in broker._events
                 if e["event"] == "recovery_denied"],
                ["recovery_budget_exhausted"])
            broker.close()

    def test_fifth_query_is_still_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker, wrapped = make_broker(tmp, **CAPPED)
            # 1 reference + 3 relations fills the budget of 4; the 4th relation
            # check is refused.
            verdicts = drive_relation_queries(broker, wrapped, 4)
            self.assertEqual(broker._query_used, 4)
            self.assertEqual([v["status"] for v in verdicts[:3]],
                             ["support"] * 3)
            self.assertEqual(verdicts[-1]["reason"], "budget_exhausted")
            self.assertEqual(
                [e["reason"] for e in broker._events
                 if e["event"] == "query_denied"], ["budget_exhausted"])
            broker.close()
            self.assertEqual(broker.manifest["query_budget"], 4)

    def test_frozen_loaders_still_refuse_the_unlimited_marker(self):
        from cap.world_model import scene_broker as sb
        profiles = (
            (lb.load_live_config, {"schema_version": 2,
             "mode": "opus46-diagnostic", "profile": "bowl-on-plate-dev"}),
            (sb.load_scene_config, {"schema_version": 3, "mode": sb.MODE,
             "profile": sb.PROFILE, "scene_inventory": INVENTORY,
             "identity_binding": "unique_high_score_mask",
             "query_schedule": "policy_world_verify_only"}),
        )
        for loader, profile in profiles:
            for key in CAPPED:
                with self.subTest(loader=loader.__name__, key=key):
                    with tempfile.TemporaryDirectory() as tmp:
                        native = write_inputs(tmp)
                        config = {k: native[k] for k in (
                            "task_gate", "world_program", "observation",
                            "tolerance", "trial_timeout_seconds", "output_root",
                            "run_name", "model_provenance")}
                        config.update(CAPPED, dev_seeds=[51, 65], **profile)
                        config[key] = lb.UNLIMITED
                        (Path(tmp) / "native_config.json").write_text(
                            json.dumps(config))
                        with self.assertRaisesRegex(ValueError, key):
                            loader(make_args(tmp, trial=51))


def write_inputs(root, *, world=WORLD, policy=POLICY, inventory=INVENTORY):
    root = Path(root)
    (root / "world_program.py").write_text(world)
    (root / "policy.py").write_text(policy)
    (root / "inventory.json").write_text(json.dumps(inventory) + "\n")
    (root / "task.yaml").write_text(
        "env:\n  cfg:\n    privileged: false\n"
        "    apis: [FrankaLiberoApiReducedSkillLibraryTraced]\n")
    config = {
        "schema_version": nwb.SCHEMA, "mode": nwb.MODE,
        "task_gate": {"suite": lb.SUITE, "task": lb.TASK},
        "world_program": "world_program.py",
        "world_program_sha256": lb._sha256(root / "world_program.py"),
        "policy_sha256": lb._sha256(root / "policy.py"),
        "scene_inventory_path": "inventory.json",
        "scene_inventory_sha256": lb._sha256(root / "inventory.json"),
        "observation": {"object_id": "bowl", "segmentation_prompt": "bowl",
                        "min_score": 0.5},
        "query_budget": lb.UNLIMITED, "tolerance": 0.03,
        "max_actions": lb.UNLIMITED, "max_recovery": lb.UNLIMITED,
        "trial_timeout_seconds": 900,
        "identity_binding": nwb.IDENTITY_BINDING, "query_schedule": SCHEDULE,
        "output_root": str(root / "evidence"), "run_name": "native_world",
        "model_provenance": {"model_id": "native-cc",
                             "policy_generator": "native-cc",
                             "world_program_generator": "native-cc",
                             "generation_context": "phase1 offline test"},
    }
    (root / "native_config.json").write_text(json.dumps(config) + "\n")
    return config


def make_args(root, **overrides):
    root = Path(root)
    args = {"suite": lb.SUITE, "task": lb.TASK, "trial": 7,
            "world_model_config": str(root / "native_config.json"),
            "ordinary_budget_config": None,
            "output_dir": str(root / "replay_outputs"),
            "replay_code": str(root / "policy.py"),
            "config": str(root / "task.yaml"), "record_video": False,
            "debug": False, "interactive": False}
    args.update(overrides)
    return SimpleNamespace(**args)


class HashConsistency(unittest.TestCase):
    """policy / world / inventory digests are validated and preserved."""

    def test_matching_digests_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_inputs(tmp)
            config, _ = nwb.load_native_config(make_args(tmp))
            self.assertEqual(config["scene_inventory"], INVENTORY)
            self.assertTrue(Path(config["world_program"]).is_absolute())
            self.assertEqual(config["max_actions"], lb.UNLIMITED)

    def test_each_mutated_input_is_rejected(self):
        for name, text in [("world_program.py", WORLD + "\n# edited\n"),
                           ("policy.py", POLICY + "# edited\n"),
                           ("inventory.json", json.dumps(INVENTORY[:1]))]:
            with tempfile.TemporaryDirectory() as tmp:
                write_inputs(tmp)
                (Path(tmp) / name).write_text(text)
                with self.assertRaises(ValueError, msg=name):
                    nwb.load_native_config(make_args(tmp))

    def test_numeric_caps_are_refused_by_the_native_loader(self):
        for key, value in [("max_actions", 30), ("max_recovery", 1),
                           ("query_budget", 4), ("max_actions", 10 ** 9)]:
            with tempfile.TemporaryDirectory() as tmp:
                config = write_inputs(tmp)
                config[key] = value
                (Path(tmp) / "native_config.json").write_text(
                    json.dumps(config))
                with self.assertRaises(ValueError, msg=f"{key}={value}"):
                    nwb.load_native_config(make_args(tmp))

    def test_prepare_preserves_all_four_input_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_inputs(tmp)
            args = make_args(tmp)
            config, _ = nwb.load_native_config(args)
            request_path = nwb.prepare(args, config)
            directory = request_path.parent
            manifest = json.loads(
                (directory / "live_manifest.json").read_text())
            self.assertEqual(manifest["mode"], nwb.MODE)
            self.assertEqual(manifest["schema_version"], nwb.SCHEMA)
            self.assertEqual(manifest["execution_caps"], lb.UNLIMITED)
            self.assertEqual(manifest["max_actions"], lb.UNLIMITED)
            self.assertEqual(manifest["query_budget"], lb.UNLIMITED)
            for field, name in [
                    ("policy_sha256", "frozen_policy.py"),
                    ("world_program_sha256", "world_program.py"),
                    ("scene_inventory_sha256", "scene_inventory.json"),
                    ("live_config_sha256", "live_config.json"),
                    ("yaml_sha256", "source_config.yaml")]:
                self.assertEqual(manifest[field],
                                 lb._sha256(directory / name), field)
            self.assertEqual(manifest["policy_sha256"],
                             lb._sha256(Path(tmp) / "policy.py"))
            self.assertEqual(manifest["world_program_sha256"],
                             lb._sha256(Path(tmp) / "world_program.py"))
            self.assertEqual(manifest["scene_inventory_sha256"],
                             lb._sha256(Path(tmp) / "inventory.json"))
            request = json.loads(request_path.read_text())
            self.assertEqual(request["schema_version"], nwb.SCHEMA)
            self.assertIsNone(request["args"]["world_model_config"])

    def test_child_rejects_a_swapped_world_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_inputs(tmp)
            args = make_args(tmp)
            config, _ = nwb.load_native_config(args)
            request_path = nwb.prepare(args, config)
            (request_path.parent / "world_program.py").write_text(
                WORLD + "\n# swapped after prepare\n")
            with self.assertRaises(ValueError):
                nwb._child_main(request_path)


def load_replay_module():
    import importlib.util

    path = SIM / "scripts/libero/replay_trial.py"
    spec = importlib.util.spec_from_file_location("_native_test_replay", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Routing(unittest.TestCase):
    """Opt-in only: the ordinary replay path must not enter the world path."""

    def test_ordinary_replay_never_enters_the_world_path(self):
        replay = load_replay_module()
        args = SimpleNamespace(
            suite=lb.SUITE, task=lb.TASK, trial=7, world_model_config=None,
            ordinary_budget_config=None, output_dir="./outputs/x",
            replay_code=None, config="c.yaml", record_video=False,
            debug=False, interactive=False)
        with patch.object(replay, "_run_replay", return_value="ordinary") as run, \
                patch("aspire.sim.cap.world_model.native_world_broker"
                      ".run_native_world") as native:
            self.assertEqual(replay.main(args), "ordinary")
        run.assert_called_once_with(args)
        native.assert_not_called()

    def test_native_mode_routes_to_the_native_runner(self):
        replay = load_replay_module()
        with tempfile.TemporaryDirectory() as tmp:
            write_inputs(tmp)
            args = make_args(tmp)
            with patch.object(replay, "_run_replay") as run, \
                    patch("aspire.sim.cap.world_model.native_world_broker"
                          ".run_native_world", return_value="native") as native:
                self.assertEqual(replay.main(args), "native")
            native.assert_called_once_with(args)
            run.assert_not_called()

    def test_both_config_flags_together_are_refused(self):
        replay = load_replay_module()
        with tempfile.TemporaryDirectory() as tmp:
            write_inputs(tmp)
            args = make_args(tmp, ordinary_budget_config="o.json")
            with self.assertRaises(ValueError):
                replay.main(args)


if __name__ == "__main__":
    unittest.main()
