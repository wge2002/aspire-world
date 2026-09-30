# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import Any

from aspire.sim.cap.llm import client
from aspire.sim.cap.llm.client import (
    ModelQueryArgs,
    VLM_MODELS,
    deepseek_v4_chat_template_kwargs,
    is_deepseek_v4_model,
    is_vlm_model,
)


def test_deepseek_v4_model_aliases() -> None:
    assert is_deepseek_v4_model("deepseek-ai/DeepSeek-V4-Flash-Vision-Exp")
    assert is_deepseek_v4_model("deepseek-v4-flash-vision-exp")
    assert is_deepseek_v4_model("local/DeepSeek_V4_Flash_Vision_Exp")
    assert is_deepseek_v4_model("deepseek-ai/DeepSeek-V4-Flash-0731")
    assert is_deepseek_v4_model("deepseek-v4-flash-0731")
    assert is_deepseek_v4_model("local/DeepSeek_V4_Flash_0731")
    assert not is_deepseek_v4_model("deepseek/deepseek-v3.2")


def test_deepseek_v4_vision_model_is_multimodal() -> None:
    assert "deepseek-ai/DeepSeek-V4-Flash-Vision-Exp" in VLM_MODELS
    assert "deepseek-v4-flash-vision-exp" in VLM_MODELS
    assert is_vlm_model("local/DeepSeek_V4_Flash_Vision_Exp")


def test_deepseek_v4_reasoning_effort_mapping() -> None:
    assert deepseek_v4_chat_template_kwargs("minimal") == {"thinking": False}
    assert deepseek_v4_chat_template_kwargs("low") == {
        "thinking": True,
        "reasoning_effort": "low",
    }
    assert deepseek_v4_chat_template_kwargs("medium") == {
        "thinking": True,
        "reasoning_effort": "high",
    }
    assert deepseek_v4_chat_template_kwargs("max") == {
        "thinking": True,
        "reasoning_effort": "max",
    }


class _FakeResponse:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "a carrot",
                        "reasoning_content": "The image shows orange carrots.",
                    }
                }
            ]
        }


def test_deepseek_v4_vision_query_preserves_image_input(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(client.requests, "post", fake_post)
    image_url = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="
    prompt = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What vegetable is visible?"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    args = ModelQueryArgs(
        model="deepseek-v4-flash-vision-exp",
        server_url="http://127.0.0.1:8120/v1/chat/completions",
        reasoning_effort="max",
    )

    result = client.query_model(args, prompt)

    payload = json.loads(captured["data"])
    assert captured["url"] == args.server_url
    assert payload["messages"] == prompt
    assert payload["messages"][0]["content"][1]["image_url"]["url"] == image_url
    assert payload["chat_template_kwargs"] == {
        "thinking": True,
        "reasoning_effort": "max",
    }
    assert result == {
        "content": "a carrot",
        "reasoning": "The image shows orange carrots.",
    }
