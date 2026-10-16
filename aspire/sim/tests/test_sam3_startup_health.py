"""Actual HTTP/body/mask semantics and queue admission ordering, outside robot budgets."""
import base64, contextlib, gzip, hashlib, importlib.util, io, json, sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
from PIL import Image
import pytest
import test_runtime_study_support as queue_fixture

DOC=Path(__file__).resolve().parents[1]/'docs/experiments/code-world-runtime-p2-20261011'
spec=importlib.util.spec_from_file_location('sam3_health_test',DOC/'sam3_service_health.py')
health=importlib.util.module_from_spec(spec);spec.loader.exec_module(health)

def proposal(score=.9,mask=None,**changes):
    if mask is None:mask=np.array([[0,1,1],[0,1,0]],dtype=np.uint8)
    row={'score':score,'shape':[2,3],'mask_base64':base64.b64encode(mask.tobytes()).decode(),'box':[1,0,3,2]}
    row.update(changes);return row


def test_highest_score_valid_skips_empty_high_and_low_proposals():
    rows=[proposal(.99,np.zeros((2,3),dtype=np.uint8)),proposal(.8),proposal(.1,np.zeros((2,3),dtype=np.uint8)),proposal(.7)]
    mask,receipt=health.select_valid_mask({'results':rows},2,3)
    assert mask.sum()==3 and receipt['proposal_index']==1 and receipt['score']==.8
    assert receipt['higher_ranked_invalid_proposals'][0]['proposal_index']==0
    assert receipt['raw_proposals']==4


@pytest.mark.parametrize('change',[{'shape':[3,2]},{'mask_base64':'???'},{'mask_base64':base64.b64encode(b'abc').decode()}, {'box':[0,0,4,2]},{'box':[0,0,float('nan'),2]}])
def test_invalid_top_candidate_can_fall_back_to_valid_mask(change):
    _,receipt=health.select_valid_mask({'results':[proposal(.99,**change),proposal(.8)]},2,3)
    assert receipt['proposal_index']==1


@pytest.mark.parametrize('response',[{}, {'results':[]},{'results':[proposal(mask=np.zeros((2,3),dtype=np.uint8))]}, {'results':[proposal(mask=np.ones((2,3),dtype=np.uint8))]}, {'results':[proposal(score=float('nan'))]}, {'results':['malformed']}])
def test_no_valid_proposal_rejected(response):
    with pytest.raises((ValueError,TypeError)):health.select_valid_mask(response,2,3)


def public_image(tmp_path):
    path=tmp_path/'public.jpg';Image.fromarray(np.zeros((2,3,3),dtype=np.uint8)).save(path)
    return path,hashlib.sha256(path.read_bytes()).hexdigest()


def fake_http(monkeypatch,responses):
    calls=[];iterator=iter(responses)
    def urlopen(request,timeout):
        calls.append(json.loads(request.data));status,raw=next(iterator)
        if status!=200:
            raise health.urllib.error.HTTPError(request.full_url,status,'failed',{},io.BytesIO(raw))
        return contextlib.closing(SimpleNamespace(status=status,read=lambda:raw,close=lambda:None))
    monkeypatch.setattr(health.urllib.request,'urlopen',urlopen);return calls


def test_actual_post_payloads_and_compressed_raw_receipts(monkeypatch,tmp_path):
    image,sha=public_image(tmp_path);raw=json.dumps({'results':[proposal(),proposal(.1,np.zeros((2,3),dtype=np.uint8))]}).encode()
    calls=fake_http(monkeypatch,[(200,raw),(200,raw)])
    identities=[]
    def identity(*args):identities.append(args);return {'pid':42,'create_time':1,'CUDA_VISIBLE_DEVICES':'4','owns_listener':True}
    monkeypatch.setattr(health,'service_identity',identity)
    result=health.check_inference('http://127.0.0.1:8114',image,sha,tmp_path/'receipt',service_pid=42,expected_cuda=4)
    assert result['state']=='passed' and len(calls)==2
    assert [c['text_prompt'] for c in calls]==['bowl','plate']
    assert all(base64.b64decode(c['image_base64'])==image.read_bytes() for c in calls)
    assert identities==[(42,4,8114),(42,4,8114)]
    for row in result['results']:
        assert gzip.decompress(Path(row['response_path']).read_bytes())==raw
        assert row['response_sha256']==hashlib.sha256(raw).hexdigest()
        assert Path(row['overlay']).exists()
    assert result['robot_trials_added']==result['attempts_refunded']==0
    assert not result['solver_input'] and not result['real_mechanism_gate']
    with pytest.raises(FileExistsError):health.check_inference('http://127.0.0.1:8114',image,sha,tmp_path/'receipt')


@pytest.mark.parametrize('status,raw',[(500,b'CUDA out of memory'),(200,b'not json'),(200,b'{"results":[]}'),(200,json.dumps({'results':[proposal(shape=[3,2])]}).encode())])
def test_failed_http_or_mask_keeps_original_body_and_stops(monkeypatch,tmp_path,status,raw):
    image,sha=public_image(tmp_path);calls=fake_http(monkeypatch,[(status,raw)])
    with pytest.raises(health.Sam3HealthFailure) as caught:health.check_inference('http://127.0.0.1:8114',image,sha,tmp_path/'receipt')
    report=caught.value.report;assert report['state']=='failed' and len(calls)==1
    assert gzip.decompress(Path(report['results'][0]['response_path']).read_bytes())==raw
    assert json.loads((tmp_path/'receipt/report.json').read_text())==report


@pytest.mark.parametrize('url,changed',[('http://external.example:8114',False),('http://secret@127.0.0.1:8114',False),('http://127.0.0.1:8114',True)])
def test_bad_source_or_nonlocal_service_stops_before_http(monkeypatch,tmp_path,url,changed):
    image,sha=public_image(tmp_path);calls=fake_http(monkeypatch,[])
    with pytest.raises(health.Sam3HealthFailure):health.check_inference(url,image,'changed' if changed else sha,tmp_path/'receipt')
    assert calls==[] and (tmp_path/'receipt/report.json').exists()


def test_actual_owned_pid_gpu_or_listener_required_before_inference(monkeypatch,tmp_path):
    image,sha=public_image(tmp_path);calls=fake_http(monkeypatch,[])
    def bad(*_):raise ValueError('owned SAM3 CUDA mapping differs')
    monkeypatch.setattr(health,'service_identity',bad)
    with pytest.raises(health.Sam3HealthFailure):health.check_inference('http://127.0.0.1:8114',image,sha,tmp_path/'receipt',service_pid=42,expected_cuda=4)
    assert calls==[]


@pytest.mark.parametrize('before_claim',[1,2])
def test_queue_health_failure_prevents_next_claim_and_spawn(tmp_path,monkeypatch,before_claim):
    calls=[]
    def callback():
        calls.append(True)
        if len(calls)==before_claim:raise RuntimeError('actual SAM3 inference failed')
    with pytest.raises(RuntimeError,match='actual SAM3 inference failed'):
        queue_fixture.queue_harness(tmp_path,monkeypatch,[sys.executable,'-c','pass'],5,
                                    supervisor=DOC/'dlc-supervisor-r7.py',functional_health=callback)
    claims=tmp_path/'round/queue/claims'
    assert not (claims/('cell0' if before_claim==1 else 'cell1')).exists()
    if before_claim==2:
        result=json.loads((claims/'cell0/result.json').read_text())
        assert result['state']=='full_complete' and result['process_cleanup']['verified_no_live_work']
    assert not (tmp_path/'round/cell1/queue-result.json').exists()


def test_production_health_runs_before_native_compat_and_first_claim():
    text=(DOC/'dlc-supervisor-r7.py').read_text()
    assert text.index('initial_sam3_health=actual_sam3_health()')<text.index('status("native_compatibility")')
    assert 'functional_health=cell_boundary_health' in text
    assert "manifest['files']['reference/sam3-public-dev51-agentview.jpg']" in text
