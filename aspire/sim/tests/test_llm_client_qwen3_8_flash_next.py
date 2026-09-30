# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import Any

from aspire.sim.cap.llm import client
from aspire.sim.cap.llm.client import (
    ModelQueryArgs,
    is_qwen3_8_flash_next_model,
    is_vlm_model,
    qwen3_8_flash_next_request_options,
)


def test_qwen3_8_flash_next_model_aliases() -> None:
    assert is_qwen3_8_flash_next_model("Qwen/Qwen3.8-Flash-Next")
    assert is_qwen3_8_flash_next_model("Qwen/Qwen3.8-Flash-Next-FP8")
    assert is_qwen3_8_flash_next_model("qwen3.8-flash-next")
    assert is_qwen3_8_flash_next_model("local/Qwen3_8_Flash_Next_FP8")
    assert not is_qwen3_8_flash_next_model("Qwen/Qwen3.8-27B")


def test_qwen3_8_flash_next_is_registered_as_vlm() -> None:
    assert is_vlm_model("Qwen/Qwen3.8-Flash-Next-FP8")
    assert is_vlm_model("qwen3.8-flash-next")


def test_qwen3_8_flash_next_reasoning_effort_mapping() -> None:
    disabled = qwen3_8_flash_next_request_options("minimal")
    assert disabled["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in disabled
    assert disabled["top_p"] == 0.8
    assert disabled["presence_penalty"] == 1.5

    assert qwen3_8_flash_next_request_options("low")["reasoning_effort"] == "low"
    assert (
        qwen3_8_flash_next_request_options("medium")["reasoning_effort"]
        == "medium"
    )
    assert qwen3_8_flash_next_request_options("high")["reasoning_effort"] == "xhigh"
    assert qwen3_8_flash_next_request_options("max")["reasoning_effort"] == "xhigh"


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
                        "content": "red",
                        "reasoning_content": "The image is uniformly red.",
                    }
                }
            ]
        }


def test_qwen3_8_flash_next_query_preserves_image_input(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(client.requests, "post", fake_post)
    image_url = "data:image/png;base64,iVBORw0KGgo="
    prompt = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What color is this image?"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ]
    args = ModelQueryArgs(
        model="qwen3.8-flash-next",
        server_url="http://127.0.0.1:8121/v1/chat/completions",
        reasoning_effort="medium",
    )

    result = client.query_model(args, prompt)

    payload = json.loads(captured["data"])
    assert captured["url"] == args.server_url
    assert payload["messages"] == prompt
    assert payload["messages"][0]["content"][1]["image_url"]["url"] == image_url
    assert payload["reasoning_effort"] == "medium"
    assert payload["chat_template_kwargs"] == {
        "enable_thinking": True,
        "preserve_thinking": True,
    }
    assert result == {
        "content": "red",
        "reasoning": "The image is uniformly red.",
    }


def test_qwen3_8_flash_next_converts_aspire_video_data_urls() -> None:
    video_url = "data:video/mp4;base64,AAAA"
    prompt = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe the video."},
                {"type": "image_url", "image_url": {"url": video_url}},
            ],
        }
    ]

    prepared = client._prepare_qwen3_8_multimodal_messages(prompt)

    assert prepared[0]["content"][1] == {
        "type": "video_url",
        "video_url": {"url": video_url},
    }
    assert prompt[0]["content"][1]["type"] == "image_url"
