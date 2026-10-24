"""CPU-only checks of the speed-only backport through the existing DLC launcher."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/common"))
import qwen_decode_config as decode
import streaming_dlc_entry as entry


def eager_manifest():
    source = json.loads((ROOT.parents[1] / "deploy/qwen/model-server.example.json").read_text())
    source["argv"].remove("--compilation-config")
    source["argv"].pop()  # its final JSON value
    source["argv"].append("--enforce-eager")
    source["argv"][source["argv"].index("--gpu-memory-utilization") + 1] = "0.80"
    source["env"].update(CUDA_VISIBLE_DEVICES="2,3,4,5", PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True",
                         VLLM_PLE_CPU_OFFLOAD="1", FLASHINFER_WORKSPACE_BASE="/existing/flashinfer")
    source.update(max_output_config=64000, claude_version="2.1.220", weights_manifest={"pinned": "weights"})
    source["argv_sha256"] = hashlib.sha256(json.dumps(source["argv"], sort_keys=True).encode()).hexdigest()
    return source


def test_converted_manifest_runs_through_existing_dlc_spec_without_losing_protocol_or_device_binding(tmp_path):
    original = eager_manifest()
    before = copy.deepcopy(original)
    converted = decode.enable_decode_graph(original)
    path = tmp_path / "new-service.json"
    path.write_text(json.dumps(converted))
    case = {"model_service": {"server_manifest": str(path), "server_manifest_sha256": entry.sha256_file(path),
                              "endpoint": converted["endpoint"]}}
    dep = {"runtime_root": "/frozen/runtime", "source_checkout_prefix": "/engineering",
           "flashinfer_seed_cache": "/old/cache"}
    spec = entry.qwen_spec(dep, case, tmp_path / "cache")
    assert spec["argv"] == converted["argv"]
    assert spec["gpu"] == "2,3,4,5" and spec["port"] == 8121
    assert spec["argv"][spec["argv"].index("--gpu-memory-utilization") + 1] == "0.80"
    assert json.loads(spec["argv"][spec["argv"].index("--compilation-config") + 1]) == decode.COMPILATION_CONFIG
    assert "--enforce-eager" not in spec["argv"]
    assert "PYTORCH_CUDA_ALLOC_CONF" not in spec["env"]
    assert spec["env"]["VLLM_PLE_CPU_OFFLOAD"] == "1"
    assert converted["argv_sha256"] == hashlib.sha256(json.dumps(spec["argv"], sort_keys=True).encode()).hexdigest()
    assert converted["argv_sha256"] != original["argv_sha256"]
    assert {k: v for k, v in converted.items() if k not in {"argv", "env", "argv_sha256"}} == {
        k: v for k, v in original.items() if k not in {"argv", "env", "argv_sha256"}}
    assert {k: v for k, v in original["env"].items() if k != "PYTORCH_CUDA_ALLOC_CONF"} == converted["env"]
    assert original == before
    assert decode.enable_decode_graph(converted) == converted


def test_cli_keeps_parent_bytes_records_provenance_and_refuses_overwrite(tmp_path, capsys):
    parent, output = tmp_path / "parent.json", tmp_path / "new.json"
    parent.write_text(json.dumps(eager_manifest()))
    before = parent.read_bytes()
    assert decode.main(["--source", str(parent), "--out", str(output)]) == 0
    stdout = json.loads(capsys.readouterr().out)
    result = json.loads(output.read_text())
    assert result["decode_graph_update"]["parent_manifest_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["decode_graph_update"]["source_commit"] == decode.SOURCE_COMMIT
    assert stdout["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert stdout["services_started"] is False
    for path in (parent, output):
        with pytest.raises(FileExistsError):
            decode.main(["--source", str(parent), "--out", str(path)])
    assert parent.read_bytes() == before


@pytest.mark.parametrize("change", [
    lambda m: m["argv"].append("--disable-custom-all-reduce"),
    lambda m: m["argv"].extend(["--compilation-config", '{"mode": 3}']),
    lambda m: m["argv"].__setitem__(m["argv"].index("--tensor-parallel-size") + 1, "2"),
    lambda m: m["env"].update(PYTORCH_ALLOC_CONF="expandable_segments:True"),
])
def test_unreviewed_engine_settings_do_not_produce_an_output(tmp_path, change):
    manifest = eager_manifest()
    change(manifest)
    parent, output = tmp_path / "parent.json", tmp_path / "new.json"
    parent.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        decode.main(["--source", str(parent), "--out", str(output)])
    assert not output.exists()
