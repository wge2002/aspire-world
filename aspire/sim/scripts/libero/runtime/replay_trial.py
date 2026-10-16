"""Fresh runtime study: verify and record the actual constructed public API."""
import json
import os
from pathlib import Path
import aspire.sim.scripts.libero.replay_trial as replay


def verify_environment(env):
    apis = {key: type(api).__module__ + "." + type(api).__name__
            for key, api in env._apis.items()}
    if env.cfg.privileged is not False or env.low_level_env.privileged is not False:
        raise RuntimeError("runtime experiment refuses actual privileged environments")
    if list(apis) != ["FrankaLiberoApiReducedSkillLibraryTraced"]:
        raise RuntimeError(f"runtime experiment refuses unexpected constructed APIs: {apis}")
    return {"environment": type(env).__name__, "low_level": type(env.low_level_env).__name__,
            "privileged": False, "constructed_apis": apis, "prompt": env.cfg.prompt}


def main():
    original = replay.instantiate
    def instantiate(config):
        env = original(config)
        proof = verify_environment(env)
        directory = Path(os.environ["ASPIRE_RUNTIME_EVIDENCE_DIR"])
        (directory / "resolved-environment.json").write_text(json.dumps(proof, indent=2) + "\n")
        return env
    replay.instantiate = instantiate
    replay.tyro.cli(replay.main)


if __name__ == "__main__":
    main()
