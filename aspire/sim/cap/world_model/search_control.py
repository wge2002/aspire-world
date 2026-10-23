"""p3 search control: signature early stop (layer 1) and class saturation (layer 3).

Read-only over a development ledger (`development_state.json`) and the recorded
artifacts of its trials. No LLM, no simulator, no task geometry: everything here
is computed from the world's own prediction/observation trace and the ledger's
accounting. Layer 2 (refuted-hypothesis rejection) is a later task.

Terms
  charged attempt  a ledger row that spent one of a seed's attempts and really
                   ran a simulator process (aliases excluded), numbered 1.. in
                   the order given (ledger order live; lifecycle order in the
                   retrospective).
  graded attempt   a charged smoke/initial/repair row with a resolved outcome.
                   Diagnostic sessions and infrastructure failures grade nothing.
  gripper-level    an entity every observed write of which came from the
                   proprioceptive `get_observation` return; every other entity is
                   object-level. This is read from the recorded `source.api`, not
                   from entity names, so it holds for any authored world.
  observed         a graded attempt whose world returned a progress() value
                   and decided (match/mismatch) at least one object-level
                   prediction: the target was re-perceived after it was
                   manipulated. Only observed attempts are saturation evidence
                   (N, best progress); a trial that re-checked only the gripper
                   would otherwise read as "arm arrived, object did not move".
  signature        prediction-status counts per entity/fact + rounded
                   object-level residuals + outcome. Absolute scene coordinates
                   are deliberately excluded: they vary per seed and are not
                   information about the hypothesis under test. The rounded
                   object-level net change of every observed fact is still
                   recorded beside the digest but no longer hashed by default:
                   on the p2 ledgers it tracked perception noise (a handle
                   estimate drifting 0.1 m, a pull direction rotating), which
                   delayed drawer_r1's early stop from 4 to 11 (PLAN.md,
                   "Decisions after CC Task 1").
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

REVISION = "p3-search"
DEFAULT_CLASS = "undeclared"
RETRY_LIMIT = 3
GRADED_PHASES = ("smoke", "initial", "repair")
CHARGED_PHASES = (*GRADED_PHASES, "diagnostic")
#: p4 coverage sweep: graded, but spends no repair retry, so it is never part of
#: the charged-attempt statistics (early stop, saturation, informative fraction).
#: Only the triage table, which groups the sweep's failures, includes it.
SWEEP_PHASE = "sweep"
RESOLVED_STATUSES = frozenset({"complete", "world_program_error", "diagnostic_program_error"})
PROPRIOCEPTIVE_APIS = frozenset({"get_observation"})
MEASURED_LAYERS = frozenset({"observed", "rehearsal"})
RESOLVED_CHECKS = ("match", "mismatch")
#: Quantization step for residuals and net changes (metres or radians).
SIGNATURE_STEP = 0.1
SATURATION_DEFAULTS = {"K": 5, "N": 8, "frac": 0.3, "gripper_match": 0.8}
UNOBSERVED_HINT = "target not re-observed: this attempt is not evidence for saturation"
#: p4: the world could not resolve the fact at all, which is a broken channel and
#: not merely an idle one. Different defect, different repair, different hint.
UNAVAILABLE_HINT = ("the world's measurement channel for the target is broken, not merely idle: "
                    "the fact could not be resolved at all (typically a lost association). "
                    "Repair the association in the world — re-observe the target, then re-associate "
                    "it — before spending more attempts; this attempt is not saturation evidence either")
#: The values a recorded progress channel can take. `error` is a raising progress().
PROGRESS_STATUSES = ("observed", "unobserved", "unavailable", "error")
#: Components of one trial signature, in the order the report names them.
SIGNATURE_FIELDS = ("object_residuals", "world_facts", "outcome")
RULES = [
    "physically infeasible is not an accepted BLOCKED root cause while untried primitive "
    "classes remain: benchmark tasks are feasible by construction",
    "the next charged repair bundle must declare a PRIMITIVE_CLASS outside the saturated "
    "classes; `trial --replicate <reason>` explicitly re-runs a saturated class",
    "a new PRIMITIVE_CLASS means a new contact geometry or grasp mode; re-tuned parameters "
    "or a renamed class is the same class (bundles are audited by class-stripped policy digest)",
    "progress() counts only the target object's own observed change: moving a different "
    "object or a jittering estimate is not progress",
    "re-perceive the target object after every manipulation primitive and before the trial "
    "ends; progress() returns None when the target was not observed since the last "
    "manipulation, and such a trial is unobserved: it is not evidence for saturation",
    "a None progress() has two different causes and the world must say which: `unobserved` "
    "(this trial did not re-perceive the target) or `unavailable` (the world cannot resolve "
    "the fact at all — a lost association, not an idle channel). An unavailable attempt is "
    "not evidence for saturation either, and a class whose attempts are all unavailable is a "
    "broken channel to repair (re-observe the target, then re-associate it) before more "
    "attempts are spent",
    "a candidate is only evidence once its OWN bytes have run on the whole development set "
    "51–65; the cell may not record `selected` while any development seed is missing. Missing "
    "seeds are swept with `trial --phase sweep`, which spends no repair retry (once per seed per "
    "tested candidate; sweep only the candidate you intend to freeze, and never select one whose "
    "coverage is incomplete); a gate no candidate can reach is a terminal blocker, never a "
    "lowered gate",
    "a sweep's failures are grouped by trial signature into modes; repair the MODE (one "
    "defect), not one seed — seeds that failed the same way are one problem",
]


#: CC_TASK_6: the ASPIRE v1.0 development budget, added to RULES only in a cell
#: whose identity carries `development_budget: "aspire-v1"`.
BUDGET_REVISION = "aspire-v1"
BUDGET_RULES = [
    "aspire-v1 phase A: until development seed 51 actually passes, every attempt runs on seed 51 "
    "and attempts there are uncapped while diagnostics make progress; no sweep, no select, no freeze",
    "aspire-v1 phase B: every version runs on all 15 development seeds (its first seed with "
    "`trial --phase repair --mode <mode> --mode-seeds <seeds>`, the rest with `trial --phase sweep`, "
    "which spends nothing); each version declares the failure mode it targets; at most 3 versions per "
    "mode and 8 in total; a mode still unfixed after 3 versions is blocked: write "
    "attempts/mode_<mode>_BLOCKED.md (root cause, what was tried) and spend no more on it; a renamed "
    "mode is the same mode",
    "aspire-v1 freeze: only when the selected version passes 15/15, all 8 versions are used, or every "
    "remaining failing seed belongs to a blocked mode; select the complete version with the most passes, "
    "then fewer crashes (program_ok=false), then the simpler one",
    "aspire-v1 probes (`trial --phase probe`) observe without any motor command; they are not versions "
    "and cost nothing",
]


#: The one RULES clause the budget supersedes, and what it reads under aspire-v1.
BUDGET_RULE_EDIT = ("sweep only the candidate you intend to freeze, and never select one whose "
                    "coverage is incomplete",
                    "under aspire-v1 every version is swept in full before the next one, and never "
                    "select one whose coverage is incomplete")


def rules_for(ledger):
    """RULES, plus the budget's rules for an aspire-v1 ledger (CC_TASK_6)."""
    identity = (ledger.get("identity") or {}) if isinstance(ledger, dict) else {}
    if identity.get("development_budget") != BUDGET_REVISION:
        return list(RULES)
    old, new = BUDGET_RULE_EDIT
    edited = [rule.replace(old, new) for rule in RULES]
    if sum(old in rule for rule in RULES) != 1:
        raise ValueError("budget rule anchor changed")
    return edited + list(BUDGET_RULES)


# --- contract ---------------------------------------------------------------


def declared_class(source):
    """The policy's module-level `PRIMITIVE_CLASS = "..."`, or None if absent/invalid."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    value = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PRIMITIVE_CLASS" for t in node.targets):
            value = node.value.value if (isinstance(node.value, ast.Constant)
                                         and isinstance(node.value.value, str)
                                         and node.value.value.strip()) else None
    return value.strip() if value else None


def primitive_class(policy_path):
    """Declared class of a recorded policy file; `undeclared` when missing."""
    try:
        return declared_class(Path(policy_path).read_text()) or DEFAULT_CLASS
    except OSError:
        return DEFAULT_CLASS


def policy_body_sha256(source):
    """sha256 of the policy with every PRIMITIVE_CLASS assignment line removed.

    Two bundles with the same body digest under different classes are a rename,
    not a new contact strategy; the report lists them for audit.
    """
    lines = source.splitlines(keepends=True)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    drop = set()
    for node in (tree.body if tree else ()):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "PRIMITIVE_CLASS" for t in node.targets):
            drop.update(range(node.lineno - 1, node.end_lineno))
    body = "".join(line for i, line in enumerate(lines) if i not in drop)
    return hashlib.sha256(body.encode()).hexdigest()


def _policy_body(policy_path):
    try:
        return policy_body_sha256(Path(policy_path).read_text())
    except OSError:
        return None


def contract_errors(policy_source, world_source):
    """What p3-search adds over p2-runtime, checked statically before anything runs."""
    problems = []
    if declared_class(policy_source) is None:
        problems.append('p3-search policy must declare a module-level PRIMITIVE_CLASS = "<contact strategy>"')
    try:
        names = {n.name for n in ast.parse(world_source).body if isinstance(n, ast.FunctionDef)}
    except SyntaxError:
        names = set()
    if "progress" not in names:
        problems.append("p3-search world must define progress() -> float in [0, 1] or None")
    return problems


def check_progress(value):
    """A progress() return as a float in [0, 1], or raise ValueError.

    None (target not observed since the last manipulation) is handled by the
    caller before this check; it is recorded as unobserved, never as 0.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"progress() must return a finite number, got {value!r}")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"progress() must lie in [0, 1], got {value!r}")
    return float(value)


# --- one trial ----------------------------------------------------------------


def read_events(trial_dir):
    path = Path(trial_dir) / "judgment_world/events.jsonl"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def entity_levels(events):
    """entity -> "gripper" | "object", from the recorded source of its measured writes.

    Measured means the observed layer of a live trial or the rehearsal layer of
    an offline rehearsal (the same adapter code under the rehearsal binding);
    the predicted layer never classifies an entity.
    """
    sources = {}
    for event in events:
        if event.get("event") != "state_write":
            continue
        record = event.get("record") or {}
        if record.get("layer", "observed") not in MEASURED_LAYERS or not record.get("entity"):
            continue
        api = (record.get("source") or {}).get("api")
        sources.setdefault(record["entity"], set()).add(api)
    return {e: "gripper" if apis and apis <= PROPRIOCEPTIVE_APIS else "object"
            for e, apis in sources.items()}


def prediction_levels(events, levels=None):
    """Committed and resolved predictions per entity level, from one trace.

    An entity the trace never measured has no level ("unmeasured"): a
    prediction about it can never be judged, so it satisfies neither side of
    the per-trial gripper + object rule.
    """
    levels = entity_levels(events) if levels is None else levels
    out = {level: {"committed": 0, "resolved": 0, "match": 0, "mismatch": 0, "entities": set()}
           for level in ("gripper", "object", "unmeasured")}
    for e in events:
        if e.get("event") not in ("prediction_committed", "prediction_check") or not e.get("entity"):
            continue
        row = out[levels.get(e["entity"], "unmeasured")]
        row["entities"].add(e["entity"])
        if e["event"] == "prediction_committed":
            row["committed"] += 1
        elif e.get("status") in RESOLVED_CHECKS:
            row["resolved"] += 1
            row[e["status"]] += 1
    for row in out.values():
        row["entities"] = sorted(row["entities"])
    return out


def level_contract_errors(levels):
    """p3-search per-trial rule: >= 1 gripper-level and >= 1 object-level committed prediction."""
    return [f"p3-search policy must commit at least one {level}-level prediction per trial "
            f"(this run committed none; entities measured from get_observation are gripper-level, "
            f"all other measured entities object-level)"
            for level in ("gripper", "object") if not levels[level]["committed"]]


def is_observed(progress, levels):
    """A graded trial is observed when progress() returned a value and at least
    one object-level prediction was decided (match/mismatch) on its trace."""
    return progress is not None and bool(levels) and levels["object"]["resolved"] > 0


def observation_reason(value, status, levels):
    """Why a graded trial is not observed: the world's own reason, else the trace's.

    `unavailable` wins when the world reported it: a world that cannot resolve
    the fact at all is saying something stronger and more actionable than "no
    value was recorded", and the two repairs differ. A world that returned a
    value but left every object-level prediction undecided is `unobserved` for
    a trace-side reason, which the hint names separately.
    """
    if status == "unavailable":
        return "unavailable"
    if value is None:
        return "unobserved"
    return "no_object_prediction" if not (levels and levels["object"]["resolved"]) else None


def trial_observation(trial_dir):
    """Observed/unobserved verdict of one recorded trial, with the solver hint.

    p4: an unobserved trial carries WHICH kind of nothing it was. `unavailable`
    means the world's measurement channel could not resolve the fact (repair the
    channel, e.g. re-observe and re-associate the target); `unobserved` means it
    simply did not look since the last manipulation, and is not saturation
    evidence.
    """
    levels = prediction_levels(read_events(trial_dir))
    value, status = record_progress(trial_dir)
    observed = is_observed(value, levels)
    reason = None if observed else observation_reason(value, status, levels)
    out = {"progress": value, "progress_status": status,
           "object_predictions_decided": levels["object"]["resolved"],
           "gripper_predictions_decided": levels["gripper"]["resolved"], "observed": observed,
           "reason": reason}
    if not observed:
        out["hint"] = UNAVAILABLE_HINT if reason == "unavailable" else UNOBSERVED_HINT
    return out


def _quantize(value, step):
    return int(round(value / step))


def _delta(first, last, unit):
    if isinstance(first, bool) or isinstance(last, bool):
        return None
    if isinstance(first, (int, float)) and isinstance(last, (int, float)):
        d = float(last) - float(first)
        if unit == "rad":
            d = (d + math.pi) % (2 * math.pi) - math.pi
        return d
    if (isinstance(first, list) and isinstance(last, list) and len(first) == len(last)
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in first + last)):
        return [float(b) - float(a) for a, b in zip(first, last)]
    return None


def gripper_checks(events, levels=None):
    """Resolved gripper-level prediction checks: Counter of match/mismatch."""
    levels = entity_levels(events) if levels is None else levels
    return Counter(e["status"] for e in events
                   if e.get("event") == "prediction_check" and e.get("status") in RESOLVED_CHECKS
                   and levels.get(e.get("entity")) == "gripper")


def target_entities(attempts):
    """The object-level entity a cell's world models in most detail.

    `progress()` is identity-aware — it measures ONE target object — and the
    recovered reason must ask about that same entity, not about whichever object
    happened to be written. A world tracks its target with more than a single
    position reading (a displacement, an angle, a pull direction), so the entity
    owning the most distinct observed facts across the cell is the target. On the
    p3 cells this picks `knob` for stove and `target_handle` for drawer, which is
    exactly what each cell's own `progress()` reads.
    """
    facts = {}
    for a in attempts:
        if not a["graded"]:
            continue
        for key in (a["signature"] or {}).get("object_change") or {}:
            entity, _, fact = key.partition(".")
            facts.setdefault(entity, set()).add(fact)
    if not facts:
        return set()
    widest = max(len(v) for v in facts.values())
    return {e for e, v in facts.items() if len(v) == widest and widest > 1}


def trace_progress_reason(value, signature, targets=None):
    """Recover the p4 progress reason of a PRE-p4 trial from its own trace.

    Every trial recorded before p4 carries only `search_progress`; reading
    `unobserved` into all of them is the very ambiguity p4 exists to remove. The
    reason is therefore recovered from the world's own recorded object facts,
    which is what `progress()` had to resolve to produce a number:
      * a value was recorded                        -> `observed`;
      * no value, and a TARGET object fact was never
        written, is `never_known` or is `unknown_at_end` -> `unavailable` — the
        world could not resolve the fact, so the channel is broken, not idle;
      * no value, but every target fact was resolved -> `unobserved` — the
        conservative reading, since the inputs existed and no number was
        produced, which is what "did not look" means.

    `targets` narrows the question to the cell's target entity (see
    `target_entities`). Without it every recorded object fact is considered,
    which is the right reading for a single-object world and the conservative
    one for any other.
    """
    if value is not None:
        return "observed"
    change = signature.get("object_change") or {}
    keys = [k for k in change if not targets or k.partition(".")[0] in targets]
    if not keys:
        # The target's facts were never recorded at all, so there was nothing for
        # progress() to resolve. That is a broken channel, not an idle one — but
        # only assert it when the caller named a target to be absent.
        return "unavailable" if targets else "unobserved"
    for key in keys:
        if change[key] in ("never_known", "unknown_at_end"):
            return "unavailable"
    return "unobserved"


def mode_families(modes):
    """Group modes into the few defects they are variants of.

    An exact signature digest is the right definition of a mode — two seeds are
    the same mode only if their whole information content matches — but it is
    too fine to answer "how many distinct things are wrong". On bowl_p3_r1 the
    digest yields a dozen modes whose object residuals differ only in how far a
    bowl that did not move is measured to have moved; the actionable count is
    two families: the object never moved, and it moved and then went somewhere
    else. Families are therefore reported BESIDE the modes, never instead of
    them: the family is what a solver plans a repair against, the mode is what
    the ledger can actually defend.
    """
    families = {}
    for m in modes:
        definition = m.get("signature_definition") or {}
        residuals = definition.get("object_residuals") or []
        if residuals:
            moved = any(not r.endswith("=0") for r in residuals)
            key = ("object_residuals", "object_moved" if moved else "no_object_motion")
        else:
            key = (m.get("deciding_field", "outcome"), "non_object_evidence")
        f = families.setdefault(key, {"family": key[0] + ":" + key[1], "deciding_field": key[0],
                                      "behavior": key[1], "modes": 0, "seeds": [],
                                      "mode_digests": [], "labels": []})
        f["modes"] += 1
        f["seeds"] += m["seeds"]
        f["mode_digests"].append(m["signature"])
        f["labels"].append(m["label"])
    out = []
    for f in families.values():
        f["seeds"] = sorted(set(f["seeds"]))
        f["labels"] = sorted(set(f["labels"]))
        out.append(f)
    out.sort(key=lambda f: (-len(f["seeds"]), f["family"]))
    return out


def claimed_counts(text):
    """Every `N of M` / `N/M` pass-count claim in a selection reason, as (N, M).

    Deliberately narrow: only explicit "of" and slash forms are read, so an
    ordinary seed list ("seeds 55, 57, 58") is never mistaken for a claim. The
    caller re-derives each against the ledger and reports the disagreement
    instead of trusting the solver's prose.
    """
    out = []
    for match in re.finditer(r"(\d+)\s*(?:of|/)\s*(\d+)\b", text or ""):
        count, total = int(match.group(1)), int(match.group(2))
        if total > 0 and count <= total:
            out.append((count, total))
    return out


def trial_signature(trial_dir, outcome=None, *, step=SIGNATURE_STEP, include_change=False):
    """The information content of one trial, as a comparable record with a digest.

    `outcome` overrides what the trial's world manifest says (a sealed cell
    passes the gate verdict here); otherwise the manifest result is used.
    `object_change` is always recorded; it enters the digest only with
    `include_change=True` (the Task 1 definition, kept for analysis).
    """
    trial_dir = Path(trial_dir)
    events = read_events(trial_dir)
    levels = entity_levels(events)
    statuses = Counter()
    residuals = []
    for e in events:
        if e.get("event") != "prediction_check" or not e.get("entity"):
            continue
        statuses[f"{e['entity']}.{e.get('fact')}:{e.get('status')}"] += 1
        if (levels.get(e["entity"], "object") == "object" and e.get("status") in RESOLVED_CHECKS
                and isinstance(e.get("residual"), (int, float))):
            residuals.append(f"{e['entity']}.{e.get('fact')}={_quantize(e['residual'], step)}")
    first, last = {}, {}
    for e in events:
        if e.get("event") != "state_write":
            continue
        r = e.get("record") or {}
        if r.get("layer", "observed") != "observed" or levels.get(r.get("entity")) != "object":
            continue
        key = f"{r['entity']}.{r.get('fact')}"
        known = r.get("status") == "known" and r.get("value") is not None
        if known and key not in first:
            first[key] = (r["value"], r.get("unit"))
        last[key] = r["value"] if known else None
    changes = {}
    for key, value in sorted(last.items()):
        if key not in first:
            changes[key] = "never_known"
        elif value is None:
            changes[key] = "unknown_at_end"
        else:
            d = _delta(first[key][0], value, first[key][1])
            if d is None:
                changes[key] = json.dumps(value, sort_keys=True)
            else:
                changes[key] = ([_quantize(v, step) for v in d] if isinstance(d, list)
                                else _quantize(d, step))
    if outcome is None:
        try:
            manifest = json.loads((trial_dir / "judgment_world/manifest.json").read_text())
        except (OSError, json.JSONDecodeError):
            manifest = {}
        result = manifest.get("result") or {}
        outcome = {"world_status": manifest.get("status"),
                   "sandbox_rc": result.get("sandbox_rc"),
                   "passed": bool(result.get("task_completed"))}
    body = {"prediction_status": dict(sorted(statuses.items())),
            "object_residuals": sorted(residuals), "outcome": outcome, "step": step}
    if include_change:
        body["object_change"] = changes
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    return {**body, "object_change": changes, "digest": digest,
            "digest_includes_change": include_change, "events": len(events)}


# --- the ledger -----------------------------------------------------------------


def passed(row, sealed=False):
    if sealed:
        return bool((row.get("gate") or {}).get("passed"))
    return bool(row.get("task_completed"))


def charged_attempts(ledger, task_dir, *, order=None, sweeps=False):
    """Charged attempts with signature, class and gripper checks attached.

    `order` maps a row to a sort key (the retrospective sorts by lifecycle
    completion); None keeps ledger order, which is completion order live.
    `sweeps=True` adds the uncharged p4 sweep rows (for the triage table only).
    """
    task_dir = Path(task_dir)
    sealed = (ledger.get("identity") or {}).get("development_gate") not in (None, "oracle")
    rows = [r for r in ledger["trials"]
            if ((r.get("phase") in CHARGED_PHASES and r.get("spends_retry"))
                or (sweeps and r.get("phase") == SWEEP_PHASE))
            and r.get("executed", True) and not r.get("alias_of")
            and r.get("status") != "running"]
    if order is not None:
        rows = sorted(rows, key=order)
    attempts = []
    for n, row in enumerate(rows, 1):
        directory = task_dir / row["directory"]
        graded = row["phase"] in (*GRADED_PHASES, SWEEP_PHASE) and row["status"] in RESOLVED_STATUSES
        attempt = {"n": n, "phase": row["phase"], "seed": row["seed"],
                   "directory": row["directory"], "bundle_sha256": row.get("bundle_sha256"),
                   "status": row["status"], "graded": graded, "passed": graded and passed(row, sealed),
                   "primitive_class": primitive_class(directory / "code.py"),
                   "policy_body_sha256": _policy_body(directory / "code.py"),
                   "signature": None, "gripper_checks": {}, "prediction_levels": None,
                   "progress_status": None}
        if graded:
            events = read_events(directory)
            outcome = {"status": row["status"], "sandbox_rc": row.get("sandbox_rc"),
                       "passed": attempt["passed"]}
            attempt["signature"] = trial_signature(directory, outcome)
            attempt["gripper_checks"] = dict(gripper_checks(events))
            attempt["prediction_levels"] = {level: {k: v for k, v in row_.items() if k != "entities"}
                                            for level, row_ in prediction_levels(events).items()}
            # p4: the world's own reason for a missing progress value, so the
            # report can tell a broken channel from an idle one. A p3-era
            # manifest carries no reason and reads back as `unobserved`.
            attempt["progress_status"] = record_progress(directory)[1]
        attempts.append(attempt)
    return attempts


def _attempts(ledger, task_dir):
    if isinstance(ledger, list):
        return ledger
    if task_dir is None:
        raise ValueError("a raw ledger needs task_dir to read its trial artifacts")
    return charged_attempts(ledger, task_dir)


def informative_attempts(ledger, *, task_dir=None):
    """Which graded attempts produced a signature no earlier graded attempt had."""
    attempts = _attempts(ledger, task_dir)
    seen, rows = set(), []
    for a in attempts:
        if not a["graded"]:
            rows.append({"n": a["n"], "phase": a["phase"], "informative": None,
                         "reason": "diagnostic" if a["phase"] == "diagnostic" else "not graded"})
            continue
        digest = a["signature"]["digest"]
        new = digest not in seen
        seen.add(digest)
        rows.append({"n": a["n"], "phase": a["phase"], "informative": new,
                     "reason": "new signature" if new else "repeats an earlier signature"})
    graded = [r for r in rows if r["informative"] is not None]
    first = next((a["n"] for a in attempts if a["passed"]), None)
    before = [r for r in graded if first is None or r["n"] <= first]
    return {"attempts": rows, "graded": len(graded),
            "informative": sum(r["informative"] for r in graded),
            "fraction": (sum(r["informative"] for r in graded) / len(graded)) if graded else None,
            "first_success": first,
            "until_first_success": {
                "graded": len(before), "informative": sum(r["informative"] for r in before),
                "fraction": (sum(r["informative"] for r in before) / len(before)) if before else None},
            "distinct_signatures": len(seen)}


def initial_early_stop(ledger, k=3, *, task_dir=None, seeds=None, retry_limit=RETRY_LIMIT, key=None):
    """After k identical failing initial signatures, defer the remaining initial seeds.

    Returns the trigger attempt, the deferred seeds (no initial evidence yet at
    the trigger) and the attempts their unspent budget banks for repair.
    Identical passes do not trigger: replicating a success is coverage, not search.
    `key(attempt)` replaces the full signature digest (analysis variants only).
    """
    attempts = _attempts(ledger, task_dir)
    if seeds is None:
        seeds = (sorted({a["seed"] for a in attempts}) if isinstance(ledger, list)
                 else list(ledger["identity"]["dev_seeds"]))
    counts, members = Counter(), {}
    for i, a in enumerate(attempts):
        if a["phase"] != "initial" or not a["graded"] or a["passed"]:
            continue
        digest = key(a) if key else a["signature"]["digest"]
        counts[digest] += 1
        members.setdefault(digest, []).append(a["n"])
        if counts[digest] >= k:
            so_far = attempts[:i + 1]
            covered = {b["seed"] for b in so_far if b["phase"] == "initial" and b["graded"]}
            deferred = [s for s in seeds if s not in covered]
            banked = {s: max(0, retry_limit - sum(b["seed"] == s for b in so_far)) for s in deferred}
            return {"triggered": True, "k": k, "trigger_attempt": a["n"],
                    "signature": digest, "matching_attempts": members[digest],
                    "deferred_seeds": deferred, "banked": banked,
                    "banked_attempts": sum(banked.values())}
    return {"triggered": False, "k": k, "trigger_attempt": None, "deferred_seeds": [],
            "banked": {}, "banked_attempts": 0,
            "largest_identical_group": max(counts.values(), default=0)}


def signature_factors(signature):
    """Which of the signature's three components are populated, for the triage table.

    `object_residuals` is the rounded object-level residual list, `world_facts`
    the prediction-status counts per entity/fact, `outcome` the graded verdict.
    Naming them lets the table say WHAT a mode is a mode about, which is what
    makes "repair the mode" actionable rather than "the seeds failed".
    """
    return {"object_residuals": list(signature.get("object_residuals") or []),
            "world_facts": dict(signature.get("prediction_status") or {}),
            "outcome": dict(signature.get("outcome") or {})}


def outcome_label(signature):
    """A short human label for a mode: the outcome plus its dominant residual."""
    outcome = signature.get("outcome") or {}
    residuals = signature.get("object_residuals") or []
    if outcome.get("passed"):
        return "passed"
    if outcome.get("status") and outcome["status"] != "complete":
        return str(outcome["status"])
    if residuals:
        return "residual:" + ",".join(residuals[:2])
    facts = signature.get("prediction_status") or {}
    if facts:
        return "facts:" + ", ".join(sorted(facts)[:2])
    return "no_residual_no_fact"


def failure_modes(attempts, *, step=SIGNATURE_STEP):
    """Group a bundle's sweep FAILURES by the ledger's own trial signature.

    The sweep produces a per-seed pass/fail vector for one bundle; grouping the
    failures by signature turns "six seeds failed" into the one or two distinct
    defects the next repair can act on. bowl_p3_r1 is the motivating case: six
    no-lift seeds and six lift-then-release seeds are two modes with different
    fixes, and a per-seed list never showed them as modes.

    Deliberately groups failures only. Passing trials are not modes to repair,
    and a seed that passed is coverage, not triage input. One failing seed is
    still a group of size one: a single unfamiliar mode is exactly the thing
    that must survive into the next round rather than be rounded away as noise.
    """
    groups = {}
    for a in attempts:
        if not a["graded"] or a["passed"] or a["signature"] is None:
            continue
        digest = a["signature"]["digest"]
        g = groups.setdefault(digest, {"signature": digest, "seeds": [], "attempts": [],
                                       "phases": [], "classes": [],
                                       "factors": signature_factors(a["signature"]),
                                       "signature_definition": {k: v for k, v in a["signature"].items()
                                                                if k in ("prediction_status",
                                                                         "object_residuals", "outcome",
                                                                         "step")},
                                       "events": a["signature"].get("events")})
        g["seeds"].append(a["seed"])
        g["attempts"].append(a["n"])
        g["phases"].append(a["phase"])
        g["classes"].append(a["primitive_class"])
    modes = []
    for g in groups.values():
        g["seeds"] = sorted(set(g["seeds"]))
        g["classes"] = sorted(set(g["classes"]))
        g["label"] = outcome_label(g["signature_definition"])
        # The field that decides the mode: residuals when the object was measured
        # to move differently, otherwise the fact pattern, otherwise the outcome.
        g["deciding_field"] = ("object_residuals" if g["signature_definition"].get("object_residuals")
                               else "world_facts" if g["signature_definition"].get("prediction_status")
                               else "outcome")
        modes.append(g)
    modes.sort(key=lambda m: (-len(m["seeds"]), m["signature"]))
    return modes


def triage_table(attempts, *, step=SIGNATURE_STEP):
    """The `coverage_triage.json` body: the sweep's modes and the seeds in each.

    Reported per bundle, because two bundles' failures are not the same defect
    however similar the signatures look; the mode digest is meaningful only for
    the bytes that produced it.
    """
    graded = [a for a in attempts if a["graded"]]
    by_bundle = {}
    for a in graded:
        by_bundle.setdefault(a["bundle_sha256"], []).append(a)
    bundles = []
    for digest, rows in by_bundle.items():
        modes = failure_modes(rows, step=step)
        bundles.append({"bundle_sha256": digest,
                        "partial_digest": (digest or "")[:12],
                        "tested_seeds": sorted({r["seed"] for r in rows}),
                        "passed_seeds": sorted({r["seed"] for r in rows if r["passed"]}),
                        "failed_seeds": sorted({r["seed"] for r in rows if not r["passed"]}),
                        "mode_count": len(modes), "modes": modes,
                        "family_count": len(mode_families(modes)),
                        "families": mode_families(modes)})
    bundles.sort(key=lambda b: (-len(b["failed_seeds"]), b["partial_digest"]))
    return {"schema": "p4-coverage-triage/1", "signature_step": step,
            "signature_definition": "prediction-status counts per entity/fact + rounded object-level "
                                    "residuals + outcome",
            "grouping": "failures of one bundle, by identical trial_signature digest",
            "bundles": bundles,
            "step": step,
            "empty": not any(b["failed_seeds"] for b in bundles)}


def primitive_report(ledger, progress_fn, *, task_dir=None, class_fn=None, status_fn=None):
    """Per primitive class (and per bundle): best progress, attempts, gripper match.

    `progress_fn(attempt)` returns the object-level goal progress in [0, 1] or
    None when unknown. `class_fn(attempt)` overrides the declared class (the
    retrospective uses it for its single-class assumption). Each class keeps a
    cumulative history row after every graded attempt so saturation can name
    the attempt at which it first held.

    An attempt is observed only when its progress is known AND its trace
    decided an object-level prediction (`is_observed`). Best progress is taken
    over observed attempts only, and saturation's N counts observed attempts
    only; an unobserved attempt still counts as an attempt and a bundle, and its
    decided gripper rows still enter gripper_match.

    p4 splits the not-observed attempts by the world's own reason
    (`status_fn`, default: the status charged_attempts read from the manifest).
    An `unavailable` attempt means the measurement channel could not resolve the
    fact at all — a world defect to repair — while `unobserved` means the trial
    merely did not look. Both are excluded from N and from best progress; only
    the reported counters and the solver hint differ.
    """
    attempts = _attempts(ledger, task_dir)
    classes = {}
    for a in attempts:
        if not a["graded"]:
            continue
        name = class_fn(a) if class_fn else a["primitive_class"]
        value = progress_fn(a)
        value = None if value is None else min(1.0, max(0.0, float(value)))
        status = status_fn(a) if status_fn else a.get("progress_status")
        if status not in PROGRESS_STATUSES:
            status = "observed" if value is not None else "unobserved"
        observed = is_observed(value, a.get("prediction_levels"))
        unavailable = (not observed
                       and observation_reason(value, status, a.get("prediction_levels")) == "unavailable")
        c = classes.setdefault(name, {"class": name, "bundles": {}, "attempts": 0, "passes": 0,
                                      "observed_attempts": 0, "unobserved_attempts": 0,
                                      "unavailable_attempts": 0,
                                      "best_progress": None, "progress_unknown": 0,
                                      "first_attempt": a["n"],
                                      "gripper_checks": Counter(), "history": []})
        b = c["bundles"].setdefault(a["bundle_sha256"], {"attempts": 0, "passes": 0,
                                                         "observed_attempts": 0, "unobserved_attempts": 0,
                                                         "unavailable_attempts": 0,
                                                         "best_progress": None, "first_attempt": a["n"],
                                                         "policy_body_sha256": a.get("policy_body_sha256")})
        for group in (c, b):
            group["attempts"] += 1
            group["passes"] += int(a["passed"])
            group["observed_attempts" if observed
                  else "unavailable_attempts" if unavailable else "unobserved_attempts"] += 1
            if observed and (group["best_progress"] is None or value > group["best_progress"]):
                group["best_progress"] = value
        c["progress_unknown"] += int(value is None)
        c["gripper_checks"].update(a.get("gripper_checks") or {})
        resolved = sum(c["gripper_checks"][s] for s in RESOLVED_CHECKS)
        c["history"].append({"n": a["n"], "bundles": len(c["bundles"]), "attempts": c["attempts"],
                             "observed_attempts": c["observed_attempts"], "observed": observed,
                             "unavailable_attempts": c["unavailable_attempts"],
                             "passes": c["passes"], "best_progress": c["best_progress"],
                             "gripper_match": c["gripper_checks"]["match"] / resolved if resolved else None,
                             "gripper_resolved": resolved})
    report = []
    for c in classes.values():
        last = c["history"][-1]
        report.append({"class": c["class"], "bundle_count": len(c["bundles"]),
                       "first_attempt": c["first_attempt"],
                       # Audit of "renamed, not changed": every bundle digest of
                       # this class in first-seen order, with the attempt that
                       # first ran it and its class-stripped policy digest.
                       "bundle_digests": [{"bundle_sha256": digest, "first_attempt": b["first_attempt"],
                                           "policy_body_sha256": b["policy_body_sha256"]}
                                          for digest, b in sorted(c["bundles"].items(),
                                                                  key=lambda kv: kv[1]["first_attempt"])],
                       "attempts": c["attempts"], "observed_attempts": c["observed_attempts"],
                       "unobserved_attempts": c["unobserved_attempts"],
                       "unavailable_attempts": c["unavailable_attempts"], "passes": c["passes"],
                       "best_progress": c["best_progress"], "progress_unknown": c["progress_unknown"],
                       "gripper_checks": dict(c["gripper_checks"]),
                       "gripper_match": last["gripper_match"], "bundles": c["bundles"],
                       "history": c["history"]})
    bodies = {}
    for c in report:
        for b in c["bundle_digests"]:
            if b["policy_body_sha256"]:
                bodies.setdefault(b["policy_body_sha256"], set()).add(c["class"])
    renamed = [{"policy_body_sha256": body, "classes": sorted(names)}
               for body, names in sorted(bodies.items()) if len(names) > 1]
    return {"classes": report, "graded_attempts": sum(a["graded"] for a in attempts),
            "observed_attempts": sum(c["observed_attempts"] for c in report),
            "unobserved_attempts": sum(c["unobserved_attempts"] for c in report),
            "unavailable_attempts": sum(c["unavailable_attempts"] for c in report),
            "same_policy_body_across_classes": renamed}


def _saturated(row, K, N, frac, gripper_match):
    conditions = {
        "bundles>=K": row["bundles"] >= K,
        "observed_attempts>=N": row["observed_attempts"] >= N,
        "best_progress<frac": row["best_progress"] is not None and row["best_progress"] < frac,
        "gripper_match>=threshold": row["gripper_match"] is not None and row["gripper_match"] >= gripper_match,
        "never_passed": row["passes"] == 0,
    }
    return all(conditions.values()), conditions


def saturation(report, K=5, N=8, frac=0.3, gripper_match=0.8):
    """Saturated classes, each with the attempt at which saturation first held.

    Saturated: >= K bundles and >= N observed graded attempts of one class, best
    observed object progress still < frac of the goal, decided gripper-level
    predictions matching at >= gripper_match (the arm went where it was told;
    the object did not respond), and no pass. An unobserved attempt (unknown
    progress or no decided object prediction) or no decided gripper check is
    not evidence for saturation.
    """
    out = []
    for c in report["classes"]:
        trigger = next((h for h in c["history"] if _saturated(h, K, N, frac, gripper_match)[0]), None)
        now, conditions = _saturated(c["history"][-1], K, N, frac, gripper_match)
        out.append({"class": c["class"], "saturated": now,
                    "trigger_attempt": trigger["n"] if trigger else None,
                    "at_trigger": trigger, "conditions_now": conditions})
    return out


def summary(report, saturated, *, remaining=None, thresholds=None, rules=None):
    """The compact block the solver reads before its next charged repair."""
    return {"revision": REVISION,
            "thresholds": thresholds or dict(SATURATION_DEFAULTS),
            "saturated_classes": [s["class"] for s in saturated if s["saturated"]],
            "classes": [{k: c[k] for k in ("class", "bundle_count", "attempts", "observed_attempts",
                                           "unobserved_attempts", "unavailable_attempts", "passes",
                                           "best_progress", "gripper_match")}
                        for c in report["classes"]],
            # A class whose channel is dead everywhere is visible here rather than
            # reading as "no evidence". A single observed attempt clears it: the
            # channel recovered, so more attempts are worth spending on this class.
            "unavailable_classes": [c["class"] for c in report["classes"]
                                    if c["unavailable_attempts"] > 0 and c["observed_attempts"] == 0],
            "observed_attempts": report.get("observed_attempts", 0),
            "unobserved_attempts": report.get("unobserved_attempts", 0),
            "unavailable_attempts": report.get("unavailable_attempts", 0),
            "same_policy_body_across_classes": report.get("same_policy_body_across_classes", []),
            "remaining_budget": remaining, "rules": list(RULES) if rules is None else list(rules)}


def record_progress(trial_dir):
    """The recorded progress channel of one trial: (value, status).

    p3 recorded only `search_progress`; the p4 contract adds the reason beside
    it (`search_progress_status`) so "the channel is broken" and "this trial did
    not look" stop being the same fact. A manifest that predates the reason —
    every p3 and earlier trial — is read as `unobserved`: that is exactly the
    ambiguity the reason field was added to remove, and inventing `unavailable`
    for it would be a claim the old trial never made.
    """
    try:
        manifest = json.loads((Path(trial_dir) / "judgment_world/manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None, "unobserved"
    value = manifest.get("search_progress")
    status = manifest.get("search_progress_status")
    if status not in PROGRESS_STATUSES:
        status = "unobserved" if value is None else "observed"
    if status != "observed":
        return None, status
    try:
        return check_progress(value), "observed"
    except ValueError:
        return None, "unobserved"


def recorded_progress_value(trial_dir):
    """The world's recorded progress() of one trial; None when unobserved or invalid."""
    return record_progress(trial_dir)[0]


def recorded_progress(task_dir):
    """progress_fn for a live p3-search ledger: the world's own recorded progress()."""
    task_dir = Path(task_dir)
    return lambda attempt: recorded_progress_value(task_dir / attempt["directory"])


def control_report(ledger, task_dir, *, k=3, thresholds=None, remaining=None):
    """Everything the protocol writes before a charged repair, from one ledger read."""
    thresholds = thresholds or dict(SATURATION_DEFAULTS)
    attempts = charged_attempts(ledger, task_dir)
    report = primitive_report(attempts, recorded_progress(task_dir))
    saturated = saturation(report, **thresholds)
    info = informative_attempts(attempts)
    return {"revision": REVISION, "progress_source": "world.progress()",
            "class_source": "declared PRIMITIVE_CLASS",
            "summary": summary(report, saturated, remaining=remaining, thresholds=thresholds,
                               rules=rules_for(ledger)),
            "saturation": saturated, "primitive_report": report,
            "informative": {key: info[key] for key in ("graded", "informative", "fraction",
                                                       "distinct_signatures", "first_success")},
            "early_stop": (ledger.get("search_control") or {}).get("early_stop")
                          or initial_early_stop(attempts, k, seeds=list(ledger["identity"]["dev_seeds"]))}
