#!/usr/bin/env python3
"""DSW preflight for bowl_selfeval_r2 (put_the_bowl_on_the_plate), seed 51.

Adapted from run-dsw-preflight-bowldrawer-a.py with the following differences:

  1. Cell: bowl_selfeval_r2 (condition C, judgment profile, executable-world revision r1).
  2. Output: this study's PARENT / coordination/dsw-preflight-bowl-selfeval-r2.
  3. Synthetic executable-world bundle: world_program.py (7 required functions,
     no forbidden APIs) and code.py (toolchain probe policy) are written to
     OUT/bundle/ before the worker runs.  replay_trial routes through the
     run_executable_world() path, exercising ExecutableSession, public tape, and
     the judgment_world artifact tree.
  4. Source pins extended to cover executable_world.py and simple_world.py.
  5. Static write-target audit: every write path is enumerated and confirmed
     outside the production C control root and C sim tree before execution.

This run spends one scratch diagnostic retry on seed 51 of bowl_selfeval_r2.  It proves:
  - non-privileged traced API class (get_observation → SAM3 → GraspNet → IK)
  - ExecutableSession lifecycle (world loaded, public tape written, manifest.json)
  - all writes land in OUT (scratch), not in any production C tree

No model endpoint is reached.  No held-out seed is touched.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

REPO = Path('/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim')
PARENT = Path('/mnt/home/gewang/experiments/code-world-gate-ablation-20261005')
OUT = PARENT / 'coordination/dsw-preflight-bowl-selfeval-r2'
CELL = 'bowl_selfeval_r2'
TASK = 'put_the_bowl_on_the_plate'
GATE = 'self_eval'
SEALED = True
BUNDLE_DIR = OUT / 'bundle'
PY = '/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3'

# Production roots that must never be written.
PRODUCTION_C_CONTROL = PARENT / 'bowl_selfeval_r2'
PRODUCTION_C_SIM = Path(
    '/mnt/home/gewang/code/ASPIRE-code-world-gate-ablation-20261005'
    '/cells/bowl_selfeval_r2/aspire/sim')

# Source pins: extended from the A runner to cover the executable-world path.
# All hashes measured 2026-09-27 from the engineering checkout before this
# script ran.
SOURCE_PINS = {'scripts/libero/replay_trial.py': 'ffa79c8b57a9ca69aeef8633ecb24c68c784b7f9025f1aa71305fee9b5681588', 'scripts/libero/native_cc_toolchain_probe.py': 'c0d1dd6b1b807104c1c3898e47907eeb61b0660d62f161fbfae8ea77bd6f62e2', 'env_configs/libero/franka_libero_traced.yaml': '759e6d80a3a3631cdfdd9cf995cbd130b071a2a4363edf9d8fb07a47fc9edcad', 'cap/envs/tasks/base.py': '07165b60966adac79affa867799a66302c4e96429a32135233ec76ef0fb2b46e', 'cap/envs/simulators/libero.py': '690c31e355d0d287262d0c316f73e8391f6e468299a790ef4a84206089d508ac', 'cap/world_model/executable_world.py': '8f8150333ffe13b169863f559542fcfaa8074d905b44861e564b8fcf00694a05', 'cap/world_model/simple_world.py': '7b364f1445cab2ec3475a9568e6d7ef435df2c0c575e2c8fa6c17b32916bf32e', 'cap/world_model/judgment_world.py': 'c5fe598738b85e4069f97b666630dc4f0086691990afd2536eea12c8f2f54309', 'cap/world_model/evidence_state.py': 'eb293e4fb21887288a03c0948490ec65c75b5fd5a5102e8eff79420791ea9841', 'cap/world_model/decision_revision.py': 'cf431e990f4f7c4341dccced85e038da854304ca949195105a33b242223f3bab', 'cap/world_model/development_gate.py': '2b658e31af6260d9d6606214ab6476f89ca8a39a5e0e4ea94a14d1bdd6d7c6ed', 'cap/world_model/vlm_judge.py': '7edf3dd2ece884a7f65a1675da0f477ff3271fdcd4a334ce3b08deee243f9f79'}

# Synthetic world program source.  Must have all 7 REQUIRED functions, no
# forbidden APIs, and not crash when called by the toolchain-probe policy
# (get_observation → SAM3 → GraspNet → IK).  This is not a solver output; its
# only job is to make ExecutableSession load cleanly so the C replay path runs.
_WORLD_SOURCE = 'import numpy as np\nFOUNDATION_REVISION = "r1"\nstate = WorldState()\ndef predict(call): raise Unsupported("diagnostic has no predictor")\ndef observe(event): pass\ndef simulate(call): raise Unsupported("diagnostic has no physical model")\ndef update(obs, last_action=None):\n    for key, value in obs["values"].items():\n        state.set(key, value, evidence_ids=obs["evidence_ids"])\ndef query(name, **kwargs): return state.query(name, **kwargs)\ndef snapshot(): return state.snapshot()\ndef done(): return state.all_of([state.predicate("positive_width", lambda w: np.dot([w], [1]) > 0, ("width",))])\n'

# Synthetic policy source.  Uses the toolchain-probe pattern exactly like
# native_cc_toolchain_probe.py but must also satisfy ExecutableSession's
# world-interaction requirements: the world's observe() callback is invoked
# for every API call automatically by ExecutableSession.invoke, so the policy
# does not need to call observe or update manually.
_POLICY_SOURCE = '''\
"""Synthetic policy for bowl_selfeval_r2 DSW preflight.

Mirrors native_cc_toolchain_probe.py: get_observation -> SAM3 -> GraspNet ->
IK.  ASPIRE_TOOLCHAIN_PROBE_DIR and ASPIRE_TOOLCHAIN_PROBE_OBJECT come from
the runner environment.  The world program's observe/update hooks are invoked
automatically by ExecutableSession.invoke around every API call.
"""
import json
import os
from pathlib import Path

probe_dir = Path(os.environ["ASPIRE_TOOLCHAIN_PROBE_DIR"])
probe_obj = os.environ.get("ASPIRE_TOOLCHAIN_PROBE_OBJECT", "bowl")

checks = []

obs = get_observation()
checks.append({"step": "get_observation", "ok": obs is not None})

# get_observation() returns camera-namespaced keys; use the same path as
# native_cc_toolchain_probe.py: obs["agentview"]["images"]["rgb"]
camera = obs["agentview"]
rgb = camera["images"]["rgb"]
depth = camera["images"]["depth"]
checks.append({"step": "observation_keys",
               "ok": rgb is not None and depth is not None})
import world
world.update({"evidence_ids": [world.evidence_id()], "values": {"width": int(rgb.shape[1])}})
assert world.query("width")["value"] == int(rgb.shape[1])
assert world.snapshot()["layers"]["observed"]["width"]["value"] == int(rgb.shape[1])
assert world.done()["verdict"] == "true"
checks.append({"step": "numpy_bool_predicate", "ok": True})
world.update({"evidence_ids": [world.evidence_id()], "values": {"width": None}})
assert world.query("width")["status"] == "unknown"
assert world.done()["verdict"] == "unknown"
checks.append({"step": "foundation_read_write_unknown", "ok": True})


seg = segment_sam3_text_prompt(rgb, probe_obj)
checks.append({"step": "segment_sam3_text_prompt",
               "ok": seg is not None and hasattr(seg, "__len__")})

grasp = plan_grasp(depth, camera["intrinsics"], seg[0]["mask"] if seg else None)
checks.append({"step": "plan_grasp", "ok": grasp is not None})

if isinstance(grasp, dict):
    pos = grasp.get("position", [0.0, 0.0, 0.5])
    quat = grasp.get("quaternion", [0.0, 0.0, 0.0, 1.0])
elif isinstance(grasp, (list, tuple)) and len(grasp) > 0:
    first = grasp[0] if isinstance(grasp[0], dict) else {}
    pos = first.get("position", [0.0, 0.0, 0.5])
    quat = first.get("quaternion", [0.0, 0.0, 0.0, 1.0])
else:
    pos, quat = [0.0, 0.0, 0.5], [0.0, 0.0, 0.0, 1.0]

joints = solve_ik(pos, quat)
checks.append({"step": "solve_ik", "ok": joints is not None})

passed = all(c["ok"] for c in checks)
result = {"passed": passed, "checks": checks, "via": "executable_world_c"}
(probe_dir / "toolchain.json").write_text(
    __import__("json").dumps(result, indent=2) + "\\n")
'''


def save(name, data):
    p = OUT / name
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(p)


def audit_write_targets():
    """Enumerate every path this runner will write and prove none touches production.

    Must be called before any directory is created or any subprocess is spawned.
    Raises if any target resolves inside the production C control root or C sim tree.
    """
    PROD_ROOTS = (
        PRODUCTION_C_CONTROL.resolve(),
        PRODUCTION_C_SIM.resolve(),
    )

    # Every path the runner may write, in order of creation.
    candidates = [
        OUT,
        BUNDLE_DIR,
        BUNDLE_DIR / 'world_program.py',
        BUNDLE_DIR / 'code.py',
        BUNDLE_DIR / 'executable_world_config.json',
        OUT / 'source-sha256.json',
        OUT / 'summary.json',
        OUT / 'nvidia-egl-vendor.json',
        OUT / 'replay.log',
        OUT / 'resolved-environment.json',
        OUT / 'toolchain.json',
        # toolchain probe also writes toolchain.json at ASPIRE_TOOLCHAIN_PROBE_DIR=OUT
        # replay_trial writes under OUT/results/
        OUT / 'results',
        OUT / 'sealed',
        # ExecutableSession writes judgment_world/ under config_path.parent = BUNDLE_DIR
        BUNDLE_DIR / 'judgment_world',
        BUNDLE_DIR / 'judgment_world' / 'manifest.json',
        BUNDLE_DIR / 'judgment_world' / 'public_tape.jsonl',
        BUNDLE_DIR / 'judgment_world' / 'events.jsonl',
    ]

    print("  [AUDIT] write targets:")
    problems = []
    for target in candidates:
        resolved = target.resolve()
        for prod in PROD_ROOTS:
            try:
                resolved.relative_to(prod)
                problems.append(
                    f"    UNSAFE: {target} -> {resolved} "
                    f"is inside {prod}")
            except ValueError:
                pass  # not under this production root — good
        print(f"    {target}")

    if problems:
        raise SystemExit(
            "STATIC AUDIT FAILED — refusing to run:\n" + "\n".join(problems))

    # Also confirm OUT itself is not already a symlink into production.
    if OUT.exists() or OUT.is_symlink():
        raise SystemExit(f"output exists; preserve and inspect it: {OUT}")
    print("  [AUDIT] all targets outside production roots — safe to run\n")


def worker():
    """Worker subprocess: sets up the C executable-world path and runs one trial."""
    # Import via the package path (REPO.parents[1] is on PYTHONPATH) so the
    # module object that run_executable_world imports via
    #   from aspire.sim.scripts.libero.replay_trial import _run_replay
    # is the SAME object we patch here.  A bare sys.path insert + import
    # produces a separate module object even for the same file.
    import aspire.sim.scripts.libero.replay_trial as replay

    original_instantiate = replay.instantiate

    def checked_instantiate(config):
        assert config['cfg']['privileged'] is False, \
            f"privileged must be False, got {config['cfg']['privileged']}"
        assert config['cfg']['low_level']['privileged'] is False, \
            "low_level privileged must be False"
        env = original_instantiate(config)
        assert env.cfg.privileged is False and env.low_level_env.privileged is False
        apis = {k: type(v).__module__ + '.' + type(v).__name__
                for k, v in env._apis.items()}
        assert list(apis) == ['FrankaLiberoApiReducedSkillLibraryTraced'], \
            f"expected FrankaLiberoApiReducedSkillLibraryTraced, got {list(apis)}"
        save('resolved-environment.json', dict(
            environment=type(env).__name__,
            low_level=type(env.low_level_env).__name__,
            privileged=False,
            constructed_apis=apis,
            prompt=env.cfg.prompt))
        return env

    replay.instantiate = checked_instantiate

    # Build the synthetic bundle that makes replay_trial dispatch through the
    # run_executable_world() path (condition C, profile=judgment, revision=r1).
    world_sha256 = hashlib.sha256(_WORLD_SOURCE.encode()).hexdigest()
    policy_sha256 = hashlib.sha256(_POLICY_SOURCE.encode()).hexdigest()
    (BUNDLE_DIR / 'world_program.py').write_text(_WORLD_SOURCE)
    (BUNDLE_DIR / 'code.py').write_text(_POLICY_SOURCE)

    config = {
        "mode": "opus46-executable-world-c-r1",
        "task_gate": {
            "suite": "libero_goal_swap",
            "task": "put_the_bowl_on_the_plate",
        },
        "world_program": "world_program.py",
        "world_program_sha256": world_sha256,
        "policy_sha256": policy_sha256,
        "c_arm": "full",
        "development_gate": GATE,
    }
    config_path = BUNDLE_DIR / 'executable_world_config.json'
    config_path.write_text(json.dumps(config, indent=2) + '\n')

    # Must call replay.main(), not _run_replay(), so the world_model_config
    # dispatch fires and run_executable_world() is invoked for mode
    # "opus46-executable-world-c-r1".  _run_replay() ignores world_model_config.
    replay.main(replay.ReplayTrialArgs(
        suite='libero_goal_swap',
        task='put_the_bowl_on_the_plate',
        trial=51,
        model='infrastructure-rehearsal-no-inference',
        replay_code=str(BUNDLE_DIR / 'code.py'),
        config='env_configs/libero/franka_libero_traced.yaml',
        output_dir=str(OUT / 'results'),
        world_model_config=str(config_path),
    ))


def main():
    # ── static write-target audit ────────────────────────────────────────────
    # Must run before any directory is created so the run can be stopped if any
    # target would resolve inside a production C tree.
    audit_write_targets()

    # Create scratch root only after the audit passes.
    OUT.mkdir(parents=True, exist_ok=False)
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Verify source pins before anything touches the simulator.
    actual = {n: hashlib.sha256((REPO / n).read_bytes()).hexdigest()
              for n in SOURCE_PINS}
    mismatches = {n: {'expected': SOURCE_PINS[n], 'actual': actual[n]}
                  for n in SOURCE_PINS if actual[n] != SOURCE_PINS[n]}
    if mismatches:
        raise SystemExit(
            f"source pin mismatch — do not run:\n"
            + json.dumps(mismatches, indent=2))
    save('source-sha256.json', actual)

    # 2. GPU 2 must be free (0 MiB used).
    rows = subprocess.check_output(
        ['nvidia-smi', '--query-gpu=index,memory.used',
         '--format=csv,noheader,nounits'], text=True)
    gpu2_used = int(next(
        r for r in rows.splitlines() if r.split(',')[0].strip() == '2'
    ).split(',')[1])
    if gpu2_used != 0:
        raise SystemExit(f"GPU 2 is not free: {gpu2_used} MiB used")

    # 3. Retry accounting (same shape as the A runner).
    # This run is a scratch diagnostic; nothing is imported into the cell ledger.  bowl_selfeval_r2 has no prior
    # import, so every other dev seed has 3 remaining.
    remaining_real_budget = {str(seed): 3
                              for seed in range(51, 66)}
    state = dict(
        state='running',
        started_at=time.time(),
        host=socket.gethostname(),
        gpu=2,
        seed=51,
        spends_retry=True,
        imported=False,
        development_gate=GATE,
        sealed=SEALED,
        cell='bowl_selfeval_r2',
        task='put_the_bowl_on_the_plate',
        purpose='infrastructure-only observation/SAM3/GraspNet/IK — no model inference',
        task_policy_executed=False,
        attempt_number=1,
        attempts_limit=3,
        remaining_real_budget=remaining_real_budget,
        executable_world_revision='r1',
        c_arm='full',
        note=(
            'Seed 51: one scratch diagnostic retry for ' + CELL + ' (condition C, '
            'judgment profile, executable-world r1). '
            'ExecutableSession loaded with a synthetic diagnostic world. '
            'No model endpoint is reached. '
            'The Qwen server runs in DLC only and is absent on DSW by design.'),
    )
    save('summary.json', state)
    save('nvidia-egl-vendor.json',
         {'file_format_version': '1.0.0',
          'ICD': {'library_path': 'libEGL_nvidia.so.0'}})

    env = {k: os.environ[k]
           for k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL',
                     'LANG', 'LC_ALL', 'TMPDIR')
           if k in os.environ}
    env.update(
        PYTHONPATH=str(REPO.parents[1]),
        MUJOCO_GL='egl',
        CUDA_VISIBLE_DEVICES='2',
        MUJOCO_EGL_DEVICE_ID='2',
        TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1',
        __EGL_VENDOR_LIBRARY_FILENAMES=str(OUT / 'nvidia-egl-vendor.json'),
        SAM3_SERVICE_URL='http://127.0.0.1:8114',
        GRASPNET_SERVICE_URL='http://127.0.0.1:8115',
        PYROKI_SERVICE_URL='http://127.0.0.1:8116',
        # ASPIRE_TOOLCHAIN_PROBE_DIR is where the policy writes toolchain.json.
        ASPIRE_TOOLCHAIN_PROBE_DIR=str(OUT),
        ASPIRE_TOOLCHAIN_PROBE_OBJECT='bowl',
    )
    if SEALED:
        # The protocol sets this for every sealed development trial; the
        # preflight proves the replay honours it on a real simulator run.
        env['ASPIRE_SEALED_OUTCOME'] = str(OUT / 'sealed' / 'outcome.json')

    with (OUT / 'replay.log').open('x') as log:
        proc = subprocess.Popen(
            [PY, str(Path(__file__).resolve()), '--worker'],
            cwd=REPO, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        state.update(pid=proc.pid)
        save('summary.json', state)
        try:
            rc = proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            rc = -124

    # Require both toolchain.json (written by the policy) and the
    # judgment_world/manifest.json (written by ExecutableSession.close).
    # ExecutableSession writes judgment_world/ under config_path.parent,
    # which in the worker is BUNDLE_DIR.
    proof = (json.loads((OUT / 'toolchain.json').read_text())
             if (OUT / 'toolchain.json').exists() else {})

    judgment_manifest = BUNDLE_DIR / 'judgment_world' / 'manifest.json'
    world_ok = judgment_manifest.exists()
    gate_ok, gate_evidence = False, {}
    if world_ok:
        manifest = json.loads(judgment_manifest.read_text())
        result = manifest.get("result") or {}
        if SEALED:
            sys.path.insert(0, str(REPO.parents[1]))
            from aspire.sim.cap.world_model.development_gate import public_leaks, read_sealed_outcome
            outcome = read_sealed_outcome(OUT / 'sealed' / 'outcome.json')
            leaks = public_leaks(OUT / 'results') + public_leaks(BUNDLE_DIR)
            gate_ok = (outcome is not None and result.get("outcome") == "sealed"
                       and "task_completed" not in result and "reward" not in result
                       and not leaks and Path(outcome["trial_dir"]).name.endswith("_sealed"))
            gate_evidence = {"sealed_outcome_written": outcome is not None,
                             "sealed_trial_dir": None if outcome is None else outcome["trial_dir"],
                             "public_leaks": leaks, "manifest_result": result}
        else:
            gate_ok = "task_completed" in result
            gate_evidence = {"manifest_result": result}
    passed = (rc == 0
              and proof.get('passed') is True
              and (OUT / 'resolved-environment.json').exists()
              and world_ok and gate_ok)

    state.update(
        state='passed' if passed else 'failed',
        exit_code=rc,
        finished_at=time.time(),
        toolchain=proof,
        judgment_world_manifest=str(judgment_manifest) if world_ok else None,
        gate_check=gate_evidence,
    )
    save('summary.json', state)
    print(json.dumps(state, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    if '--worker' in sys.argv:
        worker()
    else:
        raise SystemExit(main())
