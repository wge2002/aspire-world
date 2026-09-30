"""Bounded Opus generation for the relational scene revision; never executes it."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scene_world_smoke import Model, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--attempt", type=int, choices=[1, 2], required=True)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    output = campaign / "generation" / f"attempt-{args.attempt}"
    if args.attempt == 2 and not (campaign / "generation/attempt-1/attempt.json").exists():
        raise ValueError("revision requires a recorded first attempt")
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads((campaign / "control/generation-config.json").read_text())
    assert config["model"] == "claude-opus-4-6"
    assert config["base_url"] == "https://llmapi.roboscience.xyz:18443"
    prompt_path = campaign / "control" / f"generation-prompt-{args.attempt}.txt"
    prompt = prompt_path.read_text()
    save(output / "attempt.json", {"attempt": args.attempt, "started_unix": time.time(),
         "model": config["model"], "request_cap": 1, "automatic_retries": 0,
         "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()})
    text, receipt = Model(config, output).request("world", prompt, max_tokens=7500)
    source = text.strip()
    if source.startswith("```"):
        source = source.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    source += "\n"
    tree = ast.parse(source)
    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if not {"initialize", "advance", "predict", "assimilate"} <= functions:
        raise ValueError("missing numerical interface")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(n.name != "math" for n in node.names):
            raise ValueError("only math imports allowed")
        if isinstance(node, ast.ImportFrom) and (node.module != "math" or node.level):
            raise ValueError("only math imports allowed")
    with (output / "world_program.py").open("x") as stream:
        stream.write(source)
    save(output / "source-receipt.json", {"sha256": hashlib.sha256(source.encode()).hexdigest(),
         "response_model": receipt["response_model"], "elapsed_seconds": receipt["elapsed_seconds"],
         "usage": receipt["usage"], "source_edited_by_coordinator": False,
         "validation": "syntax and interface names only; numerical tests still required"})
    print(json.dumps({"status": "generated", "attempt": args.attempt,
         "source": str(output / "world_program.py"), "receipt": receipt}), flush=True)


if __name__ == "__main__":
    main()
