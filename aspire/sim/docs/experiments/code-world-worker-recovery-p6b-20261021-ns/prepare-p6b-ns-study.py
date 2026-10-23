#!/usr/bin/env python3
"""P6b-ns (CC_TASK_11): the p6b method on three never-run LIBERO-Pro suites + mokastove, one 8-GPU node.

Imports the p6b stager (`code-world-worker-recovery-p6b-20261021/prepare-p6b-study.py`,
unchanged; it imports the unchanged r3 stager) and re-points it, as the l10 stager does.
Differences from p6b node 0, and only these:
  * names/roots: this directory's name;
  * cells: four fresh cells (TASKS below); three are in suites this pipeline never ran
    (libero_spatial_task, libero_object_task, libero_10_task), added to the stager's
    position -> (suite, task) table. Suite per cell is the p4n mechanism (case['suite']);
    env_config stays franka_libero_traced.yaml like every libero_10 cell so far;
  * N per cell (NS below, with reasons in the HANDOFF).
No cell has an r3 case, so the r3 assertion/diff of the p6b stager is replaced by a
direct check against TASKS and p6b node 0. No inference, services or submissions here.

  prepare-p6b-ns-study.py --prepare | --verify
"""
from pathlib import Path
import argparse, difflib, hashlib, importlib.util, json, re

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[2]
P6B_DIR = SIM / 'docs/experiments/code-world-worker-recovery-p6b-20261021'
P6B_ROOT = Path('/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021')
P6B_RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-code-world-worker-recovery-p6b-20261021')
#: position -> (suite, task stem). The worker's goal is the runtime task_language.
TASKS = {
    # runtime: "put the white mug in the microwave and close it"
    'mugtaskmicrowave': ('libero_10_task', 'KITCHEN_SCENE6_put_the_yellow_and_white_mug_in_the_microwave_and_close_it'),
    # runtime: "turn on the stove and put the moka pot on it"
    'mokastove': ('libero_10_swap', 'KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it'),
    # runtime: "Pick the akita black bowl next to the cookie box and place it on the plate"
    'spatialtaskcookie': ('libero_spatial_task', 'pick_up_the_black_bowl_next_to_the_ramekin_and_place_it_on_the_plate'),
    # runtime: "Pick the milk and place it in the basket"
    'objtaskmilk': ('libero_object_task', 'pick_up_the_ketchup_and_place_it_in_the_basket'),
}
#: cell -> N (claim order: alternate units get one libero_10 cell each).
NS = {'mugtaskmicrowave_p6b_r1': 2, 'mokastove_p6b_r1': 4, 'spatialtaskcookie_p6b_r1': 4, 'objtaskmilk_p6b_r1': 4}
#: cell -> the earlier cell of the same task (metadata only), if any.
CASE_OF = {'mokastove_p6b_r1': 'mokastove_p5_r1 (p5)'}
NODES = {0: tuple(NS)}
NAME = HERE.name
ROOT = Path('/mnt/home/gewang/experiments') / NAME
RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-' + NAME)
ENV_CONFIG = 'env_configs/libero/franka_libero_traced.yaml'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load(P6B_DIR / 'prepare-p6b-study.py', 'p6b_ns_p6b_stager')
p6, p5, pc, p3, base, limits = b.p6, b.p5, b.pc, b.p3, b.base, b.limits
p5.TASKS_BY_POSITION.update(TASKS)  # in place: hs / pc / p3 / base read it through p5.repoint


def position(cell):
    return cell.split('_')[0]


def point():
    """b.point(False) with this root, these cells and per-cell N; same p6b overlay."""
    b.NAME, b.ROOT, b.RUNTIME = NAME, ROOT, RUNTIME
    p5.NAME = p6.NAME = NAME
    p5.STUDY_DIR = HERE / 'records'
    p5.ARM = 'p6b'
    p5.CELL = re.compile(r'([a-z]+)_p6b_(?:r(\d+)|smoke(\d+))')
    p5.CELL_ORDER = tuple(c for node in sorted(NODES) for c in NODES[node])
    p5.NODE_CELLS = p6.NODE_CELLS = NODES
    b.N.clear(); b.N.update(NS)
    p6.N.clear(); p6.N.update(NS)
    p5.OVERLAY.update({p.name: p.resolve() for p in (P6B_DIR / 'support').glob('*.py')
                       if p.name in base.SUPPORT_FILES})
    p5.hs.OVERLAY = p5.OVERLAY
    return p5.repoint(False)


def case_config(cell, condition):
    """b.case_config without its r3-case assertion (no r3 case exists for these cells)."""
    case = p6.original_case(cell, condition)
    case.update(heldout_parallel=NS[cell], sweep_parallel=NS[cell], trial_threads=2)
    suite, task = TASKS[position(cell)]
    assert (case['suite'], case['task'], case['env_config']) == (suite, task, ENV_CONFIG), (cell, case['suite'],
                                                                                            case['task'],
                                                                                            case['env_config'])
    case['generation_context'] = case['generation_context'].replace(
        'p3-search + p4 coverage gate + aspire-v1 development budget',
        'p3-search + p4 coverage gate + aspire-v1 development budget + Task7 parallel + Task8 closeout'
        ' + Task9 worker recovery')
    return case


pc.case_config = p3.case_config = base.case_config = case_config


def study():
    s = b.study()
    s['note'] = ('P6b fresh independent C cells: r3 method (P5 + Task7 parallel + Task8 closeout) + '
                 'Task9 worker recovery and B1/B2/B3 closeout repairs; one node; new suites '
                 'libero_spatial_task / libero_object_task / libero_10_task + mokastove; per-cell N')
    return s


pc.study = study


def campaign_plan():
    p = p6.original_plan()
    p.update(study_id=NAME, kind=study()['note'], parallel_by_cell=dict(NS),
             case_of={c: (CASE_OF[c] + ' metadata only; ' if c in CASE_OF else 'first cell of this task; ')
                      + 'no program/ledger/feedback inherited' for c in NS})
    p['launch_shape']['mode'] = 'one independent 1x8-GPU TOP job, two 4-GPU units x two slots'
    p['development_budget']['freeze'] += '; or outer Task8 phase-B end selects a valid complete best candidate'
    p['development_budget']['phase_a'] += '; stagnation starts a new diagnostic round under the original cumulative deadline'
    p['worker_recovery'] = ('an unresumable primary (SendMessage could not be resumed, nothing of it ran '
                            'afterwards) is replaced by a new primary generation in a new coordinator session: '
                            'same frozen assignment + driver handoff, same ledger/attempts/phase/deadline')
    return p


pc.campaign_plan = campaign_plan


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


#: Lines whose text is derived from N (the batch-sweep contract); allowed to differ from an N=4 cell.
N_LINES = (re.compile(r'up to \d+ seeds at once'), re.compile(r'^\d+ trial lengths\); never wrap it in `timeout`\.$'))
SLOT = ((re.compile(r'^GPU:   \d+$', re.M), 'GPU:   <slot>'),
        (re.compile(r'physical GPU \d+ exclusively'), 'physical GPU <slot> exclusively'),
        (re.compile(r'for p in \d+ \d+ \d+; do'), 'for p in <unit ports>; do'))


def normalized(text, cell, suite, task, root, runtime, name):
    for a, c in ((str(root), '<ROOT>'), (str(runtime), '<RUNTIME>'), (name, '<NAME>'),
                 (f'{suite}/{task}', '<SUITE>/<TASK>'), (f'{suite}_{task}', '<SUITE>_<TASK>'),
                 (f'SUITE: {suite}', 'SUITE: <SUITE>'), (task, '<TASK>'), (cell, '<CELL>')):
        text = text.replace(a, c)
    text = re.sub(r'(suite:\s+)' + re.escape(suite) + r'\b', r'\1<SUITE>', text)  # coordinator 'Cell:' header
    for pattern, repl in SLOT:
        text = pattern.sub(repl, text)
    return text


#: The p6b node-0 cell each new cell is compared against (nearest suite family).
NODE0_REF = {'libero_10_swap': 'bowldrawerclose_p6b_r1', 'libero_10_task': 'bowldrawerclose_p6b_r1'}
NODE0_DEFAULT = 'goaltaskbowldrawer_p6b_r1'
CASE_DIFF_NODE0 = {'suite': 'suite of the cell', 'task': 'task of the cell', 'heldout_parallel': 'N',
                   'sweep_parallel': 'N', 'runtime_manifest_sha256': 'roots + study.json hash inside the manifest',
                   'repeat': 'repeat of the reference cell'}
STUDY_DIFF_NODE0 = {'cells': 'cell list', 'node_cells': 'cell list', 'note': 'cell list / suites / N',
                    'parallel_by_cell': 'N'}
PLAN_DIFF_NODE0 = {'study_id': 'root', 'kind': 'note', 'parallel_by_cell': 'N', 'case_of': 'cell list',
                   'cells': 'cell list (suite/task per cell)', 'claim_order': 'cell list', 'node_cells': 'cell list',
                   'nominal_nodes': 'cell list', 'serial_order': 'cell list',
                   'tasks': 'position table gains the new positions'}


def diff_vs_node0(receipt):
    """Every byte difference from the p6b node-0 staging (`dlc3fdmvfaxdfyel`), read-only on its root."""
    theirs = json.loads((P6B_ROOT / 'prepare-receipt.json').read_text())
    problems, cells = [], {}
    for cell in NS:
        suite, task = TASKS[position(cell)]
        ref = NODE0_REF.get(suite, NODE0_DEFAULT)
        ref_case = json.loads((P6B_ROOT / ref / 'case.json').read_text())
        own = (cell, suite, task, ROOT, RUNTIME, NAME)
        other = (ref, ref_case['suite'], ref_case['task'], P6B_ROOT, P6B_RUNTIME, P6B_ROOT.name)
        mine = receipt['runtime_sha256'][cell]
        runtime = sorted(n for n in set(mine) | set(theirs['runtime_sha256'][ref])
                         if mine.get(n) != theirs['runtime_sha256'][ref].get(n))
        problems += [f'{cell}: runtime {n} differs from p6b node 0' for n in runtime]
        case = json.loads((ROOT / cell / 'case.json').read_text())
        keys = sorted(k for k in set(case) | set(ref_case)
                      if normalized(json.dumps(case.get(k)), *own) != normalized(json.dumps(ref_case.get(k)), *other))
        problems += [f'{cell}: case key {k} differs from p6b {ref}' for k in keys if k not in CASE_DIFF_NODE0]
        prompts = {}
        for name in ('worker-prompt.md', 'coordinator-prompt.md'):
            a = normalized((P6B_ROOT / ref / name).read_text(), *other).splitlines()
            c = normalized((ROOT / cell / name).read_text(), *own).splitlines()
            lines = [l for l in difflib.unified_diff(a, c, lineterm='', n=0)
                     if l[:1] in '+-' and not l.startswith(('+++', '---'))]
            unexplained = [l for l in lines if not any(p.search(l[1:]) for p in N_LINES)]
            prompts[name] = {'changed_lines': lines, 'unexplained': unexplained}
            problems += [f'{cell}: {name} line differs from p6b {ref} beyond N: {l[:120]}' for l in unexplained]
        cells[cell] = {'suite': suite, 'task': task, 'node0_reference': ref, 'case_keys_differ': keys,
                       'runtime_differs': runtime, 'prompts': prompts}
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
    problems += [f'campaign plan key {k} differs from p6b node 0' for k in plan_keys if k not in PLAN_DIFF_NODE0]
    contract_same = digest(HERE / 'records/baseline-contract.json') == digest(P6B_DIR / 'records/baseline-contract.json')
    if not contract_same:
        problems.append('baseline contract differs from p6b node 0')
    return {'reference_root': str(P6B_ROOT), 'reference_job': 'dlc3fdmvfaxdfyel', 'cells': cells,
            'support_differs': support, 'launch_differs': launch,
            'study_json_keys_differ': {k: STUDY_DIFF_NODE0.get(k) for k in study_keys},
            'campaign_plan_keys_differ': {k: PLAN_DIFF_NODE0.get(k) for k in plan_keys},
            'baseline_contract_bytes_equal': contract_same,
            'normalized': 'result/runtime roots, study name, cell name, suite, task, staged slot GPU and unit ports',
            'problems': problems}


def additional_verify():
    """b.additional_verify's per-cell checks (minus its r3 diff), then the node-0 diff."""
    receipt = json.loads((ROOT / 'prepare-receipt.json').read_text())
    out, problems = {}, []
    for cell in NS:
        case = json.loads((ROOT / cell / 'case.json').read_text()); repo = Path(case['sim'])
        errors = limits.check_case(case)
        if case['heldout_parallel'] != NS[cell]:
            errors.append('N differs from frozen plan')
        if (case['suite'], case['task'], case['env_config']) != (*TASKS[position(cell)], ENV_CONFIG):
            errors.append('suite/task/env_config differ from the task table')
        task = repo / 'outputs/libero_fix_loop' / case['suite'] / case['task']
        if task.exists() and any(task.iterdir()):
            errors.append('cell does not start from empty task artifacts')
        for filename in ['scripts/common/native_cc_trial_process.py', 'scripts/libero/native_world_protocol.py',
                         'scripts/libero/native_world_fixloop_state.py', 'scripts/libero/native_world_heldout.py',
                         'scripts/libero/executable_world_profile.py']:
            if (repo / filename).read_bytes() != (SIM / filename).read_bytes():
                errors.append('runtime differs from final source: ' + filename)
        prompt = (ROOT / cell / 'worker-prompt.md').read_text()
        if f'SUITE: {case["suite"]}\nTASK:  {case["task"]}\n' not in prompt:
            errors.append('worker prompt does not carry this suite/task')
        if '.claude/libero/fix-loop/skills/task-exploration.md' not in prompt:
            errors.append('worker prompt lacks the task-exploration step (runtime task language)')
        out[cell] = {'suite': case['suite'], 'task': case['task'], 'N': NS[cell],
                     'cell_timeout_seconds': limits.cell_timeout(case), 'problems': errors}
        problems.extend(cell + ': ' + e for e in errors)
    for name in ('run_two_task_cell.py', 'canonical_assignment.py', 'worker_recovery.py'):
        if digest(ROOT / 'support' / name) != digest(P6B_DIR / 'support' / name):
            problems.append(f'frozen support {name} differs from the p6b source')
    if (ROOT / 'support/smoke_recovery_driver.py').exists():
        problems.append('the smoke wrapper must not be frozen into the formal root')
    if not (ROOT / 'launch/unit_supervisor.py').is_file():
        problems.append('frozen tracker dependency absent')
    if len({json.loads((ROOT / c / 'case.json').read_text())['claude_config_dir'] for c in NS}) != len(NS):
        problems.append('cells share a native config dir')
    timeouts = [out[c]['cell_timeout_seconds'] for c in NS]
    return {'cells': out, 'problems': problems, 'byte_diff_vs_p6b_node0': diff_vs_node0(receipt),
            'minimum_window_minutes': -(-(max(timeouts) + 90 + 1800 + 900) // 60)}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true'); group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    where = point()
    if args.prepare:
        receipt = pc.prepare()
        base.freeze_bytes(Path(__file__), ROOT / 'frozen-prepare-p6b-ns-study.py')
        base.freeze_bytes(P6B_DIR / 'prepare-p6b-study.py', ROOT / 'frozen-prepare-p6b-study.py')
        base.record(ROOT / 'p6b-receipt.json', {
            'name': NAME, 'node_cells': p5.NODE_CELLS, 'N': NS, 'tasks': TASKS, 'case_of': CASE_OF,
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
               or bool(additional['byte_diff_vs_p6b_node0']['problems']))


if __name__ == '__main__':
    raise SystemExit(main())
