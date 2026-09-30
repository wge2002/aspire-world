#!/usr/bin/env python3
"""Exercise pinned native Write/Edit in a case's real skill directory."""
import argparse
import json
import os
from pathlib import Path
import sys

from native_cc_stream import run_native_cc
from native_cc_permissions import skill_write_denials
from native_cc_guard import settings as guard_settings
from native_cc_freeze import verify_runtime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libero"))
from record_skill_promotion import begin_promotion, finish_promotion, verify_promotion, skill_hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    repo, output = Path(case["sim"]), args.output.resolve()
    verify_runtime(case, repo)
    output.mkdir(parents=True, exist_ok=False)
    skills_rel = Path(case["skill_library_dir"])
    probe = repo / skills_rel / "_native_write_probe.md"
    if probe.exists():
        raise RuntimeError("probe file already exists; do not overwrite")
    before = skill_hashes(repo, skills_rel)
    suite, task = "native_permission_probe_" + output.name.replace("-", "_"), case["id"]
    begin_promotion(repo, suite=suite, task=task, skills_rel=skills_rel)
    expected = "ASPIRE_NATIVE_EDIT_OK\n"
    prompt = f"""Perform only this bounded infrastructure permission test, no robot work.
Use the native Write tool to create {probe} with exactly ASPIRE_NATIVE_WRITE_OK followed by a newline.
Use native Read on that file, then native Edit to replace ASPIRE_NATIVE_WRITE_OK with ASPIRE_NATIVE_EDIT_OK.
Use Read to verify the final content, then reply ASPIRE_SKILL_WRITE_EDIT_OK.
Do not use Bash, agents, scripts, change other files, or solve any experiment task.
If any tool denies permission, report the denial and stop. Do not work around it.
"""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR") if k in os.environ}
    env.update(CC_LOCAL_CONFIG_DIR=str(output / "cc-config"),
               CC_LOCAL_CONTEXT_TOKENS=str(case["context_tokens"]),
               CC_LOCAL_MAX_OUTPUT_TOKENS=str(case["max_output_tokens"]),
               CC_LOCAL_EFFORT=case["effort"], CC_LOCAL_CLAUDE_BIN=case["claude_bin"],
               CLAUDE_CODE_AUTO_CONNECT_IDE="false", API_TIMEOUT_MS="300000")
    command = ["bash", str(Path(__file__).with_name("claude_with_local_model.sh")),
               case["endpoint"], case["model"], "-p", prompt, "--output-format", "stream-json",
               "--verbose", "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--no-chrome", "--permission-mode", "dontAsk", "--tools", "Read,Write,Edit",
               "--allowedTools", "Read", "Write", "Edit", "--effort", case["effort"]]
    if case.get("require_runtime_freeze"):
        command.extend(["--settings", json.dumps(guard_settings(args.case, repo))])
    rc = run_native_cc(command, cwd=repo, env=env, stdout_path=output / "cc.stdout.jsonl",
                       stderr_path=output / "cc.stderr.log", timeout=900)
    transcripts = [output / "cc.stdout.jsonl", *(output / "cc-config/projects").rglob("*.jsonl")]
    denials = skill_write_denials(transcripts, repo, str(skills_rel))
    records = [json.loads(line) for line in (output / "cc.stdout.jsonl").read_text().splitlines() if line.startswith("{")]
    calls = [b for r in records if r.get("type") == "assistant" for b in r.get("message", {}).get("content", []) if b.get("type") == "tool_use"]
    models = sorted({r["message"]["model"] for r in records if r.get("type") == "assistant" and r.get("message", {}).get("model") and not r["message"]["model"].startswith("<")})
    passed = rc == 0 and not denials and probe.is_file() and probe.read_text() == expected and models == [case["model"]]
    passed = passed and all(any(c["name"] == name and (repo / c.get("input", {}).get("file_path", "")).resolve() == probe.resolve() for c in calls) for name in ("Write", "Edit"))
    guard_checked = None
    if case.get("require_runtime_freeze"):
        audit = Path(case["control"]) / "guard-audit.jsonl"
        events = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
        guard_checked = all(any(e["tool"] == name and e.get("path") and
                               (repo / e["path"]).resolve() == probe.resolve() and not e["denied"]
                               for e in events) for name in ("Write", "Edit"))
        passed = passed and guard_checked
    record = None
    if passed:
        record = finish_promotion(repo, suite=suite, task=task, skills_rel=skills_rel)
        verify_promotion(repo, suite=suite, task=task, skills_rel=skills_rel)
        passed = record["changed_skill_files"] == [str(probe.relative_to(repo))] and not record["no_op"]
    if passed:
        probe.unlink()
        passed = skill_hashes(repo, skills_rel) == before
    verify_runtime(case, repo)
    result = dict(passed=bool(passed), case=case["id"], models=models, denials=denials,
                  native_tools=[c["name"] for c in calls], promotion=record,
                  pristine_skills_restored=bool(passed), exit_code=rc, runtime_guard_exercised=guard_checked)
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
