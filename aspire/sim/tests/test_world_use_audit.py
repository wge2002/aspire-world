"""Bounded world-use audit: small fixtures, no simulator, assets, or model calls.

Each fixture is a few lines of ordinary policy Python plus a hand-written query
trace of the shape `judgment_world` records. The point of these tests is the
three-way distinction and its honesty at the edges: unsupported Python must come
out inconclusive, never a false pass and never a false fail.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Loaded by location, exactly as the protocol loads it, so this test does not
# depend on which of the two checkout layouts it is running in.
audit = load("world_use_audit_under_test", ROOT / "cap/world_model/world_use_audit.py")


def line_of(source, needle):
    """The 1-based file line holding `needle`, so no offset is hard-coded."""
    for number, text in enumerate(source.splitlines(), start=1):
        if needle in text:
            return number
    raise AssertionError(f"{needle!r} is not in the fixture")


def row(name, line, *, index=0, result=None, error=None, file="<code>"):
    return {"event": "world_query", "query_index": index, "name": name,
            "args": [], "kwargs": {}, "result": result, "error": error,
            "caller": {"file": file, "line": line, "function": "<module>"},
            "api_calls_before": 1, "api_trace_step": 1}


# --- (a) logging-only: no query at all ------------------------------------

NO_QUERY = '''import world

obs = get_observation()
world.update(obs, None)
move_to_joints([0.0, 0.1, 0.2])
'''


def test_world_updated_but_never_queried():
    report = audit.audit(NO_QUERY)
    assert report["status"] == audit.NO_QUERY
    assert report["sites"] == []
    assert report["recorded_queries"] == 0
    assert "never consulted" in report["summary"]


# --- (a) logging-only: the answer is discarded or only printed ------------

DISCARDED = '''import world

world.update(get_observation(), None)
world.query("bowl_shift")
move_to_joints([0.0, 0.1])
'''

PRINTED = '''import world

world.update(get_observation(), None)
answer = world.query("bowl_shift")
print("world says", answer)
move_to_joints([0.0, 0.1])
'''

BOUND_UNREAD = '''import world

world.update(get_observation(), None)
answer = world.query("bowl_shift")
move_to_joints([0.0, 0.1])
'''

INERT_BRANCH = '''import world

answer = world.query("bowl_shift")
if answer["confident"]:
    print("confident")
move_to_joints([0.0, 0.1])
'''


def test_discarded_query_is_logging_only():
    line = line_of(DISCARDED, 'world.query("bowl_shift")')
    report = audit.audit(DISCARDED, trace=[row("bowl_shift", line)])
    assert report["status"] == audit.LOGGING_ONLY
    site, = report["sites"]
    assert site["classification"] == audit.LOGGING_ONLY
    assert site["name"] == "bowl_shift"
    assert site["line"] == line
    assert [f["form"] for f in site["forms"]] == ["discarded"]
    # A recorded query is corroboration of asking, never of using.
    assert site["recorded_queries"] == 1
    assert site["corroborated"] is False


def test_printed_query_is_logging_only():
    line = line_of(PRINTED, 'answer = world.query')
    report = audit.audit(PRINTED, trace=[row("bowl_shift", line)])
    assert report["status"] == audit.LOGGING_ONLY
    site, = report["sites"]
    assert [f["form"] for f in site["forms"]] == ["printed"]
    assert site["bound_names"] == ["answer"]


def test_bound_and_never_read_is_logging_only():
    report = audit.audit(BOUND_UNREAD)
    site, = report["sites"]
    assert site["classification"] == audit.LOGGING_ONLY
    assert "never read" in site["forms"][0]["detail"]


def test_branch_that_does_not_act_is_logging_only():
    line = line_of(INERT_BRANCH, "answer = world.query")
    report = audit.audit(INERT_BRANCH, trace=[row("bowl_shift", line)])
    assert report["status"] == audit.LOGGING_ONLY
    site, = report["sites"]
    assert [f["form"] for f in site["forms"]] == ["inert_condition"]


# --- (b) a supported, corroborated use candidate --------------------------

TARGET = '''import world

obs = get_observation()
world.update(obs, None)
shift = world.query("bowl_shift")
joints = solve_ik([shift["dx"], shift["dy"], 0.9], [0.0, 1.0, 0.0, 0.0])
move_to_joints(joints)
'''

ACTING_BRANCH = '''import world

grasp = world.query("grasp_state")
if not grasp["holding"]:
    open_gripper()
    move_to_joints([0.0, 0.1])
'''


def test_answer_reaching_a_motion_target_is_a_supported_candidate():
    line = line_of(TARGET, "shift = world.query")
    report = audit.audit(TARGET, trace=[row("bowl_shift", line)])
    assert report["status"] == audit.SUPPORTED
    site, = report["sites"]
    assert site["classification"] == audit.SUPPORTED
    assert site["corroborated"] is True
    forms = {f["form"] for f in site["forms"]}
    assert forms == {"action_argument"}
    # Both `dx` and `dy` reach the same motion call, each recorded at its location.
    consumers = [f for f in site["forms"] if f["form"] == "action_argument"]
    assert len(consumers) == 2
    assert {f["line"] for f in consumers} == {line_of(TARGET, "joints = solve_ik")}
    assert all("solve_ik" in f["detail"] for f in consumers)
    # The rebinding after the consumer is already explained by the consumer.
    assert site["notes"] == []


def test_answer_deciding_an_acting_branch_is_a_supported_candidate():
    line = line_of(ACTING_BRANCH, "grasp = world.query")
    report = audit.audit(ACTING_BRANCH, trace=[row("grasp_state", line)])
    assert report["status"] == audit.SUPPORTED
    site, = report["sites"]
    assert [f["form"] for f in site["forms"]] == ["control_condition"]
    assert site["forms"][0]["line"] == line_of(ACTING_BRANCH, "if not grasp")


def test_candidate_without_a_recorded_query_stays_inconclusive():
    """Static syntax alone never passes: the trial may never have reached it."""
    report = audit.audit(TARGET, trace=[])
    assert report["status"] == audit.INCONCLUSIVE
    site, = report["sites"]
    assert site["classification"] == audit.INCONCLUSIVE
    assert site["corroborated"] is False
    assert any("no corroborating recorded query" in n["detail"] for n in site["notes"])


def test_a_failed_query_does_not_corroborate():
    line = line_of(TARGET, "shift = world.query")
    report = audit.audit(TARGET, trace=[row("bowl_shift", line, error="KeyError('bowl_shift')")])
    assert report["status"] == audit.INCONCLUSIVE
    site, = report["sites"]
    assert (site["recorded_queries"], site["recorded_errors"]) == (0, 1)


# --- (c) unsupported Python stays inconclusive ----------------------------

HELPER_BOUNDARY = '''import world

def go(target):
    move_to_joints(solve_ik(target, [0.0, 1.0, 0.0, 0.0]))

shift = world.query("bowl_shift")
go([shift["dx"], shift["dy"], 0.9])
'''

CONTAINER = '''import world

shift = world.query("bowl_shift")
plan = {"dx": shift["dx"]}
move_to_joints([plan["dx"], 0.1])
'''

COMPREHENSION = '''import world

shift = world.query("bowl_shift")
offsets = [v + 0.01 for v in shift["deltas"]]
move_to_joints(offsets)
'''


def test_helper_boundary_is_inconclusive_not_a_pass_and_not_a_fail():
    line = line_of(HELPER_BOUNDARY, "shift = world.query")
    report = audit.audit(HELPER_BOUNDARY, trace=[row("bowl_shift", line)])
    assert report["status"] == audit.INCONCLUSIVE
    site, = report["sites"]
    assert site["classification"] == audit.INCONCLUSIVE
    assert site["classification"] not in (audit.SUPPORTED, audit.LOGGING_ONLY)
    assert any("go()" in n["detail"] for n in site["notes"])


def test_container_hop_is_inconclusive():
    line = line_of(CONTAINER, "shift = world.query")
    report = audit.audit(CONTAINER, trace=[row("bowl_shift", line)])
    assert report["status"] == audit.INCONCLUSIVE
    assert report["sites"][0]["notes"]


def test_comprehension_is_inconclusive():
    line = line_of(COMPREHENSION, "shift = world.query")
    report = audit.audit(COMPREHENSION, trace=[row("bowl_shift", line)])
    assert report["status"] == audit.INCONCLUSIVE
    assert report["sites"][0]["classification"] == audit.INCONCLUSIVE


# --- runtime/source identity mismatch ------------------------------------


def test_recorded_query_with_no_matching_call_site_is_a_mismatch():
    report = audit.audit(TARGET, trace=[row("bowl_shift", 999)])
    assert report["status"] == audit.IDENTITY_MISMATCH
    mismatch, = report["mismatches"]
    assert mismatch["kind"] == "unmatched_recorded_query"
    assert mismatch["caller_line"] == 999
    # A mismatch withholds corroboration everywhere, including from a site that
    # would otherwise have passed.
    assert all(not site["corroborated"] for site in report["sites"])


def test_recorded_query_name_must_match_the_call_site():
    line = line_of(TARGET, "shift = world.query")
    report = audit.audit(TARGET, trace=[row("something_else", line)])
    assert report["status"] == audit.IDENTITY_MISMATCH


def test_policy_digest_mismatch_is_reported():
    report = audit.audit(TARGET, expected_policy_sha256="0" * 64)
    assert report["status"] == audit.IDENTITY_MISMATCH
    mismatch, = report["mismatches"]
    assert mismatch["kind"] == "policy_digest"
    assert mismatch["analyzed"] == hashlib.sha256(TARGET.encode()).hexdigest()


# --- honesty at the code-block boundary ----------------------------------
#
# The replay splits the policy on `# Code block N` and runs the blocks in order
# in one namespace, and the caller line it records is block-local. Every fixture
# below is ordinary replay code that the first implementation judged with a claim
# the evidence did not support.

CROSS_BLOCK_IMPORT = '''# Code block 1
import world

# Code block 2
value = world.query("target")
move_to_joints(value)
'''

CROSS_BLOCK_CONSUMER = '''# Code block 1
import world

value = world.query("target")

# Code block 2
move_to_joints(value)
'''

SHARED_BLOCK_LINE = '''# Code block 1
import world

value = world.query("target")
print(value)

# Code block 2
import world

value = world.query("target")
move_to_joints(value)
'''

LATER_BLOCK_UNPARSEABLE = '''# Code block 1
import world

value = world.query("target")

# Code block 2
move_to_joints(
'''

UNPARSEABLE = '''import world
value = world.query(
'''


def test_world_imported_in_an_earlier_block_is_recognized_not_called_a_mismatch():
    """The blocks share a namespace, so block 2 needs no import of its own."""
    report = audit.audit(CROSS_BLOCK_IMPORT, trace=[row("target", 1)])
    assert report["mismatches"] == []
    assert report["status"] == audit.SUPPORTED
    site, = report["sites"]
    assert (site["block"], site["block_line"]) == (1, 1)
    assert site["line"] == line_of(CROSS_BLOCK_IMPORT, "value = world.query")
    assert {f["form"] for f in site["forms"]} == {"action_argument"}
    assert site["corroborated"] is True
    assert report["blocks"][1]["world_import_inherited"] is True
    assert report["blocks"][0]["world_import_inherited"] is False


def test_a_binding_a_later_block_consumes_is_inconclusive_not_logging_only():
    report = audit.audit(CROSS_BLOCK_CONSUMER, trace=[row("target", 3)])
    assert report["status"] == audit.INCONCLUSIVE
    site, = report["sites"]
    assert site["classification"] == audit.INCONCLUSIVE
    assert site["bound_names"] == ["value"]
    assert site["recorded_queries"] == 1
    assert site["corroborated"] is False
    # The claim that had to go: this reader cannot see block 2's read.
    assert not any("never read" in f["detail"] for f in site["forms"])
    assert any("later code block" in n["detail"] for n in site["notes"])


def test_an_unparseable_later_block_does_not_prove_the_binding_unread():
    report = audit.audit(LATER_BLOCK_UNPARSEABLE, trace=[row("target", 3)])
    assert report["status"] == audit.INCONCLUSIVE
    site, = report["sites"]
    assert site["classification"] == audit.INCONCLUSIVE
    assert any("does not parse" in n["detail"] for n in site["notes"])


def test_a_shared_block_local_line_corroborates_neither_site():
    """One record, two candidate sites: evidence about the pair, not about either."""
    report = audit.audit(SHARED_BLOCK_LINE, trace=[row("target", 3)])
    assert report["mismatches"] == []
    assert report["status"] == audit.INCONCLUSIVE
    printed, acting = report["sites"]
    assert [s["block"] for s in report["sites"]] == [0, 1]
    assert all(s["ambiguous"] is True for s in report["sites"])
    assert all(s["corroborated"] is False for s in report["sites"])
    # The acting site is the one the old verdict passed on an ambiguous match.
    assert {f["form"] for f in acting["forms"]} == {"action_argument"}
    assert acting["classification"] == audit.INCONCLUSIVE
    assert any("more than one call site" in n["detail"] for n in acting["notes"])
    # The print-only site is not upgraded, and is not called logging-only either:
    # block 2 reads that name, and this reader does not resolve a later rebinding.
    assert printed["classification"] == audit.INCONCLUSIVE


def test_a_block_that_does_not_parse_is_inconclusive_never_no_query():
    report = audit.audit(UNPARSEABLE)
    assert report["status"] == audit.INCONCLUSIVE
    assert report["blocks"][0]["parsed"] is False
    assert report["sites"] == []
    assert any("does not parse" in note for note in report["notes"])
    assert "never consulted" not in report["summary"]


# --- one ordinary observation-driven positive demonstration ---------------

DEMO = '''# Code block 1
import numpy as np
import world

obs = get_observation()
mask = segment_sam3_text_prompt(obs["rgb"], "the white bowl")
points = mask_to_world_points(mask, obs["depth"], obs["intrinsics"], obs["extrinsics"])
world.update({"bowl_points": points.tolist(), "eef": obs["robot_state"]}, None)

# Code block 2
import world

approach = world.query("bowl_approach")
if approach["reachable"]:
    joints = solve_ik(approach["position"], approach["quaternion"])
    move_to_joints(joints)
    close_gripper()
else:
    goto_home_joint_position()
'''


def test_observation_driven_policy_passes_on_source_and_trace_together():
    query_line = line_of(DEMO, "approach = world.query")
    blocks = audit.code_blocks(DEMO)
    assert [b["index"] for b in blocks] == [0, 1]
    # The recorded caller line is block-local, and must still resolve to the file.
    report = audit.audit(DEMO, trace=[row("bowl_approach", 3)])
    assert report["status"] == audit.SUPPORTED
    site, = report["sites"]
    assert site["block"] == 1
    assert site["block_line"] == 3
    assert site["line"] == query_line
    assert site["corroborated"] is True
    assert {f["form"] for f in site["forms"]} == {"control_condition", "action_argument"}
    assert report["blocks"][1]["query_sites"] == 1
    assert report["blocks"][0]["query_sites"] == 0
    assert report["policy_sha256"] == hashlib.sha256(DEMO.encode()).hexdigest()
    assert all(b["sha256"] for b in report["blocks"])
    # Honesty: the pass is still only a candidate.
    assert any("not evidence that the consumer statement executed" in limit
               for limit in report["limits"])


def test_block_offsets_do_not_shift_the_unflagged_single_block_case():
    line = line_of(TARGET, "shift = world.query")
    blocks = audit.code_blocks(TARGET)
    assert len(blocks) == 1 and blocks[0]["line_offset"] == 0
    assert audit.audit(TARGET, trace=[row("bowl_shift", line)])["status"] == audit.SUPPORTED


# --- trial directory integration -----------------------------------------


def write_trial(directory, policy, trace, *, policy_sha256=None):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "code.py").write_text(policy)
    (directory / "judgment_world_config.json").write_text(json.dumps(
        {"mode": "opus46-judgment-world",
         "policy_sha256": policy_sha256 or hashlib.sha256(policy.encode()).hexdigest()}))
    world_dir = directory / "judgment_world"
    world_dir.mkdir(exist_ok=True)
    (world_dir / "events.jsonl").write_text("".join(
        json.dumps(r) + "\n" for r in
        [{"event": "world_update", "seq": 0}] + list(trace)))
    (world_dir / "manifest.json").write_text(json.dumps(
        {"status": "complete", "world_sha256": "b" * 64, "queries": len(trace)}))
    return directory


def test_trial_feedback_reads_a_recorded_trial_and_writes_its_audit(tmp_path):
    line = line_of(TARGET, "shift = world.query")
    directory = write_trial(tmp_path / "attempt_01", TARGET, [row("bowl_shift", line)])
    feedback = audit.trial_feedback(directory)
    assert feedback["status"] == audit.SUPPORTED
    assert feedback["revision"] == audit.REVISION
    assert feedback["world_sha256"] == "b" * 64
    assert feedback["recorded_queries"] == 1
    assert feedback["trace_available"] is True
    assert feedback["limits"]
    saved = json.loads((directory / audit.ARTIFACT).read_text())
    assert saved["status"] == audit.SUPPORTED
    assert saved["trial_dir"] == str(directory)
    assert saved["world_manifest_status"] == "complete"


def test_trial_feedback_never_raises_on_a_directory_it_cannot_read(tmp_path):
    feedback = audit.trial_feedback(tmp_path / "missing")
    assert feedback["status"] == "unavailable"
    assert "FileNotFoundError" in feedback["detail"]


def test_selected_pair_report_prefers_a_single_corroborated_execution(tmp_path):
    line = line_of(TARGET, "shift = world.query")
    used = write_trial(tmp_path / "a", TARGET, [row("bowl_shift", line)])
    unused = write_trial(tmp_path / "b", DISCARDED,
                         [row("bowl_shift", line_of(DISCARDED, 'world.query("bowl_shift")'))])
    report = audit.selected_pair_report([unused, used])
    assert report["status"] == audit.SUPPORTED
    assert report["trials_audited"] == 2
    assert not (used / audit.ARTIFACT).exists()  # the pair report writes nothing


def test_selected_pair_report_of_logging_only_executions_says_so(tmp_path):
    line = line_of(DISCARDED, 'world.query("bowl_shift")')
    one = write_trial(tmp_path / "a", DISCARDED, [row("bowl_shift", line)])
    two = write_trial(tmp_path / "b", DISCARDED, [row("bowl_shift", line)])
    report = audit.selected_pair_report([one, two])
    assert report["status"] == audit.LOGGING_ONLY
    assert "discarded" in report["summary"]


def test_selected_pair_report_with_no_executions_is_unavailable():
    report = audit.selected_pair_report([])
    assert report["status"] == "unavailable"
    assert report["trials_audited"] == 0
