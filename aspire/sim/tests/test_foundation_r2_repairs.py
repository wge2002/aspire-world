"""CPU-only end-to-end exit and report-contract regressions; no task solutions."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

SIM = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SIM.parents[1]), str(SIM / 'scripts/libero'), str(SIM / 'scripts/common')]
from test_executable_world import WORLD, session, source
from aspire.sim.cap.world_model.executable_world import run_offline
import executable_world_profile as profile
from native_cc_guard import denial as frozen_denial
from test_native_world_fixloop import Harness, POLICY, WORLD as LEGACY_WORLD, TRANSCRIPT
import native_world_protocol as protocol

SUPPORT = SIM / 'docs/experiments/code-world-qwen-foundation-r2-20260930/support'
sys.path.insert(0, str(SUPPORT))
import two_task_scope as scope
import cell_read_guard


@pytest.mark.parametrize('mode', ['rehearsal', 'replay'])
@pytest.mark.parametrize('exit_value,expected', [('0', 'unsupported'), ('None', 'unsupported'), ('7', 'program_error'), ("'failed'", 'program_error')])
def test_policy_exit_leaves_report_and_correct_profile_classification(tmp_path, mode, exit_value, expected):
    with session(tmp_path) as s:
        s.invoke('get_observation', lambda: {'value': 2}, (), {})
    policy = tmp_path / 'policy.py'
    policy.write_text(f'get_observation()\nraise SystemExit({exit_value})\nraise AssertionError("unreachable")\n')
    report = profile.run_mode({'c_arm': 'full', 'task': 'synthetic'}, SIM, dict(os.environ), tmp_path,
                              {'policy': policy, 'world': tmp_path / 'world.py'}, mode,
                              tmp_path / 'session/public_tape.jsonl', 60)
    assert report['status'] == expected, report
    assert report['retryable'] is False
    assert report['conclusion'] == ('unknown' if expected == 'unsupported' else 'authored_error')
    saved = json.loads((tmp_path / mode / 'offline_result.json').read_text())
    assert saved['api_calls'] == 1 and saved['termination'] == 'policy_exit'
    assert 'goal' not in saved
    assert (tmp_path / mode / 'manifest.json').exists()
    if mode == 'replay':
        assert saved['matched_calls'] == saved['tape_calls'] == 1


def test_world_load_exit_is_authored_error_and_restores_module(tmp_path):
    previous = sys.modules.get('world')
    world, _ = source(tmp_path, 'raise SystemExit(0)\n' + WORLD)
    policy = tmp_path / 'policy.py'; policy.write_text('get_observation()\n')
    report = run_offline(policy, world, tmp_path / 'out')
    assert report['status'] == 'program_error' and report['stage'] == 'world_load'
    assert sys.modules.get('world') is previous
    assert json.loads((tmp_path / 'out/manifest.json').read_text())['status'] == 'program_error'


def test_keyboard_interrupt_is_not_treated_as_policy_exit(tmp_path):
    world, _ = source(tmp_path)
    policy = tmp_path / 'policy.py'; policy.write_text('raise KeyboardInterrupt()\n')
    with pytest.raises(KeyboardInterrupt):
        run_offline(policy, world, tmp_path / 'out')
    assert not (tmp_path / 'out/offline_result.json').exists()


def test_real_prompt_handoff_and_unchanged_file_boundaries(tmp_path):
    import native_world_campaign as campaign
    case = {'id': 'bowl_C', 'condition': 'C', 'profile': 'judgment', 'task': 'put_the_bowl_on_the_plate',
            'suite': 'libero_goal_swap', 'gpu': 6, 'sim': str(tmp_path/'sim'), 'control': str(tmp_path/'control'),
            'skill_library_dir': 'private_skills', 'executable_world_revision': 'r1', 'c_lineage': 'fresh',
            'foundation_revision': 'r1', 'world_use_revision': 'r1', 'c_arm': 'full',
            'world_interface_doc': 'docs/experiments/code-world-qwen-foundation-r2-20260930/NATIVE_WORLD_INTERFACE.md'}
    repo = Path(case['sim']); repo.mkdir()
    outputs = Path(case['control'])/'outputs'; outputs.mkdir(parents=True)
    (repo/'outputs').symlink_to(outputs, target_is_directory=True)
    worker = scope.report_handoff_worker(campaign.worker_prompt(case, SIM))
    coordinator = scope.report_handoff_coordinator(campaign.coordinator_prompt(case, tmp_path/'worker-prompt.md'), case)
    assert 'BEGIN_FINDINGS_MD' in worker and 'END_FINDINGS_MD' in worker
    assert 'Write `$TASK_DIR/findings.md`.' not in worker
    assert 'native_world_protocol.py check\n' not in worker
    assert 'native_world_protocol.py select --reason' in worker
    assert coordinator.index('save that text verbatim') < coordinator.index('native_world_protocol.py check')
    assert 'SAME worker' in coordinator
    task = outputs/'libero_fix_loop'/case['suite']/case['task']
    for path in [task/'findings.md', task/'task_analysis.md', task/'fix_code.py', task/'attempts/seed_51_BLOCKED.md']:
        payload = {'tool_name': 'Write', 'cwd': str(repo), 'tool_input': {'file_path': str(path), 'content': 'fixture'}}
        assert frozen_denial(payload, case, repo) is None
        assert cell_read_guard.denial(payload, case) is None
    for path in [tmp_path/'other/outputs/findings.md', Path(case['control'])/'heldout/findings.md', repo/'scripts/evil.py']:
        payload = {'tool_name': 'Write', 'cwd': str(repo), 'tool_input': {'file_path': str(path)}}
        assert frozen_denial(payload, case, repo) or cell_read_guard.denial(payload, case)


def test_all_failure_budget_exhaustion_finishes_after_coordinator_report(tmp_path):
    # Synthetic ledger fixture: exercise actual selection/completion/finalize,
    # with simulator execution mocked by the established protocol harness.
    with contextlib.ExitStack() as stack:
        h = Harness(stack, 'C')
        h.initial_batch(failures=h.case['dev_seeds'])
        for seed in h.case['dev_seeds']:
            for _ in range(2): h.trial('repair', seed)
            h.write(f'attempts/seed_{seed}_BLOCKED.md', '## Root Cause\nAlgorithmic\n## Details\nSynthetic failure\n## What Was Tried\nThree recorded executions\n')
        h.write('task_analysis.md', 'Synthetic development observation; no simulator geometry.\n')
        h.write('fix_code.py', POLICY); h.write('fix_world_program.py', LEGACY_WORLD)
        h.write('fix_inventory.json', (h.task_dir/'initial_inventory.json').read_text())
        (h.repo/'outputs/working_codes'/f"{h.case['suite']}_{h.case['task']}_fix.py").write_text(POLICY)
        bundle = protocol.selected_bundle(h.case, h.state)
        h.state.select(bundle, 'All tested candidates failed; retain the only tested bundle.')
        before = protocol.check(h.case, h.repo, h.state)
        assert not before['ready'] and any('findings' in error for error in before['errors'])
        # Coordinator persists the worker's exact honest report; no new trial.
        h.write('findings.md', '## Root causes observed\nSynthetic algorithmic failure on every seed.\n## What fixed them\nNo repair succeeded.\n## Generalizable patterns\nnone\n## Blocked seeds\nAll development seeds exhausted their three attempts.\n')
        assert protocol.check(h.case, h.repo, h.state)['ready']
        count = len(h.state.executed())
        protocol.finalize(h.case, h.repo, h.state, [h.write('transcript.jsonl', TRANSCRIPT)])
        assert len(h.state.executed()) == count
        assert (h.task_dir/'stage1_result.json').exists()
