"""Failure regressions: shared JIT caches, live failed engines, and DLC peers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

COMMON = Path(__file__).resolve().parents[1] / "scripts/common"
sys.path.insert(0, str(COMMON))
sys.path.insert(0, str(COMMON.parent / "libero"))
import native_cc_runtime as runtime
import native_cc_campaign as campaign


class RuntimeTests(unittest.TestCase):
    def test_early_campaign_failure_gets_a_fresh_durable_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            control = root / "control"
            control.mkdir()
            (control / "status.json").write_text('{"state":"previous"}')
            case = root / "case.json"
            case.write_text(json.dumps(dict(id="case", sim=str(root / "sim"), control=str(control),
                cuda_visible_devices="4,0", egl_device_id=0, claude_bin="missing", model_server_config="missing")))
            with patch.object(sys, "argv", ["campaign", "--case", str(case), "--attempt-id", "restart"]), patch("builtins.print"):
                self.assertEqual(campaign.main(), 1)
            previous = control / "attempts/restart/previous-status.json"
            self.assertEqual(previous.read_text(), '{"state":"previous"}')
            self.assertIn("missing preflight dependency", (control / "status.json").read_text())

    def test_preflight_failure_does_not_rewrite_campaign_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = root / "case.json"
            case.write_text(json.dumps(dict(id="case", sim=str(root / "sim"), control=str(root),
                cuda_visible_devices="4,0", egl_device_id=0, claude_bin="missing", model_server_config="missing")))
            (root / "status.json").write_text('{"state":"previous"}')
            with patch.object(sys, "argv", ["campaign", "--case", str(case), "--preflight"]), patch("builtins.print"):
                self.assertEqual(campaign.main(), 1)
            self.assertEqual((root / "status.json").read_text(), '{"state":"previous"}')
            self.assertFalse((root / "attempts").exists())

    def test_each_launch_uses_a_private_local_flashinfer_workspace(self):
        with patch.dict(os.environ, TMPDIR="/mnt/shared/cpfs"):
            first, second = runtime.isolated_jit_env("same-cell"), runtime.isolated_jit_env("same-cell")
        for value in (first, second):
            self.addCleanup(shutil.rmtree, value["FLASHINFER_WORKSPACE_BASE"])
            self.assertTrue(value["FLASHINFER_WORKSPACE_BASE"].startswith("/tmp/"))
            self.assertNotIn("HOME", value)
        self.assertNotEqual(first["FLASHINFER_WORKSPACE_BASE"], second["FLASHINFER_WORKSPACE_BASE"])

    def test_restart_keeps_original_logs_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "status.json").write_text('{"state":"blocked"}')
            (root / "logs").mkdir()
            old = root / "logs/model.log"
            old.write_text("old failure evidence")
            attempt = runtime.new_attempt(root, "authorized-resume")
            (attempt / "logs/model.log").write_text("new service")
            self.assertEqual(old.read_text(), "old failure evidence")
            self.assertEqual((attempt / "previous-status.json").read_text(), (root / "status.json").read_text())
            with self.assertRaises(FileExistsError):
                runtime.new_attempt(root, "authorized-resume")

    def test_dead_engine_is_detected_even_when_api_parent_is_alive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], start_new_session=True)
            try:
                log = root / "model.log"
                log.write_text("WARNING allocator OOM retry; retry succeeded\n")
                service = runtime.Service("qwen", process, log, "unused")
                service.check()  # A recoverable allocator warning is not fatal.
                with log.open("a") as stream:
                    stream.write("WorkerProc hit an exception\nRuntimeError: sampling.so: file too short\n")
                self.assertIsNone(process.poll())
                with self.assertRaises(runtime.ServiceFailure) as error:
                    service.check()
                self.assertEqual(error.exception.details["service"], "qwen")
                self.assertEqual(error.exception.details["reason"], "fatal_worker_error")
                self.assertEqual(error.exception.details["log"], str(log))
            finally:
                process.terminate()
                process.wait()

    def test_watchdog_cleans_up_on_fatal_worker_error(self):
        service = Mock()
        service.check.side_effect = runtime.ServiceFailure(dict(service="qwen", reason="fatal_worker_error"))
        cleanup = Mock()
        watch = runtime.ServiceWatch([service], cleanup, interval=0.01)
        watch.start()
        watch.thread.join(timeout=2)
        watch.close()
        cleanup.assert_called_once()
        with self.assertRaises(runtime.ServiceFailure):
            watch.check()

    def test_timeout_names_pending_service(self):
        service = Mock(name="service")
        service.name, service.log = "sam3", Path("sam3.log")
        service.ready.return_value = False
        watch = runtime.ServiceWatch([service], lambda: None)
        with self.assertRaises(runtime.ServiceFailure) as error:
            watch.wait_ready(timeout=0)
        self.assertEqual(error.exception.details["pending_services"], ["sam3"])


class GroupTests(unittest.TestCase):
    def test_one_failed_cell_waits_until_healthy_peer_finishes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            members = []
            for i in range(2):
                case = root / f"cell{i}"
                case.mkdir()
                (case / "case.json").write_text("{}")
                entry = case / "entry.sh"
                entry.write_text("exit 1\n" if i == 0 else 'sleep 1\nprintf complete > "' + str(case / "finished") + '"\n')
                members.append(dict(id=f"cell{i}", case=str(case / "case.json"), entry=str(entry)))
            config = root / "group.json"
            config.write_text(json.dumps(dict(members=members, state_dir=str(root / "state"),
                attempt_id="test", poll_seconds=0.02, timeout_seconds=5, registration_timeout=3)))
            children = [subprocess.Popen([sys.executable, str(COMMON / "native_cc_group.py"),
                         "--config", str(config), "--rank", str(rank)], stdout=subprocess.DEVNULL) for rank in range(2)]
            try:
                deadline = time.monotonic() + 2
                while not (root / "state/result-0.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((root / "state/result-0.json").exists())
                self.assertIsNone(children[0].poll(), "failed case must not exit its DLC worker early")
                for child in children:
                    self.assertEqual(child.wait(timeout=5), 1)
                self.assertEqual((root / "cell1/finished").read_text(), "complete")
                final = json.loads((root / "state/group-result-0.json").read_text())
                self.assertEqual([r["case"] for r in final["failed_cells"]], ["cell0"])
            finally:
                for child in children:
                    if child.poll() is None:
                        child.terminate()
                    child.wait(timeout=5)

    def test_missing_peer_is_bounded_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            entry = root / "success.sh"
            entry.write_text("exit 0\n")
            case = root / "case.json"
            case.write_text("{}")
            config = root / "group.json"
            config.write_text(json.dumps(dict(members=[dict(id=f"cell{i}", case=str(case), entry=str(entry)) for i in range(2)],
                state_dir=str(root / "state"), attempt_id="test", poll_seconds=0.01, timeout_seconds=2,
                registration_timeout=0.1)))
            result = subprocess.run([sys.executable, str(COMMON / "native_cc_group.py"),
                "--config", str(config), "--rank", "0"], capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 1)
            self.assertIn("did not register", (root / "state/group-error-0.json").read_text())


if __name__ == "__main__":
    unittest.main()
