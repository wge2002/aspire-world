"""Bound recovery to one original next stage and SAME native primary."""
import argparse,copy,json,re,shlex,sys
from pathlib import Path
from startup_gate import load,sha,require


def denial(payload,control,plan):
    tool=payload.get('tool_name');inp=payload.get('tool_input',{})
    if tool in {'Agent','Task'}:return 'Recovery resumes SAME primary with SendMessage; no new Agent/Task dispatch.'
    case=load(control/'case.json');task=(Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task']).resolve()
    if tool in {'Write','Edit','MultiEdit'}:
        if declared_stage_finished(load(task/'development_state.json'),plan):return 'The declared stage has ended; preserve authored work and pause, no further strategy editing in this invocation.'
        target=Path(inp.get('file_path','')).resolve()
        if (not target.is_relative_to(task) or target.name in {'initial_code.py','initial_world_program.py','development_state.json'}
                or target.is_relative_to(task/'development')):
            return 'Preserve original initial, ledger and executed snapshots; author a distinct revision only.'
    if tool!='Bash':return None
    command=inp.get('command','')
    if any(x in command for x in ['replay_trial.py','native_world_heldout','--args.interactive','scene_snapshot.py']):return 'Recovery must use the ordinary recorded runtime protocol; no direct replay or snapshot.'
    if 'native_world_protocol' not in command:return None
    if re.search(r'\b(?:init|snapshot|select|finalize)\b',command):return 'Recovery cannot reset, snapshot, select, finalize or close unreviewed infrastructure.'
    if 'close-infra' in command:
        if plan.get('stage')!='close_infra' or command.count('native_world_protocol')!=1:return 'Only a declared original infrastructure disposition is permitted.'
        try:
            tokens=shlex.split(command);directory=tokens[tokens.index('--directory')+1]
            if tokens.count('--directory')!=1 or directory not in plan['directories']:return 'Infrastructure disposition must name a declared ended record.'
        except (ValueError,IndexError):return 'Cannot establish original infrastructure record.'
        return None
    if plan.get('stage')=='close_infra' and re.search(r'\b(?:trial|alias-smoke)\b',command):return 'Infrastructure disposition grants no trial or alias.'
    trial=bool(re.search(r'\btrial\b',command));alias='alias-smoke' in command
    if not trial and not alias:return None
    if command.count('native_world_protocol')!=1 or (trial and alias):return 'One recorded protocol invocation per command.'
    try:tokens=shlex.split(command)
    except ValueError:return 'Cannot establish recovery trial arguments.'
    def value(flag):return tokens[tokens.index(flag)+1] if tokens.count(flag)==1 and tokens.index(flag)+1<len(tokens) else None
    try:seed=int(value('--seed'))
    except (TypeError,ValueError):return 'Cannot establish declared development seed.'
    ledger=load(task/'development_state.json')
    if any(r.get('status')=='running' for r in ledger['trials']):return 'A trial is already running.'
    if alias:
        if seed!=plan.get('alias_seed') or any(r.get('alias_of') and r['seed']==seed for r in ledger['trials']):return 'Only the declared eligible original smoke alias is allowed once.'
        return None
    counts=sum(r.get('spends_retry',False) and r['seed']==seed for r in ledger['trials'])
    if seed not in plan['seeds'] or value('--phase')!=plan['phase']:return 'Trial must use a declared seed and the next original phase.'
    if counts!=plan['before_counts'][str(seed)] or counts>=3:return 'This seed already executed in this stage, or its three-attempt budget is exhausted.'
    if tokens.count('trial')!=1:return 'One recorded protocol trial per invocation.'
    return None

def declared_stage_finished(ledger,plan):
    if any(r['status']=='running' for r in ledger['trials']):return False
    if plan['stage']=='close_infra':
        return not any(r['directory'] in plan['directories'] and r.get('recovery',{}).get('disposition')!='closed_without_usable_evidence' for r in ledger['trials'])
    return all(sum(r.get('spends_retry',False) and r['seed']==seed for r in ledger['trials'])==plan['maximum_after_counts'][str(seed)] for seed in plan['seeds'])


def canonical_coordinator_message(payload,control,plan):
    """Coordinator messages cannot grant a successor stage; worker replies stay native."""
    if payload.get('tool_name')!='SendMessage':return payload,False,False
    sys.path.insert(0,str(control.parent/'support'))
    from pilot_assignment_guard import is_subagent
    if is_subagent(payload):return payload,False,False
    inp=payload.get('tool_input',{})
    require(inp.get('type','message')=='message','Stage coordinator cannot broadcast, shut down or replace the existing primary')
    recipients=[inp.get(k) for k in ('to','recipient') if inp.get(k)]
    require(recipients and all(r==plan['native_worker'] for r in recipients),'Stage message must address ONLY the same existing primary')
    require(isinstance(inp.get('message'),str),'Actual native SendMessage schema requires message text')
    case=load(control/'case.json');task=Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task']
    ledger=load(task/'development_state.json')
    finished=declared_stage_finished(ledger,plan)
    if finished:
        text=(f"The declared {plan['stage']} stage is complete. Stop at the current safe no-trial boundary and return this stage's actual attempt report. "
              "Preserve all authored files and original charged records. No successor stage, new trial, strategy editing, snapshot, selection, finalization, new worker or refund is authorized in this invocation. "
              "Automatic action_evidence is declared bookkeeping, not independent proof that prediction feedback drove an action. Semantic/source acceptance is performed outside this native coordinator. "
              "The outer wrapper must settle this stage and bind independent review before any same-worker continuation. Pause and report now; do not execute more work.")
    else:
        text=plan.get('worker_stage_message')
        require(isinstance(text,str) and text,'Frozen worker-stage message missing; cannot infer a replacement authorization')
    effective=copy.deepcopy(payload);effective['tool_input']['message']=text
    if 'content' in inp:effective['tool_input']['content']=text
    if 'summary' in inp:effective['tool_input']['summary']='Pause completed declared stage' if finished else 'Continue ONLY the declared stage'
    return effective,True,finished


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--control',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);a=p.parse_args()
    try:
        payload=json.load(sys.stdin);plan=load(a.plan)
        effective,changed,finished=canonical_coordinator_message(payload,a.control,plan)
        reason=denial(effective,a.control,plan)
        if changed:
            record={'tool_use_id':payload.get('tool_use_id'),'session_id':payload.get('session_id'),'stage':plan['stage'],'stage_finished':finished,'proposed_input':payload['tool_input'],'effective_input':effective['tool_input'],'denied':bool(reason)}
            with (a.plan.parent/'stage-message-audit.jsonl').open('a') as out:out.write(json.dumps(record)+'\n')
            if not reason:print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','updatedInput':effective['tool_input']}}))
    except Exception as exc:reason=f'Recovery preflight failed closed: {exc}'
    if reason:print(reason,file=sys.stderr);raise SystemExit(2)
