"""A recovered infrastructure attempt cannot reset budget or fabricate success."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/libero"))
import native_cc_recover_trial as recovery
from native_cc_protocol import identity
from fix_loop_state import Stage1State, ProtocolError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "aspire/sim"
        config = self.repo / "env_configs/libero/franka_libero_traced.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("env: {}")
        self.case = dict(model="test", suite="suite", task="task", dev_seeds=[51], smoke_budget=3,
                         context_tokens=1000000, max_output_tokens=64000, effort="high",
                         sim=str(self.repo), cuda_visible_devices="4,0", egl_device_id=0)
        self.case_path = self.root / "case.json"
        self.case_path.write_text(json.dumps(self.case))
        self.task_dir = self.repo / "outputs/libero_fix_loop/suite/task"
        self.state = Stage1State(self.task_dir, identity(self.case, self.repo))
        self.record = self.state.begin_trial("snapshot", 51, "get_observation()\n")
        self.state.finish_trial(self.record, result=dict(sandbox_rc=0), exit_code=0)
        self.record = self.state.begin_trial("smoke", 51, "get_observation()\n")
        self.original = self.task_dir / self.record["directory"]
        self.original.joinpath("replay.log").write_text("original interrupted log")
        command = [str(self.repo / ".venv-libero/bin/python3"), "scripts/libero/replay_trial.py",
            "--args.suite", "suite", "--args.task", "task", "--args.trial", "51", "--args.model", "test",
            "--args.replay-code", str(self.original / "code.py"), "--args.config", "env_configs/libero/franka_libero_traced.yaml",
            "--args.output-dir", str(self.original / "results")]
        (self.original / "command.json").write_text(json.dumps(command))
        self.attempt = self.root / "approved-resume"
        self.attempt.mkdir()

    def run_recovery(self, exit_code):
        def replay(command, **kwargs):
            artifact = Path(command[-1]) / "suite/task/trial_51_sandboxrc_0_reward_1.0_taskcompleted_1"
            artifact.mkdir(parents=True)
            return exit_code, ""
        with patch.object(recovery, "run_replay", side_effect=replay):
            return recovery.recover(self.case_path, self.record["directory"], self.attempt)

    def test_exact_program_continues_same_slot_and_preserves_old_evidence(self):
        result = self.run_recovery(0)
        after = json.loads(self.state.path.read_text())
        self.assertEqual(len(after["trials"]), 2)
        self.assertEqual(after["identity"], self.state.data["identity"])
        self.assertEqual(result["attempt"], 1)
        self.assertEqual(result["code_sha256"], self.record["code_sha256"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual((self.original / "replay.log").read_text(), "original interrupted log")
        self.assertIn("infrastructure-recovery", result["trial_dir"])

    def test_nonzero_exit_with_result_folder_is_not_completion(self):
        with self.assertRaises(ProtocolError):
            self.run_recovery(1)
        after = json.loads(self.state.path.read_text())
        self.assertEqual(after["trials"][-1]["status"], "infrastructure_error")
        self.assertEqual(len(after["trials"]), 2)

    def test_changed_program_cannot_be_adopted(self):
        (self.original / "code.py").write_text("open_gripper()")
        with self.assertRaisesRegex(ProtocolError, "program changed"):
            self.run_recovery(0)

    def test_existing_result_cannot_be_silently_rerun(self):
        (self.original / "results/trial_51_sandboxrc_0_reward_1.0_taskcompleted_1").mkdir(parents=True)
        with self.assertRaisesRegex(ProtocolError, "already has result artifacts"):
            self.run_recovery(0)


if __name__ == "__main__":
    unittest.main()
