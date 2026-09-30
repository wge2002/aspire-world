#!/usr/bin/env python3
"""One reviewed blank-line adjudication; run the existing frozen evaluator."""
import argparse
import collections
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import fcntl

ROOT = Path('/mnt/home/gewang/experiments/code-world-c-opt-ablation-20260920-r2')
CONTROL = ROOT / 'bowl_C_full'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'support'))
    import legacy_stager as staging
    staging.PARENT = ROOT
    from native_lineage_r2 import audit_lineage
    from infra_guard import bind_child_env, fault
    receipt = json.loads((ROOT / 'prepare-receipt.json').read_text())
    case = staging.verify_cell(receipt['cells']['bowl_C_full'], receipt['frozen_support'])
    repo = Path(case['sim'])
    sys.path.insert(0, str(repo / 'scripts/libero'))
    import native_world_campaign as campaign
    lock = (CONTROL / 'recovery.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    prior = json.loads((CONTROL / 'campaign_state.json').read_text())
    assert prior['status'] == 'recovery_blocked' and prior['coordinator_exit_code'] == 0
    transcript = Path(prior['recovery_dir']) / 'turn-00.stdout.jsonl'
    call_id, agent_id = 'toolu_012vUgkGkSxzEct33Ekct5wK', 'a87166440ce9db761'
    prompts = set()
    for line in transcript.read_text().splitlines():
        event = json.loads(line)
        for block in (event.get('message') or {}).get('content', []):
            if isinstance(block, dict) and block.get('id') == call_id and block.get('type') == 'tool_use':
                prompts.add(block['input']['prompt'].strip())
    assert len(prompts) == 1
    actual = prompts.pop()
    expected = (CONTROL / 'worker-prompt.md').read_text().strip()
    marker = '\n\n**The selected bundle must itself have been tested.**'
    assert expected.count(marker) == 1
    assert actual == expected.replace(marker, '\n' + marker, 1), 'Only the reviewed single blank line is admissible'
    audit = audit_lineage([transcript], known_primaries={agent_id: call_id})
    assert audit['ok'] and audit['primary_worker'] == agent_id and len(audit['sessions']) == 1
    task = repo / 'outputs/libero_fix_loop' / case['suite'] / case['task']
    ledger = task / 'development_state.json'
    before = json.loads(ledger.read_text())
    counts = collections.Counter(r['seed'] for r in before['trials'] if r.get('charged'))
    assert counts == collections.Counter({s: 3 for s in range(51, 66)})
    assert all(r['status'] != 'running' for r in before['trials']) and not fault(case)
    assert before['selected']['bundle_sha256'] == 'b5a8880c24b9f5e48795b4f42c58ce8112dc29b7e53014b118cb5347a761f1ed'
    evidence = {'at': datetime.now(timezone.utc).isoformat(), 'lineage': audit,
                'expected_prompt_sha256': hashlib.sha256(expected.encode()).hexdigest(),
                'actual_prompt_sha256': hashlib.sha256(actual.encode()).hexdigest(),
                'deviation': 'one extra blank line before the selected-bundle paragraph',
                'charged_attempts': 45, 'reset_attempts': False, 'selected': before['selected'],
                'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if args.dry_run:
        print(json.dumps(evidence, indent=2))
        return
    folder = CONTROL / 'adjudication-20260920'
    folder.mkdir(exist_ok=False)
    shutil.copy2(CONTROL / 'campaign_state.json', folder / 'prior-campaign-state.json')
    shutil.copy2(ledger, folder / 'ledger-before.json')
    campaign.atomic_json(folder / 'evidence.json', evidence)
    env = bind_child_env(campaign.native_environment(case, repo, CONTROL / 'case.json', Path(case['claude_config_dir'])), case)
    for name, command in [('check', ['check']), ('finalize', ['finalize', '--transcript', str(transcript)])]:
        result = campaign.protocol(case, repo, env, folder, name, *command)
        if result['exit_code']:
            raise RuntimeError(f'Original {name} refused; inspect {folder}')
    after = json.loads(ledger.read_text())
    assert after['trials'] == before['trials'] and after['selected'] == before['selected']
    state = {**prior, 'status': 'heldout_running', 'blocker': None, 'adjudication': str(folder),
             'resumed_at': evidence['at'], 'resume_pid': os.getpid(), 'solver_transcripts': [str(transcript)]}
    state.pop('finished_at', None)
    campaign.atomic_json(CONTROL / 'campaign_state.json', state)
    with (folder / 'heldout.log').open('x') as log:
        code = subprocess.run([str(repo / '.venv-libero/bin/python3'), str(ROOT / 'support/evaluate.py'),
                               '--case', str(CONTROL / 'case.json')], cwd=repo, env=env,
                              stdout=log, stderr=subprocess.STDOUT).returncode
    report_path = CONTROL / 'heldout/heldout_result.json'
    report = json.loads(report_path.read_text()) if report_path.is_file() else {}
    ok = code == 0 and report.get('all_seeds_accounted') and not report.get('unusable_seeds')
    state.update(status='complete' if ok else 'recovery_blocked',
                 blocker=None if ok else f'Frozen evaluation incomplete: exit {code}',
                 finished_at=datetime.now(timezone.utc).isoformat(), heldout={'exit_code': code})
    campaign.verify_runtime(case, repo)
    campaign.atomic_json(CONTROL / 'campaign_state.json', state)
    campaign.atomic_json(folder / 'final-state.json', state)
    print(json.dumps({'status': state['status'], 'blocker': state['blocker'], 'counts': report.get('counts')}), flush=True)
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
