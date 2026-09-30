"""Offline replay integration tests: no simulator, model, GPU, or new dependency."""
from __future__ import annotations

import builtins
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CAPTURE_NAME = "aspire.sim.cap.world_model.libero_capture"


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


capture = load_file("world_capture_test_subject", ROOT / "cap/world_model/libero_capture.py")


def yaml_stub():
    module = types.ModuleType("yaml")
    module.safe_load = lambda source: json.loads(source.read() if hasattr(source, "read") else source)
    module.dump = lambda value, destination: json.dump(value, destination)
    return module


class FakeAPI:
    def __init__(self):
        self.actions = []
        self.observation_calls = 0
        self.masks = [{"score": 0.9, "mask": [[True]]}]

    def functions(self):
        return {"move_to_joints": self.move_to_joints, "close_gripper": self.close_gripper,
                "get_observation": self.get_observation}

    def move_to_joints(self, joints):
        self.actions.append(("move", joints))
        return 17

    def close_gripper(self):
        self.actions.append(("close",))
        return "closed"

    def get_observation(self):
        self.observation_calls += 1
        return {"robot_cartesian_pos": [0.1, 0.2, 0.3, 1, 0, 0, 0, 0.4],
                "agentview": {"images": {"rgb": [[[0, 0, 0]]], "depth": [[1.0]]},
                              "intrinsics": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                              "pose_mat": [[1, 0, 0, 0], [0, 1, 0, 0],
                                           [0, 0, 1, 0], [0, 0, 0, 1]]}}

    def segment_sam3_text_prompt(self, rgb, prompt):
        return self.masks

    def mask_to_world_points(self, *args):
        return [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]


class FakeEnv:
    def __init__(self):
        self.api = FakeAPI()
        self._apis = {"reduced": self.api}
        self.closed = False
        self.globals = {}
        self.reset_seed = None

    def reset(self, seed):
        self.reset_seed = seed
        self.globals = self.api.functions()
        return {}, {}

    def step(self, code):
        exec(code, self.globals, self.globals)
        return {}, 0.0, False, False, {
            "sandbox_rc": 0, "stdout": "", "stderr": "", "task_completed": False}

    def close(self):
        self.closed = True


class OptInTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.base = {"env": {"cfg": {"privileged": False,
                                      "apis": ["FrankaLiberoApiReducedSkillLibraryTraced"],
                                      "low_level": {"untouched": "original"}}}}
        self.config = self.path / "original.yaml"
        self.config.write_text(json.dumps(self.base))
        self.policy = self.path / "policy.py"
        self.policy.write_text("a = close_gripper()\nb = move_to_joints([0, 0, 0, 0, 0, 0, 0])\n")
        self.world_config = {
            "schema_version": 1, "mode": "capture", "run_name": "test-v0",
            "model_provenance": {"model_id": "Qwen/test-open", "open_weights": True,
                                 "source": "https://huggingface.co/Qwen/test-open"},
            "observation": {"object_id": "alphabet_soup", "segmentation_prompt": "alphabet soup can"},
            "output_root": str(self.path / "world_outputs"),
        }
        self.world_path = self.path / "world.json"
        self.world_path.write_text(json.dumps(self.world_config))
        self.env = FakeEnv()
        instantiate = types.ModuleType("aspire.sim.cap.envs.configs.instantiate")
        instantiate.instantiate = lambda _: self.env
        loader = types.ModuleType("aspire.sim.cap.envs.configs.loader")
        loader.DictLoader = object
        self.dependencies = {
            "numpy": types.ModuleType("numpy"), "tyro": types.ModuleType("tyro"), "yaml": yaml_stub(),
            "aspire.sim.cap.envs.configs.instantiate": instantiate,
            "aspire.sim.cap.envs.configs.loader": loader,
        }
        self.context = patch.dict(sys.modules, self.dependencies)
        self.context.start()
        self.addCleanup(self.context.stop)
        self.replay = load_file("world_replay_test_subject", ROOT / "scripts/libero/replay_trial.py")
        self.replay._find_task_id = lambda suite, task: 3
        self.args = self.replay.ReplayTrialArgs(
            suite="libero_object_swap", task=capture.TASK, trial=51,
            config=str(self.config), output_dir=str(self.path / "outputs"),
            replay_code=str(self.policy), record_video=False)

    def enabled(self):
        self.args.world_model_config = str(self.world_path)
        return self.args

    def run_quiet(self, args):
        # File encoding is already exercised by the normal tracer. Numeric capture
        # tests use plain lists so that these integration tests need only stdlib.
        with patch.dict(sys.modules, {CAPTURE_NAME: capture}), \
             patch.object(capture, "_launch_child", side_effect=self.inline_child), \
             patch.object(capture.LiberoCapture, "_save_sources", return_value={}), \
             patch.object(capture.LiberoCapture, "_save_masks"), \
             patch("sys.stdout", new_callable=io.StringIO):
            self.replay.main(args)

    def inline_child(self, request_path, environment, timeout):
        """Exercise the private child with a fake env; parent process is tested below."""
        capture._run_child_request(request_path, self.replay._run_replay)
        return {"schema_version": 1, "status": "completed", "exit_code": 0,
                "timeout_seconds": timeout, "manifest_valid": False}

    def manifest_path(self):
        return self.path / "world_outputs/test-v0/seed_51/capture_manifest.json"

    def test_no_flag_runs_original_actions_paths_and_configuration_without_world_import(self):
        args_before = copy.deepcopy(vars(self.args))
        input_before = self.config.read_bytes()
        importer = builtins.__import__

        def reject_world(name, *args, **kwargs):
            if "world_model" in name:
                raise AssertionError("opt-in module imported by default replay")
            return importer(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_world), \
             patch("sys.stdout", new_callable=io.StringIO):
            self.replay.main(self.args)
        self.assertEqual(vars(self.args), args_before)
        self.assertEqual(self.config.read_bytes(), input_before)
        self.assertEqual(self.env.api.actions, [("close",), ("move", [0] * 7)])
        self.assertEqual(self.env.globals["a"], "closed")
        self.assertEqual(self.env.globals["b"], 17)
        self.assertEqual(self.env.api.observation_calls, 0)
        self.assertFalse(self.env.closed)  # Existing lifecycle is unchanged.
        self.assertFalse((self.path / "world_outputs").exists())
        task_dir = Path(self.args.output_dir) / self.args.suite / self.args.task
        saved = json.loads((task_dir / "config.yaml").read_text())
        self.assertEqual(saved["env"]["cfg"]["low_level"]["task_id"], 3)
        self.assertEqual(saved["env"]["cfg"]["low_level"]["max_steps"], 4000)
        self.assertIn(self.args.model.replace("/", "_"), saved["output_dir"])
        self.assertEqual(len(list(task_dir.rglob("code.py"))), 1)

    def test_enabled_copies_args_freezes_inputs_and_preserves_actions(self):
        self.enabled()
        before = copy.deepcopy(vars(self.args))
        self.run_quiet(self.args)
        self.assertEqual(vars(self.args), before)
        self.assertEqual(self.config.read_text(), json.dumps(self.base))
        self.assertEqual(self.env.api.actions, [("close",), ("move", [0] * 7)])
        self.assertEqual((self.env.globals["a"], self.env.globals["b"]), ("closed", 17))
        self.assertTrue(self.env.closed)
        self.assertNotIn("functions", vars(self.env.api))
        manifest = json.loads(self.manifest_path().read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["frame_count"], 3)
        self.assertEqual(manifest["model_requests"], 0)
        self.assertFalse(manifest["generated_world"])
        directory = self.manifest_path().parent
        self.assertEqual((directory / "frozen_policy.py").read_bytes(), self.policy.read_bytes())
        self.assertEqual(manifest["policy_sha256"], capture._sha256(self.policy))
        self.assertEqual(manifest["tape_sha256"], capture._sha256(directory / manifest["tape"]))
        tape = [json.loads(line) for line in (directory / manifest["tape"]).read_text().splitlines()]
        self.assertEqual([row["action"]["api"] for row in tape], ["initial", "close_gripper", "move_to_joints"])
        self.assertEqual([row["frame_id"] for row in tape], [0, 1, 2])
        self.assertEqual(tape[1]["robot_state"]["position"], [0.1, 0.2, 0.3])
        self.assertFalse(tape[0]["measurement"]["source"]["identity_verified"])
        self.assertFalse((Path(self.args.output_dir) / self.args.suite).exists())
        self.assertFalse(Path(self.args.output_dir).exists())
        with self.assertRaises(FileExistsError):
            self.run_quiet(self.args)

    def test_failure_restores_api_and_closes_environment(self):
        self.enabled()
        self.env.step = lambda code: (_ for _ in ()).throw(RuntimeError("fake replay failure"))
        with self.assertRaisesRegex(RuntimeError, "fake replay failure"):
            self.run_quiet(self.args)
        self.assertTrue(self.env.closed)
        self.assertNotIn("functions", vars(self.env.api))
        manifest = json.loads(self.manifest_path().read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["error"], "RuntimeError")

    def test_ambiguous_or_missing_segmentation_is_unknown_not_top_one(self):
        for masks, reason in (([{"score": 0.9, "mask": [[True]]},
                                {"score": 0.8, "mask": [[True]]}], "ambiguous_segmentation"),
                              ([], "no_eligible_mask")):
            with self.subTest(reason=reason):
                directory = self.path / reason
                directory.mkdir()
                settings = copy.deepcopy(self.world_config)
                settings["observation"]["min_score"] = 0.5
                collector = capture.LiberoCapture(directory, settings, {"measurement_ok": 0})
                collector.api = self.env.api
                collector.api.masks = masks
                with patch.object(collector, "_save_sources", return_value={}), \
                     patch.object(collector, "_save_masks"):
                    collector.capture("initial", {})
                collector.close()
                row = json.loads((directory / "numeric_tape.jsonl").read_text())
                self.assertEqual(row["measurement"]["status"], "unknown")
                self.assertEqual(row["measurement"]["reason"], reason)
                self.assertIsNone(row["measurement"]["position"])
                self.assertEqual(row["capture_cost"]["segmentation_attempts"], 1)
                self.assertEqual(row["capture_cost"]["projection_attempts"], 0)

    def test_segmentation_exception_preserves_action_return_and_records_unknown(self):
        self.enabled()
        self.env.api.segment_sam3_text_prompt = lambda *args: (_ for _ in ()).throw(TimeoutError())
        self.run_quiet(self.args)
        self.assertEqual(self.env.globals["b"], 17)
        rows = [json.loads(line) for line in (self.manifest_path().parent / "numeric_tape.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["measurement"]["reason"] == "capture_error:TimeoutError" for row in rows))
        self.assertTrue(all(row["capture_cost"]["segmentation_attempts"] == 1 for row in rows))

    def test_rejects_invalid_modes_before_output_or_environment(self):
        self.enabled()
        for updates in ({"trial": 1}, {"trial": 66}, {"interactive": True},
                        {"replay_code": None}, {"suite": "libero_90"}):
            args = copy.deepcopy(self.args)
            for name, value in updates.items():
                setattr(args, name, value)
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.run_quiet(args)
        self.assertFalse((self.path / "outputs").exists())
        self.assertIsNone(self.env.reset_seed)

    def test_rejects_privileged_and_closed_model_provenance(self):
        self.enabled()
        for field in ("privileged", "low_level"):
            base = copy.deepcopy(self.base)
            if field == "low_level":
                base["env"]["cfg"][field]["privileged"] = True
            else:
                base["env"]["cfg"][field] = True
            self.config.write_text(json.dumps(base))
            with self.assertRaisesRegex(ValueError, "privileged"):
                self.run_quiet(self.args)
        self.config.write_text(json.dumps(self.base))
        self.world_config["model_provenance"]["model_id"] = "anthropic/claude-test"
        self.world_path.write_text(json.dumps(self.world_config))
        with self.assertRaisesRegex(ValueError, "open-weights"):
            self.run_quiet(self.args)
        self.assertFalse((self.path / "outputs").exists())

    def test_missing_proprio_is_failed_evidence_with_preserved_frame(self):
        self.enabled()
        self.env.api.get_observation = lambda: {}
        with self.assertRaisesRegex(RuntimeError, "capture evidence is incomplete"):
            self.run_quiet(self.args)
        manifest = json.loads(self.manifest_path().read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["frame_count"], 3)
        self.assertTrue(all(error.startswith("missing_proprio") for error in manifest["capture_errors"]))
        rows = [json.loads(line) for line in (self.manifest_path().parent / "numeric_tape.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["robot_state"] is None for row in rows))

    def test_source_write_failure_raises_after_manifest_and_cleanup(self):
        self.enabled()
        with patch.dict(sys.modules, {CAPTURE_NAME: capture}), \
             patch.object(capture, "_launch_child", side_effect=self.inline_child), \
             patch.object(capture.LiberoCapture, "_save_sources", side_effect=OSError("fake disk error")), \
             patch.object(capture.LiberoCapture, "_save_masks"), \
             patch("sys.stdout", new_callable=io.StringIO), \
             self.assertRaisesRegex(RuntimeError, "capture evidence is incomplete"):
            self.replay.main(self.args)
        self.assertTrue(self.env.closed)
        self.assertNotIn("functions", vars(self.env.api))
        manifest = json.loads(self.manifest_path().read_text())
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["trial_result"]["sandbox_rc"], 0)
        self.assertEqual(manifest["frame_count"], 3)
        self.assertTrue(all(error == "source_write:OSError" for error in manifest["capture_errors"]))

    def test_failed_policy_with_complete_capture_is_valid_evidence(self):
        self.enabled()

        def failed_policy(code):
            self.env.globals["close_gripper"]()
            return {}, 0.0, False, False, {
                "sandbox_rc": 1, "stdout": "", "stderr": "ValueError: fake policy failure",
                "task_completed": False}

        self.env.step = failed_policy
        self.run_quiet(self.args)
        manifest = json.loads(self.manifest_path().read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["trial_result"]["sandbox_rc"], 1)
        self.assertEqual(manifest["frame_count"], 2)

    def test_rejects_output_overlap_and_credential_config_before_writing(self):
        self.enabled()
        for changes in ({"output_root": self.args.output_dir},
                        {"output_root": str(Path(self.args.output_dir) / "world_model")},
                        {"api_key": "do-not-save"},
                        {"model_provenance": {**self.world_config["model_provenance"],
                                              "source": "https://example.com/model?key=do-not-save"}}):
            settings = {**self.world_config, **changes}
            self.world_path.write_text(json.dumps(settings))
            with self.subTest(changes=list(changes)), self.assertRaises(ValueError):
                self.run_quiet(self.args)
        self.assertFalse((self.path / "outputs").exists())
        self.assertFalse((self.path / "world_outputs").exists())

    def test_nested_actions_capture_one_outer_boundary_and_keep_original_exception(self):
        directory = self.path / "nested"
        directory.mkdir()
        settings = copy.deepcopy(self.world_config)
        settings["observation"]["min_score"] = 0.5
        collector = capture.LiberoCapture(directory, settings, {"measurement_ok": 0})
        collector.attach(self.env)
        self.addCleanup(collector.close)
        original_functions = self.env.api.functions

        def outer():
            original_functions()["move_to_joints"]([1] * 7)
            raise ValueError("original action failure")

        wrapped = collector._wrap(outer, "goto_pose")
        with patch.object(collector, "_save_sources", return_value={}), \
             patch.object(collector, "_save_masks"), \
             self.assertRaisesRegex(ValueError, "original action failure"):
            wrapped()
        rows = [json.loads(line) for line in (directory / "numeric_tape.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"]["api"], "goto_pose")
        self.assertEqual(rows[0]["action_error"], "ValueError")
        self.assertEqual(self.env.api.actions, [("move", [1] * 7)])

    def test_parent_request_excludes_model_secrets_and_rejects_explicit_key_before_writes(self):
        self.enabled()
        self.args.api_key = "fixture-secret"
        with self.assertRaisesRegex(ValueError, "do not pass"):
            self.run_quiet(self.args)
        self.assertFalse((self.path / "world_outputs").exists())
        self.args.api_key = None
        self.args.server_url = "https://user:fixture-secret@example.com"
        self.run_quiet(self.args)
        request = json.loads((self.manifest_path().parent / "child_request.json").read_text())
        self.assertNotIn("api_key", request["args"])
        self.assertNotIn("server_url", request["args"])
        self.assertNotIn("fixture-secret", json.dumps(request))

    def test_child_environment_is_allowlisted_and_service_urls_cannot_carry_credentials(self):
        parent = {"PATH": "/bin", "HOME": "/tmp/config-home", "CUDA_VISIBLE_DEVICES": "7",
                  "NVIDIA_API_KEY": "fixture-secret", "HF_TOKEN": "fixture-secret",
                  "AWS_ACCESS_KEY_ID": "fixture-secret", "HTTP_PROXY": "http://secret:pass@example.com",
                  "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/metadata", "EXTRA_RUNTIME_TOKEN": "fixture-secret",
                  "SAM3_SERVICE_URL": "http://127.0.0.1:8214"}
        environment = capture.child_environment(parent)
        self.assertEqual(environment["CUDA_VISIBLE_DEVICES"], "7")
        self.assertEqual(environment["SAM3_SERVICE_URL"], "http://127.0.0.1:8214")
        self.assertEqual(environment["HOME"], "/tmp/config-home")
        self.assertFalse(any("fixture-secret" in value for value in environment.values()))
        self.assertNotIn("HTTP_PROXY", environment)
        self.assertNotIn("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", environment)
        with self.assertRaisesRegex(ValueError, "without credentials"):
            capture.child_environment({"SAM3_SERVICE_URL": "http://token:secret@localhost:8114"})

    def test_real_lightweight_child_receives_filtered_environment_and_timeout_is_failed(self):
        self.enabled()
        self.world_config["trial_timeout_seconds"] = 0.15
        self.world_path.write_text(json.dumps(self.world_config))
        real_popen = subprocess.Popen

        def fixture_child(command, **kwargs):
            return real_popen([sys.executable, "-c", (
                "import json,os,time; print(json.dumps({'leaked': 'NVIDIA_API_KEY' in os.environ, "
                "'cuda': os.environ.get('CUDA_VISIBLE_DEVICES')}), flush=True); time.sleep(30)")], **kwargs)

        with patch.dict(sys.modules, {CAPTURE_NAME: capture}), \
             patch.dict(os.environ, {"NVIDIA_API_KEY": "fixture-secret", "CUDA_VISIBLE_DEVICES": "7"}), \
             patch.object(capture.subprocess, "Popen", side_effect=fixture_child), \
             self.assertRaisesRegex(RuntimeError, "capture child timeout"):
            self.replay.main(self.args)
        directory = self.manifest_path().parent
        receipt = json.loads((directory / "child_exit.json").read_text())
        self.assertEqual(receipt["status"], "timeout")
        self.assertFalse(receipt["manifest_valid"])
        self.assertLess(receipt["elapsed_seconds"], 5)
        self.assertEqual(json.loads((directory / "child_stdout.log").read_text()), {"leaked": False, "cuda": "7"})
        self.assertEqual(json.loads(self.manifest_path().read_text())["status"], "failed")
        self.assertIsNone(self.env.reset_seed)

    def test_child_nonzero_and_missing_manifest_are_not_success(self):
        self.enabled()
        for status, code in (("nonzero", 3), ("completed", 0)):
            with self.subTest(status=status):
                self.world_config["run_name"] = status
                self.world_path.write_text(json.dumps(self.world_config))
                with patch.dict(sys.modules, {CAPTURE_NAME: capture}), \
                     patch.object(capture, "_launch_child", return_value={"status": status, "exit_code": code}), \
                     self.assertRaisesRegex(RuntimeError, "incomplete evidence"):
                    self.replay.main(self.args)
                directory = self.path / "world_outputs" / status / "seed_51"
                self.assertEqual(json.loads((directory / "capture_manifest.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
