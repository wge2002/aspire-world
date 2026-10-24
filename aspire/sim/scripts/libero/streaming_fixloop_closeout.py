"""Opt-in round closeout for the supervised repair rounds (runner side, no LLM client).

Enabled only by `supervised_repair.closeout` (schema `aspire-round-closeout-v1`,
validated in streaming_fixloop_rounds). A round then has two separate states:

* trials complete: all 15 seeds have their one resolved charged attempt of the
  round (the ledger refuses any further attempt of the round);
* closeout complete: this module's `verify` finds the round's own reports,
  exhausted-seed notes and selection in order, and the tested evidence of every
  round so far is unchanged since the trials completed.

The round session may keep writing after its last trial for at most
`report_grace_seconds` (instead of the plain boundary grace). When its closeout is
not complete, the campaign runs at most `max_sessions` restricted native
sessions of the same cell, each bounded by `session_timeout_seconds` and the
unchanged development deadline. Their input is this cell's own development
evidence; their output is reports, blocker notes, a copy of an already tested
bundle of this study with its own oracle success, and `select`. They run no
trial: the ledger refuses every further attempt of a complete round, and the
watcher stops a session whose ledger gains a running or charged row. Nothing
here refunds, re-opens a round, extends a clock or relaxes check/finalize.
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path

import streaming_fixloop_protocol as sp
from native_world_fixloop_state import bundle_identity, code_hash, report_errors

rounds = sp.rounds
PROTOCOL = "scripts/libero/streaming_fixloop_protocol.py"
FINDINGS_SECTIONS = ("Root causes observed", "What fixed them", "Generalizable patterns", "Blocked seeds")
BLOCKED_SECTIONS = ("Root Cause", "Details", "What Was Tried")


def config(case: dict) -> dict | None:
    return rounds.closeout_config(case)


def charged(data: dict) -> int:
    return sum(1 for r in data["trials"] if r.get("spends_retry"))


def boundary(data: dict, task_dir: Path, round_: int) -> dict:
    """Tested evidence at the moment the round's trials completed."""
    return {"charged_attempts": charged(data), "rows": len(data["trials"]),
            "evidence_sha256": {str(k): rounds.round_evidence(data, task_dir, k)["evidence_sha256"]
                                for k in range(1, round_ + 1)}}


def _sha(path: Path) -> str | None:
    return sp.file_sha256(path) if path.is_file() else None


def disk_bundle(data: dict, task_dir: Path) -> tuple[str | None, str | None]:
    """Identity of fix_code.py + fix_streaming_config.json, computed like `select`."""
    policy, config_ = task_dir / "fix_code.py", task_dir / "fix_streaming_config.json"
    if not policy.is_file() or not config_.is_file():
        return None, "fix_code.py and fix_streaming_config.json must both exist"
    text = config_.read_text()
    problems = sp.streaming_config_errors(text, data["identity"]["profile"])
    if problems:
        return None, "fix_streaming_config.json: " + "; ".join(problems)
    digest, _ = sp.world_digest(data["identity"], code_hash(text))
    return bundle_identity({"policy": code_hash(policy.read_text()), "world": digest}), None


def verify(data: dict, status: dict, task_dir: Path, round_: int, start: dict,
           previous_reports: dict | None) -> dict:
    """Is round `round_` closed out? `status` is the protocol's own `status` output.

    `errors` are fixable by a restricted closeout session; `violations` mean the
    tested evidence or the charge count changed and are never fixable.
    """
    errors, violations = [], []
    if not rounds.round_complete(data, round_):
        errors.append(f"round {round_} trials are not complete")
    now = boundary(data, task_dir, round_)
    if now["charged_attempts"] != start["charged_attempts"]:
        violations.append(f"charged attempts changed during closeout: {start['charged_attempts']} -> "
                          f"{now['charged_attempts']}")
    elif now["rows"] != start["rows"]:
        violations.append(f"ledger rows changed during closeout: {start['rows']} -> {now['rows']}")
    changed = [k for k, v in start["evidence_sha256"].items() if now["evidence_sha256"].get(k) != v]
    if changed:
        violations.append(f"tested evidence of rounds {changed} changed after the trials completed")
    analysis, findings = task_dir / "task_analysis.md", task_dir / "findings.md"
    reports = {"task_analysis.md": _sha(analysis), "findings.md": _sha(findings)}
    if not analysis.is_file() or not analysis.read_text().strip():
        errors.append("task_analysis.md is missing or empty")
    errors += report_errors(findings, FINDINGS_SECTIONS)
    for name, sha in reports.items():
        if previous_reports and sha is not None and previous_reports.get(name) == sha:
            errors.append(f"{name} is unchanged since the previous round's closeout; add this "
                          f"round's evidence")
    blocked = status.get("blocked_notes_required")
    if blocked is None:
        errors.append("protocol status did not report blocked_notes_required")
    elif blocked:
        errors.append(f"attempts/seed_N_BLOCKED.md ({', '.join(BLOCKED_SECTIONS)}) is required for "
                      f"exhausted seeds without a pass: {blocked}")
    successes = (status.get("supervised") or {}).get("study_successes")
    if successes is None:
        errors.append("protocol status did not report study_successes")
        successes = []
    selected, winners = data.get("selected"), {s["bundle_sha256"] for s in successes}
    on_disk, problem = disk_bundle(data, task_dir)
    if winners:
        if not selected or not str(selected.get("reason", "")).strip():
            errors.append("a tested bundle of this study has its own oracle success; copy it into "
                          "fix_code.py/fix_streaming_config.json and `select --reason` it")
        elif selected["bundle_sha256"] not in winners:
            errors.append("the selection is not a bundle with its own oracle success in this study")
        elif problem or on_disk != selected["bundle_sha256"]:
            errors.append(f"fix files do not match the selected bundle ({problem or 'different content'})")
    elif selected:
        errors.append("a selection exists although no bundle of this study has an oracle success")
    return {"round": round_, "complete": not errors and not violations, "errors": errors,
            "violations": violations, "reports": reports, "study_successes": successes,
            "selected": (selected or {}).get("bundle_sha256"), "fix_bundle_sha256": on_disk,
            "blocked_notes_required": blocked, "evidence": now}


ROUND_SECTION = """
---

## Round closeout (explicit opt-in, {schema})

Finishing the 15 trials of a round is NOT the end of the round. The round is
closed only after its closeout is complete: updated `task_analysis.md` and
`findings.md` for this round, `attempts/seed_N_BLOCKED.md` for every exhausted
seed without a pass, and — only if a bundle of this study has its own normal
oracle success — that exact tested bundle copied into `fix_code.py` /
`fix_streaming_config.json` and recorded with `select --reason`. After your last
trial of the round you have at most {grace} s to finish this, then end the
session; the outer runner stops a session still running at that limit. It
then verifies the closeout. If something is missing, a restricted closeout
session of this cell completes it (no trial, no new policy); its report is
what the round review reads. Closeout never adds or refunds an attempt.
"""

SESSION_PROMPT = """Restricted ROUND {round} CLOSEOUT of cell {cell}, study {study} (no trial session).

All 15 charged attempts of round {round} are resolved. This session runs NO
trial of any kind: no `trial`, `snapshot`, `diagnostic` or `alias-smoke`. The
ledger refuses further attempts of this round and the outer runner stops this
session if one is admitted. Do not author a new policy or streaming config, and
never edit any attempt directory, `development/` row or tested program.

Inputs: only this cell's own development evidence in `{task_dir}` (run `status`
first; attempt directories with code.py, authored_streaming_config.json and
replay.log; task_analysis.md; findings.md; attempts/) and these own records:
{inputs}
Never read another cell, a held-out path or a coordinator result file.

Outputs, in `{task_dir}` only:
1. task_analysis.md and findings.md updated with round {round} evidence. findings.md
   keeps nonempty sections: {findings}.
2. attempts/seed_N_BLOCKED.md with sections {blocked_sections} for each exhausted
   seed without a pass{blocked}.
3. Selection: {selection}

The outer runner found these closeout problems:
{errors}

Commands (from {sim}):
  .venv-libero/bin/python3 {protocol} status
  .venv-libero/bin/python3 {protocol} select --reason "<why this exact tested bundle>"
End the session when the outputs are written.
"""


def session_prompt(case: dict, task_dir: Path, result: dict, inputs: list[str]) -> str:
    successes = result["study_successes"]
    if successes:
        listed = ", ".join(f"seed {s['seed']} round {s['round']} {s['directory']} bundle "
                           f"{s['bundle_sha256'][:12]}" for s in successes)
        selection = ("copy code.py and authored_streaming_config.json of ONE of these tested "
                     f"executions with its own normal oracle success ({listed}) unchanged into "
                     "fix_code.py and fix_streaming_config.json, then `select --reason`.")
    else:
        selection = ("no bundle of this study has a normal oracle success. Do NOT select; "
                     "record that fact in findings.md.")
    blocked = result.get("blocked_notes_required")
    return SESSION_PROMPT.format(
        round=result["round"], cell=case["id"], study=case["supervised_repair"]["study_id"],
        task_dir=task_dir, inputs="\n".join(f"- `{p}`" for p in inputs) or "- (none)",
        findings=", ".join(FINDINGS_SECTIONS), blocked_sections=", ".join(BLOCKED_SECTIONS),
        blocked=f" (currently required: {blocked})" if blocked else "", selection=selection,
        errors="\n".join(f"- {e}" for e in result["errors"]), sim=case["sim"], protocol=PROTOCOL)


def session_watch(task_dir: Path, start: dict, info: dict, poll: float = 5.0):
    """on_start hook: stop a closeout session whose ledger gains a running or charged row."""
    def on_start(process):
        def watch():
            while process.poll() is None:
                time.sleep(poll)
                try:
                    data = json.loads((task_dir / "development_state.json").read_text())
                except (OSError, ValueError):
                    continue
                if (charged(data) != start["charged_attempts"] or len(data["trials"]) != start["rows"]
                        or any(r["status"] == "running" for r in data["trials"])):
                    info["stopped_for_trial"] = True
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    return
        threading.Thread(target=watch, daemon=True).start()
    return on_start
