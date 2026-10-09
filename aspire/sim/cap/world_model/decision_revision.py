"""Opt-in closed-loop revision: online decisions grounded in public evidence.

The executable-world revision ``r1`` deliberately kept ``done()`` observational:
its foundation prompt told cells not to build recovery or termination on top of
it. That stays the default and this module never changes it. What it adds is a
*separate, explicitly opted-in* revision, ``closed_loop_revision="r1"``, for
studies that want world judgments to drive online branching.

Three kinds of statement are kept apart:

``observed``   derived from a real public observation in this episode.
``predicted``  a modelled guess. It may guide where to look or move next, and
               may never establish a grasp target, measured progress, or goal
               completion.
``unknown``    no admissible current evidence. Not ``false``, never success:
               it calls for observing or relocalizing.

Nothing here is a robot controller and nothing here decides a task. The
generated policy owns every action and the generated world owns every predicate,
threshold and relation. This module supplies only what the harness can check:
branch admissibility against verifiable evidence, and a conservative static
screen that reports concerns without claiming to prove runtime causality.
"""
from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path

REVISION = "r1"
PROFILE = "judgment"
CONDITION = "C"

#: Ablation arms this revision supports. ``no_self_eval`` deletes the online goal
#: outright, so requiring an online decision contract at the same time is a
#: contradiction rather than an ablation: refuse it instead of quietly turning
#: the goal back on or quietly ignoring the flag.
ARMS = frozenset({"full", "no_rehearsal"})

#: ``stop``     the only branch that asserts the goal.
#: ``observe``  the state is not established: reobserve, relocalize, search.
#: ``recover``  grounded failure or lack of progress: change the plan.
#: ``continue`` grounded "not yet": carry on with the plan.
BRANCHES = frozenset({"stop", "observe", "recover", "continue"})

#: Which branches each verdict admits. ``true`` admits only ``stop`` and
#: ``unknown`` only ``observe``, so no contradictory or missing branch can leave
#: a policy holding a ``true`` it might stop on, or an ``unknown`` it might act
#: on as though the state were established.
ADMITS = {"true": frozenset({"stop"}),
          "false": frozenset({"recover", "continue", "observe"}),
          "unknown": frozenset({"observe"})}

#: Origins of a recorded evaluation. The runtime tags each one when it is made;
#: readers filter by tag and never infer origin from position.
ORIGINS = ("policy", "framework_final")

LIMITS = (
    "Static screening reads syntax and data flow only; it cannot prove that a "
    "runtime decision was caused by a particular world value.",
    "`concern` marks a defensible problem shape; `inconclusive` means the "
    "analysis could not follow the code, which is not a defect.",
    "Query counts are never treated as evidence that a world result was used.",
    "No simulator, model or held-out data participates in this screen.",
)


def revision_errors(case: dict) -> list[str]:
    """Refuse an incompatible combination up front, before anything runs.

    An absent flag is not an error and adds no constraint, so every case staged
    before this key existed validates exactly as it did.
    """
    revision = case.get("closed_loop_revision")
    if not revision:
        return []
    errors = []
    if revision != REVISION:
        errors.append(f"unknown closed_loop_revision {revision!r}; this build implements {REVISION!r}")
    if case.get("executable_world_revision") != "r1":
        errors.append("closed_loop_revision requires executable_world_revision 'r1'")
    if case.get("foundation_revision") != "r1":
        # Without the foundation contract, done() clauses are not re-validated
        # against evidence, so an online stop could rest on an unchecked verdict.
        errors.append("closed_loop_revision requires foundation_revision 'r1'")
    if case.get("condition") != CONDITION or case.get("profile") != PROFILE:
        errors.append(f"closed_loop_revision is a {CONDITION}-only {PROFILE}-profile opt-in")
    if case.get("c_arm") not in ARMS:
        errors.append("closed_loop_revision is unsupported with c_arm "
                      f"{case.get('c_arm')!r}: an online goal cannot be ablated and required at once")
    return errors


def enabled(case: dict) -> bool:
    """True only for a cell that explicitly opted into this revision."""
    return bool(case.get("closed_loop_revision")) and not revision_errors(case)


# --- runtime adjudication of one online decision ----------------------------


def adjudicate(checked: dict, raw: dict, evidence_current) -> dict:
    """Check the branch an online judgment requested against verifiable evidence.

    ``checked`` is the verdict after the session's evidence checks; ``raw`` is
    what the world returned and the only place the requested branch lives.
    ``evidence_current(ids)`` must hold for EVERY id. Any inadmissible request --
    unknown name, missing, contradicting its verdict, or resting on evidence that
    is not all current -- becomes ``unknown``/``observe``, with the authored
    verdict and the reason recorded. Never silently promoted, never silently
    dropped. The returned ``branch`` and ``verdict`` are what the policy acts on.
    """
    status = dict(checked)
    requested = raw.get("branch")
    status.update(requested_branch=requested, branch=requested, branch_source="authored")
    reason = None
    if requested not in BRANCHES:
        reason = "an online decision must name one of " + ", ".join(sorted(BRANCHES))
    elif requested not in ADMITS[status["verdict"]]:
        reason = f"verdict {status['verdict']!r} does not admit branch {requested!r}"
    elif status["verdict"] != "unknown" and not evidence_current(status.get("evidence_ids") or []):
        # Defence in depth: re-check the exact IDs rather than trusting the
        # verdict's own bookkeeping, because these branches change the episode.
        reason = f"{requested!r} requires every evidence ID to be a current observation"
    if reason:
        if status["verdict"] != "unknown":
            status.setdefault("authored_verdict", status["verdict"])
        status.update(verdict="unknown", branch="observe", branch_source="downgraded",
                      branch_reason=reason)
    return status


def decision_counts(evaluations) -> dict:
    """How each recorded decision branched, without scoring its correctness."""
    counts = Counter()
    for row in evaluations if isinstance(evaluations, list) else []:
        if isinstance(row, dict):
            counts[row.get("branch") or "unrecorded"] += 1
    return dict(sorted(counts.items()))


# --- static screen ----------------------------------------------------------


def _aliases(tree: ast.AST, attribute: str) -> set[str]:
    """Local names that may refer to ``<something>.<attribute>``.

    Follows ``f = world.done``, ``g = f`` and ``from world import done as d``
    to a fixed point. Helper and alias forms are legitimate, so the screen
    follows the ones it can rather than calling them defects.
    """
    names: set[str] = {attribute}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == attribute:
                    names.add(alias.asname or alias.name)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not ((isinstance(value, ast.Attribute) and value.attr == attribute)
                    or (isinstance(value, ast.Name) and value.id in names)):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True
    return names


def _is_call_to(node: ast.AST, attribute: str, aliases: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return ((isinstance(func, ast.Attribute) and func.attr == attribute)
            or (isinstance(func, ast.Name) and func.id in aliases))


_TESTS = (ast.If, ast.While, ast.IfExp, ast.Assert)


def _in_test(node: ast.AST, parents: dict) -> bool:
    """Whether a node sits inside the condition of a branch."""
    child, parent = node, parents.get(node)
    while parent is not None:
        if isinstance(parent, _TESTS) and child is parent.test:
            return True
        if isinstance(parent, ast.stmt):
            return False
        child, parent = parent, parents.get(parent)
    return False


def _uses(tree: ast.AST, attribute: str) -> list[dict]:
    """Classify where each result of ``.<attribute>(...)`` flows, syntactically.

    ``discarded``  bare statement; the value cannot reach anything.
    ``unread``     bound to a name that is never read anywhere in the module.
    ``branched``   the call, or a name bound to it, appears in a branch condition.
    ``passed_on``  read elsewhere (returned, passed to a helper, stored);
                   whether that decides an action is not established.
    Flow-insensitive on purpose: the screen may miss a defect, but it must not
    call a supported helper form one.
    """
    aliases = _aliases(tree, attribute)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    loads = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, []).append(node)
    rows = []
    for node in ast.walk(tree):
        if not _is_call_to(node, attribute, aliases):
            continue
        parent = parents.get(node)
        use = "passed_on"
        if isinstance(parent, ast.Expr):
            use = "discarded"
        elif _in_test(node, parents):
            use = "branched"
        elif isinstance(parent, ast.Assign) and all(isinstance(t, ast.Name) for t in parent.targets):
            reads = [n for t in parent.targets for n in loads.get(t.id, [])]
            if not reads:
                use = "unread"
            elif any(_in_test(n, parents) for n in reads):
                use = "branched"
        rows.append({"line": node.lineno, "use": use})
    return sorted(rows, key=lambda r: r["line"])


def source_findings(world_source: str, policy_source: str) -> dict:
    """Conservative structural screen of the authored pair.

    Records world results that are computed but cannot reach an action or a
    branch, and predicted facts a goal might lean on. Returns findings, never a
    rejection: only an authored Python error in the supported part of the
    screening rejects a candidate.
    """
    analyses = {"policy_parsed": False, "world_parsed": False}
    findings = []
    try:
        policy = ast.parse(policy_source)
        analyses["policy_parsed"] = True
        world = ast.parse(world_source)
        analyses["world_parsed"] = True
    except SyntaxError as exc:
        return {"findings": [{"kind": "unparsed", "severity": "inconclusive",
                              "detail": f"{type(exc).__name__}: {exc}"}],
                "analyses": analyses, "limits": list(LIMITS)}

    for attribute, label in (("done", "judgment"), ("query", "world query")):
        rows = _uses(policy, attribute)
        analyses[f"{attribute}_uses"] = rows
        ignored = [r for r in rows if r["use"] in {"discarded", "unread"}]
        if ignored:
            findings.append({"kind": f"{attribute}_result_ignored", "severity": "concern",
                             "lines": [r["line"] for r in ignored],
                             "detail": f"a {label} result is discarded or never read, so it cannot decide an action or a branch"})
        if not rows:
            referenced = any(isinstance(n, ast.Attribute) and n.attr == attribute
                             for n in ast.walk(policy))
            findings.append({
                "kind": f"{attribute}_not_called",
                "severity": "inconclusive" if referenced else "concern",
                "detail": (f"the policy references {attribute} but never calls it here; a helper may own the call"
                           if referenced else f"the policy never consults a {label}, so no branch can depend on it")})
        elif any(r["use"] == "branched" for r in rows):
            findings.append({"kind": f"{attribute}_branches", "severity": "supported",
                             "detail": f"at least one {label} result reaches a branch condition; that is syntax, not runtime causality"})
        elif not ignored:
            findings.append({"kind": f"{attribute}_branch_unseen", "severity": "inconclusive",
                             "detail": f"{label} results are passed on, but no branch condition visibly reads them; a helper may"})

    predicted = [n for n in ast.walk(world)
                 if isinstance(n, ast.keyword) and n.arg == "layer"
                 and isinstance(n.value, ast.Constant) and n.value.value == "predicted"]
    analyses["predicted_layer_uses"] = len(predicted)
    if predicted:
        # The runtime already refuses a goal clause on any non-observed layer;
        # naming the shape here lets it be fixed before an attempt is spent.
        done_fn = next((n for n in world.body if isinstance(n, ast.FunctionDef) and n.name == "done"), None)
        inside = set(ast.walk(done_fn)) if done_fn is not None else set()
        in_goal = any(n in inside for n in predicted)
        findings.append({
            "kind": "predicted_layer_in_goal" if in_goal else "predicted_layer_present",
            "severity": "concern" if in_goal else "inconclusive",
            "lines": sorted({n.lineno for n in predicted}),
            "detail": ("done() reads the predicted layer; predictions cannot establish a goal"
                       if in_goal else "the world stores predicted facts; they may guide search, never decide a goal")})
    return {"findings": findings, "analyses": analyses, "limits": list(LIMITS)}


# --- per-trial and aggregate feedback ---------------------------------------


def feedback(directory, *, write: bool = True) -> dict:
    """Online decisions for one development trial, apart from the shadow judge.

    Never raises: an audit that cannot run is reported as unavailable beside the
    task result, blocks nothing, costs nothing, and is never a verdict.
    """
    directory = Path(directory)
    report = {"revision": REVISION, "trial_dir": str(directory), "status": "unavailable",
              "limits": list(LIMITS)}
    try:
        manifest = json.loads((directory / "judgment_world/manifest.json").read_text())
        if not manifest.get("closed_loop"):
            report["detail"] = "this trial did not run the closed-loop revision"
        else:
            evaluations = [e for e in manifest.get("self_evaluations") or [] if isinstance(e, dict)]
            # The runtime tags each evaluation's origin when it is made. Position
            # is never trusted: a crash or a raising final judgment leaves no
            # framework_final row, and the last row is then a real policy decision.
            online = [e for e in evaluations if e.get("origin") == "policy"]
            final = [e for e in evaluations if e.get("origin") == "framework_final"]
            report.update(
                status="audited", binding=manifest.get("binding"),
                online_decisions=[{k: e.get(k) for k in
                                   ("verdict", "branch", "branch_source", "requested_branch",
                                    "branch_reason", "evidence_ids", "reason", "api_calls_before")}
                                  for e in online],
                online_counts=decision_counts(online), online_total=len(online),
                final_shadow_self_evaluation=final[-1] if final else None,
                features=manifest.get("features"), api_calls=manifest.get("api_calls"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        report["detail"] = f"{type(exc).__name__}: {exc}"
    if write:
        try:
            (directory / "closed_loop_audit.json").write_text(json.dumps(report, indent=2) + "\n")
        except OSError:
            pass
    return report


def aggregate(rows) -> dict:
    """Development-only summary of online branching, grouped by frozen pair."""
    groups = {}
    for row in rows if isinstance(rows, list) else []:
        report = (row or {}).get("closed_loop")
        if not report or report.get("status") != "audited":
            continue
        bundle = row.get("bundle") or {}
        key = (bundle.get("policy"), bundle.get("world"))
        group = groups.setdefault(key, {"policy_sha256": key[0], "world_sha256": key[1],
                                        "counts": Counter(), "trials": []})
        group["counts"].update(report.get("online_counts") or {})
        group["trials"].append({"seed": row.get("seed"), "directory": row.get("directory"),
                                "online_total": report.get("online_total")})
    return {"scope": "development only; repeated attempts are not independent samples",
            "separates": "online decisions recorded while acting; the framework's final "
                         "shadow self-evaluation is reported per trial, not merged here",
            "limits": list(LIMITS),
            "by_bundle": [{**g, "counts": dict(sorted(g["counts"].items()))} for g in groups.values()]}
