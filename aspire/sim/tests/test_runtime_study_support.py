"""Fresh p2 orchestration regressions: real accounting, scope and command envelope."""
import contextlib
import importlib.util
from pathlib import Path
import sys
import pytest

SIM = Path(__file__).resolve().parents[1]
SUPPORT = SIM / "docs/experiments/code-world-runtime-p2-20261011/support"
for path in (SIM.parents[1], SIM / "scripts/libero", SIM / "scripts/common"):
    sys.path.insert(0, str(path))
from native_world_fixloop_state import NativeWorldState, code_hash, ProtocolError
import native_world_campaign as campaign
import test_native_world_fixloop as fixtures


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load(SIM / "scripts/libero/runtime/native_world_protocol.py", "p2_runtime_protocol_test")
guard = load(SUPPORT / "infra_guard.py", "p2_infra_test")
limits = load(SUPPORT / "runtime_limits.py", "p2_limits_test")
scope = load(SUPPORT / "two_task_scope.py", "p2_scope_test")


@pytest.fixture
def canonical():
    previous = sys.path[:]
    saved = sys.modules.pop("pilot_assignment_guard", None)
    sys.path.insert(0, str(SUPPORT))
    try:
        yield load(SUPPORT / "canonical_assignment.py", "p2_canonical_test")
    finally:
        sys.path[:] = previous
        sys.modules.pop("pilot_assignment_guard", None)
        if saved is not None: sys.modules["pilot_assignment_guard"] = saved


def test_canonical_primary_keeps_all_native_fields_and_exact_bytes(canonical):
    proposal = {"tool_name": "Agent", "tool_use_id": "id1", "tool_input": {
        "prompt": canonical.SENTINEL, "subagent_type": "general-purpose", "run_in_background": True,
        "model": "opus", "description": "cell worker"}}
    text = "# Frozen assignment\nUnicode — →， methods and budgets.\n"
    effective, first = canonical.normalize(proposal, {"primary": None}, text)
    assert first and effective["tool_input"]["prompt"] == text
    assert proposal["tool_input"]["prompt"] == canonical.SENTINEL
    assert {k:v for k,v in effective["tool_input"].items() if k != "prompt"} == {
        k:v for k,v in proposal["tool_input"].items() if k != "prompt"}
    assert canonical.guard.dispatch_mismatch(effective["tool_input"], text) is None


def test_canonical_accepts_actual_native_optional_background_schema(canonical):
    proposal = {"tool_name":"Agent", "tool_input":{"prompt":canonical.SENTINEL,
                                                    "description":"Frozen primary assignment",
                                                    "subagent_type":"general-purpose"}}
    effective, first = canonical.normalize(proposal, {"primary":None}, "exact frozen bytes\n")
    assert first and effective["tool_input"]["prompt"] == "exact frozen bytes\n"
    assert "run_in_background" not in effective["tool_input"]


@pytest.mark.parametrize("change", [{"agent_id": "child"}, {"tool_name": "Bash"},
                                     {"tool_input": {"prompt": "helper", "subagent_type": "Explore", "run_in_background": True}},
                                     {"tool_input": {"prompt": "resume", "subagent_type": "general-purpose", "run_in_background": True, "resume":"old"}}])
def test_canonical_does_not_rewrite_other_tools_roles_or_subagents(canonical, change):
    proposal = {"tool_name": "Agent", "tool_input": {"prompt": "helper", "subagent_type":"general-purpose", "run_in_background":True}}
    proposal.update(change)
    effective, first = canonical.normalize(proposal, {"primary":None}, "frozen")
    assert not first and effective == proposal


def test_canonical_never_creates_second_primary(canonical):
    proposal={"tool_name":"Agent","tool_input":{"prompt":canonical.SENTINEL,"subagent_type":"general-purpose","run_in_background":True}}
    with pytest.raises(canonical.guard.GuardError, match="already admitted"):
        canonical.normalize(proposal, {"primary":{"worker":"id1"}}, "frozen")


def state_at(tmp_path):
    return NativeWorldState(tmp_path / "task", {"dev_seeds": list(range(51, 66)), "profile": "judgment"})


def lost(state, phase="smoke", seed=51):
    bundle = {"policy": code_hash("get_observation()")}
    row = state.begin_trial(phase, seed, bundle, {})
    state.finish_trial(row, result=None, exit_code=124, error="transport lost")
    return row


def test_closed_infra_keeps_status_slot_and_zero_grades(tmp_path):
    state = state_at(tmp_path)
    row = lost(state)
    before = state.retries_remaining(51)
    runtime.close_attempt(state, Path(row["directory"]), "observed process exit124 and missing result")
    runtime.close_attempt(state, Path(row["directory"]), "idempotent")
    assert row["status"] == "infrastructure_error"
    assert state.retries_remaining(51) == before == 2
    assert state.graded() == [] and state.candidates() == {}
    result = runtime.progress(state)
    assert result["infrastructure_errors"] == []
    assert len(result["closed_infrastructure_attempts"]) == 1
    assert 51 in result["seeds_needing_initial"]
    assert guard.classify(state.data) is None
    assert guard.pending(state.data) == []


def test_exhausted_closed_infra_remains_terminal(tmp_path):
    state = state_at(tmp_path)
    for _ in range(3):
        row = lost(state)
        runtime.close_attempt(state, Path(row["directory"]), "missing execution evidence")
    assert runtime.progress(state)["seeds_exhausted_without_graded_evidence"] == [51]
    assert guard.classify(state.data)["kind"] == "terminal_missing_graded_evidence"
    with pytest.raises(ProtocolError):
        lost(state)
    assert len(state.data["trials"]) == 3


def test_closed_loss_then_real_grade_restores_coverage_without_refund(tmp_path):
    state = state_at(tmp_path)
    row = lost(state)
    runtime.close_attempt(state, Path(row["directory"]), "missing evidence")
    graded = state.begin_trial("smoke", 51, row["bundle"], {})
    state.finish_trial(graded, result={"sandbox_rc": 0, "task_completed": 1, "reward": 1}, exit_code=0)
    assert state.retries_remaining(51) == 1
    assert len(state.graded()) == 1
    assert guard.classify(state.data) is None


@pytest.mark.parametrize("success", [True, False])
def test_final_running_attempt_survives_actual_fault_monitor(tmp_path, monkeypatch, success):
    import json
    import threading
    state = state_at(tmp_path)
    for _ in range(2):
        row = lost(state)
        runtime.close_attempt(state, Path(row["directory"]), "ended without evidence")
    row = state.begin_trial("smoke", 51, row["bundle"], {})
    case = {"sim": str(tmp_path / "sim"), "control": str(tmp_path / "control"),
            "suite": "libero_goal_swap", "task": "bowl"}
    task = Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    task.mkdir(parents=True)
    ledger = task / "development_state.json"
    ledger.write_text(json.dumps(state.data))
    assert guard.fault(case) is None
    killed = []
    monkeypatch.setattr(guard.os, "killpg", lambda *a: killed.append(a))
    class Process:
        pid = 123456
        reads = 0
        def poll(self):
            self.reads += 1
            return None if self.reads == 1 else 0
    threads = []
    import types
    class Thread:
        def __init__(self, target, daemon):
            self.target = target; threads.append(self)
        def start(self): self.target()
        def join(self, timeout): pass
    monkeypatch.setattr(guard, "threading", types.SimpleNamespace(
        Thread=Thread, Event=lambda: types.SimpleNamespace(wait=lambda seconds: None)))
    guard.monitor(Process(), case, tmp_path / "monitor.json")
    threads[0].join(timeout=2)
    assert not killed and not (tmp_path / "monitor.json").exists()
    state.finish_trial(row, result={"sandbox_rc": 0, "task_completed": 1, "reward": 1} if success else None,
                       exit_code=0 if success else 124)
    ledger.write_text(json.dumps(state.data))
    if success:
        assert guard.fault(case) is None and len(state.graded()) == 1
    else:
        assert guard.fault(case)["kind"] == "terminal_missing_graded_evidence"
        guard.monitor(Process(), case, tmp_path / "monitor.json")
        threads[-1].join(timeout=2)
        assert killed == [(123456, guard.signal.SIGTERM)]
        assert json.loads((tmp_path / "monitor.json").read_text())["kind"] == "terminal_missing_graded_evidence"
    assert state.retries_remaining(51) == 0


@pytest.mark.parametrize("phase", ["snapshot", "running"])
def test_close_refuses_single_snapshot_and_active_rows(tmp_path, phase):
    state = state_at(tmp_path)
    if phase == "snapshot":
        row = lost(state, "snapshot")
    else:
        row = state.begin_trial("smoke", 51, {"policy": code_hash("get_observation()")}, {})
    with pytest.raises(ProtocolError):
        runtime.close_attempt(state, Path(row["directory"]), "probe")


def test_close_refuses_discarding_present_trial_result(tmp_path):
    state = state_at(tmp_path)
    row = lost(state)
    (state.task_dir / row["directory"] / "results/trial_51_sandboxrc_0_reward_1.000_taskcompleted_1").mkdir(parents=True)
    with pytest.raises(ProtocolError, match="outer recovery"):
        runtime.close_attempt(state, Path(row["directory"]), "do not discard")


def test_outer_envelope_covers_screening_and_child_without_budget_change():
    case = {"trial_timeout": 900}
    settings = limits.install({"env": {}}, case)
    assert limits.command_seconds(case) == 2520
    assert settings["env"]["BASH_DEFAULT_TIMEOUT_MS"] == "2520000"
    assert case["trial_timeout"] == 900
    cmd = ".venv-libero/bin/python3 scripts/libero/runtime/native_world_protocol.py trial --phase initial --seed 51"
    assert limits.trial_timeout_error({"tool_name": "Bash", "tool_input": {"command": "timeout 890 " + cmd}}, case)
    assert limits.trial_timeout_error({"tool_name": "Bash", "tool_input": {"command": cmd, "timeout": 900000}}, case)
    assert limits.trial_timeout_error({"tool_name": "Bash", "tool_input": {"command": cmd, "timeout": 2520000}}, case) is None
    assert limits.trial_timeout_error({"tool_name": "Bash", "tool_input": {"command": cmd, "run_in_background": True}}, case) is None


def test_runtime_replay_preserves_child_watchdog_and_existing_env(monkeypatch, tmp_path):
    captured = {}
    def replay(command, **kw): captured.update(command=command, **kw); return (0, "")
    monkeypatch.setattr(runtime, "original_replay", replay)
    runtime.run_replay(["python", "scripts/libero/replay_trial.py", "--args.trial", "51"],
                       env={"EXISTING": "yes"}, directory=tmp_path, timeout=900)
    assert captured["command"][1] == "scripts/libero/runtime/replay_trial.py"
    assert captured["timeout"] == 900
    assert captured["env"] == {"EXISTING": "yes", "ASPIRE_RUNTIME_EVIDENCE_DIR": str(tmp_path)}


@pytest.mark.parametrize("privileged,api_name", [(False, "FrankaLiberoApiReducedSkillLibraryTraced"),
                                                (True, "FrankaLiberoApiReducedSkillLibraryTraced"),
                                                (False, "PrivilegedApi")])
def test_constructed_api_gate_checks_actual_objects(privileged, api_name):
    import ast
    import types
    # Execute the production verifier independently of unavailable local simulator imports.
    tree = ast.parse((SIM / "scripts/libero/runtime/replay_trial.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "verify_environment")
    namespace = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "runtime_api_gate", "exec"), namespace)
    env = types.SimpleNamespace(cfg=types.SimpleNamespace(privileged=privileged, prompt="public"),
                                low_level_env=types.SimpleNamespace(privileged=privileged),
                                _apis={api_name: type(api_name, (), {})()})
    if privileged or api_name != "FrankaLiberoApiReducedSkillLibraryTraced":
        with pytest.raises(RuntimeError): namespace["verify_environment"](env)
    else:
        proof = namespace["verify_environment"](env)
        assert proof["privileged"] is False and list(proof["constructed_apis"]) == [api_name]


def test_actual_p2_prompt_and_scope_preserve_boundaries(tmp_path):
    _, case = fixtures.make_case(tmp_path, "C")
    case.update(profile="judgment", c_arm="full", c_lineage="fresh", foundation_revision="r1",
                executable_world_revision="r1", prediction_contract="p2-runtime", development_gate="oracle",
                world_interface_doc="cap/world_model/RUNTIME_INTERFACE.md",
                heldout_seeds=list(range(1, 51)))
    # Do not leave scope mutations in the shared campaign module.
    attrs = ("worker_prompt", "coordinator_prompt", "PROTOCOL", "_two_task_scope_cell")
    saved = {a: getattr(campaign, a) for a in attrs if hasattr(campaign, a)}
    try:
        scope.apply(campaign, case)
        text = campaign.worker_prompt(case, SIM)
        assert "scripts/libero/runtime/native_world_protocol.py trial" in text
        assert "2520 seconds" in text and "close-infra" in text
        assert "Runtime world p2" in text
        scope.verify_worker(text, case, SIM)
        with pytest.raises(scope.ScopeError):
            scope.verify_worker(text, dict(case, prediction_contract="p1"), SIM)
    finally:
        for attr in attrs:
            if attr in saved: setattr(campaign, attr, saved[attr])
            elif hasattr(campaign, attr): delattr(campaign, attr)


def test_actual_child_prompt_receipt_requires_exact_native_bytes(tmp_path):
    import ast,json
    source=ast.parse((SUPPORT/'lineage.py').read_text())
    fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='verify_received_assignment')
    namespace={'json':json};exec(compile(ast.Module(body=[fn],type_ignores=[]),'lineage.py','exec'),namespace)
    config=tmp_path/'config';child=config/'projects/project/session/subagents/agent-a123.jsonl';child.parent.mkdir(parents=True)
    text='Full frozen assignment — Unicode\n';assignment=tmp_path/'prompt.md';assignment.write_text(text)
    child.write_text(json.dumps({'type':'user','message':{'content':[{'type':'text','text':text}]}})+'\n')
    receipt=namespace['verify_received_assignment'](config,'session','a123',assignment)
    assert receipt['state']=='passed' and receipt['assignment_sha256']==receipt['received_prompt_sha256']
    child.write_text(json.dumps({'type':'user','message':{'content':text.rstrip()}})+'\n')
    with pytest.raises(ValueError,match='differs'):namespace['verify_received_assignment'](config,'session','a123',assignment)


def queue_harness(tmp_path, monkeypatch, first_command, timeout, cleanup_failure=False, checkpoint=False, *, supervisor=None, functional_health=None):
    """Run the real queue function against real child Popen drivers."""
    import ast,json,os,signal,subprocess,time,types,psutil
    source=ast.parse((supervisor or SUPPORT/'dlc-supervisor.py').read_text())
    nodes=[n for n in source.body if isinstance(n,(ast.ClassDef,ast.FunctionDef)) and n.name in {'CellProcessTree','run_queue'}]
    parent=tmp_path/'round';parent.mkdir();cases=[]
    for i in range(2):
        cell=parent/f'cell{i}';cell.mkdir();case={'id':cell.name,'sim':str(cell),'cuda_visible_devices':'6','egl_device_id':'6'}
        (cell/'case.json').write_text(json.dumps(case));(cell/'campaign_state.json').write_text(json.dumps({'status':'full_complete'}));cases.append(cell)
    if checkpoint:
        (cases[0]/'native_smoke_state.json').write_text(json.dumps({'status':'native_smoke_checkpoint','native_session':'same-session','native_worker':'same-worker'}))
        (cases[0]/'probe.stdout.jsonl').write_text('retained original capacity output\n')
        (cases[0]/'development_state.json').write_text('retained charged ledger')
    (parent/'prepare-receipt.json').write_text(json.dumps({'cells':{c.name:{'case_sha256':'fixture'} for c in cases}}))
    cursor=iter(cases);processes=[];signals=[]
    def claim(*_):
        cell=next(cursor,None)
        if cell is not None:(parent/'queue/claims'/cell.name).mkdir(parents=True)
        return cell
    def spawn(*_):
        if processes:assert processes[0].poll() is not None, 'first driver alive at second spawn'
        proc=subprocess.Popen(first_command if not processes else [sys.executable,'-c','pass'],start_new_session=True)
        processes.append(proc);return proc
    namespaces={'psutil':psutil,'time':time,'subprocess':subprocess,'json':json,'Path':Path,'sys':sys,
                'claim_next':claim,'same_runtime_family':lambda *_:[],'digest':lambda *_:'fixture',
                'verify_staged_runtime':lambda *_:None,'python_entry':lambda *_:[],
                'DRIVER_REL':'support/driver.py','driver_timeout':lambda _:timeout,
                'full_deadlines_module':lambda:types.SimpleNamespace(DEVELOPMENT=1,HELDOUT=1),
                'accept_cell_result':lambda result,code: result if code==0 else (_ for _ in ()).throw(RuntimeError('bad code')),
                'atomic_json':lambda p,d:p.write_text(json.dumps(d)), 'TERMINAL':'full_complete'}
    monkeypatch.setitem(sys.modules,'native_world_campaign',types.SimpleNamespace(verify_runtime=lambda *_:None))
    monkeypatch.setitem(sys.modules,'output_ownership',types.SimpleNamespace(verify=lambda *_:None))
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'dlc-supervisor.py','exec'),namespaces)
    tree_class=namespaces['CellProcessTree'];original_cleanup=tree_class.cleanup
    if cleanup_failure:
        def fail_cleanup(self):raise RuntimeError('ownership cleanup cannot be confirmed')
        tree_class.cleanup=fail_cleanup
    else:
        tree_class.cleanup=lambda self:original_cleanup(self,grace=.3)
    queue=namespaces['run_queue'];args=types.SimpleNamespace(worker_rank=0,queue=parent)
    try:
        kwargs={} if functional_health is None else {"functional_health":functional_health}
        code=queue(args,{},parent,parent,sys.executable,{},spawn,types.SimpleNamespace(check=lambda:None),lambda *_a,**_k:None,{},parent,**kwargs)
        rows=[json.loads(p.read_text()) for c in cases if (p:=parent/'queue/claims'/c.name/'result.json').exists()]
        return code,rows,processes
    finally:
        for proc in processes:
            if proc.poll() is None:os.killpg(proc.pid,signal.SIGKILL)
            proc.wait()


def test_real_queue_timeout_cleans_first_driver_before_second_spawn(tmp_path,monkeypatch):
    code,rows,processes=queue_harness(tmp_path,monkeypatch,[sys.executable,'-c','import time;time.sleep(60)'],.15)
    assert code==1 and len(processes)==2 and rows[0]['state']=='failed'
    assert rows[0]['process_cleanup']['verified_no_live_work']
    assert rows[0]['process_cleanup']['signals']
    assert rows[1]['process_cleanup']['verified_no_live_work']


def test_real_queue_normal_completion_sends_no_termination(tmp_path,monkeypatch):
    code,rows,processes=queue_harness(tmp_path,monkeypatch,[sys.executable,'-c','pass'],5)
    assert code==0 and len(processes)==2
    assert all(r['process_cleanup']['signals']==[] for r in rows)


def test_real_queue_timeout_cleans_detached_descendant_before_next_cell(tmp_path,monkeypatch):
    import psutil
    marker=tmp_path/'child.pid'
    command=[sys.executable,'-c',f"import subprocess,sys,time;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True);Path({str(marker)!r}).write_text(str(p.pid));time.sleep(60)"]
    code,rows,processes=queue_harness(tmp_path,monkeypatch,command,.3)
    assert len(processes)==2 and code==1
    pid=int(marker.read_text())
    assert pid in rows[0]['process_cleanup']['tracked_pids']
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE


def test_queue_never_claims_next_cell_when_cleanup_unconfirmed(tmp_path,monkeypatch):
    code,rows,processes=queue_harness(tmp_path,monkeypatch,[sys.executable,'-c','import time;time.sleep(60)'],.15,cleanup_failure=True)
    assert code==1 and len(processes)==1 and len(rows)==1
    assert rows[0]['state']=='cleanup_failed'
    assert not (tmp_path/'round/queue/claims/cell1').exists()


def test_queue_same_worker_handoff_preserves_probe_and_ledger(tmp_path,monkeypatch):
    code,rows,processes=queue_harness(tmp_path,monkeypatch,[sys.executable,'-c','pass'],5,checkpoint=True)
    assert code==0
    root=tmp_path/'round';cell=root/'cell0'
    assert (root/'queue/claims/cell0/prior-native-smoke-probe/probe.stdout.jsonl').read_text()=='retained original capacity output\n'
    assert not (cell/'probe.stdout.jsonl').exists()
    assert (cell/'development_state.json').read_text()=='retained charged ledger'
    handoff=rows[0]['native_startup_handoff']
    assert handoff['native_session']=='same-session' and handoff['native_worker']=='same-worker'
    assert handoff['attempts_refunded']==0 and handoff['ledger_reset'] is False
