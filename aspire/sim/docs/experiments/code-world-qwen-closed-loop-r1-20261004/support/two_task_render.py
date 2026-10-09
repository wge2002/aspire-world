#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render one cell's prompts through the frozen campaign renderer, gated.

Same frozen renderer and same `write_prompts` the original `--render-only` uses,
with `two_task_scope.apply()` installed first. That gate rewrites nothing, so
these bytes are the frozen renderer's own output; it only refuses a render that
disagrees with the case. The driver installs the identical gate before its own
`write_prompts`, and `apply` is idempotent per cell, so the prepare-time render
and the run-time render produce the same bytes.

Runs no model and no simulator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import two_task_scope as scope


def render(case_path: Path) -> dict:
    case = json.loads(Path(case_path).read_text())
    repo = Path(case["sim"]).resolve()
    control = Path(case["control"])
    control.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign

    scope.apply(campaign, case)
    prompt, coordinator = campaign.write_prompts(case, repo, control)
    return {"worker_prompt": str(prompt), "coordinator": str(coordinator),
            "diff": str(control / "worker-prompt.diff"),
            "gated_by": "support/two_task_scope.py",
            "rewrites": "none; the gate returns the frozen renderer's own bytes",
            "condition": case["condition"], "task": case["task"],
            "dev_seeds": scope.dev_seeds(case),
            "heldout_seeds": scope.heldout_seeds(case),
            "remaining_budget": scope.remaining_budget(case),
            "interface_doc": scope.interface_doc(case)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(render(args.case), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
