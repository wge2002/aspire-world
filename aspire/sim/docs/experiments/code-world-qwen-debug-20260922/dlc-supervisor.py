#!/usr/bin/env python3
"""Start the verified local Qwen and perception services, then one frozen C pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "reference"))
from native_cc_runtime import (Service, ServiceWatch, atomic_json,
                               isolated_jit_env, new_attempt, stop_processes)

MODEL_CONFIG_SHA = "22c61284fd39db1310bc7cecbb09ec06ddcb417aeb4156fbea8a163829985c67"
GRASPNET_ROOT = Path("/mnt/home/gewang/code/ASPIRE/aspire/sim/cap/third_party/contact_graspnet_pytorch")
RENDEZVOUS = ("RANK", "LOCAL_RANK", "WORLD_SIZE", "LOCAL_WORLD_SIZE", "MASTER_ADDR",
              "MASTER_PORT", "GROUP_RANK", "ROLE_RANK", "TORCHELASTIC_RUN_ID")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_env():
    return {k: os.environ[k] for k in (
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
        "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES") if k in os.environ}


def python_entry(py, script, *args):
    # SIGTERM must unwind the native transport's finally block and its separately
    # owned CC process group, rather than orphaning it when the driver is stopped.
    wrapper = ("import runpy,signal,sys; "
               "signal.signal(signal.SIGTERM,lambda *_:signal.raise_signal(signal.SIGINT)); "
               "sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; "
               "runpy.run_path(sys.argv[0],run_name='__main__')")
    return [py, "-u", "-c", wrapper, str(Path(script).parent), str(script), *map(str, args)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--preflight-gpu", type=int)
    args = parser.parse_args()
    if args.preflight_gpu is not None and not args.preflight:
        parser.error("GPU override is only allowed for environment preflight")
    case_path = args.case.resolve()
    case = json.loads(case_path.read_text())
    repo, control = Path(case["sim"]), Path(case["control"])
    parent, py = control.parent, str(repo / ".venv-libero/bin/python3")
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign
    from native_world_protocol import runtime_env

    manifest = json.loads((HERE / "launch-manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if digest(HERE / name) != expected:
            raise RuntimeError(f"launch file changed: {name}")
    receipt = json.loads((parent / "prepare-receipt.json").read_text())
    assert digest(case_path) == receipt["cells"][case["id"]]["case_sha256"]
    for name, expected in receipt["frozen_support"].items():
        assert digest(parent / "support" / name) == expected, name
    assert case["dev_seeds"] == [51, 52, 53] and case["condition"] == "C"
    assert case["c_arm"] == "full" and case["executable_world_revision"] == "r1"
    assert case["model_provider"] == "local-vllm" and case["gpu"] == 6
    assert {case[k] for k in ("model", "model_tag", "expected_served_model")} == {"qwen3.8-flash-next"}
    assert "api_key_helper" not in case
    campaign.local_vllm_pilot_check(case)
    campaign.verify_runtime(case, repo)
    import yaml
    env_config = yaml.safe_load((repo / case["env_config"]).read_text())["env"]["cfg"]
    assert env_config["privileged"] is False and env_config["low_level"]["privileged"] is False
    assert env_config["apis"] == ["FrankaLiberoApiReducedSkillLibraryTraced"]
    model_path = HERE / "reference/model-server.json"
    assert digest(model_path) == MODEL_CONFIG_SHA
    model = json.loads(model_path.read_text())
    assert model["endpoint"] == case["inference_endpoint"]
    assert model["model"] == case["model"] and model["context_tokens"] == case["context_tokens"]
    assert model["env"]["CUDA_VISIBLE_DEVICES"] == "0,1,2,3"
    grasp_checkpoint = GRASPNET_ROOT / "checkpoints/contact_graspnet/checkpoints"
    for path in (GRASPNET_ROOT / "contact_graspnet_pytorch", GRASPNET_ROOT / "Pointnet_Pointnet2_pytorch"):
        assert path.is_dir(), str(path)
    for path in (Path(py), Path(case["claude_bin"]), Path(model["argv"][0]),
                 Path(model["argv"][3]) / "config.json",
                 Path("/mnt/home/gewang/.cache/aspire/sam3/sam3.pt"),
                 grasp_checkpoint / "model.pt", grasp_checkpoint.parent / "config.yaml",
                 parent / "support/run_cell.py", HERE / "qwen-native-compat.py"):
        assert path.is_file(), str(path)
    assert os.getuid() == 10011 and os.getgid() == 10011
    assert os.environ.get("USER") and os.environ.get("LOGNAME")
    assert not any(key in os.environ for key in RENDEZVOUS)

    active_case = dict(case)
    if args.preflight_gpu is not None:
        active_case.update(gpu=args.preflight_gpu, cuda_visible_devices=str(args.preflight_gpu),
                           egl_device_id=args.preflight_gpu)
    env = runtime_env(active_case, repo, clean_env())
    env.update(HF_HUB_OFFLINE="1", SAM3_CHECKPOINT_PATH="/mnt/home/gewang/.cache/aspire/sam3/sam3.pt",
               CONTACT_GRASPNET_ROOT=str(GRASPNET_ROOT), CONTACT_GRASPNET_CHECKPOINT_DIR=str(grasp_checkpoint))
    # No simulator/environment construction, reset, trial, model startup or inference.
    check = ("import os,getpass,json,torch,mujoco,jax; "
             "assert os.access('.',os.R_OK); assert getpass.getuser(); "
             "assert os.environ.get('USER') and os.environ.get('LOGNAME'); "
             "assert not any(k in os.environ for k in ('RANK','WORLD_SIZE','MASTER_ADDR')); "
             "c=mujoco.GLContext(96,96); c.make_current(); c.free(); "
             "print(json.dumps(dict(environment_preflight=True,torch=torch.__version__,"
             "jax_backend=jax.default_backend(),cuda=os.environ['CUDA_VISIBLE_DEVICES'],"
             "egl=os.environ['MUJOCO_EGL_DEVICE_ID'])))")
    subprocess.run([py, "-c", check], cwd=repo, env=env, check=True, timeout=120)
    subprocess.run([py, "cap/serving/launch_pyroki_server.py", "--help"], cwd=repo,
                   env=env, check=True, timeout=120, stdout=subprocess.DEVNULL)
    model_env = clean_env() | model["env"]
    if args.preflight:
        # Import checks use only the selected free DSW GPU; serving remains TP4.
        model_env["CUDA_VISIBLE_DEVICES"] = str(active_case["gpu"])
        subprocess.run([model["argv"][0], "-c",
                        "import torch,vllm,transformers; assert torch.cuda.is_available(); "
                        "print('QWEN_RUNTIME_IMPORT_OK',torch.__version__,vllm.__version__)"],
                       cwd=repo, env=model_env, check=True, timeout=120)
        # The first node attempt exposed a missing vendor-root default in the
        # trimmed frozen tree. Validate the actual service and weights once on
        # the free preflight GPU, at an ephemeral port, with no robot or task.
        probe_dir = Path(tempfile.mkdtemp(prefix="graspnet-startup-", dir=control))
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        log = probe_dir / "service.log"
        with log.open("x") as output:
            process = subprocess.Popen([py, "-u", "cap/serving/launch_contact_graspnet_server.py",
                                        "--host", "127.0.0.1", "--port", str(port)],
                                       cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        service = Service("graspnet-preflight", process, log, f"http://127.0.0.1:{port}/health", allow_404=True)
        try:
            deadline = time.monotonic() + 150
            while True:
                service.check()
                if service.ready():
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"GraspNet startup preflight exceeded 150s: {log}")
                time.sleep(2)
            atomic_json(probe_dir / "result.json", {"passed": True, "vendor_root": str(GRASPNET_ROOT),
                        "checkpoint": str(grasp_checkpoint), "gpu": active_case["gpu"], "port": port,
                        "simulator_trials": 0})
        finally:
            stop_processes([process])
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        print(json.dumps({"environment_preflight": "passed", "case": str(case_path),
                          "preflight_gpu": active_case["gpu"], "frozen_sim_gpu": case["gpu"],
                          "qwen_started": False, "graspnet_startup": "passed",
                          "graspnet_evidence": str(probe_dir), "simulator_trials": 0}), flush=True)
        return 0

    if (control / "campaign_state.json").exists():
        raise RuntimeError("pilot already has a campaign record; inspect/resume explicitly")
    invocation = new_attempt(control / "dlc")
    logs, owned, watch = invocation / "logs", [], None
    state = {"case": str(case_path), "attempt_dir": str(invocation),
             "host": os.uname().nodename, "supervisor_pid": os.getpid(),
             "started_at": time.time(), "model_cwd_override": str(repo),
             "gpu_map": {"model": [0, 1, 2, 3], "sam3": [4], "graspnet": [5],
                         "simulator_and_pyroki_runtime": [6], "spare": [7]},
             "launch_manifest_sha256": digest(HERE / "launch-manifest.json")}

    def status(name, **extra):
        state.update(state=name, updated_at=time.time(), owned_pids=[p.pid for p in owned], **extra)
        atomic_json(invocation / "status.json", state)
        atomic_json(control / "dlc/status.json", state)
        print(json.dumps(state), flush=True)

    def spawn(command, name, run_env):
        with (logs / name).open("x") as log:
            process = subprocess.Popen(command, cwd=repo, env=run_env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        owned.append(process)
        return process

    def interrupted(signum, frame):
        raise RuntimeError(f"supervisor received signal {signum}; preserving attempts")

    handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        status("starting_services")
        cache = isolated_jit_env("qwen-c-pilot")
        atomic_json(invocation / "jit-cache.json", cache)
        process = spawn(model["argv"], "model.log", model_env | cache)
        services = [Service("qwen", process, logs / "model.log", model["endpoint"] + "/health")]
        for name, port, gpu, extra in (("sam3", 8114, 4, ["--device", "cuda"]),
                                      ("contact_graspnet", 8115, 5, []), ("pyroki", 8116, 6, [])):
            service_env = env | {"CUDA_VISIBLE_DEVICES": str(gpu), "MUJOCO_EGL_DEVICE_ID": str(gpu)}
            process = spawn([py, "-u", f"cap/serving/launch_{name}_server.py", "--host",
                             "127.0.0.1", "--port", str(port), *extra], name + ".log", service_env)
            services.append(Service(name, process, logs / (name + ".log"),
                                    f"http://127.0.0.1:{port}/health", allow_404=True))
        watch = ServiceWatch(services, lambda: stop_processes(owned))
        watch.start()
        status("waiting_for_services")
        watch.wait_ready(timeout=1800)
        with urllib.request.urlopen(model["endpoint"] + "/v1/models", timeout=15) as response:
            models = json.load(response)
        atomic_json(invocation / "served-models.json", models)
        assert [m["id"] for m in models["data"]] == [case["model"]]
        assert models["data"][0].get("max_model_len") == case["context_tokens"], models
        status("native_compatibility")
        process = spawn(python_entry(py, HERE / "qwen-native-compat.py", "--case", case_path,
                                     "--output", invocation / "native-compat"), "native-compat.log", env)
        code = process.wait(timeout=1920)
        watch.check()
        assert code == 0, f"native image/Agent/compaction fixture failed: {code}"
        compat = json.loads((invocation / "native-compat/summary.json").read_text())
        assert compat["passed"] and compat["models"] == [case["model"]]
        status("pilot_running", compatibility=compat)
        process = spawn(python_entry(py, parent / "support/run_cell.py", "--case", case_path),
                        "pilot.log", env)
        code = process.wait(timeout=case["campaign_timeout"])
        watch.check()
        result = json.loads((control / "campaign_state.json").read_text())
        assert code == 0 and result["status"] == "pilot_complete", result.get("blocker")
        assert result["heldout"]["performed"] is False
        status("pilot_complete", driver_exit=code, campaign_state=str(control / "campaign_state.json"))
        return 0
    except Exception as exc:
        status("failed", error=f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        if watch:
            watch.close()
        stop_processes(owned)
        for process in owned:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
