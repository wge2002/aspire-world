"""An independent same-backbone VLM judge of a recorded rollout.

Reads three evenly spaced agentview keyframes the replay already saved and the
task language, and asks the served model for `success`, `failure` or `unsure`.
It sees no world state, no self-evaluation, no trace and no outcome label, so
its verdict is a second, independent grade that can be compared with both the
environment and the world's own `done()`.

Used in-loop as the `vlm_judge` development gate and post hoc over any trial
directory (held-out seeds included) by the study analysis. Infrastructure
trouble is reported as `unavailable`, never as a verdict.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

VERDICTS = ("success", "failure", "unsure")
KEYFRAME = re.compile(r"video_frame_(\d+)_of_(\d+)_step_(\d+)\.jpg$")

SYSTEM = ("You are an impartial judge of robot manipulation outcomes. You are shown "
          "frames from one recorded attempt and the task instruction. Decide only from "
          "what is visible. Do not assume the robot succeeded because it moved; a task "
          "counts as success only if the final frame shows the instructed end state.")
INSTRUCTION = ("Task instruction: {task}\n\n"
               "You will see {n} frames from the attempt in chronological order; the last one "
               "is the final state. Answer in exactly two lines:\n"
               "VERDICT: success|failure|unsure\n"
               "REASON: <one sentence naming the visual evidence>\n"
               "Use `unsure` when the end state is occluded or not visible.")


def keyframes(trial_dir: Path) -> list[Path]:
    folder = Path(trial_dir) / "keyframes"
    rows = []
    for path in folder.glob("video_frame_*.jpg"):
        match = KEYFRAME.search(path.name)
        if match:
            rows.append((int(match[1]), path))
    return [p for _, p in sorted(rows)]


def select_frames(frames: list[Path], count: int = 3) -> list[Path]:
    if not frames:
        return []
    if len(frames) <= count:
        return list(frames)
    indices = sorted({round(i * (len(frames) - 1) / (count - 1)) for i in range(count)})
    return [frames[i] for i in indices]


def data_url(path: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def build_messages(task_language: str, frame_paths: list[Path]) -> list[dict]:
    content = [{"type": "text", "text": INSTRUCTION.format(task=task_language, n=len(frame_paths))}]
    labels = ["initial"] + ["midway"] * max(0, len(frame_paths) - 2) + (["final"] if len(frame_paths) > 1 else [])
    for label, path in zip(labels, frame_paths):
        content.append({"type": "text", "text": f"Frame ({label}):"})
        content.append({"type": "image_url", "image_url": {"url": data_url(path)}})
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def parse_verdict(text: str) -> tuple[str | None, str]:
    body = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    match = re.search(r"VERDICT\s*:\s*\**\s*(success|failure|unsure)\b", body, re.I)
    reason = re.search(r"REASON\s*:\s*(.+)", body, re.I)
    return (match[1].lower() if match else None,
            reason[1].strip() if reason else body.strip()[-400:])


def request(endpoint: str, model: str, messages: list[dict], *, effort: str,
            max_tokens: int, timeout: int, retries: int = 3) -> dict:
    """One bounded chat-completions request. 404 means a wrong route, not a retry."""
    import requests
    from aspire.sim.cap.llm.client import qwen3_8_flash_next_request_options, is_qwen3_8_flash_next_model
    payload = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": max_tokens}
    if is_qwen3_8_flash_next_model(model):
        payload.update(qwen3_8_flash_next_request_options(effort))
    url = endpoint.rstrip("/") + "/v1/chat/completions"
    last = None
    for attempt in range(retries):
        try:
            response = requests.post(url, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(min(30, 5 * (attempt + 1)))
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            last = f"HTTP {response.status_code}"
            time.sleep(min(60, 10 * (attempt + 1)))
            continue
        if response.status_code != 200:
            raise RuntimeError(f"judge endpoint answered HTTP {response.status_code}: {response.text[:300]}")
        return response.json()
    raise RuntimeError(f"judge endpoint unavailable after {retries} attempts: {last}")


def judge(endpoint: str, model: str, task_language: str, trial_dir: Path, *,
          effort: str = "high", max_tokens: int = 4096, timeout: int = 600,
          frames: int = 3, requester=None) -> dict:
    started = time.monotonic()
    report = {"model": model, "task_language": task_language, "trial_dir": str(trial_dir),
              "status": "unavailable", "verdict": None, "reason": None, "frames": []}
    selected = select_frames(keyframes(trial_dir), frames)
    report["frames"] = [str(p.relative_to(Path(trial_dir))) for p in selected]
    if not selected:
        report["reason"] = "no keyframes were saved for this trial"
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
        return report
    try:
        body = (requester or request)(endpoint, model, build_messages(task_language, selected),
                                      effort=effort, max_tokens=max_tokens, timeout=timeout)
        message = body["choices"][0]["message"]
        text = message.get("content") or ""
        verdict, reason = parse_verdict(text)
        report.update(raw=text[-2000:], reasoning_present=bool(message.get("reasoning") or message.get("reasoning_content")))
        if verdict is None:
            report.update(status="complete", verdict="unsure",
                          reason="judge gave no parseable VERDICT line; recorded as unsure")
        else:
            report.update(status="complete", verdict=verdict, reason=reason)
    except (RuntimeError, KeyError, IndexError, TypeError, ValueError) as exc:
        report["reason"] = f"{type(exc).__name__}: {exc}"
    report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial-dir", type=Path, required=True)
    parser.add_argument("--task-language", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", default="high")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = judge(args.endpoint, args.model, args.task_language, args.trial_dir,
                   effort=args.effort, max_tokens=args.max_tokens, timeout=args.timeout)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
