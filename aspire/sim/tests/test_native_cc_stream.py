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
        # A quiet second end-turn makes the session a candidate for shutdown,
        # but not `finished`: only a hard signal ends a session outright.
        flow.accept(self.result)
        self.assertTrue(flow.idle)
        self.assertFalse(flow.finished)

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


class EndTurnIsNotSessionEndTests(unittest.TestCase):
    """A native `result` ends one turn; only a hard signal ends the session."""

    started = {"type": "system", "subtype": "task_started", "task_id": "child"}
    done = {"type": "system", "subtype": "task_notification", "task_id": "child",
            "uuid": "completion-1", "status": "completed", "summary": "Child finished"}
    result = {"type": "result", "is_error": False}

    def test_end_turn_with_pending_background_work_is_not_terminal(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        flow.accept(self.result)
        # The child is still running, so stdin must stay open for its report.
        self.assertFalse(flow.finished)
        self.assertFalse(flow.idle)

    def test_quiet_end_turn_is_idle_but_never_finished(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.result)
        self.assertTrue(flow.idle)
        self.assertFalse(flow.finished)

    def test_later_turn_revokes_idle(self):
        # The bowl_C1 shape: end-turn, then the same session starts new work.
        flow = MODULE.NotificationFlow()
        flow.accept(self.result)
        self.assertTrue(flow.idle)
        flow.accept({"type": "system", "subtype": "init"})
        self.assertTrue(flow.busy)
        self.assertFalse(flow.idle)
        flow.accept(self.started)
        self.assertFalse(flow.idle)
        self.assertFalse(flow.finished)
        self.assertEqual(flow.accept(self.done), [])
        self.assertEqual(flow.accept(self.result), [self.done])

    def test_delivering_a_notification_revokes_idle(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.result)
        self.assertTrue(flow.idle)
        self.assertEqual(flow.accept(self.done), [self.done])
        self.assertFalse(flow.idle)

    def test_hard_error_still_ends_the_session(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        flow.accept(self.result | {"is_error": True})
        self.assertTrue(flow.finished)
        self.assertFalse(flow.idle)


EMIT = """import json, sys, time
def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()
sys.stdin.readline()
"""

STARTED = {"type": "system", "subtype": "task_started", "task_id": "child"}
NOTIFICATION = {"type": "system", "subtype": "task_notification", "task_id": "child",
                "uuid": "u1", "status": "completed", "summary": "done"}
END_TURN = {"type": "result", "is_error": False}


class TransportCloseOrderingTests(unittest.TestCase):
    """CPU-only fixtures. No Claude Code binary, model request or simulator."""

    def run_fixture(self, body: str, **kwargs):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "session_fixture.py"
            fixture.write_text(EMIT + body)
            delivered = root / "delivered.txt"
            options = {"first_wait": 0, "timeout": 60, "idle_grace": 1.0} | kwargs
            code = MODULE.run_native_cc(
                [sys.executable, str(fixture), str(delivered), "-p", "prompt"],
                cwd=root, env=dict(os.environ), stdout_path=root / "stdout.jsonl",
                stderr_path=root / "stderr.log", **options)
            return code, (delivered.read_text() if delivered.exists() else "")

    def test_pending_background_work_keeps_stdin_open_past_end_turn(self):
        # The bowl_C1 failure: end-turn while a child still runs, then the
        # child's notification arrives and must reach the same live session.
        code, delivered = self.run_fixture(f"""
emit({STARTED!r})
emit({END_TURN!r})
time.sleep(3.0)
emit({NOTIFICATION!r})
open(sys.argv[1], "w").write(sys.stdin.readline())
emit({END_TURN!r})
sys.stdin.readline()
""")
        self.assertEqual(code, 0)
        self.assertIn("child", delivered)
        self.assertIn("done", delivered)

    def test_new_parent_turn_can_think_past_idle_grace_before_spawning(self):
        # Real tail ordering: result -> init -> silent parent work -> task.
        code, delivered = self.run_fixture(f"""
emit({END_TURN!r})
emit({dict(type="system", subtype="init")!r})
time.sleep(0.3)
emit({STARTED!r})
emit({END_TURN!r})
emit({NOTIFICATION!r})
open(sys.argv[1], "w").write(sys.stdin.readline())
emit({END_TURN!r})
sys.stdin.readline()
""", idle_grace=0.05)
        self.assertEqual(code, 0)
        self.assertIn("child", delivered)

    def test_genuinely_finished_session_still_exits(self):
        # No background work outstanding: the grace period expires, stdin is
        # closed, and the session exits instead of hanging until the timeout.
        code, _ = self.run_fixture(f"""
emit({END_TURN!r})
sys.stdin.read()
""")
        self.assertEqual(code, 0)

    def test_error_result_closes_input_without_waiting(self):
        code, _ = self.run_fixture(f"""
emit({STARTED!r})
emit({dict(END_TURN, is_error=True)!r})
sys.stdin.read()
""", idle_grace=600.0)
        self.assertEqual(code, 0)

    def test_late_notification_after_exit_is_not_a_write_to_closed_stdin(self):
        # The session ends while a notification is still in flight. The write
        # must fail softly rather than raising on a closed stream, and the lost
        # completion must not be reported as a clean success.
        with self.assertRaises(RuntimeError) as caught:
            self.run_fixture(f"""
emit({STARTED!r})
emit({END_TURN!r})
import os
os.close(sys.stdin.fileno())
emit({NOTIFICATION!r})
sys.stdout.close()
""")
        message = str(caught.exception)
        self.assertIn("not delivered", message)
        self.assertIn("child", message)
        self.assertNotIn("closed file", message)

    def test_nonzero_exit_is_preserved_as_a_real_failure(self):
        code, _ = self.run_fixture(f"""
emit({END_TURN!r})
sys.exit(7)
""")
        self.assertEqual(code, 7)


FIXTURE = (Path(__file__).parents[1] / "docs/experiments/code-world-pipeline-repair-20261005"
           / "NATIVE_EVENT_FIXTURE.json")
COMPACT = {"type": "system", "subtype": "compact_boundary", "session_id": "s",
           "compact_metadata": {"trigger": "auto"}}


def recorded(name):
    import json
    return [row["event"] for row in json.loads(FIXTURE.read_text())[name]["events"]]


class ParentCompactionDeliveryTests(unittest.TestCase):
    """busy -> completion pending -> parent compact_boundary, before any result."""

    started = {"type": "system", "subtype": "task_started", "task_id": "child", "session_id": "s"}
    done = {"type": "system", "subtype": "task_notification", "task_id": "child",
            "uuid": "completion-1", "status": "completed", "summary": "Child finished",
            "session_id": "s"}
    result = {"type": "result", "is_error": False}

    def test_live_before_r3_completion_is_delivered_at_first_parent_compaction(self):
        # Exact events from live-before-r3 (lines 159, 624, 669, 795, 2030).
        started, done, compact_1, compact_2, final = recorded("live_before")
        flow = MODULE.NotificationFlow()
        self.assertEqual(flow.accept(started), [])
        self.assertEqual(flow.accept(done), [])
        self.assertTrue(flow.busy)
        self.assertEqual(flow.accept(compact_1), [done])
        self.assertIn("BEGIN_FINDINGS_MD", done["summary"])
        self.assertIn("END_FINDINGS_MD", done["summary"])
        # Delivered once: the second compaction and the final result send nothing.
        self.assertTrue(flow.busy)
        self.assertFalse(flow.idle)
        self.assertEqual(flow.accept(compact_2), [])
        self.assertEqual(flow.accept(final), [])
        self.assertEqual(flow.pending, [])
        self.assertEqual(flow.undelivered, [])

    def test_old_bowl_completion_is_delivered_before_the_resume(self):
        events = recorded("old_bowl")
        started, done, compact_1, compact_2 = events[:4]
        flow = MODULE.NotificationFlow()
        flow.accept(started)
        self.assertEqual(flow.accept(done), [])
        self.assertEqual(flow.accept(compact_1), [done])
        self.assertEqual(flow.accept(compact_2), [])
        self.assertTrue(flow.drained)

    def test_completion_is_not_redelivered_at_the_next_result(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        flow.accept(self.done)
        self.assertEqual(flow.accept(COMPACT), [self.done])
        self.assertEqual(flow.accept(self.result), [])
        self.assertEqual(flow.accept(self.done), [])
        self.assertTrue(flow.idle)
        self.assertFalse(flow.finished)

    def test_compaction_without_pending_work_changes_nothing(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        self.assertEqual(flow.accept(COMPACT), [])
        self.assertTrue(flow.busy)
        self.assertEqual(flow.active, {"child"})
        self.assertFalse(flow.idle)

    def test_child_compaction_does_not_release_parent_input(self):
        flow = MODULE.NotificationFlow()
        flow.accept({"type": "system", "subtype": "init", "session_id": "s"})
        flow.accept(self.started)
        flow.accept(self.done)
        self.assertEqual(flow.accept(COMPACT | {"parent_tool_use_id": "agent-tool"}), [])
        self.assertEqual(flow.accept(COMPACT | {"session_id": "child-session"}), [])
        self.assertEqual(flow.pending, [self.done])
        self.assertEqual(flow.accept(COMPACT), [self.done])

    def test_resumed_task_supersedes_its_undelivered_stale_completion(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        flow.accept(self.done)
        flow.accept(self.started | {"tool_use_id": "resume"})
        self.assertEqual(flow.pending, [])
        self.assertEqual(flow.superseded, [self.done])
        self.assertFalse(flow.drained)
        fresh = self.done | {"uuid": "completion-2", "summary": "Resumed run finished"}
        self.assertEqual(flow.accept(fresh), [])
        self.assertEqual(flow.accept(COMPACT), [fresh])
        self.assertTrue(flow.drained)

    def test_compaction_after_hard_error_does_not_write(self):
        flow = MODULE.NotificationFlow()
        flow.accept(self.started)
        flow.accept(self.result | {"is_error": True})
        flow.accept(self.done)
        self.assertEqual(flow.accept(COMPACT), [])

    def test_compaction_delivery_does_not_satisfy_terminal_evidence(self):
        flow = MODULE.NotificationFlow(terminal_evidence=lambda e: e.get("verified") is True)
        flow.accept(self.started)
        flow.accept(self.done)
        flow.accept(COMPACT)
        self.assertFalse(flow.evidence)
        self.assertFalse(flow.complete)
        flow.accept(self.result)
        self.assertFalse(flow.complete)


class ParentCompactionLifecycleTests(unittest.TestCase):
    """Fake CLI: the parent never ends its turn until it receives the completion."""

    run_fixture = TransportCloseOrderingTests.run_fixture

    def test_completion_reaches_a_turn_that_compacts_without_ending(self):
        code, delivered = self.run_fixture(f"""
emit({ParentCompactionDeliveryTests.started!r})
emit({ParentCompactionDeliveryTests.done!r})
emit({COMPACT!r})
line = sys.stdin.readline()
open(sys.argv[1], "w").write(line)
emit({COMPACT!r})
emit({END_TURN!r})
rest = sys.stdin.read()
open(sys.argv[1], "a").write("REST:" + rest)
""", timeout=30)
        self.assertEqual(code, 0)
        first, rest = delivered.split("REST:")
        import json
        content = json.loads(first)["message"]["content"]
        self.assertIn("Child finished", content)
        self.assertIn('"task_id": "child"', content)
        # No second copy at the later compaction or end-turn; stdin then closes.
        self.assertEqual(rest, "")

    def test_without_compaction_a_stuck_turn_still_times_out(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fixture(f"""
emit({ParentCompactionDeliveryTests.started!r})
emit({ParentCompactionDeliveryTests.done!r})
time.sleep(30)
""", timeout=2)


if __name__ == "__main__":
    unittest.main()
