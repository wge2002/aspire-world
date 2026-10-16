"""Development diagnostics cannot enter initial stage or cross attempt/cell boundaries."""
import importlib.util,json,sys
from pathlib import Path
import pytest
DOC=Path(__file__).resolve().parents[1]/'docs/experiments/code-world-runtime-p2-20261011'
sys.path.insert(0,str(DOC))
spec=importlib.util.spec_from_file_location('recovery_diagnosis_test',DOC/'recover-native-gate-with-diagnosis.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def fixture(tmp_path):
    control=tmp_path/'control';control.mkdir();sim=tmp_path/'sim';task=sim/'outputs/libero_fix_loop/suite/task';attempt=task/'development/initial/seed_53/attempt_1';attempt.mkdir(parents=True)
    source=attempt/'code.py';source.write_text('actual immutable development snapshot')
    case={'sim':str(sim),'suite':'suite','task':'task','dev_seeds':list(range(51,66))};(control/'case.json').write_text(json.dumps(case))
    row={'directory':'development/initial/seed_53/attempt_1','spends_retry':True,'status':'complete','phase':'initial','seed':53,'bundle':{'policy':'pair'}}
    (task/'development_state.json').write_text(json.dumps({'trials':[row]}))
    packet={'for_repair_only':True,'requires_original_initial_coverage':True,'native_session':'session','native_worker':'worker','worker_request':'independently diagnose actual residual','semantic_rejection':'feedback printed, not consumed','evidence':[{'seed':53,'selected_attempt':row['directory'],'bundle':row['bundle'],'semantic_accepted':False,'actual_files':{str(source):module.sha(source)}}]}
    path=tmp_path/'diagnosis.json';path.write_text(json.dumps(packet));plan={'stage':'repair','phase':'repair','native_session':'session','native_worker':'worker'}
    return control,source,packet,path,plan


def test_repair_attachment_validates_actual_snapshot_and_retains_feedback(tmp_path):
    control,source,packet,path,plan=fixture(tmp_path);before=(control/'case.json').read_bytes();text=module.development_diagnosis(control,plan,path)
    assert packet['worker_request'] in text and packet['semantic_rejection'] in text
    assert 'ordinary native tools' in text and (control/'case.json').read_bytes()==before


@pytest.mark.parametrize('stage',['initial_batch','close_infra'])
def test_diagnosis_cannot_be_delivered_before_original_repair(tmp_path,stage):
    control,source,packet,path,plan=fixture(tmp_path);plan.update(stage=stage,phase='initial' if stage=='initial_batch' else None)
    with pytest.raises(module.GateError,match='only at the next original repair'):module.development_diagnosis(control,plan,path)


@pytest.mark.parametrize('mutation',['worker','heldout','other_cell','changed_snapshot','missing_actual_hashes','noncompleted_attempt'])
def test_diagnosis_rejects_foreign_or_changed_evidence(tmp_path,mutation):
    control,source,packet,path,plan=fixture(tmp_path)
    if mutation=='worker':packet['native_worker']='other'
    elif mutation=='heldout':packet['evidence'][0]['seed']=1
    elif mutation=='other_cell':packet['evidence'][0]['actual_files']={str(path):module.sha(path)}
    elif mutation=='changed_snapshot':source.write_text('changed')
    elif mutation=='missing_actual_hashes':packet['evidence'][0]['actual_files']={}
    else:
        case=json.loads((control/'case.json').read_text());ledger=Path(case['sim'])/'outputs/libero_fix_loop/suite/task/development_state.json';d=json.loads(ledger.read_text());d['trials'][0]['status']='running';ledger.write_text(json.dumps(d))
    path.write_text(json.dumps(packet))
    with pytest.raises(module.GateError):module.development_diagnosis(control,plan,path)
