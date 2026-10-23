#!/usr/bin/env python3
"""P6b (CC_TASK_9): the r3 method plus worker recovery and the B1/B2/B3 closeout repairs.

Imports the r3 stager (`code-world-repair-parallel-p6-20261020/prepare-p6-study.py`,
unchanged) and re-points it; nothing is copied. Differences from r3:
  * names/roots: this directory's name; one node, four fresh N=4 cells whose
    suite/task/env_config must equal the matching r3 cell's case;
  * support overlay `<this dir>/support`: run_two_task_cell.py (p6b driver),
    canonical_assignment.py (generation-N dispatch only in a driver recovery round),
    worker_recovery.py (new, added to SUPPORT_FILES);
  * runtime: native_world_protocol.py / native_world_fixloop_state.py from the
    worktree (end-development `terminal` reason and per-candidate infrastructure rule).
No inference, services or submissions here.

  prepare-p6b-study.py --prepare | --verify                    the four formal cells
  prepare-p6b-study.py --recovery-smoke --prepare | --verify   two drawer smoke cells
                                    (smoke_recovery_driver.py: injected / normal path)
"""
from pathlib import Path
import argparse, hashlib, importlib.util, json, re

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[2]
P6DIR = SIM / 'docs/experiments/code-world-repair-parallel-p6-20261020'
R3 = Path('/mnt/home/gewang/experiments/code-world-repair-parallel-p6-20261020-r3')
R3_RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-code-world-repair-parallel-p6-20261020-r3')
FORMAL = {0: ('goaltaskbowldrawer_p6b_r1', 'drawer_p6b_r1', 'bowldrawerclose_p6b_r1', 'bowldrawer_p6b_r1')}
SMOKE_CELLS = {0: ('drawer_p6b_smoke1', 'drawer_p6b_smoke2')}
#: The r3 cell each p6b cell takes suite/task/env_config from (metadata only).
R3_CASE_OF = {'goaltaskbowldrawer_p6b_r1': 'goaltaskbowldrawer_p6_r1', 'drawer_p6b_r1': 'drawer_p6_r2',
              'bowldrawerclose_p6b_r1': 'bowldrawerclose_p6_r1', 'bowldrawer_p6b_r1': 'bowldrawer_p6_r1',
              'drawer_p6b_smoke1': 'drawer_p6_r2', 'drawer_p6b_smoke2': 'drawer_p6_r2'}
#: Runtime and support bytes allowed to differ from r3, each with the CC_TASK_9 change behind it.
RUNTIME_DIFF = {'scripts/libero/native_world_protocol.py': 'B3 end-development reason terminal; B1 per-candidate infrastructure rule',
                'scripts/libero/native_world_fixloop_state.py': 'B1 tolerated rows are advisories after development end'}
SUPPORT_DIFF = {'run_two_task_cell.py': 'worker recovery round + generations; B1 infrastructure rounds; B2 solver-failure rounds; B3 terminal closeout',
                'canonical_assignment.py': 'generation-N sentinel dispatch accepted only in an open driver recovery request',
                'worker_recovery.py': 'new: resume evidence from the native stream; driver handoff',
                'smoke_recovery_driver.py': 'smoke root only: injection and turn bounds'}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p6 = load(P6DIR / 'prepare-p6-study.py', 'p6b_p6_stager')
p5, pc, p3, base, limits = p6.p5, p6.pc, p6.p3, p6.base, p6.limits
base.SUPPORT_FILES = tuple(base.SUPPORT_FILES) + ('worker_recovery.py',)
N = {}
NAME = ROOT = RUNTIME = None


def point(smoke):
    """Point the r3 stager chain at the p6b formal root or its recovery-smoke root."""
    global NAME, ROOT, RUNTIME
    nodes = SMOKE_CELLS if smoke else FORMAL
    NAME = HERE.name + ('-smoke' if smoke else '')
    ROOT = Path('/mnt/home/gewang/experiments') / NAME
    RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-' + NAME)
    p5.NAME = p6.NAME = NAME
    p5.STUDY_DIR = HERE / ('smoke' if smoke else 'records')
    p5.ARM = 'p6b'
    p5.CELL = re.compile(r'([a-z]+)_p6b_(?:r(\d+)|smoke(\d+))')
    p5.CELL_ORDER = tuple(c for node in sorted(nodes) for c in nodes[node])
    p5.NODE_CELLS = p6.NODE_CELLS = nodes
    N.clear(); N.update({c: 4 for c in p5.CELL_ORDER})
    p6.N.clear(); p6.N.update(N)
    if smoke:
        base.SUPPORT_FILES = tuple(base.SUPPORT_FILES) + ('smoke_recovery_driver.py',)
        pc.DRIVER = 'support/smoke_recovery_driver.py'
    p5.OVERLAY.update({p.name: p.resolve() for p in (HERE / 'support').glob('*.py')
                       if p.name in base.SUPPORT_FILES})
    p5.hs.OVERLAY = p5.OVERLAY
    return p5.repoint(False)


def case_config(cell, condition):
    case = p6.original_case(cell, condition)
    case.update(heldout_parallel=4, sweep_parallel=4, trial_threads=2)
    previous = json.loads((R3 / R3_CASE_OF[cell] / 'case.json').read_text())
    for key in ('suite', 'task', 'env_config'):
        assert case[key] == previous[key], (cell, key, case[key], previous[key])
    case['generation_context'] = case['generation_context'].replace(
        'p3-search + p4 coverage gate + aspire-v1 development budget',
        'p3-search + p4 coverage gate + aspire-v1 development budget + Task7 parallel + Task8 closeout'
        ' + Task9 worker recovery')
    return case


pc.case_config = p3.case_config = base.case_config = case_config


def study():
    s = p6.original_study()
    s['note'] = ('P6b fresh independent C cells: r3 method (P5 + Task7 parallel + Task8 closeout) + '
                 'Task9 worker recovery and B1/B2/B3 closeout repairs; one node; N=4')
    s['parallel_by_cell'] = dict(N)
    return s


pc.study = study


def campaign_plan():
    p = p6.original_plan()
    p.update(study_id=NAME, kind=study()['note'], parallel_by_cell=dict(N),
             case_of={c: R3_CASE_OF[c] + ' (r3) metadata only; no program/ledger/feedback inherited' for c in N})
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


def additional_verify(smoke):
    receipt = json.loads((ROOT / 'prepare-receipt.json').read_text())
    out, problems = {}, []
    for cell in N:
        case = json.loads((ROOT / cell / 'case.json').read_text()); repo = Path(case['sim'])
        errors = limits.check_case(case)
        if case['heldout_parallel'] != N[cell]:
            errors.append('N differs from frozen plan')
        task = repo / 'outputs/libero_fix_loop' / case['suite'] / case['task']
        if task.exists() and any(task.iterdir()):
            errors.append('cell does not start from empty task artifacts')
        for filename in ['scripts/common/native_cc_trial_process.py', 'scripts/libero/native_world_protocol.py',
                         'scripts/libero/native_world_fixloop_state.py', 'scripts/libero/native_world_heldout.py',
                         'scripts/libero/executable_world_profile.py']:
            if (repo / filename).read_bytes() != (SIM / filename).read_bytes():
                errors.append('runtime differs from final source: ' + filename)
        out[cell] = {'N': N[cell], 'cell_timeout_seconds': limits.cell_timeout(case), 'problems': errors}
        problems.extend(cell + ': ' + e for e in errors)
    for name in ('run_two_task_cell.py', 'canonical_assignment.py', 'worker_recovery.py',
                 *(('smoke_recovery_driver.py',) if smoke else ())):
        if digest(ROOT / 'support' / name) != digest(HERE / 'support' / name):
            problems.append(f'frozen support {name} differs from the p6b source')
    if not smoke and (ROOT / 'support/smoke_recovery_driver.py').exists():
        problems.append('the smoke wrapper must not be frozen into the formal root')
    if not (ROOT / 'launch/unit_supervisor.py').is_file():
        problems.append('frozen tracker dependency absent')
    if len({json.loads((ROOT / c / 'case.json').read_text())['claude_config_dir'] for c in N}) != len(N):
        problems.append('cells share a native config dir')
    return {'cells': out, 'problems': problems, 'byte_diff_vs_r3': diff_vs_r3(receipt)}


#: The staged slot GPU is a placeholder; each slot rebinds it at claim time.
SLOT_PORTS = re.compile(r'for p in \d+ \d+ \d+; do')  # the staged unit's service ports, rebound likewise
SLOT_GPU = (re.compile(r'^GPU:   \d+$', re.M), re.compile(r'physical GPU \d+ exclusively'))


def normalize(text, cell, reference):
    for a, b in ((str(ROOT), str(R3)), (str(RUNTIME), str(R3_RUNTIME)), (NAME, R3.name), (cell, reference)):
        text = text.replace(a, b)
    text = SLOT_GPU[0].sub('GPU:   <slot>', text)
    text = SLOT_PORTS.sub('for p in <unit ports>; do', text)
    return SLOT_GPU[1].sub('physical GPU <slot> exclusively', text)


def diff_vs_r3(receipt):
    """Every byte difference from the r3 staging, each with its CC_TASK_9 cause."""
    theirs = json.loads((R3 / 'prepare-receipt.json').read_text())
    rows, problems = {}, []
    for cell in N:
        reference = R3_CASE_OF[cell]
        runtime = sorted(n for n, h in receipt['runtime_sha256'][cell].items()
                         if theirs['runtime_sha256'][reference].get(n) != h)
        case = json.loads((ROOT / cell / 'case.json').read_text())
        ref = json.loads((R3 / reference / 'case.json').read_text())
        keys = sorted(k for k in set(case) | set(ref)
                      if normalize(json.dumps(case.get(k)), cell, reference) != json.dumps(ref.get(k)))
        prompts = {}
        for name in ('worker-prompt.md', 'coordinator-prompt.md'):
            a = normalize((ROOT / cell / name).read_text(), cell, reference)
            prompts[name] = a == normalize((R3 / reference / name).read_text(), reference, reference)
        rows[cell] = {'r3_reference': reference, 'runtime_differs': {n: RUNTIME_DIFF.get(n) for n in runtime},
                      'case_keys_differ': keys, 'prompts_equal_after_normalizing': prompts}
        problems += [f'{cell}: runtime {n} differs from r3' for n in runtime if n not in RUNTIME_DIFF]
        allowed = {'generation_context', 'repeat', 'runtime_manifest_sha256'}
        problems += [f'{cell}: case key {k} differs from r3' for k in keys if k not in allowed]
        problems += [f'{cell}: {n} differs from r3' for n, same in prompts.items() if not same]
    support = sorted(n for n in set(receipt['frozen_support']) | set(theirs['frozen_support'])
                     if receipt['frozen_support'].get(n) != theirs['frozen_support'].get(n))
    problems += [f'support {n} differs from r3' for n in support if n not in SUPPORT_DIFF]
    new = json.loads((ROOT / 'launch/launch-manifest.json').read_text())['files']
    old = json.loads((R3 / 'launch/launch-manifest.json').read_text())['files']
    launch = sorted(n for n in set(new) | set(old) if new.get(n) != old.get(n))
    problems += [f'launch {n} differs from r3' for n in launch if n != 'study.json']
    s_new = json.loads((ROOT / 'launch/study.json').read_text())
    s_old = json.loads((R3 / 'launch/study.json').read_text())
    study_keys = sorted(k for k in set(s_new) | set(s_old) if s_new.get(k) != s_old.get(k))
    allowed_study = {'cells', 'note', 'node_cells', 'parallel_by_cell', 'required_support', 'driver'}
    problems += [f'study.json key {k} differs from r3' for k in study_keys if k not in allowed_study]
    required = {'added': sorted(set(s_new['required_support']) - set(s_old['required_support'])),
                'removed': sorted(set(s_old['required_support']) - set(s_new['required_support']))}
    if required['removed'] or set(required['added']) - {'worker_recovery.py', 'smoke_recovery_driver.py'}:
        problems.append(f'required_support changed beyond the CC_TASK_9 helpers: {required}')
    return {'reference_root': str(R3), 'cells': rows, 'support_differs': {n: SUPPORT_DIFF.get(n) for n in support},
            'launch_differs': launch, 'study_json_keys_differ': study_keys, 'required_support_change': required,
            'normalized': 'result/runtime roots, study name, cell name, staged slot GPU and unit service-port lines', 'problems': problems}


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true'); group.add_argument('--verify', action='store_true')
    parser.add_argument('--recovery-smoke', action='store_true')
    args = parser.parse_args()
    where = point(args.recovery_smoke)
    if args.prepare:
        receipt = pc.prepare()
        base.freeze_bytes(Path(__file__), ROOT / 'frozen-prepare-p6b-study.py')
        base.record(ROOT / 'p6b-receipt.json', {
            'name': NAME, 'node_cells': p5.NODE_CELLS, 'N': N, 'r3_case_of': {c: R3_CASE_OF[c] for c in N},
            'overlay': {n: str(p) for n, p in p5.OVERLAY.items()}, 'where': where,
            'source_root': str(SIM), 'prepared_at': receipt['prepared_at']})
        return 0
    result = pc.verify()
    additional = additional_verify(args.recovery_smoke)
    target = HERE / ('smoke-verify-extra.json' if args.recovery_smoke else 'verify-extra.json')
    if target.exists():
        assert json.loads(target.read_text()) == additional, 'verification evidence changed; preserve and inspect'
    else:
        base.record(target, additional)
    print(json.dumps(additional, indent=2))
    return int(result['status'] != 'verified' or bool(additional['problems'])
               or bool(additional['byte_diff_vs_r3']['problems']))


if __name__ == '__main__':
    raise SystemExit(main())
