"""The single-cell repair profile preserves readiness and teardown gates."""
import copy
import json
from pathlib import Path

import pytest

from test_streaming_dlc_pair import world, assets, save, dump, started, leftovers, E, P
from test_streaming_dlc_unit8_four import node8, Node8Host, launch8


@pytest.fixture
def single8(node8):
    w = node8
    cell = w.pair['ranks']['0']['A']
    w.pair.update(layout_profile=P.UNIT8_SINGLE,
                  gpu_map=copy.deepcopy(P.UNIT8_SINGLE_GPU_MAP), ranks={'0': {'A': cell}})
    case = json.loads(Path(cell['case']).read_text())
    case.update(cuda_visible_devices='2', egl_device_id=2)
    _, cell['case_sha256'] = dump(Path(cell['case']), case)
    save(w)
    return w


def test_single_cell_starts_after_capacity_probe_and_releases_all_services(single8):
    code, host, attempt = launch8(single8)
    assert code == 0, attempt
    assert len(started(host, E.CAMPAIGN)) == 1
    assert {s['env']['CUDA_VISIBLE_DEVICES'] for s in started(host, 'vllm')} == {'4,5,6,7'}
    assert attempt['slots']['A']['device_probe']['sim_gpu'] == '2'
    assert sorted(s['port'] for s in host.spawned if s['port']) == [8114, 8115, 8116, 8121]
    assert host.full_probes == 1 and not leftovers(host)


@pytest.mark.parametrize('change', [
    lambda p: p.update(workers=2), lambda p: p.update(gpus_per_worker=4),
    lambda p: p['ranks']['0'].update(B=copy.deepcopy(p['ranks']['0']['A'])),
    lambda p: p['gpu_map']['slots']['A'].update(simulator='0'),
    lambda p: p.pop('full_resolution_grasp_probe'),
])
def test_single_profile_rejects_extra_cells_or_inexact_resources(single8, change):
    change(single8.pair)
    save(single8)
    with pytest.raises(E.EntryRefused):
        P.load_pair(single8.path)


def test_failed_capacity_probe_starts_no_trial(single8):
    class BadGrasp(Node8Host):
        def http(self, url, payload=None, timeout=120):
            if payload and ':8115/' in url:
                return 500, {'error': 'capacity'}
            return super().http(url, payload, timeout)
    host = BadGrasp(single8)
    assert P.launch(single8.path, host=host) == 1
    assert not started(host, E.CAMPAIGN) and not leftovers(host)
