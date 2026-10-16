"""One approved DSW successor bootstrap, exact receipt, ONE original repair."""
import argparse,copy,datetime,fcntl,hashlib,importlib.util,json,shlex,sys
from pathlib import Path
from startup_gate import load,sha,require,original_checkpoint
from successor_lineage_r1 import CELL,SESSION,PREDECESSOR,LEDGER,REFUSAL,native_successor,audit_transition,frozen
from successor_settle_r1 import settle

def old_actor():
    spec=importlib.util.spec_from_file_location('accepted_recovery_r2',Path(__file__).with_name('recover-native-gate-with-diagnosis-r2.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def prepare(control,approval,refusal,diagnosis,review):
    actor=old_actor();case=load(control/'case.json');require(case['id']==CELL and case['model_tag']=='qwen3.8-flash-next','successor authorization is single DSW Qwen bowl cell')
    authorization=load(approval);require(authorization.get('approved') is True and authorization.get('user_message') and CELL in authorization.get('scope',''),'actual human successor approval required')
    require(sha(refusal)==REFUSAL,'unreviewed native cancellation/refusal receipt')
    actual=load(refusal);require(actual['native_session']==SESSION and actual['stopped_native_worker']==PREDECESSOR and actual['charged_added']==0 and actual['actor_ended'] and actual['controller_ended'],'native predecessor not safely ended')
    for file,expected in actual['evidence_files'].items():require(sha(file)==expected,'preserved predecessor evidence changed')
    plan=actor.prepare(control,'repair',review);require(plan['native_session']==SESSION and plan['native_worker']==PREDECESSOR and plan['ledger_before_sha256']==LEDGER,'successor baseline changed')
    require(plan['phase']=='repair' and plan['seeds']==[51] and all(n==1 for n in plan['before_counts'].values()) and plan['maximum_after_counts']['51']==2,'not original first legal repair with 15 charged')
    diagnosis_text=actor.development_diagnosis(control,plan,diagnosis)
    bootstrap=(control/'worker-prompt.md').read_text()+('\n\n--- EXPLICIT HUMAN-APPROVED SUCCESSOR HANDOFF ---\n'
        f'You are a NEW successor for THIS existing {CELL}, not worker {PREDECESSOR}. The native session stays {SESSION}. '
        'The original worker was engineering-stopped with SDK stop_task and its SendMessage resume was refused; retain that deviation. '
        'All original initial coverage is already complete: seeds51–65 each have ONE charged attempt, TWO of the original three remain. '
        'Do not snapshot, init, rerun smoke/initial, reset, refund, select, finalize, access other cells or heldout. '
        'The original assignment above is preserved in full; this handoff changes ONLY the identity and current-stage entry, not the method, public APIs, model, budgets or boundaries. '
        'BOOTSTRAP NOW IS READ-ONLY. Read your own cell ledger/initial sources and the following failed-development diagnosis. Do not run Bash, write or repair code, or execute any trial now. '
        'A separate outer invocation will verify your real identity and exact received assignment before sending you ONE legal repair-stage instruction. '
        'Return normally with a read-only receipt; do not use TaskStop or SDK stop_task to pause. Do not dispatch helpers.\n')+diagnosis_text
    return plan,bootstrap

def add_guard(settings,python,control,plan_path):
    settings=copy.deepcopy(settings)
    settings['hooks']['PreToolUse'].append({'matcher':'Agent|Task|SendMessage|TaskStop|Bash|Write|Edit|MultiEdit|Read|Glob|Grep',
        'hooks':[{'type':'command','timeout':60,'command':shlex.join([str(python),str(Path(__file__).with_name('successor_guard_r1.py')),'--control',str(control),'--plan',str(plan_path)])}]})
    return settings

def write_new(path,value):
    with path.open('x') as out:json.dump(value,out,indent=2);out.write('\n')

def main():
    parser=argparse.ArgumentParser()
    for key in ('control','approval','refusal','diagnosis','failure-review'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--execute',action='store_true');args=parser.parse_args();control=args.control.resolve()
    source_manifest=Path(__file__).with_name('source-manifest.json');source=load(source_manifest)
    for name,expected in source['files'].items():require(sha(source_manifest.parent/name)==expected,'frozen successor source changed: '+name)
    with (control/'recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        plan,assignment=prepare(control,args.approval,args.refusal,args.diagnosis,args.failure_review)
        print(json.dumps({'plan':plan,'assignment_bytes':len(assignment.encode()),'assignment_sha256':hashlib.sha256(assignment.encode()).hexdigest(),'new_agent_started':False}),flush=True)
        if not args.execute:return 0
        folder=control/'native-successor-r1';folder.mkdir() # Exclusive; no repeat and no old-stage overwrite.
        actor=old_actor();case,support,repo,driver,campaign,protocol=actor.original_context(control)
        task=repo/'outputs/libero_fix_loop'/case['suite']/case['task'];before=task/'development_state.json'
        (folder/'ledger-before.json').write_bytes(before.read_bytes());(folder/'successor-assignment.md').write_text(assignment)
        cp_path=control/'native_work_continuation_checkpoint.json';old_cp=load(cp_path)
        (folder/'predecessor-checkpoint.json').write_bytes(cp_path.read_bytes())
        predecessor_transcripts=[Path(p) for p in old_cp['solver_transcripts']]
        failed=control/'native-gate-recovery/stage-repair-167a6875c1e2/stdout.jsonl';predecessor_transcripts.append(failed)
        boot=folder/'bootstrap';boot.mkdir()
        write_new(boot/'plan.json',{'mode':'bootstrap','assignment':str(folder/'successor-assignment.md')})
        config=Path(case['claude_config_dir']);settings=load(config/'settings.json');python=repo/'.venv-libero/bin/python3'
        # Config on disk stays unchanged; add only an explicit current-invocation hook.
        driver.verify_local_provider(case,settings)
        env=driver.bind_child_env(campaign.native_environment(case,repo,control/'case.json',config),case)
        prompt=('The human explicitly approved EXACTLY ONE new successor for this existing cell. The predecessor is user-stopped; do not message or resume it. '
                'Create ONE foreground general-purpose Agent with prompt ASPIRE_APPROVED_SUCCESSOR_HANDOFF. The outer hook supplies the complete frozen assignment and explicit handoff. '
                'The successor is READ-ONLY in this invocation and must return normally. No Bash, repair, trial, code edits, additional Agent/Task, TaskStop, new session, or strategy. '
                'Wait for its actual normal result, report its real agentId, then end. Do not SendMessage to any worker in this bootstrap.')
        command=campaign.native_command(case,prompt,add_guard(settings,python,control,boot/'plan.json'),agents=campaign.worker_agent(case,folder/'successor-assignment.md'))+['--resume',SESSION]
        code=driver.fixed_transport.run_native_cc(command,cwd=repo,env=env,stdout_path=boot/'stdout.jsonl',stderr_path=boot/'stderr.log',timeout=case['campaign_timeout'])
        require(code==0,'successor bootstrap did not end safely; preserve invocation, no automatic retry')
        require(sha(before)==LEDGER,'read-only bootstrap changed original trial ledger')
        worker,tool_id=native_successor([boot/'stdout.jsonl'],SESSION)
        receipt=frozen(control).verify_received_assignment(config,SESSION,worker,folder/'successor-assignment.md')
        (boot/'actual-child-snapshot.jsonl').write_bytes(Path(receipt['actual_child_transcript']).read_bytes())
        immutable=[source_manifest,*[source_manifest.parent/name for name in source['files']],args.approval,args.refusal,args.diagnosis,control/'worker-prompt.md',folder/'successor-assignment.md',folder/'ledger-before.json',folder/'predecessor-checkpoint.json',
                   boot/'stdout.jsonl',boot/'assignment-audit.jsonl',boot/'actual-child-snapshot.jsonl',*predecessor_transcripts]
        transition={'cell':CELL,'native_session':SESSION,'predecessor':PREDECESSOR,'successor':worker,'identity_change_disclosed':True,
                    'approval':str(args.approval.resolve()),'refusal':str(args.refusal.resolve()),'diagnosis':str(args.diagnosis.resolve()),
                    'assignment':str(folder/'successor-assignment.md'),'dispatch_tool_use_id':tool_id,'bootstrap_transcript':str(boot/'stdout.jsonl'),
                    'bootstrap_child_snapshot':str(boot/'actual-child-snapshot.jsonl'),'predecessor_transcripts':[str(p) for p in predecessor_transcripts],
                    'baseline_ledger':str(folder/'ledger-before.json'),'baseline_ledger_sha256':LEDGER,'charged_retained':15,'remaining_each_seed':2,'actual_assignment_receipt':receipt,
                    'immutable_files':{str(p.resolve()):sha(p) for p in immutable},'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
        write_new(folder/'transition.json',transition)
        all_boot=predecessor_transcripts+[boot/'stdout.jsonl'];verified=audit_transition(control,all_boot,folder/'transition.json')
        write_new(folder/'actual-successor-delivery-receipt.json',verified)
        repair=folder/'repair-seed51-attempt2';repair.mkdir();(repair/'ledger-before.json').write_bytes(before.read_bytes())
        original_msg=load(control/'native-gate-recovery/stage-repair-167a6875c1e2/plan.json')['worker_stage_message']
        plan.update(mode='repair',native_worker=worker,transition=str(folder/'transition.json'),predecessor_transcripts=[str(p) for p in predecessor_transcripts],bootstrap_transcript=str(boot/'stdout.jsonl'),
                    worker_stage_message=f'You are the approved successor {worker}, predecessor {PREDECESSOR} is retained as a different stopped identity. Exact assignment and full diagnosis received were independently verified.\n'+original_msg)
        write_new(repair/'plan.json',plan)
        prompt=(f'Resume ONLY approved successor {worker} via SendMessage, in this same session. No Agent/Task or predecessor resume. '
                'The outer hook supplies exactly the frozen repair message after verified native assignment/diagnosis receipt. '
                'Permit only original repair seed51 once (totalattempt2), then have the worker return normally and end. No successor stage, no semantic acceptance, no TaskStop/SDK stop_task as pause.')
        command=campaign.native_command(case,prompt,add_guard(settings,python,control,repair/'plan.json'),agents=campaign.worker_agent(case,folder/'successor-assignment.md'))+['--resume',SESSION]
        code=driver.fixed_transport.run_native_cc(command,cwd=repo,env=env,stdout_path=repair/'stdout.jsonl',stderr_path=repair/'stderr.log',timeout=case['campaign_timeout'])
        checkpoint=settle(control,repair,plan,repair/'stdout.jsonl',code,driver)
        print(json.dumps(checkpoint,indent=2),flush=True);return 0

if __name__=='__main__':raise SystemExit(main())
