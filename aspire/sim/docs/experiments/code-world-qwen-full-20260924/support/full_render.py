#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render this study's prompts through the frozen campaign renderer, scoped.

Same frozen renderer and same `write_prompts` as `--render-only`, with
`full_scope.apply()` patched in first so the staged interface path and the
actual per-seed budget agree with the case. The driver applies exactly the same
patch before its own `write_prompts`, and `full_scope.apply` is idempotent per
cell, so the prepare-time render and the run-time render produce the same bytes.

Runs no model and no simulator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import full_scope


def render(case_path: Path) -> dict:
    case = json.loads(Path(case_path).read_text())
    repo = Path(case["sim"]).resolve()
    control = Path(case["control"])
    control.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign

    full_scope.apply(campaign, case)
    prompt, coordinator = campaign.write_prompts(case, repo, control)
    return {"worker_prompt": str(prompt), "coordinator": str(coordinator),
            "diff": str(control / "worker-prompt.diff"),
            "scoped_by": "support/full_scope.py",
            "dev_seeds": full_scope.dev_seeds(case),
            "heldout_seeds": list(full_scope.HELDOUT_SEEDS),
            "remaining_budget": full_scope.remaining_budget(case),
            "interface_doc": full_scope.interface_doc(case)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(render(args.case), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
