# SPDX-License-Identifier: MIT
"""Offline tests for the two Phase B2 helpers.

No model, simulator or GPU. Every ledger lives in a temporary directory; the
`dsw-preflight` fixture is only ever read, and tampering tests copy it first.

    .venv-libero/bin/python3 -m unittest discover \
        -s docs/experiments/code-world-qwen-debug-20260922 -p 'test_pilot_helpers.py' -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPPORT = HERE / "support"
REPO = HERE.parents[2]
SOURCE = HERE / "coordination" / "dsw-preflight"

sys.path.insert(0, str(SUPPORT))

import pilot_assignment_guard as guard  # noqa: E402
import pilot_import  # noqa: E402

state_module = pilot_import.load_state_module()
NativeWorldState = state_module.NativeWorldState
ProtocolError = state_module.ProtocolError

IDENTITY = {"protocol": "native-world-pilot", "model": "qwen-offline-test",
            "condition": "C", "dev_seeds": [51, 52, 53]}
FILLER_BUNDLE = {"policy": "f" * 64}


def fresh_state(root: Path, identity: dict | None = None) -> NativeWorldState:
    return NativeWorldState(root / "cell", dict(identity or IDENTITY))


def burn_attempt(state: NativeWorldState, seed: int, policy: str) -> dict:
    """Consume one charged slot with a non-imported diagnostic row."""
    record = state.begin_trial("diagnostic", seed, {"policy": policy}, {"policy": "x.py"})
    state.finish_trial(record, exit_code=0,
                       result={"sandbox_rc": 0, "reward": 0.0, "task_completed": 0,
                               "trial_dir": record["directory"]})
    return record


class ImportHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-import-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # ---- source verification --------------------------------------------------

    def test_pinned_hashes_match_the_immutable_fixture(self):
        measured = pilot_import.verify_tree(SOURCE)
        self.assertEqual(measured, pilot_import.PINNED_HASHES)
        self.assertEqual(pilot_import.source_digest(measured), pilot_import.PINNED_DIGEST)

    def test_summary_still_states_the_imported_claim(self):
        summary = pilot_import.read_summary(SOURCE)
        self.assertEqual(summary["remaining_real_budget"], {"51": 2, "52": 3, "53": 3})
        self.assertFalse(summary["task_policy_executed"])
        self.assertTrue(summary["charged"])

    def test_tampered_source_is_refused_before_the_ledger_is_touched(self):
        copy = self.tmp / "tampered"
        shutil.copytree(SOURCE, copy)
        (copy / "replay.log").write_text("edited\n")
        state = fresh_state(self.tmp)
        with self.assertRaises(pilot_import.PilotImportError) as caught:
            pilot_import.import_diagnostic(state, copy)
        self.assertIn("replay.log", str(caught.exception))
        self.assertEqual(state.data["trials"], [])
        self.assertEqual(state.budget_remaining(51), 3)

    # ---- one charged row ------------------------------------------------------

    def test_import_charges_one_attempt_and_leaves_2_3_3(self):
        state = fresh_state(self.tmp)
        record = pilot_import.import_diagnostic(state, SOURCE)

        self.assertEqual(record["phase"], "diagnostic")
        self.assertEqual(record["seed"], 51)
        self.assertTrue(record["charged"])
        self.assertEqual(record["status"], "complete")
        self.assertEqual(record["task_completed"], 0)
        self.assertEqual(state.attempts_used(51), 1)
        self.assertEqual(state.progress()["attempts_remaining_per_seed"],
                         {"51": 2, "52": 3, "53": 3})
        self.assertEqual(state.progress()["attempts_remaining_per_seed"],
                         pilot_import.read_summary(SOURCE)["remaining_real_budget"])

        copied = Path(state.task_dir) / record["directory"]
        self.assertEqual(pilot_import.verify_tree(copied), pilot_import.PINNED_HASHES)
        self.assertEqual(record["sources"]["policy"],
                         str(copied / pilot_import.POLICY_REL))
        self.assertEqual(record["bundle"],
                         {"policy": pilot_import.PINNED_HASHES[pilot_import.POLICY_REL]})
        self.assertEqual(record["imported_from"]["source_digest"], pilot_import.PINNED_DIGEST)
        self.assertEqual(record["imported_from"]["hashes"], pilot_import.PINNED_HASHES)
        self.assertFalse(record["imported_from"]["task_policy_executed"])
        self.assertEqual(record["imported_from"]["host"], "dsw-829708-645b58b7f6-w8l4g")

    def test_repeat_and_resume_reuse_the_same_charged_row(self):
        state = fresh_state(self.tmp)
        first = pilot_import.import_diagnostic(state, SOURCE)
        again = pilot_import.import_diagnostic(state, SOURCE)
        self.assertEqual(again["directory"], first["directory"])

        resumed = pilot_import.open_state(state.task_dir)
        third = pilot_import.import_diagnostic(resumed, SOURCE)
        self.assertEqual(third["directory"], first["directory"])
        self.assertEqual(len(pilot_import.imported_rows(resumed)), 1)
        self.assertEqual(resumed.attempts_used(51), 1)
        self.assertEqual(resumed.budget_remaining(51), 2)

    def test_crash_between_reservation_and_copy_finishes_the_same_row(self):
        state = fresh_state(self.tmp)
        original = pilot_import.copy_evidence

        def explode(*_args, **_kwargs):
            raise RuntimeError("simulated crash mid-import")

        pilot_import.copy_evidence = explode
        try:
            with self.assertRaises(RuntimeError):
                pilot_import.import_diagnostic(state, SOURCE)
        finally:
            pilot_import.copy_evidence = original

        self.assertEqual(len(state.data["trials"]), 1)
        stranded = state.data["trials"][0]
        self.assertEqual(stranded["status"], "running")
        self.assertNotIn("imported_from", stranded)
        # The reservation kept the placeholder source path.
        self.assertEqual(stranded["sources"], {"policy": pilot_import.POLICY_REL})

        resumed = pilot_import.open_state(state.task_dir)
        record = pilot_import.import_diagnostic(resumed, SOURCE)
        self.assertEqual(record["status"], "complete")
        self.assertEqual(len(resumed.data["trials"]), 1)
        self.assertEqual(resumed.attempts_used(51), 1)
        self.assertEqual(resumed.progress()["attempts_remaining_per_seed"]["51"], 2)
        # A recovered row is normalized to the copy, exactly like a new one.
        self.assertEqual(record["sources"]["policy"],
                         str(Path(resumed.task_dir) / record["directory"]
                             / pilot_import.POLICY_REL))
        self.assertTrue(Path(record["sources"]["policy"]).is_file())
        self.assertEqual(json.loads((Path(resumed.task_dir) / "development_state.json")
                                    .read_text())["trials"][0]["sources"],
                         record["sources"])

    def test_crash_between_finish_and_provenance_reuses_the_row(self):
        state = fresh_state(self.tmp)
        record = pilot_import.import_diagnostic(state, SOURCE)
        del record["imported_from"]  # the provenance write never landed
        state.save()

        resumed = pilot_import.open_state(state.task_dir)
        self.assertEqual(pilot_import.imported_rows(resumed), [])
        second = pilot_import.import_diagnostic(resumed, SOURCE)
        self.assertEqual(second["directory"], record["directory"])
        self.assertEqual(len(resumed.data["trials"]), 1)
        self.assertEqual(resumed.attempts_used(51), 1)

    def test_a_different_import_is_refused(self):
        state = fresh_state(self.tmp)
        record = pilot_import.import_diagnostic(state, SOURCE)
        record["imported_from"]["source_digest"] = "0" * 64  # some other evidence set
        state.save()
        resumed = pilot_import.open_state(state.task_dir)
        with self.assertRaises(pilot_import.PilotImportError) as caught:
            pilot_import.import_diagnostic(resumed, SOURCE)
        self.assertIn("one charged import per cell", str(caught.exception))
        self.assertEqual(resumed.attempts_used(51), 1)

    def test_two_matching_rows_are_refused_instead_of_picking_one(self):
        state = fresh_state(self.tmp)
        record = pilot_import.import_diagnostic(state, SOURCE)
        # A second charged row for the same import, unmarked: exactly what a
        # first-match lookup would hide behind the marked one.
        twin = {key: value for key, value in record.items() if key != "imported_from"}
        twin["attempt"] = 2
        state.data["trials"].append(twin)
        state.save()

        resumed = pilot_import.open_state(state.task_dir)
        self.assertEqual(len(pilot_import.matching_rows(
            resumed, record["bundle_sha256"])), 2)
        with self.assertRaises(pilot_import.PilotImportError) as caught:
            pilot_import.import_diagnostic(resumed, SOURCE)
        self.assertIn("charged it more than once", str(caught.exception))

    def test_a_degraded_import_row_is_refused(self):
        for key, value in (("charged", False), ("status", "infrastructure_error"),
                           ("phase", "initial"), ("task_completed", 1),
                           ("bundle_sha256", "e" * 64)):
            with self.subTest(field=key):
                root = self.tmp / key
                state = fresh_state(root)
                record = pilot_import.import_diagnostic(state, SOURCE)
                record[key] = value
                state.save()
                resumed = pilot_import.open_state(state.task_dir)
                with self.assertRaises(pilot_import.PilotImportError):
                    pilot_import.import_diagnostic(resumed, SOURCE)

    def test_identity_errors_accepts_a_healthy_row(self):
        state = fresh_state(self.tmp)
        record = pilot_import.import_diagnostic(state, SOURCE)
        self.assertEqual(pilot_import.identity_errors(record, record["bundle_sha256"]), [])

    # ---- what the imported row is not -----------------------------------------

    def test_imported_evidence_is_neither_candidate_nor_task_success(self):
        state = fresh_state(self.tmp)
        pilot_import.import_diagnostic(state, SOURCE)
        self.assertEqual(state.candidates(), {})
        self.assertEqual(state.progress()["tested_bundles"], {})

        outcome = state.outcome(51)
        self.assertFalse(outcome["passed"])
        self.assertFalse(outcome["initial_task_completed"])
        self.assertFalse(outcome["has_initial_evidence"])
        self.assertEqual(outcome["attempts_remaining"], 2)
        self.assertEqual(outcome["infrastructure_errors"], [])

        progress = state.progress()
        self.assertEqual(progress["infrastructure_errors"], [])
        self.assertEqual(progress["seeds_passing"], [])
        self.assertIn(51, progress["seeds_needing_initial"])
        self.assertEqual(len(state.executed()), 1)  # counted once, no alias row

    def test_exhausted_seed_refuses_the_import(self):
        state = fresh_state(self.tmp)
        for index in range(state.ATTEMPT_LIMIT):
            burn_attempt(state, 51, f"{index:064x}")
        self.assertEqual(state.budget_remaining(51), 0)
        with self.assertRaises(ProtocolError) as caught:
            pilot_import.import_diagnostic(state, SOURCE)
        self.assertIn("simulator attempts", str(caught.exception))
        self.assertEqual(state.attempts_used(51), 3)
        self.assertEqual(pilot_import.imported_rows(state), [])

    # ---- partition enforcement ------------------------------------------------

    def test_held_out_seeds_are_refused_by_the_ledger(self):
        with self.assertRaises(ProtocolError):
            fresh_state(self.tmp, {**IDENTITY, "dev_seeds": [50, 51, 52]})
        with self.assertRaises(ProtocolError):
            fresh_state(self.tmp / "b", {**IDENTITY, "dev_seeds": [1, 2, 3]})

    def test_out_of_partition_trial_is_refused(self):
        state = fresh_state(self.tmp)
        with self.assertRaises(ProtocolError) as caught:
            state.begin_trial("diagnostic", 40, FILLER_BUNDLE, {"policy": "x.py"})
        self.assertIn("outside the development partition", str(caught.exception))

    def test_import_seed_must_be_in_the_partition(self):
        state = fresh_state(self.tmp, {**IDENTITY, "dev_seeds": [61, 62]})
        with self.assertRaises(pilot_import.PilotImportError) as caught:
            pilot_import.import_diagnostic(state, SOURCE)
        self.assertIn("outside this cell's", str(caught.exception))

    def test_import_requires_an_initialized_ledger(self):
        with self.assertRaises(pilot_import.PilotImportError):
            pilot_import.open_state(self.tmp / "never-initialized")


FROZEN = "# Frozen worker assignment\n\nDo the pilot exactly as written.\n" + "x" * 500
TRIAL_CMD = (".venv-libero/bin/python3 scripts/libero/native_world_protocol.py "
             "trial --phase initial --seed 52 --code fix.py")


class AssignmentGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot-guard-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.assignment = self.tmp / "worker-prompt.md"
        self.assignment.write_text(FROZEN)
        self.state = self.tmp / "assignment_state.json"
        self.audit = self.tmp / "audit.jsonl"
        self.case = self.tmp / "case.json"
        self.case.write_text(json.dumps({"id": "pilot", "control": str(self.tmp),
                                         "sim": str(REPO)}))
        self.stub = self.tmp / "trial_ran"

    # ---- helpers --------------------------------------------------------------

    def payload(self, tool, **kwargs):
        base = {"hook_event_name": "PreToolUse", "tool_name": tool,
                "session_id": "root-session", "cwd": str(REPO),
                "transcript_path": str(self.tmp / "root.jsonl")}
        base.update(kwargs)
        return base

    def agent_call(self, prompt, role="general-purpose", **kwargs):
        return self.payload("Agent", tool_input={"subagent_type": role, "prompt": prompt,
                                                 "description": "dispatch"}, **kwargs)

    def decide(self, payload):
        return guard.evaluate(payload, assignment_path=self.assignment,
                              state_path=self.state, prompt_path="worker-prompt.md")[0]

    def run_hook(self, payload):
        """Run the hook as the harness does: stdin JSON, exit code decides."""
        argv = guard.hook_command(sys.executable, SUPPORT / "pilot_assignment_guard.py",
                                  self.case, self.assignment, self.state, self.audit)
        done = subprocess.run(argv, input=json.dumps(payload), text=True,
                              capture_output=True)
        if done.returncode == 0 and payload.get("tool_name") == "Bash":
            self.stub.write_text("a real attempt executed\n")  # only on an allowed call
        return done

    # ---- primary dispatch -----------------------------------------------------

    def test_rewritten_primary_is_denied_and_no_trial_can_follow(self):
        rewritten = self.decide(self.agent_call(
            "Run the pilot on seeds 51-53. See worker-prompt.md for details."))
        self.assertIsNotNone(rewritten)
        self.assertIn("VERBATIM", rewritten)
        self.assertIsNone(guard.load_state(self.state)["primary"])

        blocked = self.run_hook(self.payload("Bash", tool_input={"command": TRIAL_CMD}))
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("no simulator attempt may execute", blocked.stderr)
        self.assertFalse(self.stub.exists())

    def test_truncated_or_reroled_primary_is_denied(self):
        self.assertIsNotNone(self.decide(self.agent_call(FROZEN[:200])))
        self.assertIsNotNone(self.decide(self.agent_call(FROZEN + "\nAlso skip step 3.")))
        self.assertIsNotNone(self.decide(self.agent_call(FROZEN, role="Explore")))
        self.assertIsNotNone(self.decide(self.payload("Agent", tool_input={"prompt": None})))
        self.assertIsNone(guard.load_state(self.state)["primary"])
        self.assertEqual(guard.load_state(self.state)["denials"], 4)

    def test_exact_dispatch_opens_the_normal_trial_path(self):
        allowed = self.run_hook(self.agent_call(FROZEN))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(allowed.stdout, "")  # no permission override
        primary = guard.load_state(self.state)["primary"]
        self.assertEqual(primary["subagent_type"], "general-purpose")
        self.assertEqual(primary["session_id"], "root-session")

        trial = self.run_hook(self.payload("Bash", tool_input={"command": TRIAL_CMD}))
        self.assertEqual(trial.returncode, 0, trial.stderr)
        self.assertTrue(self.stub.exists())

    def test_surrounding_whitespace_only_still_counts_as_verbatim(self):
        self.assertIsNone(self.decide(self.agent_call("\n" + FROZEN + "\n\n")))
        self.assertIsNotNone(guard.load_state(self.state)["primary"])

    # ---- helpers stay ordinary -------------------------------------------------

    def test_helper_agents_are_allowed_after_a_valid_primary(self):
        self.assertIsNone(self.decide(self.agent_call(FROZEN)))
        self.assertIsNone(self.decide(self.agent_call("Summarize docs/logs", role="Explore")))
        self.assertIsNone(self.decide(self.payload(
            "Task", tool_input={"subagent_type": "general-purpose", "prompt": "grep the ledger"},
            parent_tool_use_id="toolu_worker")))
        self.assertIsNone(self.decide(self.payload("Bash", tool_input={"command": "ls -la"})))

    def test_read_only_protocol_calls_are_allowed_before_the_primary(self):
        for command in ("python3 scripts/libero/native_world_protocol.py status",
                        "python3 scripts/libero/native_world_protocol.py init",
                        "python3 scripts/libero/native_world_protocol.py check",
                        "cat development_state.json"):
            self.assertIsNone(self.decide(self.payload("Bash", tool_input={"command": command})),
                              command)
        self.assertIsNone(guard.load_state(self.state)["primary"])

    def test_trial_and_direct_runners_are_recognized(self):
        self.assertTrue(guard.is_trial_command(TRIAL_CMD))
        self.assertTrue(guard.is_trial_command(
            "python3 scripts/libero/native_world_protocol.py trial --phase snapshot --seed 51"))
        self.assertTrue(guard.is_trial_command("python3 scripts/libero/replay_trial.py --seed 52"))
        self.assertTrue(guard.is_trial_command("python3 scripts/libero/run_fix_loop_validation.py"))
        self.assertFalse(guard.is_trial_command(
            "python3 scripts/libero/native_world_protocol.py status"))
        self.assertFalse(guard.is_trial_command("grep -rn 'trial' docs/"))

    # ---- identity is conservative ---------------------------------------------

    def test_unknown_initial_identity_is_not_a_free_pass(self):
        reason = self.decide(self.payload(
            "Agent", tool_input={"subagent_type": "Explore", "prompt": "read the docs"},
            parent_tool_use_id="toolu_unknown"))
        self.assertIsNotNone(reason)
        self.assertIn("not accepted", reason)
        self.assertIsNone(guard.load_state(self.state)["primary"])

    def test_subagent_detection(self):
        self.assertFalse(guard.is_subagent(self.payload("Agent")))
        self.assertFalse(guard.is_subagent({"tool_name": "Agent", "agent_type": "root"}))
        self.assertTrue(guard.is_subagent({"parent_tool_use_id": "toolu_1"}))
        self.assertTrue(guard.is_subagent({"agent_id": "a1b2c3d4"}))
        self.assertTrue(guard.is_subagent({"agent_type": "general-purpose"}))

    # ---- state and failure modes ----------------------------------------------

    def test_state_is_task_scoped_and_written_atomically(self):
        nested = self.tmp / "cell" / "control" / "assignment_state.json"
        guard.evaluate(self.agent_call(FROZEN), assignment_path=self.assignment,
                       state_path=nested)
        self.assertTrue(nested.is_file())
        self.assertFalse(list(nested.parent.glob("*.tmp")))
        self.assertIsNotNone(json.loads(nested.read_text())["primary"])
        # A second cell's state is independent.
        self.assertIsNone(guard.load_state(self.state)["primary"])

    def test_audit_records_both_outcomes(self):
        self.run_hook(self.agent_call("rewritten"))
        self.run_hook(self.agent_call(FROZEN))
        events = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertEqual([e["denied"] for e in events], [True, False])
        self.assertEqual([e["primary_recorded"] for e in events], [False, True])

    def test_guard_fails_closed(self):
        self.assignment.write_text("   \n")
        done = self.run_hook(self.agent_call(FROZEN))
        self.assertEqual(done.returncode, 2)
        self.assertIn("gate failed", done.stderr)

        self.case.write_text("{not json")
        done = self.run_hook(self.agent_call(FROZEN))
        self.assertEqual(done.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
