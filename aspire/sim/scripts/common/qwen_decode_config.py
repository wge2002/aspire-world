#!/usr/bin/env python3
"""Port main's Qwen decode speed fix without changing cell or GPU allocation.

Source: 401a5ba23feabe1a6a46ea2a301f14c1f897d846,
scripts/common/node_layout/unit_layout.py, DESIGN section 1. The config
transformation is separate from that branch's new unit layout and p2/p3 methods.
This CLI writes a NEW manifest only; it never starts services or edits a case,
frozen runtime, parent manifest, ledger, deadline or attempt budget.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

SOURCE_COMMIT = "401a5ba23feabe1a6a46ea2a301f14c1f897d846"
COMPILATION_CONFIG = {"mode": 0, "cudagraph_mode": "FULL_DECODE_ONLY",
                      "cudagraph_capture_sizes": [1, 2, 4]}


def enable_decode_graph(manifest: dict) -> dict:
    """Same three engine changes as main; keep all host/capacity settings."""
    result = copy.deepcopy(manifest)
    argv, env = result["argv"], result["env"]
    if "--disable-custom-all-reduce" in argv:
        raise ValueError("the decode profile requires custom all-reduce to stay enabled")
    if argv[argv.index("--tensor-parallel-size") + 1] != "4":
        raise ValueError("this profile was validated for Qwen TP4")
    argv[:] = [arg for arg in argv if arg != "--enforce-eager"]
    if "--compilation-config" in argv:
        if argv.count("--compilation-config") != 1 or json.loads(argv[argv.index("--compilation-config") + 1]) != COMPILATION_CONFIG:
            raise ValueError("an existing different compilation profile needs explicit review")
    else:
        argv.extend(["--compilation-config", json.dumps(COMPILATION_CONFIG)])
    # main removes this variable: expandable_segments breaks graph capture's
    # custom all-reduce. Do not change allocator settings of perception services.
    if "expandable_segments" in env.get("PYTORCH_CUDA_ALLOC_CONF", ""):
        del env["PYTORCH_CUDA_ALLOC_CONF"]
    if "expandable_segments" in env.get("PYTORCH_ALLOC_CONF", ""):
        raise ValueError("PYTORCH_ALLOC_CONF also requests expandable_segments; review it explicitly")
    if "argv_sha256" in result:
        # Same serialization as streaming_dlc_entry.verify_model.
        result["argv_sha256"] = hashlib.sha256(json.dumps(argv, sort_keys=True).encode()).hexdigest()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    raw = args.source.read_bytes()
    result = enable_decode_graph(json.loads(raw))
    result["decode_graph_update"] = {
        "source_commit": SOURCE_COMMIT, "parent_manifest": str(args.source.resolve()),
        "parent_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "changes": ["remove --enforce-eager", "FULL_DECODE_ONLY graphs at sizes 1,2,4; mode0",
                    "remove incompatible expandable_segments allocator"],
        "gpu_layout_and_memory_budget_preserved": True,
        "validation": "configuration conversion only; new-runtime GPU/native/perception preflight still required"}
    # Never overwrite a frozen manifest, an earlier conversion, or its input.
    with args.out.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"manifest": str(args.out), "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
                      "services_started": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
