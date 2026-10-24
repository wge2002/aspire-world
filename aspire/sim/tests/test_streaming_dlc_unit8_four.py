"""Four isolated campaigns and real stand-in process cleanup on one fake node."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from test_streaming_dlc_pair import world, assets, FakeHost, save, dump, started, leftovers, E, P
from test_streaming_dlc_unit4 import UnitHost
from qwen_decode_config import COMPILATION_CONFIG


@pytest.fixture
def node8(world):
    w = world
    entries = [w.pair['ranks'][r][s] for r, s in [('1','A'), ('1','B'), ('0','A'), ('0','B')]]
    # The fixture's fourth cell used to be a migration. Give this test a truly
    # fresh directory and UID; preserve the old fixture's ledger untouched.
    fresh = entries[-1]
    fresh.update(mode='fresh', control=str(w.base/'runs/bowl-fresh'))
    fresh['identity'].update(user='fresh-bowl', uid=30013, group='fresh-bowl', gid=30013,
                             home=str(w.base/'users/bowl-fresh'))
    fresh.pop('migration', None)
    w.pair.update(workers=1, gpus_per_worker=8, layout_profile=P.UNIT8_FOUR,
                  gpu_map=copy.deepcopy(P.UNIT8_FOUR_GPU_MAP),
                  full_resolution_grasp_probe={'width':800, 'height':512},
                  ranks={'0':dict(zip('ABCD',entries))})
    path = Path(w.pair['model_service_manifest'])
    model = json.loads(path.read_text())
    model['argv'] += ['--compilation-config',json.dumps(COMPILATION_CONFIG),
                      '--gpu-memory-utilization','0.82']
    model['env']['CUDA_VISIBLE_DEVICES'] = P.UNIT8_FOUR_QWEN
    model['argv_sha256'] = hashlib.sha256(json.dumps(model['argv'],sort_keys=True).encode()).hexdigest()
    _, model_sha = dump(path, model)
    w.pair['model_service_manifest_sha256'] = model_sha
    for slot, cell in w.pair['ranks']['0'].items():
        case = json.loads(Path(cell['case']).read_text())
        case.update(control=cell['control'], claude_config_dir=str(Path(cell['control'])/'claude-config'),
                    cuda_visible_devices=P.UNIT8_FOUR_SLOTS[slot]['simulator'],
                    egl_device_id=P.UNIT8_FOUR_SLOTS[slot]['egl_device_id'],
                    perception_manifest=w.pair['perception_descriptor'],
                    perception_manifest_sha256=w.pair['perception_descriptor_sha256'],
                    sam3_url='http://127.0.0.1:8114', graspnet_url='http://127.0.0.1:8115',
                    pyroki_url='http://127.0.0.1:8116')
        case['model_service']['server_manifest_sha256'] = model_sha
        _, cell['case_sha256'] = dump(Path(cell['case']),case)
    save(w)
    return w


class Node8Host(UnitHost):
    def __init__(self,w,**kw):
        kw.setdefault('egl',list(range(8)))
        super().__init__(w,**kw)

    def run(self,argv,**kw):
        return FakeHost.run(self,argv,**kw)


def launch8(w,**kw):
    host = Node8Host(w,**kw)
    code = P.launch(w.path,host=host)
    state = json.loads((Path(w.pair['entry_root'])/'rank0/pair_state.json').read_text())
    return code,host,state['attempts'][-1]


def test_four_cells_share_one_ready_stack_and_keep_all_peer_inputs_private(node8):
    code,h,a = launch8(node8)
    assert code == 0, a
    assert sorted(s['port'] for s in h.spawned if s['port']) == [8114,8115,8116,8121]
    assert {s['env']['CUDA_VISIBLE_DEVICES'] for s in started(h,'vllm')} == {'4,5,6,7'}
    campaigns = started(h,E.CAMPAIGN)
    assert len(campaigns) == len({s['env']['HOME'] for s in campaigns}) == 4
    assert {s['device_probe']['sim_gpu'] for s in a['slots'].values()} == {'0','1','2','3'}
    for slot,cell in node8.pair['ranks']['0'].items():
        forbidden=P.slot_dep(node8.pair,0,slot)['forbidden_read_paths']
        assert all(c['control'] in forbidden and c['identity']['home'] in forbidden
                   for other,c in node8.pair['ranks']['0'].items() if other!=slot)
    assert h.full_probes == 1 and not leftovers(h)


def test_one_cell_exit_keeps_services_for_three_other_cells(node8):
    node=Path(node8.pair['entry_root'])/'rank0/pair_state.json'
    seen=[]
    def done(h):
        slots=json.loads(node.read_text())['attempts'][-1]['slots']
        return slots.get('A',{}).get('status')=='campaign_exited' and all(
            slots.get(s,{}).get('status')=='campaign_running' for s in 'BCD')
    def observe(h):
        seen.append(sorted(s['port'] for s in h.spawned if s['port'] and s['process'].poll() is None))
    code,h,a=launch8(node8,behaviour={'/drawer_sam2streaming_r1.case':'exit:0.3:0',
                                     '.case':'exit:1.4:0'},on_sleep=[(done,observe)])
    assert code==0 and seen==[[8114,8115,8116,8121]]
    assert not leftovers(h)


@pytest.mark.parametrize('port',[8121,8114,8115,8116])
def test_shared_failure_terminates_all_four_without_orphans(node8,port):
    code,h,a=launch8(node8,behaviour={str(port):'exit:0.8:1','.case':'exit:60:0'})
    assert code==1 and all(s['status']=='service_failed' for s in a['slots'].values())
    assert len(a['slots'])==4 and not leftovers(h)


@pytest.mark.parametrize('port',[8121,8114,8115,8116])
def test_shared_timeout_starts_no_campaign(node8,port):
    node8.pair['service_ready_timeout_seconds']=30;save(node8)
    code,h,a=launch8(node8,never_ready={port})
    assert code==1 and not started(h,E.CAMPAIGN) and not leftovers(h)


@pytest.mark.parametrize('change',[
    lambda p:p.update(workers=2), lambda p:p.update(gpus_per_worker=4),
    lambda p:p['ranks']['0'].pop('D'),
    lambda p:p['gpu_map']['slots']['D'].update(simulator='2'),
    lambda p:p['gpu_map']['qwen'].update(gpus='0,1,2,3'),
    lambda p:p.pop('full_resolution_grasp_probe'),
])
def test_inexact_allocation_or_binding_is_refused(node8,change):
    change(node8.pair);save(node8)
    with pytest.raises(E.EntryRefused):P.load_pair(node8.path)


def test_bad_loaded_stack_capacity_prevents_all_four_campaigns(node8):
    class BadGrasp(Node8Host):
        def http(self,url,payload=None,timeout=120):
            if payload and ':8115/' in url:return 500,{'error':'capacity'}
            return super().http(url,payload,timeout)
    h=BadGrasp(node8)
    assert P.launch(node8.path,host=h)==1
    assert not started(h,E.CAMPAIGN) and not leftovers(h)
