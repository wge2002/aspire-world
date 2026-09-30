#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render one r2 cell's prompts through the frozen campaign renderer, gated.

Byte-for-byte `two_task_render.py` with exactly one substitution:
`two_task_scope` becomes `two_task_scope_r2`, whose only behavioural difference
is that `remaining_budget` subtracts a declared `imported_charged_attempts`
instead of refusing it. The renderer itself is untouched, so these bytes are
the frozen renderer's own output; the gate only refuses a render that disagrees
with the case.

Runs no model and no simulator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import two_task_scope_r2 as scope


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
            "gated_by": "support/two_task_scope_r2.py",
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
