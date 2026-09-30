"""Offline contracts for the matched A/B/C conditions of the bowl study.

Synthetic ledgers and snapshots only: no model call, no simulator launch, no task
result. What is checked here is what actually differs between the three
conditions - the sealed identity, the frozen control inputs, the request text and
the execution device - not any experimental outcome.
"""
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM))
from scripts.libero.world_fix_loop_state import (CONDITIONS, MECHANISM_ADMISSION,
                                                 NO_MECHANISM_ADMISSION, Ledger, MODEL, PROTOCOL,
                                                 ProtocolError, condition, put, read, sha)
from scripts.libero.world_fix_loop_model import extract, ledger_schema
from scripts.libero import world_fix_loop as runner

BODY = {"model": MODEL, "max_tokens": PROTOCOL["max_tokens"], "messages": []}
PLAIN_POLICY = "import numpy as np\nprint(recovery_available())\n"
WORLD = ("def initialize(context):\n return {}\ndef advance(state, step):\n return state\n"
         "def predict(state, step):\n return {}\ndef assimilate(state, evidence):\n return state\n")
WORLD_POLICY = "decision = world_verify()\nprint(decision)\n"
INVENTORY = [{"id": name, "label": name, "role": role, "confidence": "uncertain",
              "shape_prior": "unknown"}
             for name, role in [("bowl", "manipulated"), ("plate", "target")]]
SKILLS = ["localize.md", "grasp.md", "transport.md", "manipulation.md"]


class PristineDocuments(unittest.TestCase):
    """The A/B input set is the recorded commit's bytes, or it is an error."""

    def test_the_four_recorded_documents_validate_against_their_manifest(self):
        documents = runner.shared_skill_documents()
        expected = read(runner.SKILL_EXPECTED)
        self.assertEqual([name for name, _ in documents], [e["filename"] for e in expected["skills"]])
        self.assertEqual(tuple(name for name, _ in documents), runner.SKILL_FILENAMES)
        self.assertEqual(sorted(runner.SKILL_FILENAMES), sorted(SKILLS))
        self.assertEqual(sum(len(data.decode()) for _, data in documents),
                         expected["total_characters"])
        self.assertEqual(len(expected["commit"]), 40)

    def test_an_edited_or_incomplete_document_set_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / "pristine-skills"
            staged.mkdir()
            expected = read(runner.SKILL_EXPECTED)
            for entry in expected["skills"]:
                shutil.copy(runner.SKILL_SOURCE / entry["filename"], staged / entry["filename"])
            manifest = Path(tmp) / "skills-expected.json"
            manifest.write_text(json.dumps(expected))
            with patch.object(runner, "SKILL_SOURCE", staged), \
                 patch.object(runner, "SKILL_EXPECTED", manifest):
                runner.shared_skill_documents()
                # One appended byte in one document is detected by hash, before any
                # campaign can freeze it as a control input.
                target = staged / "grasp.md"
                original = target.read_bytes()
                target.write_bytes(original + b"# edited\n")
                with self.assertRaises(ProtocolError):
                    runner.shared_skill_documents()
                target.write_bytes(original)
                # A short set is refused even when every present file matches, and
                # so is a reordered or renamed one: the four names are fixed.
                for bad in [expected["skills"][:3], list(reversed(expected["skills"]))]:
                    manifest.write_text(json.dumps(dict(expected, skills=bad)))
                    with self.assertRaises(ProtocolError):
                        runner.shared_skill_documents()


class SealedCondition(unittest.TestCase):
    """Condition, repeat, schema and admission are one frozen bundle."""

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
                         "model_origin": "https://service.example",
                         "credential_settings": str(self.base / "settings.json")}

    def cell(self, name, repeat=1, **overrides):
        bundle = CONDITIONS[name]
        return {**self.settings, "condition": name, "repeat": repeat, "cell": name + str(repeat),
                "source_schema": bundle["source_schema"],
                "mechanism_admission": bundle["mechanism_admission"], **overrides}

    def test_each_condition_resolves_to_its_own_schema_and_admission(self):
        for name, world, md, schema, rule in [
                ("A", False, True, "policy_only_v1", NO_MECHANISM_ADMISSION),
                ("B", True, True, "world_policy_inventory_v1", MECHANISM_ADMISSION),
                ("C", True, False, "world_policy_inventory_v1", MECHANISM_ADMISSION)]:
            with self.subTest(condition=name):
                ledger = Ledger(self.base / ("cell" + name), self.cell(name))
                self.assertEqual(ledger.condition["condition"], name)
                self.assertEqual(ledger.condition["cell"], name + "1")
                self.assertEqual(ledger.condition["world_model"], world)
                self.assertEqual(ledger.condition["shared_skill_md"], md)
                self.assertEqual(ledger.identity["settings"]["source_schema"], schema)
                self.assertEqual(ledger.identity["settings"]["mechanism_admission"], rule)
                self.assertEqual(ledger_schema(ledger)["world_available"], world)
                # The runner reaches the world path only where a world exists.
                self.assertIs(runner.condition_replay(ledger),
                              runner.replay if world else runner.replay_ordinary)

    def test_a_mismatched_condition_schema_or_admission_never_opens(self):
        for name, overrides in [
                ("A", {"source_schema": "world_policy_inventory_v1"}),
                ("A", {"mechanism_admission": MECHANISM_ADMISSION}),
                ("B", {"source_schema": "policy_only_v1"}),
                ("B", {"mechanism_admission": NO_MECHANISM_ADMISSION}),
                ("C", {"mechanism_admission": NO_MECHANISM_ADMISSION})]:
            with self.subTest(condition=name, bad=sorted(overrides)), \
                 self.assertRaises(ProtocolError):
                Ledger(self.base / ("bad" + name + "".join(sorted(overrides))),
                       self.cell(name, **overrides))

    def test_condition_and_repeat_must_be_a_valid_labelled_pair(self):
        for bad in [{"condition": "D"}, {"condition": "a"}, {"condition": 1},
                    {"repeat": 0}, {"repeat": "1"}, {"repeat": True}, {"repeat": None},
                    {"cell": "A2"}, {"cell": "A"}, {"cell": None}]:
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                condition(self.cell("A", **bad))
        # A repeat id without a condition stays the legacy identity: the study's
        # keys are only meaningful together, and absence is not a silent default.
        self.assertIsNone(condition(dict(self.settings)))
        self.assertEqual(condition(self.cell("A", repeat=2))["cell"], "A2")

    def test_a_sealed_condition_cannot_be_switched_after_the_fact(self):
        root = self.base / "resealed"
        Ledger(root, self.cell("A"))
        body = read(root / "identity.json")
        body["settings"]["condition"] = "B"
        (root / "identity.json").write_text(json.dumps(body))
        (root / "identity.sha256").write_text(sha(root / "identity.json") + "\n")
        # Even with a correctly recomputed seal, the triple no longer agrees.
        with self.assertRaises(ProtocolError):
            Ledger(root)

    def test_legacy_identities_keep_world_behaviour_and_gpu_seven(self):
        ledger = Ledger(self.base / "legacy", dict(self.settings))
        self.assertIsNone(ledger.condition)
        self.assertEqual(set(ledger.source_files), {"world", "policy", "inventory"})
        self.assertIs(runner.condition_replay(ledger), runner.replay)
        vendor = self.base / "legacy/control/nvidia-egl-vendor.json"
        put(vendor, {"file_format_version": "1.0.0", "ICD": {"library_path": "/x/libEGL.so"}})
        sealed = Ledger(self.base / "legacyenv",
                        dict(self.settings, inputs_sha256={"control/nvidia-egl-vendor.json": sha(vendor)}))
        shutil.copytree(self.base / "legacy/control", self.base / "legacyenv/control")
        env = runner.execution_environment(sealed)
        self.assertEqual((env["CUDA_VISIBLE_DEVICES"], env["MUJOCO_EGL_DEVICE_ID"]), ("7", "7"))


class PreparedCells(unittest.TestCase):
    """prepare() freezes the condition's documents, contract and device."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.credentials = self.base / "settings.json"
        self.credentials.write_text(json.dumps({"apiKeyHelper": "synthetic"}))

    def prepared(self, name, **kwargs):
        return runner.prepare(self.base / ("cell_" + str(name)), self.credentials,
                              condition=name, repeat=kwargs.pop("repeat", 1), **kwargs)

    def test_a_and_b_freeze_the_exact_documents_and_c_receives_none(self):
        expected = {entry["filename"]: entry["sha256"] for entry in read(runner.SKILL_EXPECTED)["skills"]}
        for name, has_md in [("A", True), ("B", True), ("C", False)]:
            with self.subTest(condition=name):
                ledger = self.prepared(name)
                skills = ledger.root / "control/skills"
                self.assertEqual(skills.is_dir(), has_md)
                inputs = ledger.identity["settings"]["inputs_sha256"]
                for filename, digest in expected.items():
                    key = "control/skills/" + filename
                    self.assertEqual(key in inputs, has_md)
                    if has_md:
                        # Byte-identical to the recorded commit, and covered by the
                        # sealed input hashes rather than only present on disk.
                        self.assertEqual(sha(skills / filename), digest)
                        self.assertEqual(inputs[key], digest)
                # Exactly one model-facing contract per condition.
                world = CONDITIONS[name]["world_model"]
                self.assertEqual((ledger.root / "control/world-interface.md").exists(), world)
                self.assertEqual((ledger.root / "control/policy-interface.md").exists(), not world)
                ledger.verify_refs(inputs)

    def test_new_cells_seal_physical_gpu_zero_and_override_the_legacy_seven(self):
        ledger = self.prepared("A")
        settings = ledger.identity["settings"]
        self.assertEqual((settings["gpu"], settings["egl_device_id"]), ("0", 0))
        env = runner.execution_environment(ledger)
        # child_environment() hardcodes 7; the sealed value must win.
        self.assertEqual((env["CUDA_VISIBLE_DEVICES"], env["MUJOCO_EGL_DEVICE_ID"]), ("0", "0"))
        self.assertEqual(env["__EGL_VENDOR_LIBRARY_FILENAMES"],
                         str(ledger.root / "control/nvidia-egl-vendor.json"))
        explicit = self.prepared("B", gpu=3, egl_device_id=3)
        self.assertEqual(runner.execution_environment(explicit)["CUDA_VISIBLE_DEVICES"], "3")
        # A caller naming no condition keeps the historical device and identity.
        legacy = runner.prepare(self.base / "legacy", self.credentials)
        self.assertIsNone(legacy.condition)
        self.assertEqual(legacy.identity["settings"]["gpu"], "7")
        self.assertEqual(runner.execution_environment(legacy)["MUJOCO_EGL_DEVICE_ID"], "7")

    def test_invalid_condition_repeat_or_device_is_refused_before_any_campaign(self):
        for kwargs in [{"condition": "D", "repeat": 1}, {"condition": "A"},
                       {"repeat": 1}, {"condition": "A", "repeat": 0},
                       {"condition": "A", "repeat": 1, "gpu": "gpu0"},
                       {"condition": "A", "repeat": 1, "egl_device_id": -1},
                       {"condition": "A", "repeat": 1, "egl_device_id": "0"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises((ProtocolError, KeyError)):
                runner.prepare(self.base / ("refused" + str(sorted(kwargs.items()))),
                               self.credentials, **kwargs)


class ConditionRequests(unittest.TestCase):
    """What each condition's model request actually contains."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.credentials = self.base / "settings.json"
        self.credentials.write_text(json.dumps({"apiKeyHelper": "synthetic"}))

    def cell(self, name):
        ledger = runner.prepare(self.base / ("cell_" + name), self.credentials,
                                condition=name, repeat=1)
        directory = ledger.root / "snapshot"
        for image in ["scene_snapshot.jpg", "scene_snapshot_wrist.jpg"]:
            put(directory / image, b"synthetic-jpeg-bytes")
        put(directory / "result.json",
            {"status": "complete", "valid": True, "sandbox_rc": 0,
             "task_language": "put the bowl on the plate",
             "evidence": ledger.refs([directory / "scene_snapshot.jpg",
                                      directory / "scene_snapshot_wrist.jpg"])})
        return ledger

    def text(self, body):
        return "\n".join(c["text"] for c in body["messages"][0]["content"] if c["type"] == "text")

    def generated(self, ledger, index, world):
        data = ({"world": WORLD, "policy": WORLD_POLICY, "inventory": INVENTORY} if world
                else {"policy": PLAIN_POLICY})
        response = {"model": MODEL, "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": json.dumps(data)}]}
        ledger.begin_generation(index, BODY)
        attempt = ledger.begin_request(index)
        path = ledger.slot(index) / f"request-{attempt}.response.json"
        put(path, response)
        ledger.finish_generation(index, "valid", response_path=path,
                                 sources=extract(response, ledger_schema(ledger)))

    def trial(self, ledger, phase, index, **evidence):
        record = ledger.begin_trial(phase, index)
        raw = ledger.trial_dir(phase, index) / "raw.json"
        put(raw, {"synthetic_test_only": True})
        ledger.finish_trial(phase, index, {"status": "task_failure", "seed": record["seed"],
                                           **evidence}, [raw])

    def test_the_shared_documents_appear_in_full_for_a_and_b_only(self):
        documents = {name: data.decode() for name, data in runner.shared_skill_documents()}
        for name, has_md in [("A", True), ("B", True), ("C", False)]:
            with self.subTest(condition=name):
                text = self.text(runner.build_request(self.cell(name), 0))
                for filename, content in documents.items():
                    # The whole document, not a summary or an excerpt.
                    self.assertEqual(content in text, has_md)
                    self.assertEqual(("document: " + filename) in text, has_md)

    def test_a_receives_the_ordinary_contract_and_no_world_interface(self):
        plain = " ".join(self.text(runner.build_request(self.cell("A"), 0)).split())
        self.assertIn("This condition has no world model.", plain)
        self.assertIn("Produce a complete policy JSON response.", plain)
        # A's contract names the world only to state its absence; it never asks
        # for a world program, an inventory, a prediction or a verify call.
        for absent in ["world_verify()", "request_query", "query_purpose",
                       "def initialize(context)", "def assimilate(state, evidence)",
                       "grasp_check", "Produce a complete world/policy/inventory",
                       "Return one JSON object with `world`"]:
            with self.subTest(absent=absent):
                self.assertNotIn(absent, plain)
        self.assertIn("no world program, scene inventory", plain)
        # The recovery contract and the shared budgets are still stated.
        for present in ["recovery_available()", "use_recovery()", "30 motor API calls",
                        "1 recovery, 900 seconds", "fifteen revisions",
                        "fifty independent executions", "put the bowl on the plate"]:
            with self.subTest(present=present):
                self.assertIn(present, plain)
        # No world query budget is offered to A.
        self.assertNotIn("4 attempted additional", plain)

    def test_b_and_c_keep_the_existing_world_interface(self):
        for name in ["B", "C"]:
            with self.subTest(condition=name):
                text = " ".join(self.text(runner.build_request(self.cell(name), 0)).split())
                self.assertIn("Produce a complete world/policy/inventory JSON response.", text)
                for present in ["world_verify", "request_query", "assimilate",
                                "4 attempted additional world queries"]:
                    self.assertIn(present, text)

    def test_a_feedback_omits_world_fields_instead_of_sending_them_as_null(self):
        ledger = self.cell("A")
        self.generated(ledger, 0, world=False)
        self.trial(ledger, "initial", 0, ordinary_action_count=7,
                   ordinary_attempted_actions=7, ordinary_action_limit_denials=0,
                   ordinary_recovery_used=1)
        text = self.text(runner.build_request(ledger, 1))
        summaries = json.loads(text.split("Accumulated development outcomes (all versions retained):\n")[1]
                               .split("\n\n")[0])
        self.assertEqual(len(summaries), 1)
        item = summaries[0]
        self.assertEqual(set(item) & set(runner.WORLD_SUMMARY_FIELDS), set())
        self.assertEqual(item["ordinary_action_count"], 7)
        self.assertEqual(item["ordinary_recovery_used"], 1)
        self.assertEqual(set(item["source_hashes"]), {"policy"})
        self.assertIn("revision 1 of 15", text)
        # No empty world-evidence section is fabricated for a world-free trial.
        self.assertNotIn("public world evidence", text)

    def test_world_cells_still_receive_the_mechanism_evidence_fields(self):
        ledger = self.cell("C")
        self.generated(ledger, 0, world=True)
        self.trial(ledger, "initial", 0, world_reference_established=True,
                   world_relation_measurement_attempts=1)
        text = self.text(runner.build_request(ledger, 1))
        summaries = json.loads(text.split("Accumulated development outcomes (all versions retained):\n")[1]
                               .split("\n\n")[0])
        self.assertTrue(summaries[0]["world_reference_established"])
        self.assertEqual(summaries[0]["world_relation_measurement_attempts"], 1)
        self.assertEqual(set(summaries[0]) & set(runner.ORDINARY_SUMMARY_FIELDS), set())
        self.assertEqual(set(summaries[0]["source_hashes"]), {"world", "policy", "inventory"})

    def test_a_tampered_document_blocks_the_request_it_would_have_entered(self):
        ledger = self.cell("B")
        runner.build_request(ledger, 0)
        target = ledger.root / "control/skills/localize.md"
        target.write_bytes(target.read_bytes() + b"# injected advice\n")
        with self.assertRaises(ProtocolError):
            runner.build_request(ledger, 0)

    def test_the_model_and_token_budget_are_unchanged_in_every_condition(self):
        for name in sorted(CONDITIONS):
            with self.subTest(condition=name):
                body = runner.build_request(self.cell(name), 0)
                self.assertEqual(body["model"], MODEL)
                self.assertEqual(body["max_tokens"], PROTOCOL["max_tokens"])
                self.assertFalse(body["stream"])
                self.assertEqual(set(body), {"model", "max_tokens", "stream", "messages"})
                images = [c for c in body["messages"][0]["content"] if c["type"] == "image"]
                self.assertEqual(len(images), 2)


class ConditionRunnerSelection(unittest.TestCase):
    """campaign() picks the sealed condition's replay unless one is injected."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.credentials = self.base / "settings.json"
        self.credentials.write_text(json.dumps({"apiKeyHelper": "synthetic"}))

    def stop(self, _ledger):
        raise ProtocolError("synthetic stop before any execution")

    def test_campaign_selects_ordinary_replay_for_a_and_world_replay_for_b(self):
        for name, expected in [("A", runner.replay_ordinary), ("B", runner.replay),
                               ("C", runner.replay)]:
            with self.subTest(condition=name):
                ledger = runner.prepare(self.base / ("cell_" + name), self.credentials,
                                        condition=name, repeat=1)
                real, chosen = runner.condition_replay, []

                def record(target):
                    chosen.append(real(target))
                    return chosen[-1]

                with patch.object(runner, "condition_replay", side_effect=record), \
                     patch("sys.stdout", new=io.StringIO()), \
                     self.assertRaises(ProtocolError):
                    runner.campaign(ledger, snapshot_fn=self.stop)
                self.assertEqual(chosen, [expected])

    def test_an_explicitly_injected_replay_function_is_still_honoured(self):
        # The existing offline suites pass their own replay; resolving the sealed
        # one instead would break them, so injection must short-circuit it.
        ledger = runner.prepare(self.base / "cell_injected", self.credentials,
                                condition="A", repeat=1)
        with patch.object(runner, "condition_replay",
                          side_effect=AssertionError("must not resolve a sealed replay")), \
             patch("sys.stdout", new=io.StringIO()), \
             self.assertRaises(ProtocolError):
            runner.campaign(ledger, replay_fn=lambda *a: None, snapshot_fn=self.stop)

    def test_a_disagreeing_schema_and_condition_cannot_choose_a_replay(self):
        # Defence in depth: even if an identity somehow carried condition A with
        # the world triple, no replay function is returned for it.
        ledger = runner.prepare(self.base / "cell_disagree", self.credentials,
                                condition="A", repeat=1)
        with patch.object(ledger, "source_files",
                          {"world": "world_program.py", "policy": "policy.py",
                           "inventory": "inventory.json"}), \
             self.assertRaises(ProtocolError):
            runner.condition_replay(ledger)

    def test_the_sealed_engineering_note_describes_this_round(self):
        ledger = runner.prepare(self.base / "cell_note", self.credentials,
                                condition="B", repeat=1)
        note = ledger.identity["settings"]["engineering"]
        self.assertIn("Opus5", note)
        self.assertIn("Opus4.6", note)
        self.assertNotIn("Codex takeover", note)
        # A legacy caller keeps the historical note untouched.
        legacy = runner.prepare(self.base / "cell_legacy", self.credentials)
        self.assertIn("Codex takeover", legacy.identity["settings"]["engineering"])


if __name__ == "__main__":
    unittest.main()
