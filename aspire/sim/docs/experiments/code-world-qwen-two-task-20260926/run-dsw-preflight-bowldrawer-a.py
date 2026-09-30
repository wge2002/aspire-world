#!/usr/bin/env python3
"""DSW preflight for bowldrawer_A (open_the_top_drawer_and_put_the_bowl_inside), seed 51.

Adapted from code-world-qwen-full-20260924/run-dsw-preflight.py with three
changes and no others:
  1. Task: open_the_top_drawer_and_put_the_bowl_inside (not put_the_bowl_on_the_plate).
  2. Output: this study's PARENT / coordination/dsw-preflight-bowldrawer-a.
  3. Charged-attempt accounting: seed 51 consumes 1 of 3 attempts, so
     remaining_real_budget[51] = 2; all other dev seeds remain at 3.

Object prompt stays "bowl": the scene for this task contains the bowl that is
placed inside the drawer, so the SAM3 segmentation call is still meaningful.

This run charges one attempt against seed 51 of bowldrawer_A. It proves the
nonprivileged traced API class, live observation, SAM3, GraspNet and IK on
GPU 2. It does NOT route through the Qwen model endpoint (8121), which is
DLC-only and absent on DSW by design. No held-out seed is touched.
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
PARENT = Path('/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926')
OUT = PARENT / 'coordination/dsw-preflight-bowldrawer-a'
PY = '/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3'

# Hashes pinned 2026-09-27 before this script ran.
SOURCE_PINS = {
    'scripts/libero/replay_trial.py':
        '65774706ce2d5d40cb03963eef48e14eed9349a1291982cdf33f4999c2f63f4d',
    'scripts/libero/native_cc_toolchain_probe.py':
        'c0d1dd6b1b807104c1c3898e47907eeb61b0660d62f161fbfae8ea77bd6f62e2',
    'env_configs/libero/franka_libero_traced.yaml':
        '759e6d80a3a3631cdfdd9cf995cbd130b071a2a4363edf9d8fb07a47fc9edcad',
    'cap/envs/tasks/base.py':
        'a27f9378d0d954bcee3d86a14065cc54b8136ee328bf4c2bd9dd105c98a6aee5',
    'cap/envs/simulators/libero.py':
        '690c31e355d0d287262d0c316f73e8391f6e468299a790ef4a84206089d508ac',
}


def save(name, data):
    p = OUT / name
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n')
    tmp.replace(p)


def worker():
    sys.path.insert(0, str(REPO / 'scripts/libero'))
    import replay_trial as replay
    original = replay.instantiate

    def checked_instantiate(config):
        assert config['cfg']['privileged'] is False, \
            f"privileged must be False, got {config['cfg']['privileged']}"
        assert config['cfg']['low_level']['privileged'] is False, \
            f"low_level privileged must be False"
        env = original(config)
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
    replay._run_replay(replay.ReplayTrialArgs(
        suite='libero_goal_swap',
        task='open_the_top_drawer_and_put_the_bowl_inside',
        trial=51,
        model='infrastructure-rehearsal-no-inference',
        replay_code='scripts/libero/native_cc_toolchain_probe.py',
        config='env_configs/libero/franka_libero_traced.yaml',
        output_dir=str(OUT / 'results')))


def main():
    OUT.mkdir(parents=True, exist_ok=False)

    # 1. Verify source pins before anything touches the simulator.
    actual = {n: hashlib.sha256((REPO / n).read_bytes()).hexdigest()
              for n in SOURCE_PINS}
    mismatches = {n: {'expected': SOURCE_PINS[n], 'actual': actual[n]}
                  for n in SOURCE_PINS if actual[n] != SOURCE_PINS[n]}
    if mismatches:
        raise SystemExit(f"source pin mismatch — do not run: {json.dumps(mismatches, indent=2)}")
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

    # 3. Exact charged-attempt accounting.
    # This run consumes attempt 1 of 3 for seed 51. bowldrawer_A imports no
    # prior diagnostic, so every other dev seed still has 3 remaining.
    remaining_real_budget = {str(seed): 2 if seed == 51 else 3
                              for seed in range(51, 66)}
    state = dict(
        state='running',
        started_at=time.time(),
        host=socket.gethostname(),
        gpu=2,
        seed=51,
        charged=True,
        cell='bowldrawer_A',
        task='open_the_top_drawer_and_put_the_bowl_inside',
        purpose='infrastructure-only observation/SAM3/GraspNet/IK — no model inference',
        task_policy_executed=False,
        attempt_number=1,
        attempts_limit=3,
        remaining_real_budget=remaining_real_budget,
        note=('Seed 51 attempt 1/3 charged against bowldrawer_A. '
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
        ASPIRE_TOOLCHAIN_PROBE_DIR=str(OUT),
        ASPIRE_TOOLCHAIN_PROBE_OBJECT='bowl',
    )

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

    proof = (json.loads((OUT / 'toolchain.json').read_text())
             if (OUT / 'toolchain.json').exists() else {})
    passed = (rc == 0
              and proof.get('passed') is True
              and (OUT / 'resolved-environment.json').exists())
    state.update(state='passed' if passed else 'failed',
                 exit_code=rc, finished_at=time.time(), toolchain=proof)
    save('summary.json', state)
    print(json.dumps(state, indent=2), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    if '--worker' in sys.argv:
        worker()
    else:
        raise SystemExit(main())
