"""CC_TASK_9: worker recovery after an unresumable primary, and the B1/B2/B3 closeout repairs.

Groups:
  R. `worker_recovery.resume_evidence` reads only the structured native stream;
  G. the canonical-assignment hook admits generation N only in an open driver request;
  D. the p6b driver: recovery round, generations, B1 infrastructure rounds, B2
     solver-failure rounds, B3 terminal closeout (model and simulator faked as in
     test_p6_closeout.py);
  P. protocol: per-candidate infrastructure rule at end-development, reason terminal;
  X. replay of the three real r3 cells whose worker became unresumable.
"""
import contextlib
import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from test_p5_budget import BudgetHarness
import native_world_protocol as protocol
import native_world_fixloop_state as ledger

SIM = Path(__file__).resolve().parents[1]
P3 = SIM / 'docs/experiments/code-world-search-control-p3-20261013/support'
P6_PARALLEL = SIM / 'docs/experiments/code-world-budget-p5-20261019/parallel/support'
P6 = SIM / 'docs/experiments/code-world-repair-parallel-p6-20261020/support'
P6B = SIM / 'docs/experiments/code-world-worker-recovery-p6b-20261021/support'
FIXTURES = SIM / 'tests/fixtures/p6b_recovery'
UNRESUMABLE = 'Agent \\"{w}\\" could not be resumed: No transcript found for agent ID: {w}'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(P3))
recovery = load(P6B / 'worker_recovery.py', 'p6b_worker_recovery')


# ---- stream helpers ---------------------------------------------------------------

def init(session):
    return {'type': 'system', 'subtype': 'init', 'session_id': session}


def send(worker, number, ok, *, unresumable=True):
    message = (f'Agent "{worker}" was stopped (completed); resumed it in the background' if ok else
               UNRESUMABLE.format(w=worker).replace('\\"', '"') if unresumable else 'other failure')
    text = json.dumps({'success': ok, 'message': message})
    return [{'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': f'send-{number}', 'name': 'SendMessage',
                 'input': {'to': worker, 'message': 'continue'}}]}},
            {'type': 'user', 'timestamp': f'2026-10-20T10:00:{number:02d}Z', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': f'send-{number}',
                 'content': [{'type': 'text', 'text': text}]}]}}]


def subagent_event():
    return {'type': 'assistant', 'parent_tool_use_id': 'agent-call', 'message': {'content': []}}


def write(path, events):
    Path(path).write_text(''.join(json.dumps(e) + '\n' for e in events))


# ---- R: resume evidence ---------------------------------------------------------

@pytest.mark.parametrize('events,expected', [
    ([init('s'), *send('w1', 1, True), *send('w1', 2, False)], True),
    ([init('s'), *send('w1', 1, False), *send('w1', 2, False)], True),
    ([init('s'), *send('w1', 1, False), *send('w1', 2, True)], False),      # a later resume worked
    ([init('s'), *send('w1', 1, False), subagent_event()], False),           # something ran afterwards
    ([init('s'), *send('w1', 1, False, unresumable=False)], False),         # another failure kind
    ([init('s'), *send('w9', 1, False)], False),                             # another agent
    ([init('s')], False),
])
def test_resume_evidence_is_structural(tmp_path, events, expected):
    path = tmp_path / 'turn.jsonl'
    write(path, events)
    evidence = recovery.resume_evidence(path, 'w1')
    assert evidence['unresumable'] is expected


def test_resume_evidence_ignores_prose_and_malformed_lines(tmp_path):
    path = tmp_path / 'turn.jsonl'
    path.write_text('not json\n' + json.dumps({'type': 'assistant', 'message': {'content': [
        {'type': 'text', 'text': 'Agent "w1" could not be resumed'}]}}) + '\n')
    assert recovery.resume_evidence(path, 'w1')['unresumable'] is False


def test_handoff_carries_only_the_cells_own_state(tmp_path):
    task = tmp_path / 'task'
    (task / 'attempts').mkdir(parents=True)
    (task / 'development_state.json').write_text(json.dumps({'trials': [
        {'phase': 'snapshot', 'seed': 51, 'status': 'complete', 'directory': 'development/snapshot/seed_51',
         'spends_retry': False},
        {'phase': 'initial', 'seed': 51, 'attempt': 1, 'status': 'complete',
         'directory': 'development/initial/seed_51/attempt_1', 'spends_retry': True}]}))
    for name in ('findings.md', 'task_analysis.md', 'fix_code.py', 'attempts/mode_x_BLOCKED.md',
                 'attempts/notes.md', 'CHANGELOG.md'):
        (task / name).write_text(name)
    check = {'result': {'ready': False, 'decision_type': 'incomplete', 'errors': ['e'],
                        'development_budget': {'phase': 'A'}, 'progress': {'replay_invocations': 1}}}
    text, index = recovery.handoff(task_dir=task, check=check, generation=2, previous_worker='w1',
                                   remaining_seconds=3600, turn=4)
    assert 'generation 2' in text and '`w1`' in text and 'Do NOT run `init`' in text
    assert index['attempts_spent'] == 1 and index['ledger_rows'] == 2
    assert {r['relative'] for r in index['reports']} == {
        'findings.md', 'task_analysis.md', 'CHANGELOG.md', 'attempts/mode_x_BLOCKED.md', 'attempts/notes.md'}
    assert [p['relative'] for p in index['programs']] == ['fix_code.py']
    assert 'heldout' not in text.lower().replace('held-out', '')
    assert index['check']['development_budget'] == {'phase': 'A'}


# ---- G: generation-N dispatch hook -------------------------------------------------

@pytest.fixture
def hook(tmp_path):
    control = tmp_path / 'control'
    (control / 'native').mkdir(parents=True)
    assignment = control / 'worker-prompt.md'
    assignment.write_text('FROZEN ASSIGNMENT\n')
    case = tmp_path / 'case.json'
    case.write_text(json.dumps({'prediction_contract': 'p3-search', 'condition': 'C', 'control': str(control)}))
    state = control / 'native/assignment-state.json'
    audit = tmp_path / 'audit.jsonl'

    def call(payload):
        process = subprocess.run([sys.executable, str(P6B / 'canonical_assignment.py'), '--case', str(case),
                                  '--assignment', str(assignment), '--state', str(state), '--audit', str(audit)],
                                 input=json.dumps(payload), capture_output=True, text=True,
                                 env={'PYTHONPATH': str(P3), 'PATH': '/usr/bin:/bin'})
        return process.returncode, process.stdout, process.stderr

    def request(text=None, status='open'):
        generation = control / 'native/generation-02-assignment.md'
        generation.write_text(text if text is not None else 'FROZEN ASSIGNMENT\n\n## handoff\n')
        data = json.loads(state.read_text())
        data['recovery_request'] = {'status': status, 'generation': 2, 'assignment': str(generation),
                                    'assignment_sha256': hashlib.sha256(generation.read_text().encode()).hexdigest()}
        state.write_text(json.dumps(data))
        return generation

    call.request = request
    call.state = state
    call.audit = audit
    return call


def dispatch(tool_use_id, **extra):
    return {'tool_name': 'Agent', 'tool_use_id': tool_use_id, 'session_id': 's',
            'tool_input': {'subagent_type': 'general-purpose', 'prompt': 'ASPIRE_FROZEN_PRIMARY_ASSIGNMENT',
                           'run_in_background': True}, **extra}


def test_first_dispatch_unchanged_and_unsolicited_second_refused(hook):
    code, out, _ = hook(dispatch('t1'))
    assert code == 0 and json.loads(out)['hookSpecificOutput']['updatedInput']['prompt'] == 'FROZEN ASSIGNMENT\n'
    code, _, err = hook(dispatch('t2'))
    assert code == 2 and 'outer-driver recovery round' in err
    assert 'generations' not in json.loads(hook.state.read_text())


def test_generation_two_admitted_once_in_open_request(hook):
    hook(dispatch('t1'))
    generation = hook.request()
    code, out, _ = hook(dispatch('t2'))
    assert code == 0
    assert json.loads(out)['hookSpecificOutput']['updatedInput']['prompt'] == generation.read_text()
    data = json.loads(hook.state.read_text())
    assert data['recovery_request']['status'] == 'admitted'
    assert [g['tool_use_id'] for g in data['generations']] == ['t2']
    row = json.loads(hook.audit.read_text().splitlines()[-1])
    assert row['canonicalized_primary'] and row['recovery_generation'] == 2
    assert row['effective_input']['prompt'] == generation.read_text()
    code, _, err = hook(dispatch('t3'))  # the request is consumed
    assert code == 2 and 'recovery round' in err


@pytest.mark.parametrize('variant', ['subagent', 'tampered', 'not_prefixed', 'closed'])
def test_generation_dispatch_refusals(hook, variant):
    hook(dispatch('t1'))
    if variant == 'not_prefixed':
        hook.request('SOMETHING ELSE\n')
    elif variant == 'closed':
        hook.request(status='closed_without_dispatch')
    else:
        generation = hook.request()
        if variant == 'tampered':
            generation.write_text('FROZEN ASSIGNMENT\n\n## other text\n')
    payload = dispatch('t2', **({'parent_tool_use_id': 'p'} if variant == 'subagent' else {}))
    code, _, _ = hook(payload)
    assert code == 2
    assert not json.loads(hook.state.read_text()).get('generations')


# ---- D: driver ----------------------------------------------------------------------

def overlay(tmp_path):
    support = tmp_path / 'support'
    support.mkdir()
    for directory in (P3, P6_PARALLEL, P6, P6B):
        for source in directory.glob('*.py'):
            (support / source.name).unlink(missing_ok=True)
            (support / source.name).write_bytes(source.read_bytes())
    return support


def incomplete(phase, **progress):
    return {'exit_code': 1, 'result': {'ready': False, 'decision_type': 'incomplete', 'terminal_reasons': [],
        'errors': ['report missing'], 'progress': {'seeds_exhausted_without_graded_evidence': [],
                                                   'replay_invocations': 10, **progress},
        'development_budget': {'phase': phase}}}


def terminal(phase):
    check = incomplete(phase)
    check['result'].update(decision_type='terminal', terminal_reasons=[{'code': 'no_legal_action'}])
    return check


@pytest.fixture
def cell(tmp_path, monkeypatch):
    support = overlay(tmp_path)
    monkeypatch.syspath_prepend(str(support))
    for source in support.iterdir():
        monkeypatch.delitem(sys.modules, source.stem, raising=False)
    driver = load(support / 'run_two_task_cell.py', 'p6b_driver_under_test')
    repo, control = tmp_path / 'sim', tmp_path / 'control'
    repo.mkdir(); control.mkdir()
    task = repo / 'outputs/libero_fix_loop/synthetic/synthetic'
    task.mkdir(parents=True)
    ledger_path = task / 'development_state.json'
    ledger_path.write_text(json.dumps({'trials': [{'phase': 'snapshot', 'seed': 51, 'status': 'complete',
                                                   'directory': 'd0', 'spends_retry': False}]}))
    case = {'sim': str(repo), 'control': str(control), 'id': 'synthetic_C', 'condition': 'C',
            'suite': 'synthetic', 'task': 'synthetic', 'dev_seeds': list(range(51, 66)),
            'campaign_timeout': 36000, 'claude_config_dir': str(control / 'config'),
            'development_budget': 'aspire-v1', 'heldout_parallel': 4, 'trial_timeout': 900}
    case_path = tmp_path / 'case.json'
    case_path.write_text(json.dumps(case))
    calls = {'native': [], 'end': [], 'finalize': 0, 'freeze': 0}
    clock = [0.0]
    script = []

    def atomic(path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data))

    def prompts(*args):
        worker, coordinator = control / 'worker-prompt.md', control / 'coordinator-prompt.md'
        worker.write_text('synthetic assignment\n'); coordinator.write_text('synthetic coordinator\n')
        return worker, coordinator

    def append_row(status='complete', seed=51):
        data = json.loads(ledger_path.read_text())
        n = len(data['trials'])
        data['trials'].append({'phase': 'repair', 'seed': seed, 'attempt': n, 'status': status,
                               'directory': f'd{n}', 'spends_retry': True})
        ledger_path.write_text(json.dumps(data))

    def native(command, **kwargs):
        clock[0] += 600
        step = script.pop(0) if script else {}
        calls['native'].append({'resume': '--resume' in command,
                                'session': command[command.index('--resume') + 1] if '--resume' in command else None,
                                'prompt': command[command.index('-p') + 1]})
        for _ in range(step.get('rows', 0)):
            append_row()
        write(kwargs['stdout_path'], step.get('events', []))
        return step.get('code', 0)

    def identity(transcripts, assignment):
        for path in transcripts:
            for line in Path(path).read_text().splitlines():
                event = json.loads(line)
                if event.get('type') == 'fake_primary':
                    assert assignment.startswith('synthetic assignment\n')
                    calls.setdefault('identity_assignments', []).append(assignment)
                    return event['session'], event['worker']
        raise driver.Blocked('solver_lineage', 'ambiguous native worker lineage')

    def freeze(*args):
        calls['freeze'] += 1
        raise driver.Blocked('fixture_stop', 'test stops before any held-out evaluator')

    campaign = ModuleType('native_world_campaign')
    campaign.atomic_json = atomic
    campaign.verify_runtime = lambda *a: None
    campaign.native_settings = lambda *a: {'hooks': {'PreToolUse': []}}
    campaign.native_environment = lambda *a: {}
    campaign.perception_ready = lambda *a: {}
    campaign.probe = lambda *a: {}
    campaign.write_prompts = prompts
    campaign.native_command = lambda case, prompt, settings, **kw: ['claude', '-p', prompt]
    campaign.worker_agent = lambda *a: {}
    monkeypatch.setitem(sys.modules, 'native_world_campaign', campaign)
    importer = ModuleType('foundation_import')
    importer.import_diagnostic = lambda *a: {}
    monkeypatch.setitem(sys.modules, 'foundation_import', importer)
    monkeypatch.setattr(driver.full_deadlines, 'check_case', lambda *a: [])
    monkeypatch.setattr(driver, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(driver.scope, 'apply', lambda *a: None)
    monkeypatch.setattr(driver.output_ownership, 'verify', lambda *a: {})
    monkeypatch.setattr(driver.runtime_limits, 'install', lambda settings, case: settings)
    monkeypatch.setattr(driver, 'verify_local_provider', lambda *a: {})
    monkeypatch.setattr(driver, 'verify_capacity', lambda *a: {})
    monkeypatch.setattr(driver, 'bind_child_env', lambda env, case: env)
    monkeypatch.setattr(driver, 'fault', lambda *a: None)
    monkeypatch.setattr(driver.fixed_transport, 'run_native_cc', native)
    monkeypatch.setattr(driver, 'identity', identity)
    monkeypatch.setattr(driver, 'audit_assignment', lambda *a: {'ok': True})
    monkeypatch.setattr(driver, 'verify_received_assignment', lambda *a: {'ok': True, 'assignment': str(a[3])})
    monkeypatch.setattr(driver, 'heldout_manifest', freeze)

    def run(steps, checks, *, deadline=86400, end_code=0):
        script.extend(steps)
        results = iter(checks) if isinstance(checks, list) else None

        def protocol_step(case, repo, env, folder, step, command, *args):
            if command == 'check':
                return next(results) if results else checks(len(calls['native']))
            if command == 'end-development':
                calls['end'].append(args[1])
                return {'exit_code': end_code, 'result': {}}
            if command == 'finalize':
                calls['finalize'] += 1
            return {'exit_code': 0, 'result': {}}

        campaign.protocol = protocol_step
        assert driver.main(['--case', str(case_path), '--deadline-development', str(deadline)]) == 1
        return json.loads((control / 'campaign_state.json').read_text()), calls

    run.driver, run.control, run.ledger = driver, control, ledger_path
    return run


def primary(session, worker):
    return [init(session), {'type': 'fake_primary', 'session': session, 'worker': worker}]


def test_unresumable_worker_opens_one_recovery_round_with_a_new_generation(cell):
    steps = [{'events': [*primary('s1', 'w1'), *send('w1', 1, True), *send('w1', 2, False)], 'rows': 3},
             {'events': primary('s2', 'w2'), 'rows': 2},
             {'events': [init('s2'), *send('w2', 3, True)], 'code': -2}]
    before = json.loads(cell.ledger.read_text())['trials']
    state, calls = cell(steps, lambda n: incomplete('A'))
    recovery_turn, continuation = calls['native'][1], calls['native'][2]
    assert not recovery_turn['resume'] and 'WORKER RECOVERY ROUND (generation 2)' in recovery_turn['prompt']
    assert 'Do not SendMessage w1' in recovery_turn['prompt']
    assert continuation['resume'] and continuation['session'] == 's2' and 'worker w2' in continuation['prompt']
    generations = state['worker_generations']
    assert [(g['generation'], g['worker'], g['session']) for g in generations] == [(1, 'w1', 's1'), (2, 'w2', 's2')]
    assert generations[1]['previous_worker'] == 'w1' and generations[1]['detected_turn'] == 0
    assert generations[1]['reason'] == 'unresumable_worker' and generations[1]['status'] == 'dispatched'
    assert generations[1]['attempts_spent'] == 3 and generations[1]['ledger_rows'] == 4
    evidence = json.loads(Path(generations[1]['evidence']).read_text())
    assert evidence['unresumable'] and evidence['first_failure']['result_line'] == 6
    assignment = Path(generations[1]['assignment']).read_text()
    assert assignment.startswith('synthetic assignment\n') and 'recovery handoff (generation 2)' in assignment
    assert calls['identity_assignments'][-1] == assignment
    guard = json.loads((cell.control / 'native/assignment-state.json').read_text())
    assert guard['recovery_request']['status'] == 'closed_without_dispatch'  # fake hook never admitted
    trials = json.loads(cell.ledger.read_text())['trials']
    assert trials[:len(before)] == before and len(trials) == len(before) + 5  # nothing rewritten
    assert state['blocked']['phase'] == 'cancelled'
    assert calls['end'] == [] and calls['finalize'] == calls['freeze'] == 0


def test_recovery_round_without_dispatch_is_retried_then_bounded(cell):
    steps = [{'events': [*primary('s1', 'w1'), *send('w1', 1, False)]},
             {'events': [init('sx')]}, {'events': [init('sy')]}, {'events': [init('sz')]}]
    state, calls = cell(steps, lambda n: incomplete('A'))
    assert [c['resume'] for c in calls['native']] == [False, False, False]
    assert all('generation 2' in c['prompt'] for c in calls['native'][1:])
    assert state['blocked']['phase'] == 'solver'
    assert len(state['worker_generations'][1]['dispatch_rounds']) == 2


def test_phase_a_stall_with_unresumable_worker_takes_recovery_not_diagnostic(cell):
    steps = [{'events': [*primary('s1', 'w1')]}, {'events': [init('s1')]},
             {'events': [init('s1'), *send('w1', 1, False)]},
             {'events': primary('s2', 'w2'), 'rows': 1}, {'code': -2}]
    state, calls = cell(steps, lambda n: incomplete('A'))
    assert 'WORKER RECOVERY ROUND' in calls['native'][3]['prompt']
    assert 'NEW DIAGNOSTIC/STRATEGY ROUND' not in calls['native'][3]['prompt']
    assert not state.get('diagnostic_rounds')


@pytest.mark.parametrize('phase', ['A', 'B'])
def test_solver_failures_continue_then_stop_after_three_unchanged(cell, phase):
    steps = [{'events': primary('s1', 'w1'), 'code': 7}, {'code': 7}, {'code': 7}]
    state, calls = cell(steps, lambda n: incomplete(phase), end_code=2 if phase == 'A' else 0)
    assert len(calls['native']) == 3
    assert [c['resume'] for c in calls['native']] == [False, True, True]
    assert 'native solver failure (exit 7' in calls['native'][1]['prompt']
    if phase == 'A':
        assert state['blocked']['phase'] == 'solver' and calls['end'] == []
    else:
        assert calls['end'] == ['solver_exit'] and calls['finalize'] == 1


def test_solver_failure_with_ledger_progress_does_not_count(cell):
    steps = [{'events': primary('s1', 'w1'), 'code': 7}, {'code': 7, 'rows': 1}, {'code': 7},
             {'code': 7}, {'code': 7}]
    state, calls = cell(steps, lambda n: incomplete('A'))
    assert len(calls['native']) == 5
    assert [r['consecutive_without_ledger_change'] for r in state['failed_rounds']] == [1, 0, 1, 2, 3]
    assert state['blocked']['phase'] == 'solver'


def test_first_turn_failure_before_any_dispatch_starts_a_fresh_round(cell):
    steps = [{'events': [init('s0')], 'code': 1}, {'events': primary('s1', 'w1'), 'rows': 1}, {'code': -15}]
    state, calls = cell(steps, lambda n: incomplete('A'))
    assert [c['resume'] for c in calls['native']] == [False, False, True]
    assert state['worker_generations'][0]['worker'] == 'w1'


def test_structured_error_is_a_solver_failure_round_not_a_stop(cell):
    error = {'type': 'result', 'is_error': True, 'subtype': 'error_during_execution'}
    steps = [{'events': [*primary('s1', 'w1'), error]}, {'events': [init('s1')], 'rows': 1}, {'code': -2}]
    state, calls = cell(steps, lambda n: incomplete('A'))
    assert len(calls['native']) == 3 and state['failed_rounds'][0]['exit_code'] == 1


def test_infrastructure_rows_open_rounds_then_stop_when_unchanged(cell):
    infra = incomplete('A', infrastructure_errors=['development/repair/seed_51/attempt_4'])
    state, calls = cell([{'events': primary('s1', 'w1')}, {}, {}, {}], [incomplete('A')] + [infra] * 3)
    assert len(calls['native']) == 3
    assert all('INFRASTRUCTURE RECOVERY ROUND' in c['prompt'] for c in calls['native'][1:])
    assert 'close-infra' in calls['native'][1]['prompt']
    assert state['blocked']['phase'] == 'infrastructure'
    assert [r['same_evidence_rounds'] for r in state['infrastructure_rounds']] == [1, 2, 3]


def test_infrastructure_rows_resolved_by_worker_continue_normally(cell):
    infra = incomplete('A', infrastructure_errors=['development/repair/seed_51/attempt_4'])
    state, calls = cell([{'events': primary('s1', 'w1')}, {'rows': 1}, {'code': -2}],
                        [incomplete('A'), infra, incomplete('A')])
    assert 'INFRASTRUCTURE RECOVERY ROUND' not in calls['native'][2]['prompt']
    assert state['blocked']['phase'] == 'cancelled'


def test_phase_b_persistent_infrastructure_settles_through_end_development(cell):
    infra = incomplete('B', infrastructure_errors=['development/sweep/seed_52/attempt_6'])
    state, calls = cell([{'events': primary('s1', 'w1')}, {}, {}], [incomplete('B')] + [infra] * 3)
    assert calls['end'] == ['no_progress'] and calls['finalize'] == calls['freeze'] == 1


def test_pre_development_infrastructure_still_blocks(cell):
    infra = incomplete('A', infrastructure_errors=['x'])
    state, calls = cell([], [infra])
    assert state['blocked']['phase'] == 'infrastructure' and calls['native'] == []


def test_phase_b_terminal_is_settled_by_end_development(cell):
    state, calls = cell([{'events': primary('s1', 'w1')}], [incomplete('B'), terminal('B')])
    assert calls['end'] == ['terminal'] and calls['finalize'] == calls['freeze'] == 1


def test_phase_b_terminal_blocks_when_end_development_fails(cell):
    state, calls = cell([{'events': primary('s1', 'w1')}], [incomplete('B'), terminal('B')], end_code=2)
    assert calls['end'] == ['terminal'] and calls['finalize'] == 0
    assert state['blocked']['phase'] == 'development'
    assert state['blocked']['detail']['closeout']['detail']['reason_code'] == 'terminal'


def test_phase_a_terminal_still_blocks_without_closeout(cell):
    state, calls = cell([{'events': primary('s1', 'w1')}], [incomplete('A'), terminal('A')])
    assert calls['end'] == [] and state['blocked']['phase'] == 'development'


@pytest.mark.parametrize('code', [-2, -15, 130, 143])
def test_cancellation_still_stops(cell, code):
    state, calls = cell([{'events': primary('s1', 'w1'), 'code': code}], [incomplete('B')] * 2)
    assert state['blocked']['phase'] == 'cancelled' and calls['end'] == []


def test_require_development_possible_transitions(tmp_path, monkeypatch):
    support = overlay(tmp_path)
    monkeypatch.syspath_prepend(str(support))
    for source in support.iterdir():
        monkeypatch.delitem(sys.modules, source.stem, raising=False)
    driver = load(support / 'run_two_task_cell.py', 'p6b_driver_rules')
    infra = incomplete('A', unresolved_screening_blockers=[{'reason': 'service'}])
    assert driver.require_development_possible(infra, []) == 'infrastructure_round'
    with pytest.raises(driver.Blocked):
        driver.require_development_possible(infra)
    for phase, closeout in (('B', True), ('A', False)):
        with pytest.raises(driver.Blocked) as exc:
            driver.require_development_possible(terminal(phase), [])
        assert exc.value.payload['detail']['closeout'] is closeout


# ---- P: protocol end-development ------------------------------------------------------

@pytest.fixture
def h():
    with contextlib.ExitStack() as stack:
        harness = BudgetHarness(stack)
        stack.enter_context(patch.object(protocol, 'world_use', lambda case: None))
        yield harness


def test_other_candidates_unresolved_infrastructure_does_not_block_closeout(h):
    code = h.to_phase_b((51, 52, 53))
    h.sweep(code)
    h.version('other.py', 'other_mode', [54], passing=(51,), sweep=False)
    h.reports()
    row = next(r for r in reversed(h.state.data['trials']) if r['bundle_sha256'] == h.digest('other.py'))
    row.update(status='infrastructure_error', error='TimeoutExpired: watchdog 900+60 s')
    h.state.save()
    before = json.dumps(h.state.data['trials'], sort_keys=True)
    result = protocol.end_development(h.case, h.repo, h.state, 'terminal')
    assert result['check']['ready']
    assert [i['directory'] for i in h.state.data['development_end']['unresolved_not_blocking']] == [row['directory']]
    assert any(a['code'] == 'infrastructure_errors' for a in result['check']['advisories'])
    assert json.dumps(h.state.data['trials'], sort_keys=True) == before


def test_selected_candidates_own_unresolved_infrastructure_still_blocks(h):
    code = h.to_phase_b((51, 52, 53))
    h.sweep(code)
    h.reports()
    h.state.data['trials'].append(dict(h.state.data['trials'][-1], status='infrastructure_error',
                                       directory='development/sweep/seed_60/attempt_9'))
    h.state.save()
    with pytest.raises(ledger.ProtocolError, match='best complete candidate'):
        protocol.end_development(h.case, h.repo, h.state, 'deadline')
    assert not h.state.data.get('development_end')


def test_end_development_reason_set():
    assert protocol.DEVELOPMENT_END_REASONS == ('deadline', 'no_progress', 'solver_exit', 'terminal')


# ---- X: replay of the real r3 failures ---------------------------------------------------

@pytest.mark.parametrize('cell_name', ['goaltaskbowldrawer_p6_r1', 'bowldrawerclose_p6_r1', 'drawer_p6_r2'])
def test_real_r3_unresumable_cells_enter_recovery_at_the_first_failed_turn(tmp_path, monkeypatch, cell, cell_name):
    """The real turn-00 stream and lineage audit of each r3 cell, through the real lineage code."""
    manifest = json.loads((FIXTURES / 'manifest.json').read_text())[cell_name]
    archive = FIXTURES / f'{cell_name}.tgz'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['archive_sha256']
    source = tmp_path / 'r3'
    with tarfile.open(archive) as tar:
        tar.extractall(source, filter='data')
    for name, entry in manifest['files'].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == entry['sha256']
    driver = cell.driver
    import lineage
    monkeypatch.setattr(driver, 'identity', lambda transcripts, assignment: (
        lambda audit: (audit['sessions'][0]['session_id'], audit['primary_worker'])
    )(lineage.audit_assignment(transcripts, assignment)))
    (cell.control / 'worker-prompt.md').write_text((source / 'worker-prompt.md').read_text())
    monkeypatch.setattr(sys.modules['native_world_campaign'], 'write_prompts', lambda *a: (
        cell.control / 'worker-prompt.md', cell.control / 'coordinator-prompt.md'))
    (cell.control / 'coordinator-prompt.md').write_text('real coordinator prompt (not replayed)\n')
    cell.ledger.write_bytes((source / 'development_state.json').read_bytes())
    real = json.loads((source / 'outer_check-00.json').read_text())

    def replay(command, **kwargs):
        out = Path(kwargs['stdout_path'])
        if out.name == 'turn-00.stdout.jsonl':
            out.write_bytes((source / 'turn-00.stdout.jsonl').read_bytes())
            (out.parent / 'assignment-audit.jsonl').write_bytes((source / 'assignment-audit.jsonl').read_bytes())
            return 0
        out.write_text('')
        return -2  # end the replay after the recovery round's prompt is written
    monkeypatch.setattr(driver.fixed_transport, 'run_native_cc', replay)
    state, calls = cell([], [real, real])
    generations = state['worker_generations']
    assert generations[0]['worker'] == manifest['worker']
    assert generations[1]['detected_turn'] == 0 and generations[1]['previous_worker'] == manifest['worker']
    evidence = json.loads(Path(generations[1]['evidence']).read_text())
    assert evidence['unresumable'] and evidence['subagent_events_after_failure'] == 0
    assert not evidence['running_rows']
    prompt = next(cell.control.glob('campaign-*/turn-01-prompt.md')).read_text()
    assert 'OUTER-DRIVER WORKER RECOVERY ROUND (generation 2)' in prompt
    guard = json.loads((cell.control / 'native/assignment-state.json').read_text())
    assert guard['recovery_request']['generation'] == 2
    assignment = Path(generations[1]['assignment']).read_text()
    assert assignment.startswith((source / 'worker-prompt.md').read_text())
    rows = json.loads((source / 'development_state.json').read_text())['trials']
    assert generations[1]['attempts_spent'] == sum(bool(r.get('spends_retry')) for r in rows)
    assert cell.ledger.read_bytes() == (source / 'development_state.json').read_bytes()
    assert state['blocked']['phase'] == 'cancelled'
