"""Freeze independent outer recovery/acceptance; leave active cell inputs intact."""
from pathlib import Path
import hashlib,json,shutil
ROOT=Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
HERE=Path(__file__).resolve().parent
old=ROOT/'launch-20261011-r5';new=ROOT/'launch-20261011-r6'
assert not new.exists();shutil.copytree(old,new)
shutil.copy2(HERE/'dlc-supervisor-r6.py',new/'dlc-supervisor.py')
files=['startup_gate.py','recover-native-gate.py','recovery_guard.py','continuation_driver.py']
for name in files:shutil.copy2(HERE/name,new/name)
m=json.loads((old/'launch-manifest.json').read_text());m['files'].update({name:hashlib.sha256((new/name).read_bytes()).hexdigest() for name in ['dlc-supervisor.py',*files]})
m['deployment_revision']=6;m['previous_launch_manifest']={'path':str(old/'launch-manifest.json'),'sha256':hashlib.sha256((old/'launch-manifest.json').read_bytes()).hexdigest()}
m['revision_note']='Original next-stage initial-batch/repair recovery, any specific dev51–65 candidate, and ended-infra disposition; read-only admission/ledger-bound receipt. Active revision3 core/runtime/support/first checkpoint preserved. Original protocol admission order retained; no recovery trial is executed by staging. Direct first-success continuation still uses original driver.'
(new/'launch-manifest.json').write_text(json.dumps(m,indent=2)+'\n')
r={'state':'staged','deployment_revision':6,'launch':str(new),'launch_manifest_sha256':hashlib.sha256((new/'launch-manifest.json').read_bytes()).hexdigest(),'previous_launch':str(old),'core_freeze_unchanged':True,'job_max_minutes':2700,'files':m['files'],'no_additional_trials':True}
(ROOT/'coordination/deployment-r6-receipt.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
