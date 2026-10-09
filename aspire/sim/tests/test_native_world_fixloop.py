"""Focused offline tests for the native original Fix Loop (A1/B1/C1).

No simulator, no model requests, no trials. Every program and inventory here is
a synthetic fixture and is never a run result. Replay execution is faked at the
`run_replay` boundary so the accounting, selection, prompt and isolation rules
can be checked without a GPU.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SIM = Path(__file__).resolve().parents[1]
for extra in (SIM, SIM / "scripts/common", SIM / "scripts/libero"):
    sys.path.insert(0, str(extra))

import native_world_campaign as campaign
import native_world_fixloop_state as ledger
import native_world_protocol as protocol
from native_cc_guard import denial

POLICY = "obs = get_observation()\nprint(obs)\n"
# A valid world module is pure definitions: `world_module_errors` requires these
# four names at module level and, unlike the policy rule, no call expression.
WORLD = ("def initialize(context):\n    return {}\n\n"
         "def advance(state, step):\n    return state\n\n"
         "def predict(state, step):\n    return {}\n\n"
         "def assimilate(state, evidence):\n    return state\n")
INVENTORY = json.dumps([{"id": "bowl", "label": "bowl", "role": "manipulated",
                         "confidence": "high", "shape_prior": "concave"},
                        {"id": "plate", "label": "plate", "role": "target",
                         "confidence": "high", "shape_prior": "flat"}])
# The four sections `report_errors` requires, all nonempty and none of them
# placeholder text.
FINDINGS = """## Root causes observed
The grasp closed before the gripper cleared the bowl rim.

## What fixed them
Approach from directly above the measured rim centre, then descend.

## Generalizable patterns
Measure before committing; never reuse a stale reference frame.

## Blocked seeds
none
"""
TRANSCRIPT = "\n".join([
    json.dumps({"type": "assistant", "message": {"model": "claude-opus-4-6"}}),
    json.dumps({"type": "result", "usage": {"input_tokens": 120, "output_tokens": 30},
                "modelUsage": {"claude-opus-4-6": {"contextWindow": 1000000,
                                                   "maxOutputTokens": 64000}}}),
]) + "\n"


def make_case(root: Path, condition: str) -> tuple[Path, dict]:
    sim = root / "sim"
    # The task directory itself is deliberately NOT created: `NativeWorldState`
    # owns that initialization and refuses a directory that already holds
    # artifacts, so pre-creating it here would trip the fresh-directory guard.
    (sim / "outputs/libero_fix_loop/libero_goal_swap").mkdir(parents=True)
    (sim / "outputs/working_codes").mkdir(parents=True)
    (sim / "env_configs/libero").mkdir(parents=True)
    (sim / "env_configs/libero/franka_libero_traced.yaml").write_text("suite: libero\n")
    (sim / ".venv-libero/bin").mkdir(parents=True)
    (sim / ".venv-libero/bin/python3").write_text("#!/bin/sh\n")
    (sim / "scripts/libero").mkdir(parents=True)
    (sim / "scripts/libero/scene_snapshot.py").write_text(POLICY)
    (sim / "docs/skills").mkdir(parents=True)
    if condition != "C":
        for name in campaign.STRATEGY_MD:
            (sim / "docs/skills" / name).write_text("synthetic strategy fixture\n")
    case = {"id": f"{condition}1", "condition": condition, "sim": str(sim),
            "control": str(root / "control"), "suite": "libero_goal_swap",
            "task": "put_the_bowl_on_the_plate", "dev_seeds": list(range(51, 66)),
            "max_steps": 4000, "trial_timeout": 900, "gpu": 0,
            "env_config": "env_configs/libero/franka_libero_traced.yaml",
            "model": "claude-opus-4-6", "model_tag": "claude-opus-4-6[1m]",
            "expected_served_model": "claude-opus-4-6", "effort": "high",
            "context_tokens": 1000000, "max_output_tokens": 64000,
            "service_ports": [8114, 8115, 8116],
            # Device and service wiring that runtime_env and native_settings read.
            "cuda_visible_devices": "0", "egl_device_id": 0,
            "sam3_url": "http://127.0.0.1:8114",
            "graspnet_url": "http://127.0.0.1:8115",
            "pyroki_url": "http://127.0.0.1:8116",
            "disable_experimental_betas": "1",
            "claude_bin": "/bin/true", "api_key_helper": "/bin/true",
            "campaign_timeout": 3600, "generation_context": "test",
            "skill_library_dir": "docs/skills", "python_root": str(root),
            "claude_config_dir": str(root / "control/native/config")}
    (root / "control").mkdir(parents=True)
    path = root / "case.json"
    path.write_text(json.dumps(case))
    return path, case


class ConditionIdentityTests(unittest.TestCase):
    def test_strategy_metadata_matches_prepared_conditions(self):
        for condition in ("A", "B", "C"):
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as directory:
                _, case = make_case(Path(directory), condition)
                self.assertEqual(condition != "C", protocol.identity(case, Path(case["sim"]))["strategy_md"])


class Harness:
    """A case plus a ledger, with replay execution and freezing stubbed out."""

    def __init__(self, stack, condition="A"):
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.case_path, self.case = make_case(root, condition)
        self.case, self.repo, self.task_dir = protocol.load_case(self.case_path)
        stack.enter_context(patch.object(protocol, "verify_runtime", lambda case, repo: None))
        self.replay = stack.enter_context(patch.object(protocol, "run_replay"))
        self.results = {}
        stack.enter_context(patch.object(protocol, "parse_result",
                                         lambda results, seed: self.results.get(seed)))
        self.replay.return_value = (0, "")
        self.state = ledger.NativeWorldState(self.task_dir, protocol.identity(self.case, self.repo))
        self.write("initial_code.py", POLICY)
        if condition != "A":
            self.write("initial_world_program.py", WORLD)
            self.write("initial_inventory.json", INVENTORY)

    def write(self, name, text):
        (self.task_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (self.task_dir / name).write_text(text)
        return self.task_dir / name

    def outcome(self, seed, *, success=True, crash=False):
        self.results[seed] = {"sandbox_rc": 1 if crash else 0,
                              "reward": 1.0 if success else 0.0,
                              "task_completed": int(success), "trial_dir": f"trial_{seed}"}

    def trial(self, phase, seed, stem="initial"):
        world = self.task_dir / f"{stem}_world_program.py" if self.case["condition"] != "A" else None
        inventory = self.task_dir / f"{stem}_inventory.json" if world else None
        code = self.task_dir / (f"{stem}_code.py" if phase != "diagnostic"
                                else "diagnostic_session.py")
        return protocol.run_trial(self.case, self.repo, self.state, phase, seed,
                                  code, world, inventory, code)

    def snapshot(self):
        self.outcome(51, success=True)
        for name in ("scene_snapshot.jpg", "scene_snapshot_wrist.jpg"):
            self.write(name, "synthetic test image")
        return self.trial("snapshot", 51)

    def initial_batch(self, failures=(), seeds=None):
        """The original order: snapshot, then the one initial program on every
        development seed, then triage. Repairs come after that."""
        self.snapshot()
        for seed in (seeds if seeds is not None else self.case["dev_seeds"]):
            self.outcome(seed, success=seed not in failures)
            self.trial("initial", seed)

    def complete_cell(self):
        """Drive this cell to the state where `check` should pass.

        Every development seed gets initial evidence, the whole batch passes, the
        synthesis is the bundle that was actually executed, and the required
        write-ups exist. Used by the chain tests so `finalize` and the held-out
        runner can be exercised against a realistic ledger.
        """
        self.initial_batch()
        self.write("task_analysis.md", "Bowl on plate: rim-relative grasp.\n")
        self.write("findings.md", FINDINGS)
        self.write("fix_code.py", POLICY)
        (self.repo / "outputs/working_codes").mkdir(parents=True, exist_ok=True)
        (self.repo / "outputs/working_codes"
         / f"{self.case['suite']}_{self.case['task']}_fix.py").write_text(POLICY)
        if self.case["condition"] != "A":
            self.write("fix_world_program.py", WORLD)
            self.write("fix_inventory.json", INVENTORY)
        bundle = protocol.selected_bundle(self.case, self.state)
        self.state.select(bundle, "the initial bundle passed every development seed")
        return bundle


FAILED = (52, 53, 54, 55, 58)


class TotalAttemptAccounting(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.h = Harness(self.stack)
        self.h.initial_batch(failures=FAILED)

    def test_three_total_attempts_not_three_extra_repairs(self):
        h = self.h
        self.assertEqual(h.state.retries_used(52), 1)
        h.write("fix_code.py", POLICY + "# v2\n")
        for _ in range(2):
            protocol.run_trial(h.case, h.repo, h.state, "repair", 52,
                               h.task_dir / "fix_code.py", None, None, None)
        self.assertEqual(h.state.retries_used(52), 3)
        self.assertEqual(h.state.retries_remaining(52), 0)
        with self.assertRaisesRegex(ledger.ProtocolError, "all 3 retries"):
            protocol.run_trial(h.case, h.repo, h.state, "repair", 52,
                               h.task_dir / "fix_code.py", None, None, None)

    def test_snapshot_is_outside_the_budget_and_taken_once(self):
        h = self.h
        snapshot = h.state.records("snapshot")[0]
        self.assertFalse(snapshot["spends_retry"])
        # Seed 51 spent exactly one attempt: the initial run, not the snapshot.
        self.assertEqual(h.state.retries_used(51), 1)
        self.assertEqual([r["phase"] for r in h.state.retries(51)], ["initial"])
        with self.assertRaises(ledger.ProtocolError):
            h.trial("snapshot", 51)

    def test_diagnostic_and_smoke_spend_from_the_same_count(self):
        h = self.h
        h.write("diagnostic_session.py", "print(get_observation(), flush=True)\n")
        h.results.pop(53)
        protocol.run_trial(h.case, h.repo, h.state, "diagnostic", 53,
                           h.task_dir / "diagnostic_session.py", None, None,
                           h.task_dir / "diagnostic_session.py")
        self.assertEqual(h.state.retries_used(53), 2)
        spent = {r["phase"] for r in h.state.retries(53)}
        self.assertEqual(spent, {"initial", "diagnostic"})

    def test_failed_and_interrupted_attempts_keep_their_retry_spent(self):
        h = self.h
        h.results.pop(54)
        h.replay.return_value = (143, "watchdog killed the child")
        record = protocol.run_trial(h.case, h.repo, h.state, "repair", 54,
                                    h.write("fix_code.py", POLICY + "# v3\n"), None, None, None)
        self.assertEqual(record["status"], "infrastructure_error")
        self.assertTrue(record["spends_retry"])
        self.assertEqual(h.state.retries_used(54), 2)
        h.state.resolve_interrupted(record, result=None, exit_code=143, error="",
                                    recovery={"spends_retry": False})
        self.assertTrue(record["spends_retry"])
        self.assertEqual(h.state.retries_used(54), 2)

    def test_invalid_revision_is_recorded_without_spending(self):
        h = self.h
        with self.assertRaises(ledger.ProtocolError):
            protocol.run_trial(h.case, h.repo, h.state, "repair", 55,
                               h.write("broken.py", "def ("), None, None, None)
        self.assertEqual(h.state.retries_used(55), 1)  # Only the existing initial run.
        rejected = h.state.data["rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertFalse(rejected[0]["executed"])

    def test_smoke_alias_is_not_a_fictitious_extra_execution(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack)
            h.snapshot()
            h.trial("smoke", 51)
            alias = h.state.alias_smoke_as_initial(51)
            self.assertEqual(h.state.retries_used(51), 1)
            self.assertFalse(alias["spends_retry"])
            self.assertFalse(alias["executed"])
            self.assertTrue(alias["alias_of"])


class SameSeedRepairAndRegression(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.h = Harness(self.stack)
        self.h.initial_batch(failures=(56,))

    def test_repair_runs_on_the_same_failed_seed(self):
        h = self.h
        h.outcome(56, success=True)
        record = protocol.run_trial(h.case, h.repo, h.state, "repair", 56,
                                    h.write("fix_code.py", POLICY + "# fixed\n"),
                                    None, None, None)
        self.assertEqual(record["seed"], 56)
        self.assertEqual(record["task_completed"], 1)

    def test_regression_on_an_initially_passing_seed_is_allowed(self):
        h = self.h
        h.outcome(57, success=True)
        record = protocol.run_trial(h.case, h.repo, h.state, "repair", 57,
                                    h.write("fix_code.py", POLICY + "# v2\n"), None, None, None)
        self.assertEqual(record["phase"], "repair")
        self.assertEqual(h.state.retries_used(57), 2)

    def test_a_seed_exhausted_during_smoke_does_not_block_other_seeds(self):
        h = Harness(self.stack)
        h.snapshot()
        for _ in range(3):
            h.state.begin_trial("smoke", 51, {"policy": ledger.code_hash(POLICY)},
                                {"policy": str(h.task_dir / "initial_code.py")})
            h.state.finish_trial(h.state.records("smoke", 51)[-1],
                                 result={"sandbox_rc": 1, "reward": 0.0, "task_completed": 0,
                                         "trial_dir": "t"}, exit_code=0)
        for seed in range(52, 66):
            h.outcome(seed, success=seed != 58)
            h.trial("initial", seed)
        record = protocol.run_trial(h.case, h.repo, h.state, "repair", 58,
                                    h.write("fix_code.py", POLICY + "# v2\n"), None, None, None)
        self.assertEqual(record["phase"], "repair")
        errors = h.state.completion_errors(bundle={"policy": ledger.code_hash(POLICY + "# v2\n")},
                                           working_code=h.repo / "missing.py",
                                           world_required=False)
        self.assertFalse([e for e in errors if "seeds_needing_initial" in e])


class SelectionAndFreeze(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.h = Harness(self.stack)
        self.h.snapshot()

    def test_untested_bundle_cannot_be_selected(self):
        h = self.h
        h.outcome(59, success=False)
        h.trial("initial", 59)
        untested = {"policy": ledger.code_hash(POLICY + "# never run\n")}
        errors = h.state.selection_errors(untested, POLICY + "# never run\n")
        self.assertTrue([e for e in errors if "never tested" in e or "tested" in e])

    def test_selection_prefers_development_successes_over_last_noncrashing(self):
        h = self.h
        best = POLICY + "# best\n"
        h.write("initial_code.py", best)
        for seed in h.case["dev_seeds"]:
            h.outcome(seed, success=True)
            h.trial("initial", seed)
        latest = POLICY + "# latest, no successes\n"
        h.outcome(61, success=False)
        protocol.run_trial(h.case, h.repo, h.state, "repair", 61,
                           h.write("fix_code.py", latest), None, None, None)
        errors = h.state.selection_errors({"policy": ledger.code_hash(latest)}, latest)
        self.assertTrue(errors, "a bundle with no successes must not win selection")
        self.assertFalse(h.state.selection_errors({"policy": ledger.code_hash(best)}, best))
        improved = POLICY + "# tested synthesis with smaller coverage\n"
        h.outcome(62, success=True)
        protocol.run_trial(h.case, h.repo, h.state, "repair", 62,
                           h.write("fix_code.py", improved), None, None, None)
        self.assertFalse(h.state.selection_errors({"policy": ledger.code_hash(improved)}, improved))

    def test_diagnostic_only_program_is_not_a_policy_candidate(self):
        h = self.h
        session = "print(get_observation(), flush=True)\n"
        h.write("diagnostic_session.py", session)
        protocol.run_trial(h.case, h.repo, h.state, "diagnostic", 62,
                           h.task_dir / "diagnostic_session.py", None, None,
                           h.task_dir / "diagnostic_session.py")
        self.assertNotIn(ledger.bundle_identity({"policy": ledger.code_hash(session)}),
                         h.state.candidates())
        self.assertTrue(h.state.retries(62), "the session still consumed an attempt")

    def test_world_condition_freezes_world_and_inventory_with_the_policy(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            h.snapshot()
            h.outcome(63, success=True)
            record = h.trial("initial", 63)
            self.assertEqual(set(record["bundle"]), {"policy", "world", "inventory"})
            h.write("fix_code.py", POLICY)
            h.write("fix_world_program.py", WORLD)
            h.write("fix_inventory.json", INVENTORY)
            bundle = protocol.selected_bundle(h.case, h.state)
            self.assertEqual(set(bundle), {"policy", "world", "inventory"})
            self.assertEqual(ledger.bundle_identity(bundle), record["bundle_sha256"])

    def test_world_result_path_is_the_adapter_directory(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            directory = h.task_dir / "development/initial/seed_64/attempt_1"
            world = protocol.result_root(h.case, directory, "initial", 64)
            self.assertEqual(world, directory / "world/native_world/seed_64/replay")
            ordinary = protocol.result_root({"condition": "A"}, directory, "initial", 64)
            self.assertEqual(ordinary, directory / "results")


class HeldOutSeeds(unittest.TestCase):
    def test_all_fifty_seeds_are_evaluated_and_the_solver_cannot_run_them(self):
        import contextlib
        import native_world_heldout as heldout
        self.assertEqual(heldout.HELDOUT_SEEDS, tuple(range(1, 51)))
        self.assertEqual(len(heldout.HELDOUT_SEEDS), 50)
        with contextlib.ExitStack() as stack:
            h = Harness(stack)
            for seed in (1, 25, 50):
                with self.assertRaisesRegex(ledger.ProtocolError, "held out"):
                    protocol.run_trial(h.case, h.repo, h.state, "initial", seed,
                                       h.task_dir / "initial_code.py", None, None, None)

    def test_guard_refuses_the_heldout_runner_from_a_solver_bash_call(self):
        case = {"suite": "s", "task": "t", "skill_library_dir": "docs/skills"}
        reason = denial({"tool_name": "Bash", "tool_input": {
            "command": "python3 scripts/libero/native_world_heldout.py --case case.json"}},
            case, Path("/tmp"))
        self.assertIn("held-out evaluation", reason)
        self.assertIn("outer coordinator", reason)


class GuardPaths(unittest.TestCase):
    def test_symlinked_outputs_do_not_reject_legitimate_writes(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            repo, real = root / "sim", root / "results"
            (repo / "docs/skills").mkdir(parents=True)
            task = real / "libero_fix_loop/s/t"
            (task / "attempts").mkdir(parents=True)
            (repo / "outputs").symlink_to(real, target_is_directory=True)
            case = {"suite": "s", "task": "t", "skill_library_dir": "docs/skills",
                    "condition": "B"}
            for name in ("initial_code.py", "fix_code.py", "findings.md", "task_analysis.md",
                         "BLOCKED.md", "fix_world_program.py", "fix_inventory.json"):
                payload = {"tool_name": "Write", "cwd": str(repo),
                           "tool_input": {"file_path": str(task / name)}}
                self.assertIsNone(denial(payload, case, repo), name)
            framework = {"tool_name": "Write", "cwd": str(repo),
                         "tool_input": {"file_path": str(repo / "cap/anything.py")}}
            self.assertIsNotNone(denial(framework, case, repo))

    def test_world_files_are_not_writable_in_the_ordinary_condition(self):
        with tempfile.TemporaryDirectory() as name:
            repo = Path(name) / "sim"
            task = repo / "outputs/libero_fix_loop/s/t"
            task.mkdir(parents=True)
            (repo / "docs/skills").mkdir(parents=True)
            case = {"suite": "s", "task": "t", "skill_library_dir": "docs/skills",
                    "condition": "A"}
            payload = {"tool_name": "Write", "cwd": str(repo),
                       "tool_input": {"file_path": str(task / "fix_world_program.py")}}
            self.assertIsNotNone(denial(payload, case, repo))


class PromptConditions(unittest.TestCase):
    """A/B/C differ only in the world interface and the strategy MD."""

    def setUp(self):
        self.repo = SIM
        base = {"suite": "libero_goal_swap", "task": "put_the_bowl_on_the_plate", "gpu": 0,
                "trial_timeout": 900, "model": "claude-opus-4-6"}
        self.prompts = {}
        for cell, condition in (("A1", "A"), ("B1", "B"), ("C1", "C")):
            case = dict(base, id=cell, condition=condition,
                        skill_library_dir=f"docs/experiments/cells/{cell}/skills")
            self.prompts[condition] = campaign.worker_prompt(case, self.repo)

    def test_original_workflow_survives_in_every_condition(self):
        for condition, text in self.prompts.items():
            for expected in ("Stage 0", "Stage 1", "trace.json", "keyframes/", "summary.txt",
                             "api-reference.md", "env.handle.task_language",
                             "FORBIDDEN APIs", "findings.md", "Generalizable patterns",
                             "flush=True", "BLOCKED.md"):
                self.assertIn(expected, text, f"{condition} lost {expected}")

    def test_no_replacement_numerical_caps(self):
        for condition, text in self.prompts.items():
            for forbidden in ("30 actions", "1 recovery", "4 queries", "15 revisions",
                              "query budget of", "three repairs"):
                self.assertNotIn(forbidden, text, f"{condition} introduced {forbidden}")
            self.assertIn("3 TOTAL retries per seed", text)

    def test_diagnostic_repl_is_retained_and_counted(self):
        for condition, text in self.prompts.items():
            self.assertIn("--phase diagnostic", text)
            self.assertIn("counts as ONE attempt", text)

    def test_world_interface_only_in_b_and_c(self):
        for condition in ("B", "C"):
            text = self.prompts[condition]
            self.assertIn("World Interface", text)
            self.assertIn("no query, action or recovery limits", text)
            self.assertIn("fix_world_program.py", text)
            for leak in ('"action":', "JSON", "15 revisions", "query_budget"):
                self.assertNotIn(leak, text, f"{condition} world docs leaked {leak}")
        self.assertNotIn("World Interface", self.prompts["A"])
        self.assertNotIn("fix_world_program.py", self.prompts["A"])

    def test_strategy_md_only_in_a_and_b(self):
        for condition in ("A", "B"):
            text = self.prompts[condition]
            for name in campaign.STRATEGY_MD:
                self.assertIn(name, text, f"{condition} lost {name}")
        c = self.prompts["C"]
        for name in campaign.STRATEGY_MD:
            self.assertNotIn(name, c, f"C1 must not reference {name}")
        self.assertIn("api-reference.md", c)

    def test_a_and_b_prompts_differ_only_by_the_world_interface(self):
        a = self.prompts["A"].replace("cells/A1", "cells/X")
        b = self.prompts["B"].replace("cells/B1", "cells/X")
        normalize = lambda text: [line.rstrip(" \\") for line in text.splitlines()]
        removed = [line for line in normalize(a) if line not in normalize(b)]
        self.assertEqual(removed, [], "the world condition must only add material")

    def test_coordinator_delegates_exactly_one_worker(self):
        for condition in ("A", "B", "C"):
            case = {"id": f"{condition}1", "condition": condition, "suite": "s", "task": "t",
                    "skill_library_dir": "docs/skills"}
            text = campaign.coordinator_prompt(case, Path("/tmp/worker-prompt.md"))
            self.assertIn("EXACTLY ONE fix-loop worker", text)
            self.assertIn("do not write or debug any robot code", text)
            self.assertIn("never read another cell's outputs", text)
            if condition == "C":
                self.assertIn("no promotion step", text)
            else:
                self.assertIn("THIS cell's", text)


class CredentialIsolation(unittest.TestCase):
    def test_native_settings_reference_the_helper_without_reading_it(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            case_path, case = make_case(root, "B")
            settings = campaign.native_settings(case, Path(case["sim"]), case_path)
            self.assertEqual(settings["apiKeyHelper"], case["api_key_helper"])
            body = json.dumps(settings)
            for leaked in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "sk-"):
                self.assertNotIn(leaked, body)
            self.assertEqual(settings["env"]["ANTHROPIC_BASE_URL"], campaign.ENDPOINT)
            for key in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                        "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                        "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
                self.assertEqual(settings["env"][key], "claude-opus-4-6[1m]")
            self.assertEqual(settings["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "1000000")
            self.assertEqual(settings["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "64000")
            self.assertIn("hooks", settings)

    def test_solver_environment_carries_no_credential_material(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            case_path, case = make_case(root, "A")
            dirty = {"ANTHROPIC_API_KEY": "secret", "OPENAI_API_KEY": "secret",
                     "AWS_SECRET_ACCESS_KEY": "secret", "HF_TOKEN": "secret", "PATH": "/usr/bin"}
            with patch.dict("os.environ", dirty, clear=True):
                env = campaign.native_environment(case, Path(case["sim"]), case_path,
                                                  Path(case["claude_config_dir"]))
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
            self.assertNotIn("HF_TOKEN", env)
            self.assertEqual(env["CLAUDE_CONFIG_DIR"], case["claude_config_dir"])
            self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "0")

    def test_trial_environment_carries_no_credential_material(self):
        with tempfile.TemporaryDirectory() as name:
            case_path, case = make_case(Path(name), "A")
            dirty = {"ANTHROPIC_AUTH_TOKEN": "secret", "MY_CREDENTIAL": "secret",
                     "PATH": "/usr/bin"}
            with patch.dict("os.environ", dirty, clear=True):
                env = protocol.runtime_env(case, Path(case["sim"]))
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
            self.assertNotIn("MY_CREDENTIAL", env)

    def test_native_command_never_routes_through_the_local_model_wrapper(self):
        with tempfile.TemporaryDirectory() as name:
            case_path, case = make_case(Path(name), "A")
            # Settings are passed explicitly because `--setting-sources ""`
            # suppresses file-based discovery, so they are a required argument.
            settings = campaign.native_settings(case, Path(case["sim"]), case_path)
            command = campaign.native_command(case, "hello", settings)
            joined = " ".join(command)
            self.assertNotIn("claude_with_local_model.sh", joined)
            self.assertEqual(command[0], case["claude_bin"])
            self.assertIn("claude-opus-4-6[1m]", command)
            self.assertIn("high", command)
            # Permissions stay enforced: the guard hook must not be bypassed.
            self.assertNotIn("--dangerously-skip-permissions", command)
            self.assertIn("--settings", command)


class OldProtocolUnchanged(unittest.TestCase):
    def test_historical_ledger_still_uses_three_additional_repairs(self):
        import fix_loop_state
        self.assertEqual(fix_loop_state.Stage1State.REPAIR_LIMIT, 3)
        self.assertTrue(hasattr(fix_loop_state.Stage1State, "begin_trial"))

    def test_new_ledger_is_a_separate_module(self):
        import fix_loop_state
        self.assertIsNot(ledger.NativeWorldState, fix_loop_state.Stage1State)
        self.assertEqual(ledger.RETRY_LIMIT, 3)
        self.assertIn("diagnostic", ledger.RETRY_PHASES)
        self.assertNotIn("snapshot", ledger.RETRY_PHASES)

    def test_old_protocol_cli_still_imports(self):
        import native_cc_protocol
        for name in ("load_case", "identity", "run_trial", "finalize", "main"):
            self.assertTrue(hasattr(native_cc_protocol, name), name)

    def test_run_replay_without_stdin_is_unchanged_for_old_callers(self):
        import inspect
        from native_cc_trial_process import run_replay
        signature = inspect.signature(run_replay)
        self.assertIsNone(signature.parameters["stdin_path"].default)


class CampaignChain(unittest.TestCase):
    """init -> solver -> check -> finalize -> heldout, with every call mocked.

    No native binary, no simulator and no model request runs here: `run_native_cc`
    and the outer protocol steps are patched, so only the sequencing, the recorded
    status and the refusal to mislabel an incomplete cell are under test.
    """

    def test_check_passes_only_on_a_complete_cell(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack)
            status = protocol.check(h.case, h.repo, h.state)
            self.assertFalse(status["ready"])
            bundle = h.complete_cell()
            status = protocol.check(h.case, h.repo, h.state)
            self.assertTrue(status["ready"], status["errors"])
            self.assertEqual(status["selected"]["bundle_sha256"],
                             ledger.bundle_identity(bundle))
            self.assertTrue(status["coverage"])

    def test_finalize_records_served_model_and_context_window(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack)
            bundle = h.complete_cell()
            transcript = h.task_dir / "coordinator.stdout.jsonl"
            transcript.write_text(TRANSCRIPT)
            result = protocol.finalize(h.case, h.repo, h.state, [transcript])
            self.assertTrue(result["stage1_complete"])
            self.assertEqual(result["selected_bundle"], bundle)
            self.assertEqual(result["model_served"], {"claude-opus-4-6": 1})
            # Actual served window, not only the configured environment value.
            self.assertEqual(result["model_context_windows"], [1000000])
            self.assertEqual(result["retry_limit"], 3)
            self.assertIn("tested_bundles", result)
            self.assertTrue((h.task_dir / "stage1_result.json").is_file())

    def test_finalize_refuses_a_foreign_served_model(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack)
            h.complete_cell()
            transcript = h.task_dir / "coordinator.stdout.jsonl"
            transcript.write_text(json.dumps(
                {"type": "assistant", "message": {"model": "claude-opus-5"}}) + "\n")
            with self.assertRaisesRegex(ledger.ProtocolError, "provenance mismatch"):
                protocol.finalize(h.case, h.repo, h.state, [transcript])

    def test_finalize_output_satisfies_the_heldout_reader(self):
        import contextlib
        import native_world_heldout as heldout
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            bundle = h.complete_cell()
            transcript = h.task_dir / "coordinator.stdout.jsonl"
            transcript.write_text(TRANSCRIPT)
            protocol.finalize(h.case, h.repo, h.state, [transcript])
            evaluation = Path(h.case["control"]) / "heldout"
            evaluation.mkdir(parents=True, exist_ok=True)
            frozen, kept = heldout.frozen_bundle(h.case, h.task_dir, evaluation)
            self.assertEqual(frozen, bundle)
            self.assertEqual(set(kept), {"policy", "world", "inventory"})
            # The frozen copies are re-verified before every seed, not once.
            heldout.assert_frozen(frozen, kept, 1)
            kept["policy"].write_text(POLICY + "# edited mid-sweep\n")
            with self.assertRaisesRegex(ledger.ProtocolError, "immutable"):
                heldout.assert_frozen(frozen, kept, 2)

    def test_heldout_refuses_a_bundle_that_stage1_never_selected(self):
        import contextlib
        import native_world_heldout as heldout
        with contextlib.ExitStack() as stack:
            h = Harness(stack)
            h.complete_cell()
            transcript = h.task_dir / "coordinator.stdout.jsonl"
            transcript.write_text(TRANSCRIPT)
            protocol.finalize(h.case, h.repo, h.state, [transcript])
            # A post-finalize edit of the frozen path must not be evaluated.
            h.write("fix_code.py", POLICY + "# swapped after freezing\n")
            evaluation = Path(h.case["control"]) / "heldout"
            evaluation.mkdir(parents=True, exist_ok=True)
            with self.assertRaisesRegex(ledger.ProtocolError, "do not match the selected"):
                heldout.frozen_bundle(h.case, h.task_dir, evaluation)

    def test_campaign_runs_init_before_the_solver_and_gates_completion(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            case_path, case = make_case(root, "A")
            order, steps = [], {}
            stack.enter_context(patch.object(campaign, "verify_runtime",
                                             lambda case, repo: None))
            stack.enter_context(patch.object(campaign, "perception_ready",
                                             lambda case: {"8114": 200}))
            stack.enter_context(patch.object(campaign, "probe",
                                             lambda *a, **k: {"exit_code": 0}))
            stack.enter_context(patch.object(
                campaign, "write_prompts",
                lambda case, repo, control: (control / "worker-prompt.md",
                                             control / "coordinator-prompt.md")))
            for name in ("worker-prompt.md", "coordinator-prompt.md"):
                (Path(case["control"]) / name).write_text("prompt\n")

            def fake_protocol(case, repo, env, control, name, *args):
                order.append(name)
                return steps.get(name, {"step": name, "exit_code": 0, "result": {}})

            def fake_native(command, **kwargs):
                order.append("solver")
                Path(kwargs["stdout_path"]).write_text(TRANSCRIPT)
                return 0

            stack.enter_context(patch.object(campaign, "protocol", fake_protocol))
            stack.enter_context(patch.object(campaign, "run_native_cc", fake_native))
            self.assertEqual(campaign.run(case_path), 0)
            # The ledger is initialized before any solver turn can touch it, and
            # the graded steps all follow the solver.
            self.assertEqual(order, ["init", "solver", "check", "finalize", "heldout"])
            state = json.loads((Path(case["control"]) / "campaign_state.json").read_text())
            self.assertEqual(state["status"], "complete")
            self.assertIsNone(state["blocker"])

    def test_campaign_never_reports_completion_when_a_step_fails(self):
        import contextlib
        for failing, expected in (("check", "stage1_incomplete"),
                                  ("finalize", "finalize_failed"),
                                  ("heldout", "heldout_incomplete")):
            with contextlib.ExitStack() as stack:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
                case_path, case = make_case(root, "B")
                stack.enter_context(patch.object(campaign, "verify_runtime",
                                                 lambda case, repo: None))
                stack.enter_context(patch.object(campaign, "perception_ready",
                                                 lambda case: {}))
                stack.enter_context(patch.object(campaign, "probe",
                                                 lambda *a, **k: {"exit_code": 0}))
                stack.enter_context(patch.object(
                    campaign, "write_prompts",
                    lambda case, repo, control: (control / "w.md", control / "c.md")))
                for name in ("w.md", "c.md"):
                    (Path(case["control"]) / name).write_text("prompt\n")
                stack.enter_context(patch.object(
                    campaign, "protocol",
                    lambda case, repo, env, control, name, *args, _f=failing: {
                        "step": name, "exit_code": 1 if name == _f else 0,
                        "result": None, "stderr_tail": "boom"}))
                stack.enter_context(patch.object(
                    campaign, "run_native_cc",
                    lambda command, **kwargs: (
                        Path(kwargs["stdout_path"]).write_text(TRANSCRIPT), 0)[1]))
                self.assertEqual(campaign.run(case_path), 1, failing)
                state = json.loads(
                    (Path(case["control"]) / "campaign_state.json").read_text())
                self.assertEqual(state["status"], expected)
                self.assertTrue(state["blocker"])

    def test_campaign_records_a_solver_failure_as_a_blocker(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            case_path, case = make_case(root, "C")
            stack.enter_context(patch.object(campaign, "verify_runtime",
                                             lambda case, repo: None))
            stack.enter_context(patch.object(campaign, "perception_ready", lambda case: {}))
            stack.enter_context(patch.object(campaign, "probe", lambda *a, **k: {}))
            stack.enter_context(patch.object(
                campaign, "write_prompts",
                lambda case, repo, control: (control / "w.md", control / "c.md")))
            for name in ("w.md", "c.md"):
                (Path(case["control"]) / name).write_text("prompt\n")
            stack.enter_context(patch.object(
                campaign, "protocol",
                lambda *a, **k: {"step": "init", "exit_code": 0, "result": {}}))
            stack.enter_context(patch.object(
                campaign, "run_native_cc",
                lambda command, **kwargs: (
                    Path(kwargs["stdout_path"]).write_text(""), 2)[1]))
            self.assertEqual(campaign.run(case_path), 1)
            state = json.loads((Path(case["control"]) / "campaign_state.json").read_text())
            self.assertEqual(state["status"], "solver_failed")
            self.assertIn("exited 2", state["blocker"])
            # check/finalize/heldout never ran, so no result file was invented.
            self.assertFalse((Path(case["control"]) / "outer_finalize.json").is_file())


class WorldProgramFailures(unittest.TestCase):
    """An authored world bug is retry-spending feedback, not a permanent blocker."""

    def test_invalid_world_program_is_rejected_before_any_process_starts(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            h.snapshot()
            # Missing `assimilate`: a real world module needs all four functions.
            h.write("initial_world_program.py",
                    "def initialize(context):\n    return {}\n")
            with self.assertRaisesRegex(ledger.ProtocolError, "assimilate"):
                h.trial("initial", 52)
            self.assertEqual(h.state.retries_used(52), 0)
            self.assertTrue(h.state.data["rejected"])
            self.assertFalse(h.state.data["rejected"][-1]["spends_retry"])
            # The refused bytes are preserved, not just the path.
            self.assertIn("world", h.state.data["rejected"][-1]["submitted"])

    def test_invalid_inventory_is_rejected_before_any_process_starts(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            h.snapshot()
            h.write("initial_inventory.json", json.dumps(
                [{"id": "bowl", "label": "bowl", "role": "manipulated"},
                 {"id": "cup", "label": "cup", "role": "manipulated"}]))
            with self.assertRaisesRegex(ledger.ProtocolError, "exactly one"):
                h.trial("initial", 52)
            self.assertEqual(h.state.retries_used(52), 0)

    def test_world_program_error_spends_retry_and_is_separated_from_infrastructure(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            h.snapshot()
            record = h.state.begin_trial(
                "initial", 52,
                {"policy": ledger.code_hash(POLICY), "world": ledger.code_hash(WORLD),
                 "inventory": ledger.code_hash(INVENTORY)},
                {"policy": str(h.task_dir / "initial_code.py")})
            h.state.finish_trial(
                record, result=None, exit_code=1, error="",
                world_error={"reason": "predict returned no grasp_check",
                             "child_status": "completed"},
                raw_result={"sandbox_rc": 0, "reward": 1.0, "task_completed": 1})
            self.assertEqual(record["status"], "world_program_error")
            self.assertTrue(record["spends_retry"])
            outcome = h.state.outcome(52)
            self.assertEqual(outcome["retries_used"], 1)
            self.assertTrue(outcome["world_program_errors"])
            # Not an infrastructure blocker, and not a silent success either.
            self.assertFalse(outcome["infrastructure_errors"])
            self.assertFalse(outcome["passed"])
            progress = h.state.progress()
            self.assertTrue(progress["world_program_errors"])
            self.assertFalse(progress["infrastructure_errors"])
            self.assertTrue(outcome["has_initial_evidence"])
            self.assertTrue(outcome["needs_repair"])
            # It remains tested failure evidence, with its raw success separated.
            candidate = h.state.candidates()[record["bundle_sha256"]]
            self.assertEqual(candidate["passes"], [])
            self.assertEqual(candidate["crashes"], 1)

    def test_world_failure_needs_a_completed_child(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            directory = h.task_dir / "probe"
            root = protocol.world_root(directory, 52)
            root.mkdir(parents=True)
            # A killed or timed-out child is infrastructure, not authored code.
            (root / "child_exit.json").write_text(json.dumps({"status": "timeout"}))
            self.assertIsNone(protocol.world_failure(h.case, directory, "initial", 52))
            (root / "child_exit.json").write_text(json.dumps(
                {"status": "completed", "exit_code": 1, "manifest_valid": False}))
            (root / "live_manifest.json").write_text(json.dumps(
                {"status": "error", "error": "assimilation contradicted the verdict"}))
            (root / "live_tape.jsonl").write_text(json.dumps(
                {"event": "program_error", "operation": "assimilate",
                 "error": "KeyError: attachment"}) + "\n")
            detail = protocol.world_failure(h.case, directory, "initial", 52)
            self.assertEqual(detail["reason"], "assimilation contradicted the verdict")
            self.assertEqual(detail["world_program_error_count"], 1)
            self.assertEqual(detail["world_program_events"][0]["operation"], "assimilate")
            # The ordinary condition has no world program to blame.
            self.assertIsNone(protocol.world_failure(
                {"condition": "A"}, directory, "initial", 52))

    def test_nonzero_initialize_error_requires_positive_evidence(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, condition="B")
            directory = h.task_dir / "probe"
            root = protocol.world_root(directory, 53)
            root.mkdir(parents=True)
            receipt = root / "child_exit.json"
            manifest = root / "live_manifest.json"
            tape = root / "live_tape.jsonl"
            receipt.write_text(json.dumps(
                {"status": "nonzero", "exit_code": 1, "manifest_valid": False}))
            manifest.write_text(json.dumps({"status": "failed"}))
            # A service failure or empty tape must not become an authored error.
            tape.write_text(json.dumps({"event": "service_error", "error": "connection refused"}) + "\n")
            self.assertIsNone(protocol.world_failure(h.case, directory, "repair", 53))
            tape.write_text(json.dumps({"event": "program_error", "operation": "initialize",
                                        "error": "initialize exceeded 2 seconds"}) + "\n")
            detail = protocol.world_failure(h.case, directory, "repair", 53)
            self.assertEqual(detail["child_status"], "nonzero")
            self.assertEqual(detail["world_program_events"][0]["operation"], "initialize")
            for status, code in [("timeout", 1), ("killed", -9), ("interrupted", 1),
                                 ("nonzero", -9), ("nonzero", None), ("nonzero", 0)]:
                with self.subTest(status=status, exit_code=code):
                    receipt.write_text(json.dumps({"status": status, "exit_code": code}))
                    self.assertIsNone(protocol.world_failure(h.case, directory, "repair", 53))
            receipt.write_text(json.dumps({"status": "nonzero", "exit_code": 1}))
            manifest.write_text("unreadable JSON")
            self.assertIsNone(protocol.world_failure(h.case, directory, "repair", 53))


class FinalAcceptance(unittest.TestCase):
    def test_world_rejection_overrides_raw_development_success(self):
        import contextlib
        with contextlib.ExitStack() as stack:
            h = Harness(stack, "B")
            h.snapshot()
            h.outcome(52, success=True)
            h.replay.return_value = (1, "")
            stack.enter_context(patch.object(protocol, "world_failure", return_value={
                "reason": "predict omitted grasp_check", "child_status": "completed"}))
            record = h.trial("initial", 52)
            self.assertEqual(record["status"], "world_program_error")
            self.assertEqual(record["raw_result"]["task_completed"], 1)
            self.assertEqual(record["task_completed"], 0)
            self.assertTrue(h.state.outcome(52)["needs_repair"])

    def test_all_three_conditions_evaluate_fifty_frozen_seeds(self):
        import contextlib
        import io
        import native_world_heldout as heldout
        for condition in "ABC":
            with self.subTest(condition=condition), contextlib.ExitStack() as stack:
                h = Harness(stack, condition)
                bundle = h.complete_cell()
                transcript = h.write("transcript.jsonl", TRANSCRIPT)
                protocol.finalize(h.case, h.repo, h.state, [transcript])
                seen = []

                def replay(command, **kwargs):
                    seed = int(command[command.index("--args.trial") + 1])
                    seen.append(seed)
                    directory = kwargs["directory"]
                    self.assertEqual(ledger.code_hash((directory / "code.py").read_text()),
                                     bundle["policy"])
                    if condition != "A" and seed == 17:
                        root = protocol.world_root(directory, seed)
                        root.mkdir(parents=True)
                        (root / "child_exit.json").write_text(json.dumps(
                            {"status": "completed", "exit_code": 0, "manifest_valid": False}))
                        (root / "live_manifest.json").write_text(json.dumps({"status": "failed"}))
                        (root / "live_tape.jsonl").write_text(json.dumps(
                            {"event": "program_error", "operation": "predict", "error": "synthetic"}) + "\n")
                        return 1, ""
                    return 0, ""

                stack.enter_context(patch.object(heldout, "run_replay", replay))
                stack.enter_context(patch.object(heldout, "parse_result", side_effect=lambda root, seed: {
                    "sandbox_rc": 0, "reward": 1.0, "task_completed": 1, "trial_dir": str(root)}))
                stack.enter_context(patch.object(sys, "argv", ["heldout", "--case", str(h.case_path)]))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(heldout.main(), 0)
                self.assertEqual(seen, list(range(1, 51)))
                report = json.loads((Path(h.case["control"]) / "heldout/heldout_result.json").read_text())
                self.assertTrue(report["all_seeds_accounted"])
                self.assertEqual(report["counts"]["success"], 50 if condition == "A" else 49)
                self.assertEqual(report["counts"]["crash"], 0 if condition == "A" else 1)

    def test_documented_fallback_keeps_tested_world_and_reaches_freeze(self):
        import contextlib
        import native_world_heldout as heldout
        with contextlib.ExitStack() as stack:
            h = Harness(stack, "B")
            h.snapshot()
            for seed in h.case["dev_seeds"]:
                h.outcome(seed, success=False, crash=True)
                h.trial("initial", seed)
            for seed in h.case["dev_seeds"]:
                for _ in range(2):
                    h.trial("repair", seed)
                h.write(f"attempts/seed_{seed}_BLOCKED.md",
                        "## Root Cause\nAlgorithmic\n## Details\nSynthetic crash\n## What Was Tried\nThree executions\n")
            h.write("task_analysis.md", "Synthetic scene analysis\n")
            h.write("findings.md", FINDINGS)
            h.write("fix_code.py", "get_observation()\n")
            h.write("fix_world_program.py", WORLD)
            h.write("fix_inventory.json", INVENTORY)
            (h.repo / "outputs/working_codes" /
             f"{h.case['suite']}_{h.case['task']}_fix.py").write_text("get_observation()\n")
            bundle = protocol.selected_bundle(h.case, h.state)
            h.state.select(bundle, "documented observation fallback after all candidates crashed")
            result = protocol.finalize(h.case, h.repo, h.state, [h.write("transcript.jsonl", TRANSCRIPT)])
            self.assertTrue(result["observation_fallback"])
            evaluation = Path(h.case["control"]) / "heldout"
            frozen, _ = heldout.frozen_bundle(h.case, h.task_dir, evaluation)
            self.assertEqual(frozen, bundle)


if __name__ == "__main__":
    unittest.main()
