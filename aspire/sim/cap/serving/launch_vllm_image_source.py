#!/usr/bin/env python3
"""Run `vllm serve` with tool-result images labelled by their source.

vLLM 0.28.1rc1's Anthropic adapter (`AnthropicServingMessages.
_convert_user_tool_result`) turns an image tool_result into an empty
`role=tool` message plus a separate, unlabelled `role=user` image message. When
the model issues several tool calls in one assistant turn, the only link left
between an image and the call that produced it is interleaving position, and
Qwen matched colours to the wrong files in 4/11 matched requests (7/11 vs 11/11
with a file-name label; docs/experiments/code-world-pipeline-repair-20261005/
IMAGE_STAGE_RESULT.json).

This overlay prepends one factual text part to each such image message, built
only from the request itself: the tool_use_id, the tool name and its path-like
argument (`file_path` for Read). Image bytes and order, tool messages, tool IDs,
tools, text-only results and sampling are untouched. The label is keyed by
tool_use_id, so it stays correct when results arrive out of call order.

It patches only this process and refuses to start unless the installed vLLM
version and adapter source hash are exactly the reviewed ones, so a changed
environment fails loudly instead of running unpatched or mis-patched. Usage
mirrors the `vllm` console script:

    python launch_vllm_image_source.py serve <model> [vllm serve args...]
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

#: The exact adapter this overlay was written against.
EXPECTED_VLLM_VERSION = "0.28.1rc1.dev357+g4ae622828"
EXPECTED_SERVING_SHA256 = "535d9fd64bd92515cdd4b7ccaceedd37d1781031b98df9aadf3f9999e99fccb3"
LABEL_PREFIX = "[tool result image source]"
#: Arguments that name what a tool looked at, in preference order.
SOURCE_KEYS = ("file_path", "path", "notebook_path", "url")
MAX_FALLBACK_CHARS = 200
LOADED_MARKER = "VLLM_IMAGE_SOURCE_OVERLAY_LOADED"

_tool_uses: contextvars.ContextVar[dict[str, tuple[str, dict]]] = contextvars.ContextVar(
    "anthropic_tool_uses", default={})
_originals: dict[str, Any] = {}


def verify_adapter(expected_version: str = EXPECTED_VLLM_VERSION,
                   expected_sha256: str = EXPECTED_SERVING_SHA256) -> dict:
    """Refuse any vLLM other than the reviewed version and adapter source."""
    import vllm
    from vllm.entrypoints.anthropic import serving

    path = Path(serving.__file__)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if vllm.__version__ != expected_version or actual != expected_sha256:
        raise RuntimeError(
            f"image-source overlay targets vLLM {expected_version} with adapter "
            f"sha256 {expected_sha256}; found {vllm.__version__} / {actual} at {path}")
    for name in ("to_chat_completion_request", "_convert_user_tool_result"):
        if not hasattr(serving.AnthropicServingMessages, name):
            raise RuntimeError(f"adapter has no {name}; overlay does not apply")
    return {"vllm": vllm.__version__, "serving": str(path), "serving_sha256": actual}


def _source_label(tool_use_id: str, use: tuple[str, dict] | None) -> str:
    if use is None:
        return f"{LABEL_PREFIX} tool_use_id={tool_use_id}"
    name, arguments = use
    source = next((arguments[k] for k in SOURCE_KEYS if isinstance(arguments.get(k), str)), None)
    if source is None:
        source = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        if len(source) > MAX_FALLBACK_CHARS:
            source = source[:MAX_FALLBACK_CHARS] + "..."
        return f"{LABEL_PREFIX} tool_use_id={tool_use_id} tool={name} input={source}"
    return f"{LABEL_PREFIX} tool_use_id={tool_use_id} tool={name} source={source}"


def _collect_tool_uses(messages) -> dict[str, tuple[str, dict]]:
    uses = {}
    for message in messages or ():
        if getattr(message, "role", None) != "assistant" or isinstance(message.content, str):
            continue
        for block in message.content:
            if block.type == "tool_use" and block.id:
                uses[block.id] = (block.name or "", dict(block.input or {}))
    return uses


def install() -> None:
    """Patch the adapter in this process. Idempotent."""
    from vllm.entrypoints.anthropic.serving import AnthropicServingMessages as handler

    if _originals:
        return
    _originals["to_chat_completion_request"] = handler.__dict__["to_chat_completion_request"]
    _originals["_convert_user_tool_result"] = handler.__dict__["_convert_user_tool_result"]
    to_request = _originals["to_chat_completion_request"].__func__
    convert_result = _originals["_convert_user_tool_result"].__func__

    def to_chat_completion_request(cls, anthropic_request, **kwargs):
        token = _tool_uses.set(_collect_tool_uses(anthropic_request.messages))
        try:
            return to_request(cls, anthropic_request, **kwargs)
        finally:
            _tool_uses.reset(token)

    def _convert_user_tool_result(cls, block, openai_messages):
        start = len(openai_messages)
        convert_result(cls, block, openai_messages)
        tool_use_id = block.tool_use_id or ""
        for message in openai_messages[start:]:
            content = message.get("content")
            if (message.get("role") == "user" and isinstance(content, list)
                    and any(part.get("type") == "image_url" for part in content)):
                label = _source_label(tool_use_id, _tool_uses.get().get(tool_use_id))
                content.insert(0, {"type": "text", "text": label})

    handler.to_chat_completion_request = classmethod(to_chat_completion_request)
    handler._convert_user_tool_result = classmethod(_convert_user_tool_result)


def uninstall() -> None:
    from vllm.entrypoints.anthropic.serving import AnthropicServingMessages as handler

    for name, original in _originals.items():
        setattr(handler, name, original)
    _originals.clear()


def _single_api_process(argv: list[str]) -> None:
    """The patch lives in this process, so the API server must too."""
    if os.environ.get("VLLM_USE_RUST_FRONTEND", "0") not in ("", "0"):
        raise RuntimeError("VLLM_USE_RUST_FRONTEND would serve requests outside this process")
    for i, arg in enumerate(argv):
        value = arg.split("=", 1)[1] if arg.startswith("--api-server-count=") else (
            argv[i + 1] if arg == "--api-server-count" and i + 1 < len(argv) else None)
        if value is not None and value != "1":
            raise RuntimeError("--api-server-count other than 1 would bypass the overlay")
        if arg in ("--headless", "--data-parallel-size", "-dp") or arg.startswith("--data-parallel-size="):
            raise RuntimeError(f"{arg} is outside the overlay's reviewed single-server launch")


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] != "serve":
        raise SystemExit("usage: launch_vllm_image_source.py serve <model> [vllm serve args...]")
    _single_api_process(argv)
    receipt = verify_adapter()
    install()
    overlay = Path(__file__).resolve()
    receipt.update(overlay=str(overlay), overlay_sha256=hashlib.sha256(overlay.read_bytes()).hexdigest())
    print(LOADED_MARKER, json.dumps(receipt, sort_keys=True), flush=True)
    from vllm.entrypoints.cli.main import main as vllm_main

    sys.argv = ["vllm", *argv]
    return vllm_main()


if __name__ == "__main__":
    sys.exit(main())
