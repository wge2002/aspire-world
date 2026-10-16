"""Additional experiment read boundary; not an OS security sandbox.

The original frozen guard remains active. This hook restricts ordinary native
file tools and literal shell paths to the cell, including resolved symlinks.
It supplies no robot strategy, changes no trial budget and reads no results.

Runtime outputs are admitted by *ownership*, not by spelling: the canonical
output root comes from `output_ownership.canonical_root(case)`, i.e. from the
case, so this cell's own development images and generated code are readable
while another cell's outputs, a stale attempt's outputs and anything under a
`heldout` path stay rejected. When a runtime-output link is misowned the denial
names the link, its actual target and the canonical root instead of reading as a
generic per-file boundary error (the retry4 failure mode).
"""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import output_ownership


def path_allowed(value, case, cwd, *, shell=False):
    value = str(value)
    # Check the directory searched by a wildcard, not a nonexistent glob path.
    wildcard = re.search(r"[*?\[]", value)
    if wildcard:
        value = value[:wildcard.start()].rsplit("/", 1)[0] or "."
    lexical = Path(cwd) / value
    resolved = lexical.resolve()
    repo = Path(case["sim"]).resolve()
    control = Path(case["control"]).resolve()
    if "heldout" in resolved.parts or "sealed" in resolved.parts:
        return False
    # The canonical root is derived from the case; `control / "outputs"` is kept
    # as the same value it always was, now resolved through the owner helper so a
    # copied link cannot define its own root.
    if resolved.is_relative_to(repo) or output_ownership.resolves_to_root(
            resolved, output_ownership.canonical_root(case)):
        return True
    if resolved in {control / "worker-prompt.md", control / "coordinator-prompt.md"}:
        return True
    # Invoking the prepared interpreter does not authorize reading its tree.
    if (shell and lexical.name in {"python", "python3"}
            and lexical.parent.name == "bin" and lexical.parent.parent.name == ".venv-libero"
            and lexical.parent.parent.parent.resolve() == repo):
        return True
    return shell and value in {"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"}


PATH = re.compile(r"(?<![\w:])(?:/(?!/)|\.\.?/)[\w.*?@+~=$/-]+")
LOCAL_HEALTH_URL = re.compile(
    r"http://127\.0\.0\.1:(?:811[456]|\$p|\$\{p\})/health(?=$|[\s'\"`)])")


def misowned_outputs(case):
    """Name a broken runtime-output link once, instead of denying file by file.

    A copied `sim/outputs` symlink that still points at another experiment's
    control directory makes every legitimate output read fail. Report the link,
    its actual target and the canonical root so the cause is attributable.
    """
    report = output_ownership.inspect(case, Path(case["sim"]))
    if report["ok"]:
        return None
    return ("Cell output ownership error: this cell's canonical runtime output root is "
            f"{report['canonical_root']}, but " + "; ".join(report["problems"])
            + ". This is a staging defect, not a read-boundary decision: no read of this "
              "cell's own development outputs can succeed until the link is re-staged.")


def denial(payload, case):
    name = payload.get("tool_name")
    inp = payload.get("tool_input", {})
    cwd = payload.get("cwd") or case["sim"]
    reason = "Cell read boundary: use only this cell's runtime, public API and development outputs; other cells and held-out artifacts are unavailable."
    if name in {"Read", "Glob", "Grep", "Write", "Edit", "MultiEdit"}:
        value = inp.get("file_path") or inp.get("path") or "."
        if not path_allowed(value, case, cwd):
            # A denial of a path that *should* be this cell's own output is almost
            # always a misowned link; say so by name rather than as a boundary.
            return misowned_outputs(case) or reason
        if name == "Glob" and not path_allowed(inp.get("pattern", "."), case, Path(cwd) / value):
            return reason
    if name == "Bash":
        command = inp.get("command", "")
        # A variable directory must not be used to traverse to a sibling.
        if re.search(r"\$\{?\w+\}?/(?:[^\s;]*?/)?\.\.(?:/|[\s;]|$)", command):
            return reason
        assignments = re.findall(r"(?<![\w$])(\w+)=([\"']?)([\w./-]+)\2(?=$|[\s;&])", command)
        for key, _, value in assignments:
            command = re.sub(r"\$\{" + re.escape(key) + r"\}|\$" + re.escape(key) + r"\b",
                             lambda _: value, command)
        command = re.sub(r"(?<=[\w./-])[\"'](?=/)", "", command)
        # The original worker checks these existing services. A URL is not a
        # filesystem path; remove only these health URLs before checking paths.
        command = LOCAL_HEALTH_URL.sub(" ", command)
        for match in PATH.finditer(command):
            path = match.group()
            # A suffix after an interpolated variable is part of the preceding
            # path (whose literal root is checked separately), not a new root.
            if re.search(r"\$\{?\w+\}?[\"']?$", command[:match.start()]):
                continue
            if re.fullmatch(r"/\d+", path):  # e.g. f"passed={count}/15"
                continue
            # A variable suffix does not make a forbidden literal root safe.
            path = path.split("$")[0].rstrip("/") or "/"
            if not path_allowed(path, case, cwd, shell=True):
                return reason
        if re.search(r"(?:^|[\s\"'])heldout(?:/|[\s\"'])", command):
            return reason
        if re.search(r"(?:^|[\s\"'/])sealed(?:/|[\s\"'])", command):
            return reason
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--case", type=Path, required=True)
    p.add_argument("--audit", type=Path, required=True)
    a = p.parse_args()
    try:
        case = json.loads(a.case.read_text())
        payload = json.load(sys.stdin)
        reason = denial(payload, case)
        event = {"tool": payload.get("tool_name"), "denied": bool(reason),
                 "path": payload.get("tool_input", {}).get("file_path") or
                         payload.get("tool_input", {}).get("path"), "reason": reason}
        with a.audit.open("a") as out:
            out.write(json.dumps(event) + "\n")
    except Exception as exc:
        reason = f"Cell read guard failed: {type(exc).__name__}: {exc}"
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
