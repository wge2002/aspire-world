"""Synthetic native-schema and artifact tests, never real experiment evidence."""
import copy,importlib.util,json,sys
from pathlib import Path
import pytest
SIM=Path(__file__).resolve().parents[1];DOC=SIM/'docs/experiments/code-world-runtime-p2-20261011';sys.path.insert(0,str(DOC));sys.path.insert(0,str(DOC/'support'))
import successor_lineage_r1 as lineage
import successor_guard_r1 as guard
import startup_gate_successor_r1 as startup
from test_startup_gate_recovery import fixture as original_fixture,make_attempt,write,reviewed

NEW='a123456789abcdef'

def transcript(path,prompt,worker,tool='dispatch'):
    path.parent.mkdir(parents=True,exist_ok=True)
    rows=[{'type':'system','subtype':'init','session_id':lineage.SESSION},
          {'type':'assistant','message':{'content':[{'type':'tool_use','name':'Agent','id':tool,'input':{'subagent_type':'general-purpose','prompt':prompt}}]}},
          {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':tool,'content':'agentId: '+worker}]}}]
    path.write_text(''.join(json.dumps(r)+'\n' for r in rows));return path

def fixture(tmp_path,monkeypatch):
    control,task,ledger=original_fixture(tmp_path)
    case=startup.load(control/'case.json');case.update(claude_config_dir=str(control/'config'));write(control/'case.json',case)
    old=startup.load(control/'native_smoke_state.json');old.update(native_session=lineage.SESSION,native_worker=lineage.PREDECESSOR)
    transcript(control/'root.jsonl',(control/'worker-prompt.md').read_text(),lineage.PREDECESSOR,'old')
    write(control/'native_smoke_state.json',old)
    # Original valid smoke alias and uniform initial bundle, using actual admission.
    from native_world_fixloop_state import NativeWorldState
    ledger['trials'][1]['sources']={}
    state=object.__new__(NativeWorldState);state.seeds=list(range(51,66));state.data=copy.deepcopy(ledger);state.save=lambda:None
    state.alias_smoke_as_initial(51);ledger=state.data
    for seed in range(52,66):ledger['trials'].append(make_attempt(task,'initial',seed,1,1))
    write(task/'development_state.json',ledger)
    folder=control/'native-successor-r1';folder.mkdir();baseline=folder/'ledger-before.json';write(baseline,ledger);monkeypatch.setattr(lineage,'LEDGER',startup.sha(baseline))
    packet={'native_worker':lineage.PREDECESSOR,'native_session':lineage.SESSION,'worker_request':'Own failed evidence only.'};write(folder/'diagnosis.json',packet)
    assignment=(control/'worker-prompt.md').read_text()+'Explicit successor\n'+json.dumps(packet,ensure_ascii=False)
    (folder/'assignment.md').write_text(assignment)
    write(folder/'approval.json',{'approved':True,'user_message':'Approved single-cell successor','scope':'Exactly one bowl_p2_r1 successor'})
    oldchild=control/'child.jsonl';refusal={'actual_child_delivery':{'delivered':False},'charged_added':0,'evidence_files':{str(oldchild.with_name('agent-'+lineage.PREDECESSOR+'.jsonl')):startup.sha(oldchild)}}
    oldchild.rename(oldchild.with_name('agent-'+lineage.PREDECESSOR+'.jsonl'));oldchild=oldchild.with_name('agent-'+lineage.PREDECESSOR+'.jsonl')
    old['actual_worker_prompt_receipt']['actual_child_transcript']=str(oldchild);write(control/'native_smoke_state.json',old)
    write(folder/'refusal.json',refusal);monkeypatch.setattr(lineage,'REFUSAL',startup.sha(folder/'refusal.json'))
    boot=folder/'bootstrap/stdout.jsonl';transcript(boot,assignment,NEW)
    child=Path(case['claude_config_dir'])/'projects/project'/lineage.SESSION/'subagents'/('agent-'+NEW+'.jsonl');write(child,{'type':'user','message':{'content':assignment}})
    snapshot=folder/'child-snapshot.jsonl';snapshot.write_bytes(child.read_bytes())
    transition={'cell':lineage.CELL,'native_session':lineage.SESSION,'predecessor':lineage.PREDECESSOR,'successor':NEW,
        'approval':str(folder/'approval.json'),'refusal':str(folder/'refusal.json'),'diagnosis':str(folder/'diagnosis.json'),
        'assignment':str(folder/'assignment.md'),'dispatch_tool_use_id':'dispatch','bootstrap_transcript':str(boot),
        'bootstrap_child_snapshot':str(snapshot),'predecessor_transcripts':[str(control/'root.jsonl')],
        'baseline_ledger':str(baseline),'baseline_ledger_sha256':startup.sha(baseline),
        'immutable_files':{str(f):startup.sha(f) for f in [folder/'approval.json',folder/'refusal.json',folder/'diagnosis.json',folder/'assignment.md',baseline,snapshot,boot,control/'root.jsonl']}}
    write(folder/'transition.json',transition)
    plan={'mode':'repair','stage':'repair','phase':'repair','seeds':[51],'alias_seed':None,'native_worker':NEW,
        'transition':str(folder/'transition.json'),'before_counts':{str(s):1 for s in range(51,66)},
        'maximum_after_counts':{str(s):1+int(s==51) for s in range(51,66)},'worker_stage_message':'ONE original repair51 only. No successor stage.'}
    return control,task,ledger,folder,boot,child,transition,plan

def payload(tool='Bash',**inp):return {'tool_name':tool,'session_id':lineage.SESSION,'tool_use_id':'one','tool_input':inp}

def test_bootstrap_exact_assignment_and_single_native_dispatch(tmp_path):
    control=tmp_path/'control';control.mkdir();write(control/'case.json',{'id':lineage.CELL});a=tmp_path/'assignment';a.write_text('frozen original\nexplicit handoff\n')
    state={};plan={'mode':'bootstrap','assignment':str(a)};original=payload('Agent',prompt='sentinel',subagent_type='general-purpose',run_in_background=True)
    effective,state=guard.decide(original,control,plan,state)
    assert effective['tool_input']['prompt']==a.read_text() and effective['tool_input']['run_in_background'] is False
    assert original['tool_input']['prompt']=='sentinel'
    with pytest.raises(ValueError,match='only one'):guard.decide(original,control,plan,state)

@pytest.mark.parametrize('mutation',[{'agent_id':'child'},{'tool_input':{'prompt':'x','subagent_type':'Explore'}},{'tool_input':{'prompt':'x','subagent_type':'general-purpose','resume':lineage.PREDECESSOR}},{'session_id':'other'}])
def test_bootstrap_denies_nested_foreign_role_resume_or_session(tmp_path,mutation):
    control=tmp_path/'control';write(control/'case.json',{'id':lineage.CELL});a=tmp_path/'a';a.write_text('assignment')
    p=payload('Agent',prompt='x',subagent_type='general-purpose');p.update(mutation)
    with pytest.raises(ValueError):guard.decide(p,control,{'mode':'bootstrap','assignment':str(a)},{})

@pytest.mark.parametrize('tool',['Bash','Write','Edit','MultiEdit','TaskStop','SendMessage'])
def test_read_only_bootstrap_has_no_trial_write_cancel_or_root_message(tmp_path,tool):
    control=tmp_path/'control';write(control/'case.json',{'id':lineage.CELL})
    with pytest.raises(ValueError):guard.decide(payload(tool,command='trial'),control,{'mode':'bootstrap'},{})

def test_both_real_native_segments_and_full_received_diagnosis_verified(tmp_path,monkeypatch):
    c,t,l,f,b,child,tr,plan=fixture(tmp_path,monkeypatch)
    audit=lineage.audit_transition(c,[c/'root.jsonl',b]);assert audit['explicit_successor'] and audit['primary_worker']==NEW and audit['predecessor_worker']==lineage.PREDECESSOR
    child.write_bytes(child.read_bytes()+b'{"type":"assistant","message":{"content":[]}}\n')
    assert lineage.audit_transition(c,[c/'root.jsonl',b])['ok']

@pytest.mark.parametrize('mutation',['approval','original_child','actual_new_input','diagnosis','ledger_prefix','second_Agent','drop_old','session'])
def test_transition_refuses_forgery_or_missing_native_evidence(tmp_path,monkeypatch,mutation):
    c,t,l,f,b,child,tr,plan=fixture(tmp_path,monkeypatch);paths=[c/'root.jsonl',b]
    if mutation=='approval':write(f/'approval.json',{'approved':False})
    elif mutation=='original_child':Path(next(iter(startup.load(f/'refusal.json')['evidence_files']))).write_text('rewritten cancelled context')
    elif mutation=='actual_new_input':write(child,{'type':'user','message':{'content':'summary instead of full assignment'}})
    elif mutation=='diagnosis':(f/'diagnosis.json').write_text('changed')
    elif mutation=='ledger_prefix':l['trials'][1]['spends_retry']=False;write(t/'development_state.json',l)
    elif mutation=='second_Agent':paths.append(transcript(f/'extra.jsonl','x','afffffffffffffff','duplicate'))
    elif mutation=='drop_old':paths=[b]
    else:paths.append(f/'foreign.jsonl');write(paths[-1],{'type':'system','subtype':'init','session_id':'foreign'})
    with pytest.raises(ValueError):lineage.audit_transition(c,paths)

@pytest.mark.parametrize('change',[{'seed':52},{'phase':'initial'},{'duplicate':True}])
def test_trial_boundary_reuses_original_stage_and_seed_envelope(tmp_path,monkeypatch,change):
    c,t,l,f,b,ch,tr,plan=fixture(tmp_path,monkeypatch);command='python native_world_protocol.py trial --phase repair --seed 51 --code fix.py --world-program fix_world.py'
    assert guard.decide(payload(command=command),c,plan,{})[0]['tool_input']['command']==command
    if 'seed' in change:command=command.replace('--seed 51','--seed 52')
    if 'phase' in change:command=command.replace('--phase repair','--phase initial')
    if 'duplicate' in change:command+='; '+command
    with pytest.raises(ValueError):guard.decide(payload(command=command),c,plan,{})

def test_stage_end_canonical_message_never_grants_successor_and_spent_trial_denied(tmp_path,monkeypatch):
    c,t,l,f,b,ch,tr,plan=fixture(tmp_path,monkeypatch);l['trials'].append(make_attempt(t,'repair',51,2,2));write(t/'development_state.json',l)
    p=payload('SendMessage',to=NEW,recipient=NEW,message='repair52–65 now',content='wrong')
    effective,_=guard.decide(p,c,plan,{})
    assert 'No successor stage' in effective['tool_input']['message'] and effective['tool_input']['content']==effective['tool_input']['message']
    with pytest.raises(ValueError):guard.decide(payload(command='python native_world_protocol.py trial --phase repair --seed 51'),c,plan,{})
    with pytest.raises(ValueError):guard.decide(payload('Write',file_path=str(t/'fix.py')),c,plan,{})

@pytest.mark.parametrize('tool',['Agent','Task','TaskStop'])
def test_after_receipt_no_second_worker_or_cancel_pause(tmp_path,monkeypatch,tool):
    c,t,l,f,b,ch,tr,plan=fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError):guard.decide(payload(tool),c,plan,{})

def test_actual_receipt_is_required_before_any_repair(tmp_path,monkeypatch):
    c,t,l,f,b,ch,tr,plan=fixture(tmp_path,monkeypatch);(f/'transition.json').unlink()
    with pytest.raises(OSError):guard.decide(payload(command='python native_world_protocol.py trial --phase repair --seed 51'),c,plan,{})

def test_startup_rebinds_full_explicit_lineage_and_rejects_stale_semantic_review(tmp_path,monkeypatch):
    c,t,l,f,b,ch,tr,plan=fixture(tmp_path,monkeypatch);l['trials'].append(make_attempt(t,'repair',51,2,2));write(t/'development_state.json',l)
    repair=f/'repair/stdout.jsonl';write(repair,{'type':'system','subtype':'init','session_id':lineage.SESSION})
    cp={'status':'native_gate_repair_checkpoint','cell':lineage.CELL,'native_session':lineage.SESSION,'native_worker':NEW,'coordinator_exit_code':0,
        'solver_transcripts':[str(c/'root.jsonl'),str(b),str(repair)],'successor_transition':str(f/'transition.json'),'successor_transition_sha256':startup.sha(f/'transition.json'),
        'original_checkpoint_sha256':startup.sha(c/'native_smoke_state.json'),'ledger_before':str(f/'ledger-before.json'),'ledger_before_sha256':startup.sha(f/'ledger-before.json'),
        'ledger_sha256':startup.sha(t/'development_state.json'),'selected_attempt':l['trials'][-1]['directory'],'selected_bundle':l['trials'][-1]['bundle'],
        'attempt_counts':{str(s):sum(r.get('spends_retry',False) and r['seed']==s for r in l['trials']) for s in range(51,66)}}
    write(c/'native_work_continuation_checkpoint.json',cp)
    result=startup.verify(c);assert result['state']=='passed',result
    saved=reviewed(result);assert startup.require_fresh_review(c,saved)['state']=='passed'
    # Appended actual worker context changes evidence binding, not lineage identity.
    ch.write_bytes(ch.read_bytes()+b'{"type":"assistant","message":{"content":[]}}\n')
    with pytest.raises(ValueError,match='stale'):startup.require_fresh_review(c,saved)
