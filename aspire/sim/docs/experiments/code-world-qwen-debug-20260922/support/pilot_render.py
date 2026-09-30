#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render this pilot's prompts through the frozen campaign renderer, scoped.

`native_world_campaign.py --render-only` renders the pristine 51-65 development
scope and promises a Stage 2 held-out evaluation. This pilot has neither, so the
stager's `render_prompts` calls this adapter instead: same frozen renderer, same
`write_prompts`, same printed JSON contract, with `pilot_scope.apply()` patched
in first so the prompt the worker is actually handed agrees with the case.

The driver applies exactly the same patch before its own `write_prompts`, and
`pilot_scope.apply` is idempotent per cell, so the prepare-time render and the
run-time render produce the same bytes. Runs no model and no simulator.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pilot_scope  # beside this file, frozen with it at the result parent


def render(case_path: Path) -> dict:
    case = json.loads(Path(case_path).read_text())
    repo = Path(case["sim"]).resolve()
    control = Path(case["control"])
    control.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(repo / "scripts/libero"))
    import native_world_campaign as campaign

    pilot_scope.apply(campaign, case)
    prompt, coordinator = campaign.write_prompts(case, repo, control)
    return {"worker_prompt": str(prompt), "coordinator": str(coordinator),
            "diff": str(control / "worker-prompt.diff"),
            "scoped_by": "support/pilot_scope.py",
            "dev_seeds": sorted(int(s) for s in case["dev_seeds"]),
            "remaining_budget": pilot_scope.remaining_budget(case),
            "interface_doc": pilot_scope.interface_doc(case)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(render(args.case), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
