"""Task 8: stopped development selects complete evidence; phase A keeps its clock."""
import contextlib
import fcntl
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from test_p5_budget import BudgetHarness, SEEDS
import native_world_protocol as protocol
import native_world_fixloop_state as ledger

SIM = Path(__file__).resolve().parents[1]
P6 = SIM / 'docs/experiments/code-world-budget-p5-20261019/parallel/support'
P3 = SIM / 'docs/experiments/code-world-search-control-p3-20261013/support'

@pytest.fixture
def h():
    with contextlib.ExitStack() as stack:
        harness = BudgetHarness(stack)
        stack.enter_context(patch.object(protocol, 'world_use', lambda case: None))
        yield harness


def end(h, reason='deadline', receipt=None):
    return protocol.end_development(h.case, h.repo, h.state, reason, receipt)


@pytest.mark.parametrize('reason', ['deadline', 'no_progress', 'solver_exit'])
def test_complete_candidate_at_stop_with_findings_advisory(h, reason):
    code = h.to_phase_b((51, 52, 53))
    h.sweep(code)
    h.reports()
    (h.task_dir / 'findings.md').write_text('Honest remaining failures; headings absent.\n')
    before = json.dumps(h.state.data['trials'], sort_keys=True)
    result = end(h, reason)
    assert result['check']['ready']
    assert result['check']['advisories'][0]['code'] == 'findings_format'
    assert h.state.data['selected']['bundle_sha256'] == h.digest('fix0.py')
    assert json.dumps(h.state.data['trials'], sort_keys=True) == before
    assert len(h.state.final_coverage(protocol.selected_bundle(h.case, h.state))) == 15
    assert h.state.selection_errors(protocol.selected_bundle(h.case, h.state), code.read_text()) == []
    with pytest.raises(ledger.ProtocolError, match='development has ended'):
        h.trial('repair', 51, code)


def test_incomplete_latest_does_not_disqualify_complete_best(h):
    code = h.to_phase_b((51, 52))
    h.sweep(code)
    h.reports()
    h.version('later.py', 'unfixed', [53], passing=(), sweep=False)
    end(h)
    assert h.state.data['selected']['bundle_sha256'] == h.digest('fix0.py')
    assert h.state.budget_status()['incomplete_versions']  # honest, not erased


def test_ranking_then_retains_solver_simplicity_choice(h):
    code = h.to_phase_b((51,))
    h.sweep(code)
    v1, _ = h.version('v1.py', 'one', [52], passing=(51, 52))
    v2, _ = h.version('v2.py', 'two', [53], passing=(51, 52))
    h.select_on_disk('v2.py')
    h.state.select(protocol.selected_bundle(h.case, h.state), 'simpler observation-driven behavior')
    h.reports()
    end(h)
    assert h.state.data['selected']['bundle_sha256'] == h.digest('v2.py')
    assert 'retained solver' in h.state.data['development_end']['tie_break']


def test_fewer_crashes_breaks_pass_tie(h):
    code = h.to_phase_b((51,))
    h.sweep(code)
    h.version('crash.py', 'one', [52], passing=(51, 52), crashing=(53,))
    h.version('safe.py', 'two', [53], passing=(51, 52))
    h.reports()
    end(h)
    assert h.state.data['selected']['bundle_sha256'] == h.digest('safe.py')


def test_all_fail_complete_version_allowed_only_after_first_success(h):
    code = h.to_phase_b((51,))
    h.sweep(code)
    h.version('all_fail.py', 'one', [52], passing=())
    # Legacy/interrupted-history shape: v0 has first success but lacks one seed.
    h.state.data['trials'] = [r for r in h.state.data['trials']
                             if not (r['bundle_sha256'] == h.digest('fix0.py') and r['seed'] == 65)]
    h.state.save()
    h.reports()
    end(h)
    assert h.state.data['development_end']['best_complete']['score'] == [0, 0]
    assert h.state.budget_phase() == 'B'


@pytest.mark.parametrize('phase_b', [False, True])
def test_no_complete_candidate_and_no_first_pass_refused(h, phase_b):
    if phase_b:
        h.to_phase_b()
    else:
        h.trial('snapshot', 51)
        h.trial('initial', 51)
    before = h.state.path.read_bytes()
    with pytest.raises(ledger.ProtocolError, match='phase B|no complete'):
        end(h)
    assert h.state.path.read_bytes() == before


@pytest.mark.parametrize('fault', ['running', 'infra', 'snapshot', 'policy', 'report'])
def test_substantive_faults_not_waived(h, fault):
    code = h.to_phase_b()
    h.sweep(code)
    h.reports()
    if fault == 'running':
        h.state.data['trials'].append(dict(h.state.data['trials'][-1], status='running'))
    elif fault == 'infra':
        h.state.data['trials'].append(dict(h.state.data['trials'][-1], status='infrastructure_error'))
    elif fault == 'snapshot':
        h.state.data['trials'][0]['status'] = 'infrastructure_error'
    elif fault == 'policy':
        row = h.state.final_coverage(h.state.candidates()[h.digest('fix0.py')]['bundle'])[0]
        (h.task_dir / row['directory'] / 'code.py').write_text('get_observation()\n# changed')
    else:
        (h.task_dir / 'task_analysis.md').unlink()
    h.state.save()
    with pytest.raises(ledger.ProtocolError):
        end(h)
    assert not h.state.data.get('development_end')


def test_batch_lock_refuses_even_if_complete_and_stale_reader(h):
    code = h.to_phase_b()
    h.sweep(code)
    h.reports()
    with (h.task_dir / '.trial.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ledger.ProtocolError, match='still active'):
            end(h)


def driver():
    # The production overlay falls through to its unchanged p3 support modules.
    sys.path.insert(0, str(P3))
    sys.path.insert(0, str(P6))
    for name in ('full_deadlines', 'runtime_limits'):
        sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location('task8_driver', P6 / 'run_two_task_cell.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_phase_a_no_progress_is_new_diagnostic_round_not_terminal():
    module = driver()
    result = {'result': {'ready': False, 'decision_type': 'incomplete',
        'progress': {'seeds_exhausted_without_graded_evidence': []},
        'development_budget': {'phase': 'A'}}}
    history = []
    assert module.require_development_possible(result, history) is None
    assert module.require_development_possible(result, history) is None
    assert module.require_development_possible(result, history) == 'diagnostic_round'
    assert len(history) == 3
    result['result']['decision_type'] = 'terminal'
    result['result']['terminal_reasons'] = [{'code': 'real_fault'}]
    with pytest.raises(module.Blocked):
        module.require_development_possible(result, history)


def test_phase_b_no_progress_remains_explicit_closeout_trigger():
    module = driver()
    result = {'result': {'ready': False, 'progress': {'seeds_exhausted_without_graded_evidence': []},
                         'development_budget': {'phase': 'B'}}}
    with pytest.raises(module.Blocked) as exc:
        history = []
        for _ in range(3):
            module.require_development_possible(result, history)
    assert exc.value.payload['detail']['reason_code'] == 'no_progress'


from types import ModuleType, SimpleNamespace
@pytest.fixture(params=["task8", "p6"], ids=["task8", "p6-overlay"])
def run_closeout_driver(tmp_path, monkeypatch, request):
    support = tmp_path / "support"
    support.mkdir()
    for source in P3.iterdir():
        if source.suffix == ".py":
            (support / source.name).symlink_to(source)
    for source in P6.iterdir():  # the p4 overlay, as the p4n stager freezes it
        if source.suffix == ".py":
            (support / source.name).unlink(missing_ok=True)
            (support / source.name).write_bytes(source.read_bytes())
    if request.param == "p6":
        overlay = SIM / 'docs/experiments/code-world-repair-parallel-p6-20261020/support'
        for source in overlay.glob('*.py'):
            (support / source.name).unlink(missing_ok=True)
            (support / source.name).write_bytes(source.read_bytes())
    monkeypatch.syspath_prepend(str(support))
    # Other studies' support modules of the same names may already be imported.
    for source in support.iterdir():
        monkeypatch.delitem(sys.modules, source.stem, raising=False)
    spec = importlib.util.spec_from_file_location("p4_terminal_driver", support / "run_two_task_cell.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    repo, control = tmp_path / "sim", tmp_path / "control"
    repo.mkdir(); control.mkdir()
    case = {"sim": str(repo), "control": str(control), "id": "synthetic_C",
            "condition": "C", "suite": "synthetic", "task": "synthetic",
            "dev_seeds": list(range(51, 66)), "campaign_timeout": 36000,
            "claude_config_dir": str(control / "config")}
    case.update(development_budget="aspire-v1", heldout_parallel=4, trial_timeout=900)
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    calls = {"native": 0, "finalize": 0, "freeze": 0, "end": 0}

    def atomic(path, data):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data))

    def prompts(*args):
        worker, coordinator = control / "worker-prompt.md", control / "coordinator-prompt.md"
        worker.write_text("synthetic assignment"); coordinator.write_text("synthetic coordinator")
        return worker, coordinator

    clock = [0.0]
    codes = []
    prompts_seen = []
    def native(command, **kwargs):
        calls["native"] += 1
        clock[0] += 3600
        code = codes.pop(0) if codes else 0
        if code == 'owned_timeout':
            import subprocess
            with Path(kwargs['stdout_path']).open('w') as out:
                command = [sys.executable, '-c',
                    'import subprocess,sys,time,json; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True); print(json.dumps({"child_pid":p.pid}),flush=True); time.sleep(60)']
                process = subprocess.Popen(command, stdout=out, start_new_session=True)
                kwargs['on_start'](process)
                # A genuine Popen.wait expiry, not a normal exit/fake clock only.
                process.wait(timeout=.3)
        if code == 'owned_child':
            import subprocess
            with Path(kwargs['stdout_path']).open('w') as out:
                command = [sys.executable, '-c',
                    'import subprocess,sys,time,json; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],start_new_session=True); print(json.dumps({"child_pid":p.pid}),flush=True); time.sleep(.3); sys.exit(7)']
                process = subprocess.Popen(command, stdout=out, start_new_session=True)
                kwargs['on_start'](process)
                return process.wait(timeout=10)
        if code == 'structured_error':
            Path(kwargs['stdout_path']).write_text(json.dumps({'type': 'result', 'is_error': True, 'subtype': 'error_during_execution'}))
            return 0
        return code

    def freeze(*args):
        calls["freeze"] += 1
        raise driver.Blocked("fixture_stop", "test stops before any held-out evaluator")

    campaign = ModuleType("native_world_campaign")
    campaign.atomic_json = atomic
    campaign.verify_runtime = lambda *a: None
    campaign.native_settings = lambda *a: {"hooks": {"PreToolUse": []}}
    campaign.native_environment = lambda *a: {}
    campaign.perception_ready = lambda *a: {}
    campaign.probe = lambda *a: {}
    campaign.write_prompts = prompts
    campaign.native_command = lambda *a, **kw: ["synthetic-never-executed"]
    campaign.worker_agent = lambda *a: {}
    monkeypatch.setitem(sys.modules, "native_world_campaign", campaign)
    importer = ModuleType("foundation_import")
    importer.import_diagnostic = lambda *a: {}
    monkeypatch.setitem(sys.modules, "foundation_import", importer)
    monkeypatch.setattr(driver.full_deadlines, "check_case", lambda *a: [])
    monkeypatch.setattr(driver, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(driver.scope, "apply", lambda *a: None)
    monkeypatch.setattr(driver.output_ownership, "verify", lambda *a: {})
    monkeypatch.setattr(driver.runtime_limits, "install", lambda settings, case: settings)
    monkeypatch.setattr(driver, "verify_local_provider", lambda *a: {})
    monkeypatch.setattr(driver, "verify_capacity", lambda *a: {})
    monkeypatch.setattr(driver, "bind_child_env", lambda env, case: env)
    monkeypatch.setattr(driver, "fault", lambda *a: None)
    monkeypatch.setattr(driver.fixed_transport, "run_native_cc", native)
    monkeypatch.setattr(driver, "identity", lambda *a: ("same-session", "same-worker"))
    monkeypatch.setattr(driver, "audit_assignment", lambda *a: {"ok": True})
    monkeypatch.setattr(driver, "verify_received_assignment", lambda *a: {"ok": True})
    monkeypatch.setattr(driver, "heldout_manifest", freeze)

    def run(checks, *, exits=(), deadline=86400, end_code=0):
        codes.extend(exits)
        results = iter(checks)

        def protocol(case, repo, env, folder, step, command, *args):
            if command == "check":
                return next(results)
            if command == "end-development":
                calls["end"] += 1
                calls["end_reason"] = args[1]
                return {"exit_code": end_code, "result": {}}
            if command == "finalize":
                calls["finalize"] += 1
            return {"exit_code": 0, "result": {}}

        campaign.protocol = protocol
        assert driver.main(["--case", str(case_path), "--deadline-development", str(deadline)]) == 1
        return json.loads((control / "campaign_state.json").read_text()), calls

    run.driver = driver
    run.control = control
    return run



def incomplete(phase):
    return {'exit_code': 1, 'result': {'ready': False, 'decision_type': 'incomplete',
        'errors': ['report missing'], 'progress': {'seeds_exhausted_without_graded_evidence': [],
                                                'replay_invocations': 10},
        'development_budget': {'phase': phase}}}


def test_phase_a_continues_beyond_stall_to_original_cumulative_deadline(run_closeout_driver):
    run = run_closeout_driver
    state, calls = run([incomplete('A')] * 25, end_code=2)
    assert calls['native'] == 24  # 24 one-hour turns, not a reset per diagnostic round
    assert len(state['diagnostic_rounds']) == 8
    assert calls['end_reason'] == 'deadline'
    assert calls['finalize'] == calls['freeze'] == 0
    assert 'NEW DIAGNOSTIC/STRATEGY ROUND' in next(run.control.glob('campaign-*/turn-03-prompt.md')).read_text()


@pytest.mark.parametrize('trigger', ['deadline', 'no_progress', 'solver_exit'])
def test_outer_routes_closeout_into_existing_finalize_and_freeze(run_closeout_driver, trigger):
    run = run_closeout_driver
    state, calls = run([incomplete('B')] * 10,
        exits=[7] if trigger == 'solver_exit' else [], deadline=3600 if trigger == 'deadline' else 86400)
    assert calls['end_reason'] == trigger
    assert calls['end'] == calls['finalize'] == calls['freeze'] == 1
    assert state['blocked']['phase'] == 'fixture_stop'


@pytest.mark.parametrize('code', [-2, -15, 130, 143])
def test_cancel_never_finalizes_or_launches_heldout(run_closeout_driver, code):
    state, calls = run_closeout_driver([incomplete('B')] * 2, exits=[code])
    assert state['blocked']['phase'] == 'cancelled'
    assert calls['end'] == calls['finalize'] == calls['freeze'] == 0


def test_phase_a_solver_failure_stops_not_strategy_retry(run_closeout_driver):
    state, calls = run_closeout_driver([incomplete('A')] * 2, exits=[7], end_code=2)
    assert calls['native'] == 1
    assert calls['finalize'] == calls['freeze'] == 0


@pytest.mark.parametrize('cell,phase,passes', [
    ('goaltaskbowldrawer_p5_r1', 'B', 13), ('bowldrawer_p5_r1', 'B', 8),
    ('objswapsoup_p5_r1', 'B', 4), ('drawer_p5_r1', 'A', None), ('twomoka_p5_r1', 'A', None)])
def test_original_p5_ledgers_have_reported_coverage_and_preserve_failure_boundaries(tmp_path, monkeypatch, cell, phase, passes):
    import tarfile, hashlib
    fixtures = SIM / 'tests/fixtures/p6_closeout'
    manifest = json.loads((fixtures / 'manifest.json').read_text())[cell]
    archive = fixtures / (cell + '.tgz')
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == manifest['fixture_sha256']
    with tarfile.open(archive) as tar:
        tar.extractall(tmp_path, filter='data')
    task = tmp_path / 'task'
    original = (task / 'development_state.json').read_bytes()
    assert hashlib.sha256(original).hexdigest() == manifest['ledger_sha256']
    data = json.loads(original)
    state = ledger.NativeWorldState(task, data['identity'], resume=True)
    assert state.budget_phase() == phase
    best = state.best_complete()
    assert (best['score'][0] if best else None) == passes
    assert sum(bool(r.get('spends_retry')) for r in state.data['trials']) == manifest['attempts_spent']
    case = json.loads((tmp_path / 'case.json').read_text())
    repo = tmp_path / 'sim';repo.mkdir()
    monkeypatch.setattr(protocol, 'verify_runtime', lambda *a: None)  # offline replay, never claimed a runtime smoke
    monkeypatch.setattr(protocol, 'world_use', lambda *a: None)  # reporting-only, traces deliberately excluded
    if cell in ('goaltaskbowldrawer_p5_r1', 'objswapsoup_p5_r1'):
        # CC_TASK_9 B1: objswapsoup's unresolved infrastructure rows all belong to
        # another candidate (79f92657), so its complete 4/15 candidate is frozen
        # and the rows are reported, untouched, under unresolved_not_blocking.
        result = protocol.end_development(case, repo, state, 'no_progress')
        assert result['check']['ready']
        assert state.data['selected']['bundle_sha256'] in best['bundle_sha256']
        assert state.data['trials'] == data['trials']
        tolerated = state.data['development_end']['unresolved_not_blocking']
        assert {i['directory'] for i in tolerated if i['kind'] == 'trial'} == set(manifest['infrastructure_errors'])
        assert all(i['bundle_sha256'] not in best['bundle_sha256'] for i in tolerated)
    else:
        # Original unresolved infra/running rows are NOT laundered into coverage.
        with pytest.raises(ledger.ProtocolError):
            protocol.end_development(case, repo, state, 'deadline')
        assert (task / 'development_state.json').read_bytes() == original


@pytest.mark.parametrize('live,valid_receipt', [(False, True), (True, True), (False, False)])
def test_deadline_interrupt_reconciliation_requires_verified_owned_cleanup(h, live, valid_receipt):
    import os, socket, subprocess
    code = h.to_phase_b()
    h.sweep(code)
    h.reports()
    # A newly admitted partial version retains its spent attempt when interrupted.
    later = h.policy('unfinished.py', salt='unfinished')
    bundle, sources = protocol.collect_bundle(h.case, h.task_dir, 'repair', later,
                                               h.task_dir / 'initial_world_program.py', None)
    row = h.state.begin_trial('repair', 52, bundle, sources, {'mode': 'unfinished', 'target_seeds': [52]})
    row_before = dict(row)
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)' if live else 'pass'])
    try:
        if not live:
            process.wait(timeout=10)
        life = {'host': socket.gethostname(), 'pid': process.pid}
        (h.task_dir / row['directory'] / 'lifecycle.json').write_text(json.dumps(life))
        receipt = h.task_dir / 'cleanup.json'
        receipt.write_text(json.dumps({'verified_no_live_work': valid_receipt, 'tracked_pids': [process.pid]}))
        if live or not valid_receipt:
            with pytest.raises(ledger.ProtocolError):
                end(h, receipt=receipt)
            assert h.state.data['trials'][-1]['status'] == 'running'
        else:
            end(h, receipt=receipt)
            interrupted = h.state.data['trials'][-1]
            assert interrupted['status'] == 'development_interrupted'
            assert interrupted['spends_retry']
            assert interrupted['interrupted_record'] == row_before
            assert h.state.covered_seeds(ledger.bundle_identity(bundle)) == []
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=10)


def test_closeout_still_enforces_provenance_and_writes_honest_final_result(h):
    h.sweep(h.to_phase_b((51, 52)))
    h.reports()
    (h.task_dir / 'findings.md').write_text('Observed two development passes.\n')
    end(h, 'solver_exit')
    with pytest.raises(ledger.ProtocolError, match='provenance'):
        protocol.finalize(h.case, h.repo, h.state, [])
    assert not h.state.data['stage1_complete']
    transcript = h.task_dir / 'synthetic-provenance.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'message': {'model': h.case['expected_served_model']}})
        + '\n' + json.dumps({'type': 'result', 'modelUsage': {'m': {
            'contextWindow': h.case['context_tokens'], 'maxOutputTokens': h.case['max_output_tokens']}}}) + '\n')
    result = protocol.finalize(h.case, h.repo, h.state, [transcript])
    assert result['stage1_complete']
    assert result['development_budget']['freeze_conditions']['development_ended']
    assert len(result['final_development_coverage']) == 15
    assert sum(bool(r.get('task_completed')) for r in result['final_development_coverage']) == 2
    assert result['advisories'][0]['code'] == 'findings_format'


def test_structured_api_failure_with_zero_exit_is_not_retried_as_stagnation(run_closeout_driver):
    state, calls = run_closeout_driver([incomplete('A')] * 2, exits=['structured_error'], end_code=2)
    assert calls['native'] == 1
    assert state['solver_reported_error']['subtype'] == 'error_during_execution'
    assert calls['finalize'] == calls['freeze'] == 0


def test_real_solver_descendant_cleanup_finishes_before_selection(run_closeout_driver):
    import psutil
    run = run_closeout_driver
    state, calls = run([incomplete('B')] * 2, exits=['owned_child'])
    receipt = json.loads(next(run.control.glob('campaign-*/turn-00-cleanup.json')).read_text())
    transcript = json.loads(next(run.control.glob('campaign-*/turn-00.stdout.jsonl')).read_text())
    pid = transcript['child_pid']
    assert receipt['verified_no_live_work']
    assert pid in receipt['tracked_pids']
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert calls['end_reason'] == 'solver_exit'
    assert calls['end'] == calls['finalize'] == calls['freeze'] == 1


@pytest.mark.parametrize('field', ['infrastructure_errors', 'unresolved_screening_blockers'])
def test_phase_a_actual_infrastructure_fault_is_not_a_new_diagnostic_round(field):
    module = driver()
    check = incomplete('A')
    check['result']['progress'][field] = [{'reason': 'actual unavailable service'}]
    with pytest.raises(module.Blocked) as exc:
        module.require_development_possible(check, [])
    assert exc.value.payload['phase'] == 'infrastructure'


def test_closeout_does_not_bypass_unresolved_screening_infrastructure(h):
    h.sweep(h.to_phase_b())
    h.reports()
    h.state.data['blockers'] = [{'resolved': False, 'phase': 'repair', 'seed': 52,
                                'reason': 'real service failure'}]
    h.state.save()
    with pytest.raises(ledger.ProtocolError):
        end(h)
    assert not h.state.data.get('development_end')


@pytest.mark.parametrize('deadline,reason', [(3600, 'deadline'), (86400, 'solver_exit')])
def test_real_timeout_reaches_closeout_after_verified_descendant_cleanup(run_closeout_driver, deadline, reason):
    import psutil
    run = run_closeout_driver
    state, calls = run([incomplete('B')] * 2, exits=['owned_timeout'], deadline=deadline)
    receipt = json.loads(next(run.control.glob('campaign-*/turn-00-cleanup.json')).read_text())
    transcript = json.loads(next(run.control.glob('campaign-*/turn-00.stdout.jsonl')).read_text())
    assert receipt['verified_no_live_work']
    assert transcript['child_pid'] in receipt['tracked_pids']
    assert not psutil.pid_exists(transcript['child_pid']) or psutil.Process(transcript['child_pid']).status() == psutil.STATUS_ZOMBIE
    assert state['solver_timeout']['timeout_seconds'] == .3
    assert state['coordinator_exit_code'] == 124
    assert state['turns'][0]['exit_code'] == 124
    assert calls['end_reason'] == reason
    assert calls['native'] == calls['end'] == calls['finalize'] == calls['freeze'] == 1
    assert state['blocked']['phase'] == 'fixture_stop'


def test_real_timeout_phase_a_cannot_select_or_freeze(run_closeout_driver):
    state, calls = run_closeout_driver([incomplete('A')] * 2,
        exits=['owned_timeout'], deadline=3600, end_code=2)
    assert calls['end_reason'] == 'deadline'
    assert calls['native'] == calls['end'] == 1
    assert calls['finalize'] == calls['freeze'] == 0
    assert state['blocked']['phase'] == 'development'


def test_real_timeout_does_not_waive_infrastructure_fault(run_closeout_driver, monkeypatch):
    run = run_closeout_driver
    monkeypatch.setattr(run.driver, 'fault', lambda *a: {'reason': 'perception unavailable'})
    state, calls = run([incomplete('B')] * 2, exits=['owned_timeout'], deadline=3600)
    assert state['blocked']['phase'] == 'infrastructure'
    assert calls['native'] == 1
    assert calls['end'] == calls['finalize'] == calls['freeze'] == 0
