"""Apply narrow opt-in hooks to engineering source, preserving original bytes."""
from pathlib import Path
import hashlib
import json

SIM = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def replace(path, changes):
    p = SIM / path
    text = p.read_text()
    if 'executable_world' in text and path not in ('scripts/libero/native_world_protocol.py',):
        raise ValueError(f'{path} already integrated; inspect instead of double-patching')
    original = text
    for before, after in changes:
        if text.count(before) != 1:
            raise ValueError(f'{path}: expected exactly one integration point {before[:80]!r}')
        text = text.replace(before, after)
    backup = HERE / 'engineering-base' / path
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists() and backup.read_text() != original:
        raise ValueError('original engineering backup differs')
    backup.write_text(original)
    compile(text, str(p), 'exec')
    p.write_text(text)
    return {'before_sha256': hashlib.sha256(original.encode()).hexdigest(),
            'after_sha256': hashlib.sha256(text.encode()).hexdigest()}


def main():
    changes = {}
    changes['scripts/libero/replay_trial.py'] = replace('scripts/libero/replay_trial.py', [
        ('        if mode == "opus46-simple-world":',
         '        if mode == "opus46-executable-world-c-r1":\n'
         '            from aspire.sim.cap.world_model.executable_world import run_executable_world\n'
         '            return run_executable_world(args)\n'
         '        if mode == "opus46-simple-world":')])
    changes['scripts/libero/native_world_campaign.py'] = replace('scripts/libero/native_world_campaign.py', [
        ('    if is_judgment(case):\n        if world_use_enabled(case):',
         '    if case.get("executable_world_revision"):\n'
         '        from executable_world_profile import section\n'
         '        return section(case)\n'
         '    if is_judgment(case):\n        if world_use_enabled(case):')])
    changes['scripts/libero/native_world_protocol.py'] = replace('scripts/libero/native_world_protocol.py', [
        ('    sources = "".join(Path(__file__).with_name(name).read_text() for name in',
         '    if case.get("executable_world_revision"):\n'
         '        from executable_world_profile import validate\n'
         '        validate(case)\n'
         '    sources = "".join(Path(__file__).with_name(name).read_text() for name in'),
        ('        "world_interface": case["condition"] in WORLD_CONDITIONS,',
         '        **({"executable_world_revision": case["executable_world_revision"], "c_arm": case["c_arm"]}\n'
         '           if case.get("executable_world_revision") else {}),\n'
         '        "world_interface": case["condition"] in WORLD_CONDITIONS,'),
        ('            from aspire.sim.cap.world_model.judgment_world import module_errors',
         '            if case.get("executable_world_revision"):\n'
         '                from aspire.sim.cap.world_model.executable_world import module_errors\n'
         '            else:\n'
         '                from aspire.sim.cap.world_model.judgment_world import module_errors'),
        ('    if is_judgment(case):\n        return write_in_process_config(case, directory, sources, JUDGMENT_MODE,',
         '    if case.get("executable_world_revision"):\n'
         '        from aspire.sim.cap.world_model.executable_world import MODE\n'
         '        path = write_in_process_config(case, directory, sources, MODE, JUDGMENT_PROFILE, "executable_world_config.json")\n'
         '        config = json.loads(path.read_text())\n'
         '        config["c_arm"] = case["c_arm"]\n'
         '        path.write_text(json.dumps(config, indent=2) + "\\n")\n'
         '        return path\n'
         '    if is_judgment(case):\n        return write_in_process_config(case, directory, sources, JUDGMENT_MODE,'),
        ('    record = state.begin_trial(phase, seed, bundle, sources)',
         '    if case.get("executable_world_revision") and phase not in NON_TRIAL_PHASES:\n'
         '        from executable_world_profile import checks\n'
         '        screening = checks(case, repo, state, sources, runtime_env(case, repo))\n'
         '        if screening["status"] == "rejected":\n'
         '            reason = "offline candidate error; inspect " + screening["directory"]\n'
         '            state.reject(phase, seed, reason, sources)\n'
         '            raise ProtocolError(reason)\n'
         '    record = state.begin_trial(phase, seed, bundle, sources)')])
    (HERE / 'coordination/integration.json').write_text(json.dumps(changes, indent=2) + '\n')
    print(json.dumps(changes, indent=2))


if __name__ == '__main__':
    main()
