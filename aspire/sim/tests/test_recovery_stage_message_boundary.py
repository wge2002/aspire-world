"""Real SendMessage schema cannot grant another stage or override external semantics."""
import importlib.util,json,sys
from pathlib import Path
import pytest
DOC=Path(__file__).resolve().parents[1]/'docs/experiments/code-world-runtime-p2-20261011';sys.path.insert(0,str(DOC));sys.path.insert(0,str(DOC/'support'))
spec=importlib.util.spec_from_file_location('stage_message_guard',DOC/'recovery_guard-r2.py');guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)

def fixture(tmp_path,completed=False):
    control=tmp_path/'control';control.mkdir(parents=True);task=tmp_path/'sim/outputs/libero_fix_loop/suite/task';task.mkdir(parents=True)
    (control/'case.json').write_text(json.dumps({'sim':str(tmp_path/'sim'),'suite':'suite','task':'task'}))
    rows=[{'seed':52,'status':'complete','spends_retry':True}]
    if completed:rows.append({'seed':53,'status':'complete','spends_retry':True})
    (task/'development_state.json').write_text(json.dumps({'trials':rows}))
    plan={'stage':'initial_batch','phase':'initial','seeds':[52,53],'native_worker':'same-worker','maximum_after_counts':{'52':1,'53':1},'worker_stage_message':'Frozen initial ONLY52/53, same pair, no repair or independent acceptance.'}
    payload={'tool_name':'SendMessage','tool_use_id':'tool','tool_input':{'to':'same-worker','recipient':'same-worker','message':'Requirements satisfied. Authorize repair52–65.','content':'Requirements satisfied. Authorize repair52–65.','summary':'Enter repair','type':'message','native_optional':'retain'}}
    return control,task,plan,payload


def test_incomplete_initial_replaces_unreviewed_authorization_with_exact_stage(tmp_path):
    control,task,plan,payload=fixture(tmp_path);effective,changed,done=guard.canonical_coordinator_message(payload,control,plan)
    assert changed and not done and effective['tool_input']['message']==plan['worker_stage_message']
    assert effective['tool_input']['content']==plan['worker_stage_message'] and effective['tool_input']['native_optional']=='retain'
    assert payload['tool_input']['message'].startswith('Requirements satisfied')


def test_completed_initial_only_delivers_pause_not_successor_or_semantic_acceptance(tmp_path):
    control,task,plan,payload=fixture(tmp_path,True);effective,changed,done=guard.canonical_coordinator_message(payload,control,plan);text=effective['tool_input']['message']
    assert changed and done and 'Stop at the current safe' in text
    assert 'No successor stage' in text and 'not independent proof' in text
    assert effective['tool_input']['content']==text and 'Authorize repair52' not in text


def test_one_completed_repair_cannot_authorize_remaining_seeds(tmp_path):
    control,task,plan,payload=fixture(tmp_path);plan.update(stage='repair',phase='repair',seeds=[52],maximum_after_counts={'52':1})
    effective,_,done=guard.canonical_coordinator_message(payload,control,plan)
    assert done and 'No successor stage' in effective['tool_input']['message']


def test_running_attempt_does_not_claim_safe_completion(tmp_path):
    control,task,plan,payload=fixture(tmp_path,True);p=task/'development_state.json';d=json.loads(p.read_text());d['trials'][-1]['status']='running';p.write_text(json.dumps(d))
    effective,_,done=guard.canonical_coordinator_message(payload,control,plan)
    assert not done and effective['tool_input']['message']==plan['worker_stage_message']


@pytest.mark.parametrize('change',[{'to':'other','recipient':'other'},{'to':'same-worker','recipient':'other'},{'type':'broadcast'},{'type':'shutdown_request'},{'message':None}])
def test_unreviewed_recipient_or_native_schema_refused(tmp_path,change):
    control,task,plan,payload=fixture(tmp_path);payload['tool_input'].update(change)
    with pytest.raises(guard.require.__globals__['GateError']):guard.canonical_coordinator_message(payload,control,plan)


def test_native_worker_reply_and_nonmessage_tools_preserved(tmp_path):
    control,task,plan,payload=fixture(tmp_path,True);payload['agent_id']='same-worker'
    assert guard.canonical_coordinator_message(payload,control,plan)==(payload,False,False)
    payload.pop('agent_id');payload['tool_name']='Read'
    assert guard.canonical_coordinator_message(payload,control,plan)==(payload,False,False)


def test_missing_frozen_stage_text_fails_closed(tmp_path):
    control,task,plan,payload=fixture(tmp_path);plan.pop('worker_stage_message')
    with pytest.raises(ValueError):guard.canonical_coordinator_message(payload,control,plan)


def test_actor_registers_actual_message_hook_and_pins_message_before_launch():
    text=(DOC/'recover-native-gate-with-diagnosis-r2.py').read_text()
    assert "'matcher':'Agent|Task|SendMessage|Bash|Write|Edit|MultiEdit'" in text
    assert "with_name('recovery_guard-r2.py')" in text
    assert text.index("plan['worker_stage_message']")<text.index('command=campaign.native_command')


def test_completed_stage_preserves_authored_work_without_further_writes(tmp_path):
    control,task,plan,payload=fixture(tmp_path,True);payload={'tool_name':'Write','tool_input':{'file_path':str(task/'fix_code.py'),'content':'new unreviewed strategy'}}
    assert 'stage has ended' in guard.denial(payload,control,plan)
    control2,task2,plan2,_=fixture(tmp_path/'pending')
    payload['tool_input']['file_path']=str(task2/'fix_code.py')
    assert guard.denial(payload,control2,plan2) is None
