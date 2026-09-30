#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Smoke-test DeepSeek-V4-Flash-0731 text, reasoning, and tool calling."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from typing import Any


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc


def assistant_message(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return body["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected chat-completions response: {body}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8120/v1")
    parser.add_argument("--model", default="deepseek-v4-flash-0731")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    endpoint = f"{args.base_url.rstrip('/')}/chat/completions"

    reasoning_body = post_json(
        endpoint,
        {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": "Compute 17 * 19. Return only the final integer.",
                }
            ],
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 512,
            "chat_template_kwargs": {
                "thinking": True,
                "reasoning_effort": "high",
            },
        },
        args.timeout,
    )
    reasoning_message = assistant_message(reasoning_body)
    content = (reasoning_message.get("content") or "").strip()
    if "323" not in content:
        raise RuntimeError(f"Reasoning smoke test expected 323, got: {content!r}")
    reasoning = reasoning_message.get("reasoning_content") or reasoning_message.get(
        "reasoning"
    )
    print(f"reasoning: PASS (content={content!r}, reasoning_field={bool(reasoning)})")

    tool_body = post_json(
        endpoint,
        {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": "Use the multiply tool to calculate 23 times 17.",
                }
            ],
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": 512,
            "chat_template_kwargs": {
                "thinking": True,
                "reasoning_effort": "high",
            },
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "multiply",
                        "description": "Multiply two integers.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "integer"},
                                "b": {"type": "integer"},
                            },
                            "required": ["a", "b"],
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        },
        args.timeout,
    )
    tool_message = assistant_message(tool_body)
    tool_calls = tool_message.get("tool_calls") or []
    if not tool_calls or tool_calls[0].get("function", {}).get("name") != "multiply":
        raise RuntimeError(f"Tool-calling smoke test returned no multiply call: {tool_message}")
    arguments = json.loads(tool_calls[0]["function"]["arguments"])
    if sorted((arguments.get("a"), arguments.get("b"))) != [17, 23]:
        raise RuntimeError(f"Tool-calling smoke test returned wrong arguments: {arguments}")
    print(f"tool_calling: PASS (arguments={arguments})")


if __name__ == "__main__":
    main()
