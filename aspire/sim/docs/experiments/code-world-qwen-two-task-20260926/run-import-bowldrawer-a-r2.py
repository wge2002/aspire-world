#!/usr/bin/env python3
"""SUPERSEDED — do not run. Use `stage-bowldrawer-a-r2.py` instead.

This script staged r2 by copying r1's tree and rewriting paths inside r1's
`runtime-manifest.json`. That cannot work. `native_cc_freeze.verify_runtime`
recomputes `source_inventory(repo)` and `dependency_bindings(repo)` from the
staged tree on every protocol action and compares them against the bound
manifest, and `dependency_bindings` records `str(path.resolve(strict=True))` for
every symlink. A manifest has to be *built* from the r2 tree
(`native_cc_freeze.build_manifest`), not text-patched from r1's, and every
symlink has to resolve inside r2.

It also guessed the ledger initialization interface. There is none in
`native_world_fixloop_state.py` — that module has no CLI at all. Initialization
is `native_world_protocol.py --case <case.json> init`, which builds the protocol
identity (including `verify_runtime`) and then constructs `NativeWorldState`
with `resume=False`.

`stage-bowldrawer-a-r2.py` goes through the frozen staging path instead
(`prepare-two-task.stage_cell` + `legacy_stager.bind_manifest`), which is the
same path r1 was staged by, so the manifest, the symlinks, the output isolation
and the condition-A prompt oracle are all produced rather than approximated.

Kept as a record of the rejected approach.
"""
raise SystemExit(__doc__)
