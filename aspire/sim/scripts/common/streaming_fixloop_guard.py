"""Native PreToolUse guard for one SAM2-streaming Fix Loop cell.

Dedicated to profile `sam2-streaming-native-v1`; the legacy native_cc_guard.py is
unchanged. It covers Read, Glob, Grep, Write, Edit, MultiEdit, NotebookEdit and
Bash. Allowed calls return no permission override, so native `dontAsk`
permissions and the tool allowlist still apply.

This is an integrity control over the solver's tool calls, NOT a security
sandbox: a generated program executed by the protocol can still reach whatever
its OS user can. Real isolation comes from the outer deployment (separate
unprivileged user, read-only source runtime, a writable directory per cell).
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_cc_freeze import verify_runtime

TOOLS = "Read|Glob|Grep|Write|Edit|MultiEdit|NotebookEdit|Bash"
PROTOCOL = "streaming_fixloop_protocol.py"
OUTER_MARKER = "OUTER_HELDOUT_AUTHORIZED"
# Solver-authored files at the top of this cell's task directory.
AUTHORED_FILES = ("initial_code.py", "fix_code.py", "initial_streaming_config.json",
                  "fix_streaming_config.json", "diagnostic_session.py", "notes.md",
                  "analysis.md", "task_analysis.md", "findings.md", "BLOCKED.md")
# Solver-writable subtrees of the task directory: scratch analysis and the
# cell-local skills, which are never copied to another cell.
AUTHORED_DIRS = ("analysis", "skills")
BLOCKED_NOTE = re.compile(r"seed_\d+_BLOCKED\.md")
# Not readable by the solver: the high-level strategy library, historical
# solutions, logs and outputs, simulator assets and the LIBERO sources that
# parse success predicates. Matched against resolved paths and Bash text.
# A path component boundary: a resolved absolute path or a relative path in Bash.
_B = r"(?:^|[/\s'\"=:])"
READ_DENY = tuple(_B + p for p in (
    r"\.claude/libero/skills(?:/|$|\s)", r"\.claude/robosuite(?:/|$|\s)",
    r"\.claude/libero/(?:evosearch|zeroshot-transfer|library-size-scaling|"
    r"inference-time-scaling)(?:/|$|\s)", r"skill[-_]snapshots?(?:/|$|\s)",
    r"skill_library", r"outputs(?:/|$|\s)", r"working_codes(?:/|$|\s)",
    r"docs/(?:experiments|logs)(?:/|$|\s)", r"LATEST_EXPERIMENTS\.md",
    r"third_party/LIBERO[^/\s]*(?:/|$|\s)", r"libero_dependencies(?:/|$|\s)",
    r"(?:bddl_files|init_files|init_states)(?:/|$|\s)",
    r"common[-_]initial[-_]states(?:/|$|\s)", r"(?:runtime[-_])?statepack(?:/|$|\s)",
)) + (r"\.(?:bddl|pruned_init|urdf|mjcf|xml)\b", r"initial_state_manifest\.json\b",
      # Coordinator-only cross-cell/held-out result records and deployment state.
      r"EXPERIMENT_RESULTS[^/\s]*\.md", r"preparation-state\.json\b")
BASH_DENY_TEXT = (
    r"\b(?:replay_trial|run_fix_loop_validation|native_cc_recover_trial|native_world_heldout"
    r"|streaming_fixloop_heldout|streaming_fixloop_campaign|streaming_fixloop_review|streaming_fixloop_autonomous"
    r"|native_world_protocol"
    r"|native_cc_protocol|scene_snapshot|prepare_common_initial_states)\.py\b",
)
SOLVER_SUBCOMMANDS = {"status", "check", "snapshot", "trial", "alias-smoke", "select"}
MUTATING = r"\b(?:sed\s+-i|perl\s+-i|chmod|chown|rm|rmdir|mv|cp|tee|ln|truncate|dd|install|touch)\b"
REDIRECT_TARGET = r"(?<![0-9&])>>?\s*([^\s;&|]+)"
BACKGROUND = r"(?<![&>])&(?![&>])"
PROTECTED = (r"(?:^|[\s'\"=])(?:\./)?(?:cap|scripts|env_configs|\.claude|docs)/",
             r"development_state\.json|stage1_result\.json|(?:^|/)attempts/|(?:^|/)development/"
             r"|guard-audit|\.trial\.lock|campaign_state\.json|worker-settings\.json|probe\.json")


def settings(case_path: Path, repo: Path) -> dict:
    command = shlex.join([str(repo / ".venv-libero/bin/python3"),
                          str(repo / "scripts/common/streaming_fixloop_guard.py"),
                          "--case", str(case_path.resolve())])
    return {"hooks": {"PreToolUse": [{"matcher": TOOLS,
                                      "hooks": [{"type": "command", "command": command,
                                                 "timeout": 60}]}]}}


def _paths(case: dict) -> dict:
    control = Path(case["control"]).resolve()
    statepack = case.get("initial_state_manifest")
    supervised = case.get("supervised_repair") or {}
    own = [Path(supervised["review_dir"]).resolve(),
           Path(supervised["prior_input"]["manifest"]).resolve().parent] if supervised else []
    continuation = supervised.get("continuation")
    if continuation:
        own += [Path(continuation["parent"]["ledger"]).resolve().parent.parent]
        own += [Path(item["path"]).resolve().parent for item in continuation["seed_input"].values()]
    return {"control": control, "task": control / "cell", "heldout": control / "heldout",
            "marker": control / OUTER_MARKER, "campaigns": control.parent,
            # The common initial-state registry: outer environment input, never solver data.
            "statepack": Path(statepack).resolve().parent if statepack else None,
            # Supervised study: this cell's own read-only prior inputs and outer reviews.
            "own_inputs": own}


def sibling_denial(path: Path, p: dict, repo: Path | None = None) -> str | None:
    """Another cell's review or prior-input directory next to this cell's own."""
    keep = [p["control"], *p["own_inputs"], *([repo] if repo else [])]
    for mine in p["own_inputs"]:
        if path.is_relative_to(mine.parent) and not path.is_relative_to(mine) and path != mine.parent:
            top = mine.parent / path.relative_to(mine.parent).parts[0]
            if not any(k.is_relative_to(top) for k in keep):
                return "Other cells' reviews and inputs are independent; they are not readable."
    return None


def read_denial(path: Path, case: dict) -> str | None:
    p = _paths(case)
    text = str(path)
    if path == p["marker"] or path.is_relative_to(p["heldout"]) or path.name == OUTER_MARKER:
        return "Held-out evaluation and its artifacts belong to the outer coordinator."
    sibling = sibling_denial(path, p, Path(case["sim"]).resolve() if case.get("sim") else None)
    if sibling:
        return sibling
    if any(path.is_relative_to(mine) for mine in p["own_inputs"]):
        if any(re.search(pattern, text) for pattern in READ_DENY[-2:]):
            return "Coordinator result records are never solver inputs."
        return None  # this cell's own prior inputs and outer reviews: read-only
    if path.is_relative_to(p["campaigns"]) and not path.is_relative_to(p["control"]):
        return "Other cells are independent; their outputs are not readable."
    if any(re.search(pattern, text) for pattern in READ_DENY) or (
            p["statepack"] is not None and path.is_relative_to(p["statepack"])):
        return ("Condition C: no strategy library, historical solution, simulator asset, "
                "initial-state pack or success-predicate source. Use the public API "
                "reference and observations.")
    return None


def write_denial(path: Path, case: dict) -> str | None:
    task = _paths(case)["task"]
    allowed = (path in {task / n for n in AUTHORED_FILES}
               or any(path.is_relative_to(task / d) for d in AUTHORED_DIRS)
               or (path.parent == task / "attempts" and BLOCKED_NOTE.fullmatch(path.name)))
    if not allowed:
        return ("Frozen campaign: write only this cell's policy, streaming configs, notes, "
                "analysis, findings, BLOCKED notes and cell-local skills. Framework, "
                "configuration, ledger and recorded attempt evidence are immutable.")
    return None


def bash_denial(command: str, case: dict, repo: Path) -> str | None:
    p = _paths(case)
    if any(re.search(pattern, command) for pattern in BASH_DENY_TEXT):
        return ("Every simulator call goes through the recorded streaming protocol `trial`; "
                "raw replay, recovery, held-out and campaign entry points are outer-only.")
    if (re.search(OUTER_MARKER, command) or str(p["heldout"]) in command
            or re.search(r"(?:^|[\s/'\"])heldout(?:/|\b)", command)):
        return "Held-out evaluation and its artifacts belong to the outer coordinator."
    if any(re.search(pattern, command) for pattern in READ_DENY) or (
            p["statepack"] is not None and str(p["statepack"]) in command):
        return ("Condition C: no strategy library, historical solution, simulator asset, "
                "initial-state pack or success-predicate source.")
    for other in (p["campaigns"].iterdir() if p["campaigns"].is_dir() else ()):
        if other.resolve() != p["control"] and str(other) in command:
            return "Other cells are independent; their outputs are not readable."
    if PROTOCOL in command:
        if command.count(PROTOCOL) != 1 or re.search(
                r"\b(?:for|while|until|xargs|parallel|nohup|setsid|disown)\b", command) \
                or re.search(BACKGROUND, command):
            return ("Run ONE recorded protocol call per Bash call, in the foreground, then wait "
                    "for it to finish.")
        words = command.split(PROTOCOL, 1)[1].split()
        sub = next((w for w in words if not w.startswith("-") and w != "\\"), None)
        if "--case" in words:
            return "The recorded case is fixed by the environment; do not pass --case."
        if sub not in SOLVER_SUBCOMMANDS:
            return (f"`{sub}` is an outer coordinator step (init, resolve-interrupted and "
                    "finalize are not solver calls).")
    for mine in p["own_inputs"]:
        for word in re.findall(r"/[^\s'\"=:;|&]+", command):
            if sibling_denial(Path(word), p, repo):
                return "Other cells' reviews and inputs are independent; they are not readable."

    def protected(text: str) -> bool:
        return (str(repo) in text or any(re.search(pattern, text) for pattern in PROTECTED)
                or any(str(mine) in text for mine in p["own_inputs"]))
    if (any(re.search(MUTATING, s) and protected(s) for s in re.split(r"[;\n|]|&&", command))
            or any(protected(t) for t in re.findall(REDIRECT_TARGET, command))):
        return "Do not mutate the frozen runtime, ledger or recorded evidence with Bash."
    return None


def denial(payload: dict, case: dict, repo: Path) -> str | None:
    name, values = payload.get("tool_name"), payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or str(repo))
    if name == "Bash":
        return bash_denial(values.get("command", ""), case, repo)
    raw = values.get("file_path") or values.get("notebook_path") or values.get("path")
    if name in {"Glob", "Grep"}:
        pattern = f"/{values.get('pattern', '')} /{values.get('glob', '')}"
        if OUTER_MARKER in pattern or "heldout" in pattern:
            return "Held-out evaluation and its artifacts belong to the outer coordinator."
        if any(re.search(p, pattern) for p in READ_DENY):
            return "Condition C: no strategy library, historical solution or simulator asset."
        root = (cwd / (raw or ".")).resolve()
        statepack = _paths(case)["statepack"]
        if statepack is not None and statepack.is_relative_to(root):
            return "A search over this root would reach the outer initial-state pack."
        return read_denial(root, case)
    if not raw:
        return f"{name} without a path is not allowed in this cell."
    path = (cwd / raw).resolve()
    reason = read_denial(path, case)
    if reason or name == "Read":
        return reason
    return write_denial(path, case)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args()
    try:
        case = json.loads(args.case.read_text())
        repo = Path(case["sim"]).resolve()
        verify_runtime(case, repo)
        payload = json.load(sys.stdin)
        reason = denial(payload, case, repo)
        values = payload.get("tool_input") or {}
        event = {"tool": payload.get("tool_name"),
                 "path": values.get("file_path") or values.get("path"),
                 "command": (values.get("command") or "")[:400] or None,
                 "denied": bool(reason), "reason": reason}
        with (Path(case["control"]) / "guard-audit.jsonl").open("a") as log:
            log.write(json.dumps(event) + "\n")
    except Exception as exc:
        reason = f"Streaming cell guard failed closed: {type(exc).__name__}: {exc}"
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
