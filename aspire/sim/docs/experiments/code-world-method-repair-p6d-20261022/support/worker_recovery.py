"""CC_TASK_9: recognise an unresumable primary worker and write the driver's handoff.

Native Claude Code 2.1.220 reloads a returned subagent from its on-disk
transcript. Above 5 MiB its loader keeps only the chain of the last
non-sidechain line, and a subagent transcript has none, so the load comes back
empty and `SendMessage` answers `could not be resumed: No transcript found`
although the file is intact (r3 bowldrawerclose / drawer_r2 / goaltask, p5
goaltask). The coordinator may not dispatch a second worker, so without the
outer driver the cell idles until its deadline.

`resume_evidence` reads only the structured native stream of one coordinator
turn. `handoff` is generated here from the protocol check and the cell's own
files: no held-out data, no other cell, no coordinator-written strategy.

p6c (CC_TASK_12, 2026-10-22): `failure_evidence` recognises the other way a
primary is lost (G5). The resume is ACCEPTED ("resumed from transcript in the
background"), and native Claude Code then ends the worker `failed` with an API
error before it does anything: l10 mugmicrowave's worker came back at ~941k of
its 1M window, so every resumed request (936k input + 64k output) was refused
with HTTP 400, 55 resumes over 5.5 h, none of which did anything. Task 9 saw it
only once each refusal had appended ~3 KB and the transcript crossed native's
5 MiB reload limit (turn 64). `handoff` takes the reason so a replacement is
told what actually happened.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

UNRESUMABLE = "could not be resumed"

#: Worker-authored reports a replacement should read first (task dir / attempts).
REPORT_GLOBS = ("findings.md", "task_analysis.md", "CHANGELOG*", "attempts/*.md")
#: The cell's current policy/world programs.
PROGRAMS = ("fix_code.py", "fix_world_program.py", "initial_code.py", "initial_world_program.py")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
    return ""


def _send_result(text: str) -> dict | None:
    """The SendMessage tool result as the native tool reports it: {"success", "message"}."""
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and "success" in payload else None


def resume_evidence(transcript: Path, worker: str) -> dict:
    """Every top-level SendMessage to `worker` in one coordinator turn, and what followed.

    `unresumable` holds when the turn's last SendMessage to the worker failed with
    `could not be resumed`, no later SendMessage to it succeeded, and no subagent
    event (parent_tool_use_id) followed the first such failure: nothing of the
    worker ran after it, so the ledger cannot have advanced after it either.
    """
    calls, sends, after = {}, [], 0
    first_failure = None
    for number, line in enumerate(Path(transcript).read_text(errors="replace").splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("parent_tool_use_id"):
            if first_failure is not None:
                after += 1
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if (event.get("type") == "assistant" and block.get("type") == "tool_use"
                    and block.get("name") == "SendMessage"):
                target = (block.get("input") or {}).get("to") or (block.get("input") or {}).get("recipient")
                if target == worker:
                    calls[block.get("id")] = number
            elif (event.get("type") == "user" and block.get("type") == "tool_result"
                  and block.get("tool_use_id") in calls):
                text = _text(block.get("content"))
                result = _send_result(text)
                failed = bool(result) and result.get("success") is False
                entry = {"call_line": calls[block["tool_use_id"]], "result_line": number,
                         "timestamp": event.get("timestamp"), "success": bool(result and result.get("success")),
                         "unresumable": failed and UNRESUMABLE in str(result.get("message", "")),
                         "message": (str(result.get("message")) if result else text)[:400]}
                sends.append(entry)
                if entry["unresumable"] and first_failure is None:
                    first_failure = entry
    failures = [s for s in sends if s["unresumable"]]
    success_after = bool(first_failure) and any(
        s["success"] and s["result_line"] > first_failure["result_line"] for s in sends)
    return {"transcript": str(transcript), "worker": worker, "sends": sends,
            "failures": len(failures), "first_failure": first_failure,
            "last_send": sends[-1] if sends else None, "success_after_failure": success_after,
            "subagent_events_after_failure": after,
            "unresumable": bool(failures) and not success_after and after == 0
            and bool(sends) and sends[-1]["unresumable"]}


#: The API error text that says the request no longer fits the model's window.
#: Deterministic: the worker's context only grows, so every later resume fails too.
CONTEXT_OVERFLOW = ("maximum context length", "prompt is too long", "context_length_exceeded")
#: Consecutive failures with no worker activity that end a worker whose error is
#: not an overflow (a transient API error gets one more chance first).
IMMEDIATE_FAILURES = 2
END_STATUSES = ("completed", "failed", "killed", "stopped")


def _worker_activity(event: dict) -> bool:
    """A subagent event that is work of the worker, not native's synthetic API-error echo."""
    message = event.get("message") or {}
    return not (event.get("is_api_error_message") or message.get("model") == "<synthetic>")


def failure_evidence(transcript: Path, worker: str, carried: int = 0) -> dict:
    """How the runs of `worker` ended in one coordinator turn (structured events only).

    A run starts at native's `task_started` for the worker (dispatch or accepted
    resume) and ends at its `task_updated`/`task_notification` status. Activity is
    every subagent event (parent_tool_use_id) except native's synthetic API-error
    message. The worker is `dead` when its LAST run of the turn ended `failed` with
    an API error and either the error is a context-window overflow (deterministic:
    the context only grows) or the trailing runs failed without any activity
    `IMMEDIATE_FAILURES` times in a row, counting `carried` such failures from
    earlier turns (a transient API error is given one more resume first).
    """
    runs, current = [], None
    for number, line in enumerate(Path(transcript).read_text(errors="replace").splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("parent_tool_use_id"):
            if current is not None and _worker_activity(event):
                current["activity"] += 1
            continue
        if event.get("type") != "system" or event.get("task_id") != worker:
            continue
        subtype = event.get("subtype")
        if subtype == "task_started":
            current = {"start_line": number, "activity": 0}
        elif subtype in ("task_updated", "task_notification") and current is not None:
            status = ((event.get("patch") or {}).get("status") if subtype == "task_updated"
                      else event.get("status"))
            if status not in END_STATUSES:
                continue
            error = str(((event.get("patch") or {}).get("error") if subtype == "task_updated"
                         else event.get("summary")) or "")
            current.update(end_line=number, status=status, error=error[:600],
                           api_error=status == "failed" and "API error" in error,
                           overflow=status == "failed" and any(k in error for k in CONTEXT_OVERFLOW))
            runs.append(current)
            current = None
    last = runs[-1] if runs else None
    trailing = 0
    for run in reversed(runs):
        if run["status"] != "failed" or run["activity"]:
            break
        trailing += 1
    if last and last["status"] == "failed" and not last["activity"] and len(runs) == trailing:
        trailing += carried  # the whole turn failed without activity: continue the count
    dead = bool(current is None and last and last["status"] == "failed" and last["api_error"]
                and (last["overflow"] or trailing >= IMMEDIATE_FAILURES))
    return {"transcript": str(transcript), "worker": worker, "runs": runs,
            "last_run": last, "still_running": current is not None,
            "immediate_failures": trailing, "carried": carried,
            "reason": ("context_overflow" if dead and last["overflow"] else
                       "repeated_api_failure" if dead else None),
            "dead": dead}


def running_rows(ledger: Path) -> list[str]:
    if not ledger.is_file():
        return []
    return [r["directory"] for r in json.loads(ledger.read_text())["trials"] if r.get("status") == "running"]


def ledger_digest(ledger: Path) -> str | None:
    return hashlib.sha256(ledger.read_bytes()).hexdigest() if ledger.is_file() else None


def _file(path: Path, task_dir: Path) -> dict:
    data = path.read_bytes()
    return {"path": str(path), "relative": str(path.relative_to(task_dir)), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


def compact_check(result: dict) -> dict:
    """The fields of `check` a replacement needs; the full check is one `check` away."""
    result = result or {}
    progress = result.get("progress") or {}
    latest = progress.get("latest_trial") or {}
    return {
        "ready": result.get("ready"), "decision_type": result.get("decision_type"),
        "terminal_reasons": result.get("terminal_reasons"),
        "errors": [str(e)[:500] for e in (result.get("errors") or [])[:20]],
        "legal_actions": result.get("legal_actions"),
        "development_budget": result.get("development_budget"),
        "selected": {k: (result.get("selected") or {}).get(k) for k in ("bundle_sha256", "reason")},
        "progress": {
            "replay_invocations": progress.get("replay_invocations"),
            "graded_executions": progress.get("graded_executions"),
            "seeds_passing": progress.get("seeds_passing"),
            "infrastructure_errors": progress.get("infrastructure_errors"),
            "unresolved_screening_blockers": len(progress.get("unresolved_screening_blockers") or []),
            "tested_bundles": {digest: [len(row.get("passes") or []), row.get("crashes"), row.get("trials")]
                               for digest, row in (progress.get("tested_bundles") or {}).items()},
            "latest_trial": {k: latest.get(k) for k in ("phase", "seed", "attempt", "status", "directory")},
        },
    }


#: What the replacement is told about its predecessor, by recovery reason.
LOSS = {
    "unresumable_worker": ("returned, and native Claude Code can no longer resume it: `SendMessage`\n"
                           "answered \"could not be resumed\" (its native transcript can no longer be\n"
                           "loaded)."),
    "worker_api_failure": ("can no longer work: native Claude Code accepts a resume, but every run of\n"
                           "it ends at once with an API error before it can act (for a context\n"
                           "overflow: its conversation no longer fits the model's context window, so\n"
                           "any further request is refused)."),
}


def handoff(*, task_dir: Path, check: dict, generation: int, previous_worker: str,
            remaining_seconds: float, turn: int,
            reason: str = "unresumable_worker") -> tuple[str, dict]:
    """Markdown appended to the frozen assignment for generation `generation`, and its index."""
    task_dir = Path(task_dir)
    ledger = task_dir / "development_state.json"
    rows = json.loads(ledger.read_text())["trials"] if ledger.is_file() else []
    reports = sorted({p for pattern in REPORT_GLOBS for p in task_dir.glob(pattern) if p.is_file()})
    programs = [task_dir / name for name in PROGRAMS if (task_dir / name).is_file()]
    index = {"generation": generation, "previous_worker": previous_worker, "turn": turn,
             "reason": reason,
             "ledger": _file(ledger, task_dir) if ledger.is_file() else None,
             "ledger_rows": len(rows),
             "attempts_spent": sum(bool(r.get("spends_retry")) for r in rows),
             "reports": [_file(p, task_dir) for p in reports],
             "programs": [_file(p, task_dir) for p in programs],
             "recent_rows": [{k: r.get(k) for k in ("phase", "seed", "attempt", "status", "directory")}
                             for r in rows[-12:]],
             "check": compact_check((check or {}).get("result"))}
    listing = "\n".join(f"- `{item['path']}` ({item['bytes']} bytes, sha256 {item['sha256'][:16]})"
                        for item in index["reports"] + index["programs"]) or "- (none)"
    recent = "\n".join(f"- {r['directory']}: {r['status']}" for r in index["recent_rows"]) or "- (none)"
    text = f"""

---

## Outer-driver worker recovery handoff (generation {generation})

The outer driver wrote this section; the coordinator did not. It continues the
SAME development run of this cell, which a previous primary worker
(`{previous_worker}`, generation {generation - 1}) carried out. That worker
{LOSS[reason]} You are its replacement, nothing more.

Nothing is reset. The development ledger, every spent attempt, the budget
phase, versions used, versions per failure mode, blocked modes and the original
cumulative development deadline are exactly what the ledger and the check below
say. No attempt is refunded or added. Do NOT run `init` and do NOT take another
snapshot: this cell already has its ledger ({len(rows)} rows, {index['attempts_spent']}
retry-spending attempts) and its single snapshot. Start by running `status` and
`check` yourself, then read the files listed below before changing anything.
About {int(remaining_seconds // 60)} minutes of the original development wall clock
remain (an infrastructure watchdog, not an action limit).

Ledger: `{ledger}`
Development trial directories: `{task_dir / 'development'}`

The previous worker's own reports and the cell's current programs:
{listing}

Most recent ledger rows:
{recent}

Outer check after coordinator turn {turn} (compact; `check` prints all of it):
```json
{json.dumps(index['check'], indent=1, sort_keys=True)}
```
"""
    return text, index
