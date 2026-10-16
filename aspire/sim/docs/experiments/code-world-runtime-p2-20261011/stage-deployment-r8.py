"""Stage explicit successor outer integration only; no active source mutation/job."""
from pathlib import Path
import hashlib,json,shutil
ROOT=Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
HERE=Path(__file__).resolve().parent
INPUT=Path('/mnt/home/gewang/code/ASPIRE-code-world-runtime-p2-input-20261011/aspire/sim/docs/experiments/code-world-runtime-p2-20261011')
old=ROOT/'launch-20261011-r7';new=ROOT/'launch-20261011-r8'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
old_manifest=old/'launch-manifest.json'
assert sha(old_manifest)=='277ac275fface6ae84380a4995e7345670b1219695e2982cd017106edb61e11a'
m=json.loads(old_manifest.read_text())
for name,expected in m['files'].items():assert sha(old/name)==expected,(name,'accepted r7 changed')
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
source=ROOT/'coordination/successor-recovery-r1';source_manifest=json.loads((source/'source-manifest.json').read_text())
assert sha(source/'source-manifest.json')=='8c7c72adad667226fb9027365c807e99944b5c49661dd281e8e1d0d7371e1586'
for name,expected in source_manifest['files'].items():assert sha(source/name)==expected
shutil.copytree(old,new)
changed={'dlc-supervisor.py':HERE/'dlc-supervisor-r8.py',
         'submit-runtime-study-r8.py':HERE/'submit-runtime-study-r8.py',
         'stage-deployment-r8.py':Path(__file__),
         'verify-staging-successor-r8.py':HERE/'verify-staging-successor-r8.py'}
changed.update({name:source/name for name in source_manifest['files']})
for name,path in changed.items():shutil.copy2(path,new/name)
# Fresh document scripts for later pre-submit checks. Never replace differing
# existing files, and never touch runtime/support/case/prompt freeze entries.
for name,path in changed.items():
    if name=='dlc-supervisor.py':continue
    target=INPUT/name
    if target.exists():assert sha(target)==sha(path),(name,'input document source differs')
    else:shutil.copy2(path,target)
m['files'].update({name:sha(new/name) for name in changed})
m.update(deployment_revision=8,previous_launch_manifest={'path':str(old_manifest),'sha256':sha(old_manifest)},
         revision_note='Only approved bowl successor uses explicit two-segment gate/full-driver adapter. Other9cells, core3, SAM health, original model/budgets/nonprivileged/runtime and 40GPU/5worker2700min plan unchanged. Requires independent actual mechanism and explicit main release before submission.')
with (new/'launch-manifest.json').open('w') as out:json.dump(m,out,indent=2);out.write('\n')
assert verify_core()==count
for name,expected in json.loads(old_manifest.read_text())['files'].items():assert sha(old/name)==expected
result={'state':'staged_for_independent_review_no_submission','deployment_revision':8,'launch':str(new),
        'launch_manifest_sha256':sha(new/'launch-manifest.json'),'previous_launch':str(old),
        'previous_launch_manifest_sha256':sha(old_manifest),'source_successor_manifest_sha256':sha(source/'source-manifest.json'),
        'core_freeze_unchanged':True,'runtime_hashes_verified':count,'job_max_minutes':2700,'files':m['files'],
        'independent_parent_review':'pending','real_mechanism_accepted':False,'explicit_submission_release':False,'submission_done':False}
with (ROOT/'coordination/deployment-r8-receipt.json').open('x') as out:json.dump(result,out,indent=2);out.write('\n')
print(json.dumps(result))
