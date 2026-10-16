"""Stage explicit successor outer integration only; no active source mutation/job."""
from pathlib import Path
import hashlib,json,shutil
ROOT=Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
HERE=Path(__file__).resolve().parent
INPUT=Path('/mnt/home/gewang/code/ASPIRE-code-world-runtime-p2-input-20261011/aspire/sim/docs/experiments/code-world-runtime-p2-20261011')
old=ROOT/'launch-20261011-r8';new=ROOT/'launch-20261011-r9'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
old_manifest=old/'launch-manifest.json'
assert sha(old_manifest)=='b993ee20f6f4169197d669a31b77b36007e0535ff1352abd2aca889cf3b57d28'
m=json.loads(old_manifest.read_text())
for name,expected in m['files'].items():assert sha(old/name)==expected,(name,'accepted r8 changed')
receipt=json.loads((ROOT/'prepare-receipt.json').read_text())
def verify_core():
    count=0
    for name,expected in receipt['frozen_support'].items():assert sha(ROOT/'support'/name)==expected;count+=1
    for cell,details in receipt['cells'].items():
        case=json.loads((ROOT/cell/'case.json').read_text());sim=Path(case['sim']);assert sha(ROOT/cell/'case.json')==details['case_sha256']
        for name,expected in details['prompt_sha256'].items():assert sha(ROOT/cell/name)==expected
        for name,expected in receipt['runtime_sha256'][cell].items():assert sha(sim/name)==expected;count+=1
    return count
count=verify_core();assert not new.exists()
shutil.copytree(old,new)
changed={"dlc-supervisor.py":HERE/"dlc-supervisor-r9.py",
         "dlc-single-node-entry.sh":HERE/"dlc-single-node-entry-r9.sh",
         "schedule_r9.py":HERE/"schedule_r9.py",
         "submit-runtime-study-r9.py":HERE/"submit-runtime-study-r9.py",
         "stage-deployment-r9.py":Path(__file__)}
for name,path in changed.items():shutil.copy2(path,new/name)
m["files"].update({name:sha(new/name) for name in changed})
m.update(deployment_revision=9,previous_launch_manifest={"path":str(old_manifest),"sha256":sha(old_manifest)},
         serial_order=[f"{task}_p2_r{repeat}" for repeat in (1,2)
                       for task in ("bowl","bowldrawer","drawer","wine","stove")],
         queue_job_envelope={"jobs":3,"GPU_each":8,"GPU_total":24,"max_minutes_each":4800,"priority":9},
         revision_note="Three independent8GPU jobs,unique global node IDs,r1 before r2 atomicclaims. Require oldr8job Stopped/no startup proof and explicitmainrelease. Preserve core3/r8/model/method/budgets/bowl16charges;80h outer node limit with fullcell-window admission guard.")
with (new/'launch-manifest.json').open('w') as out:json.dump(m,out,indent=2);out.write('\n')
assert verify_core()==count
for name,expected in json.loads(old_manifest.read_text())['files'].items():assert sha(old/name)==expected
result={'state':'staged_for_independent_review_no_submission','deployment_revision':9,'launch':str(new),
        'launch_manifest_sha256':sha(new/'launch-manifest.json'),'previous_launch':str(old),
        'previous_launch_manifest_sha256':sha(old_manifest),'nodes':3,'GPU_each':8,'GPU_total':24,
        'core_freeze_unchanged':True,'runtime_hashes_verified':count,'job_max_minutes':4800,'files':m['files'],
        'independent_parent_review':'pending','real_mechanism_accepted':True,'explicit_submission_release':False,'submission_done':False}
with (ROOT/'coordination/deployment-r9-receipt.json').open('x') as out:json.dump(result,out,indent=2);out.write('\n')
print(json.dumps(result))
