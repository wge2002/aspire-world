"""One approved read-only bootstrap; trials require actual successor receipt."""
import argparse,copy,fcntl,importlib.util,json,sys
from pathlib import Path
from startup_gate import load,sha,require
from successor_lineage_r1 import audit_transition,SESSION,CELL

def r2():
    spec=importlib.util.spec_from_file_location('successor_frozen_r2_guard',Path(__file__).with_name('recovery_guard-r2.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def decide(payload,control,plan,state):
    require(load(control/'case.json')['id']==CELL and payload.get('session_id')==SESSION,'successor hook cell/session mismatch')
    sys.path.insert(0,str(control.parent/'support'))
    from pilot_assignment_guard import is_subagent
    tool=payload.get('tool_name');inp=payload.get('tool_input',{});inside=is_subagent(payload);effective=copy.deepcopy(payload)
    if plan['mode']=='bootstrap':
        if tool in {'Agent','Task'}:
            require(not inside and not state.get('dispatch'),'only one root successor dispatch is approved')
            require(inp.get('subagent_type')=='general-purpose' and not inp.get('resume'),'bootstrap cannot resume cancelled worker or select another role')
            require(inp.get('model') in {None,'inherit','qwen3.8-flash-next'},'successor model differs')
            require(payload.get('tool_use_id') and isinstance(inp.get('prompt'),str),'native dispatch identity/prompt missing')
            effective['tool_input']['prompt']=Path(plan['assignment']).read_text()
            effective['tool_input']['run_in_background']=False
            state['dispatch']={'tool_use_id':payload['tool_use_id'],'session_id':SESSION}
        elif tool in {'Bash','Write','Edit','MultiEdit','TaskStop'}:
            raise ValueError('Read-only successor bootstrap: no Bash, source edits, trials or stop_task/TaskStop')
        elif tool=='SendMessage':
            require(inside,'bootstrap coordinator must wait for normal completion, not send stage instructions')
        return effective,state
    require(plan['mode'] in {'repair','full'},'undeclared successor mode')
    receipt=load(plan['transition']);audit=audit_transition(control,[Path(p) for p in receipt['predecessor_transcripts']]+[Path(receipt['bootstrap_transcript'])],Path(plan['transition']))
    require(plan['native_worker']==audit['primary_worker'],'stage does not address confirmed successor')
    if tool in {'Agent','Task','TaskStop'}:raise ValueError('Approved successor already exists; no additional worker or cancellation as pause')
    if payload.get('agent_id'):require(payload['agent_id']==plan['native_worker'],'tool came from an undeclared agent')
    if tool=='SendMessage' and not inside:
        if plan['mode']=='repair':effective,_,_=r2().canonical_coordinator_message(payload,control,plan)
        else:
            recipients=[inp.get(k) for k in ('to','recipient') if inp.get(k)]
            require(recipients and all(x==plan['native_worker'] for x in recipients) and inp.get('type','message')=='message','full continuation must address only approved successor')
            text=plan.get('worker_stage_message');require(isinstance(text,str) and text,'full protocol-only continuation missing')
            effective['tool_input']['message']=text
            if 'content' in inp:effective['tool_input']['content']=text
    if plan['mode']=='repair':
        reason=r2().denial(effective,control,plan);require(not reason,reason or '')
    return effective,state

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--control',type=Path,required=True);parser.add_argument('--plan',type=Path,required=True);args=parser.parse_args()
    plan=load(args.plan);folder=args.plan.parent;payload=json.load(sys.stdin);state_path=folder/'dispatch-state.json'
    with (folder/'guard.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=load(state_path) if state_path.exists() else {}
        try:
            effective,state=decide(payload,args.control,plan,state)
            temporary=state_path.with_suffix('.tmp');temporary.write_text(json.dumps(state)+'\n');temporary.replace(state_path)
            record={'tool':payload.get('tool_name'),'tool_use_id':payload.get('tool_use_id'),'session_id':payload.get('session_id'),
                    'mode':plan['mode'],'denied':False,'proposed_input':payload.get('tool_input'),'effective_input':effective.get('tool_input'),
                    'canonicalized_primary':plan['mode']=='bootstrap' and payload.get('tool_name') in {'Agent','Task'},'primary_recorded':bool(state.get('dispatch'))}
            audit_name='assignment-audit.jsonl' if record['canonicalized_primary'] else 'stage-message-audit.jsonl'
            with (folder/audit_name).open('a') as out:out.write(json.dumps(record,ensure_ascii=False)+'\n')
            if effective['tool_input']!=payload['tool_input']:print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','updatedInput':effective['tool_input']}}))
            return 0
        except Exception as exc:
            with (folder/'denials.jsonl').open('a') as out:out.write(json.dumps({'tool':payload.get('tool_name'),'tool_use_id':payload.get('tool_use_id'),'reason':str(exc)})+'\n')
            print('Successor boundary refused: '+str(exc),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
