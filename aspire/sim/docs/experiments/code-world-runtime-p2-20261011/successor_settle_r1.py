"""Original bounded settlement plus explicit approved two-segment lineage."""
from pathlib import Path
import json
from startup_gate import load,sha,require,original_checkpoint,validate_history
from successor_lineage_r1 import PREDECESSOR

def settle(control, folder, plan, transcript, code, driver):
    require(code==0,'native repair did not end safely; retain its record for recovery')
    original_path,original=original_checkpoint(control);case=load(control/'case.json')
    task=Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task'];ledger=load(task/'development_state.json')
    before=load(folder/'ledger-before.json')
    require(sha(folder/'ledger-before.json')==plan['ledger_before_sha256'],'recovery baseline ledger changed')
    if plan['stage']=='close_infra':
        require(len(ledger['trials'])==len(before['trials']),'infra disposition must not add executions')
        for old,new in zip(before['trials'],ledger['trials']):
            if old['directory'] in plan['directories']:
                require(all(new.get(k)==v for k,v in old.items() if k not in {'recovery','error'}),'infra closure altered original attempt evidence')
                require(new.get('status')=='infrastructure_error' and new.get('recovery',{}).get('disposition')=='closed_without_usable_evidence'
                        and new['recovery'].get('attempts_refunded')==0 and new['recovery'].get('executions_added')==0,'infra closure faked grading or refunded an attempt')
            else:require(old==new,'infra closure altered unrelated history')
        previous=load(control/'native_work_continuation_checkpoint.json') if (control/'native_work_continuation_checkpoint.json').exists() else original
        transcripts=[Path(p) for p in plan['predecessor_transcripts']] + [Path(plan['bootstrap_transcript'])]+[transcript]
        require(driver.identity(transcripts,(control/'worker-prompt.md').read_text())==(plan['native_session'],plan['native_worker']),'infra closure changed native identity')
        receipt={'status':'closed_infrastructure_stage','executions_added':0,'attempts_refunded':0,'ledger_sha256':sha(task/'development_state.json'),'solver_transcripts':[str(p) for p in transcripts]}
        (folder/'checkpoint.json').write_text(json.dumps(receipt,indent=2)+'\n')
        return receipt
    require(ledger['trials'][:len(before['trials'])]==before['trials'],'recovery altered prior history')
    validate_history(case,ledger)
    added=ledger['trials'][len(before['trials']):];executions=[r for r in added if r.get('spends_retry')]
    aliases=[r for r in added if r.get('alias_of')]
    require(len(aliases)<=int(plan.get('alias_seed') is not None) and all(r['seed']==plan['alias_seed'] for r in aliases), 'undeclared smoke alias')
    require(executions and len(executions)<=plan['executions_to_add_maximum'] and
            len({r['seed'] for r in executions})==len(executions) and
            all(r['seed'] in plan['seeds'] and r['phase']==plan['phase'] and r['status']!='running' for r in executions),
            'recovery exceeded its normal stage/seed execution envelope')
    require(len(added)==len(executions)+len(aliases),'unaccounted recovery record')
    require(all(sum(r.get('spends_retry',False) and r['seed']==seed for r in ledger['trials'])<=3 for seed in case['dev_seeds']), 'original three-attempt budget exceeded')
    # Bind a specific actually completed candidate from this stage. Prefer real
    # mechanism evidence, and retain a failed receipt if none yet qualifies.
    candidates=[r for r in executions if r['status']=='complete']
    require(candidates,'infrastructure_unresolved: no completed candidate in this stage; preserve its charged record')
    from startup_gate import attempt_sources
    selected=candidates[-1]
    for candidate in candidates:
        directory,_,_=attempt_sources(task,candidate)
        if load(directory/'judgment_world/manifest.json').get('runtime_mechanism',{}).get('status')=='action_evidence':selected=candidate;break
    require({k:sha(task/n) for k,n in [('policy','initial_code.py'),('world','initial_world_program.py')]}==plan['initial_bundle'],'original initial pair changed')
    previous=load(control/'native_work_continuation_checkpoint.json') if (control/'native_work_continuation_checkpoint.json').exists() else original
    transcripts=[Path(p) for p in plan['predecessor_transcripts']] + [Path(plan['bootstrap_transcript'])]
    for p in sorted((control/'native-gate-recovery').glob('stage-close_infra-*/checkpoint.json')):
        for t in load(p)['solver_transcripts']:
            if Path(t) not in transcripts:transcripts.append(Path(t))
    transcripts.append(transcript)
    from successor_lineage_r1 import audit_transition
    audit=audit_transition(control,transcripts,Path(plan['transition']))
    session,worker=audit['native_session'],audit['primary_worker']
    require((session,worker)==(plan['native_session'],plan['native_worker']),'approved successor identity changed')
    prompt_receipt=audit['actual_worker_prompt_receipt']
    cp={'status':'native_gate_repair_checkpoint','cell':case['id'],'native_session':session,'native_worker':worker,
        'coordinator_exit_code':0,'solver_transcripts':[str(p) for p in transcripts],
        'original_checkpoint_sha256':plan['original_checkpoint_sha256'],'ledger_before':str(folder/'ledger-before.json'),
        'ledger_before_sha256':sha(folder/'ledger-before.json'),'ledger_sha256':sha(task/'development_state.json'),
        'selected_attempt':selected['directory'],'selected_bundle':selected['bundle'],
        'attempt_counts':{str(seed):sum(r.get('spends_retry',False) and r['seed']==seed for r in ledger['trials']) for seed in case['dev_seeds']},'stage':plan['stage'],'actual_worker_prompt_receipt':prompt_receipt,
        'failure_review_sha256':plan['failure_review_sha256'],'initial_bundle':plan['initial_bundle'],
        'original_checkpoint_preserved':True,'attempts_refunded':0,
        'successor_transition':plan['transition'],'successor_transition_sha256':sha(plan['transition']),
        'predecessor_native_worker':PREDECESSOR,'identity_change_disclosed':True}
    (folder/'checkpoint.json').write_text(json.dumps(cp,indent=2)+'\n')
    pointer=control/'native_work_continuation_checkpoint.json'
    if pointer.exists():
        (folder/'previous-continuation-checkpoint.json').write_bytes(pointer.read_bytes())
    tmp=pointer.with_suffix('.tmp');tmp.write_text(json.dumps(cp,indent=2)+'\n');tmp.replace(pointer)
    return cp

