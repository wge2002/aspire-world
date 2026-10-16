"""Safe original-stage completion and specifically reviewed evidence precede one repair."""
import importlib.util,json
from pathlib import Path
import pytest
DOC=Path(__file__).resolve().parents[1]/'docs/experiments/code-world-runtime-p2-20261011'
spec=importlib.util.spec_from_file_location('initial_to_repair_once',DOC/'continue-initial-to-repair-once.py');controller=importlib.util.module_from_spec(spec);spec.loader.exec_module(controller)

def fixture(tmp_path,monkeypatch):
    control=tmp_path/'control';control.mkdir();actor=tmp_path/'actor';actor.mkdir();task=tmp_path/'sim/outputs/libero_fix_loop/suite/task';task.mkdir(parents=True)
    (control/'case.json').write_text(json.dumps({'sim':str(tmp_path/'sim'),'suite':'suite','task':'task','dev_seeds':list(range(51,66))}))
    cp={'status':'native_gate_repair_checkpoint','coordinator_exit_code':0,'stage':'initial_batch'};(control/'native_work_continuation_checkpoint.json').write_text(json.dumps(cp))
    rows=[{'seed':s,'phase':'initial','status':'complete','sandbox_rc':0} for s in range(51,66)];(task/'development_state.json').write_text(json.dumps({'trials':rows}))
    file=task/'actual-seed52-code.py';file.write_text('immutable real evidence');digest=controller.sha(file)
    old={'accepted':False,'semantic_failure':True,'reason':'feedback printed before action, not consumed','selected_attempt':'development/initial/seed_52/attempt_1','bundle':{'policy':'pair'},'native_session':'session','native_worker':'worker','actual_attempt_files':{str(file):digest}}
    rejection=tmp_path/'rejection.json';rejection.write_text(json.dumps(old));packet=tmp_path/'packet.json';packet.write_text('{}')
    expected={'recover-native-gate-with-diagnosis.py':'e282b2fbc0dcce2b3a87facfb0cacd4ece6e412233acb1961b9f56c68eb0fe72','startup_gate.py':'d77d2234d976bc628a95f413806429bc2f1c471e34c6fd6ff9b45e4431097bc8','recovery_guard.py':'c4d844b246a5ac86b8b7163a4131c1c3d1b5eabef5221df8d5f83a1a7315d965'}
    (actor/'source-manifest.json').write_text(json.dumps({'files':expected}))
    real_sha=controller.sha
    def sha(path):
        p=Path(path)
        if p==rejection:return 'f16eb436776fbad2f82edd1c4f7c7d121850ff92f27d0745ca4b8fa21b6b74e9'
        if p==packet:return '94795f5bc4b338c64e93383d8c21871f3bbe22c663ac957ec5275ef322b8fa96'
        if p.parent==actor and p.name in expected:return expected[p.name]
        return real_sha(p)
    monkeypatch.setattr(controller,'sha',sha)
    gate={'state':'passed','binding_sha256':'fresh-binding',**{k:old[k] for k in ('selected_attempt','bundle','native_session','native_worker')},'evidence_files':{str(file):digest}}
    return control,actor,rejection,packet,gate,file,task,cp


def test_complete_original_batch_binds_fresh_explicit_semantic_rejection(tmp_path,monkeypatch):
    control,actor,rejection,packet,gate,file,task,cp=fixture(tmp_path,monkeypatch)
    review=controller.qualify(control,actor,rejection,packet,gate)
    assert not review['accepted'] and review['semantic_failure']
    assert review['binding_sha256']=='fresh-binding' and review['ledger_sha256']==controller.sha(task/'development_state.json')
    assert review['attempts_refunded']==0 and review['selected_attempt']==gate['selected_attempt']


@pytest.mark.parametrize('change',['different_candidate','different_pair','different_worker','gate_failure','no_safe_pause','incomplete_initial','running_trial','changed_evidence'])
def test_handoff_refuses_unreviewed_or_unsafe_state(tmp_path,monkeypatch,change):
    control,actor,rejection,packet,gate,file,task,cp=fixture(tmp_path,monkeypatch)
    if change=='different_candidate':gate['selected_attempt']='development/initial/seed_65/attempt_1'
    elif change=='different_pair':gate['bundle']={'policy':'unreviewed'}
    elif change=='different_worker':gate['native_worker']='other'
    elif change=='gate_failure':gate['state']='not_passed'
    elif change=='no_safe_pause':cp['coordinator_exit_code']=1;(control/'native_work_continuation_checkpoint.json').write_text(json.dumps(cp))
    elif change in {'incomplete_initial','running_trial'}:
        p=task/'development_state.json';d=json.loads(p.read_text())
        if change=='incomplete_initial':d['trials'].pop()
        else:d['trials'][-1]['status']='running'
        p.write_text(json.dumps(d))
    else:file.write_text('changed evidence')
    with pytest.raises(RuntimeError):controller.qualify(control,actor,rejection,packet,gate)
