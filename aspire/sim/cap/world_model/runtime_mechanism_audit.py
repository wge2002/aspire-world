"""Read-only p2 runtime dataflow evidence. Never a task-success/admission gate."""
from __future__ import annotations

import json
from pathlib import Path

PREDICTION_QUERIES = {"prediction_feedback", "prediction_checks", "prediction_summary"}
ACTION_EVIDENCE = "action_evidence"
LIMITS = [
    "Action evidence links exact runtime answers, a policy-authored decision record and "
    "the next completed public call. The branch rationale is declared by the policy.",
    "Static dependency candidates alone are inconclusive; they are not causal proof.",
    "Controlled feedback variation proves branching only for the engineering fixture "
    "policy tested. It does not establish real solver compliance, perception or task benefit.",
    "Evidence provenance is bookkeeping, not proof of correct association or dynamics.",
]


def audit_events(path, static_use=None, policy_path=None):
    path = Path(path)
    try:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return {"status": "inconclusive", "reason": "runtime trace unavailable", "limits": LIMITS}
    if static_use is None:
        policy = Path(policy_path) if policy_path is not None else path.parent.parent / "code.py"
        if policy.is_file():
            try:
                from .world_use_audit import audit, prediction_use, read_trace
                static_use = prediction_use(audit(policy.read_text(), trace=read_trace(path)),
                                            read_trace(path), query_names=PREDICTION_QUERIES)
            except (OSError, ValueError, RecursionError):
                pass  # Missing static coverage cannot establish logging-only.
    queries = [r for r in rows if r.get("event") == "world_query"
               and r.get("name") in PREDICTION_QUERIES]
    writes = [r["record"] for r in rows if r.get("event") == "state_write"]
    checks = [r for r in rows if r.get("event") == "prediction_check"]
    decisions = [r for r in rows if r.get("event") == "policy_decision"]
    actions = [r for r in rows if r.get("event") == "policy_decision_action"]
    completed = {r["api_call"]: r for r in rows if r.get("event") == "public_api"}
    chains = []
    for decision in decisions:
        feedback = decision.get("feedback", {})
        if not any(q.get("result") == feedback for q in queries):
            continue
        for action in actions:
            receipt = completed.get(action["api_call"])
            if action["decision_id"] != decision["decision_id"] or not receipt or receipt.get("error"):
                continue
            relevant = []
            for result in feedback.get("results", []):
                if result.get("status") not in {"match", "mismatch", "unknown"}:
                    continue
                if not any(c.get("prediction_call") == result.get("prediction_call") and
                           c.get("entity") == result.get("entity") and c.get("fact") == result.get("fact") and
                           c.get("status") == result.get("status") and c.get("evidence_id") == result.get("evidence_id")
                           for c in checks):
                    continue
                if not any(w.get("entity") == result.get("entity") and w.get("fact") == result.get("fact") and
                           result.get("evidence_id") in w.get("evidence_ids", []) for w in writes):
                    continue
                if not any(f.get("entity") == result.get("entity") and f.get("fact") == result.get("fact") and
                           any(q.get("name") == "state" and q.get("result") == f for q in rows)
                           for f in decision.get("facts", [])):
                    continue
                relevant.append(result)
            if relevant:
                chains.append({"decision_id": decision["decision_id"], "branch": decision["branch"],
                               "read_id": feedback.get("read_id"), "results": relevant,
                               "next_api_call": action["api_call"], "next_function": action["function"],
                               "next_args": action["args"], "next_kwargs": action["kwargs"]})
    if chains:
        status = ACTION_EVIDENCE
    elif not queries:
        status = "no_query"
    elif static_use and static_use.get("status") == "logging_only" and not decisions:
        status = "logging_only"
    else:
        status = "inconclusive"
    return {"revision": "p2-runtime", "status": status, "state_writes": len(writes),
            "supported_predictions": len([r for r in rows if r.get("event") == "prediction_committed"]),
            "prediction_queries": len(queries), "policy_decisions": len(decisions),
            "action_chains": chains, "causal_claim": False, "limits": LIMITS}
