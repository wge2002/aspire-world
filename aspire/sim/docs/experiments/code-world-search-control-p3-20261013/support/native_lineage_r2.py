"""Role-aware native Claude Code Agent lineage parser (R2).

Replaces the old ``observed()`` heuristic, which regex-scanned *every* tool
result for ``agentId:`` strings and therefore could not tell the single
fix-loop-worker apart from helper agents it (or the coordinator) launched.

This module correlates each ``Agent``/``Task`` tool_use block to the exact
``tool_result`` that carries its ``agentId``, keeps the launch role
(``input.subagent_type``) and the call nesting (``parent_tool_use_id``), and
refuses to guess: unknown top-level generic agents stay *ambiguous*, two
distinct fix-loop-workers are an error, and failed launches never become
workers.

Some real cells launched their single fix-loop worker as a plain
``general-purpose`` agent carrying the full assignment, so no launch role names
it. For those, ``known_primaries`` pins the exact ``{agent_id: tool_use_id}``
pair from independently recorded evidence. The pin is matched narrowly — both
halves must agree — and a launch description is never used to infer a primary.

Import is side-effect free; ``audit_lineage`` is pure with respect to module
state.
"""

from __future__ import annotations

import json
import os
import re

AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})
PRIMARY_SUBAGENT_TYPE = "fix-loop-worker"
MAX_ANCESTOR_DEPTH = 64

_AGENT_ID_RE = re.compile(r"agentId:\s*(a[0-9a-f]{8,})")
_LAUNCH_FAILURE_RE = re.compile(
    r"not found|failed to launch|could not be launched|unknown agent type", re.I
)


# ---------------------------------------------------------------- record shapes


def _content_blocks(record):
    message = record.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _parent_id(record):
    parent = record.get("parent_tool_use_id")
    return parent if isinstance(parent, str) and parent else None


def _result_text(content):
    """Flatten a tool_result payload (string, block, or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        text = content.get("text")
        return text if isinstance(text, str) else ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _agent_ids(text):
    seen = []
    for match in _AGENT_ID_RE.finditer(text):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


# ------------------------------------------------------------------ pass 1: read


def _read_events(paths, errors):
    """Collect Agent calls, their results, and top-level session ids."""
    calls, results, sessions = {}, {}, {}
    stats = {"paths": len(paths), "records": 0, "duplicate_events": 0}
    seq = 0

    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError as exc:
            errors.append({"code": "unreadable_file", "path": path, "detail": str(exc)})
            continue

        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                errors.append(
                    {
                        "code": "malformed_line",
                        "path": path,
                        "line": lineno,
                        "detail": str(exc),
                    }
                )
                continue
            if not isinstance(record, dict):
                errors.append(
                    {"code": "malformed_record", "path": path, "line": lineno,
                     "detail": "record is not a JSON object"}
                )
                continue

            stats["records"] += 1
            site = {"path": path, "line": lineno}
            kind = record.get("type")

            if kind == "system":
                if record.get("subtype") == "init" and _parent_id(record) is None:
                    session_id = record.get("session_id")
                    if isinstance(session_id, str) and session_id:
                        if session_id in sessions:
                            stats["duplicate_events"] += 1
                        else:
                            sessions[session_id] = site
                continue

            if kind == "assistant":
                for block in _content_blocks(record):
                    if block.get("type") != "tool_use":
                        continue
                    if block.get("name") not in AGENT_TOOL_NAMES:
                        continue
                    tool_use_id = block.get("id")
                    if not isinstance(tool_use_id, str) or not tool_use_id:
                        errors.append(
                            {"code": "agent_call_without_id", "path": path,
                             "line": lineno, "detail": "tool_use block has no id"}
                        )
                        continue
                    payload = block.get("input")
                    payload = payload if isinstance(payload, dict) else {}
                    subagent_type = payload.get("subagent_type")
                    subagent_type = subagent_type if isinstance(subagent_type, str) else None
                    entry = {
                        "tool_use_id": tool_use_id,
                        "tool_name": block.get("name"),
                        "subagent_type": subagent_type,
                        "description": payload.get("description"),
                        "parent_tool_use_id": _parent_id(record),
                        "seq": seq,
                        "sites": [site],
                    }
                    seq += 1
                    prior = calls.get(tool_use_id)
                    if prior is None:
                        calls[tool_use_id] = entry
                        continue
                    stats["duplicate_events"] += 1
                    prior["sites"].append(site)
                    conflicts = {
                        key: [prior[key], entry[key]]
                        for key in ("tool_name", "subagent_type", "parent_tool_use_id")
                        if prior[key] != entry[key]
                    }
                    if conflicts:
                        errors.append(
                            {"code": "conflicting_agent_call",
                             "tool_use_id": tool_use_id,
                             "conflicts": conflicts,
                             "sites": [prior["sites"][0], site],
                             "detail": "same tool_use id reused with different call fields; "
                                       "first occurrence kept"}
                        )
                continue

            if kind == "user":
                for block in _content_blocks(record):
                    is_result = block.get("type") == "tool_result" or (
                        "tool_use_id" in block and "content" in block
                    )
                    if not is_result:
                        continue
                    tool_use_id = block.get("tool_use_id")
                    if not isinstance(tool_use_id, str) or not tool_use_id:
                        continue
                    entry = {
                        "tool_use_id": tool_use_id,
                        "text": _result_text(block.get("content")),
                        "is_error": bool(block.get("is_error")),
                        "seq": seq,
                        "sites": [site],
                    }
                    seq += 1
                    prior = results.get(tool_use_id)
                    if prior is None:
                        results[tool_use_id] = entry
                        continue
                    stats["duplicate_events"] += 1
                    prior["sites"].append(site)
                    if (prior["text"], prior["is_error"]) != (entry["text"], entry["is_error"]):
                        errors.append(
                            {"code": "conflicting_tool_result",
                             "tool_use_id": tool_use_id,
                             "sites": [prior["sites"][0], site],
                             "detail": "same tool_use id resolved twice with different "
                                       "payloads; first occurrence kept"}
                        )

    return calls, results, sessions, stats


# ------------------------------------------------------------ pass 2: correlate


def _ancestor_primary(call, calls, pinned=frozenset()):
    """Nearest enclosing primary call, walking parent_tool_use_id.

    A call counts as primary either because it was launched as
    ``fix-loop-worker`` or because ``known_primaries`` pinned it (``pinned``
    holds only pins whose agent id was confirmed against the transcript), so a
    helper nested under a legacy generic primary is attributed, not ambiguous.
    """
    seen = set()
    parent_id = call["parent_tool_use_id"]
    for _ in range(MAX_ANCESTOR_DEPTH):
        if parent_id is None or parent_id in seen:
            return None
        seen.add(parent_id)
        parent = calls.get(parent_id)
        if parent is None:
            return None
        if parent["subagent_type"] == PRIMARY_SUBAGENT_TYPE or parent_id in pinned:
            return parent["tool_use_id"]
        parent_id = parent["parent_tool_use_id"]
    return None


def _confirmed_pins(known_primaries, calls, results, errors):
    """Pinned {agent_id: tool_use_id} pairs that the audited records support.

    Both halves must agree: the pinned call must exist, must have resolved to
    exactly one agent id, and that id must be the pinned one. Anything else is
    an error and leaves the call to the ordinary rules, so a stale or wrong pin
    can never promote some other agent to primary.
    """
    confirmed = {}
    for agent_id, tool_use_id in known_primaries.items():
        call = calls.get(tool_use_id)
        if call is None:
            errors.append(
                {"code": "known_primary_call_absent",
                 "agent_id": agent_id,
                 "tool_use_id": tool_use_id,
                 "detail": "known_primaries pins an Agent call that the audited files "
                           "do not contain"}
            )
            continue
        result = results.get(tool_use_id)
        found = [] if result is None or result["is_error"] else _agent_ids(result["text"])
        if found != [agent_id]:
            errors.append(
                {"code": "known_primary_mismatch",
                 "agent_id": agent_id,
                 "tool_use_id": tool_use_id,
                 "resolved_agent_ids": found,
                 "call_site": call["sites"][0],
                 "detail": "the pinned Agent call did not resolve to the pinned agent id"}
            )
            continue
        confirmed[tool_use_id] = agent_id
    return confirmed


def _entry(call, agent_id, role, role_source, result, calls, pinned=frozenset()):
    return {
        "agent_id": agent_id,
        "tool_use_id": call["tool_use_id"],
        "tool_name": call["tool_name"],
        "subagent_type": call["subagent_type"],
        "description": call["description"],
        "role": role,
        "role_source": role_source,
        "top_level": call["parent_tool_use_id"] is None,
        "parent_tool_use_id": call["parent_tool_use_id"],
        "ancestor_primary_tool_use_id": _ancestor_primary(call, calls, pinned),
        "call_site": call["sites"][0],
        "result_site": result["sites"][0] if result else None,
    }


def audit_lineage(paths, *, known_helpers=None, known_primaries=None):
    """Audit native Agent lineage across one or more stdout JSONL transcripts.

    Args:
        paths: a JSONL path, or an iterable of JSONL paths, in read order.
        known_helpers: optional ``{agent_id: role}`` map naming *audited agent
            IDs* that are explicitly sanctioned top-level auxiliary agents
            (e.g. a messenger). Agents absent from this map that were launched
            at top level with a generic subagent_type stay ambiguous — they are
            never silently promoted to primary nor dropped.
        known_primaries: optional ``{agent_id: tool_use_id}`` map pinning the
            single legacy primary worker of a cell that was launched without the
            ``fix-loop-worker`` role. Both halves must match the transcript or
            the pin is an error. An explicit ``fix-loop-worker`` launch stays
            primary whether or not it is pinned, and a second distinct primary
            is still an error.

    Returns:
        A JSON-serializable dict: ``ok``, ``sessions``, ``primary_worker``,
        ``primary_workers``, ``helpers``, ``failed_launches``, ``unresolved``,
        ``ambiguous``, ``errors``, ``warnings``, ``stats``.
    """
    if isinstance(paths, (str, bytes, os.PathLike)):
        paths = [paths]
    paths = [os.fspath(p) for p in paths]

    known = {str(k): str(v) for k, v in (known_helpers or {}).items()}
    pins = {str(k): str(v) for k, v in (known_primaries or {}).items()}

    errors, warnings = [], []
    calls, results, sessions, stats = _read_events(paths, errors)
    confirmed_pins = _confirmed_pins(pins, calls, results, errors)
    pinned_calls = frozenset(confirmed_pins)

    primary_workers, helpers, failed_launches, unresolved, ambiguous = [], [], [], [], []
    agent_id_owner = {}

    for call in sorted(calls.values(), key=lambda c: c["seq"]):
        tool_use_id = call["tool_use_id"]
        result = results.get(tool_use_id)

        if result is None:
            unresolved.append(
                {"tool_use_id": tool_use_id,
                 "subagent_type": call["subagent_type"],
                 "description": call["description"],
                 "top_level": call["parent_tool_use_id"] is None,
                 "call_site": call["sites"][0],
                 "reason": "no_tool_result",
                 "detail": "Agent call has no correlated tool_result in the audited files"}
            )
            continue

        found = _agent_ids(result["text"])

        if result["is_error"] or (not found and _LAUNCH_FAILURE_RE.search(result["text"])):
            failed_launches.append(
                {"tool_use_id": tool_use_id,
                 "subagent_type": call["subagent_type"],
                 "description": call["description"],
                 "top_level": call["parent_tool_use_id"] is None,
                 "parent_tool_use_id": call["parent_tool_use_id"],
                 "is_error_flag": result["is_error"],
                 "error_text": result["text"][:500],
                 "call_site": call["sites"][0],
                 "result_site": result["sites"][0]}
            )
            if found:
                errors.append(
                    {"code": "failed_launch_with_agent_id",
                     "tool_use_id": tool_use_id,
                     "agent_ids": found,
                     "detail": "failed Agent launch also carried an agentId; not counted "
                               "as a worker or helper"}
                )
            continue

        if not found:
            errors.append(
                {"code": "resolved_call_without_agent_id",
                 "tool_use_id": tool_use_id,
                 "subagent_type": call["subagent_type"],
                 "result_site": result["sites"][0],
                 "detail": "successful Agent tool_result carried no agentId"}
            )
            continue

        if len(found) > 1:
            errors.append(
                {"code": "multiple_agent_ids_in_result",
                 "tool_use_id": tool_use_id,
                 "agent_ids": found,
                 "result_site": result["sites"][0],
                 "detail": "one Agent result mentioned several agent IDs; cannot attribute"}
            )
            ambiguous.append(
                {"agent_id": None, "tool_use_id": tool_use_id,
                 "subagent_type": call["subagent_type"],
                 "agent_ids": found,
                 "top_level": call["parent_tool_use_id"] is None,
                 "call_site": call["sites"][0],
                 "reason": "multiple_agent_ids_in_result"}
            )
            continue

        agent_id = found[0]
        owner = agent_id_owner.get(agent_id)
        if owner is not None and owner != tool_use_id:
            errors.append(
                {"code": "agent_id_reused",
                 "agent_id": agent_id,
                 "tool_use_ids": [owner, tool_use_id],
                 "detail": "one agentId was reported by two distinct Agent calls"}
            )
        agent_id_owner[agent_id] = tool_use_id

        top_level = call["parent_tool_use_id"] is None
        declared_helper_role = known.get(agent_id)

        explicit_primary = call["subagent_type"] == PRIMARY_SUBAGENT_TYPE
        pinned_primary = confirmed_pins.get(tool_use_id) == agent_id

        if explicit_primary or pinned_primary:
            if declared_helper_role is not None:
                errors.append(
                    {"code": "role_conflict",
                     "agent_id": agent_id,
                     "tool_use_id": tool_use_id,
                     "detail": "known_helpers assigns role %r to an agent that is %s"
                               % (declared_helper_role,
                                  "launched as %r" % PRIMARY_SUBAGENT_TYPE if explicit_primary
                                  else "pinned by known_primaries")}
                )
            primary_workers.append(
                _entry(call, agent_id, "primary",
                       "subagent_type" if explicit_primary else "known_primaries",
                       result, calls, pinned_calls)
            )
            continue

        if declared_helper_role is not None:
            helpers.append(
                _entry(call, agent_id, declared_helper_role, "known_helpers", result, calls,
                       pinned_calls)
            )
            continue

        if not top_level:
            ancestor = _ancestor_primary(call, calls, pinned_calls)
            if ancestor is not None:
                helpers.append(
                    _entry(call, agent_id, "nested_helper", "nested_under_primary",
                           result, calls, pinned_calls)
                )
            else:
                ambiguous.append(
                    {"agent_id": agent_id, "tool_use_id": tool_use_id,
                     "subagent_type": call["subagent_type"],
                     "top_level": False,
                     "parent_tool_use_id": call["parent_tool_use_id"],
                     "call_site": call["sites"][0],
                     "reason": "nested_under_unknown_parent",
                     "detail": "no enclosing fix-loop-worker call to attribute this agent to"}
                )
            continue

        ambiguous.append(
            {"agent_id": agent_id, "tool_use_id": tool_use_id,
             "subagent_type": call["subagent_type"],
             "description": call["description"],
             "top_level": True,
             "parent_tool_use_id": None,
             "call_site": call["sites"][0],
             "reason": "unknown_top_level_agent",
             "detail": "top-level generic agent; pass it in known_helpers to sanction it "
                       "as auxiliary, otherwise treat the lineage as unresolved"}
        )

    distinct_workers = sorted({entry["agent_id"] for entry in primary_workers})
    if len(distinct_workers) > 1:
        errors.append(
            {"code": "multiple_primary_workers",
             "agent_ids": distinct_workers,
             "tool_use_ids": [e["tool_use_id"] for e in primary_workers],
             "detail": "more than one distinct fix-loop-worker (replacement or ambiguity), "
                       "including nested launches"}
        )
    elif not distinct_workers:
        errors.append(
            {"code": "no_primary_worker",
             "detail": "no successful fix-loop-worker launch found in the audited files"}
        )

    top_level_workers = [e for e in primary_workers if e["top_level"]]
    if distinct_workers and not top_level_workers:
        warnings.append(
            {"code": "primary_worker_not_top_level",
             "detail": "the only fix-loop-worker was launched from inside another agent"}
        )
    if not sessions:
        warnings.append(
            {"code": "no_session_init",
             "detail": "no top-level system:init record identified a coordinator session"}
        )
    elif len(sessions) > 1:
        warnings.append(
            {"code": "multiple_sessions",
             "session_ids": sorted(sessions),
             "detail": "audited files contain more than one top-level session"}
        )

    primary = None
    if len(distinct_workers) == 1 and not any(
        e["code"] in ("multiple_primary_workers", "agent_id_reused",
                      "known_primary_mismatch", "known_primary_call_absent")
        for e in errors
    ):
        primary = distinct_workers[0]

    return {
        "ok": not errors and not ambiguous and not unresolved and primary is not None,
        "sessions": [
            {"session_id": sid, "first_site": sessions[sid]} for sid in sorted(sessions)
        ],
        "primary_worker": primary,
        "primary_workers": primary_workers,
        "helpers": helpers,
        "failed_launches": failed_launches,
        "unresolved": unresolved,
        "ambiguous": ambiguous,
        "errors": errors,
        "warnings": warnings,
        "stats": dict(
            stats,
            agent_calls=len(calls),
            correlated_results=sum(1 for cid in calls if cid in results),
            known_helpers=sorted(known),
            known_primaries=sorted(pins),
            confirmed_primary_pins=sorted(confirmed_pins.values()),
        ),
    }
