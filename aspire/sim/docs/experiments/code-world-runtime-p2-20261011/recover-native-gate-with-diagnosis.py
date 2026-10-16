"""Prepare/run the next bounded ORIGINAL stage only after a safe first pause.

No original runtime/support/checkpoint/initial source is rewritten. Original
NativeWorldState admission is authoritative; a protocol-order refusal is a
reported next-stage/terminal decision, never a hidden bypass or an extra replay. --prepare is dry.
"""
import argparse,fcntl,importlib.util,json,os,shlex,sys
from pathlib import Path
from startup_gate import sha,load,stable,require,GateError,verify,original_checkpoint,validate_history


def original_context(control):
    case=load(control/'case.json');support=control.parent/'support';repo=Path(case['sim'])
    sys.path.insert(0,str(support));sys.path.insert(0,str(repo/'scripts/libero'));sys.path.insert(0,case['python_root'])
    spec=importlib.util.spec_from_file_location('frozen_startup_driver',support/'run_two_task_cell.py')
    driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
    import native_world_campaign as campaign
    driver.scope.apply(campaign,case)
    import native_world_protocol as protocol
    return case,support,repo,driver,campaign,protocol


def capture_first_pause(control, driver=None):
    """Capture ended code0 startup with lost infra, preserving blocked original."""
    source=control/'native_smoke_state.json';original=load(source)
    if original.get('status')=='native_smoke_checkpoint':return source,original
    target=control/'native_gate_first_pause.json'
    if target.exists():return original_checkpoint(control)
    require(original.get('status')=='blocked' and original.get('coordinator_exit_code')==0,'first coordinator is not safely ended at code0')
    case=load(control/'case.json');task=Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task'];ledger=load(task/'development_state.json')
    charged=[r for r in ledger['trials'] if r.get('spends_retry')]
    require(len(charged)==1 and charged[0]['phase']=='smoke' and charged[0]['seed']==case['dev_seeds'][0] and charged[0]['status']!='running','first pause must contain exactly one ended original smoke')
    require(not any(r['status']=='running' or (r['phase']=='snapshot' and r['status']!='complete') for r in ledger['trials']),'running/lost snapshot must use original outer recovery')
    require({k:sha(task/n) for k,n in [('policy','initial_code.py'),('world','initial_world_program.py')]}==charged[0]['bundle'],'first input bundle changed')
    if driver is None:driver=original_context(control)[3]
    transcripts=sorted(Path(original['campaign_dir']).glob('turn-*.stdout.jsonl'))
    require(transcripts,'actual original native transcript missing')
    session,worker=driver.identity(transcripts,(control/'worker-prompt.md').read_text())
    from lineage import verify_received_assignment
    receipt=verify_received_assignment(Path(case['claude_config_dir']),session,worker,control/'worker-prompt.md')
    captured={**original,'status':'native_smoke_checkpoint','native_session':session,'native_worker':worker,
              'solver_transcripts':[str(p) for p in transcripts],'actual_worker_prompt_receipt':receipt,
              'smoke_attempt':charged[0],'source_state_sha256':sha(source),'source_state':str(source),
              'capture_reason':'ended first startup; original blocked state and every charged attempt retained'}
    with target.open('x') as f:f.write(json.dumps(captured,indent=2)+'\n')
    return target,captured


def next_stage(state):
    """Select the next ORIGINAL protocol stage; admission remains authoritative."""
    require(not state.data.get('stage1_complete') and not state.data.get('selected'),'cell already selected/frozen')
    require(not any(r['status']=='running' for r in state.data['trials']),
            'infrastructure_unresolved: settle the ended attempt through original recovery; no new trial yet')
    ended=[r for r in state.data['trials'] if r['status']=='infrastructure_error' and r.get('recovery',{}).get('disposition')!='closed_without_usable_evidence']
    if ended:
        require(all(r['phase']!='snapshot' for r in ended),'terminal_snapshot: the original single snapshot cannot be replaced')
        return {'stage':'close_infra','phase':None,'seeds':[],'alias_seed':None,'initial_batch_bundle':state.initial_bundle(),'directories':[r['directory'] for r in ended]}
    first=state.seeds[0];alias=None
    smokes=[r for r in state.records('smoke',first) if r.get('status')=='complete' and r.get('sandbox_rc')==0]
    if not state.has_initial_evidence(first) and smokes and (state.initial_bundle() in {None,smokes[-1]['bundle_sha256']}):
        alias=first
    pending=[seed for seed in state.seeds if not state.has_initial_evidence(seed) and seed!=alias]
    runnable=[seed for seed in pending if state.retries_remaining(seed)>0]
    if runnable:
        digest=state.initial_bundle() or (smokes[-1]['bundle_sha256'] if alias is not None else state.retries(first)[-1]['bundle_sha256'])
        for seed in runnable:state.validate_admission('initial',seed,digest)
        return {'stage':'initial_batch','phase':'initial','seeds':runnable,'alias_seed':alias,'initial_batch_bundle':digest if state.initial_bundle() or alias is not None else None}
    # Test repairs after a legitimate alias on a read-only shadow state.
    import copy
    shadow=copy.copy(state);shadow.data=copy.deepcopy(state.data);shadow.save=lambda:None
    if alias is not None:shadow.alias_smoke_as_initial(alias)
    candidates=[seed for seed in shadow.seeds if shadow.retries_remaining(seed)>0]
    require(candidates,'budget_exhausted: no development attempts remain; preserve all charged history')
    seed=candidates[0]
    try:shadow.validate_admission('repair',seed,shadow.initial_bundle() or '')
    except Exception as exc:raise GateError(f'original protocol terminal/admission blocker: {exc}') from exc
    return {'stage':'repair','phase':'repair','seeds':[seed],'alias_seed':alias,'initial_batch_bundle':shadow.initial_bundle()}


def prepare(control, phase, failure_review):
    case,support,repo,driver,campaign,protocol=original_context(control)
    original_path,original=original_checkpoint(control)
    require(original.get('status')=='native_smoke_checkpoint' and original.get('coordinator_exit_code')==0,'first smoke has not safely paused')
    campaign.verify_runtime(case,repo)
    task=repo/'outputs/libero_fix_loop'/case['suite']/case['task']
    ledger=load(task/'development_state.json');charged=[r for r in ledger['trials'] if r.get('spends_retry')]
    require(charged,'first smoke has not executed')
    require(not (control/'campaign_state.json').exists(),'DLC/full driver already started')
    validate_history(case,ledger)
    gate=verify(control);review=load(failure_review)
    require(review.get('accepted') is False and review.get('reason') and review.get('native_worker')==original['native_worker']
            and review.get('native_session')==original['native_session'] and review.get('ledger_sha256')==sha(task/'development_state.json'),
            'recovery requires a current explicit failure review bound to SAME worker and ledger')
    if gate['state']=='passed':
        require(review.get('binding_sha256')==gate['binding_sha256'],'semantic failure review is stale')
        require(review.get('semantic_failure') is True,'passed mechanism requires a specific semantic failure, never extra trials')
    from native_world_fixloop_state import NativeWorldState
    state=NativeWorldState(task,protocol.identity(case,repo),resume=True)
    stage=next_stage(state)
    require(phase in {'auto',stage['phase']},f"next legal stage is {stage['stage']}; cannot jump to {phase}")
    initial={k:sha(task/n) for k,n in [('policy','initial_code.py'),('world','initial_world_program.py')]}
    require(initial==charged[0]['bundle'],'original initial files were overwritten')
    counts={str(seed):sum(r['seed']==seed for r in charged) for seed in case['dev_seeds']}
    return {'cell':case['id'],'native_session':original['native_session'],'native_worker':original['native_worker'],**stage,
            'before_counts':counts,'maximum_after_counts':{s:n+int(int(s) in stage['seeds']) for s,n in counts.items()},
            'ledger_before_sha256':sha(task/'development_state.json'),'original_checkpoint_sha256':sha(original_path),
            'initial_bundle':initial,'failure_review':str(failure_review),'failure_review_sha256':sha(failure_review),
            'executions_to_add_maximum':len(stage['seeds']),'may_pause_early_on_mechanism':True}


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
        transcripts=[Path(p) for p in previous['solver_transcripts']]+[transcript]
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
    transcripts=[Path(p) for p in previous['solver_transcripts']]
    for p in sorted((control/'native-gate-recovery').glob('stage-close_infra-*/checkpoint.json')):
        for t in load(p)['solver_transcripts']:
            if Path(t) not in transcripts:transcripts.append(Path(t))
    transcripts.append(transcript)
    session,worker=driver.identity(transcripts,(control/'worker-prompt.md').read_text())
    require((session,worker)==(plan['native_session'],plan['native_worker']),'native worker changed')
    from lineage import verify_received_assignment
    prompt_receipt=verify_received_assignment(Path(case['claude_config_dir']),session,worker,control/'worker-prompt.md')
    cp={'status':'native_gate_repair_checkpoint','cell':case['id'],'native_session':session,'native_worker':worker,
        'coordinator_exit_code':0,'solver_transcripts':[str(p) for p in transcripts],
        'original_checkpoint_sha256':plan['original_checkpoint_sha256'],'ledger_before':str(folder/'ledger-before.json'),
        'ledger_before_sha256':sha(folder/'ledger-before.json'),'ledger_sha256':sha(task/'development_state.json'),
        'selected_attempt':selected['directory'],'selected_bundle':selected['bundle'],
        'attempt_counts':{str(seed):sum(r.get('spends_retry',False) and r['seed']==seed for r in ledger['trials']) for seed in case['dev_seeds']},'stage':plan['stage'],'actual_worker_prompt_receipt':prompt_receipt,
        'failure_review_sha256':plan['failure_review_sha256'],'initial_bundle':plan['initial_bundle'],
        'original_checkpoint_preserved':True,'attempts_refunded':0}
    (folder/'checkpoint.json').write_text(json.dumps(cp,indent=2)+'\n')
    pointer=control/'native_work_continuation_checkpoint.json'
    if pointer.exists():
        (folder/'previous-continuation-checkpoint.json').write_bytes(pointer.read_bytes())
    tmp=pointer.with_suffix('.tmp');tmp.write_text(json.dumps(cp,indent=2)+'\n');tmp.replace(pointer)
    return cp


def development_diagnosis(control, plan, path):
    """Attach immutable THIS-cell development feedback only at legal repair."""
    require(plan['stage']=='repair' and plan['phase']=='repair','development diagnosis is permitted only at the next original repair stage')
    packet=load(path)
    require(packet.get('for_repair_only') is True and packet.get('requires_original_initial_coverage') is True,'diagnosis lacks original-stage boundary')
    require(packet.get('native_session')==plan['native_session'] and packet.get('native_worker')==plan['native_worker'],'diagnosis belongs to another native worker/session')
    case=load(control/'case.json');task=(Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task']).resolve()
    ledger=load(task/'development_state.json')
    require(packet.get('worker_request') and packet.get('semantic_rejection'),'actual development diagnosis missing')
    for evidence in packet.get('evidence',[]):
        require(evidence['seed'] in case['dev_seeds'] and evidence.get('semantic_accepted') is False,'diagnosis includes nondevelopment or accepted evidence')
        rows=[r for r in ledger['trials'] if r['directory']==evidence['selected_attempt'] and r.get('spends_retry')]
        require(len(rows)==1 and rows[0]['status']=='complete' and rows[0]['phase'] in {'initial','repair'} and rows[0]['seed']==evidence['seed'] and rows[0]['bundle']==evidence['bundle'],'diagnosis does not bind an actual completed development attempt')
        require(evidence.get('actual_files'),'diagnosis actual evidence hashes missing')
        for filename,expected in evidence['actual_files'].items():
            file=Path(filename).resolve()
            require(file.is_relative_to((task/evidence['selected_attempt']).resolve()) and file.is_relative_to(task),'diagnosis source escapes this actual development attempt')
            require(sha(file)==expected,'immutable diagnosis evidence changed')
    require(packet.get('evidence'),'no actual development evidence')
    return ('\nActual THIS-cell development diagnosis from independent source/event review, provided only after ORIGINAL initial coverage: '
            +json.dumps(packet,ensure_ascii=False)+'\nThis is failed-development feedback, not a supplied policy, fixed successful coordinates or a successful solution. Independently diagnose and repair with your ordinary native tools. Keep the original total attempt budget and boundaries.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--control',type=Path,required=True);p.add_argument('--phase',choices=['auto','initial','repair'],default='auto')
    p.add_argument('--development-diagnosis',type=Path);p.add_argument('--failure-review',type=Path);p.add_argument('--capture-first-pause',action='store_true');p.add_argument('--execute',action='store_true');args=p.parse_args()
    control=args.control.resolve();lock=(control/'recovery.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.capture_first_pause:
        path,checkpoint=capture_first_pause(control);print(json.dumps({'captured':str(path),'native_worker':checkpoint['native_worker']}));return 0
    require(args.failure_review is not None,'failure review is required for a trial stage')
    plan=prepare(control,args.phase,args.failure_review)
    diagnosis_text=''
    if args.development_diagnosis is not None:
        diagnosis_text=development_diagnosis(control,plan,args.development_diagnosis)
        plan['development_diagnosis']={'path':str(args.development_diagnosis.resolve()),'sha256':sha(args.development_diagnosis)}
    print(json.dumps(plan,indent=2),flush=True)
    if not args.execute:return 0
    case,support,repo,driver,campaign,protocol=original_context(control)
    folder=control/'native-gate-recovery'/f"stage-{plan['stage']}-{plan['ledger_before_sha256'][:12]}";folder.mkdir(parents=True)
    task=repo/'outputs/libero_fix_loop'/case['suite']/case['task'];(folder/'ledger-before.json').write_bytes((task/'development_state.json').read_bytes())
    (folder/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    original=load(control/'native_smoke_state.json');config=Path(case['claude_config_dir'])
    settings=load(config/'settings.json')
    settings['hooks']['PreToolUse'].append({'matcher':'Agent|Task|Bash|Write|Edit|MultiEdit','hooks':[{'type':'command','timeout':60,'command':shlex.join([str(repo/'.venv-libero/bin/python3'),str(Path(__file__).with_name('recovery_guard.py')),'--control',str(control),'--plan',str(folder/'plan.json')])}]})
    prompt=(f"Resume ONLY the existing primary {plan['native_worker']} using SendMessage in this SAME session. No Agent/Task dispatch or replacement. "
            f"The first mechanism/source review failed. Continue the ORIGINAL Fix Loop next legal stage {plan['stage']}, phase {plan['phase']}, allowed seeds {plan['seeds']}. "
            f"Eligible alias seed: {plan['alias_seed']}; use the original alias-smoke command only for this declared seed, never fake an execution. "
            f"For initial_batch reuse its frozen bundle {plan['initial_batch_bundle']} exactly (find its actual executed policy/world snapshots); if no batch exists and no alias is eligible, author one initial candidate and freeze it for the whole batch. "
            "Do not repair before the initial batch is complete. For repair diagnose current development evidence and author a distinct corrected pair. "
            "Preserve initial_code.py, initial_world_program.py, original snapshot/checkpoint, all prior ledger records, API/method/seed boundaries. "
            "Run each allowed seed at most once in this stage through the ordinary runtime protocol. After each completed trial inspect its actual mechanism evidence; pause immediately if it has physical prediction/check/decision/completed-call evidence; otherwise finish the declared stage and pause. "
            "No held-out, snapshot, diagnostic, direct replay, selection/finalization, new worker, free trial or refunds. Report the actual completed attempt directory. If infrastructure/admission fails, pause with the preserved blocker, never bypass it.")
    if plan['stage']=='close_infra':
        prompt=(f"Resume ONLY the SAME primary {plan['native_worker']} with SendMessage. No new Agent/Task. "
                f"Inspect ended infrastructure records {plan['directories']}; use only the ORIGINAL native_world_protocol.py close-infra for these exact directories with a concrete unusable-evidence reason. "
                "The original command refuses running attempts or usable-looking results. Preserve errors, source identity, charged slot and status; no executions, retries, snapshot, grade, refund, selection or finalization. Pause after these dispositions and report blockers. If usable results exist, report them for outer recovery; never discard them.")
    prompt+=diagnosis_text
    (folder/'prompt.md').write_text(prompt)
    command=campaign.native_command(case,prompt,settings,agents=campaign.worker_agent(case,control/'worker-prompt.md'));command+=['--resume',plan['native_session']]
    env=driver.bind_child_env(campaign.native_environment(case,repo,control/'case.json',config),case)
    code=driver.fixed_transport.run_native_cc(command,cwd=repo,env=env,stdout_path=folder/'stdout.jsonl',stderr_path=folder/'stderr.log',timeout=case['campaign_timeout'])
    cp=settle(control,folder,plan,folder/'stdout.jsonl',code,driver);print(json.dumps(cp,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
