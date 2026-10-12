"""CPU tests for the two-worker DLC pair entry (one shared Qwen, two cells per node).

Nothing here starts a model, perception service, simulator, native CC or DLC job.
The host boundary is fake: user tables, nvidia-smi, probes, HTTP readiness and
the DLC rank environment. Services and campaigns are stand-in Python processes
(sleep or exit), started in their own process groups, so the lifecycle and
teardown code is exercised for real. Deployment loading, slot mapping, mode/
handoff checks, POSIX permission checks and service specs are the real code.
"""
import grp
import hashlib
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO / "scripts/common"))
import native_cc_runtime  # noqa: E402
import streaming_dlc_entry as E  # noqa: E402
import streaming_dlc_pair as P  # noqa: E402

MODEL = "qwen3.8-flash-next"
PREFIX = "/root/github/aspire-world"
START, DEADLINE = 1791627274.3236666, 1791677674.3236666
SECRETS = {"ANTHROPIC_API_KEY": "sk-secret", "OSS_ACCESS_KEY_ID": "ak", "HTTPS_PROXY":
           "http://127.0.0.1:10809", "NCCL_IB_HCA": "mlx5", "PYTHONPATH": "/root/x",
           "MASTER_ADDR": "dlc-master", "MASTER_PORT": "23456"}
STANDIN = r"""
import json, sys, time
mode = sys.argv[1]
if mode == "serve":
    time.sleep(600)
elif mode.startswith("exit:"):
    _, delay, code = mode.split(":")
    time.sleep(float(delay))
    sys.exit(int(code))
elif mode.startswith("rewrite:"):
    path = mode.split(":", 1)[1]
    data = json.load(open(path))
    data["phase_clock"]["development"]["deadline"] += 3600
    json.dump(data, open(path, "w"))
"""


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path: Path, value) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return str(path), sha(path)


def tree(root: Path) -> dict:
    return {str(p): (p.stat().st_mode, p.stat().st_uid, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def world():
    # Not under /tmp/pytest-of-root (0700): the cell users' traversal is checked.
    base = Path(tempfile.mkdtemp(prefix="dlcpair-", dir="/tmp"))
    os.chmod(base, 0o755)
    try:
        yield build(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def build(base: Path) -> SimpleNamespace:
    runtime = base / "runtime"
    repo = runtime / "aspire/sim"
    (repo / ".venv-libero/bin").mkdir(parents=True)
    (repo / ".venv-libero/bin/python3").write_text("")
    envs = {k: base / "envs" / k for k in ("qwen", "libero", "cuda_compat", "cuda_toolkit")}
    for path in envs.values():
        (path / "bin").mkdir(parents=True)
    (envs["qwen"] / "bin/python").write_text("")
    (envs["libero"] / "bin/python").write_text("")
    (base / "cuda").mkdir()
    deploy = base / "deploy"
    seed = base / "flashinfer-seed"
    (seed / "cached").mkdir(parents=True)
    argv = [str(envs["qwen"] / "bin/python"), "vllm", "serve", str(base / "models/qwen"),
            "--host", "127.0.0.1", "--port", "8121", "--tensor-parallel-size", "4",
            "--max-model-len", "1000000", "--max-num-seqs", "4"]
    qwen = {"argv": argv, "argv_sha256": hashlib.sha256(json.dumps(argv, sort_keys=True)
                                                        .encode()).hexdigest(),
            "env": {"CUDA_VISIBLE_DEVICES": "2,3,4,5", "VLLM_PLE_CPU_OFFLOAD": "1",
                    "LD_LIBRARY_PATH": str(envs["cuda_compat"]), "CUDA_HOME": str(envs["cuda_toolkit"]),
                    "FLASHINFER_WORKSPACE_BASE": str(seed), "HF_HUB_OFFLINE": "1"},
            "cwd": PREFIX + "/aspire/sim", "endpoint": "http://127.0.0.1:8121",
            "served_model": MODEL, "max_model_len": 1000000}
    model, model_sha = dump(deploy / "qwen-model-service-manifest.json", qwen)

    def task(name, port, extra=None):
        return {"name": name, "port": port, "gpu": "6",
                "argv": [str(envs["libero"] / "bin/python"), "-u", f"cap/serving/{name}.py",
                         "--port", str(port), "--host", "127.0.0.1"],
                "env": {"CUDA_VISIBLE_DEVICES": "" if "pyroki" in name else "6",
                        "CUDA_HOME": str(base / "cuda"), "PYTHONPATH": PREFIX,
                        "LD_LIBRARY_PATH": str(envs["cuda_compat"]),
                        "LIBERO_CONFIG_PATH": "/old/libero-config", **(extra or {})}}
    common = {"startup_manifest": {"tasks": [task("sam3-service", 8114),
                                             task("graspnet-service", 8115),
                                             task("pyroki-service", 8116, {"JAX_PLATFORMS": "cpu"})]},
              "sam_weights": [{"name": "SAM3", "sha256": "ab"}],
              "robot_model": {"cache_path": str(base / "assets/robot/example-robot-data")},
              "versions": {"torch": "2.12.1+cu126"}}

    def perception(path, port):
        urls = {"sam3_url": f"http://127.0.0.1:{port}", "graspnet_url":
                f"http://127.0.0.1:{port + 1}", "pyroki_url": f"http://127.0.0.1:{port + 2}"}
        return (*dump(path, {**urls, **common}), urls)
    descriptor, descriptor_sha, _ = perception(deploy / "perception-service-manifest.json", 8114)
    runtime_manifest, runtime_sha = dump(deploy / "freeze.json",
                                         {"files": {}, "external_files": {}, "executables": {}})
    egl, _ = dump(deploy / "egl/10_nvidia.json", {"ICD": {"library_path": "libEGL_nvidia.so"}})
    (deploy / "libero-config").mkdir()
    (deploy / "libero-config/config.yaml").write_text("benchmark_root: /frozen\n")
    claude = base / "tools/claude"
    claude.parent.mkdir()
    claude.write_text("")
    (base / "assets/sam2/sam2").mkdir(parents=True)
    (base / "assets/sam2.pt").write_text("")
    (base / "runs").mkdir()
    (base / "runs-bowl").mkdir()
    (base / "users").mkdir()
    tasks = {"bowl": "put_the_bowl_on_the_plate",
             "bowldrawer": "open_the_top_drawer_and_put_the_bowl_inside",
             "drawer": "open_the_middle_drawer_of_the_cabinet",
             "wine": "put_the_wine_bottle_on_the_rack"}
    states, _ = dump(base / "assets/states/initial_state_manifest.json",
                     {"tasks": {t: {"development": {"states": {}}} for t in tasks.values()}})

    def cell(short, slot, uid, mode):
        cid = f"{short}_sam2streaming_r1"
        layout = P.SLOTS[slot]
        port = layout["ports"]["sam3"]
        if slot == "A":
            pm, pm_sha, urls = perception(deploy / f"perception-{cid}.json", port)
        else:
            pm, pm_sha, urls = descriptor, descriptor_sha, {
                "sam3_url": "http://127.0.0.1:8114", "graspnet_url": "http://127.0.0.1:8115",
                "pyroki_url": "http://127.0.0.1:8116"}
        control = (base / "runs-bowl" if mode == "migrate" else base / "runs") / cid
        home = base / "users" / (f"{short}-r1")
        task_manifest, task_sha = dump(deploy / f"{short}-task.json", {"task": tasks[short]})
        case = {"id": cid, "method": "sam2-streaming", "repeat": 1, "profile": "sam2-streaming-native-v1",
                "sim": str(repo), "control": str(control), "suite": "libero_goal_swap",
                "task": tasks[short], **E.CASE_FIXED,
                "env_config": "env_configs/libero/franka_libero_traced.yaml",
                "model_service": {"endpoint": "http://127.0.0.1:8121", "served_model": MODEL,
                                  "server_manifest": model, "server_manifest_sha256": model_sha},
                "cuda_visible_devices": layout["simulator"], "egl_device_id": layout["egl_device_id"],
                **urls, "perception_manifest": pm, "perception_manifest_sha256": pm_sha,
                "streaming": {"camera": "agentview", "sam2": {
                    "source_root": str(base / "assets/sam2"), "config_file": "sam2.1/s.yaml",
                    "checkpoint": str(base / "assets/sam2.pt"), "device": "cuda"}},
                "runtime_manifest": runtime_manifest, "runtime_manifest_sha256": runtime_sha,
                "claude_bin": str(claude), "claude_config_dir": str(control / "claude-config"),
                "libero_config_path": str(deploy / "libero-config"),
                "task_manifest": task_manifest, "task_manifest_sha256": task_sha,
                "initial_state_manifest": states, "egl_vendor_config": egl}
        case_path, case_sha = dump(deploy / f"{cid}.case.json", case)
        user = "aspire-bowl-r1" if mode == "migrate" else f"aspire-{short}-r1"
        entry = {"cell": cid, "case": case_path, "case_sha256": case_sha, "control": str(control),
                 "mode": mode, "identity": {"user": user, "uid": uid, "group": user, "gid": uid,
                                            "home": str(home)}}
        if mode == "migrate":
            entry["migration"] = {"development_started_at": START, "development_deadline": DEADLINE}
        return entry
    pair = {"schema": P.SCHEMA, "workers": 2, "gpus_per_worker": 8, "runtime_root": str(runtime),
            "source_checkout_prefix": PREFIX, "envs": {k: str(v) for k, v in envs.items()},
            "entry_root": str(base / "entry"),
            "runtime_group": {"name": "aspire-bowl-r1", "gid": 999},
            "model_service_manifest": model, "model_service_manifest_sha256": model_sha,
            "perception_descriptor": descriptor, "perception_descriptor_sha256": descriptor_sha,
            "flashinfer_seed_cache": str(seed), "service_ready_timeout_seconds": 600,
            "handoff_wait_seconds": 3600, "poll_seconds": 15,
            "ranks": {"0": {"A": cell("bowldrawer", "A", 30010, "fresh"),
                            "B": cell("bowl", "B", 999, "migrate")},
                      "1": {"A": cell("drawer", "A", 30011, "fresh"),
                            "B": cell("wine", "B", 30012, "fresh")}}}
    w = SimpleNamespace(base=base, repo=repo, pair=pair, path=base / "deploy/pair.json")
    # The migrated RS cell: private HOME/control owned by uid 999, an open ledger.
    r1 = Path(pair["ranks"]["0"]["B"]["control"])
    (r1 / "cell").mkdir(parents=True)
    ledger(w, {"status": "stage1_incomplete"})
    for path in (r1, Path(pair["ranks"]["0"]["B"]["identity"]["home"])):
        path.mkdir(exist_ok=True)
        os.chown(path, 999, 999)
        os.chmod(path, 0o700)
    save(w)
    return w


def ledger(w, campaign=None, **change):
    r1 = Path(w.pair["ranks"]["0"]["B"]["control"])
    data = {"identity": {"cell": "bowl_sam2streaming_r1"}, "stage1_complete": False,
            "selected": None, "phase_clock": {"development": {
                "started_at": START, "watchdog_seconds": 50400, "deadline": DEADLINE}},
            "trials": [{"phase": "initial", "seed": s, "status": "complete"} for s in range(51, 66)]
            + [{"phase": "repair", "seed": 51, "status": "complete"}]}
    data.update(change)
    (r1 / "cell/development_state.json").write_text(json.dumps(data))
    if campaign is not None:
        (r1 / "campaign_state.json").write_text(json.dumps(campaign))
    return sha(r1 / "cell/development_state.json")


def handoff(w, **change):
    r1 = Path(w.pair["ranks"]["0"]["B"]["control"])
    record = {"cell": "bowl_sam2streaming_r1", "quiesced": True, "old_host": "dsw-908087",
              "old_pids": [4242], "ledger_sha256": sha(r1 / "cell/development_state.json"),
              "development_started_at": START, "development_deadline": DEADLINE,
              "quiesced_at": "2026-10-11T00:00:00+00:00", **change}
    (r1 / P.HANDOFF).write_text(json.dumps(record))


def save(w):
    w.path.write_text(json.dumps(w.pair))


class Clock:
    def __init__(self):
        self.now = 1000.0


class FakeHost(P.PairHost):
    def __init__(self, w, rank, **kw):
        self.w, self.clock_ = w, Clock()
        self.environ = {"WORLD_SIZE": "2", "RANK": str(rank), "LANG": "C.UTF-8",
                        "HOME": "/root", **SECRETS, **kw.pop("env", {})}
        self.users = {"root": pwd.struct_passwd(("root", "x", 0, 0, "", "/root", "/bin/bash"))}
        self.groups = {"root": grp.struct_group(("root", "x", 0, []))}
        self.calls, self.spawned, self.listening_ports = [], [], set(kw.pop("listening", ()))
        self.behaviour = kw.pop("behaviour", {})
        self.egl = kw.pop("egl", list(range(8)))
        self.on_sleep = kw.pop("on_sleep", [])
        self.alive = kw.pop("alive", set())
        self.mods = SimpleNamespace(camp=SimpleNamespace(load=self.load), runtime=native_cc_runtime)

    # identity tables
    def user(self, name=None, uid=None):
        return next((u for u in self.users.values() if name in (None, u.pw_name)
                     and uid in (None, u.pw_uid)), None)

    def group(self, name=None, gid=None):
        return next((g for g in self.groups.values() if name in (None, g.gr_name)
                     and gid in (None, g.gr_gid)), None)

    def euid(self):
        return 0

    def hostname(self):
        return "dlc-node"

    def pid_alive(self, pid):
        return pid in self.alive

    def modules(self, sim, libero_config):
        assert (Path(libero_config) / "config.yaml").is_file()
        return self.mods

    @staticmethod
    def load(path):
        case = json.loads(Path(path).read_text())
        return case, Path(case["sim"]).resolve(), Path(case["control"]).resolve() / "cell"

    def listening(self, port):
        return port in self.listening_ports

    def clock(self):
        return self.clock_.now

    def sleep(self, seconds):
        self.clock_.now += seconds
        time.sleep(0.05)
        while self.on_sleep and self.on_sleep[0][0](self):
            self.on_sleep.pop(0)[1](self)

    def run(self, argv, **kw):
        self.calls.append(("run", list(argv), kw.get("env")))
        ok = lambda out: subprocess.CompletedProcess(argv, 0, out, "")  # noqa: E731
        if argv[0] == "groupadd":
            gid, name = int(argv[2]), argv[3]
            self.groups[name] = grp.struct_group((name, "x", gid, []))
            return ok("")
        if argv[0] == "useradd":
            uid, gid, extra, home, name = int(argv[2]), int(argv[4]), int(argv[6]), argv[8], argv[-1]
            self.users[name] = pwd.struct_passwd((name, "x", uid, gid, "", home, "/bin/bash"))
            g = self.group(gid=extra)
            self.groups[g.gr_name] = grp.struct_group((g.gr_name, "x", g.gr_gid, [*g.gr_mem, name]))
            return ok("")
        if argv[0] == "nvidia-smi":
            if "--query-gpu=index,uuid,name,memory.total,memory.used" in argv:
                return ok("".join(f"{i}, GPU-{i}, NVIDIA L20Z, 81559, 4\n" for i in range(8)))
            return ok("")
        assert argv[0] == "/usr/sbin/runuser", argv
        user = self.user(name=argv[2])
        code = argv[argv.index("-c") + 1]
        if code == E.IDENTITY_PROBE:
            spec = json.loads(kw["input"])
            return ok(json.dumps({"uid": user.pw_uid, "euid": user.pw_uid, "gid": user.pw_gid,
                                  "groups": sorted({user.pw_gid, 999}), "home": user.pw_dir,
                                  "forbidden_accessible": [], "unreadable": [],
                                  "writable_shared": [], "control_writable": True,
                                  "home_writable": True, "seen_forbidden": spec["forbidden"]}))
        if code == E.EGL_MAP_PROBE:
            return ok(json.dumps({"egl_devices": 8, "egl_to_cuda": self.egl}))
        assert code == E.DEVICE_PROBE
        env = kw["env"]
        return ok(json.dumps({"env": {}, "torch": "2.12.1+cu126", "cuda_available": True,
                              "cuda_devices": ["NVIDIA L20Z"], "cuda_kernel_ok": True,
                              "egl_devices": 8, "render_nonzero": True,
                              "gl_vendor": "NVIDIA Corporation",
                              "gl_renderer": "NVIDIA L20Z/PCIe/SSE2", "gl_version": "4.6",
                              "sim_gpu": env["CUDA_VISIBLE_DEVICES"],
                              "egl_id": env["MUJOCO_EGL_DEVICE_ID"]}))

    def popen(self, argv, *, cwd, env, stdout, stderr):
        user = argv[2] if argv[0] == "/usr/sbin/runuser" else "root"
        name = next((n for n, b in self.behaviour.items() if n in " ".join(argv)), None)
        mode = self.behaviour.get(name, "serve" if "--port" in argv else "exit:0.3:0")
        if mode == "rewrite":
            mode = "rewrite:" + str(Path(json.loads(Path(argv[argv.index("--case") + 1])
                                                    .read_text())["control"]) /
                                    "cell/development_state.json")
        process = subprocess.Popen([sys.executable, "-I", "-c", STANDIN, mode], cwd="/",
                                   env={"PATH": "/usr/bin:/bin"}, stdout=stdout, stderr=stderr,
                                   start_new_session=True)
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else None
        self.spawned.append({"argv": list(argv), "env": dict(env), "cwd": cwd, "user": user,
                             "port": port, "process": process})
        return process

    def http(self, url, payload=None, timeout=120):
        port = int(url.split(":")[2].split("/")[0])
        live = [s for s in self.spawned if s["port"] == port and s["process"].poll() is None]
        if not live:
            return None, "connection refused"
        if url.endswith("/v1/models"):
            return 200, {"data": [{"id": MODEL, "max_model_len": 1000000}]}
        return 200, {"/segment": {"results": []}, "/plan": {"grasps_base64": ""},
                     "/ik": {"joint_positions": [0.0] * 8}}[url[url.rindex("/"):]]


@pytest.fixture(autouse=True)
def assets(monkeypatch):
    """Weight/freeze rehashing is covered on RS by --validate-only, not with fakes."""
    monkeypatch.setattr(E, "verify_model", lambda *a, **k: {"problems": []})
    monkeypatch.setattr(E, "verify_cell_assets", lambda *a, **k: {"problems": []})
    monkeypatch.setattr(E, "set_subreaper", lambda: False)


def launch(w, rank, **kw):
    host = FakeHost(w, rank, **kw)
    code = P.launch(w.path, host=host)
    node = Path(w.pair["entry_root"]) / f"rank{rank}"
    state = json.loads((node / "pair_state.json").read_text())
    return code, host, state["attempts"][-1], state


def started(host, needle):
    return [s for s in host.spawned if needle in " ".join(s["argv"])]


def leftovers(host):
    return [s["argv"][:4] for s in host.spawned if s["process"].poll() is None]


# --- deployment, rank and slot mapping --------------------------------------------


def test_rank_env_is_checked_recorded_and_never_guessed():
    assert P.rank_of({"WORLD_SIZE": "2", "RANK": "1", "MASTER_ADDR": "m"}) == \
        {"rank": 1, "recorded": {"WORLD_SIZE": "2", "RANK": "1", "MASTER_ADDR": "m"}}
    for env in ({}, {"WORLD_SIZE": "1", "RANK": "0"}, {"WORLD_SIZE": "3", "RANK": "0"},
                {"WORLD_SIZE": "2", "RANK": "2"}, {"WORLD_SIZE": "2", "RANK": "0", "NODE_RANK": "1"},
                {"WORLD_SIZE": "2", "RANK": "1", "LOCAL_RANK": "1"}):
        with pytest.raises(P.EntryRefused):
            P.rank_of(env)


def test_two_ranks_map_four_cells_to_disjoint_gpus_ports_and_users(world):
    pair, _ = P.load_pair(world.path)
    seen = {}
    for rank, slot, cell in P.cells_of(pair):
        dep = P.slot_dep(pair, rank, slot)
        layout = P.SLOTS[slot]
        seen[cell["cell"]] = (rank, slot, layout["sam3"], layout["simulator"],
                              layout["ports"]["sam3"], dep["identity"]["uid"])
        others = {c["control"] for _, _, c in P.cells_of(pair) if c is not cell}
        assert others <= set(dep["forbidden_read_paths"])
        assert dep["identity"]["runtime_group"] == {"name": "aspire-bowl-r1", "gid": 999}
    assert seen == {"bowldrawer_sam2streaming_r1": (0, "A", "0", "1", 8214, 30010),
                    "bowl_sam2streaming_r1": (0, "B", "6", "7", 8114, 999),
                    "drawer_sam2streaming_r1": (1, "A", "0", "1", 8214, 30011),
                    "wine_sam2streaming_r1": (1, "B", "6", "7", 8114, 30012)}
    # Qwen is outside both slots' GPUs on every node.
    for layout in P.SLOTS.values():
        assert not set(layout["qwen"].split(",")) & {layout["sam3"], layout["simulator"]}


@pytest.mark.parametrize("change, message", [
    (lambda p: p.update(workers=3), "2 workers"),
    (lambda p: p["ranks"]["1"]["A"]["identity"].update(uid=30012), "used by two cells"),
    (lambda p: p["ranks"]["1"]["A"]["identity"].update(gid=999), "must differ from the runtime"),
    (lambda p: p["ranks"]["1"]["A"].update(mode="restart"), "mode must be one of"),
    (lambda p: p["ranks"]["0"]["B"]["migration"].update(development_deadline=DEADLINE + 1),
     "50,400"),
    (lambda p: p["ranks"]["1"]["B"].update(control=p["ranks"]["1"]["A"]["control"] + "/x"),
     "nested"),
    (lambda p: p["ranks"].update({"2": p["ranks"]["1"]}), "match declared workers"),
    (lambda p: p.update(cells=[]), None),
])
def test_deployment_refusals(world, change, message):
    change(world.pair)
    save(world)
    if message is None:  # unknown top-level keys are not used for anything
        P.load_pair(world.path)
        return
    with pytest.raises(P.EntryRefused, match=message):
        P.load_pair(world.path)


# --- a full node: one Qwen, two cells ---------------------------------------------


def test_rank1_starts_one_qwen_and_two_isolated_fresh_cells(world):
    code, host, attempt, state = launch(world, 1)
    assert code == 0, attempt.get("blocker") or attempt["slots"]
    qwen = started(host, "vllm")
    assert len(qwen) == 1, "exactly one Qwen per node"
    manifest = json.loads(Path(world.pair["model_service_manifest"]).read_text())
    assert qwen[0]["argv"] == manifest["argv"]
    assert qwen[0]["env"]["CUDA_VISIBLE_DEVICES"] == "2,3,4,5"
    assert qwen[0]["env"]["VLLM_PLE_CPU_OFFLOAD"] == "1"
    assert qwen[0]["env"]["FLASHINFER_WORKSPACE_BASE"].startswith(world.pair["entry_root"])
    perception = {(s["env"]["CUDA_VISIBLE_DEVICES"], s["port"])
                  for s in host.spawned if s["port"] and s["port"] != 8121}
    assert perception == {("0", 8214), ("0", 8215), ("", 8216), ("6", 8114), ("6", 8115), ("", 8116)}
    campaigns = started(host, E.CAMPAIGN)
    assert sorted(c["argv"][2] for c in campaigns) == ["aspire-drawer-r1", "aspire-wine-r1"]
    for c in campaigns:
        assert "--resume" not in c["argv"] and c["argv"][0] == "/usr/sbin/runuser"
        assert c["env"]["HOME"] == host.user(name=c["argv"][2]).pw_dir
    for spawned in host.spawned:  # no credential, proxy or distributed variable leaks
        leaked = set(spawned["env"]) & {*SECRETS, "WORLD_SIZE", "RANK"}
        assert not leaked - {"PYTHONPATH"}, (spawned["argv"][:3], leaked)
        if spawned in campaigns:
            assert spawned["env"]["PYTHONPATH"] == world.pair["runtime_root"]
    assert {u.pw_uid for u in host.users.values()} == {0, 30011, 30012}
    assert host.group(gid=999).gr_mem == ["aspire-drawer-r1", "aspire-wine-r1"]
    for slot in ("A", "B"):
        cell = world.pair["ranks"]["1"][slot]
        assert Path(cell["control"]).stat().st_uid == cell["identity"]["uid"]
        assert oct(Path(cell["control"]).stat().st_mode & 0o777) == "0o700"
        assert attempt["slots"][slot]["status"] == "campaign_exited"
        logs = Path(world.pair["entry_root"]) / "cells" / cell["cell"] / "logs/01"
        assert {p.name for p in logs.iterdir()} == {f"{slot}-sam3.log", f"{slot}-graspnet.log",
                                                    f"{slot}-pyroki.log"}
    ready = json.loads((Path(world.pair["entry_root"]) / "rank1/services_ready.json").read_text())
    assert set(ready["ready"]) == {"qwen", "A-sam3", "A-graspnet", "A-pyroki",
                                   "B-sam3", "B-graspnet", "B-pyroki"}
    assert attempt["rank"]["recorded"]["MASTER_ADDR"] == "dlc-master"
    assert attempt["services"]["A-sam3"]["adaptations"]["--port"] == \
        {"manifest": 8114, "actual": 8214, "reason": "this cell's own loopback port (case URL)"}
    assert not leftovers(host)


def test_one_cells_perception_failure_does_not_touch_the_other_cell(world):
    node = Path(world.pair["entry_root"]) / "rank1/pair_state.json"
    observed = []

    def failure_published(host):
        state = json.loads(node.read_text())["attempts"][-1]
        return state.get("slots", {}).get("A", {}).get("status") == "service_failed"

    def observe(host):
        observed.append(json.loads(node.read_text())["attempts"][-1])

    code, host, attempt, _ = launch(world, 1, behaviour={
        "8214": "exit:1.0:1", "drawer_sam2streaming_r1.case": "exit:60:0",
        "wine_sam2streaming_r1.case": "exit:2.0:0"},
        on_sleep=[(failure_published, observe)])
    a, b = attempt["slots"]["A"], attempt["slots"]["B"]
    assert a["status"] == "service_failed" and "A-sam3" in a["blocker"]
    assert a["campaign"].get("exit_code") is None  # killed by the entry, never finished
    assert b["status"] == "campaign_exited" and b["campaign"]["exit_code"] == 0
    assert observed and observed[0]["slots"]["B"]["status"] == "campaign_running"
    assert observed[0]["health_checked_at"]
    qwen_pid = attempt["services"]["qwen"]["pid"]
    assert qwen_pid in attempt["teardown"]["signalled"], "Qwen is reclaimed only at the end"
    assert code == 1 and not leftovers(host)


def test_shared_qwen_failure_stops_both_campaigns(world):
    code, host, attempt, _ = launch(world, 1, behaviour={
        "vllm": "exit:1.5:1", ".case": "exit:60:0"})
    assert attempt["status"] == "startup_failed" and "shared Qwen" in attempt["blocker"]
    assert {s["status"] for s in attempt["slots"].values()} == {"service_failed"}
    assert code == 1 and not leftovers(host)


def test_wrong_egl_mapping_fails_only_that_slot(world):
    egl = list(range(8))
    egl[1] = 0  # EGL device 1 is not CUDA GPU 1
    code, host, attempt, _ = launch(world, 1, egl=egl)
    assert attempt["slots"]["A"]["status"] == "startup_failed"
    assert "EGL device 1 is not CUDA GPU 1" in attempt["slots"]["A"]["blocker"]
    assert attempt["slots"]["B"]["status"] == "campaign_exited"
    assert not started(host, "8214") and not leftovers(host)


def test_listening_port_refuses_the_node_before_any_process_or_user(world):
    code, host, attempt, _ = launch(world, 1, listening={8121})
    assert code == 2 and "8121" in attempt["blocker"]
    assert not host.spawned and not [c for c in host.calls if c[1][0] == "useradd"]


def test_terminate_signal_reclaims_everything_and_keeps_the_ledger(world):
    before = sha(Path(world.pair["ranks"]["0"]["B"]["control"]) / "cell/development_state.json")
    handoff(world)
    fire = [(lambda h: bool(started(h, E.CAMPAIGN)),
             lambda h: os.kill(os.getpid(), signal.SIGTERM))]
    code, host, attempt, _ = launch(world, 0, behaviour={".case": "exit:60:0"}, on_sleep=fire)
    assert attempt["status"] == "terminated" and not leftovers(host)
    after = Path(world.pair["ranks"]["0"]["B"]["control"]) / "cell/development_state.json"
    assert sha(after) == before


def test_unclosed_attempt_refuses_a_second_entry_on_the_same_rank(world):
    node = Path(world.pair["entry_root"]) / "rank1"
    node.mkdir(parents=True)
    os.chmod(world.pair["entry_root"], 0o700)
    os.chmod(node, 0o700)
    (node / "pair_state.json").write_text(json.dumps({"rank": 1, "cells": {}, "attempts": [
        {"n": 1, "started_at": "x"}]}))
    host = FakeHost(world, 1)
    with pytest.raises(P.EntryRefused, match="never closed"):
        P.launch(world.path, host=host)
    assert not host.spawned


# --- migration of the RS cell -----------------------------------------------------


def test_migration_waits_for_the_handoff_then_resumes_with_the_original_clock(world):
    ready = Path(world.pair["entry_root"]) / "rank0/services_ready.json"
    fire = [(lambda h: ready.is_file(), lambda h: handoff(world))]
    code, host, attempt, state = launch(world, 0, on_sleep=fire)
    assert code == 0, attempt["slots"]
    b = attempt["slots"]["B"]
    assert b["campaign"]["argv"][:3] == ["/usr/sbin/runuser", "-u", "aspire-bowl-r1"]
    assert "--resume" in b["campaign"]["argv"]
    assert "--resume" not in attempt["slots"]["A"]["campaign"]["argv"]
    assert b["handoff"]["handoff"]["old_host"] == "dsw-908087"
    clock = {"development": {"started_at": START, "watchdog_seconds": 50400, "deadline": DEADLINE}}
    assert b["ledger_before"]["phase_clock"] == b["ledger_after"]["phase_clock"] == clock
    assert b["ledger_before"]["trials"] == 16 and b["ledger_before"]["charged_per_seed"]["51"] == 2
    assert b["ledger_invariants"].startswith("kept")
    assert state["cells"]["bowl_sam2streaming_r1"]["phase_clock"] == clock
    assert host.user(name="aspire-bowl-r1").pw_uid == 999
    assert ("/usr/sbin/runuser", "aspire-bowl-r1") in {(c["argv"][0], c["argv"][2])
                                             for c in started(host, E.CAMPAIGN)}
    # B keeps the RS mapping: perception on 6 at 8114-8116, simulator/EGL 7.
    assert {s["port"] for s in host.spawned if s["port"] and s["env"].get("CUDA_VISIBLE_DEVICES") == "6"} \
        == {8114, 8115}
    assert not leftovers(host)


def test_ledger_change_during_the_campaign_is_reported(world):
    handoff(world)
    code, host, attempt, _ = launch(world, 0, behaviour={"bowl_sam2streaming_r1.case": "rewrite"})
    assert attempt["slots"]["B"]["ledger_invariants"] == ["development phase clock changed"]


def test_missing_handoff_times_out_for_b_only(world):
    world.pair["handoff_wait_seconds"] = 60
    save(world)
    code, host, attempt, _ = launch(world, 0)
    assert attempt["slots"]["B"]["status"] == "refused"
    assert "not quiesced" in attempt["slots"]["B"]["blocker"]
    assert attempt["slots"]["A"]["status"] == "campaign_exited"
    assert not started(host, "bowl_sam2streaming_r1.case")


@pytest.mark.parametrize("setup, message", [
    (lambda w: ledger(w, {"status": "running"}), "campaign_state is running"),
    (lambda w: ledger(w, {"status": "stage1_finalized"}), "outer resume"),
    (lambda w: ledger(w, stage1_complete=True), "outer resume"),
    (lambda w: ledger(w, phase_clock={"development": {"started_at": START + 5,
                                                      "watchdog_seconds": 50400,
                                                      "deadline": DEADLINE + 5}}), "original"),
    (lambda w: ledger(w, trials=[{"phase": "initial", "seed": 51, "status": "running"}]),
     "trial row is still running"),
    (lambda w: ledger(w, identity={"cell": "other"}), "another cell"),
])
def test_migration_preconditions_refuse_b_and_leave_a_running(world, setup, message):
    setup(world)
    handoff(world)  # Invalid live state must be refused at handoff, after readiness.
    code, host, attempt, _ = launch(world, 0)
    assert attempt["slots"]["B"]["status"] == "refused"
    assert message in attempt["slots"]["B"]["blocker"]
    assert attempt["slots"]["A"]["status"] == "campaign_exited"
    assert not started(host, "bowl_sam2streaming_r1.case")


@pytest.mark.parametrize("change, message", [
    (dict(ledger_sha256="0" * 64), "ledger changed after the handoff"),
    (dict(quiesced=False), "not a quiesce record"),
    (dict(development_deadline=DEADLINE + 60), "clocks differ"),
    (dict(old_host="dlc-node"), "still alive"),
])
def test_inconsistent_handoff_is_refused(world, change, message):
    handoff(world, **change)
    code, host, attempt, _ = launch(world, 0, alive={4242})
    assert attempt["slots"]["B"]["status"] == "refused"
    assert message in attempt["slots"]["B"]["blocker"]
    assert not started(host, "bowl_sam2streaming_r1.case")


def test_fresh_cell_with_a_ledger_and_changed_case_are_refused(world):
    cell = world.pair["ranks"]["1"]["A"]
    (Path(cell["control"]) / "cell").mkdir(parents=True)
    (Path(cell["control"]) / "cell/development_state.json").write_text("{}")
    code, _, attempt, state = launch(world, 1)
    assert "already has a ledger" in attempt["slots"]["A"]["blocker"]
    assert "drawer_sam2streaming_r1" not in state["cells"]
    state["cells"]["drawer_sam2streaming_r1"] = {"case_sha256": "older"}
    with pytest.raises(P.EntryRefused, match="case changed"):
        P.preflight_slot(world.pair, 1, "A", FakeHost(world, 1).mods, FakeHost(world, 1),
                         state["cells"]["drawer_sam2streaming_r1"])


# --- isolation checks and validate-only -------------------------------------------


def test_group_readable_r1_home_is_refused_for_fresh_users(world):
    home = Path(world.pair["ranks"]["0"]["B"]["identity"]["home"])
    os.chmod(home, 0o750)  # the RS state today: r1 HOME is group 999 readable
    code, host, attempt, _ = launch(world, 0)
    assert attempt["slots"]["A"]["status"] == "refused"
    assert str(home) in attempt["slots"]["A"]["blocker"]
    # The migrated cell refuses its own non-private HOME at setup, before any probe.
    assert attempt["slots"]["B"]["status"] == "startup_failed"
    assert "0700" in attempt["slots"]["B"]["blocker"]
    assert code == 1 and not host.spawned


def test_validate_only_has_no_side_effects(world, capsys):
    before = tree(world.base)
    host = FakeHost(world, 0)
    assert P.validate_only(world.path, rehash_weights=False, host=host) == 0
    report = json.loads(capsys.readouterr().out)
    assert tree(world.base) == before and not host.calls and not host.spawned
    assert not Path(world.pair["entry_root"]).exists()
    assert set(report["cells"]) == {"rank0/A/bowldrawer_sam2streaming_r1",
                                    "rank0/B/bowl_sam2streaming_r1",
                                    "rank1/A/drawer_sam2streaming_r1",
                                    "rank1/B/wine_sam2streaming_r1"}
    assert report["cells"]["rank0/B/bowl_sam2streaming_r1"]["handoff"].startswith("pending")
    assert report["qwen_service"]["gpu"] == "2,3,4,5"
    ledger(world, {"status": "running"})
    assert P.validate_only(world.path, rehash_weights=False, host=FakeHost(world, 0)) == 0
    assert "pending" in capsys.readouterr().out


def test_cli_validate_only_refuses_a_bad_deployment(world, capsys):
    world.pair["workers"] = 1
    save(world)
    assert P.main(["--deployment", str(world.path), "--validate-only"]) == 2
    assert "ranks must match declared workers" in capsys.readouterr().out


def test_single_worker_pair_launches_one_qwen_and_two_isolated_cells(world):
    # Both originally rank1 slots are fresh cells; give them declared rank0.
    world.pair["workers"] = 1
    world.pair["ranks"] = {"0": world.pair["ranks"]["1"]}
    dump(world.path, world.pair)
    code, host, attempt, state = launch(world, 0, env={"WORLD_SIZE": "1"})
    assert code == 0 and attempt["status"] == "campaigns_exited"
    assert len(started(host, "vllm")) == 1
    assert len(started(host, "streaming_fixloop_campaign.py")) == 2
    assert not leftovers(host)
    assert P.rank_of({"WORLD_SIZE": "1", "RANK": "0"}, 1)["rank"] == 0
    with pytest.raises(P.EntryRefused):
        P.rank_of({"WORLD_SIZE": "2", "RANK": "0"}, 1)
    with pytest.raises(SystemExit):
        P.main(["--deployment", str(world.path), "--skip-weight-rehash"])


def test_validate_only_refuses_missing_outer_parent_before_startup(world, capsys):
    world.pair["entry_root"] = str(world.base / "not_prepared" / "entry")
    save(world)
    before = tree(world.base)
    host = FakeHost(world, 0)
    assert P.validate_only(world.path, rehash_weights=False, host=host) == 2
    assert "must already exist" in capsys.readouterr().out
    assert tree(world.base) == before and not host.calls and not host.spawned


def test_live_migration_warms_up_before_outer_quiesce(world):
    ledger(world, {"status": "running"})
    ready = Path(world.pair["entry_root"]) / "rank0/services_ready.json"
    def quiesce(host):
        assert not started(host, "bowl_sam2streaming_r1.case")
        ledger(world, {"status": "stage1_incomplete"})
        handoff(world)
    code, host, attempt, _ = launch(world, 0,
                                  on_sleep=[(lambda h: ready.is_file(), quiesce)])
    assert code == 0, attempt["slots"]
    assert "--resume" in attempt["slots"]["B"]["campaign"]["argv"]
    assert attempt["slots"]["B"]["ledger_invariants"].startswith("kept")


def test_evaluation_clock_first_initialization_keeps_development_budget(world):
    from types import SimpleNamespace
    cell = world.pair["ranks"]["0"]["B"]
    directory = Path(cell["control"]) / "cell"
    before = P.ledger_summary(directory)
    state_path = directory / "development_state.json"
    raw = json.loads(state_path.read_text())
    raw["phase_clock"]["evaluation"] = {"started_at": 77, "deadline": 18077,
                                        "watchdog_seconds": 18000}
    state_path.write_text(json.dumps(raw))
    slot = {"campaign": {}, "case": {"control": cell["control"]},
            "task_dir": directory, "ledger_before": before}
    P.finish_campaign(slot, 0)
    assert slot["ledger_invariants"].startswith("kept")
