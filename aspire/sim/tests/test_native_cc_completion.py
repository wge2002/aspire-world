"""Completion contract for the native CC transport and the R3 compat fixture.

Everything here is CPU-only: a small fake CLI stands in for Claude Code, and no
model request, simulator, GPU or DLC job is involved. The event orderings
replayed below are the ones actually recorded for bowl_C and drawer_C on
2026-09-30, where a session that had already produced and verified every
required artifact was SIGTERMed at the 1800 s deadline and reported as exit 124.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SIM = Path(__file__).parents[1]
SHARED = SIM / "scripts/common/native_cc_stream.py"
R3 = SIM / "docs/experiments/code-world-qwen-foundation-r3-20261002/support"
R2 = SIM / "docs/experiments/code-world-qwen-foundation-r2-20260930/support"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load("native_cc_stream_r3", SHARED)
FIXTURE = load("qwen_native_compat_r3", R3 / "qwen-native-compat-r2.py")

STARTED = {"type": "system", "subtype": "task_started", "task_id": "child"}
NOTIFICATION = {"type": "system", "subtype": "task_notification", "task_id": "child",
                "uuid": "u1", "status": "completed", "summary": "colors.json\nBEGIN_FINDINGS_MD"}
FAILED = {"type": "system", "subtype": "task_notification", "task_id": "child",
          "uuid": "u2", "status": "failed", "summary": "subagent failed"}
END_TURN = {"type": "result", "is_error": False}


def verifier_call(call_id: str = "call-1", command: str = FIXTURE.VERIFIER_COMMAND, **extra) -> dict:
    """The assistant event that issues the fixture's verifier, as CC emits it."""
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call_id, "name": "Bash",
         "input": {"command": command, "description": "Run the result verifier"}}]}} | extra


def verifier_output(call_id: str = "call-1", text: str = FIXTURE.VERIFIER_SENTINEL, **extra) -> dict:
    """The matching tool_result, in the shape recorded for both R2 cells."""
    return {"type": "user", "message": {"role": "user", "content": [
        {"tool_use_id": call_id, "type": "tool_result", "content": text, "is_error": False}]}} | extra


class CompletionContractTests(unittest.TestCase):
    """`NotificationFlow` alone: when is a session allowed to be shut down."""

    def flow(self):
        return MODULE.NotificationFlow(FIXTURE.verifier_evidence())

    def test_without_a_predicate_nothing_is_ever_complete(self):
        # The solver path passes no predicate and must behave exactly as before.
        flow = MODULE.NotificationFlow()
        for event in (verifier_call(), verifier_output(), END_TURN):
            flow.accept(event)
        self.assertFalse(flow.evidence)
        self.assertFalse(flow.complete)
        self.assertTrue(flow.idle)

    def test_verified_quiet_session_is_complete(self):
        flow = self.flow()
        flow.accept(verifier_call())
        flow.accept(verifier_output())
        self.assertTrue(flow.evidence)
        self.assertFalse(flow.complete, "a turn is still in flight")
        flow.accept(END_TURN)
        self.assertTrue(flow.complete)

    def test_evidence_does_not_complete_a_session_with_a_live_worker(self):
        # The background subagent is still running: stdin must stay open for it.
        flow = self.flow()
        flow.accept(STARTED)
        flow.accept(verifier_call())
        flow.accept(verifier_output())
        flow.accept(END_TURN)
        self.assertTrue(flow.evidence)
        self.assertFalse(flow.drained)
        self.assertFalse(flow.complete)
        self.assertEqual(flow.accept(NOTIFICATION), [NOTIFICATION])
        self.assertTrue(flow.drained)

    def test_evidence_does_not_complete_a_session_with_an_undelivered_report(self):
        flow = self.flow()
        flow.accept(verifier_call())
        flow.accept(verifier_output())
        flow.accept(END_TURN)
        flow.undelivered.append({"task_id": "child"})
        self.assertFalse(flow.drained)
        self.assertFalse(flow.complete)

    def test_a_buffered_notification_defers_completion_until_it_is_handed_over(self):
        flow = self.flow()
        flow.accept(STARTED)
        flow.accept(NOTIFICATION)
        flow.accept(verifier_call())
        flow.accept(verifier_output())
        self.assertEqual(flow.accept(END_TURN), [NOTIFICATION])
        self.assertTrue(flow.busy, "delivering a notification opens a new turn")
        self.assertFalse(flow.complete)
        flow.accept(END_TURN)
        self.assertTrue(flow.complete)

    def test_evidence_latches_across_later_events(self):
        flow = self.flow()
        flow.accept(verifier_call())
        flow.accept(verifier_output())
        flow.accept({"type": "system", "subtype": "init"})
        flow.accept(STARTED)
        self.assertTrue(flow.evidence)
        self.assertFalse(flow.complete)

    def test_a_failed_background_task_is_still_reported(self):
        flow = self.flow()
        flow.accept(STARTED)
        self.assertEqual(flow.accept(FAILED), [])
        self.assertEqual(flow.accept(END_TURN), [FAILED])
        self.assertFalse(flow.evidence)

    def test_duplicate_and_late_notifications_are_delivered_once(self):
        flow = self.flow()
        flow.accept(STARTED)
        flow.accept(NOTIFICATION)
        flow.accept(dict(NOTIFICATION))
        self.assertEqual(flow.accept(END_TURN), [NOTIFICATION])
        self.assertEqual(flow.accept(dict(NOTIFICATION)), [])


class VerifierEvidenceTests(unittest.TestCase):
    """What the R3 fixture will and will not accept as proof of completion."""

    def accepts(self, *events) -> bool:
        accepted = FIXTURE.verifier_evidence()
        return any(accepted(event) for event in events)

    def test_recorded_bowl_and_drawer_shape_is_accepted(self):
        # Verbatim shape of cc.stdout.jsonl 3224/3225 (bowl_C) and 4578/4579
        # (drawer_C): the only two Bash calls either session made.
        for call_id in ("chatcmpl-tool-ae981a64808d83ff", "chatcmpl-tool-a9639b3266ede4c3"):
            with self.subTest(call_id=call_id):
                self.assertTrue(self.accepts(
                    verifier_call(call_id, parent_tool_use_id=None),
                    verifier_output(call_id, parent_tool_use_id=None)))

    def test_block_list_content_shape_is_accepted(self):
        self.assertTrue(self.accepts(verifier_call(), verifier_output(
            text=[{"type": "text", "text": FIXTURE.VERIFIER_SENTINEL + "\n"}])))

    def test_a_result_without_its_call_is_refused(self):
        self.assertFalse(self.accepts(verifier_output("never-issued")))

    def test_a_different_bash_command_is_refused(self):
        self.assertFalse(self.accepts(
            verifier_call(command="echo NATIVE_CC_COMPAT_OK"), verifier_output()))

    def test_a_failed_verifier_is_refused(self):
        failed = verifier_output()
        failed["message"]["content"][0].update(is_error=True, content="AssertionError")
        self.assertFalse(self.accepts(verifier_call(), failed))

    def test_a_verifier_that_did_not_print_the_sentinel_is_refused(self):
        self.assertFalse(self.accepts(verifier_call(), verifier_output(text="")))

    def test_the_coordinator_merely_saying_the_sentinel_is_refused(self):
        self.assertFalse(self.accepts({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Verification passed. " + FIXTURE.VERIFIER_SENTINEL}]}}))

    def test_a_subagent_cannot_supply_the_evidence(self):
        self.assertFalse(self.accepts(
            verifier_call(parent_tool_use_id="agent-tool"),
            verifier_output(parent_tool_use_id="agent-tool")))


EMIT = """import json, sys, time
def emit(o):
    sys.stdout.write(json.dumps(o) + "\\n")
    sys.stdout.flush()
sys.stdin.readline()
"""


class TransportLifecycleTests(unittest.TestCase):
    """Process stdin/exit lifecycle against a fake CLI. No Claude Code binary."""

    def run_fixture(self, body: str, **kwargs):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = root / "fake_cli.py"
            fake.write_text(EMIT + body)
            delivered = root / "delivered.txt"
            outcome: dict = {}
            options = {"first_wait": 0, "timeout": 60, "idle_grace": 600.0,
                       "shutdown_grace": 60.0, "terminal_evidence": FIXTURE.verifier_evidence(),
                       "outcome": outcome} | kwargs
            started = time.monotonic()
            try:
                code = MODULE.run_native_cc(
                    [sys.executable, str(fake), str(delivered), "-p", "prompt"],
                    cwd=root, env=dict(os.environ), stdout_path=root / "stdout.jsonl",
                    stderr_path=root / "stderr.log", **options)
            finally:
                self.elapsed = time.monotonic() - started
                self.outcome = outcome
                self.delivered = delivered.read_text() if delivered.exists() else ""
            return code

    def test_bowl_shape_exits_promptly_instead_of_waiting_out_the_grace(self):
        # bowl_C: the subagent reported mid-turn, the verifier passed, the turn
        # ended, the transport handed the notification over, and a second turn
        # ended cleanly 61 s later. R2 then sat in a 90 s idle grace until the
        # 1800 s deadline killed a session that was already done. `idle_grace`
        # is 600 s here, so only the completion contract can finish this test.
        code = self.run_fixture(f"""
emit({STARTED!r})
emit({NOTIFICATION!r})
emit({verifier_call()!r})
emit({verifier_output()!r})
emit({END_TURN!r})
open(sys.argv[1], "w").write(sys.stdin.readline())
emit({END_TURN!r})
sys.stdin.read()
""", timeout=30)
        self.assertEqual(code, 0)
        self.assertIn("child", self.delivered)
        self.assertLess(self.elapsed, 20, "stdin was not closed on the completion contract")
        self.assertTrue(self.outcome["terminal_evidence"])
        self.assertFalse(self.outcome["completed_at_deadline"])
        self.assertEqual(self.outcome["undelivered"], [])

    def test_drawer_shape_at_the_deadline_is_a_completed_run_not_a_timeout(self):
        # drawer_C: the first turn ended without finishing, the transported
        # notification restarted the coordinator, and the verifier passed four
        # seconds before the deadline. R2 SIGTERMed it mid-reply, which the
        # transcript records as "[Request interrupted by user]" — a kill, not a
        # human. The turn is still in flight when the deadline arrives here.
        code = self.run_fixture(f"""
emit({STARTED!r})
emit({NOTIFICATION!r})
emit({END_TURN!r})
open(sys.argv[1], "w").write(sys.stdin.readline())
emit({verifier_call()!r})
emit({verifier_output()!r})
time.sleep(2.0)
emit({END_TURN!r})
sys.stdin.read()
""", timeout=1)
        self.assertEqual(code, 0)
        self.assertTrue(self.outcome["completed_at_deadline"])
        self.assertTrue(self.outcome["terminal_evidence"])

    def test_an_unverified_session_still_times_out_at_the_deadline(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fixture(f"""
emit({STARTED!r})
emit({END_TURN!r})
time.sleep(30)
""", timeout=1, shutdown_grace=30.0)
        self.assertFalse(self.outcome["terminal_evidence"])
        self.assertLess(self.elapsed, 25, "a genuine timeout was granted a shutdown window")

    def test_a_verified_session_with_a_live_worker_still_times_out(self):
        # Evidence alone must not buy teardown time: the subagent has not
        # reported, so work really is outstanding and this is a real timeout.
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fixture(f"""
emit({STARTED!r})
emit({verifier_call()!r})
emit({verifier_output()!r})
emit({END_TURN!r})
time.sleep(30)
""", timeout=1, shutdown_grace=30.0)
        self.assertTrue(self.outcome["terminal_evidence"])
        self.assertLess(self.elapsed, 25)

    def test_a_live_worker_keeps_stdin_open_after_verification(self):
        # The same ordering, but the subagent does report: the session must not
        # have been shut down early, and the notification must reach it.
        code = self.run_fixture(f"""
emit({STARTED!r})
emit({verifier_call()!r})
emit({verifier_output()!r})
emit({END_TURN!r})
time.sleep(2.0)
emit({NOTIFICATION!r})
open(sys.argv[1], "w").write(sys.stdin.readline())
emit({END_TURN!r})
sys.stdin.read()
""", timeout=30)
        self.assertEqual(code, 0)
        self.assertIn("child", self.delivered)
        self.assertEqual(self.outcome["undelivered"], [])

    def test_a_verified_session_that_loses_input_is_not_a_clean_success(self):
        # A completion the session never saw is still missing work, even with
        # the verifier's evidence already in hand.
        with self.assertRaises(RuntimeError) as caught:
            self.run_fixture(f"""
emit({STARTED!r})
emit({verifier_call()!r})
emit({verifier_output()!r})
emit({END_TURN!r})
import os
os.close(sys.stdin.fileno())
emit({NOTIFICATION!r})
sys.stdout.close()
""", timeout=30)
        self.assertIn("not delivered", str(caught.exception))
        self.assertEqual(self.outcome["undelivered"], [
            {"task_id": "child", "status": "completed", "summary": NOTIFICATION["summary"]}])

    def test_verified_shutdown_grace_is_bounded(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_fixture(f"""
emit({verifier_call()!r})
emit({verifier_output()!r})
emit({END_TURN!r})
time.sleep(30)
""", timeout=1, shutdown_grace=0.2)
        self.assertTrue(self.outcome["completed_at_deadline"])
        self.assertLess(self.elapsed, 10, "shutdown grace became an unbounded wait")

    def test_a_nonzero_exit_after_verification_is_still_a_failure(self):
        code = self.run_fixture(f"""
emit({verifier_call()!r})
emit({verifier_output()!r})
emit({END_TURN!r})
sys.stdin.read()
sys.exit(7)
""", timeout=30)
        self.assertEqual(code, 7)

    def test_an_error_result_after_verification_still_ends_the_session(self):
        code = self.run_fixture(f"""
emit({verifier_call()!r})
emit({verifier_output()!r})
emit({dict(END_TURN, is_error=True)!r})
sys.stdin.read()
""", timeout=30)
        self.assertEqual(code, 0)
        self.assertTrue(self.outcome["terminal_evidence"])


class StagedSourceTests(unittest.TestCase):
    """The staging manifest names the R3 support copies; pin what they contain."""

    def test_r3_transport_matches_the_shared_transport(self):
        # The R3 copy is frozen. The shared transport has since gained parent-
        # compaction delivery (2026-10-05), so pin the frozen bytes instead.
        import hashlib
        self.assertEqual(hashlib.sha256((R3 / "native_cc_stream.py").read_bytes()).hexdigest(),
                         "5d7eea15d181410940c89dbe11e2a48e6dc57eb7a9aa5ae51fa0e73624b5c50d")
        self.assertTrue(hasattr(load("frozen_r3_stream", R3 / "native_cc_stream.py")
                                .NotificationFlow, "complete"))

    def test_r3_transport_carries_the_completion_contract(self):
        # The R3 fixture file keeps the `-r2` name the prepare script pins, so
        # guard against either support copy being reverted to the R2 bytes.
        self.assertTrue(hasattr(MODULE.NotificationFlow, "complete"))
        self.assertNotEqual((R3 / "native_cc_stream.py").read_bytes(),
                            (R2 / "native_cc_stream.py").read_bytes())
        self.assertEqual(FIXTURE.FIXTURE_REVISION, "r3-completion-contract")

    def test_the_fixture_prompt_and_verifier_are_unchanged_from_r2(self):
        # Only session shutdown was repaired. The work the session is asked to
        # do, and the bytes it is checked against, must not have drifted.
        previous = load("qwen_native_compat_r2", R2 / "qwen-native-compat-r2.py")
        for module in (FIXTURE, previous):
            source = Path(module.__file__).read_text()
            start = source.index('    prompt = """This is a native harness')
            self.assertIn("ONE FILE PER TOOL TURN", source[start:start + 2400])
        self.assertEqual(
            self.rendered(FIXTURE)["verifier"], self.rendered(previous)["verifier"])
        self.assertEqual(self.rendered(FIXTURE)["prompt"], self.rendered(previous)["prompt"])

    @staticmethod
    def rendered(module) -> dict:
        """Evaluate fixture text assignments, preserving multiline literals."""
        import ast
        source = Path(module.__file__).read_text()
        main = next(node for node in ast.parse(source).body
                    if isinstance(node, ast.FunctionDef) and node.name == "main")
        names = {"sentinels", "report_text", "expected", "verifier", "prompt"}
        assignments = []
        for node in main.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in names
                    for target in node.targets):
                assignments.append(node)
            elif (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name)
                  and node.target.id == "verifier"):
                assignments.append(node)
        namespace = {"VERIFIER_SENTINEL": "NATIVE_CC_COMPAT_OK",
                     "colors": dict.fromkeys(["red", "green", "blue", "yellow", "black"])}
        exec(compile(ast.Module(body=assignments, type_ignores=[]),
                     str(module.__file__), "exec"), namespace)
        return namespace


if __name__ == "__main__":
    unittest.main()
