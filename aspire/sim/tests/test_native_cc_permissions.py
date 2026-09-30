import importlib.util
import json
from pathlib import Path


def test_native_denial_detects_relative_and_absolute_skill_paths(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/common/native_cc_permissions.py"
    spec = importlib.util.spec_from_file_location("native_cc_permissions", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    skills = ".claude/skills/libero"
    denied = [dict(tool_name="Write", tool_input=dict(file_path=str(tmp_path / skills / "grasp.md"))),
              dict(tool_name="Edit", tool_input=dict(file_path=skills + "/transport.md"))]
    unrelated = dict(tool_name="Write", tool_input=dict(file_path="outputs/note.md"))
    transcript = tmp_path / "stream.jsonl"
    transcript.write_text(json.dumps(dict(type="result", permission_denials=denied + [unrelated])) + "\n")
    assert module.skill_write_denials([transcript], tmp_path, skills) == denied
