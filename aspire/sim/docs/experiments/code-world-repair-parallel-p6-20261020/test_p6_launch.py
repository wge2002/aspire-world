"""P6-only mixed-N integration, legacy fallback and frozen launch dependency."""
from pathlib import Path
import importlib.util, json, subprocess, sys
from types import SimpleNamespace
import pytest
HERE = Path(__file__).resolve().parent
SIM = HERE.parents[2]
sys.path[:0] = [str(SIM/'scripts/common/node_layout'), str(SIM/'scripts/common')]
import unit_supervisor as supervisor
import unit_layout as layout
spec = importlib.util.spec_from_file_location('p6_launch_deadlines', HERE/'support/full_deadlines.py')
d = importlib.util.module_from_spec(spec);spec.loader.exec_module(d)

def case(n):return {'heldout_parallel':n,'sweep_parallel':n,'trial_timeout':900}

@pytest.mark.parametrize('n,bound,cell', [(1,51120,139320),(2,25620,113820),(4,13380,101580)])
def test_case_and_remaining_seed_sizing(n,bound,cell):
 assert not d.check_case(case(n))
 assert d.heldout_deadline(50,900,n)==bound
 assert d.cell_timeout(case(n))==cell
 assert d.heldout_deadline(6,900,n)==120+-(-6//n)*1020

@pytest.mark.parametrize('n', [True,0,3,8,'4'])
def test_bad_parallelism_refused(n):
 assert d.check_case(case(n))
 with pytest.raises(ValueError):d.cell_timeout(case(n))

@pytest.mark.parametrize('change', [{'sweep_parallel':2},{'trial_timeout':901}])
def test_mismatch_cannot_silently_underbudget(change):
 c=case(4)|change
 assert d.check_case(c)
 with pytest.raises(ValueError):d.cell_timeout(c)

def test_node_uses_slowest_assigned_case_with_legacy_fallback():
 assert supervisor.node_cell_timeout([case(4)]*4,d)==101580
 assert supervisor.node_cell_timeout([case(4),case(4),case(2),case(1)],d)==139320
 legacy=SimpleNamespace(DEVELOPMENT=10,HELDOUT=20,FINALIZE_MARGIN=3)
 assert supervisor.node_cell_timeout([case(4)],legacy)==33

def test_final_node_windows_allow_all_existing_compat_attempts():
 for timeout,window in [(101580,1890),(139320,2520)]:
  assert window*60 > timeout+d.SERVICE_STARTUP+supervisor.COMPAT_ATTEMPTS*d.NATIVE_COMPAT+supervisor.CLAIM_STOP_RESERVE+90
  assert window>=supervisor.minimum_window_minutes(timeout)

def test_staged_frozen_tracker_dependency_and_fresh_cases():
 root=Path('/mnt/home/gewang/experiments/code-world-repair-parallel-p6-20261020-r3')
 result=subprocess.run([sys.executable,'-c',
   'import sys;sys.path[:0]=[sys.argv[1],sys.argv[2]];import native_world_campaign;import cell_process_tree as u;assert hasattr(u,"CellProcessTree");print(u.__file__)',
   str(Path(json.loads((root/"drawer_p6_r1/case.json").read_text())["sim"])/"scripts/libero"),str(root/'support')],capture_output=True,text=True,check=True)
 assert str(root/'support/cell_process_tree.py') in result.stdout
 study=json.loads((root/'launch/study.json').read_text())
 all_cells=[root/c for c in study['cells']]
 a=layout.node_cells(study,all_cells,0,2);b=layout.node_cells(study,all_cells,1,2)
 assert len(a)==len(b)==4 and not set(a)&set(b)
 assert root/'drawer_p6_r2' in a and root/'drawer_p6_r1' in b
 for cell in all_cells:
  c=json.loads((cell/'case.json').read_text())
  assert not d.check_case(c)
  assert not list((cell/'outputs').rglob('development_state.json'))
