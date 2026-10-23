"""CC_TASK_12: read-only copies of the two l10 cells' real records for the replay tests.

mugmicrowave_p6b_r1 (G5): turn-00 coordinator stream, the hook audit lines of
that coordinator session, the frozen worker prompt, the outer check after turn 0,
and the development ledger as it stood after turn 0. The cell is still running,
so its ledger file has grown since: the fixture keeps the JSON with `trials`
cut to the first 52 rows (the generation-2 handoff recorded 52 rows / 44 spent
attempts at turn 64, and turns 1-64 changed nothing).

twomoka_p6b_r1 (G6): the development ledger, the outer checks after turns 0-2,
and the evidence of development/repair/seed_51/attempt_19 the watchdog verdict
reads: lifecycle.json, command.json, the replay's trial directory (code.py,
summary.txt; keyframes are left out), and the tail of the judgment world's
event tape (last 400 lines; the verdict reads only the last `elapsed_seconds`).

Sources are only read. Every copied file is hashed into manifest.json together
with the hash of its source (or how it was derived from it).
"""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path("/mnt/home/gewang/experiments/code-world-worker-recovery-p6b-20261021-l10")
OUT = Path(__file__).resolve().parents[4] / "tests/fixtures/p6c_repair"
MUG = ROOT / "mugmicrowave_p6b_r1"
MUG_TASK = MUG / "outputs/libero_fix_loop/libero_10_swap/KITCHEN_SCENE6_put_the_yellow_and_white_mug_in_the_microwave_and_close_it"
MOKA = ROOT / "twomoka_p6b_r1"
MOKA_TASK = MOKA / "outputs/libero_fix_loop/libero_10_swap/KITCHEN_SCENE8_put_both_moka_pots_on_the_stove"
ATTEMPT = "development/repair/seed_51/attempt_19"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack(name, members, meta):
    """members: {archive name: (bytes, provenance)}"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for arcname, (data, _) in sorted(members.items()):
            info = tarfile.TarInfo(arcname)
            # Source mtimes are kept: the watchdog verdict times the policy's end by them.
            info.size, info.mtime, info.mode = len(data), members[arcname][1].get("mtime", 0), 0o644
            tar.addfile(info, io.BytesIO(data))
    archive = OUT / f"{name}.tgz"
    archive.write_bytes(buffer.getvalue())
    return {**meta, "archive_sha256": sha(archive.read_bytes()),
            "files": {k: {"sha256": sha(d), "source": p} for k, (d, p) in sorted(members.items())}}


def copy(path):
    data = Path(path).read_bytes()
    return data, {"path": str(path), "sha256": sha(data), "mtime": Path(path).stat().st_mtime}


def mugmicrowave():
    campaign = next(MUG.glob("campaign-*"))
    state = json.loads((MUG / "campaign_state.json").read_text())
    session, worker = state["worker_generations"][0]["session"], state["worker_generations"][0]["worker"]
    audit_source = (campaign / "assignment-audit.jsonl").read_bytes()
    audit = b"".join(line + b"\n" for line in audit_source.splitlines()
                     if json.loads(line).get("session_id") == session)
    ledger_source = (MUG_TASK / "development_state.json").read_bytes()
    data = json.loads(ledger_source)
    data["trials"] = data["trials"][:52]
    ledger = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    handoff = json.loads((MUG / "native/worker-generations/generation-02-handoff.json").read_text())
    members = {
        "turn-00.stdout.jsonl": copy(campaign / "turn-00.stdout.jsonl"),
        "turn-01.stdout.jsonl": copy(campaign / "turn-01.stdout.jsonl"),
        "outer_check-00.json": copy(campaign / "outer_check-00.json"),
        "worker-prompt.md": copy(MUG / "worker-prompt.md"),
        "assignment-audit.jsonl": (audit, {"path": str(campaign / "assignment-audit.jsonl"),
                                           "derived": f"lines with session_id {session}",
                                           "source_sha256_at_copy": sha(audit_source)}),
        "development_state.json": (ledger, {"path": str(MUG_TASK / "development_state.json"),
                                            "derived": "trials[:52] (state after turn 0)",
                                            "source_sha256_at_copy": sha(ledger_source)}),
    }
    return pack("mugmicrowave_p6b_r1", members, {
        "worker": worker, "session": session, "ledger_rows": 52,
        "attempts_spent": handoff["attempts_spent"], "real_detected_turn": 64,
        "real_recovery_dispatch_turn": 65})


def twomoka():
    campaign = next(MOKA.glob("campaign-*"))
    attempt = MOKA_TASK / ATTEMPT
    members = {
        "development_state.json": copy(MOKA_TASK / "development_state.json"),
        **{f"outer_check-0{n}.json": copy(campaign / f"outer_check-0{n}.json") for n in range(3)},
        f"{ATTEMPT}/lifecycle.json": copy(attempt / "lifecycle.json"),
        f"{ATTEMPT}/command.json": copy(attempt / "command.json"),
    }
    run = next(attempt.glob("results/*/*/*/run/trial_*"))
    for name in ("code.py", "summary.txt"):
        members[f"{ATTEMPT}/{run.relative_to(attempt)}/{name}"] = copy(run / name)
    tape = (attempt / "judgment_world/events.jsonl").read_bytes()
    members[f"{ATTEMPT}/judgment_world/events.jsonl"] = (
        b"".join(line + b"\n" for line in tape.splitlines()[-400:]),
        {"path": str(attempt / "judgment_world/events.jsonl"), "derived": "last 400 lines",
         "source_sha256": sha(tape)})
    state = json.loads((MOKA / "campaign_state.json").read_text())
    return pack("twomoka_p6b_r1", members, {
        "worker": state["worker_generations"][0]["worker"], "session": state["worker_generations"][0]["session"],
        "real_blocked": state["blocked"], "real_turns": len(state["turns"]),
        "real_total_seconds": state["total_seconds"], "attempt": ATTEMPT})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {"note": __doc__.strip().splitlines()[0], "mugmicrowave_p6b_r1": mugmicrowave(),
                "twomoka_p6b_r1": twomoka()}
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: v.get("archive_sha256") for k, v in manifest.items() if isinstance(v, dict)}))


if __name__ == "__main__":
    sys.exit(main())
