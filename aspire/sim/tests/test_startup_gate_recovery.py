"""Synthetic artifact/regression fixtures only; never actual simulator evidence."""
import copy,hashlib,importlib.util,json,sys
from pathlib import Path
import pytest
SIM=Path(__file__).resolve().parents[1];DOC=SIM/'docs/experiments/code-world-runtime-p2-20261011'
sys.path.insert(0,str(DOC));sys.path.insert(0,str(SIM/'scripts/libero'))
import startup_gate as gate
from aspire.sim.cap.world_model.runtime_mechanism_audit import audit_events


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value)+'\n')


def make_attempt(task,phase,seed,attempt,revision):
    relative=f'development/{phase}/seed_{seed}/attempt_{attempt}';d=task/relative;d.mkdir(parents=True)
    (d/'code.py').write_text(f'get_observation() # policy {revision}\n');(d/'world_program.py').write_text(f'# world {revision}\n')
    pair={'policy':gate.sha(d/'code.py'),'world':gate.sha(d/'world_program.py')}
    row={'phase':phase,'seed':seed,'attempt':attempt,'directory':relative,'bundle':pair,'bundle_sha256':gate.stable(pair),'status':'complete','sandbox_rc':0,'spends_retry':True,'executed':True,'process_exit_code':0}
    write(d/'executable_world_config.json',{'policy_sha256':pair['policy'],'world_program_sha256':pair['world']})
    write(d/'resolved-environment.json',{'privileged':False,'constructed_apis':{'FrankaLiberoApiReducedSkillLibraryTraced':'fixture actual class'}})
    fact={'entity':'bowl','fact':'center','kind':'geometry','status':'known','evidence_ids':['e']}
    result={'entity':'bowl','fact':'center','kind':'geometry','status':'match','evidence_id':'e','prediction_call':'p'}
    feedback={'read_id':'read','results':[result]}
    events=[{'event':'public_api','function':name,'api_call':i+1} for i,name in enumerate(['get_observation','segment_sam3_text_prompt','mask_to_world_points','move_to_pose'])]
    events += [{'event':'state_write','record':fact},{'event':'prediction_committed'},
               {'event':'world_query','name':'state','result':fact},{'event':'prediction_check',**result},
               {'event':'world_query','name':'prediction_feedback','result':feedback},
               {'event':'policy_decision','decision_id':'d','branch':'verified','feedback':feedback,'facts':[fact]},
               {'event':'policy_decision_action','decision_id':'d','api_call':4,'function':'move_to_pose','args':[],'kwargs':{}}]
    world=d/'judgment_world';world.mkdir();(world/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
    write(world/'manifest.json',{'runtime_mechanism':audit_events(world/'events.jsonl',static_use={})})
    return row


def fixture(tmp_path,count=1):
    control=tmp_path/'control';sim=tmp_path/'sim';task=sim/'outputs/libero_fix_loop/suite/task';task.mkdir(parents=True)
    core=sim/'cap/world_model/runtime_mechanism_audit.py';core.parent.mkdir(parents=True);core.write_bytes((SIM/'cap/world_model/runtime_mechanism_audit.py').read_bytes())
    case={'id':'bowl_p2_r1','sim':str(sim),'control':str(control),'suite':'suite','task':'task','dev_seeds':list(range(51,66))}
    write(control/'case.json',case);(control/'worker-prompt.md').write_text('Exact frozen prompt\n')
    support=control.parent/'support';support.mkdir()
    for name in ['lineage.py','native_lineage_r2.py']:(support/name).write_bytes((DOC/'support'/name).read_bytes())
    root=control/'root.jsonl';root.write_text(''.join(json.dumps(row)+'\n' for row in [
        {'type':'system','subtype':'init','session_id':'same-session'},
        {'type':'assistant','message':{'content':[{'type':'tool_use','id':'tool1','name':'Agent','input':{'subagent_type':'general-purpose','prompt':'Exact frozen prompt\n'}}]}},
        {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'tool1','content':'agentId: a0123456789'}]}}]))
    child=control/'child.jsonl';write(child,{'type':'user','message':{'content':'Exact frozen prompt\n'}})
    snapshot={'phase':'snapshot','seed':51,'attempt':1,'status':'complete','directory':'development/snapshot/seed_51/attempt_1','spends_retry':False,'executed':True,'bundle':{'policy':'a'*64},'bundle_sha256':gate.stable({'policy':'a'*64})}
    first=make_attempt(task,'smoke',51,1,1)
    original={'status':'native_smoke_checkpoint','cell':case['id'],'native_worker':'a0123456789','native_session':'same-session','coordinator_exit_code':0,
              'solver_transcripts':[str(root)],'smoke_attempt':copy.deepcopy(first),'actual_worker_prompt_receipt':{'state':'passed','actual_child_transcript':str(child)}}
    write(control/'native_smoke_state.json',original)
    for key,name in [('policy','initial_code.py'),('world','initial_world_program.py')]:
        (task/name).write_bytes((task/first['directory']/('code.py' if key=='policy' else 'world_program.py')).read_bytes())
    rows=[snapshot,first]
    if count>=2:rows.append(make_attempt(task,'initial',51,2,2))
    if count>=3:
        # A THIRD real repair is legal only AFTER the full initial batch.
        for seed in range(52,66):rows.append(make_attempt(task,'initial',seed,1,2))
        rows.append(make_attempt(task,'repair',51,3,3))
    ledger={'trials':rows,'selected':None,'stage1_complete':False,'aliases':[]};write(task/'development_state.json',ledger)
    if count>=2:
        before=control/'recovery/ledger-before.json';write(before,{**ledger,'trials':rows[:-1]})
        cp={'status':'native_gate_repair_checkpoint','native_worker':'a0123456789','native_session':'same-session','coordinator_exit_code':0,
            'solver_transcripts':[str(root)],'original_checkpoint_sha256':gate.sha(control/'native_smoke_state.json'),'ledger_before':str(before),'ledger_before_sha256':gate.sha(before),
            'selected_attempt':rows[-1]['directory'],'selected_bundle':rows[-1]['bundle'],'attempt_count':count,'attempt_counts':{str(seed):sum(r.get('spends_retry',False) and r['seed']==seed for r in rows) for seed in case['dev_seeds']},'ledger_sha256':gate.sha(task/'development_state.json')}
        write(control/'native_work_continuation_checkpoint.json',cp)
    return control,task,ledger


@pytest.mark.parametrize('count',[1,2,3])
def test_first_second_and_third_specific_valid_execution_acceptance(tmp_path,count):
    control,task,ledger=fixture(tmp_path,count)
    result=gate.verify(control)
    assert result['state']=='passed',result
    assert result['attempt_count']==count and result['selected_attempt']==ledger['trials'][-1]['directory']
    assert result['bundle']==ledger['trials'][-1]['bundle']
    assert gate.load(control/'native_smoke_state.json')['smoke_attempt']==ledger['trials'][1]


def test_fourth_execution_refused_even_with_forged_checkpoint(tmp_path):
    control,task,ledger=fixture(tmp_path,3);ledger['trials'].append(make_attempt(task,'repair',51,4,4));write(task/'development_state.json',ledger)
    assert gate.verify(control)['state']=='not_passed'


def test_worker_replacement_refused(tmp_path):
    control,_,_=fixture(tmp_path,2);p=control/'native_work_continuation_checkpoint.json';cp=gate.load(p);cp['native_worker']='replacement';write(p,cp)
    assert 'worker/session changed' in str(gate.verify(control)['errors'])


def test_old_initial_bundle_cannot_stand_in_for_second_attempt(tmp_path):
    control,_,ledger=fixture(tmp_path,2);p=control/'native_work_continuation_checkpoint.json';cp=gate.load(p);cp.update(selected_attempt=ledger['trials'][1]['directory'],selected_bundle=ledger['trials'][1]['bundle']);write(p,cp)
    assert 'specific real execution' in str(gate.verify(control)['errors'])


def reviewed(result):
    return {**result,'semantic_source_review':{'accepted':True,**{k:result[k] for k in ['binding_sha256','selected_attempt','bundle']}}}


def test_submit_revalidates_all_current_evidence_and_bound_review(tmp_path):
    control,task,ledger=fixture(tmp_path,2);saved=reviewed(gate.verify(control));assert gate.require_fresh_review(control,saved)['state']=='passed'
    manifest=task/ledger['trials'][-1]['directory']/'judgment_world/manifest.json';data=gate.load(manifest);data['benign_extra']='new bytes';write(manifest,data)
    assert gate.verify(control)['state']=='passed'
    with pytest.raises(gate.GateError,match='stale'):gate.require_fresh_review(control,saved)


def test_stale_or_unbound_semantic_review_refused(tmp_path):
    control,_,_=fixture(tmp_path);saved=reviewed(gate.verify(control));saved['semantic_source_review']['bundle']={'policy':'old'}
    with pytest.raises(gate.GateError,match='semantic source review'):gate.require_fresh_review(control,saved)


def test_false_early_repair_is_refused_by_actual_frozen_admission(tmp_path):
    control,task,ledger=fixture(tmp_path,2);ledger['trials'][-1]['phase']='repair';write(task/'development_state.json',ledger)
    result=gate.verify(control)
    assert result['state']=='not_passed' and 'before repairs' in str(result['errors'])


def test_mutable_source_cannot_replace_actual_execution_snapshot(tmp_path):
    control,task,ledger=fixture(tmp_path,2);(task/ledger['trials'][-1]['directory']/'world_program.py').write_text('# changed world\n')
    assert 'snapshots differ' in str(gate.verify(control)['errors'])


def test_actual_transcript_replacement_cannot_hide_behind_checkpoint_identity(tmp_path):
    control,_,_=fixture(tmp_path,2);root=control/'root.jsonl';root.write_text(root.read_text().replace('agentId: a0123456789','agentId: a9999999999'))
    assert 'actual native transcript worker/session lineage changed' in str(gate.verify(control)['errors'])


def test_mechanism_manifest_cannot_fabricate_missing_event_dataflow(tmp_path):
    control,task,ledger=fixture(tmp_path);events=task/ledger['trials'][-1]['directory']/'judgment_world/events.jsonl'
    rows=[json.loads(line) for line in events.read_text().splitlines()];rows=[r for r in rows if r['event']!='prediction_check'];events.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    assert 'actual event dataflow' in str(gate.verify(control)['errors'])


def test_recovery_guard_cannot_spawn_replacement_or_spend_fourth(tmp_path):
    spec=importlib.util.spec_from_file_location('recovery_guard_test',DOC/'recovery_guard.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    control,_,_=fixture(tmp_path,3);plan={'phase':'repair','seeds':[51],'before_counts':{'51':3}}
    assert guard.denial({'tool_name':'Agent','tool_input':{}},control,plan)
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':'python scripts/libero/runtime/native_world_protocol.py trial --phase repair --seed 51'}},control,plan)


def test_recovery_guard_limits_one_attempt_and_declared_seed_phase(tmp_path):
    spec=importlib.util.spec_from_file_location('recovery_guard_seed_test',DOC/'recovery_guard.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    control,_,_=fixture(tmp_path,1);plan={'phase':'initial','seeds':[51],'before_counts':{'51':1},'alias_seed':None}
    base='python scripts/libero/runtime/native_world_protocol.py trial --phase initial --seed 51 --code new.py --world-program new_world.py'
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':base}},control,plan) is None
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':base.replace('--seed 51','--seed 52')}},control,plan)
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':base+'; '+base}},control,plan)


def test_recovery_settlement_keeps_first_checkpoint_and_initial_pair(tmp_path):
    import types
    spec=importlib.util.spec_from_file_location('recover_settle_test',DOC/'recover-native-gate.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    control,task,ledger=fixture(tmp_path,2);case=gate.load(control/'case.json');config=control/'config';case['claude_config_dir']=str(config);write(control/'case.json',case)
    actual=config/'projects/project/same-session/subagents/agent-a0123456789.jsonl';actual.parent.mkdir(parents=True);actual.write_bytes((control/'child.jsonl').read_bytes())
    folder=control/'recovery/new';folder.mkdir(parents=True);write(folder/'ledger-before.json',{**ledger,'trials':ledger['trials'][:-1]})
    transcript=folder/'stdout.jsonl';transcript.write_text(json.dumps({'type':'system','subtype':'init','session_id':'same-session'})+'\n')
    original_sha=gate.sha(control/'native_smoke_state.json');initial_pair={k:gate.sha(task/n) for k,n in [('policy','initial_code.py'),('world','initial_world_program.py')]}
    plan={'native_session':'same-session','native_worker':'a0123456789','phase':'initial','stage':'initial_batch','seeds':[51],'executions_to_add_maximum':1,'ledger_before_sha256':gate.sha(folder/'ledger-before.json'),
          'original_checkpoint_sha256':original_sha,'failure_review_sha256':'f'*64,'initial_bundle':initial_pair}
    driver=types.SimpleNamespace(identity=lambda *_:('same-session','a0123456789'))
    cp=runner.settle(control,folder,plan,transcript,0,driver)
    assert cp['selected_bundle']==ledger['trials'][-1]['bundle'] and cp['attempt_counts']['51']==2
    assert gate.sha(control/'native_smoke_state.json')==original_sha
    assert {k:gate.sha(task/n) for k,n in [('policy','initial_code.py'),('world','initial_world_program.py')]}==initial_pair
    assert (folder/'previous-continuation-checkpoint.json').exists()


def test_recovery_guard_preserves_initial_and_executed_artifacts(tmp_path):
    spec=importlib.util.spec_from_file_location('recovery_guard_write_test',DOC/'recovery_guard.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    control,task,ledger=fixture(tmp_path);plan={'phase':'initial','seeds':[51],'before_counts':{'51':1},'alias_seed':None}
    for path in [task/'initial_code.py',task/'development_state.json',task/ledger['trials'][-1]['directory']/'code.py']:
        assert guard.denial({'tool_name':'Write','tool_input':{'file_path':str(path)}},control,plan)
    assert guard.denial({'tool_name':'Write','tool_input':{'file_path':str(task/'startup_revision2_code.py')}},control,plan) is None


def runner_module():
    spec=importlib.util.spec_from_file_location('recover_plan_test',DOC/'recover-native-gate.py');runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    return runner


def read_only_state(ledger):
    from native_world_fixloop_state import NativeWorldState
    state=object.__new__(NativeWorldState);state.seeds=list(range(51,66));state.data=copy.deepcopy(ledger);state.save=lambda:None
    return state


def test_original_order_plans_initial_batch_and_eligible_alias(tmp_path):
    _,_,ledger=fixture(tmp_path)
    before=copy.deepcopy(ledger);plan=runner_module().next_stage(read_only_state(ledger))
    assert plan['stage']=='initial_batch' and plan['seeds']==list(range(52,66)) and plan['alias_seed']==51
    assert plan['initial_batch_bundle']==ledger['trials'][1]['bundle_sha256'] and ledger==before


def test_repair_is_available_after_original_initial_batch(tmp_path):
    _,_,ledger=fixture(tmp_path,3)
    plan=runner_module().next_stage(read_only_state(ledger))
    assert plan['stage']=='repair' and plan['seeds']==[52] and plan['alias_seed'] is None


def test_infrastructure_running_is_recoverable_not_budget_exhaustion(tmp_path):
    _,_,ledger=fixture(tmp_path);ledger['trials'][-1]['status']='running'
    with pytest.raises(gate.GateError,match='infrastructure_unresolved'):runner_module().next_stage(read_only_state(ledger))


def test_true_budget_exhaustion_preserves_history(tmp_path):
    _,task,ledger=fixture(tmp_path,3)
    for seed in range(52,66):
        ledger['trials'] += [make_attempt(task,'repair',seed,2,4),make_attempt(task,'repair',seed,3,5)]
    before=copy.deepcopy(ledger)
    with pytest.raises(gate.GateError,match='budget_exhausted'):runner_module().next_stage(read_only_state(ledger))
    assert ledger==before


def test_gate_binds_a_real_other_development_seed_not_extra_51_replay(tmp_path):
    control,task,ledger=fixture(tmp_path,3);p=control/'native_work_continuation_checkpoint.json';cp=gate.load(p)
    # The recovered batch may stop as soon as a real seed52 mechanism qualifies.
    row=next(r for r in ledger['trials'] if r['seed']==52);before=gate.load(Path(cp['ledger_before']));before['trials']=ledger['trials'][:3]
    write(Path(cp['ledger_before']),before);cp.update(ledger_before_sha256=gate.sha(cp['ledger_before']),selected_attempt=row['directory'],selected_bundle=row['bundle']);write(p,cp)
    result=gate.verify(control)
    assert result['state']=='passed' and result['seed']==52 and result['attempt_counts']['51']==3


def test_guard_declared_initial_batch_can_run_other_seeds_once(tmp_path):
    spec=importlib.util.spec_from_file_location('recovery_guard_batch_test',DOC/'recovery_guard.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    control,task,ledger=fixture(tmp_path);plan={'phase':'initial','seeds':list(range(52,66)),'before_counts':{str(seed):0 for seed in range(52,66)},'alias_seed':51}
    cmd='python scripts/libero/runtime/native_world_protocol.py trial --phase initial --seed 52'
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':cmd}},control,plan) is None
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':cmd.replace('initial','repair')}},control,plan)
    alias='python scripts/libero/runtime/native_world_protocol.py alias-smoke --seed 51'
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':alias}},control,plan) is None
    ledger['trials'].append(make_attempt(task,'initial',52,1,1));write(task/'development_state.json',ledger)
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':cmd}},control,plan)


def test_alias_is_reference_only_and_cannot_forge_evidence(tmp_path):
    control,task,ledger=fixture(tmp_path);state=read_only_state(ledger)
    first=state.data['trials'][1];first.update(sources={'policy':'original'},reward=0,task_completed=0)
    alias=state.alias_smoke_as_initial(51);gate.validate_history(gate.load(control/'case.json'),state.data)
    alias['executed']=True
    with pytest.raises(gate.GateError,match='alias changed'):gate.validate_history(gate.load(control/'case.json'),state.data)


def test_ended_infra_plan_is_disposition_without_new_trial(tmp_path):
    _,_,ledger=fixture(tmp_path);ledger['trials'][-1]['status']='infrastructure_error'
    plan=runner_module().next_stage(read_only_state(ledger))
    assert plan['stage']=='close_infra' and plan['seeds']==[] and plan['directories']==[ledger['trials'][-1]['directory']]


def test_close_infra_does_not_grant_replay(tmp_path):
    spec=importlib.util.spec_from_file_location('infra_guard_scope_test',DOC/'recovery_guard.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    control,_,ledger=fixture(tmp_path);plan={'stage':'close_infra','directories':[ledger['trials'][-1]['directory']]}
    cmd=f"python scripts/libero/runtime/native_world_protocol.py close-infra --directory {plan['directories'][0]} --reason lost"
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':cmd}},control,plan) is None
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':cmd.replace(plan['directories'][0],'other-record')}},control,plan)
    assert guard.denial({'tool_name':'Bash','tool_input':{'command':'python scripts/libero/runtime/native_world_protocol.py trial --phase initial --seed 51'}},control,plan)


def test_capture_safe_blocked_first_pause_preserves_original(tmp_path):
    import types
    runner=runner_module();control,task,ledger=fixture(tmp_path);case=gate.load(control/'case.json');case['claude_config_dir']=str(control/'config');write(control/'case.json',case)
    original=gate.load(control/'native_smoke_state.json');original.update(status='blocked',campaign_dir=str(control/'first-run'))
    write(control/'first-run/turn-00.stdout.jsonl',{'type':'system','subtype':'init','session_id':'same-session'})
    actual=Path(case['claude_config_dir'])/'projects/project/same-session/subagents/agent-a0123456789.jsonl';actual.parent.mkdir(parents=True);actual.write_bytes((control/'child.jsonl').read_bytes())
    write(control/'native_smoke_state.json',original);old=gate.sha(control/'native_smoke_state.json')
    captured,cp=runner.capture_first_pause(control,types.SimpleNamespace(identity=lambda *_:('same-session','a0123456789')))
    assert captured.name=='native_gate_first_pause.json' and cp['source_state_sha256']==old and cp['native_worker']=='a0123456789'
    assert gate.sha(control/'native_smoke_state.json')==old and cp['smoke_attempt']==ledger['trials'][1]


def test_running_first_pause_cannot_be_captured(tmp_path):
    runner=runner_module();control,task,ledger=fixture(tmp_path);p=control/'native_smoke_state.json';original=gate.load(p);original.update(status='solver_running',coordinator_exit_code=None);write(p,original)
    with pytest.raises(gate.GateError,match='safely ended'):runner.capture_first_pause(control)
    assert not (control/'native_gate_first_pause.json').exists()


def test_passed_first_mechanism_does_not_authorize_extra_trial(tmp_path,monkeypatch):
    import types
    runner=runner_module();control,task,ledger=fixture(tmp_path);case=gate.load(control/'case.json')
    monkeypatch.setattr(runner,'original_context',lambda _: (case,None,Path(case['sim']),None,types.SimpleNamespace(verify_runtime=lambda *_:None),None))
    current=gate.verify(control);review={'accepted':False,'reason':'continue anyway','native_worker':current['native_worker'],'native_session':current['native_session'],'ledger_sha256':gate.sha(task/'development_state.json'),'binding_sha256':current['binding_sha256']};write(control/'review.json',review)
    with pytest.raises(gate.GateError,match='specific semantic failure'):runner.prepare(control,'auto',control/'review.json')


def test_prepare_automatically_plans_original_batch_and_refuses_early_repair(tmp_path,monkeypatch):
    import types
    from native_world_fixloop_state import NativeWorldState
    runner=runner_module();control,task,ledger=fixture(tmp_path);case=gate.load(control/'case.json')
    monkeypatch.setattr(runner,'original_context',lambda _: (case,None,Path(case['sim']),None,types.SimpleNamespace(verify_runtime=lambda *_:None),types.SimpleNamespace(identity=lambda *_:None)))
    def constructor(self,path,identity,resume=False):self.data=gate.load(path/'development_state.json');self.seeds=case['dev_seeds']
    monkeypatch.setattr(NativeWorldState,'__init__',constructor)
    current=gate.verify(control);review={'accepted':False,'reason':'specific source entity association wrong','semantic_failure':True,'native_worker':current['native_worker'],'native_session':current['native_session'],'ledger_sha256':gate.sha(task/'development_state.json'),'binding_sha256':current['binding_sha256']};write(control/'review.json',review)
    plan=runner.prepare(control,'auto',control/'review.json')
    assert plan['stage']=='initial_batch' and plan['before_counts']['51']==1 and plan['maximum_after_counts']['51']==1
    assert plan['maximum_after_counts']['52']==1
    with pytest.raises(gate.GateError,match='cannot jump'):runner.prepare(control,'repair',control/'review.json')


def test_initial_stage_settles_specific_other_seed_and_preserves_frozen_pair(tmp_path):
    import types
    runner=runner_module();control,task,ledger=fixture(tmp_path,3)
    ledger['trials']=ledger['trials'][:-1];write(task/'development_state.json',ledger)
    case=gate.load(control/'case.json');config=control/'config';case['claude_config_dir']=str(config);write(control/'case.json',case)
    child=config/'projects/project/same-session/subagents/agent-a0123456789.jsonl';child.parent.mkdir(parents=True);child.write_bytes((control/'child.jsonl').read_bytes())
    folder=control/'stage';folder.mkdir();write(folder/'ledger-before.json',{**ledger,'trials':ledger['trials'][:3]})
    transcript=folder/'stdout.jsonl';write(transcript,{'type':'system','subtype':'init','session_id':'same-session'})
    initial={k:gate.sha(task/n) for k,n in [('policy','initial_code.py'),('world','initial_world_program.py')]}
    plan={'stage':'initial_batch','phase':'initial','seeds':list(range(52,66)),'executions_to_add_maximum':14,'alias_seed':None,
          'native_worker':'a0123456789','native_session':'same-session','original_checkpoint_sha256':gate.sha(control/'native_smoke_state.json'),
          'ledger_before_sha256':gate.sha(folder/'ledger-before.json'),'failure_review_sha256':'a'*64,'initial_bundle':initial}
    cp=runner.settle(control,folder,plan,transcript,0,types.SimpleNamespace(identity=lambda *_:('same-session','a0123456789')))
    assert cp['selected_attempt']=='development/initial/seed_52/attempt_1' and cp['attempt_counts']['51']==2 and cp['attempt_counts']['52']==1
    assert gate.verify(control)['state']=='passed'
