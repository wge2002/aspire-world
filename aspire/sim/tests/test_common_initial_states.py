"""CPU tests for the common initial-state statepack (libero-pro-common-env-v3).

No LIBERO/MuJoCo, policy, oracle or model is used: the generator runs over stub
task files and a stub env (statepack_fixture.py); the loader, protocol binding
and guard are the real code.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts/libero"))
sys.path.insert(0, str(REPO / "scripts/common"))
import statepack_fixture as fx  # noqa: E402
import streaming_fixloop_guard as guard  # noqa: E402
import streaming_fixloop_protocol as sp  # noqa: E402
from aspire.sim.cap.integrations.libero import LiberoHandle  # noqa: E402
from aspire.sim.cap.integrations.libero import initial_states as ist  # noqa: E402

gen = fx.gen
BOWL = "put_the_bowl_on_the_plate"
STOVE = "turn_on_the_stove"


@pytest.fixture
def pack(tmp_path):
    log = []
    manifest = fx.generate(tmp_path / "suite", tmp_path / "assets/statepack", log=log)
    path = tmp_path / "assets/statepack" / gen.MANIFEST
    return {"path": path, "sha": ist.file_sha256(path), "manifest": manifest, "log": log,
            "suite": fx.task_files(tmp_path / "suite"), "tmp": tmp_path}


def registry(pack, task=BOWL, suite="libero_goal_swap", sha=None):
    return ist.InitialStateRegistry(pack["path"], sha or pack["sha"], suite, task)


def rebind(pack, edit):
    data = json.loads(pack["path"].read_text())
    edit(data)
    pack["path"].write_text(json.dumps(data))
    return ist.file_sha256(pack["path"])


# --- generator -------------------------------------------------------------------

def test_generator_adds_15_dev_states_per_task_and_keeps_the_50_test_files(pack):
    m = pack["manifest"]
    assert m["status"] == "complete" and m["environment_version"] == ist.ENVIRONMENT_VERSION
    assert m["partitions"] == {"test": list(range(1, 51)), "development": list(range(51, 66))}
    assert sorted(m["tasks"]) == sorted(gen.TASKS)
    for name, entry in m["tasks"].items():
        test = entry["test"]
        assert test["count"] == 50 and test["seed_to_slot"]["50"] == 49
        assert ist.file_sha256(test["path"]) == test["sha256"]  # byte-identical original
        states = entry["development"]["states"]
        assert sorted(map(int, states)) == list(range(51, 66))
        for seed, r in states.items():
            assert r["status"] == "complete" and r["slot"] == int(seed) - 51 and r["finite"]
            assert r["path"] == f"development/{name}/seed_{seed}.npy"
            assert r["sampling_seed"] == gen.sampling_seed(name, int(seed))
            assert r["array_sha256"] not in test["state_sha256"]
    # Outer-only: the env saw seed/reset/get_sim_state/close and nothing else.
    assert {c[0] for c in pack["log"]} == {"seed", "reset", "get_sim_state", "close"}
    assert sum(c[0] == "reset" for c in pack["log"]) == 75
    assert m["outer_only"] == {"policy_runs": 0, "env_steps": 0, "oracle_queries": 0,
                               "heldout_trajectories": 0, "state_contents_provided_to_solver": False}
    assert m["identity"]["sampling"]["settling_steps_before_capture"] == 0
    assert m["identity"]["sampling"]["env_args"]["controller"] == "JOINT_POSITION"


def test_sampling_env_exposes_no_step_or_success_check():
    env = gen.SamplingOnlyEnv(fx.StubEnv("x", []))
    for name in ("step", "check_success", "set_init_state", "env", "sim"):
        with pytest.raises(gen.GenerationError):
            getattr(env, name)


def test_output_root_must_be_fresh_and_outside_the_source(pack, tmp_path):
    with pytest.raises(gen.GenerationError, match="fresh"):
        fx.generate(tmp_path / "suite", pack["path"].parent)
    with pytest.raises(gen.GenerationError, match="outside the source"):
        fx.generate(tmp_path / "suite", REPO / "statepack-should-not-exist")
    assert not (REPO / "statepack-should-not-exist").exists()


class CrashAfter(fx.StubEnv):
    budget = 0

    def get_sim_state(self):
        if CrashAfter.budget <= 0:
            raise RuntimeError("simulated crash")
        CrashAfter.budget -= 1
        return super().get_sim_state()


def test_resume_keeps_recorded_states_marks_incomplete_and_requires_same_identity(tmp_path):
    out = tmp_path / "statepack"
    CrashAfter.budget = 20  # 15 bowl states, 5 of the next task
    with pytest.raises(RuntimeError, match="simulated crash"):
        fx.generate(tmp_path / "suite", out, env_class=CrashAfter)
    m = json.loads((out / gen.MANIFEST).read_text())
    second = m["tasks"][gen.TASKS[1]]["development"]["states"]
    assert m["status"] == "incomplete" and second["56"]["status"] == "sampling"
    assert sum(r["status"] == "complete" for r in second.values()) == 5
    kept = {p: (p.stat().st_mtime_ns, ist.file_sha256(p)) for p in out.rglob("*.npy")}
    assert len(kept) == 20
    # Drifted identity (an original test file changed) is never resumed.
    test_file = Path(m["tasks"][BOWL]["test"]["path"])
    original = test_file.read_bytes()
    test_file.write_bytes(original + b"\0")
    with pytest.raises(gen.GenerationError, match="identity differs"):
        fx.generate(tmp_path / "suite", out, resume=True)
    test_file.write_bytes(original)
    log = []
    m = fx.generate(tmp_path / "suite", out, resume=True, log=log)
    assert m["status"] == "complete"
    assert sum(c[0] == "reset" for c in log) == 75 - 20  # recorded states not regenerated
    assert all((p.stat().st_mtime_ns, ist.file_sha256(p)) == v for p, v in kept.items())
    # A resumed pack equals an uninterrupted one: sampling is seeded per state.
    clean = fx.generate(tmp_path / "suite", tmp_path / "clean")
    assert ({n: {s: r["array_sha256"] for s, r in t["development"]["states"].items()}
             for n, t in m["tasks"].items()}
            == {n: {s: r["array_sha256"] for s, r in t["development"]["states"].items()}
                for n, t in clean["tasks"].items()})


def test_resume_refuses_an_unrecorded_state_file(tmp_path):
    out = tmp_path / "statepack"
    CrashAfter.budget = 3
    with pytest.raises(RuntimeError):
        fx.generate(tmp_path / "suite", out, tasks=(BOWL,), env_class=CrashAfter)
    orphan = out / "development" / BOWL / "seed_54.npy"
    np.save(orphan, np.zeros(fx.DIM))
    with pytest.raises(gen.GenerationError, match="unrecorded"):
        fx.generate(tmp_path / "suite", out, tasks=(BOWL,), resume=True)


class ReturnsTestState(fx.StubEnv):
    def get_sim_state(self):
        if self.calls.count(("reset",)) == 3:  # seed 53 lands on an original test state
            return np.asarray(fx.torch.load(self.definition_path.replace(
                "bddl_files", "init_files").replace(".bddl", ".pruned_init"),
                weights_only=False))[7]
        return super().get_sim_state()


def test_duplicate_state_is_recorded_and_fails_without_substituting_a_seed(tmp_path):
    out = tmp_path / "statepack"
    with pytest.raises(gen.GenerationError, match="duplicates test seed 8"):
        fx.generate(tmp_path / "suite", out, tasks=(BOWL,), env_class=ReturnsTestState)
    states = json.loads((out / gen.MANIFEST).read_text())["tasks"][BOWL]["development"]["states"]
    assert states["53"]["status"] == "duplicate" and states["53"]["duplicate_of"] == "test seed 8"
    assert sorted(states, key=int) == ["51", "52", "53"]  # nothing sampled in its place
    assert not (out / "development" / BOWL / "seed_53.npy").exists()
    with pytest.raises(gen.GenerationError, match="recorded duplicate"):
        fx.generate(tmp_path / "suite", out, tasks=(BOWL,), resume=True)


def test_asset_preflight_mismatch_is_refused(tmp_path):
    resolved = fx.task_files(tmp_path / "suite", (BOWL,))
    row = {"task": BOWL, "suite": "libero_goal_swap", "task_index": 8, "initial_states": 50,
           "definition_sha256": ist.file_sha256(resolved[BOWL]["definition_path"]),
           "initial_state_sha256": ist.file_sha256(resolved[BOWL]["test_path"])}
    with pytest.raises(gen.GenerationError, match="task_index"):
        fx.generate(tmp_path / "suite", tmp_path / "out", tasks=(BOWL,), expect={"tasks": [row]})
    row["task_index"] = 0
    assert fx.generate(tmp_path / "suite", tmp_path / "out2", tasks=(BOWL,),
                       expect={"tasks": [row]})["status"] == "complete"


# --- trusted loader --------------------------------------------------------------

def test_test_seeds_read_original_slots_and_dev_seeds_read_new_states(pack):
    reg = registry(pack)
    originals = ist.load_test_states(pack["suite"][BOWL]["test_path"])
    for seed in (1, 2, 50):
        state, info = reg.state_for(seed)
        assert np.array_equal(state, originals[seed - 1])
        assert info["init_state_partition"] == "test" and info["init_state_slot"] == seed - 1
    for seed in range(51, 66):
        state, info = reg.state_for(seed)
        assert info == {"init_state_partition": "development", "init_state_slot": seed - 51,
                        "init_state_sha256": ist.array_sha256(state),
                        "environment_version": ist.ENVIRONMENT_VERSION}
        # No modulo: development seed 51+k never reuses test slot k (or any test slot).
        assert not any(np.array_equal(state, o) for o in originals)


@pytest.mark.parametrize("seed", [0, 66, 101, -1, None, "51", True, 51.0])
def test_seeds_outside_the_protocol_are_refused(pack, seed):
    with pytest.raises(ist.InitialStateError):
        registry(pack).state_for(seed)


def test_tampering_and_mismatched_bindings_are_refused(pack):
    with pytest.raises(ist.InitialStateError, match="bound sha256"):
        registry(pack, sha="0" * 64).state_for(51)
    with pytest.raises(ist.InitialStateError, match="sha256"):
        ist.InitialStateRegistry(pack["path"], "", "libero_goal_swap", BOWL)
    with pytest.raises(ist.InitialStateError, match="no libero_goal_swap/nonexistent"):
        registry(pack, task="nonexistent").state_for(51)
    with pytest.raises(ist.InitialStateError, match="suite"):
        registry(pack, suite="libero_goal").state_for(51)
    # Tasks are not interchangeable: the stove registry loads the stove's own states.
    assert not np.array_equal(registry(pack, STOVE).state_for(51)[0], registry(pack).state_for(51)[0])
    dev = pack["path"].parent / "development" / BOWL / "seed_57.npy"
    np.save(dev, np.zeros(fx.DIM))
    with pytest.raises(ist.InitialStateError, match="seed 57 was modified"):
        registry(pack).state_for(57)
    registry(pack).state_for(58)  # other slots still load
    dev.unlink()
    with pytest.raises(ist.InitialStateError, match="missing"):
        registry(pack).state_for(57)
    test_file = Path(pack["suite"][BOWL]["test_path"])
    test_file.write_bytes(test_file.read_bytes() + b"\0")
    with pytest.raises(ist.InitialStateError, match="original test init file"):
        registry(pack).state_for(3)


@pytest.mark.parametrize("edit,match", [
    (lambda d: d["tasks"][BOWL]["development"]["states"].pop("65"), "exactly seeds 51-65"),
    (lambda d: d.update(partitions={"test": list(range(1, 66)), "development": []}), "partitions"),
    (lambda d: d.update(seed_mapping="(seed - 1) % 50"), "seed_mapping"),
    (lambda d: d.update(status="incomplete"), "status"),
    (lambda d: d.update(environment_version="libero-reset-v2-2026-10-10"), "environment_version"),
    (lambda d: d["tasks"][BOWL]["development"]["states"]["51"].update(slot=1), "exact slot"),
    (lambda d: d["tasks"][BOWL]["development"]["states"]["52"].update(
        array_sha256=d["tasks"][BOWL]["test"]["state_sha256"][1]), "duplicate"),
    (lambda d: d["tasks"][BOWL]["development"]["states"]["51"].update(
        path="../../suite/init_files/x.npy"), "inside the statepack"),
])
def test_rebound_but_malformed_manifests_are_refused(pack, edit, match):
    sha = rebind(pack, edit)
    with pytest.raises(ist.InitialStateError, match=match):
        registry(pack, sha=sha).state_for(51)


def test_environment_boundary_requires_manifest_when_formal(pack):
    env = {ist.MANIFEST_ENV: str(pack["path"]), ist.MANIFEST_SHA_ENV: pack["sha"],
           ist.REQUIRED_ENV: "1"}
    suite = pack["suite"][BOWL]
    reg = ist.InitialStateRegistry.from_environment(
        "libero_goal_swap", BOWL, definition_path=suite["definition_path"],
        test_states_path=suite["test_path"], environ=env)
    assert reg.state_for(60)[1]["init_state_slot"] == 9
    with pytest.raises(ist.InitialStateError, match="never falls back"):
        ist.InitialStateRegistry.from_environment("libero_goal_swap", BOWL,
                                                  environ={ist.REQUIRED_ENV: "1"})
    assert ist.InitialStateRegistry.from_environment("libero_goal_swap", BOWL, environ={}) is None
    with pytest.raises(ist.InitialStateError, match="bound by its sha256"):
        ist.InitialStateRegistry.from_environment(
            "libero_goal_swap", BOWL, environ={ist.MANIFEST_ENV: str(pack["path"])})
    with pytest.raises(ist.InitialStateError, match="not the registered"):
        ist.InitialStateRegistry.from_environment(
            "libero_goal_swap", BOWL, definition_path=pack["suite"][STOVE]["definition_path"],
            environ=env)


class StubLiberoEnv:
    def __init__(self):
        self.calls = []

    def seed(self, seed):
        self.calls.append(("seed", seed))

    def reset(self):
        self.calls.append(("reset",))
        return {"sampled": True}

    def set_init_state(self, state):
        self.calls.append(("set_init_state", ist.array_sha256(np.asarray(state))))
        return {"restored": ist.array_sha256(np.asarray(state))}


def test_handle_with_registry_resets_once_and_loads_the_exact_slot(pack):
    reg = registry(pack)
    h = LiberoHandle(env=StubLiberoEnv(), suite_name="libero_goal_swap", task_id=8,
                     task_language="put the bowl on the plate", init_states=list(range(50)),
                     initial_state_registry=reg)
    obs, info = h.reset(seed=51)
    digest = pack["manifest"]["tasks"][BOWL]["development"]["states"]["51"]["array_sha256"]
    assert h.env.calls == [("seed", 51), ("reset",), ("set_init_state", digest)]
    assert obs == {"restored": digest} and info["init_state_partition"] == "development"
    h.env.calls.clear()
    h.reset(seed=1)
    assert h.env.calls[-1] == ("set_init_state", pack["manifest"]["tasks"][BOWL]["test"]["state_sha256"][0])
    h.env.calls.clear()
    with pytest.raises(ist.InitialStateError):
        h.reset(seed=66)
    assert h.env.calls == []  # refused before the env is touched
    obs, info = h.reset(seed=None)  # FrankaLiberoEnv construction reset: no protocol state
    assert h.env.calls == [("seed", None), ("reset",)] and info == {"init_state_partition": None}


# --- protocol, campaign and guard binding ----------------------------------------

def formal_case(pack, tmp_path):
    return {"suite": "libero_goal_swap", "task": BOWL, "control": str(tmp_path / "campaigns/c1"),
            "initial_state_manifest": str(pack["path"]),
            "initial_state_manifest_sha256": pack["sha"]}


def test_protocol_identity_and_trial_env_bind_the_manifest_hash(pack, tmp_path, monkeypatch):
    case = formal_case(pack, tmp_path)
    ident = sp.initial_state_identity(case)
    assert ident["evidence"] == "frozen" and ident["manifest_sha256"] == pack["sha"]
    assert ident["seed_mapping"] == ist.SEED_MAPPING and sorted(ident["development_sha256"]) == \
        sorted(str(s) for s in range(51, 66))
    with pytest.raises(sp.RuntimeChanged, match="initial-state registry"):
        sp.initial_state_identity({**case, "initial_state_manifest_sha256": "f" * 64})
    with pytest.raises(sp.RuntimeChanged, match="no libero_goal_swap/turn_on"):
        sp.initial_state_identity({**case, "task": "turn_on_the_stove_y"})
    assert sp.initial_state_identity({"suite": "s", "task": "t"})["evidence"] == "cpu-test-fake"
    monkeypatch.setattr(sp, "runtime_env", lambda c, r: {"PYTHONPATH": "", ist.MANIFEST_ENV: "/stale"})
    trial = {**case, "streaming": {"sam2": {"source_root": str(tmp_path)}}, "max_steps": 4000}
    env = sp.trial_env(trial, REPO, tmp_path)
    assert env[ist.MANIFEST_ENV] == str(pack["path"].resolve())
    assert env[ist.MANIFEST_SHA_ENV] == pack["sha"] and env[ist.REQUIRED_ENV] == "1"
    fake = sp.trial_env({k: v for k, v in trial.items() if not k.startswith("initial_state")},
                        REPO, tmp_path)
    assert ist.MANIFEST_ENV not in fake and ist.REQUIRED_ENV not in fake
    for loader in ("cap/integrations/libero/initial_states.py", "cap/integrations/libero/__init__.py"):
        assert loader in sp.ENGINEERED_WORLD_FILES


def test_formal_case_requires_the_initial_state_manifest():
    import streaming_fixloop_campaign as camp
    with pytest.raises(sp.ProtocolError, match="initial_state_manifest_sha256"):
        sp.validate_case({"dependency_evidence": "frozen"})
    assert camp.ENVIRONMENT_VERSION == ist.ENVIRONMENT_VERSION
    assert camp.SEED_MAPPING == ist.SEED_MAPPING and "%" not in camp.SEED_MAPPING


@pytest.mark.parametrize("source", [
    "s = env.get_sim_state()\n", "env.handle.env.set_init_state(x)\n", "x = init_states[3]\n",
    "import numpy as np\nx = np.load(p)\n", "import torch\nx = torch.load(p)\n",
    "import pickle\nx = pickle.loads(b)\n", "p = 'seed_51.npy'\n", "p = \"pack.npz\"\n",
    "p = 'put_the_bowl_on_the_plate.pruned_init'\n", "p = 'scene.bddl'\n", "p = 'robot.urdf'\n",
    "p = 'model.xml'\n", "import os\np = os.environ['ASPIRE_LIBERO_INITIAL_STATE_MANIFEST']\n",
    "from libero.libero import get_libero_path\n", "x.set_state(s)\n",
])
def test_authored_code_may_not_read_states_or_raw_assets(source):
    with pytest.raises(sp.ProtocolError, match="simulator state or assets|ground-truth"):
        sp.check_authored(source)


def test_public_robot_api_code_is_still_accepted():
    sp.check_authored(
        "obs = get_observation()\nmask = segment_sam3_text_prompt(obs['rgb'], 'bowl')\n"
        "pts = mask_to_world_points(mask, obs['depth'], obs['intrinsics'], obs['extrinsics'])\n"
        "q = solve_ik(np.mean(pts, axis=0), quat)\nmove_to_joints(q)\nclose_gripper()\n"
        "initial_obs = obs\nnp.save_note = None\nprint(env.handle.task_language)\n")
    sp.check_authored(json.dumps({"objects": [{"identity": "bowl", "prompt": "white bowl"}],
                                  "cadence": {}, "notes": "seed the bowl first"}))


def test_guard_denies_the_statepack_and_generator(pack, tmp_path):
    case = formal_case(pack, tmp_path)
    (tmp_path / "campaigns/c1/cell").mkdir(parents=True)
    root = pack["path"].parent

    def read(path, tool="Read", **extra):
        return guard.denial({"tool_name": tool, "tool_input": {"file_path": str(path), **extra},
                             "cwd": str(tmp_path / "campaigns/c1/cell")}, case, REPO)

    def bash(command):
        return guard.denial({"tool_name": "Bash", "tool_input": {"command": command}}, case, REPO)

    assert read(pack["path"]) and read(root / f"development/{BOWL}/seed_51.npy")
    assert guard.denial({"tool_name": "Grep", "tool_input": {"pattern": "x", "path": str(tmp_path)},
                         "cwd": str(tmp_path)}, case, REPO)
    assert bash(f"cat {root}/initial_state_manifest.json")
    assert bash(f"python3 -c 'import numpy; numpy.load(\"{root}/development/x.npy\")'")
    assert bash("ls /mnt/runtime-assets/common-initial-states/")
    assert bash("python scripts/libero/prepare_common_initial_states.py --output-root /tmp/x")
    # Public protocol docs and the cell's own notes stay readable.
    assert read(REPO / "docs/libero-pro-common-protocol.md") is None
    assert read(tmp_path / "campaigns/c1/cell/notes.md") is None


# --- SAM2 runtime binding and generator CLI ---------------------------------------

def sam2_case(root, evidence):
    (root / "sam2/csrc").mkdir(parents=True, exist_ok=True)
    (root / "sam2/build_sam.py").write_text("def build_sam2_camera_predictor(): pass\n")
    (root / "sam2/configs.yaml").write_text("a: 1\n")
    (root / "sam2/csrc/connected_components.cu").write_text("// kernel\n")
    (root / "sam2/csrc/cc.h").write_text("// header\n")
    ckpt = root / "ckpt.pt"
    ckpt.write_bytes(b"weights")
    return {"dependency_evidence": evidence, "streaming": {"sam2": {
        "source_root": str(root), "config_file": "c.yaml", "checkpoint": str(ckpt),
        "device": "cuda"}}}


def test_sam2_identity_binds_extension_sources_and_compiled_binary(tmp_path):
    fake = sam2_case(tmp_path, "cpu-test-fake")
    ident = sp.sam2_identity(fake)
    assert ident["compiled_extensions"] == "absent (cpu-test-fake fixture)"
    with pytest.raises(sp.RuntimeChanged, match="compiled sam2/_C"):
        sp.sam2_identity({**fake, "dependency_evidence": "frozen"})
    (tmp_path / "sam2/_C.so").write_bytes(b"\x7fELF built")
    formal = sp.sam2_identity({**fake, "dependency_evidence": "frozen"})
    assert list(formal["compiled_extensions"]) == ["sam2/_C.so"]
    before = formal["source_sha256"]
    (tmp_path / "sam2/_C.so").write_bytes(b"\x7fELF rebuilt")
    assert sp.tree_sha256(tmp_path) != before
    after_so = sp.tree_sha256(tmp_path)
    (tmp_path / "sam2/csrc/connected_components.cu").write_text("// changed kernel\n")
    assert sp.tree_sha256(tmp_path) != after_so
    after_cu = sp.tree_sha256(tmp_path)
    (tmp_path / "sam2/csrc/cc.h").write_text("// changed header\n")
    assert sp.tree_sha256(tmp_path) != after_cu


def cli(*argv):
    import subprocess
    return subprocess.run([sys.executable, str(REPO / "scripts/libero/prepare_common_initial_states.py"),
                           *map(str, argv)], capture_output=True, text=True, timeout=120)


def test_generator_cli_refuses_bad_output_roots_before_touching_libero(tmp_path):
    assert cli("--help").returncode == 0
    assert cli().returncode == 2  # --output-root is required
    inside = cli("--output-root", REPO / "statepack-should-not-exist")
    assert inside.returncode == 3 and "outside the source" in inside.stderr
    assert not (REPO / "statepack-should-not-exist").exists()
    used = tmp_path / "used"
    used.mkdir()
    (used / "x").write_text("x")
    busy = cli("--output-root", used)
    assert busy.returncode == 3 and "fresh" in busy.stderr
    missing = cli("--output-root", tmp_path / "none", "--resume")
    assert missing.returncode == 3 and "nothing to resume" in missing.stderr
    assert not (tmp_path / "none").exists()
