"""Prepare independent RS v3 cells and a read-only source freeze; no trials."""
from pathlib import Path
import copy,grp,hashlib,json,os,pwd,shutil,subprocess,sys

B=Path('/mnt/workspace/aspire-world-validation-20261012-articulation-v3')
G=Path('/mnt/workspace/aspire-world-deployment-20261012-rs-articulation-v3')
R=Path('/mnt/workspace/aspire-world-runs/rs-articulation-v3-20261012')
V=Path('/mnt/workspace/aspire-world-runtimes/articulation-v3-SZ-DEV-20261012')
OLD=Path('/mnt/workspace/aspire-world-deployment-20261011-rs-e2e/dlc-four-rs-validated-20261011')
assert (B/'joint-contracts.exit').read_text().strip()=='0'
assert not G.exists() and not R.exists() and not V.exists()
G.mkdir(mode=0o750);os.chown(G,0,999)
(G/'manifests').mkdir(mode=0o750);os.chown(G/'manifests',0,999)
R.mkdir(mode=0o755)
subprocess.run(['cp','-a','--reflink=auto',str(B/'source'),str(V)],check=True)
SIM=V/'aspire/sim'
os.environ['LIBERO_CONFIG_PATH']='/mnt/workspace/aspire-world-deployment-20261010/public-libero-config'
sys.path[:0]=[str(V),str(SIM/'scripts/common'),str(SIM/'scripts/libero')]
from native_cc_freeze import build_manifest
import streaming_fixloop_protocol as sp

def write(path,value):
    with path.open('x') as f:json.dump(value,f,indent=2,ensure_ascii=False);f.write('\n')
    os.chown(path,0,999);path.chmod(0o640)
    return hashlib.sha256(path.read_bytes()).hexdigest()

start=json.loads((B/'qwen-start.json').read_text())
model=copy.deepcopy(json.loads(Path('/mnt/workspace/aspire-world-deployment-20261010/qwen-model-service-manifest.json').read_text()))
model.update(endpoint=start['endpoint'],served_model=start['served_model'],max_model_len=1000000,
    argv=start['argv'],env=start['env'],deployment_factor=start['deployment_difference'],
    source_start_receipt=str(B/'qwen-start.json'))
model['argv_sha256']=hashlib.sha256(json.dumps(model['argv'],sort_keys=True).encode()).hexdigest()
model_path=G/'manifests/model-service.json';model_sha=write(model_path,model)
template=json.loads((OLD/'drawer.case.json').read_text())
perception=copy.deepcopy(json.loads(Path(template['perception_manifest']).read_text()))
urls={'sam3_url':'http://127.0.0.1:8114','graspnet_url':'http://127.0.0.1:8115','pyroki_url':'http://127.0.0.1:8116'}
perception.update(urls)
perception.update(deployment_factor='RS existing shared perception stack; serial independent cells')
perception_path=G/'manifests/perception.json';perception_sha=write(perception_path,perception)
oldfreeze=json.loads((OLD/'runtime-freeze.json').read_text())
freeze=build_manifest(SIM,external_files=[*oldfreeze['external_files'],str(model_path),str(perception_path)],executables=list(oldfreeze['executables']))
freeze_path=G/'runtime-freeze.json';freeze_sha=write(freeze_path,freeze)
differences=['public RGB-D feature correspondence and robust rigid-part motion estimation',
    'fixed/sliding/rotating template fitting with temporal prediction validation',
    'event-driven direct local Qwen review, schema/data rejection and transactional structural changes',
    'joint effect checks include rotation; representation changes interrupt an ongoing trajectory',
    'unchanged native CC generation/xhigh/1M/64K, nonprivileged C input, seed budgets and scoring',
    'deployment factor: RS shared GPUs4,5,7,6, Qwen TP4 memory fraction0.80 (prior dedicated0.95)',
    'deployment factor: one shared existing perception stack, simulator/SAM2 GPU0, serial isolated cells']
queue=[]
shared_group=grp.getgrgid(999).gr_name
for i,task in enumerate(['drawer','stove','wine']):
    tpl=OLD/(task+'.case.json') if task!='stove' else Path('/mnt/workspace/aspire-world-deployment-20261011-stove-r1/stove.case.json')
    case=copy.deepcopy(json.loads(tpl.read_text()))
    name=task+'_sam2articulation_r1';control=R/name
    user='aspire-rs-articulation-'+task+'-r1';uid=30101+i
    home=Path('/mnt/workspace/aspire-world-users')/('rs-articulation-'+task+'-r1')
    assert not home.exists() and not control.exists()
    try:pwd.getpwnam(user);raise RuntimeError('user already exists')
    except KeyError:pass
    try:pwd.getpwuid(uid);raise RuntimeError('UID already exists')
    except KeyError:pass
    subprocess.run(['useradd','-u',str(uid),'-U','-M','-d',str(home),'-s','/bin/bash',user],check=True)
    subprocess.run(['usermod','-a','-G',shared_group,user],check=True)
    for path in (home,control):path.mkdir(mode=0o700);os.chown(path,uid,uid)
    task_meta=copy.deepcopy(json.loads(Path(case['task_manifest']).read_text()))
    task_path=G/'manifests'/(task+'-task.json');task_sha=write(task_path,task_meta)
    case.update(id=name,method='sam2-articulation-qwen-v3',repeat=1,profile='sam2-articulation-native-v3',
        method_differences=differences,sim=str(SIM),control=str(control),
        runtime_manifest=str(freeze_path),runtime_manifest_sha256=freeze_sha,
        task_manifest=str(task_path),task_manifest_sha256=task_sha,
        perception_manifest=str(perception_path),perception_manifest_sha256=perception_sha,
        model_service={'endpoint':start['endpoint'],'served_model':start['served_model'],
            'server_manifest':str(model_path),'server_manifest_sha256':model_sha},
        cuda_visible_devices='0',egl_device_id=0,claude_config_dir=str(control/'claude-config'),**urls)
    sp.validate_case(case)
    path=G/(task+'.case.json');write(path,case)
    queue.append({'task':task,'cell':name,'case':str(path),'user':user,'uid':uid,'home':str(home),
        'control':str(control),'status':'pending'})
for folder,dirs,files in os.walk(V,followlinks=False):
    os.chown(folder,0,999);os.chmod(folder,0o750)
    for name in files:
        p=Path(folder)/name
        if p.is_symlink():continue
        executable=bool(p.stat().st_mode&0o111)
        os.chown(p,0,999);p.chmod(0o750 if executable else 0o640)
write(G/'queue.json',{'runtime':str(V),'sim':str(SIM),'runtime_manifest_sha256':freeze_sha,
    'order':['drawer','stove','wine'],'concurrency':1,'cells':queue,'full_comparison':False,
    'budget_per_cell':{'trial_seconds':900,'development_seconds':50400,'heldout_seconds':18000},
    'max_serial_hours':57,'no_generated_code_feedback_or_skills_shared':True,'dlc_allocation':False})
write(G/'prepared.json',{'runtime':str(V),'source_files':len(freeze['files']),
    'runtime_manifest_sha256':freeze_sha,'cells':[c['cell'] for c in queue],
    'status':'prepared','not_launched_yet':True})
print(json.dumps({'runtime':str(V),'manifest_sha256':freeze_sha,'cells':[c['cell'] for c in queue]}))
