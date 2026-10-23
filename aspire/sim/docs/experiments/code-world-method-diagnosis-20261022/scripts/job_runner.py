"""One replay job through the production held-out functions of a cell runtime copy.

Same functions the driver's held-out sweep uses (native_world_heldout.prepare_seed /
run_replay / settle_seed via run_parallel): same runtime replay wrapper, same env
(runtime_env + ASPIRE_MAX_STEPS + parallel_env thread caps), same world-config writer,
same per-seed timeout and status rules.  Only two things differ from a held-out sweep:
the seed list, and the bundle (any executed development version, not only the frozen one).

  job_runner.py --case <job case.json> --policy <code.py> --world <world_program.py>
                --seeds 51-65 [--label v5]
Writes <control>/replay/{replay_state.json, replay_result.json}.  Never writes outside
case["control"].
"""
import argparse, json, sys
from pathlib import Path

sys.dont_write_bytecode = True
ap = argparse.ArgumentParser()
ap.add_argument("--case", required=True)
ap.add_argument("--policy", required=True)
ap.add_argument("--world", required=True)
ap.add_argument("--seeds", required=True)
ap.add_argument("--label", default="")
a = ap.parse_args()
case = json.loads(Path(a.case).read_text())
repo = Path(case["sim"])
sys.path.insert(0, str(repo / "scripts/libero"))
import native_world_heldout as H  # noqa: E402

lo, hi = (int(x) for x in a.seeds.split("-"))
H.HELDOUT_SEEDS = tuple(range(lo, hi + 1))
evaluation = Path(case["control"]) / "replay"
frozen = evaluation / "frozen"
frozen.mkdir(parents=True, exist_ok=True)
kept, bundle = {}, {}
for key, src in (("policy", a.policy), ("world", a.world)):
    dst = frozen / ("fix_code.py" if key == "policy" else "fix_world_program.py")
    if not dst.exists():
        dst.write_bytes(Path(src).read_bytes())
    kept[key] = dst
    bundle[key] = H.code_hash(dst.read_text())
ledger = evaluation / "replay_state.json"
data = json.loads(ledger.read_text()) if ledger.exists() else {"identity": {
    "source_case": case.get("source_case"), "label": a.label, "bundle": bundle,
    "bundle_sha256": H.bundle_identity(bundle), "seeds": list(H.HELDOUT_SEEDS)}, "seeds": {}}
parallel, threads = H.parallel_settings(case, "heldout")
H.run_parallel(case, repo, evaluation, bundle, kept, ledger, data, parallel, threads)
rows = [data["seeds"][str(s)] for s in H.HELDOUT_SEEDS]
counts = {k: sum(r["status"] == k for r in rows) for k in ("success", "failure", "crash", "infrastructure_error")}
report = {"label": a.label, "cell": case["id"], "identity": data["identity"], "counts": counts,
          "success_rate": counts["success"] / len(rows),
          "per_seed": {str(r["seed"]): r["status"] for r in rows},
          "execution": {"heldout_parallel": parallel, "trial_threads": threads}}
(evaluation / "replay_result.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report))
