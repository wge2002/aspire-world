#!/usr/bin/env python3
"""One recorded, nonprivileged sensor/IK work unit; no model or task solution."""
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
OUT = Path(__file__).resolve().parent / 'coordination/dsw-preflight'
PY = '/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3'


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
        assert config['cfg']['privileged'] is False
        assert config['cfg']['low_level']['privileged'] is False
        env = original(config)
        assert env.cfg.privileged is False and env.low_level_env.privileged is False
        apis = {k: type(v).__module__ + '.' + type(v).__name__ for k, v in env._apis.items()}
        assert list(apis) == ['FrankaLiberoApiReducedSkillLibraryTraced']
        save('resolved-environment.json', dict(environment=type(env).__name__,
             low_level=type(env.low_level_env).__name__, privileged=False,
             constructed_apis=apis, prompt=env.cfg.prompt))
        return env

    replay.instantiate = checked_instantiate
    replay._run_replay(replay.ReplayTrialArgs(
        suite='libero_goal_swap', task='put_the_bowl_on_the_plate', trial=51,
        model='infrastructure-rehearsal-no-inference',
        replay_code='scripts/libero/native_cc_toolchain_probe.py',
        config='env_configs/libero/franka_libero_traced.yaml',
        output_dir=str(OUT / 'results')))


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    rows = subprocess.check_output(['nvidia-smi', '--query-gpu=index,memory.used',
                                   '--format=csv,noheader,nounits'], text=True)
    assert int(next(r for r in rows.splitlines() if r.split(',')[0].strip() == '2').split(',')[1]) == 0
    state = dict(state='running', started_at=time.time(), host=socket.gethostname(),
                 gpu=2, seed=51, charged=True, purpose='infrastructure-only observation/SAM3/GraspNet/IK',
                 task_policy_executed=False, remaining_real_budget={'51': 2, '52': 3, '53': 3})
    save('summary.json', state)
    save('source-sha256.json', {n: hashlib.sha256((REPO / n).read_bytes()).hexdigest() for n in
        ('scripts/libero/replay_trial.py', 'scripts/libero/native_cc_toolchain_probe.py',
         'env_configs/libero/franka_libero_traced.yaml', 'cap/envs/tasks/base.py',
         'cap/envs/simulators/libero.py')})
    save('nvidia-egl-vendor.json', {'file_format_version': '1.0.0', 'ICD': {'library_path': 'libEGL_nvidia.so.0'}})
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
    env.update(PYTHONPATH=str(REPO.parents[1]), MUJOCO_GL='egl', CUDA_VISIBLE_DEVICES='2',
               MUJOCO_EGL_DEVICE_ID='2', TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1',
               __EGL_VENDOR_LIBRARY_FILENAMES=str(OUT / 'nvidia-egl-vendor.json'),
               SAM3_SERVICE_URL='http://127.0.0.1:8114', GRASPNET_SERVICE_URL='http://127.0.0.1:8115',
               PYROKI_SERVICE_URL='http://127.0.0.1:8116', ASPIRE_TOOLCHAIN_PROBE_DIR=str(OUT),
               ASPIRE_TOOLCHAIN_PROBE_OBJECT='bowl')
    with (OUT / 'replay.log').open('x') as log:
        proc = subprocess.Popen([PY, str(Path(__file__).resolve()), '--worker'], cwd=REPO,
                                env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        state.update(pid=proc.pid); save('summary.json', state)
        try:
            rc = proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL); proc.wait()
            rc = -124
    proof = json.loads((OUT / 'toolchain.json').read_text()) if (OUT / 'toolchain.json').exists() else {}
    passed = rc == 0 and proof.get('passed') is True and (OUT / 'resolved-environment.json').exists()
    state.update(state='passed' if passed else 'failed', exit_code=rc, finished_at=time.time(), toolchain=proof)
    save('summary.json', state)
    print(json.dumps(state), flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    if '--worker' in sys.argv:
        worker()
    else:
        raise SystemExit(main())
