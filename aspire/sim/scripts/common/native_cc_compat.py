#!/usr/bin/env python3
"""Exercise native CC images, subagents, and compaction without robot trials."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import struct
import subprocess
import urllib.request
import zlib

from native_cc_stream import run_native_cc, stream_command


def png(rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!2I5B", 96, 96, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + bytes(rgb) * 96) * 96)) + chunk(b"IEND", b""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    case = json.loads(args.case.read_text())
    timeout_seconds = int(case.get("compat_timeout_seconds", 900))
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
    expected = {"colors": list(colors), "sentinels": sentinels}
    verifier = "import json\nfrom pathlib import Path\nv=json.loads(Path('result.json').read_text())\nassert v == " + repr(expected) + ", v\nprint('NATIVE_CC_COMPAT_OK')\n"
    (work / "verify_result.py").write_text(verifier)
    prompt = """This is a native harness integration test, entirely within the current directory.
Use the native Agent tool to delegate this bounded task to a general-purpose subagent, with run_in_background=true:
Read swatch_1.png through swatch_5.png with Read (all five). Write colors.json containing an array of their dominant colors in that order, using lowercase English words. Do not change the images. Return the output path.
While that agent works, use Read yourself to read chunk_1.txt through chunk_12.txt completely, ONE FILE PER TOOL TURN, in order. The final line of each chunk is a sentinel. Keep these twelve sentinels, in order, in notes.json as you go. Do not read multiple chunks in one turn and do not use Bash or a script to extract them; this intentionally exercises native context compaction. Do not reread chunks already recorded in notes.json after compaction.
Wait for the native background subagent to finish, then read colors.json. Write result.json with exactly two fields: colors (the array from colors.json), and sentinels (the ordered twelve sentinel strings). Run python3 verify_result.py using Bash. Do not modify any fixture or verifier. Finish only after verification passes, and reply NATIVE_CC_COMPAT_OK.
"""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR") if k in os.environ}
    env.update(CC_LOCAL_CONFIG_DIR=str(root / "cc-config"),
               CC_LOCAL_CONTEXT_TOKENS=str(case["context_tokens"]), CC_LOCAL_MAX_OUTPUT_TOKENS=str(case["max_output_tokens"]),
               CC_LOCAL_EFFORT=case["effort"], CC_LOCAL_CLAUDE_BIN=case["claude_bin"],
               CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=str(max(1, int(48000 * 100 / (case["context_tokens"] - case["max_output_tokens"])))),
               API_TIMEOUT_MS="180000", CLAUDE_CODE_MAX_RETRIES="0", CLAUDE_CODE_AUTO_CONNECT_IDE="false")
    command = ["bash", str(Path(__file__).with_name("claude_with_local_model.sh")), case["endpoint"], case["model"],
               "-p", prompt, "--output-format", "stream-json", "--verbose", "--forward-subagent-text", "--debug-file", str(root / "cc.debug.log"),
               "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-chrome",
               "--effort", case["effort"], "--tools", "default", "--allowedTools", "Read", "Write", "Agent",
               "TaskOutput", "SendMessage", "Bash(python3 verify_result.py)", "--permission-mode", "dontAsk"]
    argv, stdin_prompt = stream_command(command)
    (root / "invocation.json").write_text(json.dumps({"argv": argv, "stdin_prompt": stdin_prompt,
                                                     "env": env, "transport": "native-notification-stream"}, indent=2))
    timed_out = False
    try:
        rc = run_native_cc(command, cwd=work, env=env, stdout_path=root / "cc.stdout.jsonl",
                           stderr_path=root / "cc.stderr.log", timeout=timeout_seconds)
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
    passed = (rc == 0 and result == expected and served == [case["model"]] and bool(compactions)
              and any(c["name"] == "Agent" for c in calls) and (work / "verify_result.py").read_text() == verifier)
    summary = {"passed": passed, "exit_code": rc, "result": result, "models": served,
               "timed_out": timed_out, "timeout_seconds": timeout_seconds,
               "native_agent_calls": sum(c["name"] == "Agent" for c in calls),
               "compaction_boundaries": len(compactions), "transcripts": [str(p) for p in paths]}
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
