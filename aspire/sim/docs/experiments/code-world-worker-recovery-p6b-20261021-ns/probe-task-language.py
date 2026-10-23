#!/usr/bin/env python3
"""CC_TASK_11 task choice: runtime task_language of candidate tasks, before staging.

`_task` suites remap the instruction, and the benchmark API's `.language` is the bddl
stem, so the goal is only visible after a reset. For each (suite, task) this runs the
env-check's observation-only path (the worktree's `scripts/libero/runtime/replay_trial.py`
with `scripts/libero/scene_snapshot.py`, seed 51, no services) and records TASK_LANGUAGE.
Output goes to a scratch directory outside every result root; nothing is staged.

  probe-task-language.py --gpu 2 --suite libero_spatial_task [--tasks a,b]
"""
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

SIM = Path(__file__).resolve().parents[3]
OUT = Path('/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-ns-language-probe')
CONFIG = 'env_configs/libero/franka_libero_traced.yaml'
MODEL = 'qwen3.8-flash-next'
STRIP = re.compile(r"API_KEY|AUTH_TOKEN|SECRET|ACCESS_KEY|CREDENTIAL|SESSION_TOKEN|^HF_TOKEN$|HUGGING_FACE_HUB_TOKEN|ANTHROPIC")


def tasks_of(suite):
    code = ("from libero import benchmark; s=benchmark.get_benchmark_dict()[%r]();"
            "print('\\n'.join(s.get_task(i).name for i in range(s.n_tasks)))" % suite)
    out = subprocess.run([str(SIM / '.venv-libero/bin/python3'), '-c', code], capture_output=True, text=True,
                         cwd=SIM, check=True).stdout
    return [line for line in out.splitlines() if line and not line.startswith('[')]


def run_one(suite, task, gpu):
    out = OUT / suite / task
    if out.exists():
        raise SystemExit(f'{out} exists; never overwritten')
    out.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not STRIP.search(k)}
    env.pop('ASPIRE_SAM3_PROMPTS', None)
    env.update(ASPIRE_ROOT=str(SIM), PYTHON_ROOT=str(SIM.parents[1]), PYTHONPATH=str(SIM.parents[1]),
               MUJOCO_GL='egl', CUDA_VISIBLE_DEVICES=str(gpu), MUJOCO_EGL_DEVICE_ID=str(gpu),
               TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD='1', SNAPSHOT_DIR=str(out), ASPIRE_RUNTIME_EVIDENCE_DIR=str(out),
               SAM3_SERVICE_URL='http://127.0.0.1:1', GRASPNET_SERVICE_URL='http://127.0.0.1:1',
               PYROKI_SERVICE_URL='http://127.0.0.1:1',
               __EGL_VENDOR_LIBRARY_FILENAMES=str(OUT / 'nvidia-egl-vendor.json'))
    command = [str(SIM / '.venv-libero/bin/python3'), 'scripts/libero/runtime/replay_trial.py',
               '--args.suite', suite, '--args.task', task, '--args.trial', '51', '--args.model', MODEL,
               '--args.config', CONFIG, '--args.output-dir', str(out / 'results'),
               '--args.replay-code', str(SIM / 'scripts/libero/scene_snapshot.py')]
    started = time.time()
    with open(out / 'replay.log', 'w') as log:
        proc = subprocess.run(command, cwd=SIM, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    text = (out / 'replay.log').read_text(errors='replace')
    language = re.search(r'^TASK_LANGUAGE: (.*)$', text, re.M)
    init = re.search(r'^Loaded init states for task (\d+) in suite (\S+)', text, re.M)
    images = all((out / n).is_file() and (out / n).stat().st_size > 0
                 for n in ('scene_snapshot.jpg', 'scene_snapshot_wrist.jpg'))
    row = {'suite': suite, 'task': task, 'exit_code': proc.returncode, 'init_states_loaded': bool(init),
           'images': images, 'task_language': language.group(1) if language else None,
           'wall_seconds': round(time.time() - started, 1), 'log': str(out / 'replay.log')}
    (out / 'probe.json').write_text(json.dumps(row, indent=2) + '\n')
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gpu', type=int, required=True)
    parser.add_argument('--suite', required=True)
    parser.add_argument('--tasks', default='')
    args = parser.parse_args()
    for task in [t for t in args.tasks.split(',') if t] or tasks_of(args.suite):
        print(json.dumps(run_one(args.suite, task, args.gpu)), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
