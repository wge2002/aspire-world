#!/usr/bin/env python3
"""P6: fresh P5 method + Task7 parallel + Task8 closeout, mixed per-cell N.
No inference, services, or submissions in this stager. Reuses the verified P5
staging chain; all overridden roots/configuration are fixed before preparation.
"""
from pathlib import Path
import argparse, importlib.util, json, re, sys, hashlib
HERE = Path(__file__).resolve().parent
SIM = HERE.parents[2]
NAME = HERE.name + "-r3"
P5DIR = SIM / 'docs/experiments/code-world-budget-p5-20261019'
P5 = Path('/mnt/home/gewang/experiments/code-world-budget-p5-20261019')
ROOT = Path('/mnt/home/gewang/experiments') / NAME
RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-' + NAME)
NODE_CELLS = {
 0: ('goaltaskbowldrawer_p6_r1', 'bowldrawer_p6_r1', 'objswapsoup_p6_r1', 'drawer_p6_r2'),
 1: ('twomoka_p6_r1', 'mugmicrowave_p6_r1', 'drawer_p6_r1', 'bowldrawerclose_p6_r1')}
N = {c: (1 if c.startswith('twomoka_') else 2 if c.startswith('mugmicrowave_') else 4)
     for node in NODE_CELLS.values() for c in node}

def load(path, name):
 s = importlib.util.spec_from_file_location(name, path); m = importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

p5 = load(P5DIR / 'prepare-budget-study.py', 'p6_p5_stager')
pc, p3, base, layout = p5.pc, p5.p3, p5.base, p5.layout
limits = load(HERE/'support/full_deadlines.py', 'p6_mixed_deadlines')
p5.NAME = NAME; p5.STUDY_DIR = HERE / "r3"; p5.ARM = 'p6'
p5.SMOKE_NAME = NAME + '-native-smoke'
p5.SMOKE_CELL = 'drawer_p6_smoke1'
p5.CELL = re.compile(r'([a-z]+)_p6_(?:r(\d+)|smoke(\d+))')
base.SUPPORT_FILES = tuple(base.SUPPORT_FILES) + ('cell_process_tree.py',)
p5.CELL_ORDER = tuple(c for node in sorted(NODE_CELLS) for c in NODE_CELLS[node])
p5.NODE_CELLS = NODE_CELLS
p5.p5_deadlines = limits
# Explicitly include Task7's common process layer, absent from the old overlay list.
base.C_OVERLAY = tuple(base.C_OVERLAY) + tuple(p for p in (
 'scripts/common/native_cc_trial_process.py',) if p not in base.C_OVERLAY)
p5.OVERLAY.update({p.name: p.resolve() for p in (P5DIR/'parallel/support').glob('*.py')
                  if p.name in base.SUPPORT_FILES})
p5.OVERLAY.update({p.name: p.resolve() for p in (HERE/'support').glob('*.py')
                  if p.name in base.SUPPORT_FILES})
p5.hs.OVERLAY = p5.OVERLAY
p5.repoint(False)
original_case = pc.case_config

def case_config(cell, condition):
 case = original_case(cell, condition)
 case.update(heldout_parallel=N[cell], sweep_parallel=N[cell], trial_threads=2)
 source_cell = 'drawer_p5_r1' if 'smoke' in cell else cell.replace('_p6_', '_p5_')
 previous = json.loads((P5 / source_cell / 'case.json').read_text())
 for key in ('suite','task','env_config'):
  assert case[key] == previous[key], (cell,key,case[key],previous[key])
 case['generation_context'] = case['generation_context'].replace('p3-search + p4 coverage gate + aspire-v1 development budget',
   'p3-search + p4 coverage gate + aspire-v1 development budget + Task7 parallel + Task8 closeout')
 return case
pc.case_config = p3.case_config = base.case_config = case_config
original_study = pc.study

def study():
 s=original_study()
 s['note']='P6 fresh independent C cells: P5 + Task7 parallel + Task8 closeout; per-node pinned queue; per-cell N=1/2/4'
 s['parallel_by_cell']=N
 return s
pc.study=study
original_plan=pc.campaign_plan

def campaign_plan():
 p=original_plan();p.update(study_id=NAME,kind=study()['note'],parallel_by_cell=N,
   case_of={c:c.replace('_p6_','_p5_')+' metadata only; no program/ledger/feedback inherited' for c in N})
 p['launch_shape']['mode']='two independent 1x8-GPU TOP jobs, two 4-GPU units x two slots; node-pinned cells'
 p['development_budget']['freeze'] += '; or outer Task8 phase-B end selects a valid complete best candidate'
 p['development_budget']['phase_a'] += '; stagnation starts a new diagnostic round under the original cumulative deadline'
 return p
pc.campaign_plan=campaign_plan


def additional_verify():
 receipt=json.loads((ROOT/'prepare-receipt.json').read_text());out={};problems=[]
 for cell in N:
  case=json.loads((ROOT/cell/'case.json').read_text());repo=Path(case['sim'])
  errors=limits.check_case(case)
  if case['heldout_parallel']!=N[cell]:errors.append('N differs from frozen plan')
  task=repo/'outputs/libero_fix_loop'/case['suite']/case['task']
  if task.exists() and any(task.iterdir()):errors.append('cell does not start from empty task artifacts')
  for filename in ['scripts/common/native_cc_trial_process.py','scripts/libero/native_world_protocol.py',
                   'scripts/libero/native_world_fixloop_state.py','scripts/libero/native_world_heldout.py',
                   'scripts/libero/executable_world_profile.py']:
   if (repo/filename).read_bytes()!=(SIM/filename).read_bytes():errors.append('runtime differs from final source: '+filename)
  if not (ROOT/'launch/unit_supervisor.py').is_file():errors.append('frozen tracker dependency absent')
  out[cell]={'N':N[cell],'cell_timeout_seconds':limits.cell_timeout(case),'problems':errors}
  problems.extend(cell+': '+e for e in errors)
 assert len({json.loads((ROOT/c/'case.json').read_text())['claude_config_dir'] for c in N})==len(N)
 return {'cells':out,'problems':problems}


def main():
 parser=argparse.ArgumentParser();g=parser.add_mutually_exclusive_group(required=True)
 g.add_argument('--prepare',action='store_true');g.add_argument('--verify',action='store_true')
 parser.add_argument('--native-smoke',action='store_true');args=parser.parse_args()
 if args.native_smoke:
  global ROOT, RUNTIME
  p5.SMOKE['on']=True
  N.clear();N[p5.SMOKE_CELL]=4
  where=p5.repoint(True);ROOT=Path(where['result_parent']);RUNTIME=Path(where['runtime_root'])
 if args.prepare:
  receipt=pc.prepare()
  base.freeze_bytes(Path(__file__),ROOT/'frozen-prepare-p6-study.py')
  base.record(ROOT/'p6-receipt.json',{'name':NAME,'node_cells':NODE_CELLS,'N':N,
    'overlay':{n:str(p) for n,p in p5.OVERLAY.items()},'source_root':str(SIM),'prepared_at':receipt['prepared_at']})
  return 0
 result=pc.verify();additional=additional_verify()
 target=HERE/('smoke-verify-extra-r3.json' if args.native_smoke else 'verify-extra-r3.json')
 if target.exists():
  assert json.loads(target.read_text())==additional, 'verification evidence changed; preserve and inspect'
 else:base.record(target,additional)
 print(json.dumps(additional,indent=2))
 return int(result['status']!='verified' or bool(additional['problems']))

if __name__=='__main__':raise SystemExit(main())
