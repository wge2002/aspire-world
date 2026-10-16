"""Record one safe prelaunch revision; preserve zero-attempt ledger and logs."""
import importlib.util,json,shutil,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('runtime_stage',HERE/'prepare-runtime-study.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s);b=s.base
receipt_path=b.PARENT/'prepare-receipt.json';r=json.loads(receipt_path.read_text())
assert r.get('prelaunch_revision')==2
archive=b.PARENT/'coordination/prelaunch-revision-2'
resume='--resume-identity-import-failure' in sys.argv
assert archive.exists() if resume else not archive.exists()
# Validate EVERY cell and original process boundary before the first mutation.
validated=[]
for cell,e in r['cells'].items():
 c=json.loads((archive/cell/'case.json' if resume else Path(e['case'])).read_text());control=Path(c['control'])
 assert b.digest(archive/cell/'case.json' if resume else Path(e['case']))==e['case_sha256']
 if resume:
  active=json.loads(Path(e['case']).read_text())
  assert {k for k in set(c)|set(active) if c.get(k)!=active.get(k)} <= {'runtime_manifest','runtime_manifest_sha256'}
 assert not (control/'campaign_state.json').exists()
 assignment=control/'native/assignment-state.json'
 assert not assignment.exists() or not json.loads(assignment.read_text()).get('primary')
 smoke=control/'native_smoke_state.json'
 if smoke.exists():assert cell=='bowl_p2_r1' and json.loads(smoke.read_text())['status']=='blocked'
 task=control/'outputs/libero_fix_loop'/c['suite']/c['task'];ledger=task/'development_state.json'
 if task.exists():
  assert cell=='bowl_p2_r1' and ledger.exists()
  d=json.loads(ledger.read_text());assert d['trials']==[] and not d.get('selected') and not d.get('stage1_complete')
  assert not d.get('aliases') and not d.get('rejected')
  assert all(p.name=='development_state.json' for p in task.iterdir())
 validated.append((cell,e,c,ledger if ledger.exists() else None))
for pid in (1211317,1210534,1214997,1216236):
 p=Path('/proc')/str(pid)/'cmdline'
 assert not p.exists(), f'owned old native process still exists: {pid}'
if not resume:
 archive.mkdir();shutil.copy2(receipt_path,archive/'prepare-receipt.json')
for cell,e,c,ledger in validated:
 if resume:continue
 d=archive/cell;d.mkdir();shutil.copy2(e['case'],d/'case.json');shutil.copy2(c['runtime_manifest'],d/'runtime-manifest.json')
 for name in ('worker-prompt.md','coordinator-prompt.md','staged-inputs.json','native_smoke_state.json'):
  p=Path(c['control'])/name
  if p.exists():shutil.copy2(p,d/name)
 if ledger:shutil.copy2(ledger,d/'empty-development-state-r2.json')
for name in b.SUPPORT_FILES:
 target=b.PARENT/'support'/name
 if not resume and target.exists():shutil.copy2(target,archive/('support-'+name))
 shutil.copy2(b.SUPPORT/name,target)
for name in s.STUDY_LAUNCH_FILES:
 if not resume:shutil.copy2(s.LAUNCH/name,archive/('launch-'+name))
 shutil.copy2(b.SUPPORT/name,s.LAUNCH/name)
if not resume:shutil.copy2(s.LAUNCH/'launch-manifest.json',archive/'launch-manifest.json')
m=json.loads((s.LAUNCH/'launch-manifest.json').read_text());m['files']={n:b.digest(s.LAUNCH/n) for n in s.LAUNCH_FILES};m['prelaunch_revision']=3
b._write(s.LAUNCH/'launch-manifest.json',m)
r['frozen_support']={n:b.digest(b.PARENT/'support'/n) for n in b.SUPPORT_FILES}
for cell,e,c,ledger in validated:
 sim=Path(c['sim'])
 for name in b.C_OVERLAY:
  target=sim/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(b.ENGINEERING/name,target)
  old=e['source_differences']['overlay'].get(name,{})
  e['source_differences']['overlay'][name]={'source':str(b.ENGINEERING/name),'sha256':b.digest(target),'replaced_sha256':old.get('sha256')}
 e['source_differences']['common_overlay']=list(s.COMMON_OVERLAY);e['source_differences']['c_only_overlay']=list(s.C_ONLY_OVERLAY)
 c['runtime_manifest']=str(Path(c['control'])/'runtime-manifest-r3.json')
 proc=subprocess.run([str(sim/'.venv-libero/bin/python3'),'-c',b.MANIFEST_PROGRAM,str(sim/'scripts/common'),str(sim),json.dumps([str(p) for p in s.external_inputs(c)]),json.dumps([c['claude_bin']])],cwd=sim,capture_output=True,text=True,check=True)
 manifest_path=Path(c['runtime_manifest']);new_manifest=json.loads(proc.stdout)
 if manifest_path.exists():assert resume and json.loads(manifest_path.read_text())==new_manifest
 else:b.record(manifest_path,new_manifest)
 c['runtime_manifest_sha256']=b.digest(manifest_path);b._write(Path(e['case']),c)
 e.update(case_sha256=b.digest(Path(e['case'])),runtime_manifest=c['runtime_manifest'],runtime_manifest_sha256=c['runtime_manifest_sha256'])
 e['render']=s.render_prompts(c);e['prompt_sha256']=s.verify_prompts(c,{})
 r['runtime_sha256'][cell]={n:b.digest(sim/n) for n in b.C_OVERLAY}
 if ledger:
  code="import sys,json;from pathlib import Path;c=json.loads(Path(sys.argv[1]).read_text());sys.path.insert(0,c['python_root']);sys.path.insert(0,'scripts/libero');import native_world_protocol as p;print(json.dumps(p.identity(c,Path.cwd())))"
  proc=subprocess.run([str(sim/'.venv-libero/bin/python3'),'-c',code,str(e['case'])],cwd=sim,capture_output=True,text=True,check=True)
  d=json.loads(ledger.read_text());identity=json.loads(proc.stdout)
  diff={k for k in set(d['identity'])|set(identity) if d['identity'].get(k)!=identity.get(k)}
  assert diff=={'runtime_manifest_sha256'}, diff
  d['prelaunch_identity_migrations']=[{'revision':3,'reason':'zero-trial native prompt transport prelaunch repair','previous_ledger':str(archive/cell/'empty-development-state-r2.json'),'previous_ledger_sha256':b.digest(archive/cell/'empty-development-state-r2.json'),'changed_identity_fields':sorted(diff),'previous_identity':d['identity'],'next_identity':identity,'attempts_added':0,'attempts_refunded':0}]
  d['identity']=identity;b._write(ledger,d)
 smoke=Path(c['control'])/'native_smoke_state.json'
 if smoke.exists():smoke.rename(archive/cell/'native_smoke_state-r2.json')
 staged=Path(c['control'])/'staged-inputs.json';d=json.loads(staged.read_text());d.update(source_differences=e['source_differences'],frozen_support=r['frozen_support'],external_inputs=[str(p) for p in s.external_inputs(c)],prelaunch_revision=3);b._write(staged,d)
r['prelaunch_revision']=3;r['previous_revision']=str(archive);r['resumed_identity_import_failure']=resume
r['prepare_source_sha256']=b.digest(HERE/'prepare-runtime-study.py')
frozen=b.PARENT/'frozen-prepare-runtime-study-r3.py';shutil.copy2(HERE/'prepare-runtime-study.py',frozen);r['frozen_prepare_source']=str(frozen)
b._write(receipt_path,r)
print(json.dumps({'prelaunch_revision':3,'cells':len(r['cells']),'archive':str(archive),'charged_trials':0}))
