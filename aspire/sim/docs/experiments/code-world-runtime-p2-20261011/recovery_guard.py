"""Bound recovery to one original next stage and SAME native primary."""
import argparse,json,re,shlex,sys
from pathlib import Path
from startup_gate import load,sha,require


def denial(payload,control,plan):
    tool=payload.get('tool_name');inp=payload.get('tool_input',{})
    if tool in {'Agent','Task'}:return 'Recovery resumes SAME primary with SendMessage; no new Agent/Task dispatch.'
    case=load(control/'case.json');task=(Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task']).resolve()
    if tool in {'Write','Edit','MultiEdit'}:
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

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--control',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);a=p.parse_args()
    try:reason=denial(json.load(sys.stdin),a.control,load(a.plan))
    except Exception as exc:reason=f'Recovery preflight failed closed: {exc}'
    if reason:print(reason,file=sys.stderr);raise SystemExit(2)
