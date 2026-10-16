"""Freeze the fourth-node outer deployment without touching live jobs/core/outputs."""
from pathlib import Path
import json
import shutil
from schedule_add_node3 import (ROOT, LAUNCH, BASE_MANIFEST_SHA, OLD_PROOF_SHA, verify_core, verify_context, available_cells)
from schedule_r9 import sha, write_once
HERE=Path(__file__).resolve().parent
base=ROOT/'launch-20261011-r9';new=ROOT/LAUNCH
assert sha(base/'launch-manifest.json')==BASE_MANIFEST_SHA
old=json.loads((base/'launch-manifest.json').read_text())
for name,expected in old['files'].items(): assert sha(base/name)==expected,name
assert sha(ROOT/'coordination/replacement-r9-old-job-stopped.json')==OLD_PROOF_SHA
assert verify_core(ROOT)==289
assert not new.exists();shutil.copytree(base,new,ignore=shutil.ignore_patterns("launch-manifest.json"))
changed={'dlc-supervisor.py':HERE/'dlc-supervisor-add-node3.py',
         'schedule_add_node3.py':HERE/'schedule_add_node3.py',
         'submit-add-node3.py':HERE/'submit-add-node3.py',
         'stage-add-node3.py':Path(__file__),
         'submit_dlc_once.py':HERE/'submit_dlc_once.py'}
for name,source in changed.items():shutil.copy2(source,new/name)
registry=ROOT/'queue/r9-worker-registry'
baseline={'registry_sha256':{f'worker-r9-node{i}.json':sha(registry/f'worker-r9-node{i}.json') for i in range(3)},
          'base_manifest_sha256':BASE_MANIFEST_SHA,'original_stop_proof_sha256':OLD_PROOF_SHA,
          'prepare_receipt_sha256':sha(ROOT/'prepare-receipt.json'),
          'historical_mechanism_review_sha256':sha(ROOT/'coordination/primary-successor-repair51-attempt2-review-r1.json'),
          'unclaimed_at_staging':available_cells(ROOT)}
write_once(new/'add-node3-baseline.json',baseline)
m=dict(old);m['files']=dict(old['files']);m['files'].update({name:sha(new/name) for name in changed})
m['files']['add-node3-baseline.json']=sha(new/'add-node3-baseline.json')
m.update(deployment_revision='r9-add-node3',previous_launch_manifest={'path':str(base/'launch-manifest.json'),'sha256':BASE_MANIFEST_SHA},
         queue_job_envelope={'additional_jobs':1,'global_node_id':3,'existing_nodes':3,'resulting_nodes':4,'GPU_each':8,'max_minutes_each':3840,'priority':9},
         revision_note='Add one independent8GPU64h node3 to the existing atomic queue; leave original node0–2/job/r9/proof/core immutable; exact separate add-node primary release required.')
write_once(new/'launch-manifest.json',m)
context=verify_context(ROOT,new)
receipt={'state':'prepared_for_independent_review_no_submission','launch':str(new),**context,
         'files':m['files'],'base_r9_28_files_unchanged':True,'ten_cases_and20_prompts_unchanged':True,
         'old_three_node_proof_unchanged':True,'baseline':baseline,'real_trials_added':0,
         'new_jobs_submitted':0,'additional_GPU':8,'additional_max_minutes':3840,
         'rehearsal':'Existing r9 actual SAM3/native fixture and live development trials; every experiment/service/driver input flag unchanged; new global ID scheduling rehearsed with offline four-process test.'}
write_once(ROOT/'coordination/deployment-add-node3-receipt.json',receipt)
print(json.dumps(receipt))
