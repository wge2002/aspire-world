"""Offline contracts for the world-free condition (A) of the matched study.

Synthetic ledgers, responses and manifests only: nothing here is a simulator
run, a model call or a task result. The world conditions' own contracts stay in
tests/test_world_fix_loop.py and must remain unchanged by this file.
"""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM))
from scripts.libero.world_fix_loop_state import (MECHANISM_ADMISSION, NO_MECHANISM_ADMISSION,
                                                 Ledger, MODEL, PROTOCOL, ProtocolError,
                                                 SOURCE_SCHEMAS, put, read, sha, source_schema)
from scripts.libero.world_fix_loop_model import (ORIGIN, extract, ledger_schema, response_schema,
                                                 validate_source)
from scripts.libero import world_fix_loop as runner
from cap.world_model import ordinary_fix_loop_guard as guard
from cap.world_model.ordinary_fix_loop_guard import (FIXED, MODE, RUN_NAME, SCHEMA,
                                                     OrdinaryBudgetGuard, load_config,
                                                     make_config, terminal_outcome)

INVENTORY = [{"id": name, "label": name, "role": role, "confidence": "uncertain",
              "shape_prior": "unknown"}
             for name, role in [("bowl", "manipulated"), ("plate", "target")]]
WORLD = ("def initialize(context):\n return {}\ndef advance(state, step):\n return state\n"
         "def predict(state, step):\n return {}\ndef assimilate(state, evidence):\n return state\n")
WORLD_POLICY = "decision = world_verify()\nprint(decision)\n"
PLAIN_POLICY = ("import numpy as np\n"
                "if recovery_available() and use_recovery():\n"
                " print('retry')\n")
BODY = {"model": MODEL, "max_tokens": PROTOCOL["max_tokens"], "messages": []}
ADAPTER_SHA = sha(SIM / "cap/world_model/ordinary_fix_loop_guard.py")


def plain_response(index=0, source=None):
    data = source or {"policy": PLAIN_POLICY + f"# synthetic version {index}\n"}
    return {"model": MODEL, "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(data)}]}


def world_response(index=0):
    data = {"world": WORLD + f"# synthetic version {index}\n",
            "policy": WORLD_POLICY + f"# synthetic version {index}\n", "inventory": INVENTORY}
    return {"model": MODEL, "stop_reason": "end_turn",
            "content": [{"type": "text", "text": json.dumps(data)}]}


class Fixture(unittest.TestCase):
    """A policy-only campaign: frozen schema plus the named no-world admission."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        self.yaml = self.runtime / "config.yaml"
        self.yaml.write_text("env:\n  cfg:\n    privileged: false\n"
                             "    apis: [FrankaLiberoApiReducedSkillLibraryTraced]\n")
        self.settings = {"runtime_root": str(self.runtime),
                         "runtime_sha256": {"config.yaml": sha(self.yaml)},
                         "yaml": str(self.yaml), "yaml_sha256": sha(self.yaml),
                         "model_origin": ORIGIN,
                         "credential_settings": str(self.base / "settings.json"),
                         "source_schema": "policy_only_v1",
                         "mechanism_admission": NO_MECHANISM_ADMISSION}
        self.ledger = Ledger(self.base / "plain", self.settings)
        # The frozen EGL/GPU environment is exercised by the world tests; here it
        # would only require campaign control inputs this fixture has no reason
        # to synthesize.
        self.environment = patch.object(runner, "execution_environment", return_value={})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def generated(self, index, status="valid"):
        ledger = self.ledger
        ledger.begin_generation(index, BODY)
        attempt = ledger.begin_request(index)
        path = ledger.slot(index) / f"request-{attempt}.response.json"
        data = plain_response(index)
        put(path, data)
        ledger.finish_generation(
            index, status, response_path=path,
            sources=extract(data, ledger_schema(ledger)) if status == "valid" else None,
            error=None if status == "valid" else "synthetic")

    def trial(self, phase, index, status="task_failure", **evidence):
        ledger = self.ledger
        record = ledger.begin_trial(phase, index)
        raw = ledger.trial_dir(phase, index) / "raw.json"
        put(raw, {"synthetic_test_only": True, "status": status})
        ledger.finish_trial(phase, index, {"status": status, "seed": record["seed"], **evidence}, [raw])
        return record


class PolicyOnlyParsing(unittest.TestCase):
    """A's response carries policy (+ optional diagnosis) and nothing else."""

    def test_plain_policy_response_needs_no_world_program_or_inventory(self):
        sources = extract(plain_response(), "policy_only_v1")
        self.assertEqual(set(sources), {"policy"})
        self.assertEqual(sources["policy"], PLAIN_POLICY + "# synthetic version 0\n")
        with_diagnosis = plain_response(source={"policy": PLAIN_POLICY, "diagnosis": "synthetic"})
        self.assertEqual(extract(with_diagnosis, "policy_only_v1"), {"policy": PLAIN_POLICY})

    def test_plain_condition_rejects_world_source_inventory_or_world_verify(self):
        # A fabricated empty world program, an inventory, or a policy reaching for
        # world_verify are all content errors, never silently dropped keys.
        for bad in [{"policy": PLAIN_POLICY, "world": WORLD},
                    {"policy": PLAIN_POLICY, "inventory": INVENTORY},
                    {"policy": PLAIN_POLICY, "world": "", "inventory": []},
                    {"policy": WORLD_POLICY}]:
            with self.subTest(keys=sorted(bad)), self.assertRaises(ValueError):
                extract(plain_response(source=bad), "policy_only_v1")

    def test_world_schema_is_unchanged_and_still_requires_world_verify(self):
        sources = extract(world_response())
        self.assertEqual(set(sources), {"world", "policy", "inventory"})
        self.assertEqual(extract(world_response(), "world_policy_inventory_v1"), sources)
        # The world triple is still required, and a plain policy is invalid there.
        with self.assertRaises(ValueError):
            extract(plain_response(), "world_policy_inventory_v1")
        with self.assertRaises(ValueError):
            validate_source(PLAIN_POLICY, False)

    def test_numeric_and_api_restrictions_still_apply_to_the_plain_policy(self):
        for bad in ["import os\n", "import numpy as np\nnp.load('x')\n",
                    "from numpy import load\nload('x')\n", "env.handle\n",
                    "point_prompt_molmo(1, 2)\n",
                    "getattr(use_recovery, '__globals__')\n"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_source(bad + PLAIN_POLICY, False, world_available=False)
        # The ordinary budget helpers stay legal; they are not world inference.
        validate_source(PLAIN_POLICY, False, world_available=False)

    def test_unknown_response_schema_is_never_a_silent_default(self):
        with self.assertRaises(ProtocolError):
            response_schema("policy_only_v2")
        self.assertFalse(response_schema("policy_only_v1")["world_available"])
        self.assertTrue(response_schema()["world_available"])


class PolicyOnlyLedger(Fixture):
    def test_schema_and_admission_come_from_the_sealed_identity(self):
        self.assertEqual(self.ledger.source_files, {"policy": "policy.py"})
        self.assertEqual(ledger_schema(self.ledger)["required"], ("policy",))
        self.assertEqual(source_schema(self.settings), SOURCE_SCHEMAS["policy_only_v1"])
        # A world campaign built from the same runtime keeps the legacy triple.
        world_ledger = Ledger(self.base / "world", dict(
            self.settings, source_schema="world_policy_inventory_v1",
            mechanism_admission=MECHANISM_ADMISSION))
        self.assertEqual(set(world_ledger.source_files), {"world", "policy", "inventory"})
        self.assertTrue(ledger_schema(world_ledger)["world_available"])

    def test_valid_generation_writes_only_policy_py(self):
        self.generated(0)
        sources = self.ledger.sources(0)
        self.assertEqual(set(sources), {"policy"})
        self.assertEqual(sources["policy"].name, "policy.py")
        self.assertFalse((self.ledger.slot(0) / "world_program.py").exists())
        self.assertFalse((self.ledger.slot(0) / "inventory.json").exists())
        with self.assertRaises(ProtocolError):
            # A world-shaped source set cannot be smuggled into this schema.
            self.ledger.finish_generation(1, "valid", response_path=None,
                                          sources={"world": WORLD, "policy": PLAIN_POLICY})

    def test_freeze_records_the_named_no_world_rule_without_filtering(self):
        for index in range(16):
            self.generated(index)
            self.trial("initial" if index == 0 else "repair", index)
        selection = self.ledger.freeze()
        self.assertEqual(selection["version"], 15)
        # Explicitly "no mechanism to check", distinct from a legacy campaign
        # that predates the gate (which records None).
        self.assertEqual(selection["mechanism_admission"], NO_MECHANISM_ADMISSION)
        self.assertIsNone(selection["mechanism_admission_audit"])
        self.assertEqual(selection["selection_rule"], PROTOCOL["selection"])
        self.assertEqual(self.ledger.selection()["version"], 15)
        summary = self.ledger.summary()
        self.assertEqual(summary["revision_source_changes"], {"policy": 15, "both_unchanged": 0})
        self.assertEqual(summary["development_executions"], 16)

    def test_a_world_campaign_still_requires_real_mechanism_evidence(self):
        # The weaker no-world rule must not become reachable for a world cell.
        gated = Ledger(self.base / "gated", dict(
            self.settings, source_schema="world_policy_inventory_v1",
            mechanism_admission=MECHANISM_ADMISSION))
        for index in range(16):
            gated.begin_generation(index, BODY)
            attempt = gated.begin_request(index)
            path = gated.slot(index) / f"request-{attempt}.response.json"
            put(path, world_response(index))
            gated.finish_generation(index, "valid", response_path=path,
                                    sources=extract(world_response(index)))
            record = gated.begin_trial("initial" if index == 0 else "repair", index)
            raw = gated.trial_dir("initial" if index == 0 else "repair", index) / "raw.json"
            put(raw, {"synthetic_test_only": True})
            gated.finish_trial("initial" if index == 0 else "repair", index,
                               {"status": "task_failure", "seed": record["seed"],
                                "world_reference_established": False,
                                "world_relation_measurement_attempts": 0}, [raw])
        with self.assertRaises(ProtocolError):
            gated.freeze()


class FakeAPI:
    """Minimal reduced-API stand-in: motor calls plus ordinary perception."""

    def __init__(self):
        self.calls = []

    def functions(self):
        return {"goto_pose": lambda *a, **k: self.calls.append("goto_pose"),
                "open_gripper": lambda: self.calls.append("open_gripper"),
                "get_observation": self.get_observation,
                "segment_sam3_text_prompt": lambda *a: [{"score": .9, "mask": [[True]]}]}

    def get_observation(self):
        self.calls.append("get_observation")
        return {"robot_cartesian_pos": [.1, .2, .3, 1, 0, 0, 0, 0]}


class OrdinaryNamespace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.config = {**FIXED, "campaign_root": "/synthetic", "phase": "initial", "index": 0,
                       "identity_sha256": "0" * 64, "generation_sha256": "1" * 64,
                       "policy_sha256": "2" * 64, "output_root": str(self.directory / "out"),
                       "run_name": RUN_NAME, "model_provenance": guard.PROVENANCE}

    def guard(self, name="run"):
        directory = self.directory / name
        directory.mkdir()
        api = FakeAPI()
        instance = OrdinaryBudgetGuard(directory, self.config, {})
        instance.attach(SimpleNamespace(_apis={"one": api}, close=lambda: None))
        return instance, api

    def test_namespace_has_recovery_helpers_and_no_world_verify(self):
        instance, api = self.guard()
        namespace = api.functions()
        self.assertNotIn("world_verify", namespace)
        self.assertEqual(namespace["recovery_available"], instance.recovery_available)
        self.assertEqual(namespace["use_recovery"], instance.use_recovery)
        # Ordinary perception is present and unwrapped.
        self.assertEqual(namespace["get_observation"], api.get_observation)
        self.assertEqual(set(instance._policy_extras()),
                         {"recovery_available", "use_recovery"})

    def test_tracked_recovery_gate_matches_the_world_conditions_contract(self):
        instance, _ = self.guard()
        self.assertTrue(instance.recovery_available())
        self.assertTrue(instance.use_recovery())
        self.assertFalse(instance.recovery_available())
        # A refusal returns False without raising and without spending budget.
        self.assertFalse(instance.use_recovery())
        self.assertEqual(instance._recovery_used, PROTOCOL["max_recovery"])
        events = [e["event"] for e in instance._events]
        self.assertEqual(events, ["recovery_invoked", "recovery_denied"])

    def test_thirty_first_motor_call_is_denied_and_counted_not_executed(self):
        instance, api = self.guard()
        namespace = api.functions()
        for _ in range(PROTOCOL["max_actions"]):
            namespace["goto_pose"]()
        with self.assertRaises(RuntimeError):
            namespace["goto_pose"]()
        instance.complete(task_completed=False, reward=0.0, sandbox_rc=1,
                          trial_dir=str(self.directory))
        instance.close()
        manifest = instance.manifest
        self.assertEqual(manifest["action_count"], PROTOCOL["max_actions"])
        self.assertEqual(manifest["attempted_actions"], PROTOCOL["max_actions"] + 1)
        self.assertEqual(manifest["action_limit_denials"], 1)
        self.assertEqual(api.calls.count("goto_pose"), PROTOCOL["max_actions"])
        denial = next(e for e in instance._events if e["event"] == "action_limit_denied")
        self.assertEqual(denial["action_count"], PROTOCOL["max_actions"])
        self.assertEqual(denial["attempted_actions"], PROTOCOL["max_actions"] + 1)
        # The refused call is a counted budget failure, not a broken runtime: the
        # guard itself records no broker error for it.
        self.assertNotIn("broker_errors", manifest)
        self.assertTrue((instance.directory / "ordinary_manifest.json").is_file())

    def test_close_restores_the_api_and_seals_the_tape(self):
        instance, api = self.guard()
        # While attached, the wrapper lives on the instance and shadows the class
        # method; close() must remove it so the API is left exactly as found.
        self.assertIn("functions", vars(api))
        instance.complete(task_completed=True, reward=1.0, sandbox_rc=0,
                          trial_dir=str(self.directory))
        instance.close()
        self.assertNotIn("functions", vars(api))
        self.assertNotIn("recovery_available", api.functions())
        manifest = read(instance.directory / "ordinary_manifest.json")
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["tape_sha256"],
                         guard._sha256(instance.directory / "ordinary_tape.jsonl"))


class OrdinaryAdapter(Fixture):
    def args(self, **overrides):
        base = dict(api_key=None, interactive=False, world_model_config=None,
                    ordinary_budget_config=None, record_video=True, debug=False,
                    replay_code=self.ledger.sources(0)["policy"], suite=runner.SUITE,
                    task=runner.TASK, trial=51, config=self.yaml,
                    output_dir=self.ledger.trial_dir("initial", 0) / "ordinary")
        return SimpleNamespace(**{**base, **overrides})

    def reserved(self):
        self.generated(0)
        self.ledger.begin_trial("initial", 0)
        return make_config(self.ledger, "initial", 0)

    def test_config_admits_only_the_exact_reserved_policy_seed_and_paths(self):
        config = self.reserved()
        self.assertEqual(set(config) - set(FIXED),
                         {"campaign_root", "phase", "index", "identity_sha256",
                          "generation_sha256", "policy_sha256", "output_root",
                          "run_name", "model_provenance"})
        self.assertNotIn("world_program", config)
        self.assertNotIn("scene_inventory", config)
        self.assertNotIn("query_budget", config)
        self.assertEqual(load_config(self.args(), config)[0], config)
        for key, value in [("policy_sha256", "0" * 64), ("model_provenance", {}),
                           ("phase", "heldout"), ("max_actions", 31),
                           ("output_root", str(self.base / "outside"))]:
            with self.subTest(key=key), self.assertRaises((ValueError, FileNotFoundError,
                                                           ProtocolError)):
                load_config(self.args(), dict(config, **{key: value}))
        with self.assertRaises(ValueError):
            load_config(self.args(trial=1), config)

    def test_a_world_config_alongside_the_ordinary_one_is_refused(self):
        config = self.reserved()
        with self.assertRaises(ValueError):
            load_config(self.args(world_model_config="/synthetic/world.json"), config)

    def test_world_schema_campaigns_cannot_build_an_ordinary_config(self):
        world_ledger = Ledger(self.base / "world", dict(
            self.settings, source_schema="world_policy_inventory_v1",
            mechanism_admission=MECHANISM_ADMISSION))
        world_ledger.begin_generation(0, BODY)
        attempt = world_ledger.begin_request(0)
        path = world_ledger.slot(0) / f"request-{attempt}.response.json"
        put(path, world_response())
        world_ledger.finish_generation(0, "valid", response_path=path,
                                       sources=extract(world_response()))
        world_ledger.begin_trial("initial", 0)
        with self.assertRaises(ValueError):
            make_config(world_ledger, "initial", 0)

    def test_replay_entry_point_rejects_both_condition_flags(self):
        replay = runner.SIM / "scripts/libero/replay_trial.py"
        text = replay.read_text()
        self.assertIn("ordinary_budget_config: str | None = None", text)
        self.assertIn("opus46-ordinary-fix-loop", text)
        self.assertIn("not both", text)

    def test_ordinary_replay_launches_the_world_free_child_command(self):
        self.generated(0)
        commands = []
        with patch.object(runner, "execute",
                          side_effect=lambda cmd, *a, **k: commands.append(cmd)):
            outcome = runner.replay_ordinary(self.ledger, "initial", 0)
        directory = self.ledger.trial_dir("initial", 0)
        self.assertEqual(len(commands), 1)
        self.assertIn("--args.ordinary-budget-config", commands[0])
        self.assertIn(str(directory / "config.json"), commands[0])
        self.assertNotIn("--args.world-model-config", commands[0])
        self.assertEqual(read(directory / "config.json"),
                         make_config(self.ledger, "initial", 0))
        self.assertTrue((directory / "frozen/shared_policy.py").is_file())
        self.assertFalse((directory / "frozen/world_program.py").exists())
        # No child terminal: an absent simulator result is infrastructure, and no
        # success or task-failure label is invented for it.
        self.assertEqual(outcome["status"], "infrastructure_error")
        self.assertFalse(outcome["valid"])

    def test_resume_refuses_a_live_reservation_even_with_a_running_child_receipt(self):
        # _launch_child writes child_exit.json with status="running" before it
        # waits, so an interrupted-then-resumed coordinator sees both a process
        # record and a receipt. A still-alive trial must be refused, never recorded
        # as an infrastructure error while it is still doing real work.
        self.generated(0)
        directory = self.ledger.trial_dir("initial", 0)
        child = directory / "evidence" / RUN_NAME / "seed_51"

        def launch(*a, **k):
            put(child / "child_exit.json", {"status": "running", "exit_code": None})
            put(directory / "process.json", {"pid": 4242, "starttime": "777"})

        with patch.object(runner, "execute", side_effect=launch), \
             patch.object(runner, "process_identity", return_value="777"), \
             self.assertRaises(ProtocolError):
            runner.replay_ordinary(self.ledger, "initial", 0)
        self.assertFalse((directory / "result.json").exists())
        # Once the process is gone, the same reservation resolves without a rerun.
        with patch.object(runner, "process_identity", return_value=None), \
             patch.object(runner, "execute", side_effect=AssertionError("no rerun")):
            outcome = runner.replay_ordinary(self.ledger, "initial", 0)
        self.assertEqual(outcome["status"], "infrastructure_error")

    def test_world_replay_resume_also_refuses_a_live_reserved_trial(self):
        # Same regression on the world path, which shares _finish_replay's rule.
        world_ledger = Ledger(self.base / "worldresume", dict(
            self.settings, source_schema="world_policy_inventory_v1",
            mechanism_admission=MECHANISM_ADMISSION))
        world_ledger.begin_generation(0, BODY)
        attempt = world_ledger.begin_request(0)
        path = world_ledger.slot(0) / f"request-{attempt}.response.json"
        put(path, world_response())
        world_ledger.finish_generation(0, "valid", response_path=path,
                                       sources=extract(world_response()))
        directory = world_ledger.trial_dir("initial", 0)
        live = directory / "evidence/world/seed_51"

        def launch(*a, **k):
            put(live / "child_exit.json", {"status": "running", "exit_code": None})
            put(directory / "process.json", {"pid": 4243, "starttime": "778"})

        with patch.object(runner, "execute", side_effect=launch), \
             patch.object(runner, "process_identity", return_value="778"), \
             self.assertRaises(ProtocolError):
            runner.replay(world_ledger, "initial", 0)
        self.assertFalse((directory / "result.json").exists())

    def test_recorded_child_terminal_is_classified_not_refabricated(self):
        self.generated(0)
        directory = self.ledger.trial_dir("initial", 0)
        child = directory / "evidence" / RUN_NAME / "seed_51"
        labelled = {"status": "task_failure", "valid": True, "task_completed": False,
                    "sandbox_rc": 0, "ordinary_action_count": 7}
        with patch.object(runner, "execute",
                          side_effect=lambda *a, **k: put(child / "child_exit.json",
                                                          {"status": "completed"})), \
             patch.object(guard, "terminal_outcome", return_value=dict(labelled)):
            outcome = runner.replay_ordinary(self.ledger, "initial", 0)
        self.assertEqual(outcome["status"], "task_failure")
        self.assertEqual((outcome["phase"], outcome["seed"], outcome["version"]),
                         ("initial", 51, 0))
        self.assertEqual(self.ledger.trial("initial", 0)["ordinary_action_count"], 7)

    def evidence(self, config, directory, tape=(), **overrides):
        """Write the raw child inputs, tape and manifest a real trial would leave."""
        (directory / "frozen_policy.py").write_bytes(
            self.ledger.sources(0)["policy"].read_bytes())
        (directory / "source_config.yaml").write_bytes(self.yaml.read_bytes())
        (directory / "ordinary_config.json").write_text(json.dumps(config))
        (directory / "summary.txt").write_text("synthetic test terminal\n")
        (directory / "ordinary_tape.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in tape))
        manifest = {"schema_version": SCHEMA, "mode": MODE,
                    "model_provenance": guard.PROVENANCE,
                    "identity_sha256": self.ledger.identity_sha,
                    "generation_sha256": config["generation_sha256"],
                    "phase": "initial", "version": 0, "world_model_present": False,
                    "adapter_sha256": ADAPTER_SHA, "status": "complete",
                    "trial_result": {"sandbox_rc": 0},
                    "policy_sha256": guard._sha256(directory / "frozen_policy.py"),
                    "ordinary_config_sha256": guard._sha256(directory / "ordinary_config.json"),
                    "yaml_sha256": guard._sha256(directory / "source_config.yaml"),
                    "tape_sha256": guard._sha256(directory / "ordinary_tape.jsonl"),
                    "events_count": len(tape),
                    "action_count": 3, "attempted_actions": 3,
                    "action_limit_denials": 0, "recovery_used": 0, "frame_count": 4}
        manifest.update(overrides)
        (directory / "ordinary_manifest.json").write_text(json.dumps(manifest))
        return manifest

    def classify(self, directory, receipt={"status": "completed"}, **artifact):
        fake = {"valid": True, "sandbox_rc": 0, "task_completed": False,
                "trial_dir": str(directory), **artifact}
        with patch("scripts.libero.paired_bowl_supervisor.artifacts", return_value=fake):
            return terminal_outcome(directory, self.ledger, "initial", 0, receipt)

    def test_missing_terminal_is_infrastructure_not_program_failure(self):
        self.reserved()
        directory = self.ledger.trial_dir("initial", 0)
        outcome = terminal_outcome(directory, self.ledger, "initial", 0, {"status": "completed"})
        self.assertEqual(outcome["status"], "infrastructure_error")
        self.assertFalse(outcome["valid"])
        self.assertIsNone(outcome["task_completed"])

    def test_denied_action_is_a_counted_program_failure_with_the_cap_intact(self):
        config = self.reserved()
        directory = self.ledger.trial_dir("initial", 0)
        self.evidence(config, directory,
                      tape=[{"event": "action_limit_denied", "action": "goto_pose"}],
                      action_count=PROTOCOL["max_actions"],
                      attempted_actions=PROTOCOL["max_actions"] + 1,
                      action_limit_denials=1, trial_result={"sandbox_rc": 1})
        outcome = self.classify(directory, sandbox_rc=1)
        self.assertEqual(outcome["status"], "program_error")
        self.assertEqual(outcome["ordinary_action_count"], PROTOCOL["max_actions"])
        self.assertEqual(outcome["ordinary_action_limit_denials"], 1)
        self.assertFalse(outcome["world_model_present"])

    def test_success_and_task_failure_need_a_completed_child_and_matching_manifest(self):
        config = self.reserved()
        directory = self.ledger.trial_dir("initial", 0)
        self.evidence(config, directory, tape=[{"event": "trial_reset"}])
        self.assertEqual(self.classify(directory, task_completed=True)["status"], "success")
        self.assertEqual(self.classify(directory)["status"], "task_failure")
        # A watchdog kill is never a task label.
        timed_out = self.classify(directory, receipt={"status": "timeout"},
                                  task_completed=True)
        self.assertEqual(timed_out["status"], "infrastructure_error")
        self.assertEqual(timed_out["validation_error"], "child did not complete")

    def test_a_missing_or_altered_ordinary_tape_cannot_be_a_valid_terminal(self):
        config = self.reserved()
        directory = self.ledger.trial_dir("initial", 0)
        tape = [{"event": "recovery_invoked", "recovery_number": 1},
                {"event": "action_limit_denied", "action": "goto_pose"}]
        self.evidence(config, directory, tape=tape, recovery_used=1, action_limit_denials=1)
        self.assertEqual(self.classify(directory)["status"], "task_failure")
        path = directory / "ordinary_tape.jsonl"
        original = path.read_bytes()
        for name, mutate in [
                ("missing", lambda: path.unlink()),
                ("truncated", lambda: path.write_text(json.dumps(tape[0]) + "\n")),
                ("rewritten", lambda: path.write_text(
                    "".join(json.dumps(e) + "\n" for e in tape) + '{"event":"trial_reset"}\n'))]:
            with self.subTest(name=name):
                mutate()
                outcome = self.classify(directory)
                self.assertEqual(outcome["status"], "infrastructure_error")
                self.assertIn("validation_error", outcome)
                path.write_bytes(original)

    def test_tape_must_corroborate_recorded_denials_and_recovery(self):
        config = self.reserved()
        directory = self.ledger.trial_dir("initial", 0)
        # Manifest counts that the raw tape does not support are rejected, so a
        # denial or recovery cannot be claimed (or hidden) after the fact.
        self.evidence(config, directory, tape=[{"event": "trial_reset"}],
                      action_limit_denials=1)
        outcome = self.classify(directory)
        self.assertEqual(outcome["status"], "infrastructure_error")
        self.assertEqual(outcome["validation_error"],
                         "recorded motor denials differ from the tape")
        self.evidence(config, directory, tape=[{"event": "trial_reset"}], recovery_used=1)
        self.assertEqual(self.classify(directory)["validation_error"],
                         "recorded recovery usage differs from the tape")

    def test_budget_or_identity_violations_stay_infrastructure_errors(self):
        config = self.reserved()
        directory = self.ledger.trial_dir("initial", 0)
        for name, overrides in [("over_cap", {"action_count": PROTOCOL["max_actions"] + 1}),
                                ("over_recovery", {"recovery_used": 2}),
                                ("claims_world", {"world_model_present": True}),
                                ("wrong_adapter", {"adapter_sha256": "0" * 64}),
                                ("wrong_policy_hash", {"policy_sha256": "0" * 64}),
                                ("incomplete", {"status": "failed"}),
                                ("no_terminal", {"trial_result": {}})]:
            with self.subTest(name=name):
                self.evidence(config, directory, tape=[{"event": "trial_reset"}], **overrides)
                outcome = self.classify(directory)
                self.assertEqual(outcome["status"], "infrastructure_error")
                self.assertIn("validation_error", outcome)


if __name__ == "__main__":
    unittest.main()
