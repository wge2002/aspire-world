"""Four-GPU production orchestration with real stand-in process lifetimes."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from test_streaming_dlc_pair import (world, assets, FakeHost, save, dump, sha,
                                     started, leftovers, E, P)
from qwen_decode_config import COMPILATION_CONFIG


@pytest.fixture
def unit4(world):
    w = world
    w.pair.update(workers=1, gpus_per_worker=4, layout_profile=P.UNIT4,
                  gpu_map=copy.deepcopy(P.UNIT4_GPU_MAP),
                  full_resolution_grasp_probe={"width": 800, "height": 512})
    w.pair["ranks"] = {"0": w.pair["ranks"]["1"]}
    path = Path(w.pair["model_service_manifest"])
    model = json.loads(path.read_text())
    model["argv"] += ["--compilation-config", json.dumps(COMPILATION_CONFIG),
                      "--gpu-memory-utilization", "0.82"]
    model["env"]["CUDA_VISIBLE_DEVICES"] = P.UNIT4_QWEN
    model["argv_sha256"] = hashlib.sha256(json.dumps(model["argv"], sort_keys=True).encode()).hexdigest()
    _, model_sha = dump(path, model)
    w.pair["model_service_manifest_sha256"] = model_sha
    for slot, cell in w.pair["ranks"]["0"].items():
        case = json.loads(Path(cell["case"]).read_text())
        case.update(cuda_visible_devices=P.UNIT4_SLOTS[slot]["simulator"],
                    egl_device_id=P.UNIT4_SLOTS[slot]["egl_device_id"],
                    perception_manifest=w.pair["perception_descriptor"],
                    perception_manifest_sha256=w.pair["perception_descriptor_sha256"],
                    sam3_url="http://127.0.0.1:8114", graspnet_url="http://127.0.0.1:8115",
                    pyroki_url="http://127.0.0.1:8116")
        case["model_service"]["server_manifest_sha256"] = model_sha
        _, cell["case_sha256"] = dump(Path(cell["case"]), case)
    save(w)
    return w


class UnitHost(FakeHost):
    def __init__(self, w, **kw):
        self.never_ready = kw.pop("never_ready", set())
        self.qwen_ready = False
        self.full_probes = 0
        kw.setdefault("egl", list(range(4)))
        super().__init__(w, 0, env={"WORLD_SIZE": "1"}, **kw)

    def run(self, argv, **kw):
        r = super().run(argv, **kw)
        if argv[0] == "nvidia-smi" and "--query-gpu=index,uuid,name,memory.total,memory.used" in argv:
            return subprocess.CompletedProcess(argv, 0, "\n".join(r.stdout.splitlines()[:4])+"\n", "")
        if "-c" in argv and argv[argv.index("-c")+1] in (E.DEVICE_PROBE, E.EGL_MAP_PROBE):
            d = json.loads(r.stdout);d["egl_devices"] = 4
            return subprocess.CompletedProcess(argv, 0, json.dumps(d), "")
        return r

    def popen(self, argv, **kw):
        if "--port" in argv and argv[argv.index("--port")+1] != "8121":
            assert self.qwen_ready, "colocated perception must load after Qwen memory profiling"
        return super().popen(argv, **kw)

    def http(self, url, payload=None, timeout=120):
        port = int(url.split(":")[2].split("/")[0])
        if port in self.never_ready:
            return None, "not ready"
        r = super().http(url, payload, timeout)
        if url.endswith("/v1/models") and r[0] == 200:
            self.qwen_ready = True
        if payload and port == 8115:
            import base64,io,numpy as np
            if np.load(io.BytesIO(base64.b64decode(payload["depth_base64"]))).shape == (512,800):
                self.full_probes += 1
        return r


def launch4(w, **kw):
    h = UnitHost(w, **kw)
    code = P.launch(w.path, host=h)
    state = json.loads((Path(w.pair["entry_root"])/"rank0/pair_state.json").read_text())
    return code,h,state["attempts"][-1]


def test_four_gpu_shared_services_start_once_after_qwen_and_cells_keep_isolated_devices(unit4):
    code,h,a = launch4(unit4)
    assert code == 0, a
    assert sorted(x["port"] for x in h.spawned if x["port"]) == [8114,8115,8116,8121]
    assert {s["env"]["CUDA_VISIBLE_DEVICES"] for s in started(h,"vllm")} == {"0,1,2,3"}
    campaigns = started(h,E.CAMPAIGN)
    assert {s["device_probe"]["sim_gpu"] for s in a["slots"].values()} == {"2","3"}
    assert {json.loads(Path(s["argv"][s["argv"].index("--case")+1]).read_text())["cuda_visible_devices"]
            for s in campaigns} == {"2","3"}
    assert len({s["env"]["HOME"] for s in campaigns}) == 2
    assert h.full_probes == 1
    assert all(s["status"] == "campaign_exited" for s in a["slots"].values())
    assert not leftovers(h)


def test_one_cell_finishes_without_stopping_services_used_by_other_cell(unit4):
    node = Path(unit4.pair["entry_root"])/"rank0/pair_state.json"
    seen=[]
    def done(h):
        s=json.loads(node.read_text())["attempts"][-1]["slots"]
        return s.get("A",{}).get("status")=="campaign_exited" and s.get("B",{}).get("status")=="campaign_running"
    def observe(h):
        seen.append(sorted(s["port"] for s in h.spawned if s["port"] and s["process"].poll() is None))
    code,h,a=launch4(unit4,behaviour={"drawer_sam2streaming_r1.case":"exit:0.3:0",
                                    "wine_sam2streaming_r1.case":"exit:1.4:0"},on_sleep=[(done,observe)])
    assert code==0 and seen==[[8114,8115,8116,8121]]
    assert not leftovers(h)


@pytest.mark.parametrize("port",[8121,8114,8115,8116])
def test_failure_of_any_shared_service_stops_both_active_campaigns(unit4,port):
    code,h,a=launch4(unit4,behaviour={str(port):"exit:0.8:1",".case":"exit:60:0"})
    assert code==1 and a["status"]=="startup_failed"
    assert all(s["status"]=="service_failed" for s in a["slots"].values())
    assert not leftovers(h)


@pytest.mark.parametrize("port",[8121,8114,8115,8116])
def test_shared_readiness_timeout_starts_no_campaign(unit4,port):
    unit4.pair["service_ready_timeout_seconds"]=30;save(unit4)
    code,h,a=launch4(unit4,never_ready={port})
    assert code==1 and a["status"]=="startup_failed"
    assert not started(h,E.CAMPAIGN) and not leftovers(h)


@pytest.mark.parametrize("change",[
    lambda p:p.update(gpus_per_worker=8),
    lambda p:p.update(workers=2),
    lambda p:p["gpu_map"]["slots"]["B"].update(simulator="2"),
    lambda p:p["gpu_map"]["sam3"].update(port=8214),
    lambda p:p.pop("full_resolution_grasp_probe"),
    lambda p:p.update(layout_profile="unknown"),
])
def test_invalid_gpu_count_layout_or_missing_post_load_probe_is_refused(unit4,change):
    change(unit4.pair);save(unit4)
    with pytest.raises(E.EntryRefused):P.load_pair(unit4.path)


def test_full_resolution_failure_refuses_both_without_creating_task_ledger(unit4):
    class BadGrasp(UnitHost):
        def http(self,url,payload=None,timeout=120):
            if payload and ":8115/" in url:
                import base64,io,numpy as np
                if np.load(io.BytesIO(base64.b64decode(payload["depth_base64"]))).shape==(512,800):
                    return 500,{"error":"capacity"}
            return super().http(url,payload,timeout)
    h=BadGrasp(unit4);assert P.launch(unit4.path,host=h)==1
    assert not started(h,E.CAMPAIGN) and not leftovers(h)
