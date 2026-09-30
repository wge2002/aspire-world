"""Engineering-only replay of existing development evidence; no simulator/model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

p = argparse.ArgumentParser()
p.add_argument('--repo', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--through-checks', action='store_true')
a = p.parse_args()
root = Path('/mnt/home/gewang/experiments/code-world-qwen-debug-20260922/bowl_C_full/outputs/libero_fix_loop/libero_goal_swap/put_the_bowl_on_the_plate')
inputs = {
    'policy': (root / 'initial_code.py', 'e86201b53ac5a0243e957db213a6356b445da3e3e7707def568daa53b1fe69b2'),
    'world': (root / 'initial_world_program.py', 'b72c6422e56d272230588516128c074de2764c11d9d19a5cacf025034da61bbf'),
    'tape': (root / 'development/initial/seed_51/attempt_2/judgment_world/public_tape.jsonl', 'ce6957854c47d9a4a6335da56abd123f5b73a69c97629c960d93da866944cd96'),
}
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
for path, expected in inputs.values():
    assert sha(path) == expected, f'Fixture identity changed: {path}'
a.output.mkdir(parents=True, exist_ok=False)
source_paths = ['cap/world_model/executable_world.py', 'cap/world_model/simple_world.py',
                'cap/integrations/franka/libero_reduced_skill_library.py',
                'scripts/libero/executable_world_profile.py']
source = {name: sha(a.repo / name) for name in source_paths}
result = dict(purpose='engineering regression only; historical development fixture never enters new solver inputs',
              started_at=time.time(), source_root=str(a.repo), source_sha256=source,
              inputs={name: {'path': str(v[0]), 'sha256': v[1]} for name, v in inputs.items()},
              real_simulator_executions=0, model_calls=0, reports=[])
def save():
    tmp = a.output / 'summary.tmp'
    tmp.write_text(json.dumps(result, indent=2) + '\n')
    tmp.replace(a.output / 'summary.json')
save()
env = {k: os.environ[k] for k in ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
env.update(PYTHONPATH=str(a.repo.parents[1]), CUDA_VISIBLE_DEVICES='')
if a.through_checks:
    sys.path.insert(0, str(a.repo / 'scripts/libero'))
    import executable_world_profile as profile
    assert Path(profile.__file__).resolve().is_relative_to(a.repo.resolve())
    state = SimpleNamespace(task_dir=a.output, data={'trials': [{
        'charged': True, 'executed': True, 'status': 'complete',
        'directory': str(inputs['tape'][0].parents[1])}]})
    case = dict(executable_world_revision='r1', condition='C', profile='judgment',
                c_arm='full', task='put_the_bowl_on_the_plate')
    sources = {k: str(inputs[k][0]) for k in ('policy', 'world')}
    started = time.monotonic()
    checked = profile.checks(case, a.repo, state, sources, env)
    result.update(checks=checked, wall_seconds=time.monotonic() - started)
    assert checked['status'] == 'checked' and not checked['cached']
    replay = next(r for r in checked['reports'] if r['mode'] == 'replay')
    assert replay['status'] == 'complete' and replay['matched_calls'] == 90
    assert checked['establishes_task_success'] is False
    cached = profile.checks(case, a.repo, state, sources, env)
    assert cached['cached'] is True and cached['identity'] == checked['identity']
    assert source == {name: sha(a.repo / name) for name in source_paths}
    assert all(sha(path) == expected for path, expected in inputs.values())
    result.update(finished_at=time.time(), source_and_inputs_unchanged=True, cache_verified=True)
    save()
    print(json.dumps(result, indent=2))
    raise SystemExit(0)
for mode in ('rehearsal', 'replay'):
    cmd = ['/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3',
           str(a.repo / 'scripts/libero/executable_world_profile.py'), '--internal',
           '--code', str(inputs['policy'][0]), '--world-program', str(inputs['world'][0]),
           '--tape', str(inputs['tape'][0]), '--mode', mode, '--arm', 'full',
           '--task-language', 'put the bowl on the plate', '--output', str(a.output / mode)]
    started = time.monotonic()
    with (a.output / (mode + '.log')).open('x') as log:
        try:
            proc = subprocess.run(cmd, cwd=a.repo, env=env, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=600)
            row = dict(mode=mode, exit_code=proc.returncode)
        except subprocess.TimeoutExpired:
            row = dict(mode=mode, infrastructure_error='600-second watchdog expired')
    row['wall_seconds'] = time.monotonic() - started
    report = a.output / mode / 'offline_result.json'
    row['report'] = json.loads(report.read_text()) if report.exists() else None
    result['reports'].append(row)
    save()
assert source == {name: sha(a.repo / name) for name in source_paths}, 'Source changed during check'
for path, expected in inputs.values():
    assert sha(path) == expected, 'Input changed during check'
result.update(finished_at=time.time(), source_and_inputs_unchanged=True)
save()
print(json.dumps(result, indent=2))
