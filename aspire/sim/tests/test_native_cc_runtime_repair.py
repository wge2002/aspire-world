"""Regression cases for the interrupted Long campaign's concrete failures."""
import base64
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM / "scripts/common"))
sys.path.insert(0, str(SIM / "scripts/libero"))
import native_cc_freeze as freeze
import native_cc_guard as guard
import native_cc_protocol as protocol
import native_cc_campaign as campaign
from native_cc_trial_process import run_replay
from fix_loop_state import Stage1State, ProtocolError


class FrozenRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "sim"
        for relative in freeze.REQUIRED_FILES:
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# frozen\n")
        self.manifest_path = Path(self.tmp.name) / "manifest.json"
        self.manifest_path.write_text(json.dumps(freeze.build_manifest(self.repo)))
        self.case = dict(sim=str(self.repo), suite="suite", task="task", skill_library_dir="skill_library/libero",
                         require_runtime_freeze=True, runtime_manifest=str(self.manifest_path),
                         runtime_manifest_sha256=freeze.digest(self.manifest_path))

    def test_framework_edit_and_new_module_are_rejected_but_task_outputs_are_legal(self):
        output = self.repo / "outputs/libero_fix_loop/suite/task/notes.md"
        output.parent.mkdir(parents=True)
        output.write_text("model notes")
        self.assertTrue(freeze.verify_runtime(self.case, self.repo))
        source = self.repo / freeze.REQUIRED_FILES[0]
        source.write_text("# modified\n")
        with self.assertRaisesRegex(freeze.RuntimeChanged, "frozen runtime changed"):
            freeze.verify_runtime(self.case, self.repo)
        source.write_text("# frozen\n")
        (self.repo / "cap").mkdir(exist_ok=True)
        (self.repo / "cap/injected.py").write_text("# new module")
        with self.assertRaisesRegex(freeze.RuntimeChanged, "injected.py"):
            freeze.verify_runtime(self.case, self.repo)

    def test_missing_recovery_helper_prevents_preparation(self):
        (self.repo / "scripts/libero/native_cc_recover_trial.py").unlink()
        with self.assertRaisesRegex(freeze.RuntimeChanged, "missing runtime dependencies"):
            freeze.build_manifest(self.repo)

    def test_guard_resolves_paths_and_preserves_skill_permissions(self):
        def reason(name, values):
            return guard.denial(dict(tool_name=name, tool_input=values, cwd=str(self.repo)), self.case, self.repo)
        self.assertIsNone(reason("Write", dict(file_path="skill_library/libero/grasp.md")))
        self.assertTrue(reason("Edit", dict(file_path="cap/integrations/vision/sam3.py")))
        self.assertTrue(reason("Edit", dict(file_path="outputs/libero_fix_loop/suite/task/development_state.json")))
        self.assertTrue(reason("Write", dict(file_path="outputs/../../../escape.py")))
        self.assertTrue(reason("Bash", dict(command="for n in 51 52; do python scripts/libero/native_cc_protocol.py trial --seed $n; done")))
        self.assertIsNone(reason("Bash", dict(command=".venv-libero/bin/python3 scripts/libero/native_cc_protocol.py trial --phase initial --seed 51 --code outputs/initial_code.py")))
        self.assertIsNone(reason("Bash", dict(command="cp outputs/fix.py outputs/working_codes/fix.py && python scripts/libero/native_cc_protocol.py check")))

    def test_manifest_cannot_be_replaced_to_hide_framework_changes(self):
        self.manifest_path.write_text("{}")
        with self.assertRaisesRegex(freeze.RuntimeChanged, "manifest hash changed"):
            freeze.verify_runtime(self.case, self.repo)


class InterruptedTrialTests(unittest.TestCase):
    def test_signal_reaps_child_and_finishes_the_existing_ledger_slot_as_infrastructure_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            config = repo / "env_configs/libero/franka_libero_traced.yaml"
            config.parent.mkdir(parents=True)
            config.write_text("env: {}")
            case = dict(model="test", suite="suite", task="task", dev_seeds=[51], smoke_budget=3,
                        context_tokens=1000000, max_output_tokens=64000, effort="high",
                        cuda_visible_devices="7,0", egl_device_id=0)
            state = Stage1State(repo / "outputs/libero_fix_loop/suite/task", protocol.identity(case, repo))
            rec = state.begin_trial("snapshot", 51, "get_observation()")
            state.finish_trial(rec, result=dict(sandbox_rc=0), exit_code=0)
            code = state.task_dir / "initial_code.py"
            code.write_text("get_observation()")
            def interrupted_replay(_command, **kwargs):
                command = [sys.executable, "-c", "import os,signal,time; os.kill(os.getppid(), signal.SIGTERM); time.sleep(100)"]
                return run_replay(command, **kwargs)
            with patch.object(protocol, "run_replay", side_effect=interrupted_replay):
                rec = protocol.run_trial(case, repo, state, "smoke", 51, code)
            self.assertEqual(rec["status"], "infrastructure_error")
            self.assertIn("signal 15", rec["error"])
            self.assertEqual(len(state.data["trials"]), 2)
            lifecycle = json.loads((state.task_dir / rec["directory"] / "lifecycle.json").read_text())
            self.assertIsNotNone(lifecycle["process_exit_code"])
            with self.assertRaises(ProtocolError):
                state.begin_trial("smoke", 51, code.read_text())

    def test_zero_cli_exit_and_not_ready_text_do_not_make_development_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "cc.jsonl"
            transcript.write_text(json.dumps(dict(type="result", is_error=False, subtype="success",
                result="NATIVE_CC_STAGE1_READY: NOT READY; seed 64 is interrupted")))
            self.assertFalse(campaign.native_final_report(transcript)["is_error"])
            # Completion is derived from the ledger, never the substring above.
            with patch.object(campaign, "verify_runtime"), patch.object(campaign, "identity", return_value={}), \
                 patch.object(campaign, "Stage1State") as state:
                state.return_value.completion_errors.return_value = ["infrastructure_errors: seed_64"]
                state.return_value.progress.return_value = {"missing_initial_seeds": [65]}
                report = campaign.development_readiness(dict(suite="suite", task="task"), Path(tmp))
            self.assertFalse(report["ready"])
            self.assertIn("seed_64", report["errors"][0])


class ServiceClientTests(unittest.TestCase):
    def test_clients_use_injected_urls_at_construction_and_keep_original_defaults(self):
        import numpy as np
        from aspire.sim.cap.integrations.vision import sam3
        from aspire.sim.cap.integrations.motion import pyroki
        with patch.dict(os.environ, {"SAM3_SERVICE_URL": "http://127.0.0.1:8414/", "PYROKI_SERVICE_URL": "http://127.0.0.1:8416/"}), \
             patch.object(sam3, "post_with_retries", return_value={"results": []}) as segment, \
             patch.object(pyroki, "post_with_retries", return_value={"joint_positions": [0]*7, "waypoints": [[0]*7]}) as ik:
            client = sam3.init_sam3()
            solve = pyroki.init_pyroki()
            plan = pyroki.init_pyroki_trajopt()
            # Changed ambient variables cannot retarget a constructed client.
            os.environ["SAM3_SERVICE_URL"] = "http://127.0.0.1:1"
            client(np.zeros((2, 2, 3), dtype=np.uint8), "can")
            self.assertEqual(segment.call_args.args[0], "http://127.0.0.1:8414/segment")
            solve(np.zeros(7))
            self.assertEqual(ik.call_args.args[0], "http://127.0.0.1:8416/ik")
            plan(np.zeros(7), np.zeros(7))
            self.assertEqual(ik.call_args.args[0], "http://127.0.0.1:8416/plan")
            sam3.init_sam3(server_url="http://127.0.0.1:9014/")(np.zeros((2, 2, 3), dtype=np.uint8), "can")
            self.assertEqual(segment.call_args.args[0], "http://127.0.0.1:9014/segment")
        with patch.dict(os.environ, {}, clear=True), patch.object(pyroki, "post_with_retries", return_value={"joint_positions": [0]*7}) as ik:
            pyroki.init_pyroki()(np.zeros(7))
            self.assertEqual(ik.call_args.args[0], "http://127.0.0.1:8116/ik")


if __name__ == "__main__":
    unittest.main()
