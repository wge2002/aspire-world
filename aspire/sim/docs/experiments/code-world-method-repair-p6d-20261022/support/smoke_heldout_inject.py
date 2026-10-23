"""CC_TASK_14 smoke only: `heldout_stop_on_infra.main`, with one held-out seed under a 60 s watchdog.

Seed INJECT_SEED's replay is given INJECT_TIMEOUT seconds instead of the trial
timeout, and its watchdog verdict is judged against that budget, so a policy still
acting is killed exactly as a 900 s overrun would be. The evaluator's own
`heldout_watchdog: settle` rule must then record a failed seed (kind
policy_timeout) instead of an infrastructure row that stops the sweep. Every other
seed and every other rule is the frozen evaluator's.
"""
import json
from pathlib import Path
import sys

INJECT_SEED, INJECT_TIMEOUT = 7, 60


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    case = json.loads(Path(argv[argv.index("--case") + 1]).read_text())
    sys.path.insert(0, str(Path(case["sim"]) / "scripts/libero"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import native_world_heldout as heldout
    import heldout_stop_on_infra as stop
    prepare, verdict = heldout.prepare_seed, heldout.watchdog_verdict

    def prepare_seed(case, repo, evaluation, bundle, kept, seed):
        launch = prepare(case, repo, evaluation, bundle, kept, seed)
        if seed == INJECT_SEED:
            launch["timeout"] = INJECT_TIMEOUT
            (Path(launch["directory"]) / "smoke-injection.json").write_text(json.dumps(
                {"seed": seed, "timeout_seconds": INJECT_TIMEOUT, "trial_timeout": case["trial_timeout"]}))
        return launch

    def watchdog_verdict(case, directory, results, phase, seed, error):
        if seed == INJECT_SEED:
            case = dict(case, trial_timeout=INJECT_TIMEOUT)
        return verdict(case, directory, results, phase, seed, error)
    heldout.prepare_seed, heldout.watchdog_verdict = prepare_seed, watchdog_verdict
    return stop.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
