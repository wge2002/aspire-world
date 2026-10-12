"""RS-only engineering smoke. Not a benchmark or a native development repeat."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

BASE = Path('/mnt/workspace/aspire-world-validation-20261012-articulation-v3')
ROOT = BASE/'source'
SIM = ROOT/'aspire/sim'
sys.path[:0] = [str(ROOT), str(SIM/'scripts/libero'), str(SIM/'scripts/common')]
from native_world_fixloop_state import NativeWorldState, code_hash
from native_cc_trial_process import run_replay
import streaming_fixloop_protocol as sp

TASK = 'open_the_middle_drawer_of_the_cabinet'
IDENTITY = {'kind': 'engineering_short_integration_not_benchmark',
    'profile': sp.ARTICULATION_PROFILE, 'method': 'sam2-articulation-qwen-v3',
    'model': 'qwen3.8-flash-next', 'dev_seeds': list(range(51,66)),
    'development_gate': 'oracle', 'trial_timeout': 900,
    'development_watchdog_seconds': 50400, 'evaluation_watchdog_seconds': 18000,
    'task': TASK, 'heldout_run': False, 'native_policy_generation': False}
CELL = BASE/'engineering-cell'
D = Path('/mnt/workspace/aspire-world-deployment-20261010')
CASE = {'cuda_visible_devices': '0', 'egl_device_id': 0,
    'sam3_url': 'http://127.0.0.1:8114', 'graspnet_url': 'http://127.0.0.1:8115',
    'pyroki_url': 'http://127.0.0.1:8116', 'max_steps': 4000,
    'egl_vendor_config': str(D/'egl/10_nvidia.json'),
    'streaming': {'sam2': {'source_root': '/mnt/workspace/aspire-world-assets/SAM2_streaming'}},
    'initial_state_manifest': '/mnt/workspace/aspire-world-assets/common-initial-states-v3/initial_state_manifest.json',
    'initial_state_manifest_sha256': '1668dd6b6bebeb0261ed507ab15f6326ac73d7a586180ca2b30a45c8b5a106e4'}


def state():
    ledger = NativeWorldState(CELL, IDENTITY, resume=CELL.exists())
    if 'phase_clock' not in ledger.data:
        now = time.time()
        ledger.data['phase_clock'] = {'development': {'started_at': now, 'deadline_at': now+50400}}
        ledger.save()
    return ledger


def execute(ledger, phase, policy, config=None):
    if time.time() >= ledger.data['phase_clock']['development']['deadline_at']:
        raise RuntimeError('Engineering development deadline passed; budget preserved')
    bundle = {'policy': code_hash(policy.read_text())}
    if config:
        bundle['world'] = code_hash(config.read_text())
    row = ledger.begin_trial(phase, 51, bundle, {'policy': str(policy), 'world': str(config) if config else None})
    directory = CELL/row['directory']
    results = directory/'results'
    command = ['/mnt/workspace/aspire-world-envs/libero/bin/python','-B','scripts/libero/replay_trial.py',
        '--args.suite','libero_goal_swap','--args.task',TASK,'--args.trial','51',
        '--args.model','qwen3.8-flash-next','--args.config','env_configs/libero/franka_libero_traced.yaml',
        '--args.output-dir',str(results),'--args.replay-code',str(policy)]
    if config:
        command += ['--args.streaming-world-config', str(config)]
    env = sp.trial_env(CASE, SIM, CELL)
    env.update(LIBERO_CONFIG_PATH=str(D/'public-libero-config'),
        LD_LIBRARY_PATH='/mnt/workspace/aspire-world-envs/cuda-compat13/usr/local/cuda-13.0/compat',
        PYTHONDONTWRITEBYTECODE='1', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
    row['started_at'] = time.time()
    ledger.save()
    rc, error = run_replay(command, repo=SIM, env=env, directory=directory, timeout=900)
    result = sp.parse_result(results, 51)
    ledger.finish_trial(row, result=result, exit_code=rc, error=error)
    row['finished_at'] = time.time()
    row['duration_seconds'] = row['finished_at']-row['started_at']
    row['purpose'] = 'public snapshot only' if phase == 'snapshot' else 'interface integration; task success is not an acceptance criterion'
    ledger.save()
    print(json.dumps({'phase':phase,'row':row}), flush=True)
    return row


if sys.argv[1] == 'snapshot':
    ledger = state()
    execute(ledger, 'snapshot', SIM/'scripts/libero/scene_snapshot.py')
elif sys.argv[1] == 'author':
    from PIL import Image
    import numpy as np
    from aspire.sim.cap.world_model.qwen_world_review import QwenWorldReviewer
    image_path = CELL/'scene_snapshot.jpg'
    image_id = 'public_initial_rgb:'+hashlib.sha256(image_path.read_bytes()).hexdigest()
    packet = {'episode_id': 'engineering/seed51', 'evidence_ids': [image_id],
        'events':[{'reason':'initial_scene_decomposition', 'instruction':
            'Propose a minimal initial parts graph using action revise from this current public image. '
            'Use clearly visible regions and precise public SAM3 text prompts. '
            'Include parent-child motion candidates only when visually meaningful. '
            'Use unknown families if motion is not observed. No robot task solution is requested.'}],
        'snapshot': {'semantic': {'parts':{}}, 'observed': {}, 'relations': {}}}
    reviewer = QwenWorldReviewer({'endpoint':'http://127.0.0.1:8127','model':'qwen3.8-flash-next',
        'effort':'xhigh','context_tokens':1000000,'max_output_tokens':64000,'timeout_seconds':900})
    reply, receipt = reviewer.review(packet, rgb=np.asarray(Image.open(image_path).convert('RGB')))
    (BASE/'public-scene-author-receipt.json').write_text(json.dumps(receipt,indent=2))
    if reply['action'] != 'revise':
        raise RuntimeError('No initial scene decomposition proposed; no program trial started')
    config = {'mode':sp.ARTICULATION_MODE, 'objects':reply['objects'], 'parts':reply['parts'],
        'joints':reply['joints'], 'review':reviewer.config, 'camera':'agentview', 'sam2': {
            'source_root':'/mnt/workspace/aspire-world-assets/SAM2_streaming',
            'config_file':'sam2.1/sam2.1_hiera_s.yaml',
            'checkpoint':'/mnt/workspace/aspire-world-models/sam2.1_hiera_small.pt','device':'cuda'}}
    (BASE/'smoke-world-config.json').write_text(json.dumps(config,indent=2))
    print(json.dumps({'config':str(BASE/'smoke-world-config.json'), 'public_parts':len(reply['parts']),
        'tracked_parts':len(reply['objects']), 'joints':len(reply['joints']), 'usage':receipt['usage']}),flush=True)
elif sys.argv[1] == 'run':
    policy = BASE/'short-policy.py'
    policy.write_text('''initial = world_snapshot()
print("Initial structure revision", initial["semantic"]["structure_revision"])
print("Initial online review count", len(initial["articulation"]["reviews"]))
request_world_verification()
goto_home_joint_position()
world_refresh()
world_review_pending()
final = world_snapshot()
assert final["semantic"]["structure_revision"] >= 1
assert len(final["articulation"]["reviews"]) >= 1
print("Online World integration completed", final["articulation"]["model_revision"])
''')
    sp.check_authored(policy.read_text())
    execute(state(), 'diagnostic', policy, BASE/'smoke-world-config.json')
else:
    raise ValueError('expected snapshot, author or run')
