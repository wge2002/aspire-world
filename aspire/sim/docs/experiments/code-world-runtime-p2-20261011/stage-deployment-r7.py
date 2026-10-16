"""Freeze a new outer startup check; never modify active solver/core revision3."""
from pathlib import Path
import hashlib,json,shutil
ROOT=Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
HERE=Path(__file__).resolve().parent
old=ROOT/'launch-20261011-r6';new=ROOT/'launch-20261011-r7'
sha=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
old_manifest=old/'launch-manifest.json'
assert sha(old_manifest)=='3d2cabab39a45a0380bdb090c4986cd19731a9e133fccb670b17ad91139b29b1'
m=json.loads(old_manifest.read_text())
for name,expected in m['files'].items():assert sha(old/name)==expected,(name,'r6 changed')
# Validate every already-frozen runtime hash in every cell before AND after staging.
receipt=json.loads((ROOT/'prepare-receipt.json').read_text())
def verify_core():
    count=0
    for relative,expected in receipt['frozen_support'].items():
        assert sha(ROOT/'support'/relative)==expected,(relative,'support changed');count+=1
    for cell,details in receipt['cells'].items():
        case=json.loads((ROOT/cell/'case.json').read_text());sim=Path(case['sim'])
        assert sha(ROOT/cell/'case.json')==details['case_sha256']
        for name,expected in details['prompt_sha256'].items():assert sha(ROOT/cell/name)==expected,(cell,name,'prompt changed')
        for relative,expected in receipt['runtime_sha256'][cell].items():
            assert sha(sim/relative)==expected,(cell,relative,'core changed');count+=1
    return count
count=verify_core()
assert not new.exists();shutil.copytree(old,new)
shutil.copy2(HERE/'dlc-supervisor-r7.py',new/'dlc-supervisor.py')
shutil.copy2(HERE/'sam3_service_health.py',new/'sam3_service_health.py')
shutil.copy2(HERE/'sam3-public-image-provenance.json',new/'reference/sam3-public-image-provenance.json')
image=HERE/'sam3-public-dev51-agentview.jpg'
assert sha(image)=='76030d065bb24a30c14b754fb4c56c550ef30c6e374c10a358f4f925b755ce2c'
shutil.copy2(image,new/'reference/sam3-public-dev51-agentview.jpg')
changed=['dlc-supervisor.py','sam3_service_health.py','reference/sam3-public-image-provenance.json','reference/sam3-public-dev51-agentview.jpg']
m['files'].update({name:sha(new/name) for name in changed});m.update(deployment_revision=7,previous_launch_manifest={'path':str(old_manifest),'sha256':sha(old_manifest)},revision_note='Actual owned SAM3 HTTP inference and highest-score valid masks on an isolated pinned existing public dev image before native compatibility and any queue claim; later cell admissions recheck. Retain raw response bytes as gzip and failure reports. Failure stops node before cell/trial admission. Active core3, solver, seeds, budgets, model and r6 continuation preserved.')
(new/'launch-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
assert verify_core()==count
r={'state':'staged','deployment_revision':7,'launch':str(new),'launch_manifest_sha256':sha(new/'launch-manifest.json'),'previous_launch':str(old),'core_freeze_unchanged':True,'runtime_hashes_verified':count,'job_max_minutes':2700,'files':m['files'],'no_additional_trials':True,'independent_parent_review':'pending','submission_done':False}
(ROOT/'coordination/deployment-r7-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
