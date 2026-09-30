# SPDX-License-Identifier: MIT
"""Offline protocol regression tests; no model, GPU, or simulator is started."""
from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/libero"
sys.path.insert(0, str(SCRIPTS))
from fix_loop_state import ProtocolError, Stage1State, code_hash
from fix_loop_worker import Worker, complete_turns, parse_args
import run_fix_loop_validation as validation

PROGRAM = 'obs = get_observation()\n'
FINDINGS = '''# Findings
## Root causes observed
All recorded development attempts failed; see development_state.json.
## What fixed them
none
## Generalizable patterns
none
## Blocked seeds
See the per-seed BLOCKED notes and the ledger.
'''
BLOCKED = '''# Blocked
## Root Cause: Algorithmic
## Details
Three recorded repairs failed; see this seed's preserved replay.log files.
## What Was Tried
Three candidate replays within the fixed budget; none succeeded.
'''


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "aspire/sim"
        self.repo.mkdir(parents=True)
        for relative in ("CLAUDE.md", ".claude/libero/fix-loop/subagent-prompt.md",
                         ".claude/libero/fix-loop/skills/task-exploration.md", ".claude/libero/api-reference.md"):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('Depth: obs["agentview"]["images"]["depth"].\n')
        config = self.repo / "env_configs/libero/franka_libero_traced.yaml"
        config.parent.mkdir(parents=True)
        config.write_text("env: {}\n")
        script = self.repo / "scripts/libero/scene_snapshot.py"
        script.parent.mkdir(parents=True)
        script.write_text("get_observation()\n# snapshot\n")
        self.argv = ["--repo", str(self.repo), "--suite", "libero_object_swap", "--task", "task",
                     "--gpu", "4", "--cuda-visible-devices", "4,0", "--egl-device-id", "0"]
        self.worker = Worker(parse_args(self.argv))

    def record(self, phase, seed=51, source=PROGRAM, success=False, crash=False):
        state = self.worker.state
        record = state.begin_trial(phase, seed, source)
        state.finish_trial(record, result={"task_completed": int(success), "sandbox_rc": int(crash),
                                           "reward": float(success), "trial_dir": record["directory"]}, exit_code=0)
        return record

    def initial_batch(self, success=False):
        self.record("snapshot", source=PROGRAM + "# snapshot")
        self.record("smoke")
        for seed in self.worker.dev_seeds:
            self.record("initial", seed, success=success)

    def reports(self):
        self.worker.write_file(str(self.worker.task_dir / "task_analysis.md"), "Observed the scene; see snapshots.")
        self.worker.write_file(str(self.worker.task_dir / "findings.md"), FINDINGS)
        self.worker.write_file(str(self.worker.task_dir / "fix_code.py"), PROGRAM)
        self.worker.write_file(str(self.worker.working_code), PROGRAM)

    def test_three_failures_do_not_exhaust_another_seed(self):
        self.initial_batch()
        for _ in range(3):
            self.record("repair", 51)
        with self.assertRaisesRegex(ProtocolError, "seed 51 exhausted"):
            self.record("repair", 51)
        self.record("repair", 52)
        progress = self.worker.state.progress()
        self.assertEqual(progress["repairs_used_per_seed"]["51"], 3)
        self.assertEqual(progress["repairs_used_per_seed"]["52"], 1)
        self.assertIn(52, progress["pending_repair_seeds"])

    def test_seed_51_only_and_placeholder_cannot_complete(self):
        self.initial_batch()
        for _ in range(3):
            self.record("repair", 51)
        self.reports()
        self.worker.write_file(str(self.worker.task_dir / "attempts/seed_51_BLOCKED.md"), BLOCKED)
        self.worker.write_file(str(self.worker.task_dir / "findings.md"), "# Findings\n(placeholder — evidence gathering in progress)")
        errors = self.worker.state.completion_errors(working_code=self.worker.working_code)
        self.assertTrue(any("pending_repair_seeds" in error and "52" in error for error in errors))
        self.assertTrue(any("placeholder" in error for error in errors))
        with patch("builtins.print"):
            self.assertEqual(self.worker.finalize("done"), 1)
        self.assertFalse(json.loads((self.worker.task_dir / "stage1_result.json").read_text())["stage1_complete"])

    def test_evidence_reads_remain_available_at_finish(self):
        self.initial_batch(success=True)
        self.reports()
        for _ in range(30):
            response, _ = self.worker.dispatch("read_file", json.dumps({"path": str(self.worker.task_dir / "fix_code.py")}))
            self.assertIn("get_observation", response)
        self.assertEqual(self.worker.state.completion_errors(working_code=self.worker.working_code), [])

    def test_resume_keeps_per_seed_budget_usage_and_steps(self):
        self.initial_batch()
        for _ in range(3):
            self.record("repair", 51)
        self.worker.state.data.update(model_steps=37, model_seconds=120.0, usage={"requests": 37})
        self.worker.state.save()
        resumed = Worker(parse_args(self.argv + ["--resume"]))
        self.assertEqual(resumed.state.data["model_steps"], 37)
        self.assertEqual(resumed.state.data["usage"]["requests"], 37)
        with self.assertRaises(ProtocolError):
            resumed.state.begin_trial("repair", 51, PROGRAM)
        resumed.state.begin_trial("repair", 52, PROGRAM)
        with self.assertRaisesRegex(ProtocolError, "same protocol"):
            Worker(parse_args(self.argv + ["--resume", "--max-tokens", "1024"]))

    def test_infrastructure_failure_is_incomplete_evidence(self):
        record = self.worker.state.begin_trial("snapshot", 51, PROGRAM)
        self.worker.state.finish_trial(record, result=None, exit_code=1, error="EGL setup failed")
        self.assertTrue(self.worker.state.progress()["infrastructure_errors"])
        with self.assertRaisesRegex(ProtocolError, "incomplete infrastructure"):
            self.record("initial")

    def test_initial_is_frozen_and_heldout_is_rejected(self):
        self.record("snapshot")
        self.record("smoke")
        self.record("initial", 51)
        with self.assertRaisesRegex(ProtocolError, "frozen"):
            self.record("initial", 52, PROGRAM + "open_gripper()\n")
        with self.assertRaisesRegex(ProtocolError, "outside"):
            self.record("initial", 1)
        result, _ = self.worker.dispatch("bash", json.dumps({"command": "python scripts/libero/replay_trial.py --args.trial 51"}))
        self.assertIn("run_trial", result)

    def test_short_legal_fallback_and_crash_preference(self):
        self.initial_batch()
        self.reports()
        for seed in self.worker.dev_seeds:
            for _ in range(3):
                self.record("repair", seed, source='raise RuntimeError("broken")\n', crash=True)
            self.worker.write_file(str(self.worker.task_dir / f"attempts/seed_{seed}_BLOCKED.md"), BLOCKED)
        self.worker.write_file(str(self.worker.task_dir / "fix_code.py"), 'raise RuntimeError("broken")\n')
        self.worker.write_file(str(self.worker.working_code), 'raise RuntimeError("broken")\n')
        self.assertTrue(any("fewer crashes" in error for error in self.worker.state.completion_errors(working_code=self.worker.working_code)))
        self.reports()
        self.assertEqual(self.worker.state.completion_errors(working_code=self.worker.working_code), [])

    def test_compaction_keeps_whole_tool_turns_and_durable_notes(self):
        notes = self.worker.task_dir / "notes.md"
        notes.write_text('Depth belongs under images. Repair evidence: development/repair/seed_51/attempt_2.')
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "assignment"}]
        for step in range(10):
            calls = [{"id": f"{step}-{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}} for i in range(3)]
            messages.append({"role": "assistant", "tool_calls": calls, "content": ""})
            messages.extend({"role": "tool", "tool_call_id": c["id"], "content": "result"} for c in calls)
        compacted = self.worker.compact(messages, keep=2)
        self.assertEqual(len(complete_turns(compacted[2:])), 2)
        self.assertIn("Depth belongs under images", compacted[2]["content"])
        for turn in complete_turns(compacted[2:]):
            self.assertEqual({c["id"] for c in turn[0]["tool_calls"]}, {m["tool_call_id"] for m in turn[1:]})

    def test_context_retry_keeps_output_budget_and_state(self):
        messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "task"}]
        for i in range(4):
            messages.append({"role": "assistant", "content": str(i)})
        requests = []
        def post(payload):
            requests.append(copy.deepcopy(payload))
            if len(requests) == 1:
                raise urllib.error.HTTPError("http://local", 400, "Bad Request", {}, io.BytesIO(b"maximum context length exceeded"))
            return {"model": self.worker.args.model, "usage": {}, "choices": []}
        with patch.object(self.worker, "post", side_effect=post):
            self.worker.call_model(messages)
        self.assertEqual([r["max_tokens"] for r in requests], [4096, 4096])
        self.assertLess(len(requests[1]["messages"]), len(requests[0]["messages"]))
        self.assertIn("recorded_progress", requests[1]["messages"][2]["content"])

    def test_bash_retains_cwd_and_missing_content_is_retryable(self):
        self.assertIn(str(self.repo), self.worker.run_bash("pwd"))
        result, _ = self.worker.dispatch("write_file", '{"path":"notes.md"}')
        self.assertIn("KeyError", result)
        self.assertFalse(self.worker.state.data["stage1_complete"])

    def test_all_crashed_allows_runbook_minimal_fallback(self):
        self.record("snapshot")
        self.record("smoke", crash=True)
        broken = 'get_observation()["wrong_key"]\n'
        for seed in self.worker.dev_seeds:
            self.record("initial", seed, source=broken, crash=True)
        for seed in self.worker.dev_seeds:
            for _ in range(3):
                self.record("repair", seed, source=broken, crash=True)
            self.worker.write_file(str(self.worker.task_dir / f"attempts/seed_{seed}_BLOCKED.md"), BLOCKED)
        self.reports()
        for path in (self.worker.task_dir / "fix_code.py", self.worker.working_code):
            self.worker.write_file(str(path), "get_observation()\n")
        self.assertEqual(self.worker.state.completion_errors(working_code=self.worker.working_code), [])
        self.assertEqual(self.worker.state.final_coverage("get_observation()\n"), [])

    def test_parallel_image_calls_keep_tool_results_adjacent(self):
        other = Worker(parse_args(self.argv + ["--task", "parallel", "--max-steps", "2"]))
        image_path = other.task_dir / "image.png"
        image_path.write_bytes(b"fixture")
        calls = [
            {"id": "image", "type": "function", "function": {"name": "view_image", "arguments": json.dumps({"path": str(image_path)})}},
            {"id": "state", "type": "function", "function": {"name": "get_state", "arguments": "{}"}},
        ]
        requests = []
        def post(payload):
            requests.append(copy.deepcopy(payload))
            message = {"role": "assistant", "content": "", "tool_calls": calls if len(requests) == 1 else []}
            return {"model": other.args.model, "usage": {}, "choices": [{"message": message}]}
        with patch.object(other, "post", side_effect=post), patch("builtins.print"):
            self.assertEqual(other.run(), 1)
        tail = requests[1]["messages"][3:]
        self.assertEqual([m["role"] for m in tail], ["assistant", "tool", "tool", "user"])

    def test_legacy_results_are_not_adopted(self):
        other = self.worker.task_dir.parent / "legacy"
        other.mkdir()
        (other / "stage1_result.json").write_text('{"stage1_complete":true}')
        with self.assertRaisesRegex(ProtocolError, "fresh task directory"):
            Stage1State(other, self.worker.state.data["identity"])

    def test_model_profiles_share_tools_protocol_and_context(self):
        messages = [{"role": "user", "content": "test"}]
        deepseek = self.worker.request_payload(messages)
        self.worker.args.model_family = "qwen"
        self.worker.args.reasoning_effort = "xhigh"
        qwen = self.worker.request_payload(messages)
        self.assertEqual(deepseek["tools"], qwen["tools"])
        self.assertEqual(deepseek["max_tokens"], qwen["max_tokens"])
        self.assertTrue(deepseek["chat_template_kwargs"]["thinking"])
        self.assertTrue(qwen["chat_template_kwargs"]["preserve_thinking"])

    def test_offline_full_campaign_records_all_60_development_trials(self):
        worker = self.worker
        worker.write_file(str(worker.task_dir / "task_analysis.md"), "Observed both scene snapshots.")
        worker.write_file(str(worker.task_dir / "initial_code.py"), PROGRAM)
        def fake_process(command, **kwargs):
            # Only the structured replay runner reaches this mock; no simulator is imported.
            self.assertEqual(command[1], "scripts/libero/replay_trial.py")
            seed = int(command[command.index("--args.trial") + 1])
            out = Path(command[command.index("--args.output-dir") + 1])
            directory = out / f"trial_{seed:02d}_sandboxrc_0_reward_0.000_taskcompleted_0"
            directory.mkdir(parents=True)
            (directory / "summary.txt").write_text("task_completed=False\n")
            if "/snapshot/" in str(out):
                for name in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"):
                    (worker.task_dir / name).write_bytes(b"offline fixture")
            return subprocess.CompletedProcess(command, 0)
        def reply(payload):
            state = worker.state
            task = str(worker.task_dir)
            if not state.records("snapshot"):
                name, args = "run_trial", {"phase": "snapshot", "seed": 51}
            elif not state.records("smoke"):
                name, args = "run_trial", {"phase": "smoke", "seed": 51, "code_path": task + "/initial_code.py"}
            elif state.progress()["missing_initial_seeds"]:
                name, args = "run_trial", {"phase": "initial", "seed": state.progress()["missing_initial_seeds"][0], "code_path": task + "/initial_code.py"}
            elif state.progress()["pending_repair_seeds"]:
                name, args = "run_trial", {"phase": "repair", "seed": state.progress()["pending_repair_seeds"][0], "code_path": task + "/initial_code.py"}
            elif state.progress()["blocked_notes_required"]:
                name, args = "write_file", {"path": task + f"/attempts/seed_{state.progress()['blocked_notes_required'][0]}_BLOCKED.md", "content": BLOCKED}
            elif not (worker.task_dir / "fix_code.py").exists():
                name, args = "copy_file", {"source": task + "/initial_code.py", "destination": task + "/fix_code.py"}
            elif not worker.working_code.exists():
                name, args = "copy_file", {"source": task + "/fix_code.py", "destination": str(worker.working_code)}
            elif not (worker.task_dir / "findings.md").exists():
                name, args = "write_file", {"path": task + "/findings.md", "content": FINDINGS}
            else:
                name, args = "finish", {"summary": "All 15 initial seeds and their 45 bounded repairs recorded; none passed."}
            return {"model": worker.args.model, "usage": {}, "choices": [{"message": {
                "role": "assistant", "content": "", "tool_calls": [{"id": "call", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]
            }}]}
        with patch.object(worker, "post", side_effect=reply), patch("fix_loop_worker.subprocess.run", side_effect=fake_process), patch("builtins.print"):
            self.assertEqual(worker.run(), 0)
        self.assertEqual(len(worker.state.records("initial")), 15)
        self.assertEqual(len(worker.state.records("repair")), 45)
        self.assertTrue(all(len(worker.state.records("repair", seed)) == 3 for seed in worker.dev_seeds))
        directories = [r["directory"] for r in worker.state.data["trials"]]
        self.assertEqual(len(directories), len(set(directories)))
        result = json.loads((worker.task_dir / "stage1_result.json").read_text())
        self.assertTrue(result["stage1_complete"])
        self.assertEqual(len(result["final_code_development_trials"]), 60)
        with patch.object(validation, "ROOT", self.repo):
            validation.check_model_stage1(worker.task_dir / "fix_code.py", worker.args.suite, worker.args.task)
            (worker.task_dir / "findings.md").write_text("placeholder")
            with self.assertRaisesRegex(ValueError, "placeholder"):
                validation.check_model_stage1(worker.task_dir / "fix_code.py", worker.args.suite, worker.args.task)


if __name__ == "__main__":
    unittest.main()
