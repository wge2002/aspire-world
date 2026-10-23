#!/usr/bin/env python3
"""P6d (CC_TASK_14): the p6c pipeline plus the CC_TASK_14 method switches, staged for rbs-debug2.

Imports the p6b stager (`code-world-worker-recovery-p6b-20261021/prepare-p6b-study.py`,
unchanged; it imports the unchanged r3 stager) and re-points it, as the ns stager does.
Differences from the p6b / ns stagings, and only these:
  * names/roots: `<this dir>` + `-smoke` / `-trial`; cells named `<position>_p6d_...`;
  * cells: the suite/task/env_config of the matching p6b or ns cell (REFERENCE, asserted);
  * case key `method_switches` = scripts/libero/method_switches.P6D (recorded in every
    case, ledger identity and runtime manifest);
  * runtime overlay: base.C_OVERLAY + P6D_RUNTIME (the CC_TASK_12 runtime files are
    already C_OVERLAY members; their bytes now come from the worktree);
  * support overlay: p6b support, then p6c (G5/G6), then this dir's support (p6d driver
    and deadlines; the two smoke-only files only into the smoke root).
No inference, services or submissions here.

  prepare-p6d-study.py --smoke --prepare | --verify      1 unit, 2 cells (bounded)
  prepare-p6d-study.py --trial --prepare | --verify      2 units, 4 cells (full budget)
"""
from pathlib import Path
import argparse, difflib, hashlib, importlib.util, json, re, sys

HERE = Path(__file__).resolve().parent
SIM = HERE.parents[2]
P6B_DIR = SIM / 'docs/experiments/code-world-worker-recovery-p6b-20261021'
P6C_SUPPORT = SIM / 'docs/experiments/code-world-pipeline-repair-p6c-20261022/support'
P6B_ROOT = Path('/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021')
NS_ROOT = Path('/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-ns')
sys.path.insert(0, str(SIM / 'scripts/libero'))
import method_switches as ms  # noqa: E402

#: position -> (suite, task stem). The worker's goal is the runtime task_language.
TASKS = {
    'drawer': ('libero_goal_swap', 'open_the_middle_drawer_of_the_cabinet'),
    'mokastove': ('libero_10_swap', 'KITCHEN_SCENE3_turn_on_the_stove_and_put_the_moka_pot_on_it'),
    'bowldrawerclose': ('libero_10_swap',
                        'KITCHEN_SCENE4_put_the_black_bowl_in_the_bottom_drawer_of_the_cabinet_and_close_it'),
    'objtaskmilk': ('libero_object_task', 'pick_up_the_ketchup_and_place_it_in_the_basket'),
    'spatialtaskcookie': ('libero_spatial_task',
                          'pick_up_the_black_bowl_next_to_the_ramekin_and_place_it_on_the_plate'),
}
#: cell -> (reference root, reference cell): same suite/task/env_config, metadata only.
REFERENCE = {
    'drawer_p6d_smoke1': (P6B_ROOT, 'drawer_p6b_r1'),
    'objtaskmilk_p6d_smoke2': (NS_ROOT, 'objtaskmilk_p6b_r1'),
    'mokastove_p6d_r1': (NS_ROOT, 'mokastove_p6b_r1'),
    'bowldrawerclose_p6d_r1': (P6B_ROOT, 'bowldrawerclose_p6b_r1'),
    'objtaskmilk_p6d_r1': (NS_ROOT, 'objtaskmilk_p6b_r1'),
    'spatialtaskcookie_p6d_r1': (NS_ROOT, 'spatialtaskcookie_p6b_r1'),
}
#: Claim order alternates the two "verification contract" cells and the two "selection" cells.
LAYOUTS = {'smoke': {0: ('drawer_p6d_smoke1', 'objtaskmilk_p6d_smoke2')},
           'trial': {0: ('mokastove_p6d_r1', 'objtaskmilk_p6d_r1', 'bowldrawerclose_p6d_r1',
                         'spatialtaskcookie_p6d_r1')}}
#: Units x GPUs of each launch on rbs-debug2 (unit-entry.sh --gpus).
GPUS = {'smoke': '0,1,2,3', 'trial': '0,1,2,3,4,5,6,7'}
N = 4  # every cell, as p6b / ns N=4 cells (sweep_parallel = heldout_parallel = 4)
ENV_CONFIG = 'env_configs/libero/franka_libero_traced.yaml'
#: Worktree runtime files added to the frozen overlay, each with its CC_TASK_14 change.
P6D_RUNTIME = {
    'scripts/libero/method_switches.py': 'new: switch validation, P6D values, env, manifest entry',
    'cap/world_model/judgment_calibration.py': 'new: M3b per-seed world judgment vs simulator',
    'cap/world_model/skills/subgoal-verification.md': 'new: M3a skill (outside .claude/)',
    'cap/world_model/skills/api-revision-m4.md': 'new: M4 API doc (outside .claude/)',
    'cap/integrations/franka/libero_reduced.py': 'M4 solve_ik report, segmented goto_pose, seeded subsample',
    'cap/serving/launch_contact_graspnet_server.py': 'M4 request-content seeding',
    'cap/envs/simulators/libero.py': 'success_rule libero_latch',
}
#: Already-overlaid runtime files whose worktree bytes differ from p6b's, with the change behind each.
RUNTIME_DIFF = {
    'scripts/libero/native_world_protocol.py': 'p6c G6 watchdog verdict; p6d switches, resweep, calibration, record-only search gate',
    'scripts/libero/native_world_fixloop_state.py': 'p6c finish_trial(watchdog); p6d sweep-only score, resweep phase',
    'scripts/libero/runtime/native_world_protocol.py': 'p6c close-infra settles watchdog rows',
    'scripts/common/native_cc_trial_process.py': 'p6c persistence grace',
    'scripts/libero/native_world_heldout.py': 'p6d held-out watchdog settle, unseen seed set',
    'scripts/libero/executable_world_profile.py': 'p6d prompt sections, record-only screening',
    'cap/world_model/executable_world.py': 'p6d budget exhaustion is not a world fault',
    'cap/envs/tasks/base.py': 'p6d budget exhaustion is not a program error',
    **P6D_RUNTIME,
}
SUPPORT_DIFF = {'run_two_task_cell.py': 'p6c G5/G6 + p6d resweep / unseen steps',
                'worker_recovery.py': 'p6c failure_evidence (G5)',
                'full_deadlines.py': 'p6d method_extra reserve in cell_timeout',
                'smoke_p6d_driver.py': 'smoke root only: turn bounds, probe, held-out injection',
                'smoke_heldout_inject.py': 'smoke root only: one held-out seed under a 60 s watchdog'}
SMOKE_ONLY = ('smoke_p6d_driver.py', 'smoke_heldout_inject.py')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b = load(P6B_DIR / 'prepare-p6b-study.py', 'p6d_p6b_stager')
p6, p5, pc, p3, base = b.p6, b.p5, b.pc, b.p3, b.base
limits = load(HERE / 'support/full_deadlines.py', 'p6d_deadlines')
b.limits = p6.limits = p5.p5_deadlines = limits
p5.TASKS_BY_POSITION.update(TASKS)
base.C_OVERLAY = tuple(base.C_OVERLAY) + tuple(n for n in P6D_RUNTIME if n not in base.C_OVERLAY)
KIND = NAME = ROOT = RUNTIME = None


def position(cell):
    return cell.split('_')[0]


def point(kind):
    """b.point with this root, these cells, the p6d arm and the p6b -> p6c -> p6d support overlay."""
    global KIND, NAME, ROOT, RUNTIME
    KIND, nodes = kind, LAYOUTS[kind]
    NAME = HERE.name + '-' + kind
    ROOT = Path('/mnt/home/gewang/experiments') / NAME
    RUNTIME = Path('/mnt/home/gewang/code/ASPIRE-' + NAME)
    b.NAME, b.ROOT, b.RUNTIME = NAME, ROOT, RUNTIME
    p5.NAME = p6.NAME = NAME
    p5.STUDY_DIR = HERE / kind
    p5.ARM = 'p6d'
    p5.CELL = re.compile(r'([a-z]+)_p6d_(?:r(\d+)|smoke(\d+))')
    p5.CELL_ORDER = tuple(c for node in sorted(nodes) for c in nodes[node])
    p5.NODE_CELLS = p6.NODE_CELLS = nodes
    b.N.clear(); b.N.update({c: N for c in p5.CELL_ORDER})
    p6.N.clear(); p6.N.update(b.N)
    if kind == 'smoke':
        base.SUPPORT_FILES = tuple(base.SUPPORT_FILES) + tuple(n for n in SMOKE_ONLY if n not in base.SUPPORT_FILES)
        pc.DRIVER = 'support/smoke_p6d_driver.py'
    for folder in (P6B_DIR / 'support', P6C_SUPPORT, HERE / 'support'):
        p5.OVERLAY.update({p.name: p.resolve() for p in folder.glob('*.py') if p.name in base.SUPPORT_FILES})
    p5.hs.OVERLAY = p5.OVERLAY
    return p5.repoint(False)


def reference_case(cell):
    root, ref = REFERENCE[cell]
    return json.loads((root / ref / 'case.json').read_text())


def case_config(cell, condition):
    """The p6b case with N=4, the cell's reference suite/task/env_config and the method switches."""
    case = p6.original_case(cell, condition)
    case.update(heldout_parallel=N, sweep_parallel=N, trial_threads=2)
    suite, task = TASKS[position(cell)]
    ref = reference_case(cell)
    assert (case['suite'], case['task'], case['env_config']) == (suite, task, ENV_CONFIG) == \
        (ref['suite'], ref['task'], ref['env_config']), (cell, case['suite'], case['task'], ref['task'])
    case[ms.KEY] = dict(ms.P6D)
    problems = ms.errors(case)
    assert not problems, problems
    case['generation_context'] = case['generation_context'].replace(
        'p3-search + p4 coverage gate + aspire-v1 development budget',
        'p3-search + p4 coverage gate + aspire-v1 development budget + Task7 parallel + Task8 closeout'
        ' + Task9 worker recovery + Task12 p6c G5/G6 + Task14 p6d method switches')
    return case


pc.case_config = p3.case_config = base.case_config = case_config
_p5_check_case = p3.check_case


def check_case(cell, case):
    """The p5 case gate, plus: the case carries exactly the P6D method switches."""
    result = _p5_check_case(cell, case)
    if case.get(ms.KEY) != ms.P6D:
        result["problems"].append(f"case[{ms.KEY!r}] is {case.get(ms.KEY)!r}, expected the P6D switches")
    result["checked"] = sorted(set(result["checked"]) | {ms.KEY})
    return result


p3.check_case = check_case


def bind_manifest(case):
    """base.bind_manifest, with the method switches recorded in the runtime manifest before it is pinned."""
    import subprocess
    sim = base.cell_sim(case['id'])
    external = [str(path) for path in base.external_inputs(case)]
    proc = subprocess.run(
        [str(sim / '.venv-libero/bin/python3'), '-c', base.MANIFEST_PROGRAM,
         str(sim / 'scripts/common'), str(sim), json.dumps(external), json.dumps([case['claude_bin']])],
        cwd=sim, capture_output=True, text=True)
    if proc.returncode:
        raise SystemExit(f"{case['id']}: manifest build failed:\n{proc.stderr[-4000:]}")
    manifest = json.loads(proc.stdout)
    manifest[ms.KEY] = ms.manifest_entry(case)
    path = Path(case['runtime_manifest'])
    base.record(path, manifest)
    case['runtime_manifest_sha256'] = base.digest(path)
    base.record(Path(case['control']) / 'case.json', case)
    return case


base.bind_manifest = bind_manifest
if hasattr(p3, 'bind_manifest'):
    p3.bind_manifest = bind_manifest


def study():
    s = b.study()
    s['note'] = ('P6d fresh independent C cells: p6b method + Task12 p6c G5/G6 + Task14 method switches '
                 '(subgoal verification, judgment calibration, record-only screening, sweep-only score, '
                 'pre-freeze re-sweep, unseen 66-80, held-out watchdog settle, API m4-r1, success latch); '
                 f'{KIND}; rbs-debug2 direct launch; N=4')
    s['parallel_by_cell'] = dict(b.N)
    return s


pc.study = study


def campaign_plan():
    p = p6.original_plan()
    p.update(study_id=NAME, kind=study()['note'], parallel_by_cell=dict(b.N),
             case_of={c: f'{REFERENCE[c][1]} ({REFERENCE[c][0].name}) suite/task/env_config only; '
                         'no program/ledger/feedback inherited' for c in b.N})
    p['launch_shape']['mode'] = (f'direct launch on rbs-debug2 (no DLC): unit-entry.sh --gpus {GPUS[KIND]}, '
                                 f'{len(GPUS[KIND].split(",")) // 4} unit(s) x two slots')
    p['development_budget']['freeze'] += ('; or outer Task8 phase-B end selects a valid complete best candidate; '
                                          'p6d: every version is swept on all 15 seeds (sweep-only score), then '
                                          'the outer pre-freeze re-sweep selects on both runs')
    p['development_budget']['phase_a'] += '; stagnation starts a new diagnostic round under the original cumulative deadline'
    p['worker_recovery'] = ('Task9 unresumable primary or Task12 G5 dead primary -> new generation in a new '
                            'coordinator session; same frozen assignment + driver handoff, same ledger/deadline')
    p[ms.KEY] = {'switches': dict(ms.P6D), 'manifest_entry': ms.manifest_entry({ms.KEY: dict(ms.P6D)}),
                 'unseen_use': ('measured once after the freeze, before held-out; never shown to a worker; does '
                                'not change this cell; the formal round chooses between independent cells by '
                                'its pre-registered rule')}
    return p


pc.campaign_plan = campaign_plan


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


SLOT = ((re.compile(r'^GPU:   \d+$', re.M), 'GPU:   <slot>'),
        (re.compile(r'physical GPU \d+ exclusively'), 'physical GPU <slot> exclusively'),
        (re.compile(r'for p in \d+ \d+ \d+; do'), 'for p in <unit ports>; do'))


def normalized(text, cell, case, root, runtime, name):
    suite, task = case['suite'], case['task']
    for a, c in ((str(root), '<ROOT>'), (str(runtime), '<RUNTIME>'), (name, '<NAME>'),
                 (f'{suite}/{task}', '<SUITE>/<TASK>'), (f'{suite}_{task}', '<SUITE>_<TASK>'),
                 (f'SUITE: {suite}', 'SUITE: <SUITE>'), (task, '<TASK>'), (cell, '<CELL>')):
        text = text.replace(a, c)
    text = re.sub(r'(suite:\s+)' + re.escape(suite) + r'\b', r'\1<SUITE>', text)
    for pattern, repl in SLOT:
        text = pattern.sub(repl, text)
    return text


def method_lines(case):
    """Lines the method switches add to / remove from the rendered profile section of this case."""
    profile = load(SIM / 'scripts/libero/executable_world_profile.py', 'p6d_verify_profile')
    plain = profile.section({k: v for k, v in case.items() if k != ms.KEY}).splitlines()
    switched = profile.section(case).splitlines()
    blanks = switched.count('') - plain.count('')  # blank lines the added sections bring with them
    return set(switched) - set(plain), set(plain) - set(switched), blanks


CASE_DIFF = {'method_switches': 'CC_TASK_14 switches', 'generation_context': 'adds Task12/Task14',
             'runtime_manifest_sha256': 'roots + overlay + manifest method entry', 'repeat': 'repeat of the reference'}
STUDY_DIFF = {'cells': 'cell list', 'node_cells': 'cell list', 'note': 'p6d note', 'parallel_by_cell': 'cell list',
              'driver': 'smoke wrapper (smoke root only)', 'required_support': 'smoke-only files',
              'required_runtime': 'adds exactly the P6D_RUNTIME overlay files (checked)'}


def diff_vs_reference(receipt):
    """Every byte difference from each cell's p6b / ns reference staging, each with its cause."""
    problems, cells, refs = [], {}, {}
    for cell in b.N:
        root, ref = REFERENCE[cell]
        theirs = refs.setdefault(root, json.loads((root / 'prepare-receipt.json').read_text()))
        ref_case = reference_case(cell)
        case = json.loads((ROOT / cell / 'case.json').read_text())
        mine, other = receipt['runtime_sha256'][cell], theirs['runtime_sha256'][ref]
        runtime = sorted(n for n in set(mine) | set(other) if mine.get(n) != other.get(n))
        problems += [f'{cell}: runtime {n} differs from {ref} without a recorded cause'
                     for n in runtime if n not in RUNTIME_DIFF]
        own = (cell, case, ROOT, RUNTIME, NAME)
        far = (ref, ref_case, root, Path('/mnt/home/gewang/code/ASPIRE-' + root.name), root.name)
        keys = sorted(k for k in set(case) | set(ref_case)
                      if normalized(json.dumps(case.get(k)), *own) != normalized(json.dumps(ref_case.get(k)), *far))
        problems += [f'{cell}: case key {k} differs from {ref}' for k in keys if k not in CASE_DIFF]
        added, removed, blanks = method_lines(case)
        prompts = {}
        for name in ('worker-prompt.md', 'coordinator-prompt.md'):
            a = normalized((root / ref / name).read_text(), *far).splitlines()
            c = normalized((ROOT / cell / name).read_text(), *own).splitlines()
            lines = [l for l in difflib.unified_diff(a, c, lineterm='', n=0)
                     if l[:1] in '+-' and not l.startswith(('+++', '---'))]
            unexplained = [l for l in lines if not ((l[0] == '+' and l[1:] in added)
                                                     or (l[0] == '-' and l[1:] in removed))]
            net_blank = sum(l == '+' for l in unexplained) - sum(l == '-' for l in unexplained)
            if 0 <= net_blank <= max(blanks, 0):  # blank lines explained up to the switches' own count
                unexplained = [l for l in unexplained if l not in ('+', '-')]
            prompts[name] = {'changed_lines': len(lines), 'added': sum(l[0] == '+' for l in lines),
                             'removed': sum(l[0] == '-' for l in lines), 'unexplained': unexplained}
            problems += [f'{cell}: {name} line differs from {ref} beyond the method switches: {l[:120]}'
                         for l in unexplained]
        cells[cell] = {'reference': f'{root.name}/{ref}', 'runtime_differs': {n: RUNTIME_DIFF.get(n) for n in runtime},
                       'case_keys_differ': {k: CASE_DIFF.get(k) for k in keys}, 'prompts': prompts}
    first = refs[REFERENCE[next(iter(b.N))][0]]
    support = sorted(n for n in set(receipt['frozen_support']) | set(first['frozen_support'])
                     if receipt['frozen_support'].get(n) != first['frozen_support'].get(n))
    problems += [f'frozen support {n} differs from the reference without a recorded cause'
                 for n in support if n not in SUPPORT_DIFF]
    ref_root = REFERENCE[next(iter(b.N))][0]
    new = json.loads((ROOT / 'launch/launch-manifest.json').read_text())['files']
    old = json.loads((ref_root / 'launch/launch-manifest.json').read_text())['files']
    launch = sorted(n for n in set(new) | set(old) if new.get(n) != old.get(n))
    problems += [f'launch {n} differs from the reference' for n in launch if n != 'study.json']
    s_new = json.loads((ROOT / 'launch/study.json').read_text())
    s_old = json.loads((ref_root / 'launch/study.json').read_text())
    study_keys = sorted(k for k in set(s_new) | set(s_old) if s_new.get(k) != s_old.get(k))
    problems += [f'study.json key {k} differs from the reference' for k in study_keys if k not in STUDY_DIFF]
    if 'required_runtime' in study_keys:
        grown = set(s_new['required_runtime']) - set(s_old['required_runtime'])
        if set(s_old['required_runtime']) - set(s_new['required_runtime']) or not grown <= set(P6D_RUNTIME):
            problems.append(f'study.json required_runtime changes beyond P6D_RUNTIME: {sorted(grown)}')
    contract = HERE / KIND / 'baseline-contract.json'
    same_contract = contract.is_file() and digest(contract) == digest(P6B_DIR / 'records/baseline-contract.json')
    if not same_contract:
        problems.append('baseline contract differs from p6b node 0')
    return {'reference_roots': sorted(str(r) for r in refs), 'cells': cells,
            'support_differs': {n: SUPPORT_DIFF.get(n) for n in support}, 'launch_differs': launch,
            'study_json_keys_differ': {k: STUDY_DIFF.get(k) for k in study_keys},
            'baseline_contract_bytes_equal': same_contract,
            'normalized': 'result/runtime roots, study name, cell name, suite, task, staged slot GPU and unit ports',
            'problems': problems}


def additional_verify():
    receipt = json.loads((ROOT / 'prepare-receipt.json').read_text())
    guard = load(ROOT / 'support/cell_read_guard.py', 'p6d_read_guard')
    out, problems = {}, []
    for cell in b.N:
        case = json.loads((ROOT / cell / 'case.json').read_text()); repo = Path(case['sim'])
        errors = limits.check_case(case)
        if case.get(ms.KEY) != ms.P6D or ms.errors(case):
            errors.append('method switches differ from P6D or are invalid')
        if (case['suite'], case['task'], case['env_config']) != (*TASKS[position(cell)], ENV_CONFIG):
            errors.append('suite/task/env_config differ from the task table')
        task = repo / 'outputs/libero_fix_loop' / case['suite'] / case['task']
        if task.exists() and any(task.iterdir()):
            errors.append('cell does not start from empty task artifacts')
        for filename in sorted(set(base.C_OVERLAY)):
            if (repo / filename).read_bytes() != (SIM / filename).read_bytes():
                errors.append('runtime differs from final source: ' + filename)
        manifest = json.loads(Path(case['runtime_manifest']).read_text())
        if manifest.get(ms.KEY) != ms.manifest_entry(case):
            errors.append('runtime manifest lacks the method switch entry')
        for skill in ('cap/world_model/skills/subgoal-verification.md', 'cap/world_model/skills/api-revision-m4.md'):
            if skill not in manifest['files']:
                errors.append(f'{skill} not pinned in the runtime manifest')
            if not guard.path_allowed(skill, case, repo):
                errors.append(f'the read guard refuses {skill}')
        prompt = (ROOT / cell / 'worker-prompt.md').read_text()
        for marker in ('### Subgoal verification contract', 'cap/world_model/skills/subgoal-verification.md',
                       'cap/world_model/skills/api-revision-m4.md', '### Success rule libero_latch',
                       '### World goal-judgment calibration', '### Selection at the freeze'):
            if prompt.count(marker) != 1:
                errors.append(f'worker prompt has {prompt.count(marker)} x {marker!r}')
        if '.claude/libero/fix-loop/skills/subgoal' in prompt:
            errors.append('worker prompt still points at the .claude skill path')
        out[cell] = {'suite': case['suite'], 'task': case['task'], 'N': case['heldout_parallel'],
                     'cell_timeout_seconds': limits.cell_timeout(case), 'problems': errors}
        problems.extend(cell + ': ' + e for e in errors)
    names = ['run_two_task_cell.py', 'worker_recovery.py', 'full_deadlines.py'] + (
        list(SMOKE_ONLY) if KIND == 'smoke' else [])
    for name in names:
        source = HERE / 'support' / name
        if digest(ROOT / 'support' / name) != digest(source):
            problems.append(f'frozen support {name} differs from the p6d source')
    if KIND != 'smoke' and any((ROOT / 'support' / n).exists() for n in SMOKE_ONLY):
        problems.append('a smoke-only file was frozen into the trial root')
    if digest(ROOT / 'support/worker_recovery.py') != digest(P6C_SUPPORT / 'worker_recovery.py'):
        problems.append('frozen worker_recovery.py is not the p6c source')
    if not (ROOT / 'launch/unit_supervisor.py').is_file():
        problems.append('frozen tracker dependency absent')
    if len({json.loads((ROOT / c / 'case.json').read_text())['claude_config_dir'] for c in b.N}) != len(b.N):
        problems.append('cells share a native config dir')
    timeouts = [out[c]['cell_timeout_seconds'] for c in b.N]
    return {'kind': KIND, 'cells': out, 'problems': problems, 'byte_diff_vs_reference': diff_vs_reference(receipt),
            'gpus': GPUS[KIND], 'minimum_window_minutes': -(-(max(timeouts) + 90 + 1800 + 900) // 60)}


def main():
    parser = argparse.ArgumentParser()
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--smoke', action='store_true'); which.add_argument('--trial', action='store_true')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare', action='store_true'); group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    where = point('smoke' if args.smoke else 'trial')
    if args.prepare:
        receipt = pc.prepare()
        base.freeze_bytes(Path(__file__), ROOT / 'frozen-prepare-p6d-study.py')
        base.freeze_bytes(P6B_DIR / 'prepare-p6b-study.py', ROOT / 'frozen-prepare-p6b-study.py')
        base.record(ROOT / 'p6d-receipt.json', {
            'name': NAME, 'kind': KIND, 'node_cells': p5.NODE_CELLS, 'N': dict(b.N), 'tasks': TASKS,
            'reference': {c: f'{r.name}/{ref}' for c, (r, ref) in REFERENCE.items() if c in b.N},
            'method_switches': ms.P6D, 'p6d_runtime': P6D_RUNTIME, 'gpus': GPUS[KIND],
            'overlay': {n: str(p) for n, p in p5.OVERLAY.items()}, 'where': where,
            'p6b_stager_sha256': digest(P6B_DIR / 'prepare-p6b-study.py'),
            'source_root': str(SIM), 'prepared_at': receipt['prepared_at']})
        return 0
    result = pc.verify()
    additional = additional_verify()
    target = HERE / f'{KIND}-verify-extra.json'
    if target.exists():
        assert json.loads(target.read_text()) == additional, 'verification evidence changed; preserve and inspect'
    else:
        base.record(target, additional)
    print(json.dumps(additional, indent=2))
    return int(result['status'] != 'verified' or bool(additional['problems'])
               or bool(additional['byte_diff_vs_reference']['problems']))


if __name__ == '__main__':
    raise SystemExit(main())
