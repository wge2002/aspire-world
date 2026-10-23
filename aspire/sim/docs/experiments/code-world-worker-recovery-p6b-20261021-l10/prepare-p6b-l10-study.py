#!/usr/bin/env python3
"""P6b-l10 (CC_TASK_10): the p6b method on the two libero_10 cells, one 4-GPU unit.

Imports the p6b stager (`code-world-worker-recovery-p6b-20261021/prepare-p6b-study.py`,
unchanged; it imports the unchanged r3 stager) and re-points it; nothing is copied.
Differences from p6b node 0, and only these:
  * names/roots: this directory's name;
  * cells: two fresh cells, mugmicrowave_p6b_r1 (case of r3 mugmicrowave_p6_r1, N=2) and
    twomoka_p6b_r1 (case of r3 twomoka_p6_r1, N=1): the r3 / Task 7 libero_10 rule;
  * layout: one 4-GPU unit, two slots (the smoke shape) instead of two units.
Support overlay, runtime bytes, prompts, budget and selection are p6b's. No inference,
services or submissions here.

  prepare-p6b-l10-study.py --prepare | --verify
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, re

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[2]
P6B_DIR = SIM / 'docs/experiments/code-world-worker-recovery-p6b-20261021'
P6B_ROOT = Path('/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021')
P6B_RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-code-world-worker-recovery-p6b-20261021')
NODES = {0: ('twomoka_p6b_r1', 'mugmicrowave_p6b_r1')}  # r3 node-1 order: longest first
#: r3 / Task 7 libero_10 rule (prepare-p6-study.py: twomoka 1, mugmicrowave 2).
N = {'mugmicrowave_p6b_r1': 2, 'twomoka_p6b_r1': 1}
R3_CASE_OF = {'mugmicrowave_p6b_r1': 'mugmicrowave_p6_r1', 'twomoka_p6b_r1': 'twomoka_p6_r1'}
NAME = HERE.name
ROOT = Path('/mnt/home/gewang/experiments') / NAME
RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-' + NAME)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load(P6B_DIR / 'prepare-p6b-study.py', 'p6b_l10_p6b_stager')
p6, p5, pc, p3, base, limits = b.p6, b.p5, b.pc, b.p3, b.base, b.limits
b.R3_CASE_OF.update(R3_CASE_OF)


def point():
    """b.point(False) with this root, these cells and per-cell N; same p6b overlay."""
    b.NAME, b.ROOT, b.RUNTIME = NAME, ROOT, RUNTIME
    p5.NAME = p6.NAME = NAME
    p5.STUDY_DIR = HERE / 'records'
    p5.ARM = 'p6b'
    p5.CELL = re.compile(r'([a-z]+)_p6b_(?:r(\d+)|smoke(\d+))')
    p5.CELL_ORDER = tuple(c for node in sorted(NODES) for c in NODES[node])
    p5.NODE_CELLS = p6.NODE_CELLS = NODES
    b.N.clear(); b.N.update(N)
    p6.N.clear(); p6.N.update(N)
    p5.OVERLAY.update({p.name: p.resolve() for p in (P6B_DIR / 'support').glob('*.py')
                       if p.name in base.SUPPORT_FILES})
    p5.hs.OVERLAY = p5.OVERLAY
    return p5.repoint(False)


def case_config(cell, condition):
    case = b.case_config(cell, condition)
    case.update(heldout_parallel=N[cell], sweep_parallel=N[cell], trial_threads=2)
    return case


pc.case_config = p3.case_config = base.case_config = case_config


def study():
    s = b.study()
    s['note'] = ('P6b fresh independent C cells: r3 method (P5 + Task7 parallel + Task8 closeout) + '
                 'Task9 worker recovery and B1/B2/B3 closeout repairs; one node, one 4-GPU unit; '
                 'N=2 mugmicrowave / N=1 twomoka')
    return s


pc.study = study


def campaign_plan():
    p = b.campaign_plan()
    p['kind'] = study()['note']
    p['launch_shape']['mode'] = 'one independent 1x4-GPU TOP job, one 4-GPU unit x two slots'
    return p


pc.campaign_plan = campaign_plan


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def normalize_node0(text, cell, reference):
    for a, c in ((str(ROOT), str(P6B_ROOT)), (str(RUNTIME), str(P6B_RUNTIME)), (NAME, P6B_ROOT.name),
                 (cell, reference)):
        text = text.replace(a, c)
    return text


#: Case keys that may differ from a p6b node-0 case, and why.
CASE_DIFF_NODE0 = {'suite': 'cell list (task definition of the r3 case)', 'task': 'cell list',
                   'env_config': 'cell list', 'heldout_parallel': 'N', 'sweep_parallel': 'N',
                   'runtime_manifest_sha256': 'root path inside the manifest'}
TASK_KEYS = ('suite', 'task', 'env_config')
STUDY_DIFF_NODE0 = {'cells': 'cell list', 'node_cells': 'cell list', 'note': 'cell list / N / layout',
                    'parallel_by_cell': 'N', 'name': 'root'}


def diff_vs_node0(receipt):
    """Every byte difference from the p6b node-0 staging (`dlc3fdmvfaxdfyel`), read-only on its root."""
    theirs = json.loads((P6B_ROOT / 'prepare-receipt.json').read_text())
    node0_cells = list(theirs['runtime_sha256'])
    problems, cells = [], {}
    for cell in N:
        mine = receipt['runtime_sha256'][cell]
        runtime = sorted({n for ref in node0_cells for n in set(mine) | set(theirs['runtime_sha256'][ref])
                          if mine.get(n) != theirs['runtime_sha256'][ref].get(n)})
        problems += [f'{cell}: runtime {n} differs from p6b node 0' for n in runtime]
        case = json.loads((ROOT / cell / 'case.json').read_text())
        per_ref = {}
        for ref in node0_cells:
            other = json.loads((P6B_ROOT / ref / 'case.json').read_text())
            # Outside the task-definition keys, the task name is normalized too (generation_context embeds it).
            keys = sorted(k for k in set(case) | set(other)
                          if (normalize_node0(json.dumps(case.get(k)), cell, ref) if k in TASK_KEYS else
                              normalize_node0(json.dumps(case.get(k)), cell, ref).replace(case['task'], other['task']))
                          != json.dumps(other.get(k)))
            per_ref[ref] = keys
            problems += [f'{cell}: case key {k} differs from p6b {ref}' for k in keys
                         if k not in CASE_DIFF_NODE0 and not (k == 'repeat' and ref == 'drawer_p6b_r1')]
        cells[cell] = {'case_keys_differ_by_node0_cell': per_ref,
                       'runtime_differs': runtime}
    support = sorted(n for n in set(receipt['frozen_support']) | set(theirs['frozen_support'])
                     if receipt['frozen_support'].get(n) != theirs['frozen_support'].get(n))
    problems += [f'frozen support {n} differs from p6b node 0' for n in support]
    new = json.loads((ROOT / 'launch/launch-manifest.json').read_text())['files']
    old = json.loads((P6B_ROOT / 'launch/launch-manifest.json').read_text())['files']
    launch = sorted(n for n in set(new) | set(old) if new.get(n) != old.get(n))
    problems += [f'launch {n} differs from p6b node 0' for n in launch if n != 'study.json']
    s_new = json.loads((ROOT / 'launch/study.json').read_text())
    s_old = json.loads((P6B_ROOT / 'launch/study.json').read_text())
    study_keys = sorted(k for k in set(s_new) | set(s_old) if s_new.get(k) != s_old.get(k))
    problems += [f'study.json key {k} differs from p6b node 0' for k in study_keys if k not in STUDY_DIFF_NODE0]
    plan_new = json.loads((HERE / 'records/campaign-plan.json').read_text())
    plan_old = json.loads((P6B_DIR / 'records/campaign-plan.json').read_text())
    plan_keys = sorted(k for k in set(plan_new) | set(plan_old) if plan_new.get(k) != plan_old.get(k))
    plan_sub = {k: sorted(j for j in set(plan_new[k]) | set(plan_old[k])
                          if plan_new[k].get(j) != plan_old[k].get(j))
                for k in plan_keys if isinstance(plan_new.get(k), dict) and isinstance(plan_old.get(k), dict)}
    allowed_plan = {'study_id': 'root', 'kind': 'note', 'parallel_by_cell': 'N', 'case_of': 'cell list',
                    'cells': 'cell list', 'claim_order': 'cell list', 'node_cells': 'cell list',
                    'nominal_nodes': 'cell list', 'serial_order': 'cell list', 'launch_shape': 'layout'}
    problems += [f'campaign plan key {k} differs from p6b node 0' for k in plan_keys if k not in allowed_plan]
    contract_same = digest(HERE / 'records/baseline-contract.json') == digest(P6B_DIR / 'records/baseline-contract.json')
    return {'reference_root': str(P6B_ROOT), 'reference_job': 'dlc3fdmvfaxdfyel',
            'cells': cells, 'support_differs': support, 'launch_differs': launch,
            'study_json_keys_differ': {k: STUDY_DIFF_NODE0.get(k) for k in study_keys},
            'campaign_plan_keys_differ': {k: allowed_plan.get(k) for k in plan_keys},
            'campaign_plan_subkeys_differ': plan_sub,
            'baseline_contract_bytes_equal': contract_same,
            'normalized': 'result/runtime roots, study name, cell name', 'problems': problems}


def additional_verify():
    out = b.additional_verify(False)  # per-cell N/timeout/empty-artifact/runtime checks + diff vs r3
    for cell in N:
        if out['cells'][cell]['N'] != N[cell]:
            out['problems'].append(f'{cell}: N {out["cells"][cell]["N"]} != {N[cell]}')
    receipt = json.loads((ROOT / 'prepare-receipt.json').read_text())
    out['byte_diff_vs_p6b_node0'] = diff_vs_node0(receipt)
    timeouts = [out['cells'][c]['cell_timeout_seconds'] for c in N]
    out['minimum_window_minutes'] = -(-(max(timeouts) + 90 + 1800 + 900) // 60)
    return out


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true'); group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    where = point()
    if args.prepare:
        receipt = pc.prepare()
        base.freeze_bytes(Path(__file__), ROOT / 'frozen-prepare-p6b-l10-study.py')
        base.freeze_bytes(P6B_DIR / 'prepare-p6b-study.py', ROOT / 'frozen-prepare-p6b-study.py')
        base.record(ROOT / 'p6b-receipt.json', {
            'name': NAME, 'node_cells': p5.NODE_CELLS, 'N': N, 'r3_case_of': R3_CASE_OF,
            'overlay': {n: str(p) for n, p in p5.OVERLAY.items()}, 'where': where,
            'p6b_stager': str(P6B_DIR / 'prepare-p6b-study.py'),
            'p6b_stager_sha256': digest(P6B_DIR / 'prepare-p6b-study.py'),
            'source_root': str(SIM), 'prepared_at': receipt['prepared_at']})
        return 0
    result = pc.verify()
    additional = additional_verify()
    target = HERE / 'verify-extra.json'
    if target.exists():
        assert json.loads(target.read_text()) == additional, 'verification evidence changed; preserve and inspect'
    else:
        base.record(target, additional)
    print(json.dumps(additional, indent=2))
    return int(result['status'] != 'verified' or bool(additional['problems'])
               or bool(additional['byte_diff_vs_r3']['problems'])
               or bool(additional['byte_diff_vs_p6b_node0']['problems']))


if __name__ == '__main__':
    raise SystemExit(main())
