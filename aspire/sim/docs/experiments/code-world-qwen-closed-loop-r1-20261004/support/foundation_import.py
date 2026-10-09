"""Import a newly executed, case-pinned diagnostic once; never import old studies."""
from pathlib import Path
import hashlib
import json
import shutil


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(case):
    spec = case["diagnostic_import"]
    root = Path(spec["directory"])
    if root.parent != Path(case["control"]).parent / "coordination":
        raise ValueError("diagnostic source must belong to this study")
    for name, expected in spec["hashes"].items():
        if digest(root / name) != expected:
            raise ValueError("diagnostic evidence changed: " + name)
    summary = json.loads((root / "summary.json").read_text())
    expected = {"state": "passed", "cell": case["id"], "task": case["task"],
                "seed": 51, "charged": True, "task_policy_executed": False, "exit_code": 0}
    if any(summary.get(k) != v for k, v in expected.items()):
        raise ValueError("diagnostic is not this cell's completed preflight")
    resolved = json.loads((root / "resolved-environment.json").read_text())
    if resolved["privileged"] is not False or list(resolved["constructed_apis"]) != ["FrankaLiberoApiReducedSkillLibraryTraced"]:
        raise ValueError("diagnostic was not actual nonprivileged perception")
    return root


def import_diagnostic(case, task_dir):
    from native_world_fixloop_state import NativeWorldState, bundle_identity
    root = verify(case)
    path = Path(task_dir) / "development_state.json"
    state = NativeWorldState(task_dir, json.loads(path.read_text())["identity"], resume=True)
    bundle = {"policy": case["diagnostic_import"]["hashes"]["bundle/code.py"],
              "world": case["diagnostic_import"]["hashes"]["bundle/world_program.py"]}
    bid = bundle_identity(bundle)
    matches = [r for r in state.data["trials"] if r["phase"] == "diagnostic" and r["seed"] == 51 and r["bundle_sha256"] == bid]
    if len(matches) > 1:
        raise ValueError("duplicate diagnostic ledger row")
    other = [r for r in state.data["trials"] if r.get("charged") and r not in matches]
    if other:
        raise ValueError("fresh import refuses a ledger with other charged attempts")
    row = matches[0] if matches else state.begin_trial("diagnostic", 51, bundle, {})
    dest = Path(task_dir) / row["directory"] / "preflight-evidence"
    # Only pinned evidence is made available. No unpinned file can become a
    # hidden input and the diagnostic tape is not a candidate-screening scenario.
    for name, expected in case["diagnostic_import"]["hashes"].items():
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and digest(target) != expected:
            raise ValueError("import destination differs from pinned evidence")
        if not target.exists():
            shutil.copy2(root / name, target)
    if row["status"] == "running":
        state.finish_trial(row, exit_code=0, result={"sandbox_rc": 0, "reward": 0.0,
                           "task_completed": 0, "trial_dir": row["directory"], "session": "diagnostic"})
    if row["status"] != "complete" or not row["charged"]:
        raise ValueError("diagnostic charge/status mismatch")
    row["imported_from"] = case["diagnostic_import"]
    state.save()
    remaining = {str(s): state.budget_remaining(s) for s in state.seeds}
    if remaining != {str(s): 2 if s == 51 else 3 for s in range(51, 66)}:
        raise ValueError("unexpected remaining diagnostic budget")
    return {"imported": True, "directory": row["directory"], "remaining_real_budget": remaining}
