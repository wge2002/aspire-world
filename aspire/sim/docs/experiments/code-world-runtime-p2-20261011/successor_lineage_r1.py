"""Explicit, approved predecessor→successor lineage; never relabel a worker."""
import importlib.util,json,re,sys
from pathlib import Path
from startup_gate import load,sha,require

CELL='bowl_p2_r1'
SESSION='2a9013f4-9ef8-4389-96bc-696b630569fe'
PREDECESSOR='a86c9bbd4d4bff01e'
LEDGER='167a6875c1e236256f97b4d6243eb901983d7f5f22252488e8204f737e16983b'
REFUSAL='965a725f5faa6e49176c643c30f78fa3916cb247dabe99618baf2d9a112e7557'

def frozen(control):
    sys.path.insert(0,str(control.parent/'support'))
    import lineage
    return lineage

def events(paths):
    for path in paths:
        for line in Path(path).read_text().splitlines():
            yield json.loads(line)

def native_successor(paths,session):
    """One real root Agent call plus its native agentId result, same session."""
    calls={};results={}
    for event in events(paths):
        require(event.get('session_id',session)==session,'successor invocation changed native session')
        if event.get('parent_tool_use_id'):continue
        for block in event.get('message',{}).get('content',[]):
            if not isinstance(block,dict):continue
            if block.get('type')=='tool_use' and block.get('name') in {'Agent','Task'}:
                require(block['input'].get('subagent_type')=='general-purpose' and not block['input'].get('resume'),'successor must be an explicit fresh general-purpose dispatch')
                calls[block['id']]=block
            if block.get('type')=='tool_result' and not block.get('is_error'):
                content=block.get('content','')
                if isinstance(content,list):content='\n'.join(b.get('text','') for b in content if isinstance(b,dict))
                ids=set(re.findall(r'agentId:\s*(a[0-9a-f]{8,})',str(content)))
                if len(ids)==1:results[block['tool_use_id']]=ids.pop()
    require(len(calls)==1 and set(calls).issubset(results),'need exactly one confirmed actual successor dispatch')
    tool_id=next(iter(calls));worker=results[tool_id]
    require(worker!=PREDECESSOR,'successor cannot reuse predecessor identity')
    return worker,tool_id

def audit_transition(control,paths,receipt_path=None):
    receipt_path=Path(receipt_path) if receipt_path else control/'native-successor-r1/transition.json'
    receipt=load(receipt_path);case=load(control/'case.json')
    require(case['id']==CELL and receipt['cell']==CELL and receipt['native_session']==SESSION
            and receipt['predecessor']==PREDECESSOR and receipt['successor']!=PREDECESSOR,'unapproved cell/session/identity transition')
    for filename,expected in receipt['immutable_files'].items():require(sha(filename)==expected,'successor immutable evidence changed: '+filename)
    baseline=load(receipt['baseline_ledger']);require(sha(receipt['baseline_ledger'])==LEDGER==receipt['baseline_ledger_sha256'],'successor baseline is not the approved unchanged ledger')
    charged=[r for r in baseline['trials'] if r.get('spends_retry')]
    require(len(charged)==15 and all(sum(r['seed']==s for r in charged)==1 for s in range(51,66)),'successor must retain all original 15 charges')
    task=Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task'];current=load(task/'development_state.json')
    require(current['trials'][:len(baseline['trials'])]==baseline['trials'],'successor changed/refunded predecessor trial history')
    approval=load(receipt['approval']);require(approval.get('approved') is True and approval.get('user_message') and CELL in approval.get('scope',''),'explicit human successor approval missing')
    refusal=load(receipt['refusal']);require(sha(receipt['refusal'])==REFUSAL and refusal['actual_child_delivery']['delivered'] is False and refusal['charged_added']==0,'actual predecessor refusal missing')
    old_children={p:h for p,h in refusal['evidence_files'].items() if f'agent-{PREDECESSOR}.jsonl' in p}
    require(len(old_children)==1 and all(sha(p)==h for p,h in old_children.items()),'cancelled predecessor transcript was modified')
    lineage=frozen(control);old=[Path(p) for p in receipt['predecessor_transcripts']]
    require(all(Path(p) in [Path(x) for x in paths] for p in old),'transition dropped predecessor transcripts')
    old_audit=lineage.audit_assignment(old,(control/'worker-prompt.md').read_text())
    require(old_audit['ok'] and old_audit['primary_worker']==PREDECESSOR and [s['session_id'] for s in old_audit['sessions']]==[SESSION],'predecessor lineage no longer valid')
    new=[Path(p) for p in paths if Path(p) not in old]
    require(new and Path(receipt['bootstrap_transcript']) in new,'actual successor bootstrap transcript missing')
    worker,tool_id=native_successor(new,SESSION)
    require((worker,tool_id)==(receipt['successor'],receipt['dispatch_tool_use_id']),'undeclared successor or duplicate dispatch')
    new_audit=lineage.audit_assignment(new,Path(receipt['assignment']).read_text())
    require(new_audit['ok'] and new_audit['primary_worker']==worker and [s['session_id'] for s in new_audit['sessions']]==[SESSION],'successor segment lineage invalid')
    prompt_receipt=lineage.verify_received_assignment(Path(case['claude_config_dir']),SESSION,worker,Path(receipt['assignment']))
    child=Path(prompt_receipt['actual_child_transcript'])
    require(child.read_bytes().startswith(Path(receipt['bootstrap_child_snapshot']).read_bytes()),'successor discarded/rewrote its received bootstrap context')
    require(Path(receipt['assignment']).read_bytes().startswith((control/'worker-prompt.md').read_bytes()),'frozen original assignment prefix changed')
    packet=load(receipt['diagnosis']);require(json.dumps(packet,ensure_ascii=False) in Path(receipt['assignment']).read_text(),'complete diagnosis missing from actual verified assignment')
    return {'ok':True,'native_session':SESSION,'primary_worker':worker,'predecessor_worker':PREDECESSOR,
            'explicit_successor':True,'transition':str(receipt_path),'transition_sha256':sha(receipt_path),
            'predecessor_audit':old_audit,'successor_audit':new_audit,'actual_worker_prompt_receipt':prompt_receipt}

def verify_checkpoint(control,checkpoint):
    cp=load(checkpoint);require(cp['native_session']==SESSION and cp['cell']==CELL,'successor checkpoint belongs to another cell/session')
    audit=audit_transition(control,[Path(p) for p in cp['solver_transcripts']],cp['successor_transition'])
    require(cp['native_worker']==audit['primary_worker'] and cp['successor_transition_sha256']==audit['transition_sha256'],'checkpoint lost explicit successor binding')
    return audit
