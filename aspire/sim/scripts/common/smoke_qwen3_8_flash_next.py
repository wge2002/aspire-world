#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Smoke-test Qwen3.8-Flash-Next reasoning, vision, and tool calling."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import struct
import urllib.error
import urllib.request
import zlib
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


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def solid_color_png_data_url(red: int, green: int, blue: int) -> str:
    """Return a dependency-free 64x64 RGB PNG as a base64 data URL."""

    width = height = 64
    row = b"\x00" + bytes((red, green, blue)) * width
    raw = row * height
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(raw))
    png += _png_chunk(b"IEND", b"")
    encoded = base64.b64encode(png).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8121/v1")
    parser.add_argument("--model", default="qwen3.8-flash-next")
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
            "top_k": 20,
            "min_p": 0.0,
            "max_tokens": 512,
            "reasoning_effort": "medium",
            "chat_template_kwargs": {
                "enable_thinking": True,
                "preserve_thinking": True,
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

    vision_body = post_json(
        endpoint,
        {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": solid_color_png_data_url(255, 0, 0),
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "What is the dominant color of this image? "
                                "Return only one lowercase English color word."
                            ),
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "max_tokens": 64,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        args.timeout,
    )
    vision_content = (assistant_message(vision_body).get("content") or "").strip()
    if re.search(r"\bred\b", vision_content, flags=re.IGNORECASE) is None:
        raise RuntimeError(
            f"Vision smoke test expected the color red, got: {vision_content!r}"
        )
    print(f"vision: PASS (content={vision_content!r})")

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
            "top_k": 20,
            "min_p": 0.0,
            "max_tokens": 512,
            "reasoning_effort": "low",
            "chat_template_kwargs": {
                "enable_thinking": True,
                "preserve_thinking": True,
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
        raise RuntimeError(
            f"Tool-calling smoke test returned no multiply call: {tool_message}"
        )
    arguments = json.loads(tool_calls[0]["function"]["arguments"])
    if sorted((arguments.get("a"), arguments.get("b"))) != [17, 23]:
        raise RuntimeError(f"Tool-calling smoke test returned wrong arguments: {arguments}")
    print(f"tool_calling: PASS (arguments={arguments})")


if __name__ == "__main__":
    main()
