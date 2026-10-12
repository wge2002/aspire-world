"""CPU tests for the single-cell streaming campaign launcher and its guard.

Nothing here starts a model, native CC, simulator, perception service or DLC job.
Native sessions, protocol subprocesses and service health are fakes; manifests,
hashes, prompt rendering and guard decisions are the real code.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts/libero"))
sys.path.insert(0, str(REPO / "scripts/common"))
import native_world_campaign as legacy  # noqa: E402
import streaming_fixloop_campaign as camp  # noqa: E402
import streaming_fixloop_guard as guard  # noqa: E402
import streaming_fixloop_protocol as sp  # noqa: E402
from streaming_fixloop_heldout import require_outer  # noqa: E402
from statepack_fixture import statepack  # noqa: E402

MODEL = "qwen3.8-flash-next"
TEMPLATE = ".claude/libero/fix-loop/subagent-prompt.md"


def manifest(path: Path, content: dict) -> tuple[str, str]:
    path.write_text(json.dumps(content))
    return str(path), sp.file_sha256(path)


@pytest.fixture
def cell(tmp_path):
    sim = tmp_path / "runtime/aspire/sim"
    (sim / TEMPLATE).parent.mkdir(parents=True)
    (sim / TEMPLATE).write_bytes((REPO / TEMPLATE).read_bytes())
    (sim / "env_configs/libero").mkdir(parents=True)
    (sim / "env_configs/libero/franka_libero_traced.yaml").write_text("# stub\n")
    control = tmp_path / "campaigns/bowl_sam2streaming_r1"
    (tmp_path / "campaigns/drawer_sam2streaming_r1").mkdir(parents=True)
    control.mkdir(parents=True)
    libero_config = tmp_path / "libero-config"
    libero_config.mkdir()
    (libero_config / "config.yaml").write_text("benchmark_root: /frozen\n")
    claude = tmp_path / "bin/claude"
    claude.parent.mkdir()
    claude.write_text("#!/bin/sh\necho '2.1.220 (Claude Code)'\n")
    claude.chmod(0o755)
    urls = {"sam3_url": "http://127.0.0.1:8214", "graspnet_url": "http://127.0.0.1:8215",
            "pyroki_url": "http://127.0.0.1:8216"}
    service, service_sha = manifest(tmp_path / "model.json", {
        "endpoint": "http://127.0.0.1:8121", "served_model": MODEL, "max_model_len": 1000000})
    perception, perception_sha = manifest(tmp_path / "perception.json", urls)
    task, task_sha = manifest(tmp_path / "task.json", {
        "suite": "libero_goal_swap", "task": "put_the_bowl_on_the_plate",
        "environment_version": camp.ENVIRONMENT_VERSION,
        "seed_to_init_state_index": camp.SEED_MAPPING, "libero_config_path": str(libero_config)})
    case = {"id": "bowl_sam2streaming_r1", "method": "sam2-streaming", "repeat": 1,
            "profile": sp.PROFILE, "sim": str(sim), "control": str(control),
            "suite": "libero_goal_swap", "task": "put_the_bowl_on_the_plate",
            "dev_seeds": list(range(51, 66)),
            **{k: v for k, v in sp.FIXED.items() if k not in {"condition", "development_gate"}},
            "model_service": {"endpoint": "http://127.0.0.1:8121", "served_model": MODEL,
                              "server_manifest": service, "server_manifest_sha256": service_sha},
            "cuda_visible_devices": "0", "egl_device_id": 0, **urls,
            "perception_manifest": perception, "perception_manifest_sha256": perception_sha,
            "dependency_evidence": "frozen", "require_runtime_freeze": True,
            "runtime_manifest": str(tmp_path / "runtime_manifest.json"),
            "runtime_manifest_sha256": "bound-by-outer-freeze",
            "streaming": {"camera": "agentview", "sam2": {
                "source_root": str(tmp_path / "SAM2_streaming"), "config_file": "sam2.1/s.yaml",
                "checkpoint": str(tmp_path / "sam2.pt"), "device": "cuda"}},
            "claude_bin": str(claude), "claude_config_dir": str(control / "claude-config"),
            "libero_config_path": str(libero_config),
            "task_manifest": task, "task_manifest_sha256": task_sha,
            # A real (fixture) common statepack: formal campaigns require it.
            **statepack(tmp_path)}
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case))
    return SimpleNamespace(path=path, case=case, sim=sim, control=control, tmp=tmp_path)


def stream(model=MODEL, context=1000000):
    return "\n".join(json.dumps(r) for r in (
        {"type": "assistant", "message": {"model": model}},
        {"type": "result", "usage": {}, "modelUsage": {"q": {"contextWindow": context,
                                                              "maxOutputTokens": 32000}}})) + "\n"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def harness(cell, monkeypatch):
    """Replace every process boundary; keep manifests, hashing and rendering real."""
    h = SimpleNamespace(events=[], clock=Clock(), sessions=[], steps={}, codes={}, context=1000000)
    monkeypatch.setattr(camp, "verify_runtime", lambda case, repo: h.events.append("freeze") or "rt")
    monkeypatch.setattr(camp, "writable", lambda p: Path(p).resolve() != cell.sim.resolve())
    monkeypatch.setattr(camp, "runtime_user", lambda: {"uid": 1001, "user": "cell_bowl_r1"})
    monkeypatch.setattr(camp, "perception_ready", lambda case: {"sam3_url": 200})

    def probe_run(command, *, cwd, env, stdout_path, stderr_path, timeout, **_):
        h.events.append("probe")
        Path(stdout_path).write_text(stream(context=h.context))
        return 0
    monkeypatch.setattr(legacy, "run_native_cc", probe_run)

    def solver(command, *, cwd, env, stdout_path, stderr_path, timeout, **_):
        h.events.append("solver")
        h.sessions.append({"command": command, "env": env, "timeout": timeout})
        Path(stdout_path).write_text(stream())
        h.clock.now += 3600
        return h.codes.get("solver", 0)
    monkeypatch.setattr(camp, "run_native_cc", solver)

    def step(case_path, repo, env, control, name, script, *args):
        h.events.append(name)
        h.steps[name] = list(args)
        if name == "init":
            task = Path(cell.case["control"]) / "cell"
            task.mkdir(parents=True, exist_ok=True)
            (task / "development_state.json").write_text(json.dumps({"phase_clock": {
                "development": {"started_at": h.clock(), "deadline": h.clock() + 50400}}}))
        if name == "heldout":
            require_outer(cell.case)
        return {"step": name, "exit_code": h.codes.get(name, 0), "result": {},
                "stderr_tail": ""}
    monkeypatch.setattr(camp, "outer_step", step)
    return h


def run(cell, harness, **kw):
    return camp.run(cell.path, clock=harness.clock, **kw)


def state(cell):
    return json.loads((cell.control / "campaign_state.json").read_text())


# --- sequence --------------------------------------------------------------------


def test_outer_step_preserves_refusal_after_import_banner(cell, monkeypatch):
    output = '[info] loading simulator\n{\n  "error": "authorization mismatch",\n  "type": "ProtocolError"\n}\n'
    monkeypatch.setattr(camp.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 2, output, "warning\n"))
    result = camp.outer_step(cell.path, cell.sim, {}, cell.control, "heldout",
                             "scripts/libero/streaming_fixloop_heldout.py")
    assert result["exit_code"] == 2
    assert result["result"]["error"] == "authorization mismatch"
    assert Path(result["stdout_path"]).read_text() == output
    assert Path(result["stderr_path"]).read_text() == "warning\n"


def test_full_sequence_records_provenance_then_heldout_after_solver_exit(cell, harness):
    assert run(cell, harness) == 0
    assert harness.events == ["freeze", "probe", "init", "solver", "freeze", "check",
                              "finalize", "heldout"]
    s = state(cell)
    session = Path(s["sessions"][0]["session"])
    assert s["status"] == "complete" and s["sessions"][0]["solver_timeout"] == 50400
    # finalize receives the settings and probe that were actually used.
    args = harness.steps["finalize"]
    assert args[args.index("--worker-settings") + 1] == str(session / "worker-settings.json")
    assert args[args.index("--probe") + 1] == str(session / "probe.json")
    assert args[args.index("--transcript") + 1] == str(session / "coordinator.stdout.jsonl")
    settings = json.loads((session / "worker-settings.json").read_text())
    probe = json.loads((session / "probe.json").read_text())
    record, errors = sp.native_provenance(
        cell.case, {"model_served": {MODEL: 1}, "context_windows": [1000000]},
        session / "worker-settings.json", session / "probe.json")
    assert errors == [] and probe["configured"]["effort"] == "xhigh"
    # The settings passed to the worker are exactly the ones recorded.
    command = harness.sessions[0]["command"]
    assert json.loads(command[command.index("--settings") + 1]) == settings
    native = json.loads((session / "native-command.json").read_text())
    assert native["dangerously_skip_permissions"] is False
    assert "--dangerously-skip-permissions" not in command
    for flag in ("--strict-mcp-config", "--no-chrome"):
        assert flag in command
    assert command[command.index("--setting-sources") + 1] == ""
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[:5] == [cell.case["claude_bin"], "--model", MODEL, "--effort", "xhigh"]
    env = harness.sessions[0]["env"]
    assert env["LIBERO_CONFIG_PATH"] == cell.case["libero_config_path"]
    assert env["CLAUDE_CONFIG_DIR"] == cell.case["claude_config_dir"]
    assert not any(k.startswith("ANTHROPIC") or "API_KEY" in k for k in env)
    info = s["sessions"][0]
    assert info["native_binary"]["version_output"].startswith("2.1.220")
    assert info["task"]["manifest"]["verified"]["environment_version"] == camp.ENVIRONMENT_VERSION


def test_worker_settings_route_every_alias_to_local_qwen_with_full_guard(cell):
    settings = camp.worker_settings(cell.case, cell.sim, cell.path)
    assert "apiKeyHelper" not in settings
    assert all(settings["env"][a] == MODEL for a in legacy.LOCAL_VLLM_MODEL_ALIASES)
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8121"
    assert settings["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"
    assert settings["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "1000000"
    hook = settings["hooks"]["PreToolUse"][0]
    assert set(hook["matcher"].split("|")) >= {"Read", "Write", "Edit", "MultiEdit", "Bash",
                                               "Glob", "Grep"}
    assert "streaming_fixloop_guard.py" in hook["hooks"][0]["command"]
    assert "native_cc_guard.py" not in hook["hooks"][0]["command"]


def test_no_hidden_restart_and_resume_keeps_ledger_deadline(cell, harness):
    harness.codes["solver"] = 1
    harness.codes["check"] = 1
    assert run(cell, harness, heldout=False) == 1
    assert state(cell)["status"] == "stage1_incomplete"
    with pytest.raises(camp.LaunchRefused, match="no hidden restart"):
        run(cell, harness)
    harness.codes.clear()
    harness.events.clear()
    assert run(cell, harness, resume=True, heldout=False) == 0
    assert "init" not in harness.events  # never re-initialized
    s = state(cell)
    assert len(s["sessions"]) == 2 and s["status"] == "stage1_finalized"
    # The second session gets only what is left of the original window.
    assert harness.sessions[1]["timeout"] == 50400 - 3600
    transcripts = [a for a in harness.steps["finalize"] if a.endswith(".jsonl")]
    assert len(transcripts) == 2
    assert not (cell.control / guard.OUTER_MARKER).exists()
    with pytest.raises(camp.LaunchRefused, match="cannot resume"):
        run(cell, harness, resume=True)


def test_expired_deadline_starts_no_solver_and_missing_proof_is_incomplete(cell, harness):
    harness.codes["solver"] = 1
    harness.codes["check"] = 1
    run(cell, harness, heldout=False)
    harness.clock.now += 50400
    harness.codes.clear()
    harness.codes["finalize"] = 2
    harness.events.clear()
    assert run(cell, harness, resume=True) == 1
    s = state(cell)
    assert "solver" not in harness.events and s["sessions"][1]["solver_timeout"] == 0
    assert s["status"] == "incomplete" and "heldout" not in harness.events


def test_probe_without_actual_1m_context_blocks_before_init(cell, harness):
    harness.context = 262144
    assert run(cell, harness) == 1
    assert state(cell)["status"] == "blocked" and "init" not in harness.events


# --- preflight refusals -------------------------------------------------------------


@pytest.mark.parametrize("change,match", [
    ({"dependency_evidence": "cpu-test-fake"}, "frozen"),
    ({"claude_version": "2.1.219"}, "2.1.220"),
    ({"tasks": ["bowl", "drawer"]}, "one cell"),
    ({"claude_config_dir": "/tmp/shared-claude"}, "claude_config_dir"),
    ({"libero_config_path": None}, "libero_config_path"),
])
def test_case_shape_refusals(cell, change, match):
    cell.path.write_text(json.dumps({**cell.case, **change}))
    with pytest.raises((camp.LaunchRefused, sp.ProtocolError), match=match):
        camp.load(cell.path)


def test_isolation_preconditions_are_verified_not_created(cell, harness, monkeypatch):
    monkeypatch.setattr(camp, "writable", lambda p: True)
    with pytest.raises(camp.LaunchRefused, match="read-only"):
        run(cell, harness)
    # Refused before any write: no state, no session directory, nothing ran.
    assert not (cell.control / "campaign_state.json").exists()
    assert not (cell.control / "sessions").exists() and harness.events == []
    with pytest.raises(camp.LaunchRefused, match="/root"):
        camp.isolation({**cell.case, "control": str(cell.control)}, Path("/root/engineering/aspire/sim"))


def test_runtime_user_refuses_root_and_repurposed_home(monkeypatch):
    monkeypatch.setattr(camp.os, "geteuid", lambda: 0)
    monkeypatch.setattr(camp.os, "getegid", lambda: 0)
    with pytest.raises(camp.LaunchRefused, match="root"):
        camp.runtime_user()
    nobody = camp.pwd.getpwnam("nobody")
    monkeypatch.setattr(camp.os, "geteuid", lambda: nobody.pw_uid)
    monkeypatch.setattr(camp.os, "getegid", lambda: nobody.pw_gid)
    monkeypatch.setenv("HOME", "/tmp/fake-home")
    with pytest.raises(camp.LaunchRefused, match="HOME"):
        camp.runtime_user()
    monkeypatch.setenv("HOME", nobody.pw_dir)
    assert camp.runtime_user()["user"] == "nobody"


def test_wrong_binary_or_task_manifest_is_refused(cell, harness):
    Path(cell.case["claude_bin"]).write_text("#!/bin/sh\necho '2.1.219 (Claude Code)'\n")
    assert run(cell, harness) == 2 and "2.1.220" in state(cell)["blocker"]
    Path(cell.case["claude_bin"]).write_text("#!/bin/sh\necho '2.1.220 (Claude Code)'\n")
    Path(cell.case["task_manifest"]).write_text(json.dumps({"environment_version": "old"}))
    (cell.control / "campaign_state.json").unlink()
    assert run(cell, harness) == 2 and "task manifest" in state(cell)["blocker"]
    assert "probe" not in harness.events


# --- prompt -----------------------------------------------------------------------


def test_rendered_prompt_is_the_legacy_template_with_mechanical_edits_only(cell):
    text = camp.worker_prompt(cell.case, REPO)
    task_dir = str(cell.control.resolve() / "cell")
    assert camp.PROTOCOL in text and legacy.PROTOCOL not in text
    for gone in ("--world-program", "--inventory", "outputs/working_codes",
                 "outputs/libero_fix_loop", ".claude/libero/skills/", "localize.md"):
        assert gone not in text, gone
    assert f'TASK_DIR="{task_dir}"' in text
    for stem in ("initial", "fix"):
        assert f'--streaming-config "$TASK_DIR/{stem}_streaming_config.json"' in text
    assert "Streaming world contract" in text and "task_completed" in text
    # The whole original workflow is kept: initial 15, repair, selection, findings.
    for kept in ("Stage 1: Debug Seeds 51–65", "Synthesize task-level fix", "alias-smoke",
                 "3 TOTAL retries per seed", "select --reason", "findings.md"):
        assert kept in text, kept
    for added in ("max_actions", "query cap", "15 versions"):
        assert added not in text
    camp.write_prompts(cell.case, REPO, cell.control)
    assert (cell.control / "worker-prompt.diff").read_text().startswith("---")
    coordinator = (cell.control / "coordinator-prompt.md").read_text()
    assert camp.PROTOCOL in coordinator and "no high-level strategy library" in coordinator


def test_legacy_campaign_and_guard_are_untouched():
    out = subprocess.run(["git", "status", "--porcelain", "scripts/libero/native_world_campaign.py",
                          "scripts/common/native_cc_guard.py", "scripts/libero/native_world_protocol.py",
                          "scripts/libero/native_world_heldout.py"],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout == ""
    assert legacy.PROTOCOL == "scripts/libero/native_world_protocol.py"


# --- guard ------------------------------------------------------------------------


def ask(cell, tool, **values):
    case = {**cell.case, "sim": str(cell.sim)}
    return guard.denial({"tool_name": tool, "tool_input": values, "cwd": str(cell.sim)},
                        case, cell.sim.resolve())


@pytest.mark.parametrize("path", [
    ".claude/libero/skills/grasp.md", "outputs/libero_fix_loop/libero_goal_swap/x/fix_code.py",
    "docs/experiments/old/RESULTS.md", "docs/logs/2026-10-09.md",
    "cap/third_party/LIBERO-PRO/libero/libero/bddl_files/x.bddl",
    "cap/third_party/LIBERO-PRO/libero/libero/envs/env_wrapper.py",
    "assets/scene.xml"])
def test_guard_refuses_condition_c_forbidden_reads(cell, path):
    assert ask(cell, "Read", file_path=str(cell.sim / path))


def test_guard_refuses_heldout_marker_and_other_cells(cell):
    for path in (cell.control / "heldout/report.json", cell.control / guard.OUTER_MARKER,
                 cell.tmp / "campaigns/drawer_sam2streaming_r1/cell/fix_code.py"):
        assert ask(cell, "Read", file_path=str(path))
        assert ask(cell, "Write", file_path=str(path), content="x")
    assert ask(cell, "Grep", pattern="success", path=str(cell.tmp / "campaigns/drawer_sam2streaming_r1"))
    assert ask(cell, "Glob", pattern="**/heldout/**", path=str(cell.control))
    assert ask(cell, "Bash", command=f"cat {cell.tmp}/campaigns/drawer_sam2streaming_r1/cell/notes.md")
    assert ask(cell, "Bash", command=f"ls {cell.control}/heldout")


def test_guard_allows_reading_public_sources_and_own_cell(cell):
    task = cell.control / "cell"
    assert ask(cell, "Read", file_path=str(cell.sim / "cap/integrations/franka/libero_reduced.py")) is None
    assert ask(cell, "Read", file_path=str(cell.sim / ".claude/libero/api-reference.md")) is None
    assert ask(cell, "Read", file_path=str(task / "attempts/seed_51/results/trace.json")) is None
    assert ask(cell, "Glob", pattern="*.py", path=str(task)) is None


@pytest.mark.parametrize("name", ["initial_code.py", "fix_code.py", "initial_streaming_config.json",
                                  "fix_streaming_config.json", "notes.md", "findings.md",
                                  "task_analysis.md", "attempts/seed_53_BLOCKED.md",
                                  "analysis/plot.py", "skills/approach.md"])
def test_guard_allows_authored_cell_files(cell, name):
    assert ask(cell, "Write", file_path=str(cell.control / "cell" / name), content="x") is None


@pytest.mark.parametrize("name", ["development_state.json", "stage1_result.json",
                                  "attempts/seed_51/results/trace.json", "development/x.json",
                                  "../worker-settings.json", "../guard-audit.jsonl"])
def test_guard_refuses_ledger_evidence_and_control_writes(cell, name):
    assert ask(cell, "Edit", file_path=str(cell.control / "cell" / name), old_string="a",
               new_string="b")
    assert ask(cell, "Write", file_path=str(cell.sim / "cap/world_model/World.py"), content="x")


TRIAL = ("cd /x && .venv-libero/bin/python3 scripts/libero/streaming_fixloop_protocol.py trial \\\n"
         "    --phase initial --seed 52 --code \"$TASK_DIR/initial_code.py\" \\\n"
         "    --streaming-config \"$TASK_DIR/initial_streaming_config.json\" 2>&1 | tail -40")


def test_guard_bash_admits_one_foreground_protocol_call(cell):
    assert ask(cell, "Bash", command=TRIAL) is None
    for sub in ("status", "check", "select --reason 'best'", "alias-smoke --seed 51", "snapshot"):
        assert ask(cell, "Bash", command=f".venv-libero/bin/python3 {camp.PROTOCOL} {sub}") is None
    assert ask(cell, "Bash", command="python3 analysis/plot.py > analysis/out.txt 2>&1") is None


@pytest.mark.parametrize("command", [
    f".venv-libero/bin/python3 {camp.PROTOCOL} init",
    f".venv-libero/bin/python3 {camp.PROTOCOL} finalize --transcript x",
    f".venv-libero/bin/python3 {camp.PROTOCOL} resolve-interrupted --directory a",
    f".venv-libero/bin/python3 {camp.PROTOCOL} --case /other/case.json status",
    "for s in 52 53; do " + TRIAL + "; done",
    TRIAL + " &",
    "nohup " + TRIAL,
    ".venv-libero/bin/python3 scripts/libero/replay_trial.py --args.trial 1",
    ".venv-libero/bin/python3 scripts/libero/streaming_fixloop_heldout.py",
    ".venv-libero/bin/python3 scripts/libero/streaming_fixloop_campaign.py --case c",
    "rm -rf cap/world_model", "sed -i s/a/b/ scripts/libero/streaming_fixloop_protocol.py",
    "echo x > cap/world_model/World.py", "echo '{}' > \"$TASK_DIR/development_state.json\"",
    "cp evil.py \"$TASK_DIR/attempts/seed_51/code.py\"",
    "grep -r success .claude/libero/skills", "cat outputs/working_codes/x.py",
    "find / -name '*.bddl'",
])
def test_guard_bash_refusals(cell, command):
    assert ask(cell, "Bash", command=command)


def test_guard_fails_closed_and_audits(cell, tmp_path):
    payload = json.dumps({"tool_name": "Read", "tool_input": {"file_path": str(cell.sim / "x.py")}})
    env = {**os.environ, "PYTHONPATH": str(REPO.parents[1])}
    proc = subprocess.run([sys.executable, str(REPO / "scripts/common/streaming_fixloop_guard.py"),
                           "--case", str(cell.path)], input=payload, text=True,
                          capture_output=True, env=env)
    # The runtime manifest named by the case does not exist: the freeze fails closed.
    assert proc.returncode == 2 and "failed closed" in proc.stderr
