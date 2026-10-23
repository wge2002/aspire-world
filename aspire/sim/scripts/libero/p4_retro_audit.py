# SPDX-License-Identifier: MIT
"""CC_TASK_4 retro audit: re-run `check` on finished p4 cells under old and new rules.

Read-only. For each cell the ledger and the small report files `check` reads
(fix_code.py, fix_world_program.py, findings.md, task_analysis.md, the BLOCKED
notes and the working copy) are copied into a scratch directory; nothing in a
result root is opened for writing. `check` runs twice on the same copy: once
with the pre-change protocol modules (`--old`, the snapshot saved before
CC_TASK_4 edited them) and once with the current worktree modules. The
report-only readers (`search_control`, `coverage_triage`, `world_use`) need the
trial directories, which are not copied; they never enter `errors`, `ready`
or `decision_type`, so they are stubbed out.

    python scripts/libero/p4_retro_audit.py --old <dir with scripts/libero + cap/world_model> \
        --out audit.json <result_root> [<result_root> ...]
"""
from __future__ import annotations

import argparse
import glob
import importlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

SIM = Path(__file__).resolve().parents[2]
REPORTS = ("fix_code.py", "fix_world_program.py", "findings.md", "task_analysis.md")


def load_modules(root: Path):
    """Import native_world_fixloop_state / native_world_protocol from `root` in isolation."""
    for name in ("native_world_fixloop_state", "native_world_protocol"):
        sys.modules.pop(name, None)
    # The old snapshot holds only the two changed modules; their unchanged
    # siblings (native_cc_freeze, executable_world_profile, ...) come from SIM.
    sys.path[:0] = [str(root / "scripts/libero"), str(SIM / "scripts/libero"), str(SIM / "scripts/common")]
    try:
        state = importlib.import_module("native_world_fixloop_state")
        protocol = importlib.import_module("native_world_protocol")
    finally:
        del sys.path[:3]
    return state, protocol


def scratch_copy(cell: Path, case: dict, scratch: Path) -> tuple[Path, Path]:
    (task,) = glob.glob(str(cell / "outputs/libero_fix_loop/*/*/development_state.json"))
    source = Path(task).parent
    repo = scratch / "repo"
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    task_dir.mkdir(parents=True)
    shutil.copy(source / "development_state.json", task_dir)
    for name in REPORTS:
        if (source / name).is_file():
            shutil.copy(source / name, task_dir)
    (task_dir / "attempts").mkdir()
    for note in (source / "attempts").glob("seed_*_BLOCKED.md"):
        shutil.copy(note, task_dir / "attempts")
    working = cell / "outputs/working_codes"
    if working.is_dir():
        shutil.copytree(working, repo / "outputs/working_codes")
    return repo, task_dir


def runtime_progress(state_mod):
    """The fresh-study runtime entry's `progress`: closed infra rows are not errors.

    The driver runs `scripts/libero/runtime/native_world_protocol.py check`, which
    replaces `NativeWorldState.progress` with exactly this filter before calling
    the ordinary protocol (see `progress()` / `closed()` there). The audit applies
    the same filter to whichever ledger module it loaded.
    """
    original = state_mod.NativeWorldState.progress

    def closed(row):
        return (row.get("status") == "infrastructure_error"
                and row.get("recovery", {}).get("disposition") == "closed_without_usable_evidence")

    def progress(self):
        result = original(self)
        directories = {r["directory"] for r in self.data["trials"] if closed(r)}
        result["infrastructure_errors"] = [p for p in result["infrastructure_errors"] if p not in directories]
        return result

    return progress


def run_check(modules, case: dict, repo: Path, task_dir: Path) -> dict:
    state_mod, protocol = modules
    if not getattr(state_mod.NativeWorldState, "_audit_runtime_patched", False):
        state_mod.NativeWorldState.progress = runtime_progress(state_mod)
        state_mod.NativeWorldState._audit_runtime_patched = True
    data = json.loads((task_dir / "development_state.json").read_text())
    state = state_mod.NativeWorldState.__new__(state_mod.NativeWorldState)
    state.task_dir, state.path, state.data = task_dir, task_dir / "development_state.json", data
    state.seeds = data["identity"]["dev_seeds"]
    state.gate = data["identity"].get("development_gate") or "oracle"
    state.sealed = state.gate != "oracle"
    state.save = lambda: None  # never writes, even to the scratch copy
    protocol.search_control = lambda case, state: None
    protocol.coverage_triage = lambda case, state: None
    protocol.world_use = lambda case: None
    data["stage1_complete"] = False  # assess the cell as it stood at its last check
    result = protocol.check(case, repo, state)
    gate = result.get("coverage_gate") or {}
    return {"ready": result["ready"], "decision_type": result["decision_type"],
            "errors": result["errors"], "terminal_reasons": [r["code"] for r in result["terminal_reasons"]],
            "advisories": [a["code"] for a in result.get("advisories", [])],
            "selected": (result.get("selected") or {}).get("bundle_sha256", "")[:8] or None,
            "coverage_complete": gate.get("complete"), "missing": gate.get("missing"),
            "unsweepable_candidates_old_cap": None}


def unsweepable_under_cap(modules, task_dir: Path) -> list[str]:
    """Tested candidates the OLD cap left with missing seeds it refused to sweep."""
    state_mod, _ = modules
    if not hasattr(state_mod, "SWEEP_ROUNDS"):
        return []
    data = json.loads((task_dir / "development_state.json").read_text())
    state = state_mod.NativeWorldState.__new__(state_mod.NativeWorldState)
    state.task_dir, state.data, state.seeds = task_dir, data, data["identity"]["dev_seeds"]
    state.gate, state.sealed = "oracle", False
    out = []
    for digest in state.candidates():
        gate = state._coverage(digest)
        if gate["missing"] and not gate["payable"] and gate["candidate_tested"]:
            out.append(digest[:8])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip", default="", help="comma-separated cells not to read (still running)")
    parser.add_argument("roots", type=Path, nargs="+")
    args = parser.parse_args()
    sys.path.insert(0, str(SIM.parents[1]))
    sys.path.insert(0, str(SIM))
    old, new = load_modules(args.old), load_modules(SIM)
    report = {}
    for root in args.roots:
        for cell in sorted(p for p in root.iterdir() if (p / "campaign_state.json").is_file()):
            if cell.name in args.skip.split(","):
                report[cell.name] = {"root": str(root), "skipped": "still running; not read"}
                continue
            campaign = json.loads((cell / "campaign_state.json").read_text())
            case = json.loads((cell / "case.json").read_text())
            entry = {"root": str(root), "status": campaign.get("status")}
            blocked = campaign.get("blocked") if isinstance(campaign.get("blocked"), dict) else {}
            entry["blocked_reason_code"] = (blocked.get("detail") or {}).get("reason_code")
            if not glob.glob(str(cell / "outputs/libero_fix_loop/*/*/development_state.json")):
                entry["skipped"] = "no development ledger"
                report[cell.name] = entry
                continue
            with tempfile.TemporaryDirectory() as tmp:
                repo, task_dir = scratch_copy(cell, case, Path(tmp))
                try:
                    entry["old"] = run_check(old, case, repo, task_dir)
                    entry["old"]["unsweepable_candidates_old_cap"] = unsweepable_under_cap(old, task_dir)
                except Exception as exc:  # report, never hide
                    entry["old"] = {"error": f"{type(exc).__name__}: {exc}"}
                try:
                    entry["new"] = run_check(new, case, repo, task_dir)
                except Exception as exc:
                    entry["new"] = {"error": f"{type(exc).__name__}: {exc}"}
            o, n = entry["old"], entry["new"]
            keys = ("ready", "decision_type", "errors", "terminal_reasons")
            entry["changed"] = any(o.get(k) != n.get(k) for k in keys)
            report[cell.name] = entry
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({c: {"status": e.get("status"), "changed": e.get("changed"),
                          "old": (e.get("old") or {}).get("decision_type"),
                          "new": (e.get("new") or {}).get("decision_type")} for c, e in report.items()},
                     indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
