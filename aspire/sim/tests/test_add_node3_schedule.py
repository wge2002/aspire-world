import importlib.util,json,multiprocessing,sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pytest
DOC=Path(__file__).resolve().parents[1]/'docs/experiments/code-world-runtime-p2-20261011'
sys.path.insert(0,str(DOC));sys.path.insert(0,str(DOC.parents[2]/'scripts/common'))
import schedule_add_node3 as add
import schedule_r9 as old

def module(name):
 s=importlib.util.spec_from_file_location(name,DOC/name);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
new=module('dlc-supervisor-add-node3.py');previous=module('dlc-supervisor-r9.py')

def setup(root,monkeypatch):
 (root/'coordination').mkdir();(root/'queue/r9-worker-registry').mkdir(parents=True)
 base=root/'launch-20261011-r9';base.mkdir();(base/'launch-manifest.json').write_text('{"files":{}}')
 monkeypatch.setattr(add,'BASE_MANIFEST_SHA',old.sha(base/'launch-manifest.json'))
 proof=root/'coordination/replacement-r9-old-job-stopped.json';proof.write_text('{"nodes":3}')
 monkeypatch.setattr(add,'OLD_PROOF_SHA',old.sha(proof));registry={}
 for i in range(3):
  f=root/f'queue/r9-worker-registry/worker-r9-node{i}.json';f.write_text(json.dumps({'node_count':3,'launch_manifest_sha256':add.BASE_MANIFEST_SHA}));registry[f.name]=old.sha(f)
 historical=root/'coordination/primary-successor-repair51-attempt2-review-r1.json';historical.write_text('{"accepted":true}')
 prepare=root/'prepare-receipt.json';prepare.write_text('{}')
 launch=root/add.LAUNCH;launch.mkdir();b=launch/'add-node3-baseline.json';b.write_text(json.dumps({'registry_sha256':registry,'prepare_receipt_sha256':old.sha(prepare),'historical_mechanism_review_sha256':old.sha(historical)}))
 (launch/'launch-manifest.json').write_text(json.dumps({'files':{b.name:old.sha(b)}}));monkeypatch.setattr(add,'verify_core',lambda root:289)
 log=root/'coordination/platform-preflight-add-node3.log';log.write_text('preflight 通过(rc=0)\n{"environment_preflight": "passed"}')
 r={'released':True,'reviewer_thread':add.REVIEWER,'operation':'add_one_node','global_node_id':3,'existing_nodes':3,'resulting_nodes':4,'GPU':8,'job_name':add.JOB,'max_minutes':3840,'campaign_root':str(root),'source_manifest_sha256':old.sha(launch/'launch-manifest.json'),'base_manifest_sha256':add.BASE_MANIFEST_SHA,'original_stop_proof_sha256':add.OLD_PROOF_SHA,'preflight_sha256':old.sha(log)}
 (root/'coordination/dlc-add-node3-release.json').write_text(json.dumps(r))
 for name in old.CELL_ORDER:
  p=root/name;p.mkdir();(p/'case.json').write_text('{}')
 return launch,r

def test_command_one_additional_job64h():
 c=add.command('plan',Path('/campaign'))
 assert c[c.index('--gpus')+1]=='8' and c[c.index('--max-minutes')+1]=='3840'
 assert c[c.index('--node-id')+1]=='3' and c[c.index('--node-count')+1]=='4'
 assert 'stop' not in c and 'submit' not in c

@pytest.mark.parametrize('key',['released','reviewer_thread','operation','global_node_id','existing_nodes','resulting_nodes','GPU','max_minutes','campaign_root','source_manifest_sha256','base_manifest_sha256','original_stop_proof_sha256','preflight_sha256'])
def test_exact_release(key,tmp_path,monkeypatch):
 launch,r=setup(tmp_path,monkeypatch);add.require_release(tmp_path,launch)
 r[key]='wrong';(tmp_path/'coordination/dlc-add-node3-release.json').write_text(json.dumps(r))
 with pytest.raises(RuntimeError):add.require_release(tmp_path,launch)

def test_existing_proof_and_nodes_remain3_exclusive_new_node(tmp_path,monkeypatch):
 launch,_=setup(tmp_path,monkeypatch)
 paths=[tmp_path/'coordination/replacement-r9-old-job-stopped.json',*sorted((tmp_path/'queue/r9-worker-registry').glob('*.json'))];before=[p.read_bytes() for p in paths]
 assert add.register_worker(tmp_path,launch,3,4)=='worker-r9-node3'
 assert [p.read_bytes() for p in paths]==before
 with pytest.raises(FileExistsError):add.register_worker(tmp_path,launch,3,4)
 for args in [(2,4),(3,3),(True,4)]:
  with pytest.raises(RuntimeError):add.register_worker(tmp_path,launch,*args)

@pytest.mark.parametrize('target',['proof','registry','manifest','file'])
def test_mutations_refused(target,tmp_path,monkeypatch):
 launch,_=setup(tmp_path,monkeypatch)
 p={'proof':tmp_path/'coordination/replacement-r9-old-job-stopped.json','registry':tmp_path/'queue/r9-worker-registry/worker-r9-node0.json','manifest':tmp_path/'launch-20261011-r9/launch-manifest.json','file':launch/'add-node3-baseline.json'}[target];p.write_text('{}')
 with pytest.raises((RuntimeError,KeyError)):add.require_release(tmp_path,launch)

@pytest.mark.parametrize('marker',['registry','journal','no_cells'])
def test_no_duplicate_or_empty_job(tmp_path,monkeypatch,marker):
 setup(tmp_path,monkeypatch);add.require_unused(tmp_path)
 if marker=='registry':(tmp_path/'queue/r9-worker-registry/worker-r9-node3.json').write_text('{}')
 elif marker=='journal':(tmp_path/'coordination/submissions/add-node3').mkdir(parents=True)
 else:
  for name in old.CELL_ORDER:(tmp_path/name/'campaign_state.json').write_text('{}')
 with pytest.raises(RuntimeError):add.require_unused(tmp_path)

def claim_all(root,worker):
 supervisor=new if worker.endswith('3') else previous;found=[]
 while True:
  p=supervisor.claim_next(Path(root),worker)
  if p is None:return found
  found.append(p.name)

def test_four_processes_mix_old3_and_add_node3_no_duplicate(tmp_path):
 for name in old.CELL_ORDER:
  p=tmp_path/name;p.mkdir();(p/'case.json').write_text('{}')
 for name in old.CELL_ORDER[:3]:
  p=tmp_path/'queue/claims'/name;p.mkdir(parents=True);(p/'claim.json').write_text('{}');(tmp_path/name/'campaign_state.json').write_text('{}')
 with ProcessPoolExecutor(4,mp_context=multiprocessing.get_context('fork')) as pool:
  got=list(pool.map(claim_all,[str(tmp_path)]*4,[f'worker-r9-node{i}' for i in range(4)]))
 flat=sum(got,[]);assert len(flat)==len(set(flat))==7 and set(flat)==set(old.CELL_ORDER[3:])

def test_supervisor_only_registration_and64h_change():
 s=(DOC/'dlc-supervisor-r9.py').read_text().replace('80 * 3600','64 * 3600').replace('from schedule_r9 import register_worker','from schedule_add_node3 import register_worker')
 assert (DOC/'dlc-supervisor-add-node3.py').read_text()==s

def test_insufficient_fullcell_time_refuses_claim(tmp_path,monkeypatch):
 from types import SimpleNamespace
 (tmp_path/'prepare-receipt.json').write_text('{}');monkeypatch.setattr(new,'full_deadlines_module',lambda:SimpleNamespace(DEVELOPMENT=50400,HELDOUT=18000,FINALIZE_MARGIN=1800));monkeypatch.setattr(new,'queue_cells',lambda root:[])
 monkeypatch.setattr(new,'claim_next',lambda *a:pytest.fail('must not claim'))
 events=[];args=SimpleNamespace(worker_rank=3,queue=tmp_path,node_stop_at=1)
 assert new.run_queue(args,{},None,tmp_path,None,None,None,None,lambda state,**kw:events.append(state),{},tmp_path)==0
 assert events==['queue_running','node_time_window_exhausted']


def test_truncated_existing_name_is_still_duplicate():
 text='| aspire-runtime-p2-10... | dlc1kydp8rdlb6w1 | user | Running | create | submit | run | finish |\n'
 assert add.listed_job_ids(text)==['dlc1kydp8rdlb6w1']
 assert add.listed_job_ids('[OK] config\n| Name | JobId | UserId | JobStatus | Create | Submit | Run | Finish |')==[]

def test_prepare_receipt_is_bound_to_original_hash(tmp_path,monkeypatch):
 launch,_=setup(tmp_path,monkeypatch);(tmp_path/'prepare-receipt.json').write_text('{"changed":true}')
 with pytest.raises(RuntimeError,match='prepare receipt changed'): add.verify_context(tmp_path,launch)


def test_only_historical_acceptance_is_bound_not_live_ledger(tmp_path,monkeypatch):
 launch,_=setup(tmp_path,monkeypatch)
 (tmp_path/'bowl_p2_r1/campaign_state.json').write_text('{"status":"heldout_running"}')
 (tmp_path/'bowl_p2_r1/development_state.json').write_text('{"selected":"new"}')
 add.require_release(tmp_path,launch)
 historical=tmp_path/'coordination/primary-successor-repair51-attempt2-review-r1.json';historical.write_text('{"accepted":false}')
 with pytest.raises(RuntimeError,match='historical mechanism'):add.require_release(tmp_path,launch)


def test_actual_original_entry_dispatches_only_new_global_id(tmp_path):
 import os,subprocess
 s=(DOC/'dlc-single-node-entry-r9.sh').read_text().replace('. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh',':')
 py=tmp_path/'fake-python';py.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');py.chmod(0o755)
 s=s.replace('/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3',str(py));f=tmp_path/'entry.sh';f.write_text(s)
 r=subprocess.run(['bash',str(f),'--node-id','3','--node-count','4','--case','/case'],env={**os.environ,'RANK':'0','WORLD_SIZE':'1'},capture_output=True,text=True)
 assert r.returncode==0 and '--worker-rank\n3\n--worker-count\n4\n--case\n/case' in r.stdout
