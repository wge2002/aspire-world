"""Image-source overlay for vLLM's Anthropic adapter (cap/serving/launch_vllm_image_source.py).

Runs against the real installed adapter, so it needs the Qwen vLLM venv:

    CUDA_VISIBLE_DEVICES= /mnt/home/gewang/envs/qwen38-flash-next-vllm/bin/python \
        -m unittest tests/test_vllm_image_source.py -v
"""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cap/serving"))
import launch_vllm_image_source as overlay  # noqa: E402

try:
    from vllm.entrypoints.anthropic.protocol import (AnthropicCountTokensRequest,
                                                     AnthropicMessagesRequest)
    from vllm.entrypoints.anthropic.serving import AnthropicServingMessages as Handler
except ImportError:  # pragma: no cover - wrong interpreter
    Handler = None

MODEL_DIR = Path("/mnt/home/gewang/models/Qwen3.8-Flash-Next-FP8")
WORK = "/tmp/fixture/work"
#: Distinct payloads stand in for five swatches; only identity matters here.
PNGS = [base64.b64encode(f"png-{i}".encode()).decode() for i in range(1, 6)]
IDS = [f"chatcmpl-tool-{i:016x}" for i in range(1, 6)]
LIVE_CONFIG = ROOT / "docs/experiments/code-world-pipeline-repair-20261005/live-qwen-dsw/config.json"


def image(i):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNGS[i]}}


def url(i):
    return f"data:image/png;base64,{PNGS[i]}"


def parallel_request(order=range(5), result_body=lambda i: [image(i)], cls=None):
    """One assistant turn with five Read calls, then one user turn with their results."""
    uses = [{"type": "tool_use", "id": IDS[i], "name": "Read",
             "input": {"file_path": f"{WORK}/swatch_{i + 1}.png"}} for i in range(5)]
    results = [{"type": "tool_result", "tool_use_id": IDS[i], "content": result_body(i)} for i in order]
    payload = {"model": "qwen3.8-flash-next", "max_tokens": 64, "messages": [
        {"role": "user", "content": "Read the five swatches."},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "Read all five.",
                                           "signature": ""}] + uses},
        {"role": "user", "content": results}]}
    return (cls or AnthropicMessagesRequest)(**payload)


def converted(request, patched=True):
    if patched:
        overlay.install()
    else:
        overlay.uninstall()
    try:
        return copy.deepcopy(Handler.to_chat_completion_request(request).messages)
    finally:
        overlay.install()


def labels(messages):
    return [part["text"] for m in messages if m["role"] == "user" and isinstance(m["content"], list)
            for part in m["content"] if part.get("type") == "text"
            and part["text"].startswith(overlay.LABEL_PREFIX)]


def strip_labels(messages):
    out = copy.deepcopy(messages)
    for m in out:
        if m["role"] == "user" and isinstance(m["content"], list):
            m["content"] = [p for p in m["content"] if not (
                p.get("type") == "text" and p["text"].startswith(overlay.LABEL_PREFIX))]
    return out


@unittest.skipIf(Handler is None, "needs the Qwen vLLM venv")
class ImageSourceOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        overlay.verify_adapter()
        overlay.install()

    @classmethod
    def tearDownClass(cls):
        overlay.uninstall()

    def test_unpatched_adapter_reproduces_the_unlabelled_shape(self):
        messages = converted(parallel_request(), patched=False)
        self.assertEqual(labels(messages), [])
        images = [m for m in messages if m["role"] == "user" and isinstance(m["content"], list)]
        self.assertEqual([m["content"] for m in images],
                         [[{"type": "image_url", "image_url": {"url": url(i)}}] for i in range(5)])

    def test_parallel_results_each_carry_their_own_source(self):
        messages = converted(parallel_request())
        self.assertEqual([m["role"] for m in messages[:2]], ["user", "assistant"])
        tail = messages[2:]
        self.assertEqual(len(tail), 10)
        for i in range(5):
            tool, user = tail[2 * i], tail[2 * i + 1]
            self.assertEqual(tool, {"role": "tool", "tool_call_id": IDS[i], "content": ""})
            self.assertEqual(user["content"], [
                {"type": "text", "text": f"{overlay.LABEL_PREFIX} tool_use_id={IDS[i]} "
                                         f"tool=Read source={WORK}/swatch_{i + 1}.png"},
                {"type": "image_url", "image_url": {"url": url(i)}}])

    def test_out_of_order_results_follow_tool_use_id_not_position(self):
        order = [3, 0, 4, 2, 1]
        messages = converted(parallel_request(order=order))
        got = labels(messages)
        self.assertEqual(got, [f"{overlay.LABEL_PREFIX} tool_use_id={IDS[i]} tool=Read "
                               f"source={WORK}/swatch_{i + 1}.png" for i in order])
        images = [p["image_url"]["url"] for m in messages if m["role"] == "user"
                  and isinstance(m["content"], list) for p in m["content"] if p["type"] == "image_url"]
        self.assertEqual(images, [url(i) for i in order])

    def test_only_label_parts_are_added(self):
        for request in (parallel_request(), parallel_request(order=[4, 3, 2, 1, 0]),
                        parallel_request(result_body=lambda i: [{"type": "text", "text": f"t{i}"}, image(i)])):
            self.assertEqual(strip_labels(converted(request)), converted(request, patched=False))

    def test_text_only_results_are_byte_identical(self):
        request = parallel_request(result_body=lambda i: f"line {i}\n")
        self.assertEqual(converted(request), converted(request, patched=False))
        request = parallel_request(result_body=lambda i: [{"type": "text", "text": f"line {i}"}])
        self.assertEqual(converted(request), converted(request, patched=False))

    def test_mixed_text_and_image_keeps_tool_text(self):
        messages = converted(parallel_request(result_body=lambda i: [{"type": "text", "text": f"t{i}"}, image(i)]))
        self.assertEqual([m["content"] for m in messages if m["role"] == "tool"], [f"t{i}" for i in range(5)])
        self.assertEqual(len(labels(messages)), 5)

    def test_unmatched_and_pathless_tools_get_factual_fallbacks(self):
        request = AnthropicMessagesRequest(model="m", max_tokens=8, messages=[
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "Screenshot",
                                               "input": {"camera": "front", "w": 64}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": [image(0)]},
                {"type": "tool_result", "tool_use_id": "missing", "content": [image(1)]}]}])
        self.assertEqual(labels(converted(request)), [
            f'{overlay.LABEL_PREFIX} tool_use_id=a tool=Screenshot input={{"camera": "front", "w": 64}}',
            f"{overlay.LABEL_PREFIX} tool_use_id=missing"])

    def test_requests_do_not_share_tool_use_state(self):
        converted(parallel_request())
        self.assertEqual(overlay._tool_uses.get(), {})
        later = AnthropicMessagesRequest(model="m", max_tokens=8, messages=[
            {"role": "user", "content": "go"},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": IDS[0], "content": [image(0)]}]}])
        self.assertEqual(labels(converted(later)), [f"{overlay.LABEL_PREFIX} tool_use_id={IDS[0]}"])

    def test_count_tokens_uses_the_same_conversion(self):
        messages = converted(parallel_request(cls=AnthropicCountTokensRequest))
        self.assertEqual(len(labels(messages)), 5)

    def test_rendered_qwen_prompt_names_each_image_before_its_pixels(self):
        import jinja2
        import jinja2.ext
        import jinja2.sandbox

        template = (MODEL_DIR / "chat_template.jinja").read_text()
        env = jinja2.sandbox.ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                                           extensions=[jinja2.ext.loopcontrols])
        env.globals["raise_exception"] = lambda message: (_ for _ in ()).throw(ValueError(message))
        messages = converted(parallel_request(order=[2, 0, 1, 4, 3]))
        for m in messages:
            for call in m.get("tool_calls") or ():
                call["function"]["arguments"] = json.loads(call["function"]["arguments"])
        text = env.from_string(template).render(messages=messages, add_generation_prompt=True,
                                                enable_thinking=True, preserve_thinking=True)
        tail = text[text.index("<tool_call>"):]
        for i in [2, 0, 1, 4, 3]:
            label = f"tool_use_id={IDS[i]} tool=Read source={WORK}/swatch_{i + 1}.png"
            at = tail.index(label)
            self.assertTrue(tail[at + len(label):].startswith("<|vision_start|>"), tail[at:at + 200])
            tail = tail[at + len(label):]
        self.assertEqual(text.count("<|vision_start|>"), 5)

    def test_version_or_source_drift_refuses(self):
        with self.assertRaises(RuntimeError):
            overlay.verify_adapter(expected_sha256="0" * 64)
        with self.assertRaises(RuntimeError):
            overlay.verify_adapter(expected_version="0.0.0")

    def test_install_is_idempotent_and_uninstall_restores(self):
        overlay.install()
        overlay.install()
        self.assertEqual(len(labels(converted(parallel_request()))), 5)
        overlay.uninstall()
        try:
            self.assertEqual(labels(Handler.to_chat_completion_request(parallel_request()).messages), [])
        finally:
            overlay.install()


class LaunchGuardTests(unittest.TestCase):
    def test_reviewed_service_argv_is_single_process(self):
        argv = json.loads(LIVE_CONFIG.read_text())["argv"]
        self.assertEqual(argv[2], "serve")
        overlay._single_api_process(argv[2:])

    def test_out_of_process_api_servers_are_refused(self):
        for extra in (["--api-server-count", "2"], ["--api-server-count=4"], ["--headless"],
                      ["--data-parallel-size", "2"]):
            with self.assertRaises(RuntimeError, msg=extra):
                overlay._single_api_process(["serve", "m", *extra])
        overlay._single_api_process(["serve", "m", "--api-server-count", "1"])


if __name__ == "__main__":
    unittest.main()
