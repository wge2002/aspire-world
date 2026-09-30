import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "native_cc_stream", Path(__file__).parents[1] / "scripts/common/native_cc_stream.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NativeNotificationTests(unittest.TestCase):
    started = {"type": "system", "subtype": "task_started", "task_id": "child"}
    done = {"type": "system", "subtype": "task_notification", "task_id": "child",
            "uuid": "completion-1", "status": "completed", "summary": "Child finished"}
    result = {"type": "result", "is_error": False}

    def test_child_finishes_while_parent_is_busy(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        self.assertEqual(flow.accept(self.done), [])
        self.assertEqual(flow.accept(self.result), [self.done])
        self.assertFalse(flow.finished)
        flow.accept(self.result)
        self.assertTrue(flow.finished)

    def test_child_finishes_after_parent_waits(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        self.assertEqual(flow.accept(self.result), [])
        self.assertFalse(flow.finished)
        self.assertEqual(flow.accept(self.done), [self.done])
        self.assertFalse(flow.finished)

    def test_duplicate_notification_is_not_delivered_twice(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        flow.accept(self.done)
        flow.accept(self.done)
        self.assertEqual(flow.accept(self.result), [self.done])
        self.assertEqual(flow.accept(self.done), [])

    def test_forwarded_subagent_result_is_not_parent_completion(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.result | {"parent_tool_use_id": "agent-tool"})
        self.assertFalse(flow.finished)
        self.assertTrue(flow.busy)

    def test_model_error_ends_transport(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        flow.accept(self.result | {"is_error": True})
        self.assertTrue(flow.finished)

    def test_single_prompt_becomes_one_stream_input(self):
        command, prompt = MODULE.stream_command(["claude", "-p", "Keep exact\ntext", "--verbose"])
        self.assertEqual(prompt, "Keep exact\ntext")
        self.assertEqual(command, ["claude", "-p", "--verbose", "--input-format", "stream-json"])

    def test_watchdog_stops_the_owned_process(self):
        # This executable is a local fixture, not Claude Code; no model call or
        # CC first-inspection wait is involved.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "waiting_fixture.py"
            executable.write_text("import sys,time\nsys.stdin.readline()\ntime.sleep(30)\n")
            owned = []
            with self.assertRaises(subprocess.TimeoutExpired):
                MODULE.run_native_cc([sys.executable, str(executable), "-p", "fixture"],
                    cwd=root, env=dict(os.environ), stdout_path=root / "stdout.jsonl",
                    stderr_path=root / "stderr.log", timeout=0, first_wait=0, on_start=owned.append)
            self.assertEqual(len(owned), 1)
            self.assertIsNotNone(owned[0].poll())
            self.assertTrue(owned[0].stdin.closed)
            self.assertTrue(owned[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
