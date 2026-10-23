"""CC_TASK_12: G5 (worker lost to an API error after an accepted resume) and G6 (watchdog rows).

Groups:
  F. `worker_recovery.failure_evidence` reads only structured native events;
  W. process layer: `run_replay` grants a finished policy its persistence grace;
  V. protocol: `watchdog_verdict` and how `_settle_trial` records each verdict;
  C. runtime `close-infra`: a watchdog row is settled from its own evidence;
  D. the p6c driver: G5 recovery round, G6 outer disposition (model and simulator
     faked as in test_p6b_recovery.py);
  X. replays of the two real l10 cells (mugmicrowave_p6b_r1, twomoka_p6b_r1).
"""
import contextlib
import hashlib
import importlib.util
import json
import os
import signal
import sys
import tarfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import test_p6b_recovery as p6b
from test_p5_budget import BudgetHarness
from test_p6b_recovery import init, load, primary, send, write
from test_p6b_recovery import cell as p6b_cell  # noqa: F401  (fixture, used through `cell`)
import native_cc_trial_process as trial_process
import native_world_fixloop_state as ledger
import native_world_protocol as protocol

SIM = Path(__file__).resolve().parents[1]
P6C = SIM / 'docs/experiments/code-world-pipeline-repair-p6c-20261022/support'
FIXTURES = SIM / 'tests/fixtures/p6c_repair'
OVERFLOW = ("Agent terminated early due to an API error: API Error: 400 This model's maximum context "
            "length is 1000000 tokens. However, you requested 64000 output tokens and your prompt "
            "contains at least 936001 input tokens")
TRANSIENT = 'Agent terminated early due to an API error: API Error: 503 upstream overloaded'

recovery = load(P6C / 'worker_recovery.py', 'p6c_worker_recovery')
runtime = load(SIM / 'scripts/libero/runtime/native_world_protocol.py', 'p6c_runtime_protocol')


# ---- stream helpers -------------------------------------------------------------------

def started(worker):
    return {'type': 'system', 'subtype': 'task_started', 'task_id': worker, 'task_type': 'local_agent'}


def ended(worker, status='failed', error=OVERFLOW):
    return [{'type': 'system', 'subtype': 'task_updated', 'task_id': worker,
             'patch': {'status': status, **({'error': error} if status == 'failed' else {})}},
            {'type': 'system', 'subtype': 'task_notification', 'task_id': worker, 'status': status,
             'summary': error if status == 'failed' else 'done'}]


def synthetic_error():
    return {'type': 'assistant', 'parent_tool_use_id': 'call', 'is_api_error_message': True,
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': 'API Error: 400'}]}}


def work():
    return {'type': 'assistant', 'parent_tool_use_id': 'call', 'message': {'model': 'm', 'content': []}}


def resumed_and_failed(worker, number, error=OVERFLOW):
    return [*send(worker, number, True), started(worker), synthetic_error(), *ended(worker, error=error)]


# ---- F: failure evidence -----------------------------------------------------------------

@pytest.mark.parametrize('events,carried,dead,reason', [
    ([started('w1'), work(), *ended('w1', 'completed'), *resumed_and_failed('w1', 1)], 0, True, 'context_overflow'),
    ([started('w1'), work(), *ended('w1', error=OVERFLOW)], 0, True, 'context_overflow'),   # overflowed mid-work
    ([*resumed_and_failed('w1', 1, TRANSIENT)], 0, False, None),                            # one transient
    ([*resumed_and_failed('w1', 1, TRANSIENT), *resumed_and_failed('w1', 2, TRANSIENT)], 0, True,
     'repeated_api_failure'),
    ([*resumed_and_failed('w1', 1, TRANSIENT)], 1, True, 'repeated_api_failure'),            # carried over
    ([*resumed_and_failed('w1', 1, TRANSIENT), started('w1'), work(), *ended('w1', error=TRANSIENT)],
     0, False, None),                                                                        # it worked again
    ([*resumed_and_failed('w1', 1), started('w1'), work(), *ended('w1', 'completed')], 0, False, None),
    ([*resumed_and_failed('w1', 1), started('w1'), work()], 0, False, None),                 # still running
    ([*resumed_and_failed('w9', 1)], 0, False, None),                                        # another agent
    ([*send('w1', 1, True), started('w1'), *ended('w1', error='Agent stopped: tool error')], 3, False, None),
    ([init('s')], 0, False, None),
])
def test_failure_evidence_is_structural(tmp_path, events, carried, dead, reason):
    path = tmp_path / 'turn.jsonl'
    write(path, events)
    evidence = recovery.failure_evidence(path, 'w1', carried)
    assert evidence['dead'] is dead and evidence['reason'] == reason


def test_failure_evidence_ignores_prose(tmp_path):
    path = tmp_path / 'turn.jsonl'
    write(path, [{'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': OVERFLOW}]}},
                 {'type': 'result', 'result': OVERFLOW}])
    assert recovery.failure_evidence(path, 'w1')['dead'] is False


def test_handoff_names_the_actual_loss(tmp_path):
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'development_state.json').write_text(json.dumps({'trials': []}))
    text, index = recovery.handoff(task_dir=task, check={}, generation=2, previous_worker='w1',
                                   remaining_seconds=60, turn=1, reason='worker_api_failure')
    assert 'API error' in text and 'could not be resumed' not in text and index['reason'] == 'worker_api_failure'
    text, _ = recovery.handoff(task_dir=task, check={}, generation=2, previous_worker='w1',
                               remaining_seconds=60, turn=1)
    assert 'could not be resumed' in text


# ---- W: process layer ------------------------------------------------------------------------

def replay_command(tmp_path, name, *, summary_after, then_sleep):
    """A child that writes `<out>/s/t/m/run/trial_51_.../summary.txt` after `summary_after` s."""
    out = tmp_path / name / 'results'
    trial = out / 's/t/m/run/trial_51_sandboxrc_0_reward_1.000_taskcompleted_1'
    code = (f"import time, pathlib; time.sleep({summary_after or 0}); "
            + (f"p = pathlib.Path({str(trial)!r}); p.mkdir(parents=True); (p / 'summary.txt').write_text('x'); "
               if summary_after is not None else "")
            + f"time.sleep({then_sleep})")
    directory = tmp_path / name
    directory.mkdir(exist_ok=True)
    return [sys.executable, '-c', code, '--args.output-dir', str(out)], directory


@pytest.mark.parametrize('threaded', [False, True])
def test_a_finished_policy_gets_its_persistence_grace(tmp_path, threaded):
    command, directory = replay_command(tmp_path, 'grace', summary_after=0.2, then_sleep=1.5)
    extra = {'interrupt': trial_process.BatchInterrupt()} if threaded else {}
    code, error = trial_process.run_replay(command, repo=tmp_path, env=dict(os.environ), directory=directory,
                                           timeout=1, persistence_grace=5, **extra)
    life = json.loads((directory / 'lifecycle.json').read_text())
    assert (code, error) == (0, '') and life['persistence_grace']['seconds'] == 5


def test_a_policy_still_running_at_the_timeout_gets_no_grace(tmp_path):
    command, directory = replay_command(tmp_path, 'running', summary_after=None, then_sleep=30)
    began = time.monotonic()
    code, error = trial_process.run_replay(command, repo=tmp_path, env=dict(os.environ), directory=directory,
                                           timeout=1, persistence_grace=20)
    assert time.monotonic() - began < 10
    assert code == -signal.SIGTERM and error.startswith('TimeoutExpired') and 'after 1 seconds' in error
    assert 'persistence_grace' not in json.loads((directory / 'lifecycle.json').read_text())


def test_the_grace_is_bounded(tmp_path):
    command, directory = replay_command(tmp_path, 'bounded', summary_after=0.2, then_sleep=30)
    code, error = trial_process.run_replay(command, repo=tmp_path, env=dict(os.environ), directory=directory,
                                           timeout=1, persistence_grace=1)
    assert code == -signal.SIGTERM and 'after 2 seconds' in error
    assert json.loads((directory / 'lifecycle.json').read_text())['persistence_grace']['seconds'] == 1


def test_default_grace_is_the_adapter_cleanup_margin():
    assert trial_process.PERSISTENCE_GRACE_SECONDS == protocol.CLEANUP_MARGIN_SECONDS == 60


# ---- V: protocol watchdog verdict -----------------------------------------------------------------

TIMEOUT = "TimeoutExpired: Command '[...]' timed out after 900 seconds"


@pytest.fixture
def h():
    with contextlib.ExitStack() as stack:
        harness = BudgetHarness(stack)
        stack.enter_context(patch.object(protocol, 'world_use', lambda case: None))
        yield harness


def killed(h, *, finished_policy, last_api=None, passed=False):
    """A replay the watchdog stopped: an optional recorded outcome and the world tape's last call."""
    def replay(command, *, repo, env, directory, timeout, stdin_path=None):
        started = time.time() - 900.3
        if finished_policy:
            h.passing[h.current] = {51} if passed else set()
            h.fake_replay(command, repo=repo, env=env, directory=directory, timeout=timeout)
            trial = next(Path(command[command.index('--args.output-dir') + 1]).rglob('trial_51_*'))
            (trial / 'summary.txt').write_text('summary')
            os.utime(trial / 'summary.txt', (started + 891, started + 891))
        world = Path(directory) / 'judgment_world'
        world.mkdir(parents=True, exist_ok=True)
        if last_api is not None:
            (world / 'events.jsonl').write_text(json.dumps({'event': 'public_api', 'elapsed_seconds': last_api}) + '\n')
        (Path(directory) / 'lifecycle.json').write_text(json.dumps(
            {'started_at': started, 'finished_at': started + 900.3, 'process_exit_code': -15}))
        return -15, TIMEOUT
    return replay


def phase_a(h):
    h.trial('snapshot', 51)
    h.trial('initial', 51)
    return h.policy('fix1.py', 'pull', 'v1')


@pytest.mark.parametrize('passed', [False, True])
def test_a_persistence_cut_is_the_outcome_the_policy_recorded(h, passed):
    code = phase_a(h)
    with patch.object(protocol, 'run_replay', killed(h, finished_policy=True, last_api=873.6, passed=passed)):
        record = h.trial('repair', 51, code)
    assert record['status'] == 'complete' and record['spends_retry'] and record['error'] == ''
    assert record['task_completed'] == int(passed) and record['watchdog']['kind'] == 'persistence_cut'
    assert record['watchdog']['attribution'] == 'harness' and record['watchdog']['policy_end_seconds'] == 891.0
    assert h.state.progress()['infrastructure_errors'] == []
    assert h.state.budget_phase() == ('B' if passed else 'A')


def test_a_policy_that_ran_out_of_budget_is_a_failed_trial(h):
    code = phase_a(h)
    with patch.object(protocol, 'run_replay', killed(h, finished_policy=False, last_api=878.0)):
        record = h.trial('repair', 51, code)
    assert record['status'] == 'complete' and record['sandbox_rc'] == 1 and record['task_completed'] == 0
    assert record['watchdog']['kind'] == 'policy_timeout' and record['watchdog']['attribution'] == 'policy'
    assert h.state.covered_seeds(h.digest('fix1.py')) == [51]  # graded: worker-visible failure


@pytest.mark.parametrize('last_api,kind', [(500.0, 'stalled'), (None, 'unknown')])
def test_a_stalled_or_unjudgeable_replay_stays_infrastructure(h, last_api, kind):
    code = phase_a(h)
    with patch.object(protocol, 'run_replay', killed(h, finished_policy=False, last_api=last_api)):
        record = h.trial('repair', 51, code)
    assert record['status'] == 'infrastructure_error' and record['watchdog']['kind'] == kind
    assert h.state.progress()['infrastructure_errors'] == [record['directory']]


def test_verdict_applies_only_to_watchdog_kills_of_oracle_trial_phases(h, tmp_path):
    case = dict(h.case)
    assert protocol.watchdog_verdict(case, tmp_path, tmp_path, 'repair', 51, 'OSError: x') is None
    assert protocol.watchdog_verdict(case, tmp_path, tmp_path, 'diagnostic', 51, TIMEOUT) is None
    assert protocol.watchdog_verdict(case, tmp_path, tmp_path, 'snapshot', 51, TIMEOUT) is None
    case['development_gate'] = 'self_eval'
    assert protocol.watchdog_verdict(case, tmp_path, tmp_path, 'repair', 51, TIMEOUT) is None


def test_a_trial_directory_without_summary_is_not_settled(h, tmp_path):
    (tmp_path / 'results/trial_51_sandboxrc_0_reward_1.000_taskcompleted_1').mkdir(parents=True)
    verdict = protocol.watchdog_verdict(h.case, tmp_path, tmp_path / 'results', 'repair', 51, TIMEOUT)
    assert verdict['settled'] is False and verdict['kind'] == 'unknown'


# ---- C: runtime close-infra ----------------------------------------------------------------------------

def legacy_row(h, *, finished_policy, last_api):
    """A row exactly as the pre-CC_TASK_12 protocol left it: infrastructure_error."""
    code = phase_a(h)
    with patch.object(protocol, 'watchdog_verdict', lambda *a: None), \
            patch.object(protocol, 'run_replay', killed(h, finished_policy=finished_policy, last_api=last_api)):
        record = h.trial('repair', 51, code)
    assert record['status'] == 'infrastructure_error'
    return record


def test_close_infra_settles_a_persistence_cut_row_from_its_evidence(h):
    record = legacy_row(h, finished_policy=True, last_api=873.6)
    row = runtime.close_attempt(h.state, record['directory'], 'outer', h.case)
    assert row['status'] == 'complete' and row['recovery']['disposition'] == runtime.SETTLED
    assert row['recovery']['original']['status'] == 'infrastructure_error'
    assert row['recovery']['attempts_refunded'] == 0 and row['spends_retry']
    assert row['watchdog']['kind'] == 'persistence_cut' and row['task_completed'] == 0
    assert h.state.progress()['infrastructure_errors'] == []
    with pytest.raises(ledger.ProtocolError, match='ended infrastructure_error'):
        runtime.close_attempt(h.state, record['directory'], 'again', h.case)  # settled once
    runtime.NativeWorldState.progress = runtime.progress
    try:
        assert [r['kind'] for r in h.state.progress()['settled_watchdog_attempts']] == ['persistence_cut']
    finally:
        runtime.NativeWorldState.progress = runtime.original_progress


def test_close_infra_settles_a_policy_timeout_row_as_a_failed_trial(h):
    record = legacy_row(h, finished_policy=False, last_api=880.0)
    row = runtime.close_attempt(h.state, record['directory'], 'outer', h.case)
    assert row['status'] == 'complete' and row['sandbox_rc'] == 1 and row['task_completed'] == 0


def test_close_infra_still_closes_a_stalled_row_without_evidence(h):
    record = legacy_row(h, finished_policy=False, last_api=100.0)
    row = runtime.close_attempt(h.state, record['directory'], 'outer', h.case)
    assert row['status'] == 'infrastructure_error' and row['recovery']['disposition'] == runtime.DISPOSITION


def test_close_infra_without_a_case_keeps_the_old_refusal(h):
    record = legacy_row(h, finished_policy=True, last_api=873.6)
    with pytest.raises(ledger.ProtocolError, match='usable-looking trial evidence'):
        runtime.close_attempt(h.state, record['directory'], 'outer')


# ---- D: the p6c driver --------------------------------------------------------------------------------

@pytest.fixture
def p6c_overlay(monkeypatch):
    monkeypatch.setattr(p6b, 'P6B', P6C)  # the p6b harness copies this overlay last


@pytest.fixture
def cell(p6c_overlay, p6b_cell):
    assert 'CC_TASK_12' in Path(p6b_cell.driver.__file__).read_text()
    return p6b_cell


def test_overflow_after_accepted_resume_opens_a_recovery_round(cell):
    steps = [{'events': [*primary('s1', 'w1'), started('w1'), work(), *ended('w1', 'completed'),
                         *resumed_and_failed('w1', 1)], 'rows': 3},
             {'events': primary('s2', 'w2'), 'rows': 1}, {'code': -2}]
    state, calls = cell(steps, lambda n: p6b.incomplete('A'))
    turn1 = calls['native'][1]
    assert not turn1['resume'] and 'WORKER RECOVERY ROUND (generation 2)' in turn1['prompt']
    assert 'API error' in turn1['prompt'] and 'could not be resumed' not in turn1['prompt']
    generation = state['worker_generations'][1]
    assert generation['reason'] == 'worker_api_failure' and generation['detected_turn'] == 0
    assert generation['worker'] == 'w2' and generation['attempts_spent'] == 3
    evidence = json.loads(Path(generation['evidence']).read_text())
    assert evidence['dead'] and evidence['reason'] == 'context_overflow'
    assert 'API error' in Path(generation['assignment']).read_text()
    assert state['failed_rounds'][0]['unresumable'] == generation['evidence']


def test_one_transient_api_failure_resumes_then_two_replace(cell):
    steps = [{'events': [*primary('s1', 'w1'), *resumed_and_failed('w1', 1, TRANSIENT)]},
             {'events': [init('s1'), *resumed_and_failed('w1', 2, TRANSIENT)]},
             {'events': primary('s2', 'w2'), 'rows': 1}, {'code': -2}]
    state, calls = cell(steps, lambda n: p6b.incomplete('A'))
    assert [c['resume'] for c in calls['native']] == [False, True, False, True]
    assert state['worker_generations'][1]['detected_turn'] == 1
    assert json.loads(Path(state['worker_generations'][1]['evidence']).read_text())['reason'] == 'repeated_api_failure'


def test_a_running_row_defers_the_replacement(cell):
    steps = [{'events': [*primary('s1', 'w1'), *resumed_and_failed('w1', 1)], 'rows': 0}, {'code': -2}]
    cell.ledger.write_text(json.dumps({'trials': [{'phase': 'repair', 'seed': 51, 'status': 'running',
                                                    'directory': 'd1', 'spends_retry': True}]}))
    state, calls = cell(steps, lambda n: p6b.incomplete('A'))
    assert len(state['worker_generations']) == 1 and calls['native'][1]['resume']


def run_with_disposals(cell, monkeypatch, steps, checks, results):
    seen = []
    driver = cell.driver
    real_main = driver.main

    def main(argv):
        campaign = sys.modules['native_world_campaign']
        fake = campaign.protocol

        def protocol_step(case, repo, env, folder, step, command, *args):
            if command == 'close-infra':
                seen.append(args[1])
                code = results.pop(0)
                return {'exit_code': code, 'result': {'closed': {'status': 'complete', 'recovery': {
                    'disposition': 'settled_from_watchdog_evidence'}}} if code == 0 else {'error': 'refused'}}
            return fake(case, repo, env, folder, step, command, *args)
        campaign.protocol = protocol_step
        return real_main(argv)
    monkeypatch.setattr(driver, 'main', main)
    state, calls = cell(steps, checks)
    return state, calls, seen


def test_persistent_infrastructure_is_disposed_by_the_driver_and_development_continues(cell, monkeypatch):
    infra = p6b.incomplete('A', infrastructure_errors=['development/repair/seed_51/attempt_19'])
    state, calls, seen = run_with_disposals(
        cell, monkeypatch, [{'events': primary('s1', 'w1')}, {}, {}, {'rows': 1}, {'code': -2}],
        [p6b.incomplete('A'), infra, infra, infra, p6b.incomplete('A')], [0])
    assert seen == ['development/repair/seed_51/attempt_19']
    assert len(calls['native']) == 5 and 'settled the unresolved infrastructure rows' in calls['native'][3]['prompt']
    assert state['infrastructure_dispositions'][0]['disposition'] == 'settled_from_watchdog_evidence'
    assert state['blocked']['phase'] == 'cancelled'


def test_a_row_the_driver_cannot_settle_still_stops(cell, monkeypatch):
    infra = p6b.incomplete('A', infrastructure_errors=['development/repair/seed_51/attempt_19'])
    state, calls, seen = run_with_disposals(cell, monkeypatch, [{'events': primary('s1', 'w1')}, {}, {}],
                                            [p6b.incomplete('A')] + [infra] * 3, [2])
    assert seen and state['blocked']['phase'] == 'infrastructure' and len(calls['native']) == 3


def test_dispositions_are_bounded_per_cell(cell, monkeypatch):
    rows = [f'development/repair/seed_51/attempt_{n}' for n in range(1, 5)]
    infra = p6b.incomplete('A', infrastructure_errors=rows)
    state, calls, seen = run_with_disposals(cell, monkeypatch, [{'events': primary('s1', 'w1')}, {}, {}],
                                            [p6b.incomplete('A')] + [infra] * 3, [0] * 4)
    assert seen == [] and state['blocked']['phase'] == 'infrastructure'


def test_screening_blockers_are_not_the_drivers_to_settle(cell, monkeypatch):
    infra = p6b.incomplete('A', infrastructure_errors=['x'], unresolved_screening_blockers=[{'seed': 51}])
    state, calls, seen = run_with_disposals(cell, monkeypatch, [{'events': primary('s1', 'w1')}, {}, {}],
                                            [p6b.incomplete('A')] + [infra] * 3, [0])
    assert seen == [] and state['blocked']['phase'] == 'infrastructure'


# ---- X: replays of the two real l10 cells ------------------------------------------------------------------

def extract(tmp_path, name):
    manifest = json.loads((FIXTURES / 'manifest.json').read_text())[name]
    archive = FIXTURES / f'{name}.tgz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['archive_sha256']
    source = tmp_path / name
    with tarfile.open(archive) as tar:
        tar.extractall(source, filter='data')
    for relative, entry in manifest['files'].items():
        assert hashlib.sha256((source / relative).read_bytes()).hexdigest() == entry['sha256']
    return source, manifest


def test_real_mugmicrowave_turn0_is_recognised_and_turn1_is_the_recovery_round(tmp_path, monkeypatch, cell):
    """l10 mugmicrowave: the real turn-00 stream (worker resumed, then refused for overflow)."""
    source, manifest = extract(tmp_path, 'mugmicrowave_p6b_r1')
    driver = cell.driver
    import lineage
    monkeypatch.setattr(driver, 'identity', lambda transcripts, assignment: (
        lambda audit: (audit['sessions'][0]['session_id'], audit['primary_worker'])
    )(lineage.audit_assignment(transcripts, assignment)))
    (cell.control / 'worker-prompt.md').write_text((source / 'worker-prompt.md').read_text())
    (cell.control / 'coordinator-prompt.md').write_text('real coordinator prompt (not replayed)\n')
    monkeypatch.setattr(sys.modules['native_world_campaign'], 'write_prompts', lambda *a: (
        cell.control / 'worker-prompt.md', cell.control / 'coordinator-prompt.md'))
    cell.ledger.write_bytes((source / 'development_state.json').read_bytes())
    real = json.loads((source / 'outer_check-00.json').read_text())

    def replay(command, **kwargs):
        out = Path(kwargs['stdout_path'])
        if out.name == 'turn-00.stdout.jsonl':
            out.write_bytes((source / 'turn-00.stdout.jsonl').read_bytes())
            (out.parent / 'assignment-audit.jsonl').write_bytes((source / 'assignment-audit.jsonl').read_bytes())
            return 0
        out.write_text('')
        return -2
    monkeypatch.setattr(driver.fixed_transport, 'run_native_cc', replay)
    state, calls = cell([], [real, real])
    generations = state['worker_generations']
    assert generations[0]['worker'] == manifest['worker'] and generations[0]['session'] == manifest['session']
    assert generations[1]['reason'] == 'worker_api_failure' and generations[1]['detected_turn'] == 0
    assert generations[1]['attempts_spent'] == manifest['attempts_spent'] == 44
    assert generations[1]['ledger_rows'] == manifest['ledger_rows']
    evidence = json.loads(Path(generations[1]['evidence']).read_text())
    assert evidence['dead'] and evidence['reason'] == 'context_overflow' and not evidence['running_rows']
    assert evidence['last_run']['activity'] == 0 and '936001 input tokens' in evidence['last_run']['error']
    prompt = next(cell.control.glob('campaign-*/turn-01-prompt.md')).read_text()
    assert 'OUTER-DRIVER WORKER RECOVERY ROUND (generation 2)' in prompt
    assert Path(generations[1]['assignment']).read_text().startswith((source / 'worker-prompt.md').read_text())
    assert cell.ledger.read_bytes() == (source / 'development_state.json').read_bytes()
    # Task 9's check alone sees nothing here: the real worker-resume evidence of turn 0 is benign.
    assert not p6b.recovery.resume_evidence(source / 'turn-00.stdout.jsonl', manifest['worker'])['unresumable']
    # The real turn 1 (the first of the 63 idle rounds) is recognised on its own as well.
    turn1 = recovery.failure_evidence(source / 'turn-01.stdout.jsonl', manifest['worker'])
    assert turn1['dead'] and turn1['last_run']['activity'] == 0


def moka_state(source, manifest):
    task = source / 'task'
    task.mkdir()
    (task / 'development_state.json').write_bytes((source / 'development_state.json').read_bytes())
    attempt = source / manifest['attempt']
    target = task / manifest['attempt']
    target.parent.mkdir(parents=True)
    attempt.rename(target)
    data = json.loads((task / 'development_state.json').read_text())
    return task, ledger.NativeWorldState(task, data['identity'], resume=True)


MOKA_CASE = {'condition': 'C', 'profile': 'judgment', 'development_gate': 'oracle', 'trial_timeout': 900}


def test_real_twomoka_attempt19_is_a_persistence_cut_of_a_finished_policy(tmp_path):
    source, manifest = extract(tmp_path, 'twomoka_p6b_r1')
    task, state = moka_state(source, manifest)
    row = next(r for r in state.data['trials'] if r['directory'] == manifest['attempt'])
    directory = task / manifest['attempt']
    verdict = protocol.watchdog_verdict(MOKA_CASE, directory, directory / 'results', row['phase'], row['seed'],
                                        row['error'])
    assert verdict['kind'] == 'persistence_cut' and verdict['settled']
    assert verdict['result']['task_completed'] == 0 and verdict['result']['sandbox_rc'] == 0
    assert 885 < verdict['policy_end_seconds'] < 900 and verdict['killed_after_seconds'] == 900.3
    # Before: close-infra refuses it, the only rule that could settle it. After: settled, nothing refunded.
    with pytest.raises(ledger.ProtocolError, match='usable-looking trial evidence'):
        runtime.close_attempt(state, manifest['attempt'], 'old rule')
    spent = sum(bool(r.get('spends_retry')) for r in state.data['trials'])
    settled = runtime.close_attempt(state, manifest['attempt'], 'replay', MOKA_CASE)
    assert settled['status'] == 'complete' and settled['recovery']['disposition'] == runtime.SETTLED
    assert sum(bool(r.get('spends_retry')) for r in state.data['trials']) == spent
    assert state.progress()['infrastructure_errors'] == []


def test_real_twomoka_is_not_blocked_after_its_infrastructure_rounds(tmp_path, monkeypatch, cell):
    """The real ledger and outer checks of turns 0-2; close-infra runs the real runtime rule."""
    source, manifest = extract(tmp_path, 'twomoka_p6b_r1')
    task, state = moka_state(source, manifest)
    checks = [json.loads((source / f'outer_check-0{n}.json').read_text()) for n in range(3)]
    assert all(c['result']['progress']['infrastructure_errors'] == [manifest['attempt']] for c in checks)
    cell.ledger.write_bytes((task / 'development_state.json').read_bytes())
    closed = []
    driver = cell.driver
    real_main = driver.main

    def main(argv):
        campaign = sys.modules['native_world_campaign']
        fake = campaign.protocol

        def protocol_step(case, repo, env, folder, step, command, *args):
            if command == 'close-infra':
                row = runtime.close_attempt(state, args[1], args[3], MOKA_CASE)
                closed.append(row)
                return {'exit_code': 0, 'result': {'closed': row}}
            return fake(case, repo, env, folder, step, command, *args)
        campaign.protocol = protocol_step
        return real_main(argv)
    monkeypatch.setattr(driver, 'main', main)
    # The real check before the solver had no infrastructure row yet.
    before = dict(checks[0], result=dict(checks[0]['result'], progress=dict(
        checks[0]['result']['progress'], infrastructure_errors=[])))
    state_out, calls = cell([{'events': primary(manifest['session'], manifest['worker'])}, {}, {}, {'code': -2}],
                            [before, *checks])
    assert [r['directory'] for r in closed] == [manifest['attempt']]
    assert closed[0]['status'] == 'complete' and closed[0]['watchdog']['kind'] == 'persistence_cut'
    assert state_out['infrastructure_dispositions'][0]['disposition'] == runtime.SETTLED
    assert len(calls['native']) == 4  # the real run stopped after turn 2 here
    assert state_out['blocked']['phase'] == 'cancelled'  # the replay's own end, not 'infrastructure'


def test_close_infra_cli_settles_through_the_runtime_entry(h, monkeypatch, capsys):
    record = legacy_row(h, finished_policy=True, last_api=873.6)
    monkeypatch.setattr(runtime.NativeWorldState, 'progress', runtime.original_progress)
    monkeypatch.setattr(protocol, 'run_replay', protocol.run_replay)
    monkeypatch.setattr(sys, 'argv', ['native_world_protocol.py', '--case', str(h.case_path), 'close-infra',
                                      '--directory', record['directory'], '--reason', 'outer driver'])
    assert runtime.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out['closed']['recovery']['disposition'] == runtime.SETTLED and out['closed']['status'] == 'complete'
    assert out['progress']['infrastructure_errors'] == []
    assert [r['kind'] for r in out['progress']['settled_watchdog_attempts']] == ['persistence_cut']


def test_policy_finished_matches_the_real_replay_layout(tmp_path):
    source, manifest = extract(tmp_path, 'twomoka_p6b_r1')
    results = source / manifest['attempt'] / 'results'
    command = json.loads((source / manifest['attempt'] / 'command.json').read_text())['command']
    command[command.index('--args.output-dir') + 1] = str(results)
    assert trial_process.policy_finished(command)
    command[command.index('--args.output-dir') + 1] = str(tmp_path / 'empty')
    assert not trial_process.policy_finished(command)
