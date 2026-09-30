"""Staging regressions; no model or simulator is started."""
import importlib.util
import json
from pathlib import Path
import pytest

HERE = Path(__file__).resolve().parent

@pytest.fixture
def stage():
    spec = importlib.util.spec_from_file_location('full_stager_acceptance', HERE / 'prepare-full.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

def test_full_case_contract(stage):
    case = stage.case_config(stage.CELL, 'C')
    assert stage.check_case(case)['problems'] == []
    assert case['dev_seeds'] == list(range(51, 66))
    assert case['heldout_seeds'] == list(range(1, 51))
    assert 'api_key_helper' not in case
    assert stage.full_scope.remaining_budget(case) == {str(s): 2 if s == 51 else 3 for s in range(51, 66)}

@pytest.mark.parametrize('kind', ['directory', 'broken_symlink'])
def test_refuse_occupied_roots(stage, tmp_path, kind):
    target = tmp_path / 'occupied'
    if kind == 'directory':
        target.mkdir()
        (target / 'evidence').write_text('keep')
    else:
        target.symlink_to(tmp_path / 'missing')
    with pytest.raises(SystemExit, match='preserve'):
        stage.refuse_existing(target)
    assert target.is_symlink() if kind == 'broken_symlink' else (target / 'evidence').read_text() == 'keep'

def test_launch_pins_and_manifest(stage, tmp_path, monkeypatch):
    source, launch, support = (tmp_path / n for n in ('v4', 'launch', 'support'))
    source.mkdir(); support.mkdir()
    (source / 'entry').write_text('trusted entry')
    (support / 'dlc-supervisor.py').write_text('full supervisor')
    monkeypatch.setattr(stage, 'V4_LAUNCH', source)
    monkeypatch.setattr(stage, 'LAUNCH', launch)
    monkeypatch.setattr(stage.base, 'SUPPORT', support)
    monkeypatch.setattr(stage, 'LAUNCH_PINS', {'entry': stage.base.digest(source / 'entry')})
    monkeypatch.setattr(stage, 'LAUNCH_FILES', ('entry', 'dlc-supervisor.py'))
    stage.stage_launch()
    manifest = json.loads((launch / 'launch-manifest.json').read_text())
    assert set(manifest['files']) == {'entry', 'dlc-supervisor.py'}
    assert manifest['files']['dlc-supervisor.py'] == stage.base.digest(support / 'dlc-supervisor.py')
    (source / 'entry').write_text('changed entry')
    with pytest.raises(SystemExit, match='input changed'):
        stage.stage_launch()
    assert (launch / 'entry').read_text() == 'trusted entry'

def test_prompt_partition_and_budget(stage, tmp_path):
    case = stage.case_config(stage.CELL, 'C')
    case['control'] = str(tmp_path)
    worker = '\n'.join(stage.full_scope.REQUIRED_AFTER) + '\n' + case['full_interface_doc'] + '\nseed 51 has **2**\nseeds 52-65 have **3**'
    (tmp_path / 'worker-prompt.md').write_text(worker)
    (tmp_path / 'coordinator-prompt.md').write_text('outer heldout')
    assert not stage.check_prompt(case)['problems']
    (tmp_path / 'worker-prompt.md').write_text(worker + '\nno held-out evaluation')
    assert stage.check_prompt(case)['problems']
    (tmp_path / 'worker-prompt.md').write_text(worker.replace('seed 51 has **2**', 'seed 51 has **3**'))
    assert stage.check_prompt(case)['problems']
