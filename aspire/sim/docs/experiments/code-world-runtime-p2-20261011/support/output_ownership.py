# SPDX-License-Identifier: MIT
"""Canonical runtime-output ownership for one cell.

This is the repair for the retry4 failure: the staged runtime carried a copied
`sim/outputs` symlink that still pointed at the OLD experiment's control
directory, so the cell's own `scene_snapshot.jpg`, `wrist.jpg` and generated code
lived outside the root the read guard was willing to accept and were rejected as
if they belonged to another cell. The guard was right; the *ownership* was wrong.

Two things are therefore separated here:

`canonical_root(case)`
    The one directory this cell may write runtime outputs into:
    `<control>/outputs`, resolved. Derived from the case, never from the tree, so
    a misowned link cannot define its own canonical root.

`verify(case, repo)`
    Checked at stage time *and* again at driver start, before any model or
    simulator process exists. Every writable/runtime-output link must resolve
    exactly to the canonical root. A link that resolves anywhere else — another
    cell, a stale attempt, the pilot's control directory — is a named,
    attributable error, not a diffuse per-file denial later.

`owned(path, case)`
    The predicate `cell_read_guard` uses so this cell's own canonical
    development images and code read as its own, while other cells' outputs and
    anything under a `heldout` path stay rejected. It widens nothing: the global
    `heldout` boundary is applied by the caller and is not weakened here.

This is a provenance/ownership guard, not an OS security sandbox.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

#: Links inside the staged runtime that must resolve to the canonical root.
#: `sim/outputs` is the only runtime-output link `stage_cell` creates; the rest
#: are the historical spellings that a copied tree can drag along.
RUNTIME_OUTPUT_LINKS = ("outputs",)

#: Subdirectories the driver and solver actually write under the canonical root.
REQUIRED_SUBDIRS = ("working_codes",)


class OwnershipError(RuntimeError):
    """A runtime-output path is not owned by this cell's canonical root."""


def canonical_root(case: dict) -> Path:
    """This cell's only writable runtime-output root, from the case alone."""
    control = case.get("control")
    if not control:
        raise OwnershipError("case has no `control` directory; cannot derive an output root")
    root = Path(control).resolve() / "outputs"
    if root.is_symlink():
        raise OwnershipError(f"canonical output root must be a real directory, not a symlink: {root}")
    return root


def resolves_to_root(path: Path, root: Path) -> bool:
    """True when `path` is the canonical root itself or lives under it.

    Resolution is applied to both sides, so a symlink chain that lands inside the
    root is owned and one that merely *looks* like it is not.
    """
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return resolved == root or resolved.is_relative_to(root)


def owned(path, case: dict) -> bool:
    """Whether `path` is this cell's own canonical runtime output.

    Used by the read guard. The repo tree is a separate allowance there; this
    answers only the outputs question, and answers it by resolved identity.
    """
    return resolves_to_root(Path(path), canonical_root(case))


def _describe(link: Path) -> dict:
    info = {"path": str(link), "exists": link.exists(), "is_symlink": link.is_symlink()}
    if link.is_symlink():
        info["link_target"] = os.readlink(link)
    try:
        info["resolved"] = str(link.resolve())
    except OSError as exc:  # pragma: no cover - broken link on an odd filesystem
        info["resolved"] = f"<unresolvable: {exc}>"
    return info


def inspect(case: dict, repo) -> dict:
    """Report every runtime-output link and whether it is canonically owned."""
    root = canonical_root(case)
    report = {"canonical_root": str(root),
              "canonical_root_exists": root.is_dir(),
              "links": {}, "problems": []}
    if not report["canonical_root_exists"]:
        report["problems"].append(f"canonical output root does not exist: {root}")
    for relative in RUNTIME_OUTPUT_LINKS:
        link = Path(repo) / relative
        info = _describe(link)
        info["owned"] = link.exists() and link.resolve() == root
        report["links"][relative] = info
        if not link.exists():
            report["problems"].append(f"runtime output link missing: {link}")
        elif not info["owned"]:
            report["problems"].append(
                f"runtime output link {link} resolves to {info['resolved']}, "
                f"not to this cell's canonical root {root}")
    for name in REQUIRED_SUBDIRS:
        target = root / name
        report.setdefault("subdirs", {})[name] = target.is_dir()
        if not target.is_dir():
            report["problems"].append(f"required output subdirectory missing: {target}")
    report["ok"] = not report["problems"]
    return report


def verify(case: dict, repo) -> dict:
    """`inspect`, and raise on the first ownership problem.

    Called at stage time and again at driver start, before a model or simulator
    process exists, so a misowned tree costs nothing instead of a whole cell.
    """
    report = inspect(case, repo)
    if not report["ok"]:
        raise OwnershipError(json.dumps({"output_ownership_failed": report}, indent=2))
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, required=True)
    args = parser.parse_args(argv)
    case = json.loads(args.case.read_text())
    report = inspect(case, Path(case["sim"]))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
