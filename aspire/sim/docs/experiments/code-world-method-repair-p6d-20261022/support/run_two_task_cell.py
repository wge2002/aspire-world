"""Run one fresh foundation C cell: import its pinned current-study diagnostic exactly once, then native development51-65, freeze and heldout1-50. Native guards, compaction, ownership and accounting remain unchanged from the verified driver.

p4 copy (CC_TASK_2, 2026-10-17) of the p3 search-control support driver; the p3
original stays frozen. Only `require_development_possible` changed: it stops on
the protocol's structured `decision_type: terminal` / `terminal_reasons` (not
just exhausted seeds) and on STALL_TURNS identical post-turn checks.

CC_TASK_5 (2026-10-19): the held-out watchdog is derived from the seeds still to
run (`full_deadlines.heldout_deadline`) unless `--deadline-heldout` overrides it,
and `--heldout-supplement` finishes a preserved, interrupted sweep on its frozen
bundle (`heldout_supplement.py`); development and freeze are unchanged.

p5 copy (CC_TASK_6, 2026-10-19) of that p4 driver; the p4 original stays as it
was frozen. For a case with `development_budget: "aspire-v1"` (the ASPIRE v1.0
development budget the protocol ledger enforces) only the text the driver
writes to the coordinator changes: the continuation message and the per-round
suffix name the budget's phase and freeze conditions instead of "the same
three-retry cap per seed". Stop rules, freeze, held-out and supplement are the
p4 driver's, byte for byte; the development watchdog comes from the p5
`full_deadlines.py` (24 h).

p6 copy (CC_TASK_7, 2026-10-20) of that p5 driver; the p5 original stays as it
was frozen. The held-out evaluator may run `heldout_parallel` seeds at once
(case key; absent = 1), so the derived held-out bound is
`full_deadlines.heldout_deadline(seeds, trial_timeout, heldout_parallel)` (the
p6 `full_deadlines.py` states the formula), and a case whose parallelism does
not match the per-cell allowance the supervisor was sized from is refused
before anything runs (`full_deadlines.check_case`).

CC_TASK_8 (2026-10-20): phase-A no-progress starts another diagnostic round
within the original cumulative deadline. Phase-B deadline/no-progress/solver
exit invokes the locked outer end-development selection, then the same finalize,
freeze and held-out path. Solver descendants are tracked and cleaned before
selection; cancellation and real infrastructure faults still stop the cell.

p6b copy (CC_TASK_9, 2026-10-21) of the p6 driver; the p6 original stays as r3
froze it. Development orchestration only:

- Worker recovery: a turn whose structured stream shows the primary cannot be
  resumed (`worker_recovery.resume_evidence`) opens a recovery round: a NEW
  coordinator session dispatches generation N with the same frozen assignment
  plus a driver-written handoff, delivered by the assignment hook only while the
  driver's request is open. Ledger, attempts, phase, versions, mode counts and
  the cumulative deadline are untouched; `worker_generations` records lineage.
- B1: unresolved infrastructure rows open an infrastructure round for the
  worker's existing close-infra flow instead of blocking at once; the same rows
  surviving INFRA_ROUNDS such rounds stop development (phase B closeout first).
- B2: a nonzero/`is_error` solver turn with development time left opens another
  round; SOLVER_FAILURE_ROUNDS consecutive failed rounds without a ledger change
  stop development (phase B closeout, phase A blocked).
- B3: a phase-B `terminal` check is settled by end-development before blocking.

p6c copy (CC_TASK_12, 2026-10-22) of the p6b driver; the p6b original stays as
p6b/l10/ns froze it. Development orchestration only:

- G5: a primary whose last run of a turn ended `failed` with an API error that is
  a context-window overflow, or that failed twice in a row without doing anything
  (`worker_recovery.failure_evidence`), is replaced through the same recovery
  round as an unresumable one (reason `worker_api_failure`): same ledger, nothing
  refunded. l10 mugmicrowave lost 5.5 h / 63 rounds to this before the transcript
  happened to cross native's 5 MiB reload limit.
- G6: the same unresolved infrastructure rows surviving INFRA_ROUNDS rounds no
  longer end development while time remains. The outer driver applies the
  protocol's own `close-infra` disposition to each ended row (a watchdog row is
  settled from its preserved evidence, any other row closed without usable
  evidence; nothing refunded or re-run) and continues; only a row it cannot settle,
  or more than INFRA_DISPOSITIONS such rows, stops as before.

p6d copy (CC_TASK_14, 2026-10-22) of the p6c driver; the p6c original stays as it is.
Only for a case with `method_switches` (absent: every step is p6c's, byte for byte):

- `prefreeze_resweep`: development always ends through the outer selection (a
  solver that reached `ready` ends it with reason `ready`), then the protocol's
  `resweep` re-runs the planned near-best complete candidates on every development
  seed and selects on both runs, before `finalize`. `state.prefreeze_resweep`.
- `unseen_seeds`: after the freeze and before held-out, the frozen bundle runs on
  the unseen seeds (66-80) through the same evaluator (`--seed-set unseen`, entered
  through `heldout_stop_on_infra.py`, whose stop guard watches only the held-out
  ledger), under the held-out deadline rule. Recorded in `state.unseen` and
  `<control>/heldout/unseen/unseen_result.json`; it never changes the freeze and a
  failed measurement is recorded without stopping held-out. No solver sees it.
- Held-out policy timeouts are settled by the runtime evaluator (`heldout_watchdog`)."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import threading
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import full_deadlines
import two_task_scope as scope
import output_ownership
import runtime_limits
from infra_guard import bind_child_env, fault, monitor
from lineage import audit_assignment, verify_received_assignment
import native_cc_stream as fixed_transport
import pilot_assignment_guard
import worker_recovery


STUDY_MODEL = "qwen3.8-flash-next"
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
HELDOUT_SEEDS = tuple(scope.HELDOUT_SEEDS)
TERMINAL = scope.TERMINAL

#: The only statuses `native_world_heldout` writes for an evaluated seed.
HELDOUT_STATUSES = ("success", "failure", "crash", "infrastructure_error")

#: Grace between SIGTERM and SIGKILL for a held-out sweep that overran. It must
#: exceed `run_replay`'s own 15s TERM->KILL cleanup of the detached replay
#: session, or the evaluator dies mid-cleanup and orphans that child.
GROUP_STOP_GRACE = 45


class Blocked(RuntimeError):
    """A structured stop: the phase, the reason, and what survives it.

    There is deliberately no `resumable` flag. The driver refuses to run against
    an existing `campaign_state.json`, so claiming a blocked run can be resumed
    would be a misreport. What every blocker does guarantee is stated instead:
    the ledger and all recorded per-seed evidence are preserved untouched, and
    continuing from them is an operator decision. The one supported
    continuation is a held-out block: `--heldout-supplement` runs only the
    seeds the sweep lacks, on the same frozen bundle.
    """

    def __init__(self, phase, reason, *, detail=None):
        super().__init__(f"{phase}: {reason}")
        self.payload = {"phase": phase, "reason": reason, "state_preserved": True,
                        "automatic_resume": False, "recovery": "manual",
                        "detail": detail}


def now():
    return datetime.now(timezone.utc).isoformat()


#: CC_TASK_2 (p4): this many consecutive post-turn checks with an identical
#: fingerprint (no new trial, rejection, blocker or selection, and the same
#: errors) end phase B; phase A starts a new diagnostic round. The
#: p4 round's bowldrawer_p4_r1 restarted an idle coordinator 47 times.
STALL_TURNS = 3

#: CC_TASK_9 B2: consecutive failed rounds (solver exit / is_error, an unresumable
#: worker, a recovery round that dispatched no worker) with an unchanged ledger
#: that stop development. Equal to STALL_TURNS: one failure is transient (an API
#: error, a timed-out turn), three in a row with nothing recorded is persistent.
SOLVER_FAILURE_ROUNDS = 3

#: CC_TASK_9 B1: infrastructure rounds the SAME unresolved rows may survive. The
#: worker's close-infra flow settles an ended row within one round; rows it
#: cannot settle (live evidence it may not discard) need the outer operator.
INFRA_ROUNDS = 2

#: CC_TASK_12 G6: ended infrastructure rows the outer driver itself may dispose of
#: (protocol `close-infra`) after INFRA_ROUNDS. Equal to the protocol's own repeat
#: rule (three infrastructure-only attempts on a seed stop the cell): beyond it the
#: rows are a recurring fault, not a single lost run.
INFRA_DISPOSITIONS = 3


def check_fingerprint(result):
    """What a coordinator round can change, from the check's structured fields only."""
    progress = result.get("progress") or {}
    gate = result.get("coverage_gate") or {}
    return json.dumps({"replays": progress.get("replay_invocations"),
                       "rejected": len(progress.get("rejected_revisions") or []),
                       "blockers": len(progress.get("screening_blockers") or []),
                       "selected": (result.get("selected") or {}).get("bundle_sha256"),
                       "reason": (result.get("selected") or {}).get("reason"),
                       "covered": gate.get("covered_seeds"),
                       "errors": sorted(result.get("errors") or [])}, sort_keys=True)


def require_development_possible(check, history=None):
    """Stop as soon as the cell can no longer reach `ready`, or stops moving.

    Read the ledger's structured fields, never model prose or an error string:
    `decision_type == "terminal"` with its `terminal_reasons` (exhausted seeds
    without graded evidence, a coverage gate no candidate can reach, ...), and
    the legacy `seeds_exhausted_without_graded_evidence` list for a protocol
    that predates `terminal_reasons`. The same check runs before the first
    worker and after every completed turn; `history` (post-turn checks only)
    adds the no-progress stop.

    CC_TASK_9: after a turn (`history` given), unresolved infrastructure evidence
    returns "infrastructure_round" (B1; the driver bounds repeats), and a terminal
    phase-B check carries `closeout: True` so the driver settles it through
    end-development before blocking (B3). Before the first worker both still block.
    """
    result = check.get("result")
    progress = result.get("progress") if isinstance(result, dict) else None
    exhausted = (progress.get("seeds_exhausted_without_graded_evidence")
                 if isinstance(progress, dict) else None)
    if not isinstance(exhausted, list) or any(type(seed) is not int for seed in exhausted):
        raise Blocked("development_check", "protocol check has no valid evidence-accounting result",
                      detail={"reason_code": "invalid_protocol_check", "check": check})
    if result.get("development_budget") and isinstance(progress, dict):
        infrastructure = (progress.get("infrastructure_errors")
                          or progress.get("unresolved_screening_blockers"))
        if infrastructure:
            if history is not None:
                return "infrastructure_round"
            raise Blocked("infrastructure", "unresolved infrastructure evidence is not strategy stagnation",
                          detail={"reason_code": "unresolved_infrastructure", "evidence": infrastructure})
    decision = result.get("decision_type")
    reasons = result.get("terminal_reasons")
    if decision == "terminal":
        if not reasons:
            # A protocol without `terminal_reasons` only knows this one terminal state.
            reasons = ([{"code": "seeds_exhausted_without_graded_evidence", "seeds": exhausted}]
                       if exhausted else [])
        if not reasons or any(not isinstance(r, dict) or not r.get("code") for r in reasons):
            raise Blocked("development_check", "protocol check is terminal without a structured reason",
                          detail={"reason_code": "invalid_protocol_check", "check": check})
        phase_b = (result.get("development_budget") or {}).get("phase") == "B"
        raise Blocked("development", "the protocol check reports a state from which the cell can no "
                      "longer reach ready; no further coordinator round is started",
                      detail={"reason_code": reasons[0]["code"], "terminal_reasons": reasons,
                              **({"seeds": exhausted} if exhausted else {}), "check": result,
                              "closeout": history is not None and phase_b})
    if exhausted:
        raise Blocked("development", "development seeds exhausted their attempts without graded evidence",
                      detail={"reason_code": "seeds_exhausted_without_graded_evidence",
                              "seeds": exhausted, "check": result})
    if history is not None and not result.get("ready"):
        history.append(check_fingerprint(result))
        if len(history) >= STALL_TURNS and len(set(history[-STALL_TURNS:])) == 1:
            budget = result.get("development_budget") or {}
            if budget.get("phase") == "A":
                # A diagnostic-round boundary, not a new experiment/budget.
                # Keep the full history; the caller starts a new strategy round.
                return "diagnostic_round"
            raise Blocked("development", f"{STALL_TURNS} consecutive coordinator rounds changed nothing "
                          "the protocol check can see; no further round is started",
                          detail={"reason_code": "no_progress", "rounds": STALL_TURNS, "check": result})


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(transcripts, assignment):
    audit = audit_assignment(transcripts, assignment)
    if not audit["ok"] or len(audit["sessions"]) != 1:
        raise Blocked("solver_lineage", "ambiguous native worker lineage",
                      detail=audit)
    return audit["sessions"][0]["session_id"], audit["primary_worker"]


def model_fields(case):
    return {key: value for key, value in case.items()
            if isinstance(value, str) and "model" in key and key != "model_provider"}


def verify_local_provider(case, settings):
    """Fail closed on the provider contract; never fall back to a provider."""
    if case.get("model_provider") != "local-vllm":
        raise ValueError(f"This study requires model_provider=local-vllm, got {case.get('model_provider')!r}")
    if "apiKeyHelper" in settings:
        raise ValueError("Native settings carry apiKeyHelper; the local provider takes no key helper")
    endpoint = settings.get("env", {}).get("ANTHROPIC_BASE_URL")
    if not endpoint:
        raise ValueError("Native settings carry no ANTHROPIC_BASE_URL for the local provider")
    if urlsplit(endpoint).hostname not in LOOPBACK:
        raise ValueError(f"Inference endpoint is not loopback: {endpoint}")
    if "inference_endpoint" in case and case["inference_endpoint"] != endpoint:
        raise ValueError("Case inference_endpoint differs from the native settings endpoint")
    models = model_fields(case)
    if len(models) != 3 or set(models.values()) != {STUDY_MODEL}:
        raise ValueError(f"Case model fields are not three x {STUDY_MODEL}: {json.dumps(models, sort_keys=True)}")
    return {"model_provider": case["model_provider"], "endpoint": endpoint,
            "api_key_helper": False, "models": models}


def verify_capacity(case, settings, probe):
    """Frozen v4's capacity rule, applied at the driver.

    `modelUsage.maxOutputTokens` reports the alias default for a custom local
    model, so only the served context window can be compared directly; the
    request setting is validated against the settings this driver installed.
    """
    problems = []
    if probe["context_windows"] != [case["context_tokens"]]:
        problems.append(f"served context windows {probe['context_windows']} "
                        f"!= configured {case['context_tokens']}")
    configured = settings.get("env", {}).get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")
    if case.get("model_provider") == "local-vllm":
        if configured != str(case["max_output_tokens"]):
            problems.append(f"request setting CLAUDE_CODE_MAX_OUTPUT_TOKENS={configured!r} "
                            f"!= configured {case['max_output_tokens']}")
    elif probe["max_output_tokens"] != [case["max_output_tokens"]]:
        problems.append(f"reported max output {probe['max_output_tokens']} "
                        f"!= configured {case['max_output_tokens']}")
    if problems:
        raise Blocked("capacity_probe", "native capacity differs from the frozen case",
                      detail={"problems": problems, "probe": probe,
                              "configured_request_max_output": configured})
    return {"context_windows": probe["context_windows"],
            "reported_max_output_tokens": probe["max_output_tokens"],
            "configured_request_max_output": configured,
            "checked_request_setting": case.get("model_provider") == "local-vllm"}


#: The ASPIRE v1.0 development budget (protocol `development_budget`).
BUDGET_REVISION = "aspire-v1"


def budgeted(case):
    return case.get("development_budget") == BUDGET_REVISION


def continuation(worker, remaining_seconds, case=None):
    """Resume the SAME worker. No budget is granted, reset or refunded here."""
    if case is not None and budgeted(case):
        return budget_continuation(worker, remaining_seconds)
    return f"""Continue this same cell under its original protocol. Use SendMessage
to resume the existing worker {worker}, with this protocol-only message:
"The protocol check is not ready. Read your own ledger status and complete the
pending development work on seeds 51-65 using the remaining original attempts.
Keep all spent retries spent and the same three-retry cap per seed. Select a tested bundle, complete required reports, and run check before
returning."
Do not create another worker, supply a task strategy, reset budgets or access
another cell or held-out data. Development seeds 51-65 are your whole boundary;
the frozen held-out evaluation on seeds 1-50 is run afterwards by the outer
driver, from the bundle you select, and no held-out outcome is returned to you.
Approximately {int(remaining_seconds // 60)} minutes of wall-clock development time remain in this
job. That is an infrastructure watchdog, not a revision or action limit: it
neither refunds nor adds a retry.
After its native completion notification, run the original protocol check and
report its actual result.
"""


def budget_continuation(worker, remaining_seconds):
    """aspire-v1: the same resume message, stated in the cell's own budget."""
    return f"""Continue this same cell under its original protocol. Use SendMessage
to resume the existing worker {worker}, with this protocol-only message:
"The protocol check is not ready. Read your own ledger status, including its
development_budget block, and continue the development work on seeds 51-65
under the aspire-v1 budget. Phase A (seed 51 has not passed yet): keep
iterating on seed 51 until it actually passes; attempts there are not capped
while your diagnostics make progress, and select/freeze are refused. Phase B:
every version runs on all 15 development seeds (sweep the rest), declares the
failure mode it targets, at most 3 versions per mode and 8 in total; a mode
still unfixed after 3 versions is blocked (write its BLOCKED note) and gets no
more budget. Freeze only when the selected version passes 15/15, all 8
versions are used, or every remaining failure mode is blocked; select the
version with the most passes, then fewer crashes, then the simpler one.
Complete required reports and run check before returning."
Do not create another worker, supply a task strategy, reset budgets or access
another cell or held-out data. Development seeds 51-65 are your whole boundary;
the frozen held-out evaluation on seeds 1-50 is run afterwards by the outer
driver, from the bundle you select, and no held-out outcome is returned to you.
Approximately {int(remaining_seconds // 60)} minutes of wall-clock development time remain in this
job. That is an infrastructure watchdog, not a revision or action limit: it
neither refunds nor adds a version or an attempt.
After its native completion notification, run the original protocol check and
report its actual result.
"""


def round_suffix(case):
    """What the driver appends to the coordinator prompt on a fresh round."""
    if budgeted(case):
        return ("\nIf check reports development_budget phase A (seed 51 has not passed), or phase B "
                "with freeze_conditions.met false, use SendMessage to resume the SAME worker to "
                "complete the existing protocol. Never reset the ledger or create a replacement "
                "worker.\n")
    return ("\nIf check reports seeds_pending_repair with original attempts "
            "remaining, use SendMessage to resume the SAME worker to complete the "
            "existing protocol. Never reset the ledger or create a replacement "
            "worker.\n")


#: CC_TASK_12: why the previous primary is lost, as the recovery round states it.
LOST = {"unresumable_worker": ('returned, and native Claude Code can no\n'
                               'longer resume it (SendMessage: "could not be resumed")'),
        "worker_api_failure": ('can no longer work: every run of it\n'
                               'ends at once with an API error (its context no longer fits the model window)')}


def recovery_suffix(generation, previous_worker, reason="unresumable_worker"):
    """CC_TASK_9: what a new coordinator session is told in an outer recovery round."""
    return f"""
OUTER-DRIVER WORKER RECOVERY ROUND (generation {generation}). The previous primary
worker of this cell, {previous_worker}, {LOST[reason]}. This is a new coordinator
session opened by the outer driver for exactly this. Dispatch the generation
{generation} primary exactly as described above: ONE background general-purpose
Agent with prompt ASPIRE_FROZEN_PRIMARY_ASSIGNMENT. The native hook accepts that
one dispatch in this round only and supplies the same frozen assignment followed
by the outer driver's handoff (ledger and check state, the previous worker's own
files), verbatim; add nothing to it. Do not SendMessage {previous_worker}. The
ledger, spent attempts, budget phase, versions, per-mode counts and the original
cumulative development deadline are unchanged; nothing is reset or refunded.
After its completion notification, save its findings and run check as above.
"""


def infrastructure_round(evidence):
    """CC_TASK_9 B1: a round for the worker's existing infrastructure recovery flow."""
    return ("\nINFRASTRUCTURE RECOVERY ROUND: check reports unresolved infrastructure evidence "
            f"{json.dumps(evidence, sort_keys=True)[:2000]}. Include in the protocol-only message: "
            "follow the existing infrastructure recovery step of your assignment: inspect status "
            "and the preserved stderr/evidence; close an ended retry-spending row that has no usable "
            "execution evidence with close-infra; no attempt is refunded. close-infra settles a row the "
            "trial watchdog stopped from its own evidence (an outcome the finished policy had recorded, "
            "or a policy that ran out of its trial budget as a failed trial). A service, GPU, simulator "
            "or snapshot fault that persists is reported as a blocker, never retried as strategy.\n")


def solver_failure_round(code, failures):
    """CC_TASK_9 B2: the previous native turn ended in a solver failure; continue."""
    return (f"\nThe previous coordinator turn ended with a native solver failure (exit {code}; "
            f"{failures} consecutive failed round(s) without a ledger change). This is a new round "
            "of the same development work, not a new budget.\n")


def disposed_round(records):
    """CC_TASK_12 G6: the next round's note after the driver settled infrastructure rows."""
    listing = "; ".join(f"{r['directory']} -> {r['status']} ({r['disposition']})" for r in records)
    return ("\nThe outer driver settled the unresolved infrastructure rows with the protocol's "
            f"close-infra rule: {listing}. Nothing was refunded or re-run. Continue the same "
            "development work from `status` and `check`.\n")


def heldout_manifest(case, repo, task_dir, evaluation):
    """The immutable 50-row manifest, written once from the frozen selection.

    Refuses an incomplete, duplicated or missing seed set, and refuses to
    rewrite an existing manifest whose identity differs: the sweep that already
    started is the one that must finish.
    """
    stage1_path = task_dir / "stage1_result.json"
    if not stage1_path.is_file():
        raise Blocked("freeze", "development finalization wrote no stage1_result.json",
                      detail={"expected": str(stage1_path)})
    stage1 = json.loads(stage1_path.read_text())
    if not stage1.get("stage1_complete"):
        raise Blocked("freeze", "stage 1 is not complete; the development ledger is not finalized",
                      detail={"stage1_complete": stage1.get("stage1_complete")})
    bundle = stage1.get("selected_bundle")
    if not bundle:
        raise Blocked("freeze", "no selected bundle to freeze",
                      detail={"stage1_keys": sorted(stage1)})
    seeds = list(HELDOUT_SEEDS)
    # The frozen sweep is seeds 1-50 exactly. "50 unique sorted integers" would
    # accept a shifted or resampled set, which is a different experiment.
    if seeds != list(range(1, 51)):
        raise Blocked("freeze", "held-out seed set is not exactly seeds 1-50",
                      detail={"seeds": seeds})
    rows = [{"row": index, "seed": seed, "status": "pending"}
            for index, seed in enumerate(seeds, start=1)]
    manifest = {"schema_version": 1, "cell": case["id"], "condition": case["condition"],
                "suite": case["suite"], "task": case["task"],
                "seeds": seeds, "rows": rows, "row_count": len(rows),
                "selected_bundle": bundle,
                "selected_bundle_sha256": stage1.get("selection", {}).get("bundle_sha256"),
                "tested_bundle_digests": sorted(stage1.get("tested_bundles", {})),
                "config_sha256": digest(repo / case["env_config"]),
                "trial_timeout": case["trial_timeout"], "max_steps": case["max_steps"],
                "frozen_at": now(), "immutable": True,
                "note": ("Outer-only evaluation. Every seed is attempted, every result "
                         "stays visible, and no held-out outcome is returned to a solver.")}
    path = evaluation / "heldout_manifest.json"
    if path.is_file():
        existing = json.loads(path.read_text())
        comparable = ("cell", "condition", "suite", "task", "seeds", "row_count",
                      "selected_bundle", "selected_bundle_sha256", "config_sha256",
                      "trial_timeout", "max_steps")
        differing = {k: (existing.get(k), manifest[k]) for k in comparable
                     if existing.get(k) != manifest[k]}
        if differing:
            raise Blocked("freeze", "an immutable held-out manifest already exists with a "
                          "different identity; inspect it rather than overwriting",
                          detail={"path": str(path), "differing": differing})
        # An existing manifest is trusted for the sweep, so its rows are checked
        # too, not just the scalar identity fields beside them.
        row_problems = manifest_row_problems(existing)
        if row_problems:
            raise Blocked("freeze", "the existing held-out manifest's rows are not the frozen "
                          "1-50 sweep; inspect it rather than evaluating against it",
                          detail={"path": str(path), "problems": row_problems})
        return existing
    evaluation.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def manifest_row_problems(manifest):
    """Ways a manifest's `rows` could stop describing the frozen 1-50 sweep."""
    rows = manifest.get("rows")
    problems = []
    if not isinstance(rows, list):
        return [f"rows is {type(rows).__name__}, not a list"]
    if len(rows) != manifest.get("row_count"):
        problems.append(f"{len(rows)} rows against row_count {manifest.get('row_count')}")
    seeds = [row.get("seed") for row in rows if isinstance(row, dict)]
    if len(seeds) != len(rows):
        problems.append("some rows are not objects")
    if seeds != list(range(1, 51)):
        problems.append(f"row seeds are not exactly 1-50: {seeds}")
    if list(manifest.get("seeds") or []) != seeds:
        problems.append("the `seeds` list and the row seeds disagree")
    numbering = [row.get("row") for row in rows if isinstance(row, dict)]
    if numbering != list(range(1, len(rows) + 1)):
        problems.append("rows are not numbered 1..N in order")
    return problems


def check_heldout_rows(manifest, report):
    """Verify the sweep against the manifest, recomputing what it reports.

    Key coverage alone is not enough: a report can name every seed and still be
    the wrong evaluation. So the frozen identity (cell/condition/suite/task and,
    above all, the selected bundle and its digest) must match the manifest, every
    per-seed status must be one the evaluator actually writes, and `counts`,
    `seeds_evaluated`, `success_rate` and `unusable_seeds` are recomputed from
    the per-seed statuses rather than believed.
    """
    report = report or {}
    per_seed = report.get("per_seed") or {}
    expected = [str(seed) for seed in manifest["seeds"]]
    missing = [seed for seed in expected if seed not in per_seed]
    extra = sorted(set(per_seed) - set(expected))
    problems = {}
    if missing:
        problems["missing_seeds"] = missing
    if extra:
        problems["unexpected_seeds"] = extra
    if len(per_seed) != manifest["row_count"]:
        problems["row_count"] = {"manifest": manifest["row_count"], "reported": len(per_seed)}
    invalid = {seed: status for seed, status in sorted(per_seed.items())
               if status not in HELDOUT_STATUSES}
    if invalid:
        problems["invalid_statuses"] = invalid

    identity = report.get("identity") or {}
    identity_problems = {key: {"manifest": manifest.get(key), "report": identity.get(key)}
                         for key in ("cell", "condition", "suite", "task")
                         if identity.get(key) != manifest.get(key)}
    if identity.get("bundle") != manifest.get("selected_bundle"):
        identity_problems["bundle"] = {"manifest": manifest.get("selected_bundle"),
                                       "report": identity.get("bundle")}
    if identity.get("bundle_sha256") != manifest.get("selected_bundle_sha256"):
        identity_problems["bundle_sha256"] = {"manifest": manifest.get("selected_bundle_sha256"),
                                              "report": identity.get("bundle_sha256")}
    if identity_problems:
        problems["identity"] = identity_problems

    counts = {status: sum(1 for value in per_seed.values() if value == status)
              for status in HELDOUT_STATUSES}
    if report.get("counts") != counts:
        problems["counts"] = {"recomputed": counts, "reported": report.get("counts")}
    if report.get("seeds_evaluated") != len(per_seed):
        problems["seeds_evaluated"] = {"recomputed": len(per_seed),
                                       "reported": report.get("seeds_evaluated")}
    rate = counts["success"] / manifest["row_count"] if manifest["row_count"] else 0.0
    if not isinstance(report.get("success_rate"), (int, float)) or \
            abs(report["success_rate"] - rate) > 1e-9:
        problems["success_rate"] = {"recomputed": rate, "reported": report.get("success_rate")}
    unusable = sorted(int(seed) for seed, status in per_seed.items()
                      if status == "infrastructure_error")
    if sorted(report.get("unusable_seeds") or []) != unusable:
        problems["unusable_seeds_report"] = {"recomputed": unusable,
                                             "reported": report.get("unusable_seeds")}
    if unusable:
        problems["unusable_seeds"] = unusable
    if not report.get("all_seeds_accounted"):
        problems["all_seeds_accounted"] = False
    if problems:
        raise Blocked("heldout", "the held-out sweep does not account for the manifest exactly",
                      detail=problems)
    return {"rows_accounted": len(per_seed), "counts": counts, "success_rate": rate,
            "identity_verified": True, "unusable_seeds": unusable}


def stop_group(process):
    """SIGTERM the sweep's own process group, then SIGKILL what survives.

    The sweep is spawned with `start_new_session=True`, so its group contains the
    evaluator and every simulator child it launched and nothing of this driver's.
    """
    for signal_number, grace in ((signal.SIGTERM, GROUP_STOP_GRACE), (signal.SIGKILL, 10)):
        try:
            os.killpg(os.getpgid(process.pid), signal_number)
        except (ProcessLookupError, PermissionError):
            break
        try:
            return process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
    return process.poll()


def judge_heldout_seeds(case, repo, env, folder, timeout=3 * 3600):
    """Run `gate_study_analysis.judge_heldout` in its own process group."""
    command = [str(repo / ".venv-libero/bin/python3"), "-c",
               "import json, sys; sys.path.insert(0, sys.argv[1]); import gate_study_analysis as a; "
               "print(json.dumps({'judged': a.judge_heldout(sys.argv[2])}))",
               str(repo / "scripts/libero"), str(Path(case["control"]) / "case.json")]
    stdout_path, stderr_path = folder / "heldout_judge.stdout.json", folder / "heldout_judge.stderr.log"
    started = time.monotonic()
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            code, timed_out = stop_group(process), True
    try:
        payload = json.loads(stdout_path.read_text())
    except (json.JSONDecodeError, OSError):
        payload = None
    return {"performed": True, "exit_code": code, "timed_out": timed_out, "result": payload,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "stderr_tail": stderr_path.read_text()[-2000:],
            "output": str(Path(case["control"]) / "heldout/judge")}


def run_heldout(campaign, repo, env, folder, argv, timeout):
    """Run the outer held-out sweep under an actually enforced deadline.

    `campaign.protocol` runs its step through `subprocess.run` with no timeout,
    so a stalled sweep would hang forever and an elapsed-time check *after* it
    returned could never fire. The sweep is therefore spawned here instead, in
    its own process group, and an expiry stops that group. Per-seed rows already
    written to `heldout_state.json` are preserved exactly as they are: this
    function never edits a ledger, refunds an attempt or re-runs a recorded seed.
    The recorded step keeps `campaign.protocol`'s shape so downstream readers and
    `outer_heldout.json` are unchanged.
    """
    # The evaluator is entered through `heldout_stop_on_infra`, which ends the
    # sweep one atomic ledger write after an infrastructure row instead of
    # starting another replay in its own session while this stop is in progress.
    command = [str(repo / ".venv-libero/bin/python3"),
               str(Path(__file__).with_name("heldout_stop_on_infra.py")), *argv]
    stdout_path, stderr_path = folder / "heldout.stdout.json", folder / "heldout.stderr.log"
    started = time.monotonic()
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            code, timed_out = stop_group(process), True
    try:
        payload = json.loads(stdout_path.read_text())
    except (json.JSONDecodeError, OSError):
        payload = None
    step = {"step": "heldout", "command": command, "exit_code": code, "result": payload,
            "stderr_tail": stderr_path.read_text()[-4000:], "timed_out": timed_out,
            "timeout_seconds": timeout, "elapsed_seconds": round(time.monotonic() - started, 1),
            "stdout_path": str(stdout_path)}
    campaign.atomic_json(folder / "outer_heldout.json", step)
    return step


def run_unseen(campaign, repo, env, folder, case_path, timeout):
    """CC_TASK_14: the frozen bundle on the unseen seeds, outer only, under an enforced deadline.

    `run_heldout`'s process handling with its own files, so the held-out step's
    `outer_heldout.json` / `heldout.stdout.json` are never touched.
    """
    command = [str(repo / ".venv-libero/bin/python3"),
               str(Path(__file__).with_name("heldout_stop_on_infra.py")),
               "--case", str(case_path), "--seed-set", "unseen"]
    if (Path(json.loads(Path(case_path).read_text())["control"]) / "heldout/unseen/unseen_state.json").is_file():
        command.append("--resume")  # Recorded unseen seeds are never re-run.
    stdout_path, stderr_path = folder / "unseen.stdout.json", folder / "unseen.stderr.log"
    started = time.monotonic()
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        process = subprocess.Popen(command, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=err, start_new_session=True)
        try:
            code, timed_out = process.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            code, timed_out = stop_group(process), True
    try:
        payload = json.loads(stdout_path.read_text())
    except (json.JSONDecodeError, OSError):
        payload = None
    step = {"step": "unseen", "command": command, "exit_code": code, "result": payload,
            "stderr_tail": stderr_path.read_text()[-4000:], "timed_out": timed_out,
            "timeout_seconds": timeout, "elapsed_seconds": round(time.monotonic() - started, 1),
            "stdout_path": str(stdout_path)}
    campaign.atomic_json(folder / "outer_unseen.json", step)
    return step


def heldout_pending(evaluation):
    """Seeds of 1-50 the held-out ledger has no row for yet (all 50 before a sweep)."""
    ledger = evaluation / "heldout_state.json"
    rows = json.loads(ledger.read_text())["seeds"] if ledger.is_file() else {}
    return [seed for seed in HELDOUT_SEEDS if str(seed) not in rows]


def heldout_deadline(args, case, seeds):
    """`--deadline-heldout` if given, else the bound of the seeds about to run,
    at the case's held-out parallelism (CC_TASK_7)."""
    if args.deadline_heldout is not None:
        return args.deadline_heldout
    return full_deadlines.heldout_deadline(len(seeds), case["trial_timeout"],
                                           case.get("heldout_parallel", 1))


def run_heldout_supplement(args, case, repo, control, campaign):
    """Finish a preserved, interrupted held-out sweep: only its missing seeds.

    Kept rows stay; seeds whose row is `infrastructure_error` and seeds with no
    row run again from the frozen copies, through the same evaluator entry and
    deadline enforcement as the original sweep. The manifest and the ledger
    identity are re-verified, never rewritten. Ends `full_complete` with all 50
    rows accounted and the supplementary seeds marked in `heldout_result.json`,
    or blocked at phase `heldout` again with everything preserved.
    """
    import heldout_supplement as supplement
    state_path = control / "campaign_state.json"
    state = json.loads(state_path.read_text()) if state_path.is_file() else None
    problems = supplement.eligibility(state)
    if problems:
        raise ValueError(f"cell cannot be supplemented: {problems}")
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    evaluation = control / "heldout"
    started = time.monotonic()
    entry = None

    def save():
        campaign.atomic_json(state_path, state)

    try:
        manifest = heldout_manifest(case, repo, task_dir, evaluation)
        if digest(evaluation / "heldout_manifest.json") != state["heldout_manifest"]["sha256"]:
            raise Blocked("heldout", "the held-out manifest changed since the sweep was frozen",
                          detail={"recorded": state["heldout_manifest"]["sha256"]})
        planned = supplement.plan(evaluation, HELDOUT_SEEDS)
        if planned["problems"] or not planned["seeds_to_run"]:
            raise ValueError(f"nothing to supplement or an unreadable ledger: {planned}")
        attempt = supplement.new_attempt(evaluation)
        env = bind_child_env(campaign.native_environment(
            case, repo, args.case, Path(case["claude_config_dir"])), case)
        record = supplement.prepare(evaluation, planned, attempt, state_path)
        deadline = heldout_deadline(args, case, planned["seeds_to_run"])
        entry = {"attempt": attempt.name, "record": str(attempt / "supplement.json"),
                 "seeds_to_run": record["seeds_to_run"],
                 "rerun_infrastructure_error": record["rerun_infrastructure_error"],
                 "missing": record["missing"], "status": "running", "started_at": now(),
                 "deadline_seconds": deadline, "driver_sha256": digest(Path(__file__)),
                 "prior": {k: state.get(k) for k in ("status", "blocker", "blocked", "heldout",
                                                     "finished_at", "total_seconds")}}
        state.setdefault("heldout_supplements", []).append(entry)
        state.update(status=supplement.RUNNING, blocker=None, blocked=None)
        save()
        heldout_args = ["--case", str(args.case)]
        if (evaluation / "heldout_state.json").is_file():
            heldout_args.append("--resume")  # Kept rows are never re-run.
        step = run_heldout(campaign, repo, env, attempt, heldout_args, deadline)
        entry.update(exit_code=step["exit_code"], timed_out=step["timed_out"],
                     elapsed_seconds=step["elapsed_seconds"])
        heldout = {"performed": True, "evaluate_invoked": True, "exit_code": step["exit_code"],
                   "timed_out": step["timed_out"],
                   "result_path": str(evaluation / "heldout_result.json"),
                   "elapsed_seconds": step["elapsed_seconds"], "deadline_seconds": deadline,
                   "supplement_attempt": attempt.name}
        state["heldout"] = heldout
        if step["timed_out"]:
            raise Blocked("heldout", "the supplementary held-out sweep exceeded its enforced "
                          "wall-clock deadline and its process group was stopped; every per-seed "
                          "row already recorded is preserved", detail=heldout)
        if step["exit_code"]:
            raise Blocked("heldout", f"the supplementary held-out sweep exited {step['exit_code']}",
                          detail={"stderr_tail": step.get("stderr_tail"), **heldout})
        heldout["accounting"] = check_heldout_rows(manifest, step["result"])
        entry["status"] = "complete"
        supplement.finish(attempt, status="complete", exit_code=step["exit_code"],
                          elapsed_seconds=step["elapsed_seconds"], deadline_seconds=deadline)
        report = supplement.annotate(evaluation, state["heldout_supplements"])
        heldout["supplementary_seeds"] = report["supplementary_seeds"]
        state["heldout_manifest"] = {"path": str(evaluation / "heldout_manifest.json"),
                                     "row_count": manifest["row_count"], "seeds": manifest["seeds"],
                                     "selected_bundle": manifest["selected_bundle"],
                                     "sha256": digest(evaluation / "heldout_manifest.json")}
        if "judge" in case:
            state["heldout_judge"] = judge_heldout_seeds(case, repo, env, attempt)
        state["status"] = TERMINAL
    except Blocked as exc:
        if entry is None:
            raise  # Nothing changed yet: the cell keeps its original record.
        state.update(status="blocked", blocked=exc.payload,
                     blocker=f"{exc.payload['phase']}: {exc.payload['reason']}")
    except Exception as exc:
        if entry is None:
            raise
        state.update(status="blocked",
                     blocked={"phase": supplement.RUNNING,
                              "reason": f"{type(exc).__name__}: {exc}", "state_preserved": True,
                              "automatic_resume": False, "recovery": "manual", "detail": None},
                     blocker=f"{type(exc).__name__}: {exc}")
    if entry is not None and entry["status"] != "complete":
        entry["status"] = "blocked"
        supplement.finish(Path(entry["record"]).parent, status="blocked", blocked=state["blocked"])
    state["finished_at"] = now()
    state["supplement_seconds"] = round(time.monotonic() - started, 1)
    save()
    print(json.dumps({k: state.get(k) for k in ("cell", "status", "blocker", "blocked")}), flush=True)
    return int(state["status"] != TERMINAL)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--deadline-development", type=int, default=full_deadlines.DEVELOPMENT)
    parser.add_argument("--deadline-heldout", type=int, default=None,
                        help="override; default: full_deadlines.heldout_deadline over the seeds to run")
    parser.add_argument("--startup-session", help="resume only the prelaunch coordinator with no admitted primary/trials")
    parser.add_argument("--native-smoke", action="store_true", help="checkpoint same native worker after one seed51 startup attempt")
    parser.add_argument("--heldout-supplement", action="store_true",
                        help="finish a preserved interrupted held-out sweep: run only its missing seeds")
    args = parser.parse_args(argv)
    case = json.loads(args.case.read_text())
    repo, control = Path(case["sim"]), Path(case["control"])
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign
    # Corrected turn-aware transport lives outside the frozen baseline source.
    campaign.run_native_cc = fixed_transport.run_native_cc
    scope.apply(campaign, case)
    lock = (control / "recovery.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    campaign.verify_runtime(case, repo)
    problems = full_deadlines.check_case(case)
    if problems:
        raise ValueError("; ".join(problems))
    if args.heldout_supplement:
        return run_heldout_supplement(args, case, repo, control, campaign)
    folder = control / ("campaign-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    folder.mkdir()
    state_path = control / ("native_smoke_state.json" if args.native_smoke else "campaign_state.json")
    if state_path.exists():
        raise ValueError("This cell already has a campaign record; preserve and inspect it")
    prior_path = control / "native_smoke_state.json"
    prior = json.loads(prior_path.read_text()) if not args.native_smoke and prior_path.exists() else None
    if prior and prior.get("status") != "native_smoke_checkpoint":
        raise ValueError("native smoke is incomplete; preserve it for outer recovery")
    transcripts = [Path(p) for p in prior["solver_transcripts"]] if prior else []
    session, worker = (prior["native_session"], prior["native_worker"]) if prior else (None, None)
    guard = Path(__file__).with_name("cell_read_guard.py").resolve()
    guard_hash = digest(guard)
    python = str(repo / ".venv-libero/bin/python3")
    settings = runtime_limits.install(campaign.native_settings(case, repo, args.case), case)
    provider = verify_local_provider(case, settings)
    hook = shlex.join([python, str(guard),
                       "--case", str(args.case), "--audit", str(folder / "read-guard-audit.jsonl")])
    settings["hooks"]["PreToolUse"].append({
        "matcher": "Read|Glob|Grep|Write|Edit|MultiEdit|Bash",
        "hooks": [{"type": "command", "command": hook, "timeout": 60}]})
    settings["hooks"]["PreToolUse"].append({
        "matcher": "Bash", "hooks": [{"type": "command", "timeout": 60,
        "command": shlex.join([python,
                               str(Path(__file__).with_name("infra_guard.py")),
                               "--case", str(args.case)])}]})
    assignment_guard = Path(__file__).with_name("pilot_assignment_guard.py").resolve()
    settings["hooks"]["PreToolUse"].append({
        "matcher": "Agent|Task|Bash", "hooks": [{"type": "command", "timeout": 60,
        "command": shlex.join(pilot_assignment_guard.hook_command(
            python, str(Path(__file__).with_name("canonical_assignment.py").resolve()), str(args.case),
            str(control / "worker-prompt.md"),
            str(control / "native/assignment-state.json"),
            str(folder / "assignment-audit.jsonl")))}]})
    config = Path(case["claude_config_dir"])
    campaign.atomic_json(config / "settings.json", settings)
    env = bind_child_env(campaign.native_environment(case, repo, args.case, config), case)
    task_dir = repo / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    evaluation = control / "heldout"
    started = time.monotonic()
    state = {"cell": case["id"], "condition": case["condition"], "started_at": now(),
             "status": "preflight", "blocker": None, "blocked": None,
             "campaign_dir": str(folder), "mode": "full_study", "provider": provider,
             "dev_seeds": sorted(int(s) for s in case["dev_seeds"]),
             "heldout_seeds": list(HELDOUT_SEEDS),
             "deadlines": {**full_deadlines.summary(),
                           "development_deadline_seconds": args.deadline_development,
                           "heldout_deadline_seconds": (args.deadline_heldout if args.deadline_heldout
                                                        is not None else full_deadlines.HELDOUT)},
             "guard_sha256": guard_hash, "driver_sha256": digest(Path(__file__)),
             "assignment_guard_sha256": digest(assignment_guard),
             "canonical_assignment_sha256": digest(Path(__file__).with_name("canonical_assignment.py")),
             "ownership_sha256": digest(Path(__file__).with_name("output_ownership.py")),
             "scope_sha256": digest(Path(__file__).with_name("two_task_scope.py")),
             "turns": []}

    def save():
        campaign.atomic_json(state_path, state)
        campaign.atomic_json(folder / "state.json", state)

    save()
    try:
        # Ownership first: before a model, a service or a simulator exists.
        state["output_ownership"] = output_ownership.verify(case, repo)
        save()
        state["perception"] = campaign.perception_ready(case)
        state["probe"] = campaign.probe(case, control, env, settings)
        state["capacity"] = verify_capacity(case, settings, state["probe"])
        prompt_path, coordinator_path = campaign.write_prompts(case, repo, control)
        state["init"] = campaign.protocol(case, repo, env, folder, "init", "status" if (prior or args.startup_session) else "init")
        if state["init"]["exit_code"]:
            raise Blocked("init", "fresh initialization failed", detail=state["init"])
        if case.get("diagnostic_import"):
            from foundation_import import import_diagnostic
            state["diagnostic_import"] = import_diagnostic(case, task_dir)
        else:
            state["diagnostic_import"] = {"imported": False, "note": (
                "no preflight retry imported; aspire-v1 development budget" if budgeted(case)
                else "no preflight retry imported; three retries per seed")}
        save()

        state["pre_development_check"] = campaign.protocol(
            case, repo, env, folder, "check-before-solver", "check")
        save()
        require_development_possible(state["pre_development_check"])

        # --- development: wall-clock watchdog, no turn ceiling -----------------
        deadline = started + args.deadline_development
        turn = 0
        stall_history = []
        diagnostic_round = False

        cleanup_receipt = None
        ledger_path = task_dir / "development_state.json"
        # CC_TASK_9: the primary's generations. Generation 1 receives the frozen
        # assignment; generation N those bytes plus the driver's handoff. Lineage
        # and the received-assignment check are per generation (one session each).
        generation_dir = control / "native/worker-generations"
        guard_state = control / "native/assignment-state.json"
        generation = {"generation": 1, "assignment": prompt_path, "transcripts": []}
        state["worker_generations"] = [{"generation": 1, "reason": "initial", "worker": worker,
                                        "session": session, "dispatch_turn": 0, "status": "initial"}]
        recovery = None      # the open recovery round: the pending generation
        failed_rounds = 0    # B2: consecutive failed rounds without a ledger change
        infra_seen = {"evidence": None, "rounds": 0}
        dispositions = []    # G6: rows the outer driver disposed of itself
        api_failures = {"worker": None, "count": 0}  # G5: immediate failures carried across turns
        next_round = ""      # text the next coordinator prompt carries (B1/B2)

        def budget_phase(check):
            result = check.get("result") if isinstance(check, dict) else None
            return ((result or {}).get("development_budget") or {}).get("phase")

        def open_recovery(previous_worker, evidence_path, reason="unresumable_worker"):
            """Write generation N's assignment and open the hook's one-dispatch request."""
            number = generation["generation"] + 1
            text, index = worker_recovery.handoff(
                task_dir=task_dir, check=state.get("check"), generation=number,
                previous_worker=previous_worker, remaining_seconds=deadline - time.monotonic(), turn=turn,
                reason=reason)
            generation_dir.mkdir(parents=True, exist_ok=True)
            assignment = generation_dir / f"generation-{number:02d}-assignment.md"
            assignment.write_text(prompt_path.read_text() + text)
            campaign.atomic_json(generation_dir / f"generation-{number:02d}-handoff.json", index)
            entry = {"generation": number, "reason": reason, "previous_worker": previous_worker,
                     "worker": None, "session": None, "detected_turn": turn, "evidence": str(evidence_path),
                     "assignment": str(assignment),
                     "assignment_sha256": hashlib.sha256(assignment.read_text().encode()).hexdigest(),
                     "handoff_index": str(generation_dir / f"generation-{number:02d}-handoff.json"),
                     "ledger_sha256": worker_recovery.ledger_digest(ledger_path),
                     "ledger_rows": index["ledger_rows"], "attempts_spent": index["attempts_spent"],
                     "opened_at": now(), "dispatch_rounds": [], "status": "open"}
            state["worker_generations"].append(entry)
            return {"generation": number, "previous_worker": previous_worker, "assignment": assignment,
                    "transcripts": [], "entry": entry, "reason": reason}

        def request_dispatch(pending):
            data = pilot_assignment_guard.load_state(guard_state)
            data["recovery_request"] = {"status": "open", "generation": pending["generation"],
                                        "assignment": str(pending["assignment"]),
                                        "assignment_sha256": pending["entry"]["assignment_sha256"],
                                        "previous_worker": pending["previous_worker"], "turn": turn,
                                        "opened_at": now()}
            pilot_assignment_guard.save_state(guard_state, data)

        def close_request():
            data = pilot_assignment_guard.load_state(guard_state)
            request = data.get("recovery_request") or {}
            if request.get("status") == "open":
                request["status"] = "closed_without_dispatch"
                pilot_assignment_guard.save_state(guard_state, data)
            return request

        def dispose_infrastructure(evidence):
            """G6: apply the protocol's close-infra disposition to every ended row; True if all settled.

            Only ended infrastructure rows; a screening blocker or a running row is
            not the driver's to settle. Bounded by INFRA_DISPOSITIONS per cell.
            """
            rows = evidence["infrastructure_errors"]
            if (not rows or evidence["unresolved_screening_blockers"]
                    or worker_recovery.running_rows(ledger_path)
                    or len(dispositions) + len(rows) > INFRA_DISPOSITIONS):
                return False
            for index, directory in enumerate(rows):
                step = campaign.protocol(case, repo, env, folder, f"close-infra-{turn:02d}-{index}",
                                         "close-infra", "--directory", directory, "--reason",
                                         f"outer driver: unresolved after {INFRA_ROUNDS} infrastructure rounds; "
                                         "settled by the protocol's own rule, nothing refunded or re-run")
                result = step.get("result") or {}
                record = {"turn": turn, "directory": directory, "exit_code": step["exit_code"],
                          "disposition": ((result.get("closed") or {}).get("recovery") or {}).get("disposition"),
                          "status": (result.get("closed") or {}).get("status"),
                          "error": result.get("error")}
                dispositions.append(record)
                state.setdefault("infrastructure_dispositions", []).append(record)
                save()
                if step["exit_code"]:
                    return False
            return True

        def finish_development(reason):
            # Only the outer driver calls this, after the solver has returned.
            # The protocol lock + unresolved-row checks refuse an active or lost batch.
            if args.native_smoke or not budgeted(case):
                raise Blocked("development", "development ended before ready", detail={"reason_code": reason})
            blocker = fault(case)
            if blocker:
                raise Blocked("infrastructure", "infrastructure failure prevents final selection", detail=blocker)
            campaign.verify_runtime(case, repo)
            if digest(guard) != guard_hash:
                raise Blocked("guard", "the read guard changed during the run")
            output_ownership.verify(case, repo)
            result = campaign.protocol(case, repo, dict(env, ASPIRE_OUTER_DEVELOPMENT_END="1"),
                                       folder, "end-development-" + reason,
                                       "end-development", "--reason", reason,
                                       *(["--cleanup-receipt", str(cleanup_receipt)]
                                         if reason == "deadline" and cleanup_receipt else []))
            state["development_end"] = result
            save()
            if result["exit_code"]:
                raise Blocked("development", "no valid complete candidate can be finalized at development end",
                              detail={"reason_code": reason, "selection": result})

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                finish_development("deadline")
                break
            if recovery:
                prompt = (coordinator_path.read_text()
                          + recovery_suffix(recovery["generation"], recovery["previous_worker"],
                                            recovery["reason"]))
                request_dispatch(recovery)
            elif session:
                prompt = continuation(worker, remaining, case)
                if prior:
                    prompt += "\nThis SAME worker authored the current cell seed51 native smoke. Reuse its exact initial bundle and completed snapshot. Alias a completed identical smoke as initial51 if eligible; do not re-run it, refund attempts or reinitialize. Continue the remaining original development work and reports.\n"
            else:
                prompt = coordinator_path.read_text() + round_suffix(case)
            prompt += next_round
            next_round = ""
            if diagnostic_round:
                prompt += ("\nNEW DIAGNOSTIC/STRATEGY ROUND: the previous coordinator rounds made no "
                           "recorded progress. Resume the SAME worker, inspect existing diagnostics and "
                           "change strategy rather than repeating the same report/check. Preserve all "
                           "ledger entries, attempts, the original cumulative development deadline, API "
                           "and perception requirements. Infrastructure/API/permission failures must be "
                           "reported as blockers, not retried as strategy stagnation.\n")
                diagnostic_round = False
            if args.native_smoke:
                prompt = ("STARTUP WORK STAGE: Read the worker assignment and dispatch one background general-purpose primary Agent with prompt set to the literal ASPIRE_FROZEN_PRIMARY_ASSIGNMENT; the native hook supplies the complete frozen file verbatim before the worker starts. Immediately SendMessage to that SAME worker: 'For this first invocation, execute only the public snapshot, fresh task-specific initial policy/world generation, and ONE recorded smoke on seed51. Pause and return a checkpoint after that attempt, before the other development seeds or repairs. This is a startup stage boundary, not an action cap or extra budget. Original p2 runtime perception/state/prediction/decision and p3 search-control requirements apply. Do not select/finalize or write full findings yet.' After its completion notification, report the actual checkpoint and stop. The next invocation resumes the SAME worker and remaining original work. No extra strategy, geometry or old code may be supplied.\n" + prompt)
            (folder / f"turn-{turn:02d}-prompt.md").write_text(prompt)
            transcript = folder / f"turn-{turn:02d}.stdout.jsonl"
            command = campaign.native_command(case, prompt, settings,
                                              agents=campaign.worker_agent(case, prompt_path))
            if (session or args.startup_session) and not recovery:
                command.extend(["--resume", session or args.startup_session])
            ledger_before = worker_recovery.ledger_digest(ledger_path)
            state.update(status="solver_running", turn=turn)
            save()
            from cell_process_tree import CellProcessTree
            tracker, tracking_errors, tracking_threads = [], [], []
            tracking_done = threading.Event()
            def track_solver(process):
                monitor(process, case, folder / "infra-stop.json")
                tree = CellProcessTree(process)
                tracker.append(tree)
                def collect():
                    while not tracking_done.wait(.05):
                        try:
                            tree.collect()
                        except Exception as exc:
                            tracking_errors.append(str(exc))
                            return
                thread = threading.Thread(target=collect, daemon=True)
                tracking_threads.append(thread)
                thread.start()
            try:
                code = campaign.run_native_cc(
                command, cwd=repo, env=env, stdout_path=transcript,
                stderr_path=folder / f"turn-{turn:02d}.stderr.log",
                # The solver turn may not outlive the development phase itself.
                timeout=min(case["campaign_timeout"], max(1, int(deadline - time.monotonic()))),
                on_start=track_solver)
            except subprocess.TimeoutExpired as exc:
                # Native transport raises when a real deadline interrupts work.
                # Continue through owned cleanup, provenance and protocol checks;
                # only end-development may select an eligible complete candidate.
                code = 124
                state["solver_timeout"] = {"turn": turn, "timeout_seconds": exc.timeout,
                                           "transcript": str(transcript)}
            finally:
                tracking_done.set()
                for thread in tracking_threads:
                    thread.join(timeout=10)
                    if thread.is_alive():
                        tracking_errors.append("descendant collector did not stop")
                if tracker:
                    cleaned = tracker[0].cleanup()
                    cleanup_receipt = folder / f"turn-{turn:02d}-cleanup.json"
                    campaign.atomic_json(cleanup_receipt, cleaned)
                if tracking_errors:
                    raise Blocked("cleanup", "solver descendant tracking failed", detail=tracking_errors)
            transcripts.append(transcript)
            current = recovery or generation
            current["transcripts"].append(transcript)
            # Some transports return zero after emitting a structured API/tool
            # failure. It is a solver failure, never phase-A strategy stagnation.
            if transcript.is_file():
                for line in transcript.read_text().splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("type") == "result" and record.get("is_error") is True:
                        state["solver_reported_error"] = {"subtype": record.get("subtype"),
                                                          "transcript": str(transcript)}
                        code = code or 1
            state["coordinator_exit_code"] = code
            blocker = fault(case)
            if blocker:
                # Stops before any further trial spending and before held-out.
                raise Blocked("infrastructure", "an infrastructure failure is recorded in a "
                              "ledger; stopping without resetting attempts and without "
                              "starting the held-out sweep", detail=blocker)
            if code in {-signal.SIGINT, -signal.SIGTERM, 128 + signal.SIGINT, 128 + signal.SIGTERM}:
                raise Blocked("cancelled", f"native coordinator cancelled ({code})")
            if code and (args.native_smoke or not budgeted(case)):
                raise Blocked("solver", f"native coordinator exited {code}",
                              detail={"turn": turn, "transcript": str(transcript)})
            ledger_changed = worker_recovery.ledger_digest(ledger_path) != ledger_before
            try:
                found = identity(current["transcripts"], Path(current["assignment"]).read_text())
            except Blocked:
                # Tolerated only where no primary of this generation can exist yet
                # and nothing was recorded: a recovery round that dispatched none,
                # or a failed first turn before the primary was admitted.
                if ledger_changed or not (recovery or (
                        code and pilot_assignment_guard.load_state(guard_state).get("primary") is None)):
                    raise
                found = None
            dispatch_failed = False
            if recovery:
                request = close_request()
                recovery["entry"]["dispatch_rounds"].append({"turn": turn, "dispatched": bool(found),
                                                             "request": request})
                if found:
                    session, worker = found
                    recovery["entry"].update(worker=worker, session=session, dispatch_turn=turn,
                                             status="dispatched")
                    generation, recovery = recovery, None
                    del generation["entry"]
                    stall_history.clear()
                else:
                    dispatch_failed = True
                    current["transcripts"].clear()
            elif found:
                session, worker = found
                state["worker_generations"][-1].update(worker=worker, session=session)
            if found and "received_assignment" not in state["worker_generations"][-1]:
                # Once per generation: the child's first input cannot change later,
                # and a later turn need not find a transcript native CC lost.
                received = verify_received_assignment(config, session, worker, generation["assignment"])
                campaign.atomic_json(folder / "actual-worker-prompt-receipt.json", received)
                state["actual_worker_prompt_receipt"] = received
                state["worker_generations"][-1]["received_assignment"] = received
            if found:
                campaign.atomic_json(folder / "lineage.json",
                                     dict(audit_assignment(generation["transcripts"],
                                                           Path(generation["assignment"]).read_text()),
                                          generation=generation["generation"]))
            campaign.verify_runtime(case, repo)
            if digest(guard) != guard_hash:
                raise Blocked("guard", "the read guard changed during the run")
            output_ownership.verify(case, repo)
            check = campaign.protocol(case, repo, env, folder, f"check-{turn:02d}", "check")
            state["check"] = check
            state["turns"].append({"turn": turn, "exit_code": code,
                                   "check_exit_code": check["exit_code"],
                                   "elapsed_seconds": round(time.monotonic() - started, 1),
                                   "generation": generation["generation"], "worker": worker,
                                   "ledger_changed": ledger_changed})
            save()
            if args.native_smoke:
                ledger = json.loads((task_dir / "development_state.json").read_text())
                attempts = [r for r in ledger["trials"] if r.get("spends_retry")]
                if len(attempts) != 1 or attempts[0]["seed"] != 51:
                    raise Blocked("native_smoke", "startup must preserve exactly one charged seed51 attempt", detail=attempts)
                state.update(status="native_smoke_checkpoint", native_session=session, native_worker=worker,
                             solver_transcripts=[str(p) for p in transcripts], smoke_attempt=attempts[0],
                             elapsed_seconds=round(time.monotonic() - started, 1))
                save()
                return 0
            if check["exit_code"] == 0:
                break
            # CC_TASK_9: can the primary still be resumed? Structured stream evidence only.
            unresumable, lost = None, "unresumable_worker"
            if worker and not dispatch_failed:
                evidence = worker_recovery.resume_evidence(transcript, worker)
                evidence.update(turn=turn, generation=generation["generation"],
                                running_rows=worker_recovery.running_rows(ledger_path),
                                ledger_changed_during_turn=ledger_changed)
                if evidence["sends"]:
                    campaign.atomic_json(folder / f"turn-{turn:02d}-worker-resume.json", evidence)
                if evidence["unresumable"] and not evidence["running_rows"]:
                    unresumable = folder / f"turn-{turn:02d}-worker-resume.json"
                # CC_TASK_12 G5: resumed, then failed with an API error before doing anything.
                carried = api_failures["count"] if api_failures["worker"] == worker else 0
                failure = worker_recovery.failure_evidence(transcript, worker, carried)
                failure.update(turn=turn, generation=generation["generation"],
                               running_rows=evidence["running_rows"],
                               ledger_changed_during_turn=ledger_changed)
                if failure["runs"]:
                    campaign.atomic_json(folder / f"turn-{turn:02d}-worker-failure.json", failure)
                    api_failures = {"worker": worker, "count": failure["immediate_failures"]}
                if unresumable is None and failure["dead"] and not failure["running_rows"]:
                    unresumable, lost = folder / f"turn-{turn:02d}-worker-failure.json", "worker_api_failure"
            failed = bool(code) or dispatch_failed or unresumable is not None
            failed_rounds = failed_rounds + 1 if failed and not ledger_changed else 0
            if failed:
                state.setdefault("failed_rounds", []).append({
                    "turn": turn, "exit_code": code, "dispatch_failed": dispatch_failed,
                    "unresumable": str(unresumable) if unresumable else None,
                    "ledger_changed": ledger_changed, "consecutive_without_ledger_change": failed_rounds})
                save()
            if time.monotonic() >= deadline:
                finish_development("deadline")
                break
            if failed_rounds >= SOLVER_FAILURE_ROUNDS:
                if budget_phase(check) == "B":
                    finish_development("solver_exit")
                    break
                raise Blocked("solver", f"{SOLVER_FAILURE_ROUNDS} consecutive failed coordinator rounds "
                              "without a ledger change; phase A cannot be finalized",
                              detail={"reason_code": "solver_failures", "rounds": state["failed_rounds"][-3:]})
            if dispatch_failed:
                turn += 1  # The same generation is requested again in a new session.
                continue
            if unresumable:
                recovery = open_recovery(worker, unresumable, lost)
                diagnostic_round = False
                save()
                turn += 1
                continue
            if code:
                next_round = solver_failure_round(code, failed_rounds)
                turn += 1
                continue
            try:
                transition = require_development_possible(check, stall_history)
            except Blocked as exc:
                detail = exc.payload.get("detail") or {}
                if detail.get("reason_code") == "no_progress":
                    finish_development("no_progress")
                    break
                if not detail.get("closeout"):
                    raise
                try:
                    finish_development("terminal")  # B3: settle a terminal phase B first.
                except Blocked as closeout:
                    raise Blocked("development", exc.payload["reason"] + "; end-development could not "
                                  "settle it", detail={**detail, "closeout": closeout.payload}) from None
                break
            if transition == "infrastructure_round":
                progress = check["result"]["progress"]
                evidence = {"infrastructure_errors": progress.get("infrastructure_errors") or [],
                            "unresolved_screening_blockers": [
                                {k: b.get(k) for k in ("phase", "seed", "reason", "bundle_sha256")}
                                for b in progress.get("unresolved_screening_blockers") or []]}
                key = json.dumps(evidence, sort_keys=True)
                infra_seen = {"evidence": key,
                              "rounds": infra_seen["rounds"] + 1 if infra_seen["evidence"] == key else 1}
                state.setdefault("infrastructure_rounds", []).append(
                    {"after_turn": turn, "evidence": evidence, "same_evidence_rounds": infra_seen["rounds"]})
                save()
                if infra_seen["rounds"] > INFRA_ROUNDS and dispose_infrastructure(evidence):
                    # G6: the outer driver settled every row itself; development continues.
                    infra_seen = {"evidence": None, "rounds": 0}
                    next_round = disposed_round(dispositions[-len(evidence["infrastructure_errors"]):])
                    turn += 1
                    continue
                if infra_seen["rounds"] > INFRA_ROUNDS:
                    if budget_phase(check) == "B":
                        finish_development("no_progress")
                        break
                    raise Blocked("infrastructure", f"the same unresolved infrastructure evidence survived "
                                  f"{INFRA_ROUNDS} infrastructure rounds", detail={
                                      "reason_code": "unresolved_infrastructure", "evidence": evidence})
                next_round = infrastructure_round(evidence)
                turn += 1
                continue
            infra_seen = {"evidence": None, "rounds": 0}
            if transition == "diagnostic_round":
                diagnostic_round = True
                state.setdefault("diagnostic_rounds", []).append({"after_turn": turn,
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "reason": "no_progress"})
                save()
                # Start a new fingerprint window without changing any ledger/budget.
                stall_history.clear()
            if check["exit_code"] == 0:
                break
            turn += 1

        switches = case.get("method_switches") or {}
        if switches.get("prefreeze_resweep") and not args.native_smoke:
            # CC_TASK_14 M2(b): the outer selection always runs, then the re-sweep.
            if "development_end" not in state:
                finish_development("ready")
            state["status"] = "prefreeze_resweep"
            save()
            campaign.verify_runtime(case, repo)
            output_ownership.verify(case, repo)
            state["prefreeze_resweep"] = campaign.protocol(
                case, repo, dict(env, ASPIRE_OUTER_DEVELOPMENT_END="1"), folder, "prefreeze-resweep", "resweep")
            save()
            if state["prefreeze_resweep"]["exit_code"]:
                raise Blocked("development", "the pre-freeze re-sweep could not settle a selection",
                              detail={"reason_code": "prefreeze_resweep", "step": state["prefreeze_resweep"]})

        final_args = ["finalize"]
        for transcript in transcripts:
            final_args.extend(["--transcript", str(transcript)])
        state["finalize"] = campaign.protocol(case, repo, env, folder, "finalize", *final_args)
        if state["finalize"]["exit_code"]:
            raise Blocked("finalize", "development finalization failed; inspect the recorded "
                          "outer result", detail=state["finalize"])
        state.update(solver_transcripts=[str(p) for p in transcripts],
                     status="development_complete",
                     development_seconds=round(time.monotonic() - started, 1))
        save()

        # --- freeze ------------------------------------------------------------
        manifest = heldout_manifest(case, repo, task_dir, evaluation)
        state["heldout_manifest"] = {"path": str(evaluation / "heldout_manifest.json"),
                                     "row_count": manifest["row_count"],
                                     "seeds": manifest["seeds"],
                                     "selected_bundle": manifest["selected_bundle"],
                                     "sha256": digest(evaluation / "heldout_manifest.json")}
        state["status"] = "frozen"
        save()

        # --- unseen measurement (CC_TASK_14 M2(d)): outer-only, after the freeze ----
        unseen = (case.get("method_switches") or {}).get("unseen_seeds")
        if unseen:
            unseen_seconds = full_deadlines.heldout_deadline(len(unseen), case["trial_timeout"],
                                                             case.get("heldout_parallel", 1))
            state["status"] = "unseen_running"
            state["deadlines"]["unseen_deadline_seconds"] = unseen_seconds
            save()
            step = run_unseen(campaign, repo, env, folder, args.case, unseen_seconds)
            report = step["result"] if isinstance(step["result"], dict) else {}
            state["unseen"] = {"performed": True, "seeds": list(unseen),
                               "exit_code": step["exit_code"], "timed_out": step["timed_out"],
                               "result_path": str(evaluation / "unseen/unseen_result.json"),
                               "counts": report.get("counts"), "success_rate": report.get("success_rate"),
                               "per_seed": report.get("per_seed"),
                               "unusable_seeds": report.get("unusable_seeds"),
                               "elapsed_seconds": step["elapsed_seconds"], "deadline_seconds": unseen_seconds,
                               "frozen_bundle_sha256": manifest.get("selected_bundle_sha256"),
                               "visibility": "outer only; never returned to a solver; does not change the freeze"}
            save()

        # --- held-out: outer-only, seeds 1-50 ----------------------------------
        heldout_args = ["--case", str(args.case)]
        if (evaluation / "heldout_state.json").is_file():
            heldout_args.append("--resume")  # Recorded seeds are never re-run.
        # The sum of the per-seed bounds of the seeds still to run: a legal slow
        # sweep (every seed inside its trial timeout) is never cut short.
        heldout_seconds = heldout_deadline(args, case, heldout_pending(evaluation))
        state["status"] = "heldout_running"
        state["deadlines"]["heldout_deadline_seconds"] = heldout_seconds
        save()
        # Enforced here, not checked afterwards: the evaluator has no timeout of
        # its own, so a stall would otherwise never end.
        step = run_heldout(campaign, repo, env, folder, heldout_args, heldout_seconds)
        state["heldout"] = {"performed": True, "evaluate_invoked": True,
                            "exit_code": step["exit_code"], "timed_out": step["timed_out"],
                            "result_path": str(evaluation / "heldout_result.json"),
                            "elapsed_seconds": step["elapsed_seconds"],
                            "deadline_seconds": heldout_seconds}
        save()
        if step["timed_out"]:
            raise Blocked("heldout", "the held-out sweep exceeded its enforced wall-clock "
                          "deadline and its process group was stopped; every per-seed row "
                          "already recorded is preserved", detail=state["heldout"])
        if step["exit_code"]:
            raise Blocked("heldout", f"the held-out sweep exited {step['exit_code']}",
                          detail={"stderr_tail": step.get("stderr_tail")})
        state["heldout"]["accounting"] = check_heldout_rows(manifest, step["result"])
        # Post hoc, outer-only: the same VLM judge over every graded held-out
        # trial's keyframes, so every arm gets a judge-vs-environment 2x2 on the
        # frozen seeds. Never a grade; a judge failure is recorded, not fatal.
        # A case with no judge configured (the prediction-contract study) skips it.
        if "judge" in case:
            state["status"] = "heldout_judging"
            save()
            state["heldout_judge"] = judge_heldout_seeds(case, repo, env, folder)
        else:
            state["heldout_judge"] = {"performed": False, "reason": "case configures no judge"}
        state["status"] = TERMINAL
    except Blocked as exc:
        state.update(status="blocked", blocked=exc.payload,
                     blocker=f"{exc.payload['phase']}: {exc.payload['reason']}")
    except Exception as exc:
        state.update(status="blocked",
                     blocked={"phase": state.get("status", "unknown"),
                              "reason": f"{type(exc).__name__}: {exc}",
                              "state_preserved": True, "automatic_resume": False,
                              "recovery": "manual", "detail": None},
                     blocker=f"{type(exc).__name__}: {exc}")
    state["finished_at"] = now()
    state["total_seconds"] = round(time.monotonic() - started, 1)
    save()
    print(json.dumps({k: state[k] for k in
                      ("cell", "status", "blocker", "blocked", "campaign_dir")}), flush=True)
    return int(state["status"] != TERMINAL)


if __name__ == "__main__":
    raise SystemExit(main())
