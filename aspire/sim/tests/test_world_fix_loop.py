"""Offline protocol tests. Synthetic responses/trials here are never run results."""
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
import types
from unittest.mock import patch

SIM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM))
from scripts.libero.world_fix_loop_state import (MECHANISM_ADMISSION, Ledger, MODEL, PROTOCOL,
                                                 ProtocolError, mechanism_admission, put, read, sha)
from scripts.libero.world_fix_loop_model import extract, validate_source, NativeModel, ORIGIN, model_endpoint
from scripts.libero import world_fix_loop as runner
from cap.world_model.fix_loop_scene_broker import (FIXED, SCHEMA, MODE, make_config, load_config,
                                                  mechanism_evidence, terminal_outcome)
from cap.world_model.live_broker import _child_environment

INVENTORY = [{"id": name, "label": name, "role": role, "confidence": "uncertain", "shape_prior": "unknown"}
             for name, role in [("bowl", "manipulated"), ("plate", "target")]]
WORLD = "def initialize(context):\n return {}\ndef advance(state, step):\n return state\ndef predict(state, step):\n return {}\ndef assimilate(state, evidence):\n return state\n"
POLICY = "decision = world_verify()\nprint(decision)\n"
BODY = {"model": MODEL, "max_tokens": PROTOCOL["max_tokens"], "messages": []}


def response(index=0, source=None):
    data = source or {"world": WORLD + f"# synthetic version {index}\n", "policy": POLICY + f"# synthetic version {index}\n", "inventory": INVENTORY}
    return {"model": MODEL, "stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(data)}]}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runtime = self.base / "runtime"
        self.runtime.mkdir()
        self.yaml = self.runtime / "config.yaml"
        self.yaml.write_text("env:\n  cfg:\n    privileged: false\n    apis: [FrankaLiberoApiReducedSkillLibraryTraced]\n")
        self.settings = {"runtime_root": str(self.runtime), "runtime_sha256": {"config.yaml": sha(self.yaml)},
                         "yaml": str(self.yaml), "yaml_sha256": sha(self.yaml), "model_origin": ORIGIN,
                         "credential_settings": str(self.base / "settings.json")}
        self.ledger = Ledger(self.base / "campaign", self.settings)

    def generated(self, index, status="valid"):
        ledger = self.ledger
        ledger.begin_generation(index, BODY)
        attempt = ledger.begin_request(index)
        path = ledger.slot(index) / f"request-{attempt}.response.json"
        data = response(index)
        put(path, data)
        ledger.finish_generation(index, status, response_path=path,
                                 sources=extract(data) if status == "valid" else None, error="synthetic" if status != "valid" else None)

    def trial(self, phase, index, status="task_failure", **evidence):
        ledger = self.ledger
        record = ledger.begin_trial(phase, index)
        raw = ledger.trial_dir(phase, index) / "raw.json"
        put(raw, {"synthetic_test_only": True, "status": status})
        ledger.finish_trial(phase, index, {"status": status, "seed": record["seed"], **evidence}, [raw])
        return record

    def development(self, status="task_failure"):
        for i in range(16):
            self.generated(i)
            self.trial("initial" if i == 0 else "repair", i, status)


class Accounting(Fixture):
    def amend(self):
        corrected = self.base / "corrected-runtime"
        shutil.copytree(self.runtime, corrected)
        (corrected / "config.yaml").write_text(self.yaml.read_text() + "# synthetic correction\n")
        path = self.ledger.root / "control/runtime-amendment.json"
        prior = list((self.ledger.root / "generations").rglob("*.json"))
        put(path, {"schema": 1, "identity_sha256": self.ledger.identity_sha,
                   "runtime_root": str(corrected), "changed_sha256": {"config.yaml": sha(corrected / "config.yaml")},
                   "prior_evidence": self.ledger.refs(prior)})
        put(path.with_suffix(".sha256"), (sha(path) + "\n").encode())
        return corrected, path

    def test_runtime_correction_preserves_failed_slots_pending_request_and_fifty_evaluations(self):
        self.generated(0)
        self.trial("initial", 0)
        for index in range(1, 6):
            self.generated(index, "content_error")
        self.ledger.begin_generation(6, BODY)
        self.ledger.begin_request(6)
        identity = self.ledger.identity_sha
        corrected, path = self.amend()
        self.ledger = Ledger(self.ledger.root)
        self.ledger.verify_runtime()
        self.assertEqual(self.ledger.identity_sha, identity)
        self.assertEqual(self.ledger.begin_request(6), 1)
        answer = self.ledger.slot(6) / "request-1.response.json"
        put(answer, response(6))
        self.ledger.finish_generation(6, "valid", response_path=answer, sources=extract(response(6)))
        self.trial("repair", 6)
        for index in range(7, 16):
            self.generated(index)
            self.trial("repair", index)
        selected = self.ledger.freeze()
        self.assertEqual(selected["runtime_amendment_sha256"], sha(path))
        for seed in range(1, 51):
            self.trial("heldout", seed)
        summary = self.ledger.summary()
        self.assertEqual([summary[k] for k in ["repair_opportunities_used", "content_invalid_generations", "model_requests_reserved", "development_executions", "heldout_executions"]], [15, 5, 17, 11, 50])
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(self.ledger.generation(5)["status"], "content_error")
        (corrected / "config.yaml").write_text("unrecorded change")
        with self.assertRaises(ProtocolError):
            self.ledger.verify_runtime()

    def test_correction_cannot_hide_changed_history_or_an_unsealed_amendment(self):
        self.generated(0, "content_error")
        _, path = self.amend()
        self.ledger.verify_runtime()
        reservation = self.ledger.slot(0) / "reserved.json"
        original = reservation.read_bytes()
        reservation.write_text("{}")
        with self.assertRaises(ProtocolError):
            self.ledger.verify_runtime()
        reservation.write_bytes(original)
        path.with_suffix(".sha256").unlink()
        with self.assertRaises(ProtocolError):
            self.ledger.verify_runtime()

    def test_correction_after_selection_is_rejected(self):
        self.development()
        self.ledger.freeze()
        self.amend()
        with self.assertRaises(ProtocolError):
            self.ledger.begin_trial("heldout", 1)

    def test_exact_one_fifteen_fifty_and_no_postfreeze_generation(self):
        self.development()
        selected = self.ledger.freeze()
        self.assertEqual(selected["version"], 15)
        self.assertFalse(selected["fallback_to_tested_program_error"])
        for seed in range(1, 51):
            record = self.trial("heldout", seed)
            self.assertEqual((record["seed"], record["version"]), (seed, 15))
        result = self.ledger.summary()
        self.assertEqual([result[k] for k in ["repair_opportunities_used", "model_requests_reserved", "development_executions", "heldout_executions"]], [15, 16, 16, 50])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["revision_source_changes"], {"world": 15, "policy": 15, "both_unchanged": 0})
        with self.assertRaises(ProtocolError):
            self.ledger.begin_generation(0, BODY)
        with self.assertRaises(ProtocolError):
            self.ledger.begin_trial("heldout", 51)

    def test_premature_evaluation_and_unexecuted_generation_fail_closed(self):
        self.generated(0)
        with self.assertRaises((ProtocolError, FileNotFoundError)):
            self.ledger.begin_trial("heldout", 1)
        with self.assertRaises((ProtocolError, FileNotFoundError)):
            self.ledger.begin_generation(1, BODY)
        with self.assertRaises((ProtocolError, FileNotFoundError)):
            self.ledger.freeze()

    def test_invalid_content_consumes_slot_without_extra_trial(self):
        self.generated(0, "content_error")
        with self.assertRaises(ProtocolError):
            self.ledger.begin_trial("initial", 0)
        for i in range(1, 16):
            self.generated(i)
            self.trial("repair", i)
        self.assertEqual(self.ledger.freeze()["version"], 15)
        self.assertEqual(self.ledger.summary()["development_executions"], 15)

    def test_program_errors_can_continue_and_fallback_is_tested(self):
        self.development("program_error")
        selection = self.ledger.freeze()
        self.assertTrue(selection["fallback_to_tested_program_error"])
        self.assertEqual(selection["version"], 15)
        self.trial("heldout", 1, "program_error")
        self.assertEqual(self.ledger.summary()["heldout_program_errors"], 1)

    def test_infrastructure_is_not_a_valid_failure_or_completion(self):
        self.generated(0)
        self.trial("initial", 0, "infrastructure_error")
        with self.assertRaises(ProtocolError):
            self.ledger.begin_generation(1, BODY)
        self.assertNotEqual(self.ledger.summary()["status"], "complete")

    def test_resume_keeps_budget_request_and_identity(self):
        self.ledger.begin_generation(0, BODY)
        self.ledger.begin_request(0)
        resumed = Ledger(self.ledger.root, self.settings)
        self.assertEqual(resumed.begin_request(0), 1)
        self.assertEqual(resumed.begin_request(0), 2)
        with self.assertRaises(ProtocolError):
            resumed.begin_request(0)
        changed = dict(self.settings, model_origin="https://other.example")
        with self.assertRaises(ProtocolError):
            Ledger(self.ledger.root, changed)
        (self.ledger.slot(0) / "request.json").write_text(json.dumps(dict(BODY, model="other")))
        with self.assertRaises(ProtocolError):
            resumed.begin_request(0)

    def test_frozen_raw_development_evidence_and_runtime_are_verified(self):
        self.development()
        self.ledger.freeze()
        (self.ledger.trial_dir("repair", 4) / "raw.json").write_text("{}")
        with self.assertRaises(ProtocolError):
            self.ledger.selection()
        self.yaml.write_text("changed")
        with self.assertRaises(ProtocolError):
            self.ledger.verify_runtime()


EXERCISED = {"world_reference_established": True, "world_relation_measurement_attempts": 1,
             "world_relation_verdicts": {"support": 1, "contradict": 0, "unknown": 0}}
UNKNOWN_BUT_MEASURED = {"world_reference_established": True, "world_relation_measurement_attempts": 2,
                        "world_relation_verdicts": {"support": 0, "contradict": 0, "unknown": 2}}
INERT = {"world_reference_established": False, "world_relation_measurement_attempts": 0,
         "world_relation_verdicts": {"support": 0, "contradict": 0, "unknown": 0}}


class MechanismAdmissionSelection(Fixture):
    """A new campaign must not freeze a candidate whose mechanism never ran.

    Synthetic ledger evidence only; nothing here is a robot or task result.
    """

    def setUp(self):
        super().setUp()
        self.gated_settings = dict(self.settings, mechanism_admission=MECHANISM_ADMISSION)
        self.ledger = Ledger(self.base / "gated", self.gated_settings)

    def develop(self, evidence_by_index, default=None):
        for index in range(16):
            self.generated(index)
            evidence = evidence_by_index.get(index, default if default is not None else EXERCISED)
            self.trial("initial" if index == 0 else "repair", index, **evidence)

    def test_inert_latest_candidate_is_filtered_and_an_eligible_earlier_one_is_chosen(self):
        # Version 15 is the latest noncrashing candidate and would win the legacy
        # rule; its mechanism measured nothing, so it must not be selected.
        self.develop({i: INERT for i in range(13, 16)})
        selection = self.ledger.freeze()
        self.assertEqual(selection["version"], 12)
        self.assertEqual(selection["mechanism_admission"], MECHANISM_ADMISSION)
        self.assertEqual(selection["selection_rule"], PROTOCOL["selection"])
        audit = selection["mechanism_admission_audit"]
        self.assertFalse(audit["15"]["eligible"])
        self.assertEqual(audit["15"]["reasons"], ["no_measured_reference", "no_relation_query_attempt"])
        self.assertTrue(audit["12"]["eligible"])
        self.assertEqual(self.ledger.selection()["version"], 12)

    def test_no_eligible_candidate_fails_before_selection_or_heldout(self):
        self.develop({}, default=INERT)
        with self.assertRaises(ProtocolError):
            self.ledger.freeze()
        self.assertFalse((self.ledger.root / "selection.json").exists())
        with self.assertRaises(ProtocolError):
            self.ledger.begin_trial("heldout", 1)

    def test_a_real_relation_query_returning_unknown_is_eligible(self):
        # An attempted measurement whose verdict was UNKNOWN is a measurement.
        # No task success, SUPPORT or decisive verdict is required.
        self.develop({15: UNKNOWN_BUT_MEASURED}, default=INERT)
        selection = self.ledger.freeze()
        self.assertEqual(selection["version"], 15)
        self.assertTrue(selection["mechanism_admission_audit"]["15"]["eligible"])

    def test_only_program_errors_with_evidence_keeps_the_tested_fallback(self):
        for index in range(16):
            self.generated(index)
            self.trial("initial" if index == 0 else "repair", index, "program_error", **EXERCISED)
        selection = self.ledger.freeze()
        self.assertEqual(selection["version"], 15)
        self.assertTrue(selection["fallback_to_tested_program_error"])

    def test_new_rule_identity_missing_evidence_fields_fails_closed(self):
        # Trials recorded before the reporting change carry none of the fields.
        self.develop({}, default={})
        with self.assertRaises(ProtocolError) as caught:
            self.ledger.freeze()
        self.assertIn("mechanism_evidence_absent", str(caught.exception))
        self.assertFalse((self.ledger.root / "selection.json").exists())

    def test_historical_identity_without_the_rule_keeps_legacy_selection(self):
        # The pre-existing campaign identity has no mechanism_admission key, so
        # no recorded result is reinterpreted and selection is unchanged.
        legacy = Ledger(self.base / "legacy", self.settings)
        self.ledger = legacy
        self.develop({}, default={})
        selection = legacy.freeze()
        self.assertEqual(selection["version"], 15)
        self.assertIsNone(selection["mechanism_admission"])
        self.assertIsNone(selection["mechanism_admission_audit"])
        self.assertEqual(legacy.selection()["version"], 15)

    def test_malformed_or_unknown_rule_cannot_silently_disable_the_gate(self):
        for bad in [{}, {"version": 2}, "on", 1,
                    dict(MECHANISM_ADMISSION, require_reference_established=False),
                    dict(MECHANISM_ADMISSION, evidence_fields=[])]:
            with self.subTest(bad=bad), self.assertRaises(ProtocolError):
                mechanism_admission({"mechanism_admission": bad})
        self.assertIsNone(mechanism_admission(dict(self.settings)))
        # A correctly sealed identity carrying an unknown rule version is still
        # rejected at open time; a broken seal is not what makes it fail.
        root = self.base / "tampered"
        Ledger(root, dict(self.settings, mechanism_admission=MECHANISM_ADMISSION))
        identity = root / "identity.json"
        body = read(identity)
        body["settings"]["mechanism_admission"] = dict(MECHANISM_ADMISSION, version=99)
        identity.write_text(json.dumps(body))
        (root / "identity.sha256").write_text(sha(identity) + "\n")
        with self.assertRaises(ProtocolError):
            Ledger(root)


class Integration(Fixture):
    def test_model_endpoint_accepts_verified_service_ports_without_allowlist(self):
        self.assertEqual(model_endpoint("https://service.example:18443/"), "https://service.example:18443")
        self.assertEqual(model_endpoint("https://future.example/gateway"), "https://future.example/gateway")
        for value in ["http://service.example", "https://user:secret@service.example", "https://service.example?key=x", "https://service.example/#x"]:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                model_endpoint(value)

    def test_native_client_uses_frozen_configured_endpoint(self):
        settings = dict(self.settings, model_origin="https://configured.example:18443", model_trust_env=False)
        ledger = Ledger(self.base / "configured", settings)
        Path(settings["credential_settings"]).write_text(json.dumps({"apiKeyHelper": "synthetic-helper"}))
        ledger.begin_generation(0, BODY)
        calls = []
        class FakeClient:
            def __init__(_, **kwargs):
                self.assertFalse(kwargs["follow_redirects"])
                self.assertFalse(kwargs["trust_env"])
            def __enter__(_): return _
            def __exit__(_, *args): pass
            def post(_, url, **kwargs):
                calls.append(url)
                return SimpleNamespace(status_code=200, json=lambda: response())
        with patch("subprocess.run", return_value=SimpleNamespace(stdout="synthetic-secret")), patch.dict(sys.modules, {"httpx": SimpleNamespace(Client=FakeClient, Timeout=lambda *a, **k: None)}):
            NativeModel(ledger).generate(0)
        self.assertEqual(calls, ["https://configured.example:18443/v1/messages"])
        self.assertEqual(read(ledger.slot(0) / "request-0.receipt.json")["origin"], settings["model_origin"])

    def test_execution_selects_frozen_nvidia_vendor_and_physical_gpu7(self):
        vendor = self.base / "configured/control/nvidia-egl-vendor.json"
        put(vendor, {"file_format_version": "1.0.0", "ICD": {"library_path": "/usr/lib/x86_64-linux-gnu/libEGL_nvidia.so.0"}})
        settings = dict(self.settings, inputs_sha256={"control/nvidia-egl-vendor.json": sha(vendor)})
        ledger = Ledger(self.base / "configured", settings)
        env = runner.execution_environment(ledger)
        self.assertEqual((env["CUDA_VISIBLE_DEVICES"], env["MUJOCO_EGL_DEVICE_ID"]), ("7", "7"))
        self.assertEqual(env["__EGL_VENDOR_LIBRARY_FILENAMES"], str(vendor.resolve()))
        self.assertNotIn("HTTPS_PROXY", env)
        vendor.write_text("{}")
        with self.assertRaises(ProtocolError):
            runner.execution_environment(ledger)

    def test_full_driver_and_completed_resume_make_no_extra_model_calls(self):
        order = []
        class FakeModel:
            def __init__(_, ledger):
                _.ledger = ledger
            def generate(_, index):
                order.append(("model", index))
                attempt = _.ledger.begin_request(index)
                path = _.ledger.slot(index) / f"request-{attempt}.response.json"
                put(path, response(index))
                return path
        def fake_replay(ledger, phase, index):
            if not (ledger.trial_dir(phase, index) / "result.json").exists():
                order.append((phase, index))
                self.trial(phase, index, "program_error" if phase == "repair" and index == 3 else "task_failure")
            return ledger.trial(phase, index)
        with patch.object(runner, "build_request", return_value=BODY), patch("sys.stdout", new=io.StringIO()):
            result = runner.campaign(self.ledger, FakeModel, fake_replay, lambda _: None)
            self.assertEqual(result["status"], "complete")
            before = list(order)
            runner.campaign(Ledger(self.ledger.root), lambda _: self.fail("model used after freeze"), fake_replay, lambda _: None)
            self.assertEqual(order, before)
        self.assertEqual(order[:4], [("model", 0), ("initial", 0), ("model", 1), ("repair", 1)])
        self.assertEqual(order[32:], [("heldout", i) for i in range(1, 51)])

    def test_source_extraction_preserves_exact_model_code_and_rejects_privileged_imports(self):
        actual = extract(response())
        self.assertEqual(actual["world"], WORLD + "# synthetic version 0\n")
        for bad in ["import os\n", "import numpy as np\nnp.load('x')\n", "from numpy import load\nload('x')\n", "env.handle\n", "getattr(world_verify, '__globals__')\n"]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_source(bad + POLICY, False)
        with self.assertRaises(ValueError):
            extract(dict(response(), model="claude-opus-5"))
        with self.assertRaises(ValueError):
            extract(dict(response(), stop_reason="max_tokens"))

    def test_prose_and_fences_do_not_change_model_source(self):
        answer = response(source={"world": WORLD, "policy": POLICY + '# Braces { and } stay verbatim.\nprint({"x": "y"})\n', "inventory": INVENTORY})
        original = extract(answer)
        payload = answer["content"][0]["text"]
        for text in [payload, "```json\n" + payload + "\n```", "The tuple {points, colors} must be unpacked.\n" + payload,
                     'Explanation {"note": "presentation only"}\n```json\n' + payload + "\n```\nDone."]:
            with self.subTest(text=text[:40]):
                wrapped = copy.deepcopy(answer)
                wrapped["content"][0]["text"] = text
                self.assertEqual(extract(wrapped), original)

    def test_malformed_or_ambiguous_program_objects_are_not_repaired_or_selected(self):
        answer = response()
        payload = answer["content"][0]["text"]
        for text in ["Explanation only", payload[:-1], '{"wrapper": ' + payload + '}',
                     payload + "\nAlternative:\n" + payload,
                     payload + '\n{"world":"bad", "policy":"bad", "inventory":[]}']:
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                wrapped = copy.deepcopy(answer)
                wrapped["content"][0]["text"] = text
                extract(wrapped)

    def test_generated_child_has_no_model_credentials_or_proxy(self):
        parent = {"ANTHROPIC_API_KEY": "synthetic", "API_KEY": "synthetic", "HTTPS_PROXY": "http://x",
                  "ANTHROPIC_BASE_URL": ORIGIN, "CLAUDE_CONFIG_DIR": "/secret", "PATH": "/bin",
                  "SAM3_SERVICE_URL": "http://127.0.0.1:8114"}
        child = _child_environment(parent)
        self.assertEqual(set(child) & set(parent), {"PATH", "SAM3_SERVICE_URL"})

    def test_adapter_admits_only_exact_reserved_source_seed_and_config(self):
        self.generated(0)
        self.ledger.begin_trial("initial", 0)
        cfg = make_config(self.ledger, "initial", 0)
        args = SimpleNamespace(api_key=None, interactive=False, replay_code=self.ledger.sources(0)["policy"],
                               suite=runner.SUITE, task=runner.TASK, trial=51, config=self.yaml,
                               output_dir=self.ledger.trial_dir("initial", 0) / "ordinary")
        self.assertEqual(load_config(args, cfg)[0], cfg)
        for key, value in [("query_budget", 5), ("world_program_sha256", "0"*64), ("model_provenance", {}),
                           ("phase", "heldout"), ("output_root", str(self.base / "outside"))]:
            with self.subTest(key=key), self.assertRaises((ValueError, FileNotFoundError)):
                load_config(args, dict(cfg, **{key: value}))
        args.trial = 1
        with self.assertRaises(ValueError):
            load_config(args, cfg)

    def test_missing_terminal_is_infrastructure_not_program_failure(self):
        self.generated(0)
        self.ledger.begin_trial("initial", 0)
        directory = self.ledger.trial_dir("initial", 0)
        outcome = terminal_outcome(directory, self.ledger, "initial", 0, {"status": "completed"})
        self.assertEqual(outcome["status"], "infrastructure_error")
        self.assertFalse(outcome["valid"])

    def test_world_program_error_terminal_remains_revision_feedback(self):
        self.generated(0)
        record = self.ledger.begin_trial("initial", 0)
        directory = self.ledger.trial_dir("initial", 0)
        config = make_config(self.ledger, "initial", 0)
        put(directory / "live_config.json", config)
        put(directory / "summary.txt", b"synthetic test terminal\n")
        manifest = {"schema_version": SCHEMA, "mode": MODE, "model_provenance": config["model_provenance"],
                    "identity_sha256": self.ledger.identity_sha, "generation_sha256": config["generation_sha256"],
                    "phase": "initial", "version": 0, "status": "failed", "trial_result": {"sandbox_rc": 0},
                    "adapter_sha256": sha(SIM / "cap/world_model/fix_loop_scene_broker.py"),
                    "relational_adapter_sha256": sha(SIM / "cap/world_model/relational_scene_broker.py"),
                    "action_count": 3, "query_used": 1, "recovery_used": 0}
        put(directory / "live_manifest.json", manifest)
        put(directory / "live_tape.jsonl", b'{"event":"program_error","operation":"predict","error":"synthetic model error"}\n')
        fake = {"valid": True, "sandbox_rc": 0, "task_completed": False, "trial_dir": str(directory)}
        with patch("scripts.libero.paired_bowl_supervisor.artifacts", return_value=dict(fake)):
            outcome = terminal_outcome(directory, self.ledger, "initial", 0, {"status": "completed"})
        self.assertEqual(outcome["status"], "program_error")
        self.assertEqual(len(outcome["world_program_errors"]), 1)
        with patch("scripts.libero.paired_bowl_supervisor.artifacts", return_value=dict(fake)):
            outcome = terminal_outcome(directory, self.ledger, "initial", 0, {"status": "timeout"})
        self.assertEqual(outcome["status"], "infrastructure_error")

    def test_terminal_outcome_separates_call_counts_from_measured_mechanism_evidence(self):
        self.generated(0)
        self.ledger.begin_trial("initial", 0)
        directory = self.ledger.trial_dir("initial", 0)
        config = make_config(self.ledger, "initial", 0)
        put(directory / "live_config.json", config)
        put(directory / "summary.txt", b"synthetic test terminal\n")
        # The v15 shape: one world_verify call, no query ever sampled.
        inert = [{"event": "world_verify", "verdict": {"status": "unknown",
                  "reason": "program_did_not_request_relation_check", "measurement_attempted": False}},
                 {"event": "relation_check_not_requested", "prediction_frame": 5},
                 {"event": "reference_request_off_schedule", "prediction_frame": 5}]
        manifest = {"schema_version": SCHEMA, "mode": MODE, "model_provenance": config["model_provenance"],
                    "identity_sha256": self.ledger.identity_sha, "generation_sha256": config["generation_sha256"],
                    "phase": "initial", "version": 0, "status": "complete", "trial_result": {"sandbox_rc": 0},
                    "adapter_sha256": sha(SIM / "cap/world_model/fix_loop_scene_broker.py"),
                    "relational_adapter_sha256": sha(SIM / "cap/world_model/relational_scene_broker.py"),
                    "action_count": 3, "query_used": 0, "recovery_used": 0, "reference_invalidations": []}
        put(directory / "live_manifest.json", manifest)
        put(directory / "live_tape.jsonl",
            "".join(json.dumps(e) + "\n" for e in inert).encode())
        fake = {"valid": True, "sandbox_rc": 0, "task_completed": False, "trial_dir": str(directory)}
        with patch("scripts.libero.paired_bowl_supervisor.artifacts", return_value=dict(fake)):
            outcome = terminal_outcome(directory, self.ledger, "initial", 0, {"status": "completed"})
        self.assertEqual(outcome["status"], "task_failure")
        self.assertEqual(outcome["world_verify_calls"], 1)          # preserved for old readers
        self.assertTrue(outcome["world_participation_observed"])    # preserved meaning
        self.assertFalse(outcome["world_mechanism_evidence_observed"])
        self.assertFalse(outcome["world_reference_established"])
        self.assertEqual(outcome["world_relation_measurement_attempts"], 0)
        self.assertEqual(outcome["world_relation_checks_not_requested"], 1)
        self.assertEqual(outcome["world_off_schedule_reference_requests"], 1)

    def test_attempted_relation_query_returning_unknown_counts_as_real_evidence(self):
        tape = [{"event": "query_comparison", "purpose": "reference", "measurement_status": "ok",
                 "comparison": {"status": "UNKNOWN", "reason": "reference_only"}},
                {"event": "query_comparison", "purpose": "relation", "measurement_status": "unknown",
                 "comparison": {"status": "UNKNOWN", "reason": "measurement_unavailable"}},
                {"event": "world_verify", "verdict": {"status": "unknown"}}]
        evidence = mechanism_evidence(tape, {"query_used": 2, "reference_invalidations": []})
        self.assertTrue(evidence["world_reference_established"])
        self.assertEqual(evidence["world_relation_measurement_attempts"], 1)
        self.assertEqual(evidence["world_relation_verdicts"],
                         {"support": 0, "contradict": 0, "unknown": 1})
        self.assertEqual(evidence["world_relation_decisive_verdicts"], 0)
        # No decisive verdict, no SUPPORT, no task success -- still real evidence.
        self.assertTrue(evidence["world_mechanism_evidence_observed"])
        # A reference whose own measurement failed does not establish a reference.
        failed = mechanism_evidence([dict(tape[0], measurement_status="unknown"), tape[1]],
                                    {"query_used": 2, "reference_invalidations": ["capture_gap"]})
        self.assertFalse(failed["world_reference_established"])
        self.assertFalse(failed["world_mechanism_evidence_observed"])
        self.assertEqual(failed["world_reference_invalidations"], ["capture_gap"])

    def test_transport_attempts_are_reserved_bounded_and_secret_not_in_receipts(self):
        self.ledger.begin_generation(0, BODY)
        Path(self.settings["credential_settings"]).write_text(json.dumps({"apiKeyHelper": "synthetic-helper"}))
        posts = []
        class FakeClient:
            def __init__(_, **kwargs):
                self.assertFalse(kwargs["follow_redirects"])
            def __enter__(_): return _
            def __exit__(_, *args): pass
            def post(_, url, **kwargs):
                self.assertEqual(url, ORIGIN + "/v1/messages")
                self.assertEqual(kwargs["headers"]["x-api-key"], "synthetic-secret")
                posts.append(kwargs["content"])
                # Incomplete streams cannot become valid source generations.
                return SimpleNamespace(status_code=200, json=lambda: dict(response(), stop_reason=None))
        fake_http = SimpleNamespace(Client=FakeClient, Timeout=lambda *a, **k: None)
        with patch("subprocess.run", return_value=SimpleNamespace(stdout="synthetic-secret\n")), patch.dict(sys.modules, {"httpx": fake_http}):
            model = NativeModel(self.ledger)
            with self.assertRaises(RuntimeError):
                model.generate(0)
            with self.assertRaises(RuntimeError):
                model.generate(0)
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts, [posts[0]] * 3)
        for path in self.ledger.slot(0).glob("*.json"):
            self.assertNotIn("synthetic-secret", path.read_text())


if __name__ == "__main__":
    unittest.main()
