"""Exercise the actual campaign probe with recorded native usage shapes."""
import json
from pathlib import Path
import sys
import pytest

SIM = Path(__file__).resolve().parents[3]
for folder in (SIM / "scripts/libero", SIM / "scripts/common"):
    sys.path.insert(0, str(folder))
import native_world_campaign as campaign


@pytest.mark.parametrize("provider,context,output_setting,allowed", [
    ("local-vllm", 1000000, "64000", True),
    ("local-vllm", 1000000, "32000", False),
    ("local-vllm", 200000, "64000", False),
    ("remote", 1000000, "64000", False),
])
def test_actual_probe_capacity_gate(tmp_path, monkeypatch, provider, context, output_setting, allowed):
    assert Path(campaign.__file__).resolve().is_relative_to(SIM)
    case = dict(sim=str(SIM), model_provider=provider, profile="judgment", condition="C",
                expected_served_model="qwen3.8-flash-next", model_tag="qwen3.8-flash-next",
                effort="xhigh", context_tokens=1000000, max_output_tokens=64000)
    def run(command, **kwargs):
        kwargs["stdout_path"].write_text("\n".join(map(json.dumps, [
            {"type": "assistant", "message": {"model": "qwen3.8-flash-next"}},
            {"type": "result", "modelUsage": {"qwen3.8-flash-next": {
                "contextWindow": context, "maxOutputTokens": 32000}}},
        ])))
        return 0
    monkeypatch.setattr(campaign, "run_native_cc", run)
    monkeypatch.setattr(campaign, "native_command", lambda *args: ["synthetic-no-model"])
    settings = {"env": {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": output_setting}}
    if allowed:
        assert campaign.probe(case, tmp_path, {}, settings)["max_output_tokens"] == [32000]
    else:
        with pytest.raises(campaign.ServiceFailure):
            campaign.probe(case, tmp_path, {}, settings)
