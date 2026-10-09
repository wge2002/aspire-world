"""The closed-loop r2 launch template serves Qwen through the frozen image-source wrapper.

CPU-only. The launch directory is staged into a temporary root from the real
template and the real verified V4 inputs; no model, simulator, service or seed
runs. The supervisor is loaded from that staged copy, exactly as DLC would.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SIM = Path(__file__).resolve().parents[1]
STUDY = SIM / "docs/experiments/code-world-qwen-closed-loop-r2-20261005"
WRAPPER = SIM / "cap/serving/launch_vllm_image_source.py"
ACCEPTED_WRAPPER_SHA256 = "ff07c0cc91e8b48416db107ea518552ea46b0d6560a551706b90e8079de35d8d"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def prepare():
    return load("r2_prepare_foundation", STUDY / "prepare-foundation.py")


@pytest.fixture(scope="module")
def wrapper():
    # Top-level imports are stdlib only; vLLM is imported inside its functions.
    return load("r2_image_source_wrapper", WRAPPER)


@pytest.fixture
def staged(prepare, tmp_path, monkeypatch):
    """stage_launch into a fake root, pinned to the config derived for that root."""
    launch = tmp_path / "launch"
    monkeypatch.setattr(prepare, "LAUNCH", launch)
    config = prepare.model_server_config(
        (prepare.V4_LAUNCH / prepare.MODEL_SERVER_REL).read_bytes(), launch / prepare.WRAPPER_REL)
    monkeypatch.setattr(prepare, "MODEL_CONFIG_SHA256", hashlib.sha256(config).hexdigest())
    prepare.stage_launch()
    supervisor = load("r2_staged_supervisor", launch / "dlc-supervisor.py")
    return launch, supervisor


def marker(receipt):
    return "INFO starting\n" + "VLLM_IMAGE_SOURCE_OVERLAY_LOADED " + json.dumps(receipt, sort_keys=True) + "\nINFO ready\n"


def exact_receipt(launch, wrapper):
    return {"overlay": str((launch / "reference/launch_vllm_image_source.py").resolve()),
            "overlay_sha256": ACCEPTED_WRAPPER_SHA256, "vllm": wrapper.EXPECTED_VLLM_VERSION,
            "serving": "/x/vllm/entrypoints/anthropic/serving.py",
            "serving_sha256": wrapper.EXPECTED_SERVING_SHA256}


def test_staged_config_differs_from_v4_only_in_argv1(prepare, staged):
    launch, _ = staged
    v4 = json.loads((prepare.V4_LAUNCH / "reference/model-server.json").read_text())
    new = json.loads((launch / "reference/model-server.json").read_text())
    assert new["argv"][1] == str(launch / "reference/launch_vllm_image_source.py")
    assert v4["argv"][1].endswith("/bin/vllm")
    assert new["argv"][:1] + new["argv"][2:] == v4["argv"][:1] + v4["argv"][2:]
    assert {k: v for k, v in new.items() if k != "argv"} == {k: v for k, v in v4.items() if k != "argv"}
    assert new["env"]["CUDA_VISIBLE_DEVICES"] == "0,1,2,3"
    assert new["argv"][new["argv"].index("--gpu-memory-utilization") + 1] == "0.95"


def test_manifest_freezes_the_accepted_wrapper_and_every_launch_file(prepare, staged):
    launch, _ = staged
    manifest = json.loads((launch / "launch-manifest.json").read_text())
    assert hashlib.sha256((launch / prepare.WRAPPER_REL).read_bytes()).hexdigest() == ACCEPTED_WRAPPER_SHA256
    assert (launch / prepare.WRAPPER_REL).read_bytes() == WRAPPER.read_bytes()
    assert manifest["files"]["reference/launch_vllm_image_source.py"] == ACCEPTED_WRAPPER_SHA256
    assert set(manifest["files"]) == set(prepare.LAUNCH_FILES)
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((launch / name).read_bytes()).hexdigest() == expected, name
    assert manifest["model_server"]["differs_from_v4"] == ["argv[1]"]
    assert manifest["model_server"]["v4_sha256"] == prepare.LAUNCH_PINS["reference/model-server.json"]


def test_wrapper_is_a_pinned_external_runtime_input(prepare, staged, monkeypatch):
    launch, _ = staged
    monkeypatch.setattr(prepare, "original_external", lambda case: [])
    pinned = prepare.external_inputs({})
    assert launch / prepare.WRAPPER_REL in pinned
    assert set(pinned) == {launch / n for n in (*prepare.LAUNCH_FILES, "launch-manifest.json")}


def test_real_launch_config_hash_is_pinned_identically_in_prepare_and_supervisor(prepare):
    config = prepare.model_server_config(
        (prepare.V4_LAUNCH / prepare.MODEL_SERVER_REL).read_bytes(),
        prepare.LAUNCH / prepare.WRAPPER_REL)
    assert json.loads(config)["argv"][1] == (
        "/mnt/home/gewang/experiments/code-world-qwen-closed-loop-r2-20261005/launch-20261005"
        "/reference/launch_vllm_image_source.py")
    assert hashlib.sha256(config).hexdigest() == prepare.MODEL_CONFIG_SHA256
    text = (STUDY / "support/dlc-supervisor.py").read_text()
    assert f'MODEL_CONFIG_SHA = "{prepare.MODEL_CONFIG_SHA256}"' in text
    assert prepare.MODEL_CONFIG_SHA256 != prepare.LAUNCH_PINS["reference/model-server.json"]


def test_changed_wrapper_is_refused_before_model_config_and_manifest(prepare, tmp_path, monkeypatch):
    changed = tmp_path / "launch_vllm_image_source.py"
    changed.write_bytes(WRAPPER.read_bytes() + b"\n")
    monkeypatch.setattr(prepare, "LAUNCH", tmp_path / "launch")
    monkeypatch.setattr(prepare, "WRAPPER_SOURCE", changed)
    with pytest.raises(SystemExit, match="wrapper changed"):
        prepare.stage_launch()
    assert not (tmp_path / "launch/reference/model-server.json").exists()
    assert not (tmp_path / "launch/launch-manifest.json").exists()


def test_supervisor_pins_match_the_wrapper(staged, wrapper):
    _, supervisor = staged
    assert supervisor.WRAPPER_SHA256 == ACCEPTED_WRAPPER_SHA256
    assert supervisor.OVERLAY_MARKER == wrapper.LOADED_MARKER
    assert supervisor.EXPECTED_VLLM_VERSION == wrapper.EXPECTED_VLLM_VERSION
    assert supervisor.EXPECTED_SERVING_SHA256 == wrapper.EXPECTED_SERVING_SHA256


def test_exact_loader_marker_is_accepted(staged, wrapper):
    launch, supervisor = staged
    receipt = exact_receipt(launch, wrapper)
    assert supervisor.overlay_receipt(marker(receipt), launch / supervisor.WRAPPER_REL) == receipt


def test_missing_loader_marker_refuses_the_plain_vllm_binary(staged):
    launch, supervisor = staged
    with pytest.raises(RuntimeError, match="0 VLLM_IMAGE_SOURCE_OVERLAY_LOADED lines"):
        supervisor.overlay_receipt("INFO vllm serve started\nINFO ready\n",
                                   launch / supervisor.WRAPPER_REL)


@pytest.mark.parametrize("mutate,match", [
    (lambda r: r.update(overlay_sha256="0" * 64), "overlay_sha256"),
    (lambda r: r.update(overlay="/mnt/home/gewang/code/ASPIRE/aspire/sim/cap/serving/launch_vllm_image_source.py"), "overlay"),
    (lambda r: r.update(vllm="0.28.0"), "vllm"),
    (lambda r: r.update(serving_sha256="1" * 64), "serving_sha256"),
    (lambda r: r.pop("overlay_sha256"), "overlay_sha256"),
])
def test_wrong_loader_marker_is_refused(staged, wrapper, mutate, match):
    launch, supervisor = staged
    receipt = exact_receipt(launch, wrapper)
    mutate(receipt)
    with pytest.raises(RuntimeError, match=match):
        supervisor.overlay_receipt(marker(receipt), launch / supervisor.WRAPPER_REL)


def test_duplicate_marker_is_refused(staged, wrapper):
    launch, supervisor = staged
    log = marker(exact_receipt(launch, wrapper)) * 2
    with pytest.raises(RuntimeError, match="2 VLLM_IMAGE_SOURCE_OVERLAY_LOADED lines"):
        supervisor.overlay_receipt(log, launch / supervisor.WRAPPER_REL)


def test_marker_gate_precedes_native_compatibility_and_preflight_checks_adapter():
    text = (STUDY / "support/dlc-supervisor.py").read_text()
    body = text[text.index("def main():"):]
    assert body.index("overlay_receipt((logs / \"model.log\")") < body.index('status("native_compatibility"')
    assert body.index('status("native_compatibility"') < body.index('"qwen-native-compat.py", "--case"')
    assert 'model["argv"][1] == str(wrapper)' in body
    assert "['verify_adapter']()" in body and "IMAGE_SOURCE_ADAPTER_OK" in body
    # Prior schema/pin/transport gates remain.
    for kept in ("verify_staged_runtime(repo, receipt, cell_id)", "campaign.local_vllm_pilot_check(case)",
                 'raise RuntimeError(f"launch file changed: {name}")', "accept_cell_result(result, code)"):
        assert kept in body or kept in text, kept
