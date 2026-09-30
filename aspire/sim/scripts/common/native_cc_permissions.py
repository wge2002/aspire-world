"""Detect native skill-write denials before accepting a campaign result."""
import json
from pathlib import Path


def skill_write_denials(transcripts, repo: Path, skills_rel: str) -> list[dict]:
    skills = (repo / skills_rel).resolve()
    denied = {}
    for path in transcripts:
        for line in Path(path).read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in record.get("permission_denials", []):
                if item.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
                    continue
                value = item.get("tool_input", {}).get("file_path")
                if value and (repo / value).resolve().is_relative_to(skills):
                    denied[json.dumps(item, sort_keys=True)] = item
    return list(denied.values())
