"""Pin a generic primary only from the exact frozen assignment and tool result."""
import json
import re
from native_lineage_r2 import audit_lineage


def audit_assignment(paths, assignment):
    calls, results = {}, {}
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
                    if inp.get("subagent_type") == "general-purpose" and inp.get("prompt", "").strip() == assignment.strip():
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
