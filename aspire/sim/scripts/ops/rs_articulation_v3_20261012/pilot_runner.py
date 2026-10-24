#!/usr/bin/env python3
"""Outer adapter and serial supervisor for the user-requested RS v3 short pilot.

User requirement (2026-10-12, explicit override of the full-50 protocol for
these three cells only): run drawer -> stove -> wine serially. In each cell the
initial program is first tried on development seeds 51, 52, 53. If none of them
produces an actual success (status `complete`, `sandbox_rc` 0 and the outer
oracle's `task_completed`), the cell stops early, keeps every charged attempt and
deadline, and is left for overall replanning; the next independent cell starts.
If one of them succeeds, the ordinary development workflow continues on seeds
51-65 with the original three TOTAL charged attempts per seed, normal selection
and finalize, and then the frozen bundle is evaluated on held-out seeds 1, 2, 3
only. Seeds 4-50 stay visibly `not_run`; the official report keeps denominator
50 and `incomplete`. This is a short pilot, never a complete 50-seed benchmark.

Nothing in the frozen runtime is edited. This file is deployed root-owned and
read-only OUTSIDE the runtime, imports the existing modules from the case's own
frozen `sim`, and adds only these clearly recorded, scoped in-process adapters:

* `write_prompts`: the original prompts, plus one generic task-neutral pilot
  override appended to BOTH the worker and coordinator prompt; the diff and
  hashes are written next to the session's prompts.
* `worker_settings`: the original settings and guard, plus one extra PreToolUse
  Bash hook (`gate-hook` below) that operationally enforces the screen.
* `run_native_cc`: the original transport, given an `on_start` that records the
  native process group and starts a watcher. After the screen is decided as
  no-pass and no trial is running, the watcher stops ONLY that group.
* held-out: `streaming_fixloop_heldout.evaluate_seed` is wrapped for the duration
  of one outer run so the sweep stops before seed 4 starts. HELDOUT_SEEDS, the
  ledger identity, freeze, classification and report are the existing ones.

World, control, solver, ledger, scoring and isolation logic are not patched.
The campaign itself runs with `heldout=False`; its normal `campaign_state.json`
status is kept, and the pilot outcome is a separate receipt under
`<control>/pilot/`. Modes:

  preflight   pure checks for one case, run as the cell user; writes nothing
  cell        one cell's pilot, run as its own unprivileged user (runuser)
  gate-hook   the extra PreToolUse Bash hook (stdin payload, exit 2 = deny)
  heldout     outer three-seed held-out run (invoked by `cell` after solver exit)
  supervise   root serial supervisor over the declared queue (run inside tmux)
  start       root: launch `supervise` in a detached tmux session
  status      root: print the supervisor status

This is an integrity/operations layer, not a security sandbox.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import fcntl
import hashlib
import json
import os
import pwd
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ADAPTER = Path(__file__).resolve()
PROTOCOL_SCRIPT = "streaming_fixloop_protocol.py"
SCREEN_SEEDS = (51, 52, 53)
DEV_SEEDS = tuple(range(51, 66))
RETRY_LIMIT = 3
HELDOUT_PILOT_SEEDS = (1, 2, 3)
HELDOUT_SEEDS = tuple(range(1, 51))
GRADED_PHASES = ("smoke", "initial", "repair")
RETRY_PHASES = ("smoke", "initial", "repair", "diagnostic")
# Same set as native_world_fixloop_state.RESOLVED_STATUSES (asserted by the tests).
SETTLED = frozenset({"complete", "world_program_error", "diagnostic_program_error"})
HELDOUT_FINAL = ("success", "failure", "crash", "infrastructure_error")
STOP_DECISIONS = ("no_pass", "missing_evidence")
PERMITS_NEXT = ("early_stopped", "heldout_pilot_complete")
HELDOUT_STATUSES = ("stage1_finalized", "complete", "heldout_incomplete", "heldout_refused")
QUEUE_ORDER = ("drawer", "stove", "wine")
# Same names as streaming_fixloop_protocol.TASKS (asserted by the tests); the root
# supervisor never imports the runtime.
TASKS = {"drawer": "open_the_middle_drawer_of_the_cabinet", "stove": "turn_on_the_stove",
         "wine": "put_the_wine_bottle_on_the_rack"}
CELL_PYTHON = "/mnt/workspace/aspire-world-envs/libero/bin/python"
RUNUSER = "/usr/sbin/runuser"
INHERIT_KEEP = re.compile(r"^(?:LANG|LANGUAGE|LC_\w+|TZ|TERM)$")
NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
PILOT_STOP_REASON = ("user-requested three-seed held-out pilot: stopped before seed 4; "
                     "seeds 4-50 are not_run by design")
PILOT_MARK = "## User-requested short pilot (outer operator override)"
PILOT_OVERRIDE = f"""
---

{PILOT_MARK}

This cell is a user-requested short pilot, not a complete 50-seed benchmark.
Every rule above is unchanged (allowed APIs, ledger, three TOTAL charged
attempts per seed, oracle gate, initial batch before repair, deadlines,
held-out boundary) except for this additional outer screen:

1. After the observation-only scene snapshot (and an optional seed-51 smoke),
   run the initial program on development seeds 51, 52 and 53, in that order,
   one recorded call at a time. No other development seed is admitted until all
   three have a settled recorded result.
2. Only a recorded execution with status `complete`, `sandbox_rc` 0 and the
   outer oracle's `task_completed` true is a success. Command receipts, World
   defer decisions and your own assessment are not.
3. If none of seeds 51-53 succeeds, the screen is closed: an outer hook refuses
   every further program execution and the outer coordinator ends this session.
   Do not retry, repair or work around it; summarize what you observed and stop.
4. If at least one of them succeeds, continue the ordinary full development
   workflow above on seeds 51-65 with the same per-seed budgets.

Every admitted attempt stays charged. Held-out evaluation remains outer-only.
"""
PILOT_SETTINGS = {
    "kind": "user-requested short pilot; not a complete 50-seed benchmark",
    "requested_on": "2026-10-12",
    "cells_order": list(QUEUE_ORDER), "concurrency": 1,
    "screen_seeds": list(SCREEN_SEEDS), "screen_order_enforced": True,
    "success_definition": {"status": "complete", "sandbox_rc": 0, "task_completed": True},
    "not_success": ["command receipts", "world defer", "model self-evaluation",
                    "sandbox_rc != 0 even when the oracle reported completion"],
    "development_seeds": list(DEV_SEEDS), "charged_attempts_per_seed_total": RETRY_LIMIT,
    "heldout_requested_seeds": list(HELDOUT_PILOT_SEEDS),
    "heldout_denominator_kept": len(HELDOUT_SEEDS),
    "share_code_feedback_or_skills_between_cells": False,
}


class Refused(RuntimeError):
    """A precondition failed; nothing was started."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode())


def file_sha256(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def digest(value) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def adapter_sha256() -> str:
    return file_sha256(ADAPTER)


def atomic_json(path: Path, value, mode: int | None = None) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    if mode is not None:
        temporary.chmod(mode)
    temporary.replace(path)


def append_jsonl(path: Path, value: dict) -> None:
    """Append-only receipts: one fsynced line per event, never rewritten."""
    with Path(path).open("a") as handle:
        handle.write(json.dumps({"at": now_iso(), **value}, ensure_ascii=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def proc_start_ticks(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def process_alive(pid: int | None, ticks: str | None) -> bool:
    """Alive AND the same process (start time), so a reused PID never matches."""
    if not pid:
        return False
    current = proc_start_ticks(int(pid))
    return current is not None and (ticks is None or current == ticks)


# --- the screen: pure functions over the development ledger -------------------------


def passed(record: dict) -> bool:
    """StreamingFixLoopState.passed: complete, sandbox_rc 0 and the oracle label."""
    return (record.get("status") == "complete" and record.get("sandbox_rc") == 0
            and bool(record.get("task_completed")))


def screen(data: dict | None) -> dict:
    """Decide the first-three screen from settled ledger evidence only.

    A seed is `tried` once it has settled initial evidence (or spent its budget
    on graded executions such as crashing smokes), `missing` when its budget is
    spent with no graded result at all (infrastructure/diagnostic only), and
    `open` otherwise, including while any of its attempts is still running. The
    screen is decided only when no screen seed is open: `pass` if a graded
    execution on 51-53 actually passed, else `no_pass`, or `missing_evidence`
    when a seed never produced graded evidence. A decision is never inferred
    from a command receipt, a model report or an in-flight attempt.
    """
    trials = (data or {}).get("trials", [])
    seeds = {}
    for seed in SCREEN_SEEDS:
        rows = [r for r in trials if r.get("seed") == seed]
        charged = sum(1 for r in rows if r.get("spends_retry"))
        graded = [r for r in rows if r.get("phase") in GRADED_PHASES and r.get("status") in SETTLED
                  and r.get("executed", True) and not r.get("alias_of")]
        initial = any(r.get("phase") == "initial" and r.get("status") in SETTLED for r in rows)
        running = [r["directory"] for r in rows if r.get("status") == "running"]
        exhausted = charged >= RETRY_LIMIT
        state = ("tried" if initial or (exhausted and graded and not running) else
                 "missing" if exhausted and not graded and not running else "open")
        seeds[str(seed)] = {
            "state": state, "charged_attempts": charged, "settled_initial": initial,
            "graded_executions": len(graded), "passed": any(passed(r) for r in graded),
            "passing_directories": [r["directory"] for r in graded if passed(r)],
            "running": running,
            "infrastructure_rows": [r["directory"] for r in rows
                                    if r.get("status") not in SETTLED and r.get("status") != "running"],
            "program_crashes": [r["directory"] for r in graded if r.get("sandbox_rc") not in (0, None)],
        }
    passing = [int(s) for s, v in seeds.items() if v["passed"]]
    if any(v["state"] == "open" for v in seeds.values()):
        decision = "undecided"
    elif passing:
        decision = "pass"
    elif any(v["state"] == "missing" for v in seeds.values()):
        decision = "missing_evidence"
    else:
        decision = "no_pass"
    beyond = [r for r in trials if r.get("seed") not in SCREEN_SEEDS and r.get("phase") in RETRY_PHASES]
    if decision == "pass":
        # Audit for a hook bypass: nothing beyond 51-53 may have started before the
        # screen was decided (the last screen seed settled).
        decided_at = screen_decided_at(trials)
        early = [r for r in beyond if decided_at is not None
                 and r.get("started_at") is not None and r["started_at"] < decided_at]
    else:
        early = beyond
    return {"decision": decision, "screen_seeds": list(SCREEN_SEEDS), "passing_seeds": passing,
            "seeds": seeds,
            "running_anywhere": [r["directory"] for r in trials if r.get("status") == "running"],
            "executions_beyond_screen": [r["directory"] for r in beyond],
            "screen_violations": [r["directory"] for r in early]}


def screen_decided_at(trials: list[dict]) -> float | None:
    """When the last screen seed stopped being open (first settled initial, else its last row)."""
    times = []
    for seed in SCREEN_SEEDS:
        rows = [r for r in trials if r.get("seed") == seed and r.get("finished_at") is not None]
        initial = [r["finished_at"] for r in rows if r.get("phase") == "initial"
                   and r.get("status") in SETTLED]
        seed_time = min(initial) if initial else max((r["finished_at"] for r in rows), default=None)
        if seed_time is None:
            return None
        times.append(seed_time)
    return max(times)


def read_ledger(task_dir: Path) -> dict | None:
    return read_json(Path(task_dir) / "development_state.json")


# Known uncharged outer preparation in a not-yet-launched control: the cell's own
# native config dir and the authorized prelaunch route probe. Kept, never deleted.
PREP_ENTRY = re.compile(r"^(?:claude-config|prelaunch-route-probe-\d{8})$")


def control_kind(control: Path) -> str:
    """fresh | prep_only (known prep entries only) | pilot (bound) | unknown."""
    entries = [p.name for p in Path(control).iterdir()]
    if not entries:
        return "fresh"
    if (Path(control) / "pilot/pilot_identity.json").is_file():
        return "pilot"
    if all(PREP_ENTRY.match(name) for name in entries):
        return "prep_only"
    return "unknown"


SHELL_PUNCTUATION = set("();<>|&")
# A plain path ending in the script: no variables, globs, `-c` code or command text.
LITERAL_SCRIPT = re.compile(r"[\w./+-]*/?" + re.escape(PROTOCOL_SCRIPT))


def shell_words(command: str) -> list[str]:
    """POSIX words with shell operators split out; quotes are removed as the shell would."""
    lexer = shlex.shlex(command.replace("\\\n", " "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _flag(word: str, flag: str) -> str | None:
    """argparse also accepts unambiguous prefixes (`--se 54`) and `--flag=value`."""
    name, _, value = word.partition("=")
    if len(name) >= 3 and flag.startswith(name):
        return value if "=" in word else ""
    return None


def protocol_call(command: str) -> dict | None:
    """Subcommand, phase and literal seed of one recorded protocol call, or None.

    The canonical call (quoted or bare interpreter/script paths, `\\`-continued
    lines, `$TASK_DIR` in --code, trailing `2>&1 | tail`) parses; anything that
    names the protocol but cannot be read literally fails closed.
    """
    if PROTOCOL_SCRIPT not in command:
        return None
    try:
        tokens = shell_words(command)
    except ValueError as exc:
        return {"sub": None, "error": f"unparseable protocol call: {exc}"}
    scripts = [i for i, t in enumerate(tokens) if PROTOCOL_SCRIPT in t]
    if len(scripts) != 1 or not LITERAL_SCRIPT.fullmatch(tokens[scripts[0]]):
        return {"sub": None, "error": "name the protocol script once, as a literal path "
                                      "(no shell variables) followed by its arguments"}
    words = []
    for token in tokens[scripts[0] + 1:]:
        if token and set(token) <= SHELL_PUNCTUATION:
            break
        words.append(token)
    sub = next((w for w in words if not w.startswith("-")), None)
    found: dict[str, list[str]] = {"--phase": [], "--seed": []}
    for i, word in enumerate(words):
        for flag in found:
            value = _flag(word, flag) if word.startswith("--") else None
            if value == "":
                found[flag].append(words[i + 1] if i + 1 < len(words) else "")
            elif value is not None:
                found[flag].append(value)
    call = {"sub": sub, "phase": None, "seed": None}
    if any(len(v) > 1 for v in found.values()):
        call["error"] = "repeat --phase/--seed only once"
    elif found["--seed"]:
        if not re.fullmatch(r"\d+", found["--seed"][0]):
            call["error"] = "the pilot screen needs a literal integer --seed (no shell variables)"
        else:
            call["seed"] = int(found["--seed"][0])
    if found["--phase"] and not call.get("error"):
        call["phase"] = found["--phase"][0]
    if sub == "trial" and not call.get("error") and (call["seed"] is None or call["phase"] is None):
        call["error"] = "a recorded trial needs literal --phase and --seed"
    return call


CLOSED = ("Pilot screen closed: development seeds 51, 52 and 53 are settled (or out of budget) "
          "and none produced an actual success (status complete, sandbox_rc 0, oracle "
          "task_completed). "
          "Per the user's pilot override no further program execution is admitted in this "
          "cell. Do not retry, repair or work around this; summarize and end the session.")


def admission_denial(command: str, data: dict | None, protected: tuple[str, ...] = ()) -> str | None:
    """The extra hook's decision for one Bash command. None admits it."""
    if any(p and p in command for p in protected):
        return "The pilot adapter, its receipts and the operator directory are outer-only."
    gate = screen(data)
    if gate["decision"] == "pass":
        return None  # Ordinary development: only the original guard and ledger apply.
    call = protocol_call(command)
    if gate["decision"] in STOP_DECISIONS:
        if call and call["sub"] in ("status", "check") and not call.get("error"):
            return None
        return CLOSED
    if call is not None and call.get("error"):
        return f"Pilot screen: {call['error']}."  # Unreadable or dynamic: fail closed.
    if call is None or call["sub"] in ("status", "check", "select", "snapshot", "alias-smoke"):
        return None  # Non-executing, or the uncharged once-only snapshot.
    if call["sub"] != "trial":
        return None  # Outer-only subcommands are refused by the original guard.
    if call.get("error"):
        return f"Pilot screen: {call['error']}."
    seed, phase = call["seed"], call["phase"]
    if seed not in SCREEN_SEEDS:
        return (f"Pilot screen: seed {seed} is not admitted until development seeds 51, 52 and "
                "53 all have settled results and at least one actually succeeded.")
    if phase == "repair":
        return ("Pilot screen: the initial program runs on seeds 51, 52, 53 first; repairs "
                "follow the original initial-batch rule only after the screen passes.")
    if phase == "initial":
        earlier = [s for s in SCREEN_SEEDS if s < seed and gate["seeds"][str(s)]["state"] == "open"]
        if earlier:
            return (f"Pilot screen: run the initial program in order 51, 52, 53; seed "
                    f"{earlier[0]} has no settled result yet.")
    return None


# --- identity, receipts, outcome --------------------------------------------------------


def pilot_identity(case_path: Path, case: dict) -> dict:
    return {"kind": PILOT_SETTINGS["kind"], "cell": case["id"], "case": str(case_path),
            "case_sha256": file_sha256(case_path), "adapter_path": str(ADAPTER),
            "adapter_sha256": adapter_sha256(), "settings": PILOT_SETTINGS,
            "settings_sha256": digest(PILOT_SETTINGS),
            "prompt_override_sha256": sha256_text(PILOT_OVERRIDE)}


BOUND_KEYS = ("cell", "case_sha256", "adapter_sha256", "settings_sha256", "prompt_override_sha256")


def bind_identity(pilot: Path, identity: dict, *, resume: bool) -> dict:
    """First launch records the adapter and pilot settings; a resume must match them."""
    path = pilot / "pilot_identity.json"
    if path.is_file():
        if not resume:
            raise Refused("this cell already has a pilot identity; resume explicitly")
        bound = json.loads(path.read_text())
        changed = [k for k in BOUND_KEYS if bound.get(k) != identity[k]]
        if changed:
            raise Refused(f"resume requires the same adapter, case and pilot settings: {changed}")
        return bound
    if resume and (pilot.parent / "cell/development_state.json").exists():
        raise Refused("ledger exists without a pilot identity; not adopting a non-pilot cell")
    pilot.mkdir(mode=0o700, exist_ok=True)
    bound = {**identity, "bound_at": now_iso()}
    with path.open("x") as handle:
        json.dump(bound, handle, indent=2)
        handle.write("\n")
    return bound


def ensure_bound(case: dict, case_path: Path) -> dict:
    pilot = Path(case["control"]).resolve() / "pilot"
    bound = read_json(pilot / "pilot_identity.json")
    if not bound:
        raise Refused("no pilot identity is bound for this cell")
    identity = pilot_identity(case_path, case)
    changed = [k for k in BOUND_KEYS if bound.get(k) != identity[k]]
    if changed:
        raise Refused(f"pilot adapter or settings differ from the bound identity: {changed}")
    return bound


def record_gate(pilot: Path, gate: dict, source: str) -> None:
    receipts = pilot / "operator_stop.jsonl"
    if gate["decision"] == "undecided" or any(
            r.get("event") == "gate_decided" for r in read_jsonl(receipts)):
        return
    append_jsonl(receipts, {"event": "gate_decided", "decision": gate["decision"],
                            "passing_seeds": gate["passing_seeds"], "seeds": gate["seeds"],
                            "observed_by": source})


def classify(status: str | None, gate: dict | None, heldout: dict | None,
             refused: str | None = None) -> dict:
    """Map the cell's evidence to the operator outcome; only two permit the next cell."""
    decision = (gate or {}).get("decision", "undecided")
    if refused:
        outcome, reason = "halted_refused", refused
    elif gate and gate["running_anywhere"]:
        outcome, reason = "halted_unresolved_running", f"running attempts {gate['running_anywhere']}"
    elif gate and gate.get("screen_violations"):
        outcome, reason = ("halted_screen_violation",
                           f"executed beyond 51-53 before the screen passed: {gate['screen_violations']}")
    elif decision == "no_pass":
        outcome, reason = "early_stopped", "51-53 tried; no actual success"
    elif decision == "missing_evidence":
        outcome, reason = ("halted_screen_missing_evidence",
                           "a screen seed spent its budget without graded evidence (infrastructure)")
    elif decision == "undecided":
        outcome, reason = "halted_screen_incomplete", f"campaign status {status}"
    elif status not in HELDOUT_STATUSES:
        outcome, reason = "halted_development_incomplete", f"campaign status {status}"
    elif not heldout:
        outcome, reason = "halted_heldout_not_run", f"campaign status {status}"
    elif not heldout.get("pilot_complete"):
        outcome, reason = "halted_heldout_incomplete", str(heldout.get("stop_reason"))
    elif heldout.get("requested_infrastructure_error_seeds"):
        outcome, reason = ("halted_heldout_infrastructure",
                           f"seeds {heldout['requested_infrastructure_error_seeds']}")
    else:
        outcome, reason = "heldout_pilot_complete", "three requested held-out seeds evaluated"
    return {"outcome": outcome, "permits_next_cell": outcome in PERMITS_NEXT, "reason": reason,
            "expected_operator_outcome": outcome in PERMITS_NEXT}


# --- in-process adapters --------------------------------------------------------------


def runtime_modules(sim: Path) -> SimpleNamespace:
    """The existing launcher, protocol, ledger and held-out modules of THIS frozen runtime."""
    sim = Path(sim).resolve()
    for extra in (sim.parents[1], sim / "scripts/common", sim / "scripts/libero"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    import native_world_fixloop_state as ledger
    import streaming_fixloop_campaign as campaign
    import streaming_fixloop_heldout as heldout
    import streaming_fixloop_protocol as sp
    for module in (campaign, sp, heldout, ledger):
        if not Path(module.__file__).resolve().is_relative_to(sim):
            raise Refused(f"{module.__name__} came from {module.__file__}, not the case runtime {sim}")
    return SimpleNamespace(sim=sim, campaign=campaign, sp=sp, heldout=heldout, ledger=ledger)


def gate_hook_entry(repo: Path, case_path: Path) -> dict:
    command = shlex.join([str(Path(repo) / ".venv-libero/bin/python3"), "-B", str(ADAPTER),
                          "--case", str(Path(case_path).resolve()), "gate-hook"])
    return {"matcher": "Bash", "hooks": [{"type": "command", "command": command, "timeout": 60}]}


def with_gate_hook(settings: dict, repo: Path, case_path: Path) -> dict:
    out = json.loads(json.dumps(settings))
    out.setdefault("hooks", {}).setdefault("PreToolUse", []).append(gate_hook_entry(repo, case_path))
    return out


def append_override(prompt_path: Path, coordinator_path: Path, directory: Path) -> dict:
    record = {"override_sha256": sha256_text(PILOT_OVERRIDE), "files": {}, "at": now_iso()}
    diffs = []
    for name, path in (("worker", Path(prompt_path)), ("coordinator", Path(coordinator_path))):
        before = path.read_text()
        if PILOT_MARK in before:
            raise RuntimeError(f"{path} already carries the pilot override")
        after = before.rstrip("\n") + "\n" + PILOT_OVERRIDE
        path.write_text(after)
        record["files"][name] = {"path": str(path), "before_sha256": sha256_text(before),
                                 "after_sha256": sha256_text(after)}
        diffs.append("".join(difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True),
            fromfile=f"{name} prompt (launcher render)", tofile=f"{name} prompt (+ pilot override)")))
    diff_path = Path(directory) / "pilot-prompt-override.diff"
    diff_path.write_text("".join(diffs))
    record.update(diff=str(diff_path), diff_sha256=file_sha256(diff_path))
    atomic_json(Path(directory) / "pilot-prompt-override.json", record)
    return record


def trial_lock_held(task_dir: Path) -> bool:
    path = Path(task_dir) / ".trial.lock"
    if not path.is_file():
        return False
    with path.open("r") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
    return False


class NativeStopWatcher(threading.Thread):
    """Stop ONE native session's process group once the screen closed and nothing runs.

    The group is the one `run_native_cc` started (start_new_session, so the
    session leader's pid is the pgid), handed over by `on_start`. Nothing is
    looked up by name, no other process is signalled and no trial is
    interrupted: a running ledger row or a held trial lock postpones the stop.
    """

    def __init__(self, process, task_dir: Path, pilot: Path, *, poll: float = 15.0,
                 grace: float = 60.0, confirmations: int = 2):
        super().__init__(daemon=True, name="pilot-gate-watcher")
        self.process, self.task_dir, self.pilot = process, Path(task_dir), Path(pilot)
        self.poll, self.grace = poll, grace
        self.confirmations, self.quiet = confirmations, 0
        self.ticks = proc_start_ticks(process.pid)
        self.done = threading.Event()
        self.result: dict | None = None

    def run(self) -> None:
        while not self.done.wait(self.poll):
            if self.check_once() != "watching":
                return

    def finish(self) -> None:
        self.done.set()

    def check_once(self) -> str:
        if self.process.poll() is not None:
            return "exited"
        gate = screen(read_ledger(self.task_dir))
        if gate["decision"] == "pass":
            record_gate(self.pilot, gate, "watcher")
            return "pass"
        if gate["decision"] not in STOP_DECISIONS:
            return "watching"
        record_gate(self.pilot, gate, "watcher")
        if gate["running_anywhere"] or trial_lock_held(self.task_dir):
            self.quiet = 0
            return "watching"  # Never interrupt a trial; it settles first.
        # Two consecutive quiet polls: a protocol call admitted just before the
        # screen closed has taken its lock / running row by the second look.
        self.quiet += 1
        if self.quiet < self.confirmations:
            return "watching"
        self.result = self.stop(gate)
        return "stopped"

    def stop(self, gate: dict) -> dict:
        pid = self.process.pid
        receipts = self.pilot / "operator_stop.jsonl"
        if (self.process.poll() is not None or proc_start_ticks(pid) != self.ticks
                or os.getpgid(pid) != pid):
            append_jsonl(receipts, {"event": "native_stop_skipped", "pid": pid,
                                    "reason": "session leader already gone or not its own group"})
            return {"signalled": False}
        append_jsonl(receipts, {"event": "native_stop_requested", "pid": pid, "pgid": pid,
                                "start_ticks": self.ticks, "signal": "SIGTERM",
                                "decision": gate["decision"],
                                "reason": "user pilot screen closed; no trial running"})
        os.killpg(pid, signal.SIGTERM)
        escalated = not self._wait(self.grace)
        if escalated and self.process.poll() is None and proc_start_ticks(pid) == self.ticks:
            os.killpg(pid, signal.SIGKILL)
            self._wait(30.0)
        result = {"signalled": True, "escalated_to_sigkill": escalated,
                  "returncode": self.process.poll()}
        append_jsonl(receipts, {"event": "native_stop_result", "pid": pid, **result})
        return result

    def _wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                return True
            time.sleep(0.05)
        return self.process.poll() is not None


@contextlib.contextmanager
def pilot_adapters(rt, pilot: Path, task_dir: Path, *, poll: float, grace: float):
    """Scoped wrappers on the launcher's prompt, settings and transport functions."""
    camp = rt.campaign
    names = ("write_prompts", "worker_settings", "run_native_cc")
    original = {name: getattr(camp, name) for name in names}

    def write_prompts(case, repo, control):
        prompt_path, coordinator_path = original["write_prompts"](case, repo, control)
        append_override(prompt_path, coordinator_path, control)
        return prompt_path, coordinator_path

    def worker_settings(case, repo, case_path):
        return with_gate_hook(original["worker_settings"](case, repo, case_path), repo, case_path)

    def run_native_cc(command, **kwargs):
        if kwargs.get("on_start") is not None:
            raise RuntimeError("the launcher already set on_start; refusing to replace it")
        box = {}

        def on_start(process):
            append_jsonl(pilot / "native_sessions.jsonl", {
                "event": "native_session_started", "pid": process.pid, "pgid": os.getpgid(process.pid),
                "start_ticks": proc_start_ticks(process.pid), "stdout": str(kwargs.get("stdout_path"))})
            box["watcher"] = NativeStopWatcher(process, task_dir, pilot, poll=poll, grace=grace)
            box["watcher"].start()

        try:
            return original["run_native_cc"](command, on_start=on_start, **kwargs)
        finally:
            watcher = box.get("watcher")
            if watcher is not None:
                watcher.finish()
                watcher.join(timeout=grace + 60)
                append_jsonl(pilot / "native_sessions.jsonl", {
                    "event": "native_session_ended", "pid": watcher.process.pid,
                    "returncode": watcher.process.poll(), "stopped_by_watcher": watcher.result})

    for name, function in (("write_prompts", write_prompts), ("worker_settings", worker_settings),
                           ("run_native_cc", run_native_cc)):
        setattr(camp, name, function)
    try:
        yield
    finally:
        for name, function in original.items():
            setattr(camp, name, function)


# --- held-out: three requested seeds of the existing runner -----------------------------


def heldout_receipt(case: dict, result: dict, evaluation: Path) -> dict:
    per_seed = result["per_seed"]
    requested = {str(s): per_seed[str(s)] for s in HELDOUT_PILOT_SEEDS}
    beyond = [s for s in HELDOUT_SEEDS if s not in HELDOUT_PILOT_SEEDS and per_seed[str(s)] != "not_run"]
    complete = (all(v in HELDOUT_FINAL for v in requested.values()) and not beyond
                and not result["unresolved_running_seeds"])
    report = evaluation / "heldout_result.json"
    identity = result["identity"]
    return {
        "schema_version": 1, "cell": case["id"],
        "kind": "user-requested three-seed held-out pilot; NOT a complete 50-seed benchmark",
        "requested_evaluation_seeds": list(HELDOUT_PILOT_SEEDS), "pilot_complete": complete,
        "requested_seed_status": requested,
        "requested_counts": {s: sum(v == s for v in requested.values()) for s in HELDOUT_FINAL},
        "requested_infrastructure_error_seeds": [int(s) for s, v in requested.items()
                                                 if v == "infrastructure_error"],
        "seeds_beyond_pilot_executed": beyond,
        "official_report": str(report), "official_report_sha256": file_sha256(report),
        "official_incomplete": result["incomplete"],
        "official_success_rate_denominator": result["success_rate_denominator"],
        "official_counts": result["counts"], "not_run_seeds": result["not_run_seeds"],
        "stop_reason": result["stop_reason"], "deadline_expired": result["deadline_expired"],
        "phase_clock": result["phase_clock"],
        "evaluation_seed_universe": identity["seeds"],
        "frozen_bundle_sha256": identity["bundle_sha256"],
        "frozen_manifest_sha256": identity["frozen_manifest_sha256"],
        "heldout_runner_sha256": identity["runner_sha256"],
        "adapter": {"path": str(ADAPTER), "sha256": adapter_sha256(),
                    "scoped_wrapper": "streaming_fixloop_heldout.evaluate_seed (stop before seed 4)"},
        "pilot_settings_sha256": digest(PILOT_SETTINGS), "written_at": now_iso()}


def run_heldout_pilot(rt, case_path: Path, *, resume: bool, resolve: int | None = None,
                      clock=time.time) -> tuple[dict, dict]:
    sh = rt.heldout
    case, _, _ = rt.sp.load_case(case_path)
    ensure_bound(case, Path(case_path).resolve())
    original = sh.evaluate_seed

    def evaluate_seed(case, repo, task_dir, evaluation, ledger, bundle, kept, seed):
        if seed not in HELDOUT_PILOT_SEEDS:
            raise sh.SweepStopped(PILOT_STOP_REASON)  # Before anything for this seed starts.
        return original(case, repo, task_dir, evaluation, ledger, bundle, kept, seed)

    sh.evaluate_seed = evaluate_seed
    try:
        result = sh.run(Path(case_path), resume=resume, resolve=resolve, clock=clock)
    finally:
        sh.evaluate_seed = original
    evaluation = Path(case["control"]).resolve() / "heldout"
    receipt = heldout_receipt(case, result, evaluation)
    atomic_json(evaluation / "pilot_receipt.json", receipt)
    append_jsonl(evaluation / "pilot_receipts.jsonl", {
        k: receipt[k] for k in ("pilot_complete", "requested_seed_status", "stop_reason",
                                "official_report_sha256", "frozen_bundle_sha256")})
    return result, receipt


def heldout_main(rt, case_path: Path, *, resume: bool, resolve: int | None,
                 clock=time.time) -> int:
    sh = rt.heldout
    try:
        result, receipt = run_heldout_pilot(rt, case_path, resume=resume, resolve=resolve, clock=clock)
    except (sh.ProtocolError, sh.RuntimeChanged, Refused, OSError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps({**{k: result[k] for k in (
        "cell", "completed", "incomplete", "stop_reason", "counts", "success_rate",
        "not_run_seeds", "unresolved_running_seeds", "infrastructure_error_seeds")},
        "pilot_complete": receipt["pilot_complete"],
        "requested_evaluation_seeds": receipt["requested_evaluation_seeds"]}, indent=2))
    # Exactly the original runner's exit semantics (incomplete -> 1).
    return int(result["incomplete"] or bool(result["infrastructure_error_seeds"]))


def ensure_solver_exited(pilot: Path) -> None:
    for row in read_jsonl(pilot / "native_sessions.jsonl"):
        if row.get("event") == "native_session_started" and process_alive(row["pid"], row.get("start_ticks")):
            raise Refused(f"native session {row['pid']} is still alive; held-out waits for solver exit")


def heldout_stage(rt, case: dict, case_path: Path, repo: Path, control: Path, pilot: Path,
                  record: dict, clock) -> dict | None:
    camp = rt.campaign
    receipt = read_json(control / "heldout/pilot_receipt.json")
    if receipt and receipt.get("pilot_complete") and record["status"] != "stage1_finalized":
        return receipt  # Already evaluated; nothing is re-run.
    ensure_solver_exited(pilot)
    authorization = control / "outer_heldout_authorization.json"
    if not authorization.exists():
        atomic_json(authorization, {"created_by": "rs_v3 pilot_runner (outer adapter)", "at": clock(),
                                    "cell": case["id"], "solver_exited": True,
                                    "requested_evaluation_seeds": list(HELDOUT_PILOT_SEEDS)})
    marker = control / camp.guard.OUTER_MARKER
    if not marker.exists():
        marker.write_text(case["id"] + "\n")
    resume = (control / "heldout/heldout_state.json").is_file()
    earlier = [p for p in control.glob("outer_heldout*.json") if p != authorization]
    name = "heldout" if not earlier else f"heldout-resume-{len(earlier) + 1:02d}"
    step = camp.outer_step(case_path, repo, camp.native_env(case, repo, case_path), control, name,
                           str(ADAPTER), "heldout", *(["--resume"] if resume else []))
    if record.get("heldout"):
        record.setdefault("heldout_history", []).append(record["heldout"])
    record["heldout"] = step
    # The launcher's own mapping of the held-out exit code.
    record["status"] = {0: "complete", 1: "heldout_incomplete"}.get(step["exit_code"], "heldout_refused")
    camp.finish(record, control / "campaign_state.json")
    return read_json(control / "heldout/pilot_receipt.json")


# --- one cell ---------------------------------------------------------------------------


def write_outcome(pilot: Path, payload: dict) -> dict:
    atomic_json(pilot / "pilot_outcome.json", payload)
    append_jsonl(pilot / "pilot_events.jsonl", {"event": "outcome", **{
        k: payload.get(k) for k in ("outcome", "permits_next_cell", "reason", "campaign_status",
                                    "launch_id")}})
    return payload


def run_cell(rt, case_path: Path, *, resume: bool, launch_id: str | None = None,
             clock=time.time, poll: float = 15.0, grace: float = 60.0) -> int:
    case_path = Path(case_path).resolve()
    camp = rt.campaign
    case, repo, task_dir = camp.load(case_path)
    control = Path(case["control"]).resolve()
    pilot = control / "pilot"
    kind = control_kind(control)
    if kind == "unknown":
        raise Refused(f"control {control} holds artifacts that are neither known prelaunch prep "
                      f"nor a bound pilot: {sorted(p.name for p in control.iterdir())}; "
                      "it is never adopted or reinitialized")
    if kind == "pilot" and not resume:
        raise Refused(f"control {control} already has a pilot; inspect it, then use --resume")
    prep = sorted(p.name for p in control.iterdir()) if kind == "prep_only" else []
    bound = bind_identity(pilot, pilot_identity(case_path, case), resume=resume)
    previous = read_json(pilot / "pilot_outcome.json")
    if previous and previous.get("permits_next_cell"):
        # Early-stopped or completed: never re-entered; only re-confirmed for this launch.
        write_outcome(pilot, {**previous, "launch_id": launch_id, "reconfirmed_at": now_iso()})
        print(json.dumps({"cell": case["id"], "already": previous["outcome"]}))
        return 0
    append_jsonl(pilot / "pilot_events.jsonl", {"event": "cell_start", "resume": resume,
                                                "launch_id": launch_id, "control_kind": kind,
                                                "preserved_prep_entries": prep,
                                                "adapter_sha256": bound["adapter_sha256"]})
    state_path = control / "campaign_state.json"
    state = read_json(state_path) or {}
    gate = screen(read_ledger(task_dir)) if read_ledger(task_dir) else None
    refused = error = None
    if state.get("status") not in HELDOUT_STATUSES and not (gate and gate["decision"] in STOP_DECISIONS):
        try:
            with pilot_adapters(rt, pilot, task_dir, poll=poll, grace=grace):
                camp.run(case_path, resume=read_ledger(task_dir) is not None, heldout=False, clock=clock)
        except camp.LaunchRefused as exc:
            refused = str(exc)
        except Exception as exc:  # Recorded and halted; the campaign state stays as left.
            error = f"{type(exc).__name__}: {exc}"
    state = read_json(state_path) or {}
    ledger = read_ledger(task_dir)
    gate = screen(ledger) if ledger else None
    if gate:
        record_gate(pilot, gate, "cell")
    heldout = None
    if not (refused or error) and state.get("status") in HELDOUT_STATUSES \
            and gate and gate["decision"] == "pass" and not gate["running_anywhere"]:
        try:
            heldout = heldout_stage(rt, case, case_path, repo, control, pilot, state, clock)
        except Refused as exc:
            refused = str(exc)
        state = read_json(state_path) or {}
    verdict = classify(state.get("status"), gate, heldout, refused or error)
    if verdict["outcome"] == "early_stopped" and not any(
            r.get("event") == "early_stopped" for r in read_jsonl(pilot / "operator_stop.jsonl")):
        append_jsonl(pilot / "operator_stop.jsonl", {
            "event": "early_stopped", "expected_operator_outcome": True,
            "campaign_status": state.get("status"), "available_for_replanning": True,
            "charged_attempts_per_seed": {s: v["charged_attempts"] for s, v in gate["seeds"].items()},
            "development_deadline": ((ledger or {}).get("phase_clock") or {}).get("development")})
    write_outcome(pilot, {
        **verdict, "cell": case["id"], "launch_id": launch_id, "campaign_status": state.get("status"),
        "campaign_state": str(state_path), "gate": gate, "heldout_receipt": heldout,
        "adapter_sha256": bound["adapter_sha256"], "settings_sha256": bound["settings_sha256"],
        "not_a_complete_benchmark": True, "written_at": now_iso()})
    print(json.dumps({"cell": case["id"], **verdict}, indent=2))
    return 0 if verdict["permits_next_cell"] else 3


# --- the extra PreToolUse Bash hook ------------------------------------------------------


def gate_hook(case_path: Path, stdin) -> int:
    reason, event = None, {}
    try:
        case = json.loads(Path(case_path).read_text())
        control = Path(case["control"]).resolve()
        pilot = control / "pilot"
        ensure_bound(case, Path(case_path).resolve())
        payload = json.load(stdin)
        if payload.get("tool_name") == "Bash":
            command = (payload.get("tool_input") or {}).get("command", "")
            data = read_ledger(control / "cell")
            reason = admission_denial(command, data, (str(pilot), str(ADAPTER.parent)))
            event = {"command": command[:400], "decision": screen(data)["decision"],
                     "denied": bool(reason), "reason": reason}
            append_jsonl(pilot / "gate-audit.jsonl", event)
    except Exception as exc:
        reason = f"Pilot gate hook failed closed: {type(exc).__name__}: {exc}"
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


# --- pure preflight -------------------------------------------------------------------------


def model_endpoint(case: dict) -> dict:
    url = case["model_service"]["endpoint"].rstrip("/") + "/v1/models"
    with urllib.request.urlopen(url, timeout=10) as response:
        models = json.loads(response.read().decode())
    rows = [m for m in models.get("data", []) if m.get("id") == case["model"]]
    if not rows or rows[0].get("max_model_len") != case["context_tokens"]:
        raise RuntimeError(f"{url} does not serve {case['model']} with max_model_len "
                           f"{case['context_tokens']}: {[(m.get('id'), m.get('max_model_len')) for m in models.get('data', [])]}")
    return {"url": url, "id": rows[0]["id"], "max_model_len": rows[0]["max_model_len"]}


def numba_cache_check(value: str | None, repo: Path, interpreter: Path) -> dict:
    """Pure check: an absolute cache outside the frozen trees whose nearest existing
    ancestor this user can write (numba creates the directory on first use)."""
    if not value or not Path(value).is_absolute():
        raise Refused("NUMBA_CACHE_DIR must be an absolute cell-user cache (the supervisor sets it)")
    path = Path(value)
    for frozen in (Path(repo).resolve().parents[1], interpreter.resolve().parents[1]):
        if path.resolve().is_relative_to(frozen):
            raise Refused(f"NUMBA_CACHE_DIR {path} lies inside the immutable tree {frozen}")
    existing = next(p for p in (path, *path.parents) if p.exists())
    if not os.access(existing, os.W_OK | os.X_OK):
        raise Refused(f"NUMBA_CACHE_DIR {path}: {existing} is not writable by this user")
    return {"path": str(path), "exists": path.exists(), "writable_ancestor": str(existing)}


def control_state(control: Path, case: dict, case_path: Path) -> dict:
    entries = sorted(p.name for p in control.iterdir()) if control.is_dir() else None
    bound = read_json(control / "pilot/pilot_identity.json")
    ledger = read_ledger(control / "cell")
    identity = pilot_identity(case_path, case)
    return {"entries": entries, "fresh": entries == [],
            "kind": control_kind(control) if control.is_dir() else None,
            "campaign_status": (read_json(control / "campaign_state.json") or {}).get("status"),
            "pilot_identity_matches": None if not bound else all(
                bound.get(k) == identity[k] for k in BOUND_KEYS),
            "pilot_outcome": (read_json(control / "pilot/pilot_outcome.json") or {}).get("outcome"),
            "gate": screen(ledger)["decision"] if ledger else None}


def preflight(rt, case_path: Path) -> dict:
    camp, sp = rt.campaign, rt.sp
    case_path = Path(case_path).resolve()
    report = {"case": str(case_path), "adapter": {"path": str(ADAPTER), "sha256": adapter_sha256()},
              "pilot_settings_sha256": digest(PILOT_SETTINGS), "writes": "none", "checks": {}}
    checks = report["checks"]
    try:
        case, repo, _ = camp.load(case_path)
        checks["user"] = camp.runtime_user()
        checks["isolation"] = camp.isolation(case, repo)
        checks["native_binary"] = camp.native_binary(case)
        checks["task"] = camp.task_metadata(case)
        checks["runtime_manifest_sha256"] = camp.verify_runtime(case, repo)
        checks["model_service_manifest"] = sp.model_service_identity(case)
        checks["perception_manifest"] = sp.perception_identity(case)
        checks["perception"] = camp.perception_ready(case)
        checks["model_endpoint"] = model_endpoint(case)
        hook_python = repo / ".venv-libero/bin/python3"
        if not hook_python.exists():
            raise Refused(f"runtime interpreter for hooks/outer steps is missing: {hook_python}")
        checks["numba_cache"] = numba_cache_check(os.environ.get("NUMBA_CACHE_DIR"), repo,
                                                  Path(sys.executable))
        checks["control"] = control_state(Path(case["control"]).resolve(), case, case_path)
        report["ok"] = (checks["control"]["pilot_identity_matches"] is not False
                        and checks["control"]["kind"] != "unknown")
        if not report["ok"]:
            report["error"] = ("control holds unrecognized artifacts or a pilot identity that "
                               "differs from this adapter/settings")
    except Exception as exc:  # A report, not an action: every failure is shown.
        report.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    return report


# --- root supervisor -------------------------------------------------------------------------


class Host:
    """Process and account boundary of the root supervisor (faked in CPU tests)."""

    environ = os.environ

    def geteuid(self) -> int:
        return os.geteuid()

    def getpwnam(self, name: str):
        return pwd.getpwnam(name)

    def stat(self, path: Path):
        return os.stat(path, follow_symlinks=False)

    def run(self, argv: list[str], **kwargs):
        return subprocess.run(argv, **kwargs)

    def popen(self, argv: list[str], **kwargs):
        return subprocess.Popen(argv, **kwargs)

    def start_ticks(self, pid: int) -> str | None:
        return proc_start_ticks(pid)

    def alive(self, pid, ticks) -> bool:
        return process_alive(pid, ticks)


def _writable_by_others(mode: int) -> bool:
    return bool(mode & 0o022)


def deployment_errors(host: Host, adapter: Path, operator_dir: Path, queue: dict,
                      queue_path: Path) -> list[str]:
    errors = []
    adapter, operator_dir = Path(adapter), Path(operator_dir)
    group = host.stat(queue_path.parent).st_gid
    if not adapter.is_relative_to(operator_dir):
        errors.append(f"adapter {adapter} must be deployed inside the operator directory")
    # The cell users (shared group) read the adapter and traverse to it; nobody else writes.
    for path, need in ((adapter, 0o040), (adapter.parent, 0o050), (operator_dir, 0o050)):
        st = host.stat(path)
        if st.st_uid != 0 or _writable_by_others(st.st_mode):
            errors.append(f"{path} must be root-owned and not group/other writable")
        if st.st_gid != group or st.st_mode & need != need:
            errors.append(f"{path} must be group-readable by the cell group {group}")
    sim = Path(queue["sim"])
    runs = {Path(c["control"]).parent for c in queue.get("cells", [])}
    for root in (sim.parents[1], *runs):
        if operator_dir.is_relative_to(root) or root.is_relative_to(operator_dir):
            errors.append(f"operator directory {operator_dir} overlaps {root}")
    return errors


def validate_queue(host: Host, queue_path: Path) -> dict:
    queue_path = Path(queue_path)
    if not queue_path.is_absolute():
        raise Refused("the queue path must be absolute")
    queue = json.loads(queue_path.read_text())
    errors = []
    if tuple(queue.get("order", ())) != QUEUE_ORDER:
        errors.append(f"queue order must be {list(QUEUE_ORDER)}")
    if queue.get("concurrency") != 1:
        errors.append("queue concurrency must be 1")
    if queue.get("full_comparison") is not False or queue.get("dlc_allocation") is not False:
        errors.append("this queue is a pilot subset without DLC allocation")
    if queue.get("no_generated_code_feedback_or_skills_shared") is not True:
        errors.append("the queue must declare that nothing is shared between cells")
    cells = queue.get("cells") or []
    if [c.get("task") for c in cells] != list(QUEUE_ORDER):
        errors.append("queue cells must follow the declared order")
    users, uids, controls, parents = set(), set(), set(), set()
    for cell in cells:
        task = cell.get("task")
        label = f"cell {cell.get('cell')}"
        case_path, control = Path(cell.get("case", "")), Path(cell.get("control", ""))
        if case_path.parent != queue_path.parent or case_path.name != f"{task}.case.json":
            errors.append(f"{label}: case must be {queue_path.parent}/{task}.case.json")
            continue
        case = json.loads(case_path.read_text())
        if case.get("id") != cell.get("cell") or not str(cell.get("cell", "")).startswith(f"{task}_"):
            errors.append(f"{label}: case id {case.get('id')} differs")
        if case.get("task") != TASKS.get(task):
            errors.append(f"{label}: case task {case.get('task')} is not {TASKS.get(task)}")
        if case.get("control") != str(control) or case.get("sim") != queue.get("sim"):
            errors.append(f"{label}: case control/sim differ from the queue")
        if case.get("runtime_manifest_sha256") != queue.get("runtime_manifest_sha256"):
            errors.append(f"{label}: runtime manifest differs from the queue")
        if not Path(case.get("claude_config_dir", "/")).is_relative_to(control):
            errors.append(f"{label}: claude_config_dir must be under its control")
        user = cell.get("user", "")
        if not NAME.match(user):
            errors.append(f"{label}: invalid user name {user!r}")
            continue
        try:
            entry = host.getpwnam(user)
        except KeyError:
            errors.append(f"{label}: user {user} does not exist")
            continue
        if entry.pw_uid != cell.get("uid") or entry.pw_uid < 1000:
            errors.append(f"{label}: {user} has uid {entry.pw_uid}, queue says {cell.get('uid')}")
        if entry.pw_dir != cell.get("home"):
            errors.append(f"{label}: passwd HOME {entry.pw_dir} differs from the queue")
        if not control.is_absolute() or Path(os.path.realpath(control)) != control or not control.is_dir():
            errors.append(f"{label}: control {control} must be an existing absolute real directory")
        else:
            st = host.stat(control)
            if st.st_uid != entry.pw_uid or _writable_by_others(st.st_mode):
                errors.append(f"{label}: control must belong to {user} only")
        if control.is_relative_to(Path(queue.get("sim", "/nonexistent")).parents[1]):
            errors.append(f"{label}: control lies inside the runtime")
        users.add(user)
        uids.add(cell.get("uid"))
        controls.add(str(control))
        parents.add(str(control.parent))
    if len(users) != len(cells) or len(uids) != len(cells) or len(controls) != len(cells):
        errors.append("each cell needs its own user, uid and control")
    if len(parents) > 1:
        errors.append("all controls belong to one campaign directory")
    if errors:
        raise Refused("; ".join(errors))
    return queue


def numba_cache_dir(passwd_home: str) -> str:
    """The cell user's own writable cache, below its validated passwd home.

    The LIBERO env and runtime are immutable, so numba's default cache locator
    (next to the imported robosuite sources) fails; nothing is written there.
    """
    return str(Path(passwd_home) / ".cache/aspire-numba")


def cell_env(case: dict, environ, passwd_home: str) -> dict:
    """runuser sets HOME/SHELL/USER/LOGNAME from passwd; they are never assigned here."""
    env = {k: v for k, v in environ.items() if INHERIT_KEEP.match(k)}
    manifest = read_json(Path(case["model_service"]["server_manifest"])) or {}
    env.update(PATH="/usr/local/bin:/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1",
               NUMBA_CACHE_DIR=numba_cache_dir(passwd_home),
               TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD="1", LIBERO_CONFIG_PATH=str(case["libero_config_path"]),
               PYTHONPATH=str(Path(case["sim"]).parents[1]), HF_HUB_OFFLINE="1",
               TRANSFORMERS_OFFLINE="1", GIT_TERMINAL_PROMPT="0")
    library = (manifest.get("env") or {}).get("LD_LIBRARY_PATH")
    if library:
        env["LD_LIBRARY_PATH"] = library
    return env


def as_cell_user(cell: dict, adapter: Path, *args: str) -> list[str]:
    return [RUNUSER, "-u", cell["user"], "--", CELL_PYTHON, "-B", str(adapter),
            "--case", cell["case"], *args]


@contextlib.contextmanager
def supervisor_lock(directory: Path):
    path = directory / "supervisor.lock"
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = read_json(directory / "supervisor.json") or {}
            raise Refused(f"another live supervisor holds {path}: {holder.get('pid')}") from exc
        yield handle


def supervise(queue_path: Path, operator_dir: Path, *, resume: bool, host: Host | None = None,
              adapter: Path = ADAPTER) -> int:
    host = host or Host()
    if host.geteuid() != 0:
        raise Refused("the serial supervisor runs as root (it launches each cell via runuser)")
    operator_dir = Path(operator_dir).resolve()
    directory = operator_dir / "supervisor"
    directory.mkdir(mode=0o700, exist_ok=True)
    with supervisor_lock(directory):
        queue = validate_queue(host, Path(queue_path))
        errors = deployment_errors(host, adapter, operator_dir, queue, Path(queue_path))
        if errors:
            raise Refused("; ".join(errors))
        identity = {"queue": str(queue_path), "queue_sha256": file_sha256(queue_path),
                    "adapter": str(adapter), "adapter_sha256": file_sha256(adapter),
                    "settings_sha256": digest(PILOT_SETTINGS)}
        status_path = directory / "status.json"
        status = read_json(status_path)
        if status and not resume:
            raise Refused(f"{status_path} exists; continue it with --resume")
        if status and any(status["identity"][k] != identity[k] for k in identity):
            raise Refused("resume requires the same queue, adapter and pilot settings")
        if not status:
            status = {"identity": identity, "kind": PILOT_SETTINGS["kind"], "created_at": now_iso(),
                      "cells": {c["cell"]: {"task": c["task"], "state": "pending", "attempts": []}
                                for c in queue["cells"]}, "runs": []}
        atomic_json(directory / "supervisor.json", {
            "pid": os.getpid(), "start_ticks": proc_start_ticks(os.getpid()), "argv": sys.argv,
            "started_at": now_iso(), "resume": resume, **identity}, mode=0o600)
        status["runs"].append({"pid": os.getpid(), "started_at": now_iso(), "resume": resume})

        def save(state: str, reason: str | None = None) -> None:
            status.update(state=state, reason=reason, updated_at=now_iso())
            atomic_json(status_path, status, mode=0o600)

        save("running")
        for cell in queue["cells"]:
            entry = status["cells"][cell["cell"]]
            if entry["state"] == "permitted":
                continue
            last = entry["attempts"][-1] if entry["attempts"] else {}
            if host.alive(last.get("pid"), last.get("start_ticks")):
                save("halted", f"{cell['cell']}: an earlier cell process {last['pid']} is still alive")
                return 1
            control = Path(cell["control"])
            kind = control_kind(control)
            if kind == "unknown":
                entry["state"] = "halted"
                save("halted", f"{cell['cell']}: control holds unrecognized artifacts; not adopted")
                return 1
            if kind == "pilot" and not resume:
                entry["state"] = "halted"
                save("halted", f"{cell['cell']}: a launched pilot requires an explicit --resume")
                return 1
            populated = kind == "pilot"
            case = json.loads(Path(cell["case"]).read_text())
            env = cell_env(case, host.environ, host.getpwnam(cell["user"]).pw_dir)
            proc = host.run(as_cell_user(cell, adapter, "preflight"), env=env, cwd=queue["sim"],
                            capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
            pre = {"exit_code": proc.returncode, "stdout_tail": proc.stdout[-6000:],
                   "stderr_tail": proc.stderr[-2000:], "at": now_iso()}
            entry.setdefault("preflights", []).append(pre)
            if proc.returncode:
                entry["state"] = "halted"
                save("halted", f"{cell['cell']}: preflight failed")
                return 1
            logs = directory / "logs" / cell["cell"]
            logs.mkdir(mode=0o700, parents=True, exist_ok=True)
            index = len(entry["attempts"]) + 1
            launch_id = uuid.uuid4().hex
            argv = as_cell_user(cell, adapter, "cell", "--launch-id", launch_id,
                                *(["--resume"] if populated else []))
            with (logs / f"{index:02d}.stdout.log").open("x") as out, \
                    (logs / f"{index:02d}.stderr.log").open("x") as err:
                child = host.popen(argv, env=env, cwd=queue["sim"], stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
                entry["attempts"].append({
                    "launch_id": launch_id, "pid": child.pid, "start_ticks": host.start_ticks(child.pid),
                    "argv": argv, "resume": populated, "started_at": now_iso(),
                    "stdout": str(out.name), "stderr": str(err.name)})
                entry["state"] = "running"
                save("running", f"{cell['cell']} running")
                code = child.wait()
            outcome = read_json(control / "pilot/pilot_outcome.json") or {}
            entry["attempts"][-1].update(exit_code=code, finished_at=now_iso(),
                                         outcome=outcome.get("outcome"))
            if (outcome.get("launch_id") != launch_id
                    or outcome.get("adapter_sha256") != identity["adapter_sha256"]):
                entry["state"] = "halted"
                save("halted", f"{cell['cell']}: exit {code} without a matching pilot outcome")
                return 1
            entry["outcome"] = {k: outcome.get(k) for k in (
                "outcome", "permits_next_cell", "reason", "campaign_status")}
            if code != 0 or not outcome.get("permits_next_cell"):
                entry["state"] = "halted"
                save("halted", f"{cell['cell']}: {outcome.get('outcome')} ({outcome.get('reason')})")
                return 1
            entry["state"] = "permitted"
            save("running", f"{cell['cell']} {outcome['outcome']}; next independent cell")
        save("finished", "all cells reached an expected pilot outcome")
        return 0


def start_tmux(queue_path: Path, operator_dir: Path, *, resume: bool, session: str,
               host: Host | None = None) -> dict:
    host = host or Host()
    if host.geteuid() != 0:
        raise Refused("start runs as root")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", session):
        raise Refused("invalid tmux session name")
    directory = Path(operator_dir).resolve() / "supervisor"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with supervisor_lock(directory):
        pass  # Refuses here when a supervisor is live.
    argv = [sys.executable, "-B", str(ADAPTER), "supervise", "--queue", str(queue_path),
            "--operator-dir", str(Path(operator_dir).resolve()), *(["--resume"] if resume else [])]
    command = ["tmux", "new-session", "-d", "-s", session, shlex.join(argv)]
    host.run(command, check=True)
    return {"tmux_session": session, "argv": argv, "status": str(directory / "status.json")}


# --- CLI ----------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["preflight", "cell", "gate-hook", "heldout", "supervise",
                                         "start", "status"])
    parser.add_argument("--case", type=Path)
    parser.add_argument("--resume", action="store_true",
                        help="explicit resume: same ledger, deadlines, adapter and settings")
    parser.add_argument("--resolve-interrupted", type=int, metavar="SEED",
                        help="heldout: settle a recorded in-flight seed from evidence (no replay)")
    parser.add_argument("--launch-id")
    parser.add_argument("--queue", type=Path)
    parser.add_argument("--operator-dir", type=Path)
    parser.add_argument("--tmux-session", default="rs-v3-pilot")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    args = parser.parse_args(argv)
    try:
        if args.mode in ("supervise", "start", "status"):
            if not args.operator_dir:
                parser.error("--operator-dir is required")
            if args.mode == "status":
                print(json.dumps(read_json(args.operator_dir / "supervisor/status.json"), indent=2))
                return 0
            if not args.queue:
                parser.error("--queue is required")
            if args.mode == "start":
                print(json.dumps(start_tmux(args.queue, args.operator_dir, resume=args.resume,
                                            session=args.tmux_session)))
                return 0
            return supervise(args.queue, args.operator_dir, resume=args.resume)
        if not args.case:
            parser.error("--case is required")
        if args.mode == "gate-hook":
            return gate_hook(args.case, sys.stdin)
        rt = runtime_modules(Path(json.loads(args.case.read_text())["sim"]))
        if args.mode == "preflight":
            report = preflight(rt, args.case)
            print(json.dumps(report, indent=2, default=str))
            return 0 if report.get("ok") else 1
        if args.mode == "heldout":
            return heldout_main(rt, args.case, resume=args.resume, resolve=args.resolve_interrupted)
        return run_cell(rt, args.case, resume=args.resume, launch_id=args.launch_id,
                        poll=args.poll_seconds)
    except Refused as exc:
        print(json.dumps({"error": str(exc), "type": "Refused"}))
        return 2
    except Exception as exc:  # Launcher refusals, protocol errors: shown, never a pilot outcome.
        import traceback
        traceback.print_exc()
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
