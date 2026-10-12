"""CPU tests for the shared pieces of the DLC entry.

Nothing here starts a model, perception service, simulator, native CC or DLC job.
Service-spec fidelity is checked against the real RS deployment manifests (read
only) when they are present. Lifecycle tests use stand-in sleep processes.
"""
import base64
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO / "scripts/common"))
import streaming_dlc_entry as E  # noqa: E402
import streaming_dlc_pair as P  # noqa: E402

DEPLOY = Path("/mnt/workspace/aspire-world-deployment-20261010")
RUNTIME = Path("/mnt/workspace/aspire-world-runtimes/streaming-common-v3-SZ-DEV-20261010")
R1_CASE = DEPLOY / "bowl-sam2-streaming-r1.case.json"
real = pytest.mark.skipif(not R1_CASE.is_file(), reason="RS deployment manifests not mounted")


def real_dep() -> tuple[dict, dict]:
    case = json.loads(R1_CASE.read_text())
    dep = {"cell": case["id"], "case": str(R1_CASE), "runtime_root": str(RUNTIME),
           "source_checkout_prefix": "/root/github/aspire-world",
           "envs": {"qwen": "/mnt/workspace/aspire-world-envs/qwen38-vllm",
                    "libero": "/mnt/workspace/aspire-world-envs/libero"},
           "flashinfer_seed_cache": "/mnt/workspace/aspire-world-envs/qwen38-flashinfer-workspace",
           "identity": {"home": "/mnt/workspace/aspire-world-users/bowl-r1"},
           "entry_dir": "/mnt/workspace/aspire-world-runs/dlc-entry-test/cells/x",
           "forbidden_read_paths": ["/mnt/workspace/aspire-world-users/elsewhere"]}
    return dep, case


@real
def test_qwen_spec_keeps_the_validated_engine_arguments_and_env():
    dep, case = real_dep()
    manifest = json.loads(Path(case["model_service"]["server_manifest"]).read_text())
    spec = E.qwen_spec(dep, case, Path("/nonexistent/cache"))
    assert spec["argv"] == manifest["argv"]  # every engine flag, byte for byte
    for key, value in manifest["env"].items():
        if key != "FLASHINFER_WORKSPACE_BASE":
            assert spec["env"][key] == value, key
    for flag in ("--max-model-len", "--max-num-seqs", "--tensor-parallel-size",
                 "--gpu-memory-utilization", "--limit-mm-per-prompt", "--hf-overrides"):
        assert flag in spec["argv"]
    assert spec["env"]["VLLM_PLE_CPU_OFFLOAD"] == "1"
    assert spec["cwd"] == str(RUNTIME / "aspire/sim")
    assert set(spec["adaptations"]) - {"cwd", "FLASHINFER_WORKSPACE_BASE"} <= set(
        E.cache_env(Path("/x"), "q"))
    E.check_specs([spec], dep)
    assert E.missing_spec_paths([spec]) == []


@real
@pytest.mark.parametrize("slot", ["A", "B"])
def test_perception_specs_rebase_only_declared_fields(slot):
    dep, case = real_dep()
    layout = P.SLOTS[slot]
    if slot == "A":
        case = {**case, **{k: f"http://127.0.0.1:{layout['ports'][r]}"
                           for r, k in P.URL_KEYS.items()}}
    specs = E.perception_specs(dep, case, RUNTIME / "aspire/sim", Path("/nonexistent/cache"),
                               layout, prefix_name=f"{slot}-")
    E.check_specs(specs, dep, layout)
    descriptor = json.loads(Path(case["perception_manifest"]).read_text())
    recorded = {t["name"]: t for t in descriptor["startup_manifest"]["tasks"]}
    for spec in specs:
        task = recorded[f"{spec['role']}-service"]
        changed = {k for k, v in task["env"].items() if spec["env"][k] != v}
        assert changed == set(spec["adaptations"]) & set(task["env"])
        argv = list(task["argv"])
        argv[argv.index("--port") + 1] = str(layout["ports"][spec["role"]])
        assert spec["argv"] == argv
        assert ("--port" in spec["adaptations"]) == (slot == "A")
        assert spec["gpu"] == layout[spec["role"]]
        assert not any(k.lower().endswith("_proxy") for k in spec["env"])
        assert spec["env"]["LIBERO_CONFIG_PATH"] == case["libero_config_path"]
    pyroki = next(s for s in specs if s["role"] == "pyroki")
    assert Path(pyroki["env"]["ROBOT_DESCRIPTIONS_CACHE"], "example-robot-data").is_dir()
    assert E.missing_spec_paths(specs) == []


@real
def test_case_layout_is_enforced_per_slot():
    dep, case = real_dep()
    mods = SimpleNamespace(camp=SimpleNamespace(load=lambda p: (
        case, (RUNTIME / "aspire/sim").resolve(), Path(case["control"]) / "cell")))
    dep["case_sha256"] = E.sha256_file(R1_CASE)
    assert E.check_case(dep, mods, P.SLOTS["B"])[0]["id"] == "bowl_sam2streaming_r1"
    with pytest.raises(E.EntryRefused, match="GPU 1"):
        E.check_case(dep, mods, P.SLOTS["A"])
    with pytest.raises(E.EntryRefused, match="sha256"):
        E.check_case({**dep, "case_sha256": "0" * 64}, mods)


def test_inherited_env_is_an_allowlist():
    environ = {"LANG": "C.UTF-8", "TZ": "UTC", "HOME": "/root", "ANTHROPIC_API_KEY": "x",
               "HF_TOKEN": "x", "https_proxy": "p", "WORLD_SIZE": "2", "RANK": "1",
               "MASTER_ADDR": "m", "NCCL_SOCKET_IFNAME": "eth0", "PYTHONPATH": "/root",
               "CUDA_VISIBLE_DEVICES": "0", "LD_PRELOAD": "x", "PAI_TOKEN": "t"}
    assert E.base_env(environ) == {"LANG": "C.UTF-8", "TZ": "UTC", "HOME": "/root"}


@pytest.mark.parametrize("unreadable_tail", [False, True])
def test_large_identity_inventory_runs_in_a_real_child(tmp_path, monkeypatch, unreadable_tail):
    groups = set(os.getgroups()) | {os.getgid()}
    if len(groups) > 2:
        pytest.skip("stand-in identity requires at most own + runtime groups")
    python = tmp_path / "env/bin/python3"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    readable = tmp_path / ("runtime-source-" + "x" * 150)
    readable.write_text("public source")
    paths = [str(readable)] * 10000
    if unreadable_tail:
        paths[-1] = str(tmp_path / "missing-last-file")
    assert len(json.dumps(paths).encode()) > 131072
    ident = {"user": "stand-in", "uid": os.getuid(), "gid": os.getgid(),
             "home": str(tmp_path),
             "runtime_group": {"gid": next((g for g in groups if g != os.getgid()), os.getgid())}}
    dep = {"identity": ident, "envs": {"libero": str(tmp_path / "env")}}
    monkeypatch.setattr(E, "as_user", lambda dep, argv: argv)
    monkeypatch.setattr(E, "cell_env", lambda *args: {"HOME": str(tmp_path)})
    monkeypatch.setattr(E, "must_read", lambda *args: paths)
    monkeypatch.setattr(E, "must_not_write", lambda *args: [])
    monkeypatch.setattr(E, "forbidden_paths", lambda *args: [])
    if unreadable_tail:
        with pytest.raises(E.StartupFailed, match="missing-last-file"):
            E.identity_probe(dep, {"control": str(tmp_path)}, tmp_path, E.Host())
    else:
        result = E.identity_probe(dep, {"control": str(tmp_path)}, tmp_path, E.Host())
        assert result["checked_reads"] == 10000


def test_synthetic_readiness_payloads_decode_like_the_servers():
    sam3, = E.readiness_requests({"name": "A-sam3", "role": "sam3", "url": "http://127.0.0.1:8214"})
    assert sam3[0] == "http://127.0.0.1:8214/segment"
    raw = base64.b64decode(sam3[1]["image_base64"])
    assert raw.startswith(b"\x89PNG") and struct.unpack(">II", raw[16:24]) == (96, 96)
    idat = raw[raw.index(b"IDAT") + 4:raw.index(b"IEND") - 8]
    assert len(zlib.decompress(idat)) == 96 * (1 + 96 * 3)
    grasp, = E.readiness_requests({"name": "graspnet", "url": "http://127.0.0.1:8115"})
    arrays = {k: np.load(io.BytesIO(base64.b64decode(grasp[1][k])))
              for k in ("depth_base64", "cam_K_base64", "segmap_base64")}
    assert arrays["depth_base64"].shape == (120, 160) and arrays["cam_K_base64"].shape == (3, 3)
    assert set(np.unique(arrays["segmap_base64"])) == {0, 1}
    ik, = E.readiness_requests({"name": "pyroki", "url": "http://127.0.0.1:8116"})
    assert ik[0].endswith("/ik") and len(ik[1]["target_pose_wxyz_xyz"]) == 7
    qwen, = E.readiness_requests({"name": "qwen", "url": "http://127.0.0.1:8121"})
    assert qwen == ("http://127.0.0.1:8121/v1/models", None)


def test_lifecycle_stops_only_its_own_process_trees(tmp_path):
    life = E.Lifecycle(E.Host(), tmp_path, grace=5)
    tree = [sys.executable, "-c", "import subprocess,sys,time;"
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(600)']);time.sleep(600)"]
    record = {}
    process = life.start("svc", tree, cwd="/", env={"PATH": "/usr/bin:/bin"}, record=record)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],
                                 start_new_session=True)
    try:
        deadline = time.time() + 10
        while len(life.owned()) < 2 and time.time() < deadline:
            time.sleep(0.1)
        owned = life.owned()
        assert record["pid"] == process.pid and record["pgid"] == process.pid and len(owned) == 2
        result = life.stop()
        assert set(result["signalled"]) == set(owned)
        assert not E.Lifecycle._alive(owned)
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait()


def test_private_dir_creates_0700_or_refuses_a_shared_directory(tmp_path):
    made = tmp_path / "cell"
    assert E.private_dir(made, 0, 0) == "created"
    assert oct(made.stat().st_mode & 0o777) == "0o700"
    assert E.private_dir(made, 0, 0) == "existing"
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    os.chmod(shared, 0o755)
    with pytest.raises(E.EntryRefused, match="0700"):
        E.private_dir(shared, 0, 0)
    with pytest.raises(E.EntryRefused, match="parent"):
        E.private_dir(tmp_path / "missing/child", 0, 0)


def test_posix_permission_check_follows_ancestors_and_groups():
    base = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        os.chmod(base, 0o755)
        group_dir = base / "home"
        group_dir.mkdir()
        os.chown(group_dir, 999, 999)
        os.chmod(group_dir, 0o750)
        (group_dir / "f").write_text("x")
        assert E.permitted(group_dir / "f", 30010, {30010, 999}, 4)  # via the shared group
        assert not E.permitted(group_dir / "f", 30010, {30010}, 4)
        os.chmod(group_dir, 0o700)
        assert not E.permitted(group_dir / "f", 30010, {30010, 999}, 4)
        assert E.permitted(group_dir / "f", 999, {999}, 4)
    finally:
        subprocess.run(["rm", "-rf", str(base)])


def test_single_entry_refuses_multi_worker_topology_and_deployments(tmp_path):
    with pytest.raises(E.EntryRefused, match="single-worker"):
        E.topology({"WORLD_SIZE": "2", "RANK": "0"})
    assert E.topology({"WORLD_SIZE": "1"}) == {"WORLD_SIZE": "1"}
    path = tmp_path / "dep.json"
    path.write_text(json.dumps({"schema": E.SCHEMA, "workers": 2}))
    with pytest.raises(E.EntryRefused, match="requires"):
        E.load_deployment(path)
