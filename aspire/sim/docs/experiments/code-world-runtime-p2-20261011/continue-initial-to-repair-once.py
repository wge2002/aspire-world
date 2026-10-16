"""One task-owned continuation: wait for existing initial, bind rejection, one repair.

No initial/trial is restarted. No future automation or DLC submission is created.
"""
import argparse,datetime,fcntl,hashlib,importlib.util,json,os,subprocess,sys,time
from pathlib import Path
import psutil
ROOT=Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
CONTROL=ROOT/'bowl_p2_r1'
ACTOR=ROOT/'coordination/diagnosis-recovery-r1'
REJECTION=ROOT/'coordination/seed52-initial-attempt1-semantic-rejection-20261011.json'
PACKET=ROOT/'coordination/development53-54-repair-diagnosis-20261011.json'
PYTHON='/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
load=lambda p:json.loads(Path(p).read_text())
def require(condition,message):
    if not condition:raise RuntimeError(message)
def save(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)
def alive(pid,created):
    try:
        p=psutil.Process(pid)
        return p.create_time()==created and p.is_running() and p.status()!=psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:return False

def qualify(control,actor,rejection,packet,gate):
    """Fail closed unless the specific already-reviewed actual attempt is unchanged."""
    require(sha(rejection)=='f16eb436776fbad2f82edd1c4f7c7d121850ff92f27d0745ca4b8fa21b6b74e9','reviewed seed52 rejection changed')
    require(sha(packet)=='94795f5bc4b338c64e93383d8c21871f3bbe22c663ac957ec5275ef322b8fa96','reviewed development packet changed')
    manifest=load(actor/'source-manifest.json')
    expected={'recover-native-gate-with-diagnosis.py':'e282b2fbc0dcce2b3a87facfb0cacd4ece6e412233acb1961b9f56c68eb0fe72',
              'startup_gate.py':'d77d2234d976bc628a95f413806429bc2f1c471e34c6fd6ff9b45e4431097bc8',
              'recovery_guard.py':'c4d844b246a5ac86b8b7163a4131c1c3d1b5eabef5221df8d5f83a1a7315d965'}
    require(manifest['files']==expected,'reviewed repair actor manifest differs')
    for name,digest in expected.items():require(sha(actor/name)==digest,'reviewed repair actor file changed')
    cp=load(control/'native_work_continuation_checkpoint.json')
    require(cp.get('status')=='native_gate_repair_checkpoint' and cp.get('coordinator_exit_code')==0 and cp.get('stage')=='initial_batch','current initial stage has no safe checkpoint')
    require(gate.get('state')=='passed','actual automatic gate not ready; requires explicit evidence review')
    old=load(rejection)
    for key in ('selected_attempt','bundle','native_session','native_worker'):
        require(gate.get(key)==old.get(key),'safe candidate differs from specifically rejected seed52; inspect it instead of rejecting automatically')
    require(old.get('accepted') is False and old.get('semantic_failure') is True and old.get('reason'),'specific semantic rejection missing')
    for filename,digest in old['actual_attempt_files'].items():
        require(gate['evidence_files'].get(filename)==digest and sha(filename)==digest,'actual rejected attempt evidence changed')
    case=load(control/'case.json');task=Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task']
    ledger=load(task/'development_state.json')
    require(not any(r['status']=='running' for r in ledger['trials']),'a trial remains running')
    initial={r['seed'] for r in ledger['trials'] if r['phase']=='initial' and r['status']=='complete' and r.get('sandbox_rc')==0}
    require(initial==set(case['dev_seeds']),'original initial coverage incomplete; do not launch repair')
    return {'accepted':False,'semantic_failure':True,'reason':old['reason'],
            **{k:gate[k] for k in ('binding_sha256','selected_attempt','bundle','native_session','native_worker')},
            'ledger_sha256':sha(task/'development_state.json'),'specific_rejection':str(rejection),'specific_rejection_sha256':sha(rejection),
            'independent_parent_review':'seed52 actual source/events rejected; repair-only packet/actor independently accepted at2026-10-10T22:27Z',
            'attempts_refunded':0,'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}

def main():
    p=argparse.ArgumentParser();p.add_argument('--initial-pid',type=int,required=True);p.add_argument('--initial-create-time',type=float,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir()  # New one-shot journal. Existing output never authorizes another launch.
    state={'state':'waiting_existing_initial','initial_pid':a.initial_pid,'initial_create_time':a.initial_create_time,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'trials_added_by_controller':0,'DLC_submission':False}
    save(a.output/'state.json',state)
    try:
        while alive(a.initial_pid,a.initial_create_time):time.sleep(300)
        lock=(CONTROL/'recovery.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        sys.path.insert(0,str(ACTOR))
        from startup_gate import verify
        gate=verify(CONTROL);save(a.output/'actual-initial-gate.json',gate)
        review=qualify(CONTROL,ACTOR,REJECTION,PACKET,gate)
        review_path=a.output/'fresh-initial-semantic-failure-review.json';save(review_path,review)
        spec=importlib.util.spec_from_file_location('accepted_diagnosis_recovery',ACTOR/'recover-native-gate-with-diagnosis.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        plan=module.prepare(CONTROL,'auto',review_path)
        require(plan['stage']=='repair' and plan['phase']=='repair','original prepare did not establish legal repair')
        module.development_diagnosis(CONTROL,plan,PACKET)
        save(a.output/'original-next-repair-plan.json',plan)
        state.update(state='starting_one_legal_repair',plan=str(a.output/'original-next-repair-plan.json'),review=str(review_path));save(a.output/'state.json',state)
        command=[PYTHON,str(ACTOR/'recover-native-gate-with-diagnosis.py'),'--control',str(CONTROL),'--phase','auto','--failure-review',str(review_path),'--development-diagnosis',str(PACKET),'--execute']
        save(a.output/'launch-command.json',command)
        # Actor owns the SAME recovery.lock throughout the actual native stage;
        # a competing holder makes it refuse, never reset/retry.
        fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
        with (a.output/'repair.stdout.log').open('x') as out,(a.output/'repair.stderr.log').open('x') as err:
            child=subprocess.Popen(command,stdout=out,stderr=err,start_new_session=True)
            state.update(state='same_native_repair_running',actor_pid=child.pid);save(a.output/'state.json',state)
            code=child.wait()
        state.update(state='repair_stage_ended_needs_actual_candidate_review' if code==0 else 'repair_stage_failed_preserved',actor_exit=code,finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        save(a.output/'state.json',state);return code
    except Exception as exc:
        state.update(state='blocked_preserved_no_retry',error=f'{type(exc).__name__}: {exc}',finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat());save(a.output/'state.json',state);return 1

if __name__=='__main__':raise SystemExit(main())
