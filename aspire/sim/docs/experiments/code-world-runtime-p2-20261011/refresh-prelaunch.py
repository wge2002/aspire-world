"""Rebind prelaunch inputs once, retaining all previous staging evidence."""
import importlib.util,json,shutil,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('runtime_stage',HERE/'prepare-runtime-study.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
b=s.base
receipt_path=b.PARENT/'prepare-receipt.json'
r=json.loads(receipt_path.read_text())
archive=b.PARENT/'coordination/prelaunch-revision-1'
archive.mkdir(exist_ok=False)
shutil.copy2(receipt_path,archive/'prepare-receipt.json')
for cell,e in r['cells'].items():
 c=json.loads(Path(e['case']).read_text())
 assert b.digest(Path(e['case']))==e['case_sha256']
 assert not (Path(c['control'])/'campaign_state.json').exists()
 assert not (Path(c['control'])/'native_smoke_state.json').exists()
 assert not (Path(c['sim'])/'outputs/libero_fix_loop'/c['suite']/c['task']).exists()
 d=archive/cell;d.mkdir();shutil.copy2(e['case'],d/'case.json')
 shutil.copy2(c['runtime_manifest'],d/'runtime-manifest.json')
for name in b.SUPPORT_FILES:
 target=b.PARENT/'support'/name
 shutil.copy2(target,archive/('support-'+name))
 shutil.copy2(b.SUPPORT/name,target)
for name in s.STUDY_LAUNCH_FILES:
 shutil.copy2(s.LAUNCH/name,archive/('launch-'+name))
 shutil.copy2(b.SUPPORT/name,s.LAUNCH/name)
shutil.copy2(s.LAUNCH/'launch-manifest.json',archive/'launch-manifest.json')
m=json.loads((s.LAUNCH/'launch-manifest.json').read_text())
m['files']={n:b.digest(s.LAUNCH/n) for n in s.LAUNCH_FILES}
m['prelaunch_revision']=2
b._write(s.LAUNCH/'launch-manifest.json',m)
r['frozen_support']={n:b.digest(b.PARENT/'support'/n) for n in b.SUPPORT_FILES}
for cell,e in r['cells'].items():
 c=json.loads(Path(e['case']).read_text());sim=Path(c['sim'])
 for name in b.C_OVERLAY:
  target=sim/name;target.parent.mkdir(parents=True,exist_ok=True)
  shutil.copy2(b.ENGINEERING/name,target)
 c['runtime_manifest']=str(Path(c['control'])/'runtime-manifest-r2.json')
 proc=subprocess.run([str(sim/'.venv-libero/bin/python3'),'-c',b.MANIFEST_PROGRAM,
                     str(sim/'scripts/common'),str(sim),json.dumps([str(p) for p in s.external_inputs(c)]),
                     json.dumps([c['claude_bin']])],cwd=sim,capture_output=True,text=True,check=True)
 b.record(Path(c['runtime_manifest']),json.loads(proc.stdout))
 c['runtime_manifest_sha256']=b.digest(Path(c['runtime_manifest']))
 b._write(Path(e['case']),c)
 e.update(case_sha256=b.digest(Path(e['case'])),runtime_manifest=c['runtime_manifest'],
          runtime_manifest_sha256=c['runtime_manifest_sha256'])
 r['runtime_sha256'][cell]={n:b.digest(sim/n) for n in b.C_OVERLAY}
r['prelaunch_revision']=2;r['previous_revision']=str(archive)
r['prepare_source_sha256']=b.digest(HERE/'prepare-runtime-study.py')
b._write(receipt_path,r)
print(json.dumps({'prelaunch_revision':2,'cells':len(r['cells']),'archive':str(archive)}))
