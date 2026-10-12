# SPDX-License-Identifier: Apache-2.0
"""Trusted loopback Qwen reviewer. Responses are validated data, never code.

No provider credentials, simulator handles, hidden assets, task outcomes or
cross-episode memory enter this client. It sees only the detached public packet.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import urllib.request

from .semantic_world import validate_parts
from .articulated_world import validate_joints

PROMPT = """You review a robot's uncertain public scene representation.
Use only the supplied current RGB image, public evidence and motion reports.
Choose from fixed/sliding/rotating/free/unknown joint model families. Axes,
angles and positions are fitted by the numerical engine; do not invent them.
Labels, links and joint types are hypotheses. Failed execution or unverified
contact does not prove a kinematic contradiction. Missing/ambiguous tracking
needs reacquisition, not a new physical law. Stationary or small-angle evidence
can be insufficient. Model fits are based on RGB-D correspondences, not truth.
Return one JSON object, with no code or prose outside it:
{"action":"defer|retain|select_models|reacquire|revise", "reason":"...",
 "evidence_ids":["actual supplied visual evidence IDs"],
 "joints":[{"child":"existing ID","parent":"its parent ID","family":"unknown"}]}
For revise, also supply the complete parts graph, complete objects list of
public text prompts, and optional lineage mapping old IDs to new IDs. Each part
has identity,parent,semantic,tracked,hypotheses. Each object has identity,prompt,
optional min_seed_score and optional selector {axis:image_x|image_y,rank:integer}.
Use revise only when part boundaries, identity or connections need to change.
Use select_models for an existing graph. Retain/defer/reacquire omit joints.
Evidence IDs must cite supplied observations supporting this decision. If
there is no informative motion yet, propose families as uncertain hypotheses
or defer. Preserve stable IDs unless a supported split/merge requires new IDs.
No scene inventory, previous solution or hidden joint definition is available.
Keep the review local to the triggering event and give a short reason.
initial_scene_decomposition creates a small graph only when no graph exists.
initial_model_review checks an EXISTING graph; it does not rebuild the scene.
Without informative motion, return defer with no joints immediately. Never
invent a parent for a root: a root may only have parent:null/family:free|unknown.
Do not speculate about unseen mechanisms or enumerate task plans.
"""


class ReviewRejected(ValueError):
    """A received proposal is unusable data; it is not a service outage."""
    def __init__(self, reason, receipt):
        self.receipt = receipt
        super().__init__(reason)


def validate_reply(reply, packet):
    allowed = {"action", "reason", "evidence_ids", "joints", "parts", "objects", "lineage"}
    if not isinstance(reply, dict) or set(reply)-allowed:
        raise ValueError("review response has unregistered fields")
    action = reply.get("action")
    if action not in ("defer", "retain", "select_models", "reacquire", "revise"):
        raise ValueError("unregistered review action")
    if not isinstance(reply.get("reason"), str) or not reply["reason"].strip():
        raise ValueError("review needs an explicit reason")
    ids = reply.get("evidence_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or not set(ids) <= set(packet["evidence_ids"]):
        raise ValueError("review must cite supplied public visual evidence")
    graph = packet["snapshot"]["semantic"]["parts"]
    if action == "revise":
        objects = reply.get("objects")
        if not isinstance(objects, list) or not objects:
            raise ValueError("revision needs nonempty public object prompts")
        keys = {"identity", "prompt", "min_seed_score", "selector"}
        for obj in objects:
            if (not isinstance(obj, dict) or set(obj)-keys or not all(isinstance(obj.get(k), str) and obj[k].strip() for k in ("identity", "prompt"))):
                raise ValueError("invalid public object definition")
            score = obj.get("min_seed_score", .5)
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
                raise ValueError("invalid public seed score")
            selector = obj.get("selector")
            if selector is not None and (not isinstance(selector, dict) or set(selector) != {"axis", "rank"} or selector["axis"] not in ("image_x", "image_y") or type(selector["rank"]) is not int or selector["rank"] < 0):
                raise ValueError("invalid public selector")
        if len({o["identity"] for o in objects}) != len(objects):
            raise ValueError("duplicate public identity")
        graph = validate_parts(reply.get("parts"), {o["identity"] for o in objects})
        lineage = reply.get("lineage", {})
        if not isinstance(lineage, dict) or any(old not in packet["snapshot"]["semantic"]["parts"] or not isinstance(new, list) or not new or any(k not in graph for k in new) for old, new in lineage.items()):
            raise ValueError("invalid split/merge lineage")
    elif any(k in reply for k in ("parts", "objects", "lineage")):
        raise ValueError("only revise may change part structure")
    if action in ("select_models", "revise"):
        validate_joints(reply.get("joints"), graph)
    elif "joints" in reply:
        raise ValueError("this review action cannot change joint models")
    return reply


class QwenWorldReviewer:
    def __init__(self, config, *, transport=None):
        expected = {"endpoint", "model", "effort", "context_tokens", "max_output_tokens", "timeout_seconds"}
        if set(config) != expected or not re.fullmatch(r"http://(?:127\.0\.0\.1|localhost):[0-9]{1,5}", str(config["endpoint"])):
            raise ValueError("review route must be a frozen local loopback endpoint")
        if not 1 <= int(config["endpoint"].rsplit(":", 1)[1]) <= 65535:
            raise ValueError("invalid review service port")
        if (config["model"], config["effort"], config["context_tokens"], config["max_output_tokens"]) != ("qwen3.8-flash-next", "xhigh", 1000000, 64000):
            raise ValueError("review model/capacity must match the common solver")
        if isinstance(config["timeout_seconds"], bool) or not 0 < config["timeout_seconds"] <= 900:
            raise ValueError("review request timeout must fit the trial watchdog")
        self.config, self.transport = dict(config), transport or self._request

    def _request(self, payload):
        request = urllib.request.Request(self.config["endpoint"]+"/v1/messages",
            data=json.dumps(payload, allow_nan=False).encode(),
            headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=self.config["timeout_seconds"]) as response:
            return json.load(response)

    def review(self, packet, *, rgb=None):
        content = [{"type": "text", "text": json.dumps(packet, sort_keys=True, allow_nan=False)}]
        if rgb is not None:
            import cv2
            ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
            if not ok:
                raise ValueError("public image encoding failed")
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(encoded).decode()}})
        payload = {"model": self.config["model"], "max_tokens": self.config["max_output_tokens"],
            "thinking": {"type": "adaptive"}, "output_config": {"effort": self.config["effort"]},
            "temperature": 1.0, "top_p": .95, "top_k": 20, "system": PROMPT,
            "messages": [{"role": "user", "content": content}]}
        started = time.monotonic()
        response = self.transport(payload)
        text = "\n".join(c["text"] for c in response.get("content", []) if c.get("type") == "text")
        if text.strip().startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        receipt = {"endpoint": self.config["endpoint"], "model": self.config["model"],
            "effort": self.config["effort"], "context_config": self.config["context_tokens"],
            "max_output_config": payload["max_tokens"], "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
            "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            "response_sha256": hashlib.sha256(text.encode()).hexdigest(), "usage": response.get("usage", {}),
            "duration_seconds": time.monotonic()-started}
        try:
            proposal = json.loads(text, parse_constant=lambda v: (_ for _ in ()).throw(ValueError("nonfinite JSON constant")))
            receipt["proposal"] = proposal
            if response.get("stop_reason") != "end_turn":
                raise ValueError("review output was truncated or attempted a tool call")
            reply = validate_reply(proposal, packet)
        except (ValueError, TypeError, KeyError) as exc:
            receipt.update(status="rejected", reason=str(exc))
            raise ReviewRejected(str(exc), receipt) from exc
        receipt.update(status="accepted", decision=reply)
        return reply, receipt
