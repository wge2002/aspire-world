#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""The independent oracle for condition A: render A from the pristine Sep-14 source.

Condition A is supposed to be the *original* Fix Loop. This study's staged A
runtime is not byte-identical to the Sep-14 A1 reference, because the common
overlay is needed for the local Qwen provider, the repaired protocol/ledger/
held-out modules and the corrected transport. That overlay is applied to both
arms identically, but "identically" is not the claim that matters — the claim
that matters is that it changed nothing about A's *assignment*.

So this module renders the A prompts a second time, from the untouched pristine
reference tree, using that tree's own `native_world_campaign.py`, and prints the
digests. The stager requires them to equal the staged cell's rendered bytes. If
any overlay file ever leaks method text into the A render, that comparison fails
at prepare time instead of at interpretation time.

Two details make the comparison meaningful rather than circular:

* The pristine renderer is loaded from `reference/native-A1-source`, whose tree
  digest and 343-file count the stager checks before staging. It is never the
  engineering copy.
* `simple_world_profile` does not exist in the pristine tree, yet the overlaid
  campaign imports it at module level. It is put on a private path for the
  overlaid load only, so the pristine load stays pristine.

Renders nothing to disk, starts no model and no simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

#: The byte-verified Sep-14 A1 reference, shared with the judgment two-task study.
PRISTINE = Path("docs/experiments/code-world-judgment-two-task-20260917"
                "/reference/native-A1-source")


def _load(module_path: Path, name: str, search: list[Path]):
    """Import one campaign module against its own `scripts/common`, then unload.

    The pristine and overlaid trees both provide modules called
    `native_cc_freeze`, `native_cc_stream` and so on. Leaving either tree's
    copies in `sys.modules` would let the second load bind the first tree's
    helpers, which is precisely the contamination this oracle exists to detect.
    """
    for path in search:
        sys.path.insert(0, str(path))
    try:
        spec = importlib.util.spec_from_file_location(name, module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        for path in search:
            sys.path.remove(str(path))
        for key, value in list(sys.modules.items()):
            origin = getattr(value, "__file__", None)
            if key != name and origin and any(str(p) in str(origin) for p in search):
                del sys.modules[key]
    return module


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def oracle(case: dict, engineering: Path) -> dict:
    """Digests of A's worker and coordinator prompts as the pristine source renders them."""
    if case["condition"] != "A":
        raise ValueError("the pristine A oracle applies to condition A only")
    pristine = (engineering / PRISTINE).resolve()
    campaign = _load(pristine / "scripts/libero/native_world_campaign.py",
                     "pristine_native_world_campaign", [pristine / "scripts/common"])
    worker = campaign.worker_prompt(case, pristine)
    coordinator = campaign.coordinator_prompt(case, Path(case["control"]) / "worker-prompt.md")
    return {"oracle": "pristine Sep-14 A1 native_world_campaign.py + original template",
            "source": str(pristine),
            "render_fields": {key: case[key] for key in
                              ("id", "condition", "suite", "task", "gpu", "sim",
                               "skill_library_dir")},
            "sha256": {"worker-prompt.md": digest(worker),
                       "coordinator-prompt.md": digest(coordinator)},
            "worker_words": len(worker.split())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--engineering", type=Path, default=Path("/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim"))
    args = parser.parse_args(argv)
    print(json.dumps(oracle(json.loads(args.case.read_text()), args.engineering), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
