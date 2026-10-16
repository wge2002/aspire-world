"""Pin a generic primary only from the exact frozen assignment and tool result."""
import json
import re
from native_lineage_r2 import audit_lineage


def audit_assignment(paths, assignment):
    calls, results = {}, {}
    delivered = {}
    for path in paths:
        audit = path.parent / "assignment-audit.jsonl"
        if audit.exists():
            for line in audit.read_text().splitlines():
                row = json.loads(line)
                if (row.get("canonicalized_primary") and not row.get("denied") and row.get("primary_recorded")
                        and (row.get("effective_input") or {}).get("prompt", "").strip() == assignment.strip()):
                    delivered[row["tool_use_id"]] = row
    for path in paths:
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # The underlying auditor reports malformed evidence.
            if event.get("parent_tool_use_id"):
                continue
            for block in (event.get("message") or {}).get("content", []):
                if not isinstance(block, dict):
                    continue
                if event.get("type") == "assistant" and block.get("type") == "tool_use" and block.get("name") in {"Agent", "Task"}:
                    inp = block.get("input") or {}
                    delivery = delivered.get(block["id"])
                    effective_matches = delivery and delivery.get("proposed_input") == inp
                    if inp.get("subagent_type") == "general-purpose" and (inp.get("prompt", "").strip() == assignment.strip() or effective_matches):
                        calls[block["id"]] = True
                if event.get("type") == "user" and block.get("type") == "tool_result" and not block.get("is_error"):
                    content = block.get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(item.get("text", "") for item in content if isinstance(item, dict))
                    identifiers = set(re.findall(r"agentId:\s*(a[0-9a-f]{8,})", str(content)))
                    if len(identifiers) == 1:
                        results[block["tool_use_id"]] = identifiers.pop()
    pins = {results[call]: call for call in calls if call in results}
    return audit_lineage(paths, known_primaries=pins)


def verify_received_assignment(config, session, worker, assignment_path):
    """Verify actual native child input, independent of hook effective_input."""
    import hashlib
    from pathlib import Path
    matches = list(Path(config).glob(f"projects/*/{session}/subagents/agent-{worker}.jsonl"))
    if len(matches) != 1:
        raise ValueError(f"expected one actual primary transcript, found {len(matches)}")
    transcript = matches[0]
    first = None
    for line in transcript.read_text().splitlines():
        row = json.loads(line)
        if row.get("type") == "user":
            first = row.get("message", {}).get("content")
            break
    if isinstance(first, list):
        first = "".join(block.get("text", "") for block in first if block.get("type") == "text")
    expected = Path(assignment_path).read_bytes()
    received = first.encode() if isinstance(first, str) else None
    if received != expected:
        raise ValueError("actual primary first user input differs from frozen assignment bytes")
    return {"state": "passed", "native_session": session, "native_worker": worker,
            "actual_child_transcript": str(transcript), "assignment": str(assignment_path),
            "assignment_bytes": len(expected), "received_prompt_sha256": hashlib.sha256(received).hexdigest(),
            "assignment_sha256": hashlib.sha256(expected).hexdigest(),
            "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest()}
