#!/usr/bin/env python3
"""Generate one DSW real-work preflight runner per cell from the verified Oct-03 template.

The Oct-03 drawer runner proved the nonprivileged C executable-world replay path
end to end (observation -> SAM3 -> GraspNet -> IK, ExecutableSession, public
tape, manifest) in a scratch directory. This generator rewrites it per cell:
paths, task, gate provenance, source pins measured from the engineering
checkout at generation time, retry wording, and, for sealed cells, the sealed
outcome environment variable plus a post-run proof that the sealed file was
written and the public tree carries no label.

Usage: make-preflights.py [--engineering PATH] [--cells a,b] [--out DIR] [--check]
`--check` only generates into a temporary directory and syntax-checks the output.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE.parent / "code-world-pipeline-repair-20261003/run-dsw-preflight-drawer-c.py"
STUDY = "code-world-gate-ablation-20261005"
PARENT = f"/mnt/home/gewang/experiments/{STUDY}"
RUNTIME = f"/mnt/home/gewang/code/ASPIRE-{STUDY}"
DEFAULT_ENGINEERING = "/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim"
PINNED = ("scripts/libero/replay_trial.py", "scripts/libero/native_cc_toolchain_probe.py",
          "env_configs/libero/franka_libero_traced.yaml", "cap/envs/tasks/base.py",
          "cap/envs/simulators/libero.py", "cap/world_model/executable_world.py",
          "cap/world_model/simple_world.py", "cap/world_model/judgment_world.py",
          "cap/world_model/evidence_state.py", "cap/world_model/decision_revision.py",
          "cap/world_model/development_gate.py", "cap/world_model/vlm_judge.py")


def study():
    spec = importlib.util.spec_from_file_location("gate_prepare", HERE / "prepare-gate-study.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slug(cell: str) -> str:
    return cell.lower().replace("_", "-")


def replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"template anchor missing or duplicated ({text.count(old)}): {old[:70]!r}")
    return text.replace(old, new)


def render(cell: str, task: str, gate: str, pins: dict, engineering: str) -> str:
    text = TEMPLATE.read_text()
    sealed = gate != "oracle"
    text = replace_once(text, "REPO = Path('/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim')",
                        f"REPO = Path({engineering!r})")
    text = replace_once(text, "PARENT = Path('/mnt/home/gewang/experiments/code-world-pipeline-repair-20261003')",
                        f"PARENT = Path({PARENT!r})")
    text = replace_once(text, "OUT = PARENT / 'coordination/dsw-preflight-drawer-c'",
                        f"OUT = PARENT / 'coordination/dsw-preflight-{slug(cell)}'\n"
                        f"CELL = {cell!r}\nTASK = {task!r}\nGATE = {gate!r}\nSEALED = {sealed!r}")
    text = replace_once(text, "PRODUCTION_C_CONTROL = PARENT / 'drawer_C'", f"PRODUCTION_C_CONTROL = PARENT / {cell!r}")
    text = replace_once(text, "'/mnt/home/gewang/code/ASPIRE-code-world-pipeline-repair-20261003'", repr(RUNTIME))
    text = replace_once(text, "'/cells/drawer_C/aspire/sim'", repr(f"/cells/{cell}/aspire/sim"))
    text = re.sub(r"^SOURCE_PINS = \{.*\}$", "SOURCE_PINS = " + repr(pins), text, count=1, flags=re.M)
    text = replace_once(text, '''        "c_arm": "full",
    }''', '''        "c_arm": "full",
        "development_gate": GATE,
    }''')
    text = replace_once(text, "    remaining_real_budget = {str(seed): 2 if seed == 51 else 3",
                        "    remaining_real_budget = {str(seed): 3")
    text = replace_once(text, "        charged=True,\n", "        spends_retry=True,\n        imported=False,\n        development_gate=GATE,\n        sealed=SEALED,\n")
    text = replace_once(text, "            'Seed 51 attempt 1/3 charged against drawer_C (condition C, '",
                        "            'Seed 51: one scratch diagnostic retry for ' + CELL + ' (condition C, '")
    text = replace_once(text, "        ASPIRE_TOOLCHAIN_PROBE_OBJECT='bowl',\n    )",
                        "        ASPIRE_TOOLCHAIN_PROBE_OBJECT='bowl',\n    )\n"
                        "    if SEALED:\n"
                        "        # The protocol sets this for every sealed development trial; the\n"
                        "        # preflight proves the replay honours it on a real simulator run.\n"
                        "        env['ASPIRE_SEALED_OUTCOME'] = str(OUT / 'sealed' / 'outcome.json')")
    text = replace_once(text, "        OUT / 'results',\n", "        OUT / 'results',\n        OUT / 'sealed',\n")
    text = replace_once(text, '''    world_ok = judgment_manifest.exists()
    passed = (rc == 0
              and proof.get('passed') is True
              and (OUT / 'resolved-environment.json').exists()
              and world_ok)
''', '''    world_ok = judgment_manifest.exists()
    gate_ok, gate_evidence = False, {}
    if world_ok:
        manifest = json.loads(judgment_manifest.read_text())
        result = manifest.get("result") or {}
        if SEALED:
            sys.path.insert(0, str(REPO.parents[1]))
            from aspire.sim.cap.world_model.development_gate import public_leaks, read_sealed_outcome
            outcome = read_sealed_outcome(OUT / 'sealed' / 'outcome.json')
            leaks = public_leaks(OUT / 'results') + public_leaks(BUNDLE_DIR)
            gate_ok = (outcome is not None and result.get("outcome") == "sealed"
                       and "task_completed" not in result and "reward" not in result
                       and not leaks and Path(outcome["trial_dir"]).name.endswith("_sealed"))
            gate_evidence = {"sealed_outcome_written": outcome is not None,
                             "sealed_trial_dir": None if outcome is None else outcome["trial_dir"],
                             "public_leaks": leaks, "manifest_result": result}
        else:
            gate_ok = "task_completed" in result
            gate_evidence = {"manifest_result": result}
    passed = (rc == 0
              and proof.get('passed') is True
              and (OUT / 'resolved-environment.json').exists()
              and world_ok and gate_ok)
''')
    text = replace_once(text, "        judgment_world_manifest=str(judgment_manifest) if world_ok else None,\n",
                        "        judgment_world_manifest=str(judgment_manifest) if world_ok else None,\n"
                        "        gate_check=gate_evidence,\n")
    # Everything else that named the drawer cell: docstring, bundle task gate, args.
    text = text.replace("open_the_middle_drawer_of_the_cabinet", task)
    text = text.replace("drawer_C", cell).replace("dsw-preflight-drawer-c", f"dsw-preflight-{slug(cell)}")
    text = text.replace("This run charges one attempt against seed 51", "This run spends one scratch diagnostic retry on seed 51")
    text = text.replace("# 3. Charged-attempt accounting", "# 3. Retry accounting")
    text = text.replace("# This run consumes attempt 1 of 3 for seed 51.", "# This run is a scratch diagnostic; nothing is imported into the cell ledger.")
    if "charged" in text.lower():
        raise SystemExit("rename incomplete in generated runner")
    ast.parse(text)
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engineering", default=DEFAULT_ENGINEERING,
                        help="checkout the pins are measured from (the stager's overlay source)")
    parser.add_argument("--cells", help="comma-separated cell ids; default every cell of the study")
    parser.add_argument("--out", type=Path, default=HERE)
    parser.add_argument("--check", action="store_true", help="generate into a temp dir and syntax-check only")
    args = parser.parse_args(argv)
    module = study()
    cells = ([c.strip() for c in args.cells.split(",") if c.strip()] if args.cells
             else [c for c, _ in module.CELLS])
    engineering = Path(args.engineering)
    missing = [n for n in PINNED if not (engineering / n).is_file()]
    if missing:
        raise SystemExit(f"cannot measure pins; missing in {engineering}: {missing}")
    pins = {n: hashlib.sha256((engineering / n).read_bytes()).hexdigest() for n in PINNED}
    out = Path(tempfile.mkdtemp(prefix="preflights-")) if args.check else args.out
    written = []
    for cell in cells:
        task = module.TASKS[cell.split("_")[0]]
        text = render(cell, task, module.gate_of(cell), pins, str(engineering))
        path = out / f"run-dsw-preflight-{slug(cell)}.py"
        path.write_text(text)
        written.append(str(path))
    print("\n".join(written))
    print(f"{len(written)} runner(s); pins measured from {engineering}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
