"""Offline provenance and admission checks; no model or simulator requests."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/libero/code_world.py"
SPEC = importlib.util.spec_from_file_location("code_world_cli", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)

PROGRAM = """def initialize(context):
    return {}
def advance(state, step):
    return state
def predict(state, step):
    return {"position": None, "request_query": True}
def assimilate(state, evidence):
    return state
"""


class CodeWorldCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = self.root / "policy.py"
        self.policy.write_text("get_observation()\n")

    def generation_args(self, **overrides):
        fields = dict(policy=str(self.policy), representation="relation",
                      endpoint="http://127.0.0.1:9000/v1/chat/completions",
                      model="qwen-test", model_family="qwen",
                      open_weights_source="https://huggingface.co/Qwen/test",
                      reasoning_effort="xhigh", temperature=0.6, max_tokens=8192,
                      timeout=2, output=str(self.root / "generation"),
                      prepare_only=False, api_key_env=None)
        return argparse.Namespace(**(fields | overrides))

    def response(self, model="qwen-test", finish="stop"):
        return io.BytesIO(json.dumps({"model": model, "usage": {"total_tokens": 12},
                                     "choices": [{"finish_reason": finish,
                                                  "message": {"content": PROGRAM}}]}).encode())

    def capture(self, seed=51, **overrides):
        tape = self.root / "numeric_tape.jsonl"
        tape.write_text('{"frame_id": "anchor"}\n')
        identity = dict(status="complete", suite=cli.SUITE, task=cli.TASK, seed=seed,
                        frame_count=1, tape_sha256=cli.digest(tape.read_bytes()),
                        policy_sha256=cli.digest(self.policy.read_bytes())) | overrides
        cli.write_json(self.root / "capture_manifest.json", identity)
        return tape

    def test_prepare_only_neither_calls_model_nor_claims_generation(self):
        with patch.object(cli.urllib.request, "urlopen", side_effect=AssertionError("network")):
            result = cli.generate(self.generation_args(prepare_only=True))
        self.assertEqual(result["status"], "prepared")
        self.assertIsNone(result["model_served"])
        self.assertFalse((self.root / "generation/world.py").exists())

    def test_exact_model_and_normal_completion_are_required(self):
        for model, finish in [("claude-fixture", "stop"), ("qwen-test", "length")]:
            output = self.root / (model + finish)
            with self.subTest(model=model, finish=finish), \
                 patch.object(cli.urllib.request, "urlopen", return_value=self.response(model, finish)):
                with self.assertRaises(RuntimeError):
                    cli.generate(self.generation_args(output=str(output)))
                self.assertEqual(cli.read_json(output / "generation.json")["status"], "failed")
                self.assertFalse((output / "world.py").exists())

    def test_generation_records_provenance_without_persisting_key(self):
        with patch.dict(cli.os.environ, {"CW_TEST_KEY": "secret-fixture"}), \
             patch.object(cli.urllib.request, "urlopen", return_value=self.response()) as request:
            result = cli.generate(self.generation_args(api_key_env="CW_TEST_KEY"))
        self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer secret-fixture")
        self.assertEqual(result["status"], "generated_unvalidated")
        for path in (self.root / "generation").iterdir():
            self.assertNotIn("secret-fixture", path.read_text())
        program = self.root / "generation/world.py"
        self.assertEqual(cli.admit_program(program, cli.digest(self.policy.read_bytes()), False)["origin"], "api_generated")
        program.write_text(PROGRAM + "# altered after freeze\n")
        with self.assertRaisesRegex(ValueError, "provenance mismatch"):
            cli.admit_program(program, cli.digest(self.policy.read_bytes()), False)

    def test_model_endpoint_and_fresh_output_are_fail_closed(self):
        for overrides in [{"model": "aws/anthropic/claude"},
                          {"endpoint": "https://user:secret@example.org/v1"},
                          {"endpoint": "http://example.org/v1"}]:
            with self.subTest(overrides=overrides), \
                 patch.object(cli.urllib.request, "urlopen", side_effect=AssertionError("network")):
                with self.assertRaises(ValueError):
                    cli.generate(self.generation_args(**overrides))
        self.assertFalse((self.root / "generation").exists())
        (self.root / "generation").mkdir()
        with self.assertRaises(FileExistsError):
            cli.generate(self.generation_args(prepare_only=True))

    def test_incomplete_held_out_and_changed_tapes_are_rejected(self):
        for fields in [{"seed": 1}, {"seed": 50}, {"status": "running"},
                       {"tape_sha256": "wrong"}, {"frame_count": 2}]:
            with self.subTest(fields=fields):
                with self.assertRaises(ValueError):
                    cli.load_tape(self.capture(**fields))
        records, manifest = cli.load_tape(self.capture())
        self.assertEqual(manifest["seed"], 51)
        self.assertEqual(len(records), 1)

    def test_human_control_cannot_be_mistaken_for_model_generation(self):
        source = self.root / "hand.py"
        source.write_text(PROGRAM)
        with self.assertRaises(FileNotFoundError):
            cli.admit_program(source, cli.digest(self.policy.read_bytes()), False)
        self.assertEqual(cli.admit_program(source, "", True)["origin"], "hand_control")

    def test_score_records_program_failure_as_failed_not_complete(self):
        program = self.root / "hand.py"
        program.write_text(PROGRAM.replace("return {}", "raise ValueError('synthetic program failure')"))
        tape = self.capture()
        tape.write_text(json.dumps({
            "frame_id": 0, "action": {"api": "reset", "args": {}},
            "robot_state": {"position": [0, 0, 0], "orientation_wxyz": [1, 0, 0, 0], "gripper": 1},
            "measurement": {"status": "ok", "object_id": "soup", "position": [1, 0, 0], "frame": "world"},
        }) + "\n")
        identity = cli.read_json(self.root / "capture_manifest.json")
        cli.write_json(self.root / "capture_manifest.json", identity | {"tape_sha256": cli.digest(tape.read_bytes())})
        output = self.root / "failed_score"
        args = argparse.Namespace(tape=str(tape), program=str(program), hand_control=True,
                                  query_budget=2, schedule="fixed", fixed_indices="",
                                  query_axes="0,1,2", tolerance=0.02, timeout=2, output=str(output))
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            cli.score(args)
        self.assertEqual(cli.read_json(output / "score_manifest.json")["status"], "failed")
        self.assertEqual(cli.read_json(output / "summary.json")["status"], "error")


if __name__ == "__main__":
    unittest.main()
