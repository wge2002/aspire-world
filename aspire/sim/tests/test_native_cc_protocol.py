"""Offline checks for native CC's evidence boundary; no inference or simulator."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/libero"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/common"))
import native_cc_protocol as native
import native_cc_compat as compatibility
import run_fix_loop_validation as validation
from fix_loop_state import Stage1State, ProtocolError


class NativeProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "aspire/sim"
        self.task = self.repo / "outputs/libero_fix_loop/suite/task"
        self.task.mkdir(parents=True)
        config = self.repo / "env_configs/libero/franka_libero_traced.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("env: {}\n")
        self.case = dict(model="experimental-model", suite="suite", task="task", dev_seeds=list(range(51, 66)),
                         smoke_budget=3, context_tokens=32768, max_output_tokens=4096, effort="max",
                         cuda_visible_devices="4,0", egl_device_id=0)
        self.state = Stage1State(self.task, native.identity(self.case, self.repo))

    def record(self, phase, seed=51):
        r = self.state.begin_trial(phase, seed, "get_observation()\n")
        self.state.finish_trial(r, result=dict(sandbox_rc=0, reward=1.0, task_completed=1, trial_dir=r["directory"]), exit_code=0)

    def test_native_subagent_model_provenance_gates_completion(self):
        self.record("snapshot")
        self.record("smoke")
        for seed in range(51, 66):
            self.record("initial", seed)
        for name in ("task_analysis.md", "fix_code.py"):
            (self.task / name).write_text("get_observation()\n")
        (self.task / "findings.md").write_text("## Root causes observed\nnone\n## What fixed them\nnone\n## Generalizable patterns\nnone\n## Blocked seeds\nnone\n")
        working = self.repo / "outputs/working_codes/suite_task_fix.py"
        working.parent.mkdir(parents=True)
        working.write_text("get_observation()\n")
        parent, child = self.task / "parent.jsonl", self.task / "child.jsonl"
        parent.write_text(json.dumps({"type": "assistant", "message": {"model": "experimental-model"}}))
        child.write_text(json.dumps({"type": "assistant", "message": {"model": "wrong-model"}}))
        with self.assertRaisesRegex(ProtocolError, "provenance mismatch"):
            native.finalize(self.case, self.repo, self.state, [parent, child])
        self.assertFalse(self.state.data["stage1_complete"])
        child.write_text(parent.read_text())
        result = native.finalize(self.case, self.repo, self.state, [parent, child])
        self.assertTrue(result["stage1_complete"])
        self.assertEqual(result["initial_passes"], 15)

    def test_ports_mapping_and_credential_filter(self):
        env = native.runtime_env(self.case | {"sam3_url": "http://127.0.0.1:9114"}, self.repo,
                                 {"ANTHROPIC_API_KEY": "secret-fixture", "ALIBABA_CLOUD_CREDENTIALS_URI": "secret-fixture", "USER": "test"})
        self.assertEqual(env["SAM3_SERVICE_URL"], "http://127.0.0.1:9114")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "4,0")
        self.assertEqual(env["MUJOCO_EGL_DEVICE_ID"], "0")
        self.assertFalse(any("secret-fixture" == value for value in env.values()))

    def test_launcher_overrides_the_ten_minute_background_exit_cap(self):
        executable = self.repo / "fake-claude"
        executable.write_text(f"#!{sys.executable}\nimport os\nprint(os.environ['CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS'])\n")
        executable.chmod(0o700)
        launcher = Path(__file__).resolve().parents[1] / "scripts/common/claude_with_local_model.sh"
        env = os.environ | dict(CC_LOCAL_CONFIG_DIR=str(self.repo / "cc"), CC_LOCAL_CONTEXT_TOKENS="65536",
                                CC_LOCAL_MAX_OUTPUT_TOKENS="4096", CC_LOCAL_CLAUDE_BIN=str(executable),
                                CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS="600000")
        result = subprocess.run(["bash", str(launcher), "http://127.0.0.1:8120", "experimental-model", "-p", "fixture"],
                                env=env, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "43200000")

    def test_compatibility_watchdog_honors_the_case_budget(self):
        for budget in (900, 3600):
            with self.subTest(budget=budget):
                case_file = self.repo / f"compat-case-{budget}.json"
                case_file.write_text(json.dumps(self.case | {
                    "endpoint": "http://127.0.0.1:8121", "claude_bin": "unused",
                    "compat_timeout_seconds": budget,
                }))
                output = self.repo / f"compat-{budget}"
                def timeout_native_cc(command, **kwargs):
                    kwargs["stdout_path"].write_text("")
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])

                with patch.object(sys, "argv", ["fixture", "--case", str(case_file), "--output", str(output)]), \
                     patch.object(compatibility.urllib.request, "urlopen", return_value=io.BytesIO(b"{}")), \
                     patch.object(compatibility, "run_native_cc", side_effect=timeout_native_cc) as run_cc, \
                     patch("builtins.print"):
                    self.assertEqual(compatibility.main(), 1)
                self.assertEqual(run_cc.call_args.kwargs["timeout"], budget)
                summary = json.loads((output / "summary.json").read_text())
                self.assertTrue(summary["timed_out"])
                self.assertEqual(summary["timeout_seconds"], budget)
                self.assertFalse(summary["passed"])

    def test_nonzero_replay_exit_cannot_be_heldout_completion(self):
        fix = self.task / "fix_code.py"
        fix.write_text("get_observation()\n")
        args = argparse.Namespace(suite="suite", task="task", gpu="4", cuda_visible_devices="4,0", egl_device_id=0,
                                  fix_code=fix, config=self.repo / "env_configs/libero/franka_libero_traced.yaml",
                                  seeds=[1], output_dir=self.repo / "outputs/eval", resume=False, trial_timeout=900)
        process = Mock(returncode=1)
        artifact = dict(seed=1, task_completed=1, sandbox_rc=0, reward=1.0, trial_dir="partial-write")
        with patch.object(validation, "ROOT", self.repo), patch.object(validation, "parse_args", return_value=args), \
             patch.object(validation, "check_model_stage1"), patch.object(validation, "git_commit", return_value="fixture"), \
             patch.object(validation, "latest_trial", return_value=artifact), patch.object(validation.os, "chdir"), \
             patch.object(validation.subprocess, "Popen", return_value=process), patch.object(validation.subprocess, "run"), patch("builtins.print"):
            self.assertEqual(validation.main(), 1)
        manifest = json.loads(next(args.output_dir.rglob("manifest.json")).read_text())
        self.assertEqual(manifest["trials"], 0)
        self.assertEqual(manifest["status"], "partial")


if __name__ == "__main__":
    unittest.main()
