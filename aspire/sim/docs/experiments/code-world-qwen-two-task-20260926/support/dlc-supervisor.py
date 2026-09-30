#!/usr/bin/env python3
"""Start the verified local Qwen and perception services, then one frozen C cell.

Derived from the frozen v4 supervisor
`/mnt/home/gewang/experiments/code-world-qwen-debug-20260922-retry4/launch-20260923-v4/dlc-supervisor.py`
(sha256 `4ec5f48417926131051135aafaa6dd821402d037ee562db62d33dc54ded93386`).
Everything that made v4 the first attempt to reach a running native cell is kept
byte-for-byte in shape: the launch-manifest and prepare-receipt digest gates, the
`local_vllm_pilot_check`/`verify_runtime` case gates, the env-config privilege
and reduced-API assertions, the pinned model-server config, the GraspNet vendor
root and checkpoint directory, the identity/rendezvous assertions, the
`--preflight` path with its separate GPU override and GraspNet startup probe, the
`isolated_jit_env` JIT cache, `ServiceWatch`, `python_entry`'s SIGTERM->SIGINT
wrapper, `start_new_session` spawning and the `stop_processes` unwind.

The deltas, all of them forced by this being the FULL study rather than the
three-seed pilot:

`DRIVER_REL`
    `support/run_cell.py` -> `support/run_full_cell.py`, and the whole frozen
    support set this driver imports is required to exist, not just the driver.

development partition
    v4 asserted `case["dev_seeds"] == [51, 52, 53]`. This study is seeds 51-65
    with the frozen held-out sweep on 1-50, so both are asserted here.

`REQUIRED_RUNTIME`
    The staged overlay must actually carry the repaired runtime sources —
    `replay_trial.py` and `native_world_fixloop_state.py` among them — and their
    digests must match the prepare receipt's frozen runtime manifest. The legacy
    overlay did not include them; assuming it does is how a "staged" run silently
    executes unrepaired code.

`output_ownership.verify`
    Called before the environment preflight and again immediately before the
    first service is spawned, so a runtime whose `sim/outputs` link resolves
    outside this cell's canonical control/outputs root — retry4's failure — costs
    nothing instead of a model cold start, a service fleet and a whole cell.

deadlines
    v4's single 300-minute maximum is not retained. Service readiness, the native
    compatibility fixture, development and held-out each get their own explicit
    wall-clock budget from `full_deadlines`, and the driver is given the
    development and held-out deadlines explicitly on its command line so the
    value the watchdog uses is the value recorded in the launch state.

terminal contract
    v4 required `status == "pilot_complete"` and `heldout["performed"] is False`.
    `accept_cell_result` requires the real thing: the full terminal status, a
    performed held-out sweep, and all 50 manifest rows accounted for. A
    structured `blocked` record is surfaced as the failure reason rather than
    being flattened into an AssertionError.
"""
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
GRASPNET_CHECKPOINT_REL = "checkpoints/contact_graspnet/checkpoints"
RENDEZVOUS = ("RANK", "LOCAL_RANK", "WORLD_SIZE", "LOCAL_WORLD_SIZE", "MASTER_ADDR",
              "MASTER_PORT", "GROUP_RANK", "ROLE_RANK", "TORCHELASTIC_RUN_ID")

#: This study's development partition and its frozen held-out sweep.
DEV_SEEDS = list(range(51, 66))
HELDOUT_SEEDS = list(range(1, 51))
TERMINAL = "full_complete"

#: The driver, relative to the prepared root. The pilot's `run_cell.py` is gone.
DRIVER_REL = "support/run_full_cell.py"

#: Every frozen support file the driver imports. A missing one is a stage error,
#: not an ImportError eight hours into a job.
REQUIRED_SUPPORT = ("run_full_cell.py", "full_scope.py", "full_deadlines.py",
                    "full_import.py", "full_render.py", "output_ownership.py",
                    "cell_read_guard.py", "infra_guard.py", "lineage.py",
                    "native_lineage_r2.py", "heldout_stop_on_infra.py",
                    "native_cc_stream.py", "pilot_assignment_guard.py")

#: Repaired runtime sources that must be in the staged overlay AND in the frozen
#: runtime hash manifest. `replay_trial.py` and `native_world_fixloop_state.py`
#: are listed explicitly because the legacy overlay omitted them.
REQUIRED_RUNTIME = ("scripts/libero/replay_trial.py",
                    "scripts/libero/native_world_fixloop_state.py",
                    "scripts/libero/native_world_campaign.py",
                    "scripts/libero/native_world_protocol.py",
                    "scripts/libero/native_world_heldout.py")


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


def driver_timeout(deadlines) -> int:
    """The driver owns development, freeze and the held-out sweep in one process.

    v4 used `case["campaign_timeout"]`, which in this study is the per-solver-turn
    timeout *inside* the driver; using it here would kill the job during its first
    long turn. The supervisor's own budget must encompass both phases plus
    finalization, and `full_deadlines.TOTAL` adds service startup and the compat
    fixture on top of that for the DLC request.
    """
    return int(deadlines.DEVELOPMENT + deadlines.HELDOUT + deadlines.FINALIZE_MARGIN)


def accept_cell_result(result: dict, code: int) -> dict:
    """The full study's terminal contract. Raises with the structured blocker.

    A cell is complete only when the driver reached `full_complete`, the held-out
    sweep actually ran, and every one of the 50 frozen manifest rows is accounted
    for. `blocked` is reported as itself so the failure names its phase and what
    survives it, instead of collapsing into `assert`.
    """
    blocked = (result or {}).get("blocked")
    if blocked:
        raise RuntimeError(json.dumps({"cell_blocked": blocked}, sort_keys=True))
    if code != 0:
        raise RuntimeError(f"driver exited {code} with status {(result or {}).get('status')!r}")
    if (result or {}).get("status") != TERMINAL:
        raise RuntimeError(f"driver status {(result or {}).get('status')!r} != {TERMINAL!r}")
    heldout = (result or {}).get("heldout") or {}
    if not heldout.get("performed") or not heldout.get("evaluate_invoked"):
        raise RuntimeError(f"held-out evaluation was not performed: {heldout}")
    if heldout.get("timed_out"):
        raise RuntimeError(f"held-out sweep hit its enforced deadline: {heldout}")
    accounting = heldout.get("accounting") or {}
    if not accounting.get("identity_verified"):
        raise RuntimeError(f"held-out report identity was not verified: {accounting}")
    if accounting.get("rows_accounted") != len(HELDOUT_SEEDS):
        raise RuntimeError(
            f"held-out accounting covers {accounting.get('rows_accounted')} rows, "
            f"not {len(HELDOUT_SEEDS)}")
    manifest = (result or {}).get("heldout_manifest") or {}
    if manifest.get("row_count") != len(HELDOUT_SEEDS) or manifest.get("seeds") != HELDOUT_SEEDS:
        raise RuntimeError(f"frozen held-out manifest is not the 1-50 sweep: {manifest}")
    return {"status": TERMINAL, "heldout": accounting,
            "selected_bundle": manifest.get("selected_bundle"),
            "heldout_manifest_sha256": manifest.get("sha256"),
            "development_seconds": (result or {}).get("development_seconds"),
            "total_seconds": (result or {}).get("total_seconds")}


def verify_staged_runtime(repo: Path, receipt: dict) -> dict:
    """Every repaired runtime source is present AND pinned by the receipt."""
    pinned = receipt.get("runtime_sha256")
    if not pinned:
        raise RuntimeError(
            "prepare-receipt.json carries no runtime_sha256 manifest; the staged "
            "overlay is not pinned and may be the legacy overlay without "
            "replay_trial.py / native_world_fixloop_state.py")
    measured, problems = {}, []
    for relative in REQUIRED_RUNTIME:
        path = Path(repo) / relative
        if not path.is_file():
            problems.append(f"missing from the staged overlay: {relative}")
            continue
        measured[relative] = digest(path)
        if relative not in pinned:
            problems.append(f"not in the frozen runtime manifest: {relative}")
        elif pinned[relative] != measured[relative]:
            problems.append(f"{relative}: staged {measured[relative]} != pinned {pinned[relative]}")
    if problems:
        raise RuntimeError(json.dumps({"staged_runtime_failed": problems}, indent=2))
    return measured


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
    for name in REQUIRED_SUPPORT:
        assert name in receipt["frozen_support"], f"support file not frozen: {name}"
        assert (parent / "support" / name).is_file(), name
    # The frozen support set is now verified, so it is safe to import from.
    sys.path.insert(0, str(parent / "support"))
    import full_deadlines
    import output_ownership

    assert sorted(int(s) for s in case["dev_seeds"]) == DEV_SEEDS, case["dev_seeds"]
    assert list(case.get("heldout_seeds", HELDOUT_SEEDS)) == HELDOUT_SEEDS
    assert case["condition"] == "C"
    assert case["c_arm"] == "full" and case["executable_world_revision"] == "r1"
    assert case["model_provider"] == "local-vllm" and case["gpu"] == 6
    assert {case[k] for k in ("model", "model_tag", "expected_served_model")} == {"qwen3.8-flash-next"}
    assert "api_key_helper" not in case
    campaign.local_vllm_pilot_check(case)
    campaign.verify_runtime(case, repo)
    runtime_digests = verify_staged_runtime(repo, receipt)
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
    grasp_checkpoint = GRASPNET_ROOT / GRASPNET_CHECKPOINT_REL
    for path in (GRASPNET_ROOT / "contact_graspnet_pytorch", GRASPNET_ROOT / "Pointnet_Pointnet2_pytorch"):
        assert path.is_dir(), str(path)
    for path in (Path(py), Path(case["claude_bin"]), Path(model["argv"][0]),
                 Path(model["argv"][3]) / "config.json",
                 Path("/mnt/home/gewang/.cache/aspire/sam3/sam3.pt"),
                 grasp_checkpoint / "model.pt", grasp_checkpoint.parent / "config.yaml",
                 parent / DRIVER_REL, HERE / "qwen-native-compat.py"):
        assert path.is_file(), str(path)
    assert os.getuid() == 10011 and os.getgid() == 10011
    assert os.environ.get("USER") and os.environ.get("LOGNAME")
    assert not any(key in os.environ for key in RENDEZVOUS)
    # Before any model, service or simulator process exists.
    ownership = output_ownership.verify(case, repo)

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
                          "graspnet_evidence": str(probe_dir), "simulator_trials": 0,
                          "output_ownership": ownership["canonical_root"],
                          "staged_runtime_files": len(runtime_digests)}), flush=True)
        return 0

    if (control / "campaign_state.json").exists():
        raise RuntimeError("this cell already has a campaign record; inspect/resume explicitly")
    invocation = new_attempt(control / "dlc")
    logs, owned, watch = invocation / "logs", [], None
    state = {"case": str(case_path), "attempt_dir": str(invocation),
             "host": os.uname().nodename, "supervisor_pid": os.getpid(),
             "started_at": time.time(), "model_cwd_override": str(repo),
             "gpu_map": {"model": [0, 1, 2, 3], "sam3": [4], "graspnet": [5],
                         "simulator_and_pyroki_runtime": [6], "spare": [7]},
             "mode": "full_study", "dev_seeds": DEV_SEEDS, "heldout_seeds": HELDOUT_SEEDS,
             "deadlines": {**full_deadlines.summary(),
                           "driver_timeout_seconds": driver_timeout(full_deadlines)},
             "output_ownership": ownership, "staged_runtime_sha256": runtime_digests,
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
        # Re-checked immediately before the first service: staging and launch are
        # separate events, and only this check precedes every spawned process.
        output_ownership.verify(case, repo)
        status("starting_services")
        cache = isolated_jit_env("qwen-c-full")
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
        watch.wait_ready(timeout=full_deadlines.SERVICE_STARTUP)
        with urllib.request.urlopen(model["endpoint"] + "/v1/models", timeout=15) as response:
            models = json.load(response)
        atomic_json(invocation / "served-models.json", models)
        assert [m["id"] for m in models["data"]] == [case["model"]]
        assert models["data"][0].get("max_model_len") == case["context_tokens"], models
        status("native_compatibility")
        process = spawn(python_entry(py, HERE / "qwen-native-compat.py", "--case", case_path,
                                     "--output", invocation / "native-compat"), "native-compat.log", env)
        code = process.wait(timeout=full_deadlines.NATIVE_COMPAT)
        watch.check()
        assert code == 0, f"native image/Agent/compaction fixture failed: {code}"
        compat = json.loads((invocation / "native-compat/summary.json").read_text())
        assert compat["passed"] and compat["models"] == [case["model"]]
        status("cell_running", compatibility=compat)
        process = spawn(python_entry(py, parent / DRIVER_REL, "--case", case_path,
                                     "--deadline-development", full_deadlines.DEVELOPMENT,
                                     "--deadline-heldout", full_deadlines.HELDOUT),
                        "cell.log", env)
        code = process.wait(timeout=driver_timeout(full_deadlines))
        watch.check()
        result = json.loads((control / "campaign_state.json").read_text())
        summary = accept_cell_result(result, code)
        status(TERMINAL, driver_exit=code, cell_summary=summary,
               campaign_state=str(control / "campaign_state.json"))
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
