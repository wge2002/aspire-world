"""One extra node on the existing atomic queue; never alters original nodes/proof."""
from pathlib import Path
import json
import os
import re
import time
from schedule_r9 import sha, write_once, CELL_ORDER

ROOT = Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
LAUNCH = 'launch-20261012-add-node3'
JOB = 'aspire-runtime-p2-1012-add-node3'
BASE_MANIFEST_SHA = 'f2808efa5317dfafbd7822e1b9e3a43daf7c4a4242f55c4e22dbad71840e286a'
OLD_PROOF_SHA = '665d9bb320e5e9275457f124d6fa5e100f537dfc8c68fbb999bdfa988bc5d32f'
REVIEWER = '01a1260c-2406-7b20-b777-92e3787c0fe9'
MAX_MINUTES = 3840

def command(mode, root=ROOT):
    if mode not in {'plan', 'submit'}: raise ValueError('unsupported operation')
    return ['/mnt/home/gewang/.local/bin/dlc-run', mode, '--name', JOB,
            '--gpus', '8', '--workdir', str(root), '--max-minutes', str(MAX_MINUTES),
            '--priority', '9', '--skip-preflight', '--', 'bash',
            str(Path(root)/LAUNCH/'dlc-single-node-entry.sh'), '--node-id', '3', '--node-count', '4', '--case',
            str(Path(root)/'wine_p2_r1/case.json')]

def verify_core(root):
    root=Path(root); receipt=json.loads((root/'prepare-receipt.json').read_text()); count=0
    if {p.parent.name for p in root.glob('*/case.json')} != set(CELL_ORDER):
        raise RuntimeError('frozen ten-cell set differs')
    for name, expected in receipt['frozen_support'].items():
        if sha(root/'support'/name)!=expected: raise RuntimeError('frozen support changed: '+name)
        count+=1
    for cell, details in receipt['cells'].items():
        case_path=root/cell/'case.json'; case=json.loads(case_path.read_text())
        if sha(case_path)!=details['case_sha256']: raise RuntimeError('frozen case changed: '+cell)
        for name, expected in details['prompt_sha256'].items():
            if sha(root/cell/name)!=expected: raise RuntimeError('frozen prompt changed: '+cell+'/'+name)
        for name, expected in receipt['runtime_sha256'][cell].items():
            if sha(Path(case['sim'])/name)!=expected: raise RuntimeError('frozen runtime changed: '+cell+'/'+name)
            count+=1
    return count

def verify_context(root, launch):
    root,launch=Path(root),Path(launch)
    manifest=json.loads((launch/'launch-manifest.json').read_text())
    for name,expected in manifest['files'].items():
        if sha(launch/name)!=expected: raise RuntimeError('add-node launch changed: '+name)
    base=root/'launch-20261011-r9'
    if sha(base/'launch-manifest.json')!=BASE_MANIFEST_SHA: raise RuntimeError('original r9 manifest changed')
    for name,expected in json.loads((base/'launch-manifest.json').read_text())['files'].items():
        if sha(base/name)!=expected: raise RuntimeError('original r9 file changed: '+name)
    proof=root/'coordination/replacement-r9-old-job-stopped.json'
    if sha(proof)!=OLD_PROOF_SHA or json.loads(proof.read_text()).get('nodes')!=3:
        raise RuntimeError('original three-node stopped proof changed')
    baseline=json.loads((launch/'add-node3-baseline.json').read_text())
    if sha(root/'prepare-receipt.json')!=baseline['prepare_receipt_sha256']:
        raise RuntimeError('original prepare receipt changed')
    historical=root/'coordination/primary-successor-repair51-attempt2-review-r1.json'
    if sha(historical)!=baseline['historical_mechanism_review_sha256'] or json.loads(historical.read_text()).get('accepted') is not True:
        raise RuntimeError('accepted historical mechanism receipt changed')
    if set(baseline['registry_sha256'])!={f'worker-r9-node{i}.json' for i in range(3)}:
        raise RuntimeError('baseline must bind exactly the existing three nodes')
    for name,expected in baseline['registry_sha256'].items():
        p=root/'queue/r9-worker-registry'/name; d=json.loads(p.read_text())
        if sha(p)!=expected or d.get('node_count')!=3 or d.get('launch_manifest_sha256')!=BASE_MANIFEST_SHA:
            raise RuntimeError('original exclusive registry changed: '+name)
    count=verify_core(root)
    if count!=289: raise RuntimeError('frozen runtime count differs')
    return {'manifest_sha256':sha(launch/'launch-manifest.json'), 'runtime_hashes_verified':count}

def require_release(root, launch):
    root,launch=Path(root),Path(launch); context=verify_context(root,launch)
    release=json.loads((root/'coordination/dlc-add-node3-release.json').read_text())
    expected={'released':True,'reviewer_thread':REVIEWER,'operation':'add_one_node',
              'global_node_id':3,'existing_nodes':3,'resulting_nodes':4,'GPU':8,
              'job_name':JOB,'max_minutes':MAX_MINUTES,'campaign_root':str(root),
              'source_manifest_sha256':context['manifest_sha256'],
              'base_manifest_sha256':BASE_MANIFEST_SHA,'original_stop_proof_sha256':OLD_PROOF_SHA}
    if any(type(release.get(k)) is not type(v) or release.get(k)!=v for k,v in expected.items()):
        raise RuntimeError('no exact primary release for one new node3 on this existing queue')
    log=root/'coordination/platform-preflight-add-node3.log'
    if release.get('preflight_sha256')!=sha(log): raise RuntimeError('released preflight binding differs')
    text=log.read_text()
    if 'preflight 通过(rc=0)' not in text or '"environment_preflight": "passed"' not in text:
        raise RuntimeError('fresh add-node environment preflight not passed')
    return release

def available_cells(root):
    root=Path(root)
    return [name for name in CELL_ORDER if not (root/'queue/claims'/name).exists()
            and not (root/name/'campaign_state.json').exists()]

def require_unused(root):
    root=Path(root)
    if (root/'queue/r9-worker-registry/worker-r9-node3.json').exists():
        raise RuntimeError('node3 already registered; never restart')
    if (root/'coordination/submissions/add-node3').exists():
        raise RuntimeError('submission journal already exists; inspect, never retry')
    if not available_cells(root): raise RuntimeError('no unclaimed cells; do not allocate an empty job')

def register_worker(root, launch, node, nodes):
    if type(node) is not int or type(nodes) is not int or (node,nodes)!=(3,4):
        raise RuntimeError('add-node release permits only global node3/count4')
    root,launch=Path(root),Path(launch); release=require_release(root,launch)
    if not available_cells(root): raise RuntimeError('no unclaimed cells at startup; no services started')
    # Original node_count=3 remains historical; the new registry declares only its addition.
    path=root/'queue/r9-worker-registry/worker-r9-node3.json'
    write_once(path,{'worker':'worker-r9-node3','global_node_id':3,'node_count':4,
                    'existing_nodes':3,'operation':'add_one_node','host':os.uname().nodename,
                    'pid':os.getpid(),'started_at':time.time(),
                    'launch_manifest_sha256':sha(launch/'launch-manifest.json'),
                    'base_manifest_sha256':BASE_MANIFEST_SHA,'original_stop_proof_sha256':OLD_PROOF_SHA,
                    'add_node_release_sha256':sha(root/'coordination/dlc-add-node3-release.json')})
    return 'worker-r9-node3'


def listed_job_ids(text):
    """The exact display_name query may truncate Name; any JobId row is a duplicate."""
    ids=[]
    for line in text.splitlines():
        fields=[s.strip() for s in line.split('|')]
        if len(fields)>=9 and re.fullmatch(r'dlc[a-z0-9]{10,}',fields[2]): ids.append(fields[2])
    return ids
