"""CC_TASK_14 (p6d) method switches: one case key, `method_switches`, validated here.

Every switch is task-independent and off when the key is absent, so a case
without it renders, admits, scores and evaluates exactly as before (p6b/p6c).
The values a p6d cell freezes are `P6D`; the case, the ledger identity and the
runtime manifest carry them, so a result can always say which method produced it.

  subgoal_verification  prompt + skill: confirm every subgoal from observations,
                        retry differently when unconfirmed, stop acting once done
  judgment_calibration  per-seed world-judgment vs simulator disagreements in the
                        trial record, `status` and `check` (development trials only)
  screening_gate        "record_only": the rehearsal prediction-level rule and the
                        saturated-class refusal are recorded, never refused on.
                        Authored Python errors still reject.
  version_score         "sweep_only": a phase-B candidate's coverage and score come
                        from its sweep rows; the repair trial that admitted it is
                        evidence for the worker, not part of the score
  prefreeze_resweep     {"margin": m, "max_candidates": k}: after development the
                        outer driver re-runs every complete candidate within m
                        passes of the best (at most k) on all development seeds;
                        selection uses both runs. Never counts as a version.
  unseen_seeds          seeds the outer driver runs the frozen bundle on after the
                        freeze and before held-out; never shown to the worker
  heldout_watchdog      "settle": a held-out watchdog kill is judged like a
                        development one (policy timeout = failed seed)
  api_revision          "m4-r1": solve_ik reports clipping / pose substitution,
                        segmented moves, GraspNet seeded from the request content
  success_rule          "libero_latch": a trial succeeds if the task was achieved at
                        any simulator step within the step budget (standard LIBERO);
                        the budget-exhausted "executing action in terminated episode"
                        ends the trial without a program error. Development and
                        held-out alike. Changes comparability with earlier rounds.
"""
from __future__ import annotations

KEY = "method_switches"
API_REVISION_ENV = "ASPIRE_API_REVISION"
API_REVISIONS = ("m4-r1",)
SUCCESS_RULE_ENV = "ASPIRE_SUCCESS_RULE"
SUCCESS_RULES = ("libero_latch",)
UNSEEN_RANGE = range(66, 81)

P6D = {
    "subgoal_verification": True,
    "judgment_calibration": True,
    "screening_gate": "record_only",
    "version_score": "sweep_only",
    "prefreeze_resweep": {"margin": 2, "max_candidates": 3},
    "unseen_seeds": list(UNSEEN_RANGE),
    "heldout_watchdog": "settle",
    "api_revision": "m4-r1",
    "success_rule": "libero_latch",
}


def switches(case: dict) -> dict:
    return case.get(KEY) or {}


def enabled(case: dict, name: str):
    """The switch's value, or None/False when the case does not set it."""
    return switches(case).get(name)


def errors(case: dict) -> list[str]:
    """Unknown keys or values are refused before anything is staged or run."""
    if KEY not in case:
        return []
    value = case[KEY]
    if not isinstance(value, dict):
        return [f"{KEY} must be a mapping, not {type(value).__name__}"]
    problems = []
    known = set(P6D)
    problems += [f"unknown method switch {k!r}" for k in sorted(set(value) - known)]
    for name in ("subgoal_verification", "judgment_calibration"):
        if name in value and type(value[name]) is not bool:
            problems.append(f"{name} must be true or false")
    if value.get("screening_gate") not in (None, "record_only"):
        problems.append("screening_gate must be 'record_only' when set")
    if value.get("version_score") not in (None, "sweep_only"):
        problems.append("version_score must be 'sweep_only' when set")
    if value.get("heldout_watchdog") not in (None, "settle"):
        problems.append("heldout_watchdog must be 'settle' when set")
    if value.get("api_revision") not in (None, *API_REVISIONS):
        problems.append(f"api_revision must be one of {API_REVISIONS} when set")
    if value.get("success_rule") not in (None, *SUCCESS_RULES):
        problems.append(f"success_rule must be one of {SUCCESS_RULES} when set")
    resweep = value.get("prefreeze_resweep")
    if resweep is not None:
        if (not isinstance(resweep, dict) or set(resweep) != {"margin", "max_candidates"}
                or any(type(resweep[k]) is not int for k in resweep)
                or not 0 <= resweep["margin"] <= 15 or not 1 <= resweep["max_candidates"] <= 8):
            problems.append("prefreeze_resweep must be {'margin': 0..15, 'max_candidates': 1..8}")
        if value.get("version_score") != "sweep_only":
            problems.append("prefreeze_resweep merges sweep-only evidence; it requires version_score sweep_only")
        if case.get("development_budget") != "aspire-v1":
            problems.append("prefreeze_resweep exists only under the aspire-v1 development budget")
    if value.get("version_score") and case.get("development_budget") != "aspire-v1":
        problems.append("version_score exists only under the aspire-v1 development budget")
    unseen = value.get("unseen_seeds")
    if unseen is not None:
        if (not isinstance(unseen, list) or not unseen or any(type(s) is not int for s in unseen)
                or len(set(unseen)) != len(unseen) or not set(unseen) <= set(UNSEEN_RANGE)):
            problems.append("unseen_seeds must be distinct integers within 66..80")
        elif set(unseen) & (set(case.get("dev_seeds", [])) | set(case.get("heldout_seeds", []))):
            problems.append("unseen_seeds overlap the development or held-out seeds")
    return problems


def resweep_settings(case: dict) -> dict | None:
    return enabled(case, "prefreeze_resweep")


def unseen_seeds(case: dict) -> tuple[int, ...]:
    return tuple(enabled(case, "unseen_seeds") or ())


#: Every environment variable the switches may set; runtime_env drops inherited copies first.
ENV_KEYS = (API_REVISION_ENV, SUCCESS_RULE_ENV)


def api_env(case: dict) -> dict[str, str]:
    """Environment the replay (policy API, simulator) and service processes get."""
    env = {}
    if enabled(case, "api_revision"):
        env[API_REVISION_ENV] = enabled(case, "api_revision")
    if enabled(case, "success_rule"):
        env[SUCCESS_RULE_ENV] = enabled(case, "success_rule")
    return env


def manifest_entry(case: dict) -> dict | None:
    """What a result / manifest records about the method, None for a legacy case."""
    if KEY not in case:
        return None
    value = switches(case)
    entry = {"switches": value, "source": "scripts/libero/method_switches.py"}
    if value.get("api_revision"):
        entry["api_changes"] = API_CHANGES[value["api_revision"]]
    if value.get("success_rule"):
        entry["scoring_change"] = SCORING_CHANGES[value["success_rule"]]
    entry["comparability"] = COMPARABILITY
    return entry


#: For comparability notes: every public-API behaviour an api_revision changes.
API_CHANGES = {
    "m4-r1": [
        "solve_ik: when the target is clipped (x <= 0.75) or a different orientation is used, "
        "prints one '[solve_ik] ...' line with the requested and the used target/orientation, and "
        "last_ik_report() returns it; the return value (joint solution or None) is unchanged",
        "move_to_pose_segmented(position, quaternion, max_step=0.05): new primitive; moves in "
        "straight-line segments of at most max_step metres, each solved and executed with the "
        "existing solve_ik / move_to_joints",
        "plan_grasp (GraspNet service): point-cloud subsampling is seeded from the request content "
        "with a request-local RNG, so the same request returns the same grasps",
    ],
}

SCORING_CHANGES = {
    "libero_latch": (
        "a trial (development and held-out) succeeds if LIBERO's per-step success check (the env's done, "
        "= _check_success()) was true at any control step within max_steps, as the standard LIBERO "
        "evaluation latches it; reward 1.0 then. When robosuite's horizon is reached, the next action "
        "raises 'executing action in terminated episode': it is no longer a world fault / "
        "world_program_error and does not set sandbox_rc 1; the trial is scored by the latch. Earlier "
        "rounds scored the end-of-trial check_success and recorded that error as world_program_error "
        "(graded failure)"),
}

COMPARABILITY = (
    "p6d changes the method (prompt contract, calibration feedback, record-only screening, "
    "sweep-only version score, pre-freeze re-sweep, unseen measurement), the policy API (api_changes) "
    "and, with success_rule, the scoring of a trial; held-out numbers are not directly comparable "
    "with p5/r3/p6b/ns/l10 or with upstream ASPIRE, which scores the end-of-trial state")
