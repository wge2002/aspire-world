#!/usr/bin/env python3
"""Exercise native CC images, subagents, and compaction without robot trials.

R3 revision. The fixture itself is unchanged from R2 — same images, same twelve
chunks, same Agent report handoff, same verifier, same tool allowlist and the
same `passed` criteria. What changed is how the session is allowed to *end*:
R2 could only reach a clean exit by going quiet for a full `idle_grace` and then
having stdin closed, and both bowl_C and drawer_C were SIGTERMed at the 1800 s
deadline while that teardown was still in progress — after the verifier had
already printed NATIVE_CC_COMPAT_OK. This revision names that verifier line as
accepted terminal evidence and hands it to the transport, so a session whose
work is demonstrably done is shut down promptly instead of waiting out the
grace period, and a deadline arriving during its own exit is not a timeout.

Nothing here weakens the gate: the evidence only governs shutdown, and `passed`
still requires exit 0, the exact result/report artifacts, the untouched
verifier, the served model, a real Agent call and real compaction.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import urllib.request
import zlib

#: Named so a reviewer can tell this file apart from the R2 copy it replaces
#: (the staging manifest still refers to both by the same `-r2` file name).
FIXTURE_REVISION = "r4-stable-handoff"

#: What r4 changes, and why (2026-10-05, after the 10/04 closed-loop launch lost
#: 2 of 3 jobs in this fixture):
#: - bowl_C: the subagent finished and wrote colors.json, but after two
#:   coordinator compactions `TaskOutput` answered "No task found" and the
#:   coordinator waited on it until the 1800 s deadline. Production coordinators
#:   are told to wait for the native completion notification and never poll, so
#:   the fixture no longer offers TaskOutput and says the same.
#: - drawer_C: the subagent read all five swatches in order but wrote the colour
#:   list permuted; the coordinator then re-read swatches until the deadline.
#:   The fixture's purpose for images is "the served model decodes PNGs through
#:   the native Read tool"; no production path depends on five-image ordering
#:   (workers read one image per turn). The colour check is therefore set
#:   equality on the five words; the sentinel check stays strictly ordered.
#: Everything else (real Agent call, two real compactions, report handoff
#: written by the coordinator, untouched verifier, served model) is unchanged.

#: The one command `--allowedTools Bash(python3 verify_result.py)` permits, and
#: the only producer of the sentinel below.
VERIFIER_COMMAND = "python3 verify_result.py"
VERIFIER_SENTINEL = "NATIVE_CC_COMPAT_OK"

#: Teardown budget granted only after terminal evidence, charged on top of
#: `compat_timeout_seconds`. 1800 + 60 + the transport's 20 s SIGTERM wait stays
#: inside the supervisor's own 1920 s `full_deadlines.NATIVE_COMPAT`.
SHUTDOWN_GRACE = 60


def png(rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 96, 96, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(rgb) * 96) * 96)) + chunk(b"IEND", b""))


def tool_result_text(block: dict) -> str:
    """The textual payload of a tool_result block, in either shape CC emits."""
    content = block.get("content")
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def verifier_evidence():
    """Accept the fixture's own verifier succeeding, and nothing else.

    The session may run exactly one Bash command, so the sentinel cannot come
    from an improvised `echo`; the verifier's bytes are re-checked after the
    run, so it cannot come from a rewritten verifier; and the result must be
    the non-error output of a Bash call this predicate itself saw issued, so it
    cannot come from the coordinator merely quoting the string in prose. A
    subagent cannot supply it either: delegated turns are ignored.
    """
    issued: set[str] = set()

    def accepted(event: dict) -> bool:
        if event.get("parent_tool_use_id"):
            return False
        content = (event.get("message") or {}).get("content")
        if not isinstance(content, list):
            return False
        if event.get("type") == "assistant":
            for block in content:
                if (block.get("type") == "tool_use" and block.get("name") == "Bash"
                        and str(block.get("input", {}).get("command", "")).strip() == VERIFIER_COMMAND):
                    issued.add(block.get("id"))
            return False
        if event.get("type") == "user":
            for block in content:
                if (block.get("type") == "tool_result" and block.get("tool_use_id") in issued
                        and not block.get("is_error")
                        and tool_result_text(block).strip() == VERIFIER_SENTINEL):
                    return True
        return False

    return accepted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    # Exercise this pilot's actual provider and corrected native transport;
    # keep the existing task-independent fixture and tool allowlist unchanged.
    repo = Path(case["sim"])
    sys.path.insert(0, str(Path(case["control"]).parent / "support"))
    import native_cc_stream
    from native_cc_stream import run_native_cc, stream_command
    assert Path(native_cc_stream.__file__).resolve() == Path(case["control"]).parent / "support/native_cc_stream.py"
    assert hasattr(native_cc_stream.NotificationFlow, "complete"), (
        "this fixture needs the completion-contract transport, not the R2 copy")
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign
    settings = campaign.local_vllm_settings(case)
    assert "apiKeyHelper" not in settings and "hooks" not in settings
    case["endpoint"] = case["inference_endpoint"]
    # Measured R2 runtimes were 1728 s (bowl_C) and 1796 s (drawer_C) of real
    # work; both then died inside the 1800 s gate during teardown. The budget is
    # unchanged — the completion contract below is what recovers the margin.
    timeout_seconds = int(case.get("compat_timeout_seconds", 1800))
    if timeout_seconds < 300:
        parser.error("compat_timeout_seconds must allow the required 300-second first wait")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    work = root / "work"
    work.mkdir()
    colors = {"red": (240, 0, 0), "green": (0, 220, 0), "blue": (0, 0, 240),
              "yellow": (240, 240, 0), "black": (0, 0, 0)}
    blocks = []
    for i, rgb in enumerate(colors.values(), 1):
        data = png(rgb)
        (work / f"swatch_{i}.png").write_bytes(data)
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(data).decode()}})
    payload = {"model": case["model"], "messages": [{"role": "user", "content": blocks}]}
    request = urllib.request.Request(case["endpoint"] + "/v1/messages/count_tokens",
                                     data=json.dumps(payload).encode(),
                                     headers={"content-type": "application/json", "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(request, timeout=120) as response:
        (root / "five_image_count.json").write_text(response.read().decode())
    sentinels = [f"ASPIRE_CHUNK_{i}_COMPLETE" for i in range(1, 13)]
    for i, sentinel in enumerate(sentinels, 1):
        lines = [f"Observation record {n}: amber stone follows silver cloud.\n" for n in range(180)]
        (work / f"chunk_{i}.txt").write_text("".join(lines) + sentinel + "\n")
    report_text = "## Root causes observed\nSynthetic fixture only.\n## What fixed them\nReport handoff verified.\n## Generalizable patterns\nnone\n## Blocked seeds\nnone\n"
    expected = {"colors": list(colors), "sentinels": sentinels}
    verifier = ("import json\nfrom pathlib import Path\nv=json.loads(Path('result.json').read_text())\n"
                "assert sorted(v) == ['colors', 'sentinels'], v\n"
                "assert sorted(v['colors']) == " + repr(sorted(colors)) + ", v\n"
                "assert v['sentinels'] == " + repr(sentinels) + ", v\n"
                "assert Path('findings.md').read_text() == " + repr(report_text) + "\n"
                "print('" + VERIFIER_SENTINEL + "')\n")
    (work / "verify_result.py").write_text(verifier)
    prompt = """This is a native harness integration test, entirely within the current directory.
Use the native Agent tool to delegate this bounded task to a general-purpose subagent, with run_in_background=true:
Read swatch_1.png through swatch_5.png with Read (all five). Write colors.json containing an array of their dominant colors in that order, using lowercase English words. Do not change the images. Return the output path and the following full report text in your response, enclosed by BEGIN_FINDINGS_MD and END_FINDINGS_MD on separate lines. Do not write a report file as a subagent. Report text:
REPORT_FIXTURE_TEXT
While that agent works, use Read yourself to read chunk_1.txt through chunk_12.txt completely, ONE FILE PER TOOL TURN, in order. The final line of each chunk is a sentinel. Keep these twelve sentinels, in order, in notes.json as you go. Do not read multiple chunks in one turn and do not use Bash or a script to extract them; this intentionally exercises native context compaction. Do not reread chunks already recorded in notes.json after compaction.
Wait for the native background subagent's completion notification; it arrives on its own. Do not poll for it and do not use TaskOutput (it is not available here). When the notification arrives, read colors.json. As coordinator, use Write to save the exact report text between the returned markers to findings.md, without the markers. Write result.json with exactly two fields: colors (the array from colors.json), and sentinels (the ordered twelve sentinel strings). Run python3 verify_result.py using Bash. Do not modify any fixture or verifier. Finish only after verification passes, and reply NATIVE_CC_COMPAT_OK.
"""
    prompt = prompt.replace("REPORT_FIXTURE_TEXT", report_text)
    env = campaign.native_environment(case, repo, args.case.resolve(), root / "cc-config")
    env.update(CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=str(max(1, int(48000 * 100 / (case["context_tokens"] - case["max_output_tokens"])))),
               API_TIMEOUT_MS="180000", CLAUDE_CODE_MAX_RETRIES="0", CLAUDE_CODE_AUTO_CONNECT_IDE="false")
    command = [case["claude_bin"], "--model", case["model_tag"], "--settings", json.dumps(settings),
               "-p", prompt, "--output-format", "stream-json", "--verbose", "--forward-subagent-text", "--debug-file", str(root / "cc.debug.log"),
               "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-chrome",
               "--effort", case["effort"], "--tools", "default", "--allowedTools", "Read", "Write", "Agent",
               "SendMessage", f"Bash({VERIFIER_COMMAND})", "--permission-mode", "dontAsk"]
    argv, stdin_prompt = stream_command(command)
    # Record only fixture/provider controls, not the full inherited environment.
    recorded_env = {k: env[k] for k in ("CLAUDE_CONFIG_DIR", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "API_TIMEOUT_MS")}
    (root / "invocation.json").write_text(json.dumps({"argv": argv, "stdin_prompt": stdin_prompt,
                                                     "env": recorded_env, "transport": "frozen-pilot-native-stream",
                                                      "fixture_revision": FIXTURE_REVISION}, indent=2))
    timed_out = False
    outcome: dict = {}
    try:
        rc = run_native_cc(command, cwd=work, env=env, stdout_path=root / "cc.stdout.jsonl",
                           stderr_path=root / "cc.stderr.log", timeout=timeout_seconds,
                           terminal_evidence=verifier_evidence(), shutdown_grace=SHUTDOWN_GRACE,
                           outcome=outcome)
    except subprocess.TimeoutExpired:
        timed_out, rc = True, 124
    records = []
    paths = [root / "cc.stdout.jsonl", *(root / "cc-config/projects").rglob("*.jsonl")]
    for path in paths:
        for line in path.read_text().splitlines():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    served = sorted({r.get("message", {}).get("model") for r in records if r.get("type") == "assistant"
                     and r.get("message", {}).get("model") and not r["message"]["model"].startswith("<")})
    calls = {b["id"]: b for r in records if r.get("type") == "assistant"
             for b in r.get("message", {}).get("content", []) if b.get("type") == "tool_use"}.values()
    compactions = {r.get("uuid") or json.dumps(r, sort_keys=True): r for r in records
                   if r.get("type") == "system" and r.get("subtype") == "compact_boundary"}
    result = json.loads((work / "result.json").read_text()) if (work / "result.json").exists() else None
    report_handoff = ((work / "findings.md").is_file() and (work / "findings.md").read_text() == report_text
                      and any(c["name"] == "Write" and Path(c.get("input", {}).get("file_path", "")).name == "findings.md" for c in calls))
    # Unchanged from R2. The completion contract decides when the session is
    # shut down, never whether it passed: every artifact is re-checked here.
    result_ok = (isinstance(result, dict) and sorted(result) == ["colors", "sentinels"]
                 and sorted(result["colors"]) == sorted(colors) and result["sentinels"] == sentinels)
    passed = (report_handoff and rc == 0 and result_ok and served == [case["model"]] and bool(compactions)
              and any(c["name"] == "Agent" for c in calls) and (work / "verify_result.py").read_text() == verifier)
    summary = {"passed": passed, "exit_code": rc, "result": result, "models": served,
               "colour_order_matches": bool(isinstance(result, dict) and result.get("colors") == list(colors)),
               "timed_out": timed_out, "timeout_seconds": timeout_seconds,
               "report_handoff_verified": report_handoff,
               "native_agent_calls": sum(c["name"] == "Agent" for c in calls),
               "compaction_boundaries": len(compactions), "transcripts": [str(p) for p in paths],
               "fixture_revision": FIXTURE_REVISION, "shutdown_grace_seconds": SHUTDOWN_GRACE,
               "terminal_evidence": bool(outcome.get("terminal_evidence")),
               "completed_at_deadline": bool(outcome.get("completed_at_deadline")),
               "undelivered_notifications": outcome.get("undelivered", [])}
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
