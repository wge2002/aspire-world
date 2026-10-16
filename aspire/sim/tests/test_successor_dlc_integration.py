"""Outer routing/release tests; never submit jobs or alter experiment evidence."""
import importlib.util,sys
from pathlib import Path
import pytest
DOC=Path(__file__).resolve().parents[1]/'docs/experiments/code-world-runtime-p2-20261011';sys.path.insert(0,str(DOC))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/common'))
def module(file):
    spec=importlib.util.spec_from_file_location(file.replace('.','_'),DOC/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
supervisor=module('dlc-supervisor-r8.py');submit=module('submit-runtime-study-r8.py')
def successor():return {'successor_transition':'receipt.json','predecessor_native_worker':'old','native_worker':'new','successor_transition_sha256':'frozen'}
def test_single_bowl_uses_explicit_successor_adapter():
    driver,gate,receipt=supervisor.continuation_route('bowl_p2_r1',successor())
    assert driver=='continuation_driver_successor_r1.py' and gate=='startup_gate_successor_r1'
    assert receipt['predecessor']=='old' and receipt['successor']=='new'
@pytest.mark.parametrize('cell',['bowl_p2_r2','drawer_p2_r1','wine_p2_r2'])
def test_other_cells_cannot_inherit_successor(cell):
    with pytest.raises(RuntimeError):supervisor.continuation_route(cell,successor())
def test_normal_continuation_keeps_reviewed_original_path():
    assert supervisor.continuation_route('drawer_p2_r1',{})==('continuation_driver.py','startup_gate',None)
@pytest.mark.parametrize('mutation',['notreleased','reviewermissing','manifestchanged','bindingchanged','undisclosed'])
def test_release_is_bound_to_exact_deployment_and_real_mechanism(mutation):
    release={'released':True,'reviewer':'main','source_manifest_sha256':'m','gate_binding_sha256':'b'}
    current={'identity_change_disclosed':True,'binding_sha256':'b'};deployment={'launch_manifest_sha256':'m'}
    submit.require_submit_release(release,current,deployment)
    if mutation=='notreleased':release['released']=False
    elif mutation=='reviewermissing':release.pop('reviewer')
    elif mutation=='manifestchanged':release['source_manifest_sha256']='stale'
    elif mutation=='bindingchanged':release['gate_binding_sha256']='stale'
    else:current['identity_change_disclosed']=False
    with pytest.raises(RuntimeError):submit.require_submit_release(release,current,deployment)
def test_same_single_submit_journal_resources_and_no_submission_in_plan():
    cmd=submit.command('plan');assert cmd[1]=='plan' and cmd[cmd.index('--gpus')+1]=='40'
    assert cmd[cmd.index('--max-minutes')+1]=='2700' and cmd[cmd.index('--priority')+1]=='9'
    assert cmd[-1].endswith('launch-20261011-r8/dlc-queue-entry.sh')
