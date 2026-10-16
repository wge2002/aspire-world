"""Owned DSW model memory headroom only; NEVER an extra trial or gate proof."""
import argparse,fcntl,hashlib,importlib.util,json,os,subprocess,time,urllib.request
from pathlib import Path
import psutil
ROOT=Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
CONTROL=ROOT/'bowl_p2_r1'
OLD=ROOT/'coordination/model-smoke/launch.json'
NEW=ROOT/'coordination/model-smoke-headroom-r1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def require(value,why):
    if not value:raise RuntimeError(why)
def model_metrics():
    with urllib.request.urlopen('http://127.0.0.1:8121/metrics',timeout=10) as f:lines=f.read().decode().splitlines()
    return {key:float(next(l.rsplit(' ',1)[1] for l in lines if l.startswith('vllm:'+key+'{'))) for key in ['num_requests_running','num_requests_waiting']}


class ExistingProcess:
    def __init__(self,process):self.pid=process.pid;self.args=process.cmdline();self.process=process;self.returncode=None
    def wait(self,timeout):
        try:self.returncode=self.process.wait(timeout=timeout);return self.returncode
        except psutil.TimeoutExpired as exc:raise subprocess.TimeoutExpired(self.args,timeout) from exc


def main():
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');a=p.parse_args()
    old=json.loads(OLD.read_text());process=psutil.Process(old['pid'])
    require(process.cmdline()==old['argv'],'owned model command/identity changed')
    require(abs(process.create_time()-old['started_at'])<5,'owned model PID/start identity changed')
    require(os.getpgid(process.pid)==old['owned_process_group']==process.pid,'model process group differs')
    require((Path('/proc')/str(process.pid)/'fd/1').readlink()==OLD.parent/'model.log','model no longer owns original task log')
    argv=list(old['argv']);flag=argv.index('--gpu-memory-utilization')+1
    require(argv[flag]=='0.90','old reservation differs');argv[flag]='0.80'
    require(argv[argv.index('--max-model-len')+1]=='1000000','context changed')
    require([x for i,x in enumerate(argv) if i!=flag]==[x for i,x in enumerate(old['argv']) if i!=flag],'nonresource model settings changed')
    state=json.loads((CONTROL/'native_smoke_state.json').read_text())
    plan={**old,'argv':argv,'old_launch_sha256':sha(OLD),'old_pid':old['pid'],'old_create_time':process.create_time(),
          'resource_only_change':{'gpu_memory_utilization':[0.90,0.80]},'model_context_effort_sampling_unchanged':True,
          'DLC_configuration_unchanged':True,'native_status':state['status'],'ready_for_execute':state.get('status')=='native_smoke_checkpoint' and state.get('coordinator_exit_code')==0,
          'requires_before_next_trial':'Actual same-service SAM3 inference health on an existing public development image; this diagnostic never replaces real trial mechanism evidence.'}
    print(json.dumps(plan,indent=2),flush=True)
    if not a.execute:return 0
    lock=(CONTROL/'recovery.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    state=json.loads((CONTROL/'native_smoke_state.json').read_text())
    require(state.get('status')=='native_smoke_checkpoint' and state.get('coordinator_exit_code')==0,'native first startup has not safely paused')
    case=json.loads((CONTROL/'case.json').read_text());task=Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task']
    ledger=json.loads((task/'development_state.json').read_text());charged=[r for r in ledger['trials'] if r.get('spends_retry')]
    require(len(charged)==1 and charged[0]['phase']=='smoke' and charged[0]['status']=='complete' and not any(r['status']=='running' for r in ledger['trials']),'original first smoke boundary changed')
    require(all(x==0 for x in model_metrics().values()),'owned endpoint still has active/queued model requests')
    evidence=[CONTROL/'case.json',CONTROL/'worker-prompt.md',CONTROL/'native_smoke_state.json',task/'development_state.json',task/'initial_code.py',task/'initial_world_program.py',ROOT/'launch-20261011-r6/reference/model-server.json']
    before={str(p):sha(p) for p in evidence}
    env=dict(os.environ);env.update(old['env'])
    inherited=process.environ()
    for key in ['VLLM_CACHE_ROOT','FLASHINFER_WORKSPACE_BASE','TRITON_CACHE_DIR','TORCHINDUCTOR_CACHE_DIR','HF_HUB_OFFLINE','XDG_CACHE_HOME']:
        if key in inherited:env[key]=inherited[key]
    NEW.mkdir();(NEW/'before.json').write_text(json.dumps({'launch':old,'hashes':before},indent=2)+'\n')
    spec=importlib.util.spec_from_file_location('accepted_process_cleanup',ROOT/'launch-20261011-r6/dlc-supervisor.py');supervisor=importlib.util.module_from_spec(spec);spec.loader.exec_module(supervisor)
    tree=supervisor.CellProcessTree(ExistingProcess(process));require(all(os.getpgid(p.pid)==old['owned_process_group'] for p in tree.survivors()),'owned descendants escape model group')
    cleanup=tree.cleanup()
    (NEW/'cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
    require({str(p):sha(p) for p in evidence}==before,'native/core/DLC records changed during owned service cleanup')
    output=(NEW/'model.log').open('xb')
    child=subprocess.Popen(argv,cwd=old['cwd'],env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
    plan.update(pid=child.pid,started_at=time.time(),owned_process_group=child.pid,native_hashes_before=before,cleanup=cleanup)
    (NEW/'launch.json').write_text(json.dumps(plan,indent=2)+'\n')
    print(json.dumps({'state':'owned_model_restarting','pid':child.pid,'launch':str(NEW/'launch.json'),'ledger_reset':False,'attempts_refunded':0}),flush=True)
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        require(child.poll() is None,'headroom model exited; inspect preserved new log, do not relax model/context')
        try:
            with urllib.request.urlopen(old['endpoint']+'/v1/models',timeout=5) as f:models=json.load(f)
            if any(x.get('id')==old['model'] and x.get('max_model_len')==1000000 for x in models.get('data',[])):
                require({str(p):sha(p) for p in evidence}==before,'native/core/DLC records changed during owned restart')
                (NEW/'ready.json').write_text(json.dumps({'state':'ready','models':models,'hashes_unchanged':True,'gpu_reservation':0.80,'actual_SAM3_inference_still_required':True},indent=2)+'\n')
                print('HEADROOM_MODEL_READY_CONTEXT1000000_NATIVE_AND_DLC_HASHES_UNCHANGED',flush=True);return 0
        except (OSError,ValueError):pass
        time.sleep(5)
    raise RuntimeError('owned model readiness timeout; logs retained, no extra trial permitted')

if __name__=='__main__':raise SystemExit(main())
