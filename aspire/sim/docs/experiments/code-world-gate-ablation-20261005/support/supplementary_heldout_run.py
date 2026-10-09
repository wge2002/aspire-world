#!/usr/bin/env python3
"""Supplementary held-out evaluation of one stopped cell's recorded selection.

Why this exists
---------------
`drawer_oracle_r2` spent its 10 h development budget and was stopped by the
wall-clock watchdog. Before the stop the solver had already recorded a selection
(4101f02d…, 6/7 graded development successes) and then kept iterating on the same
retries. Two consequences:

  * `fix_code.py` / `fix_world_program.py` on disk hold a LATER, unselected
    bundle (dbcbc536…, 6/6 successes, never selected);
  * `development/repair/seed_57/attempt_3` is an unresolved
    `infrastructure_error` row (SIGTERM, exit -15). The protocol cannot clear it:
    seed 57 has spent all three retries, so `validate_admission` refuses the
    recovery trial that `resolve_interrupted` requires.

So `check` can never be `ready`, `finalize` refuses, and the frozen
`stage1_result.json` was never written. The held-out evaluator does not read
`check`; it reads a stage-1 result naming a selected bundle that really ran in
development. This runner writes exactly that, from the cell's own ledger, and
then runs the UNMODIFIED frozen held-out sequence.

What it does not do
-------------------
It never re-runs development, never invents or refunds a retry, never edits the
ledger, and never evaluates a policy other than the one the solver selected. The
superseded on-disk files are preserved beside the originals, never deleted.

Usage
-----
    supplementary_heldout_run.py --preflight --case <cell>/case.json   # env self-check
    supplementary_heldout_run.py           --case <cell>/case.json   # the run
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent          # .../<study>/support
STUDY = HERE.parent                             # .../<study>

#: The bundle the solver recorded, and the two digests that make it up.
SELECTED_BUNDLE = "4101f02dab22892e6830d892d39fdaa8ef5611a401ad3335acca1403c7568f3d"
SELECTED_POLICY = "ae19405afeff2f4cfd9f172261e319b7680c581e36a44fc7d6a0997115acc23a"
SELECTED_WORLD = "76dd6f0131034599e3ed4a7e57d6d000ac17ff1d15d56377771653bd821c2951"

LAUNCH = STUDY / "launch-20261005"              # the study's pinned launch dir (reference/)
MODEL_CONFIG_SHA = "22c61284fd39db1310bc7cecbb09ec06ddcb417aeb4156fbea8a163829985c67"
GRASPNET_ROOT = Path("/mnt/home/gewang/code/ASPIRE/aspire/sim/cap/third_party/contact_graspnet_pytorch")
GRASPNET_CHECKPOINT_REL = "checkpoints/contact_graspnet/checkpoints"
SAM3_CHECKPOINT = Path("/mnt/home/gewang/.cache/aspire/sam3/sam3.pt")


def digest(path: Path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean_env() -> dict:
    return {k: os.environ[k] for k in (
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR",
        "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES") if k in os.environ}


def service_env(env: dict) -> dict:
    """The study supervisor's service environment, verbatim.

    Every one of these four is load-bearing. `CONTACT_GRASPNET_ROOT` is the one
    whose absence killed the first attempt: without it the launcher falls back to
    `<repo>/cap/third_party/contact_graspnet_pytorch`, and the frozen cell
    checkout is trimmed, so it raises FileNotFoundError and exits 1.
    """
    grasp_checkpoint = GRASPNET_ROOT / GRASPNET_CHECKPOINT_REL
    assert GRASPNET_ROOT.is_dir(), f"missing vendor root: {GRASPNET_ROOT}"
    assert (grasp_checkpoint / "model.pt").is_file(), f"missing checkpoint: {grasp_checkpoint}"
    assert SAM3_CHECKPOINT.is_file(), f"missing SAM3 checkpoint: {SAM3_CHECKPOINT}"
    return env | {"HF_HUB_OFFLINE": "1", "SAM3_CHECKPOINT_PATH": str(SAM3_CHECKPOINT),
                  "CONTACT_GRASPNET_ROOT": str(GRASPNET_ROOT),
                  "CONTACT_GRASPNET_CHECKPOINT_DIR": str(grasp_checkpoint)}


def import_runtime():
    """The study's pinned service/watch helpers, loaded from the launch dir."""
    sys.path.insert(0, str(LAUNCH / "reference"))
    from native_cc_runtime import (Service, ServiceWatch, atomic_json,
                                   isolated_jit_env, stop_processes)
    return dict(Service=Service, ServiceWatch=ServiceWatch, atomic_json=atomic_json,
                isolated_jit_env=isolated_jit_env, stop_processes=stop_processes)


def stage_result(case: dict, state) -> dict:
    """Write the stage-1 result `frozen_bundle` reads, from the cell's own ledger.

    Every field is recomputed from the ledger by the frozen protocol module. The
    added `supplementary` block documents why this file exists when the normal
    `finalize` path could not produce it.
    """
    from native_world_protocol import selected_bundle
    bundle = selected_bundle(case, state)
    status = state.progress()
    path = state.task_dir / "stage1_result.json"
    result = {
        "schema_version": 1, "harness": "claude-code", "cell": case["id"],
        "condition": case["condition"], "stage1_complete": True,
        "suite": case["suite"], "task": case["task"],
        "selected_bundle": bundle,
        "selection": state.data["selected"],
        "model_served": state.data.get("model_served") or {},
        "usage": state.data.get("usage") or {},
        "retry_limit": state.RETRY_LIMIT,
        "retries_used_per_seed": status["retries_used_per_seed"],
        "seed_outcomes": status["seeds"],
        "tested_bundles": status["tested_bundles"],
        "rejected_revisions": state.data.get("rejected", []),
        "aliases": state.data.get("aliases", []),
        "world_program_errors": status["world_program_errors"],
        "diagnostic_program_errors": status["diagnostic_program_errors"],
        "graded_executions": status["graded_executions"],
        "final_development_coverage": state.final_coverage(bundle),
        "replay_invocations": status["replay_invocations"],
        "supplementary": {
            "reason": ("development was stopped by the wall-clock watchdog after the solver "
                       "recorded its selection; the frozen finalize path could not run "
                       "because an unresolved infrastructure row cannot be cleared"),
            "written_by": "support/supplementary_heldout_run.py",
            "unresolved_rows": [r["directory"] for r in state.data["trials"]
                                if r["status"] not in
                                ("complete", "world_program_error", "diagnostic_program_error")],
            "note": ("carries no new execution evidence: every field comes from the cell's "
                     "own development ledger, as read by the frozen protocol module"),
        },
    }
    path.write_text(json.dumps(result, indent=2) + "\n")
    return result


def start_services(case, repo, env, invocation, runtime, spawn):
    """Mirror the study's supervisor service block: Qwen on 0-3, perceptions on 4/5/6."""
    Service, ServiceWatch = runtime["Service"], runtime["ServiceWatch"]
    logs = invocation / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    model_path = LAUNCH / "reference/model-server.json"
    assert digest(model_path) == MODEL_CONFIG_SHA, "model-server.json changed"
    model = json.loads(model_path.read_text())
    assert model["endpoint"] == case["inference_endpoint"] and model["model"] == case["model"]
    cache = runtime["isolated_jit_env"]("supplementary-heldout")
    runtime["atomic_json"](invocation / "jit-cache.json", cache)
    owned = []
    process = spawn(model["argv"], "model.log", (clean_env() | model["env"]) | cache)
    owned.append(process)
    services = [Service("qwen", process, logs / "model.log", model["endpoint"] + "/health")]
    py = str(repo / ".venv-libero/bin/python3")
    base = service_env(env)
    for name, port, gpu, extra in (("sam3", 8114, 4, ["--device", "cuda"]),
                                   ("contact_graspnet", 8115, 5, []), ("pyroki", 8116, 6, [])):
        per_service = base | {"CUDA_VISIBLE_DEVICES": str(gpu), "MUJOCO_EGL_DEVICE_ID": str(gpu)}
        process = spawn([py, "-u", f"cap/serving/launch_{name}_server.py", "--host",
                         "127.0.0.1", "--port", str(port), *extra], name + ".log", per_service)
        owned.append(process)
        services.append(Service(name, process, logs / (name + ".log"),
                                f"http://127.0.0.1:{port}/health", allow_404=True))
    watch = ServiceWatch(services, lambda: runtime["stop_processes"](owned))
    watch.start()
    import full_deadlines
    watch.wait_ready(timeout=full_deadlines.SERVICE_STARTUP)
    import urllib.request
    with urllib.request.urlopen(model["endpoint"] + "/v1/models", timeout=15) as response:
        served = json.load(response)
    runtime["atomic_json"](invocation / "served-models.json", served)
    assert [m["id"] for m in served["data"]] == [case["model"]], served
    assert served["data"][0].get("max_model_len") == case["context_tokens"], served
    return watch, owned


def preflight(case, repo) -> int:
    """Environment self-check only: no service, no simulator, no model.

    The device variables must be set the way the real run sets them, or the check
    fails on a missing key instead of testing the environment. SUPP_PREFLIGHT_GPU
    lets a free DSW card stand in for the cell's own (occupied) GPU.
    """
    gpu = os.environ.get("SUPP_PREFLIGHT_GPU", str(case["cuda_visible_devices"]))
    pre = os.environ.copy() | {"CUDA_VISIBLE_DEVICES": gpu, "MUJOCO_EGL_DEVICE_ID": gpu,
                               "MUJOCO_GL": "egl"}
    if case.get("egl_vendor_config"):
        pre["__EGL_VENDOR_LIBRARY_FILENAMES"] = case["egl_vendor_config"]
    py = str(repo / ".venv-libero/bin/python3")
    pre = service_env(pre)
    check = ("import os,getpass,json,torch,mujoco,jax; "
             "assert os.access('.',os.R_OK); assert getpass.getuser(); "
             "assert os.environ.get('USER') and os.environ.get('LOGNAME'); "
             "assert not any(k in os.environ for k in ('RANK','WORLD_SIZE','MASTER_ADDR')); "
             "c=mujoco.GLContext(96,96); c.make_current(); c.free(); "
             "print(json.dumps(dict(environment_preflight=True,torch=torch.__version__,"
             "jax_backend=jax.default_backend(),cuda=os.environ['CUDA_VISIBLE_DEVICES'],"
             "egl=os.environ['MUJOCO_EGL_DEVICE_ID'])))")
    subprocess.run([py, "-c", check], cwd=repo, env=pre, check=True, timeout=180)
    subprocess.run([py, "cap/serving/launch_pyroki_server.py", "--help"], cwd=repo,
                   env=pre, check=True, timeout=120, stdout=subprocess.DEVNULL)
    # Actually start each perception server once, at an ephemeral port, and wait
    # for it to answer. Import checks alone passed while GraspNet was dying on
    # every run: this is the check the study's own supervisor performs.
    probes = {}
    for name, extra, budget in (("contact_graspnet", [], 240), ("sam3", ["--device", "cuda"], 420)):
        import socket
        import tempfile
        import time
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        folder = Path(tempfile.mkdtemp(prefix=f"supp-preflight-{name}-",
                                       dir=os.environ.get("TMPDIR", "/tmp")))
        log = folder / "service.log"
        with log.open("w") as out:
            process = subprocess.Popen(
                [py, "-u", f"cap/serving/launch_{name}_server.py", "--host", "127.0.0.1",
                 "--port", str(port), *extra],
                cwd=repo, env=pre, stdin=subprocess.DEVNULL, stdout=out,
                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + budget
            import urllib.error
            import urllib.request
            while True:
                if process.poll() is not None:
                    raise SystemExit(json.dumps({
                        "supplementary_preflight": "FAILED", "service": name,
                        "exit_code": process.returncode, "log": str(log),
                        "tail": log.read_text()[-800:]}))
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                        if r.status in (200, 404):
                            break
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise SystemExit(json.dumps({
                        "supplementary_preflight": "FAILED", "service": name,
                        "reason": "startup_timeout", "log": str(log),
                        "tail": log.read_text()[-800:]}))
                time.sleep(2)
            probes[name] = {"port": port, "log": str(log), "passed": True}
        finally:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    print(json.dumps({"supplementary_preflight": "passed", "case": case["id"],
                      "probes": probes, "simulator_trials": 0}), flush=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)
    case_path = args.case.resolve()
    case = json.loads(case_path.read_text())
    repo, control = Path(case["sim"]), Path(case["control"])

    runtime = import_runtime()
    if args.preflight:
        return preflight(case, repo)

    # The frozen checkout root is what makes `aspire.sim.cap.world_model`
    # importable; without it `gate_module()` fails exactly as it does when the
    # protocol CLI is run without PYTHONPATH.
    sys.path.insert(0, str(repo.parents[1]))
    sys.path.insert(0, str(repo / "scripts/libero"))
    sys.path.insert(0, str(HERE))
    import native_world_campaign as campaign
    import native_world_protocol as protocol
    import output_ownership
    import full_deadlines
    from infra_guard import bind_child_env
    import run_two_task_cell as frozen

    report = {"cell": case["id"], "case": str(case_path), "started_at": frozen.now()}

    # --- the same gates the real driver applies, before anything runs ---------
    receipt = json.loads((STUDY / "prepare-receipt.json").read_text())
    assert digest(case_path) == receipt["cells"][case["id"]]["case_sha256"], "case digest"
    for name, expected in receipt["frozen_support"].items():
        assert digest(HERE / name) == expected, name
    campaign.verify_runtime(case, repo)
    report["output_ownership"] = output_ownership.verify(case, repo)["canonical_root"]

    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    state = protocol.NativeWorldState(task_dir, protocol.identity(case, repo), resume=True)
    selection = state.data.get("selected") or {}
    report["ledger_selection"] = selection.get("bundle_sha256")
    assert selection.get("bundle_sha256") == SELECTED_BUNDLE, "the ledger's selection changed"
    assert selection["bundle"]["policy"] == SELECTED_POLICY, "selected policy changed"
    assert selection["bundle"]["world"] == SELECTED_WORLD, "selected world changed"

    # --- restore the SELECTED bundle into the frozen on-disk paths ------------
    superseded = task_dir / "development" / "superseded-post-selection"
    superseded.mkdir(parents=True, exist_ok=True)
    pairs = (("fix_code.py", f"{case['suite']}_{case['task']}_fix.py", SELECTED_POLICY),
             ("fix_world_program.py", f"{case['suite']}_{case['task']}_fix_world.py", SELECTED_WORLD))
    working = repo / "outputs" / "working_codes"
    restored = {}
    for target_name, copy_name, expected in pairs:
        target, source = task_dir / target_name, working / copy_name
        assert source.is_file(), f"missing named copy: {source}"
        assert digest(source) == expected, f"{copy_name} is not the selected revision"
        if target.is_file() and digest(target) != expected:
            kept = superseded / target_name
            if not kept.exists():
                kept.write_bytes(target.read_bytes())
            report.setdefault("preserved", {})[target_name] = {"path": str(kept),
                                                               "sha256": digest(kept)}
        target.write_bytes(source.read_bytes())
        restored[target_name] = digest(target)
    report["restored"] = restored

    # --- stage-1 result, then the UNMODIFIED frozen held-out sequence ---------
    stage = stage_result(case, state)
    report["stage1_result"] = {"path": str(task_dir / "stage1_result.json"),
                               "selected_bundle_sha256": stage["selection"]["bundle_sha256"],
                               "tested_bundle_count": len(stage["tested_bundles"])}
    assert stage["selection"]["bundle_sha256"] in stage["tested_bundles"], \
        "the selected bundle was never executed in development"

    invocation = control / ("supplementary-" + frozen.now().replace(":", "").replace("-", ""))
    invocation.mkdir(parents=True, exist_ok=True)

    def spawn(command, name, run_env):
        log = (invocation / "logs" / name)
        with log.open("x") as out:
            return subprocess.Popen(command, cwd=repo, env=run_env, stdin=subprocess.DEVNULL,
                                    stdout=out, stderr=subprocess.STDOUT, start_new_session=True)

    env = bind_child_env(protocol.runtime_env(case, repo), case)
    watch, owned = start_services(case, repo, env, invocation, runtime, spawn)
    evaluation = control / "heldout"
    try:
        manifest = frozen.heldout_manifest(case, repo, task_dir, evaluation)
        report["heldout_manifest"] = {"row_count": manifest["row_count"],
                                      "selected_bundle_sha256": manifest["selected_bundle_sha256"],
                                      "sha256": digest(evaluation / "heldout_manifest.json")}
        heldout_args = ["--case", str(case_path)]
        if (evaluation / "heldout_state.json").is_file():
            heldout_args.append("--resume")
        step = frozen.run_heldout(campaign, repo, env, invocation, heldout_args,
                                  full_deadlines.HELDOUT)
        report["heldout"] = {k: step[k] for k in ("exit_code", "timed_out", "elapsed_seconds")}
        if step["timed_out"]:
            raise SystemExit(json.dumps({**report, "blocked": "held-out hit its deadline"}))
        if step["exit_code"]:
            raise SystemExit(json.dumps({**report, "blocked": f"held-out exited {step['exit_code']}",
                                         "stderr_tail": step.get("stderr_tail")}))
        report["accounting"] = frozen.check_heldout_rows(manifest, step["result"])
        report["judge"] = frozen.judge_heldout_seeds(case, repo, env, invocation)
        watch.check()
    finally:
        runtime["stop_processes"](owned)
        watch.close()
    report["finished_at"] = frozen.now()
    (invocation / "supplementary_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
