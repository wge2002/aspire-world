"""DSW replay harness for the method diagnosis (CC_TASK_13).  Nothing here is a pipeline change.

  replay_harness.py build --root R --out plan.json --job CELLDIR:VERSIONSPEC:SEEDS ...
      VERSIONSPEC = frozen | all | v0,v2,... (version index = order swept in phase B, v0 = the
      phase-A bundle).  Copies nothing; resolves each bundle to an executed development trial
      directory and checks its policy/world digests against the ledger.
  replay_harness.py run --plan plan.json --root R --sam3-card 2 --graspnet-card 5 \
      --sim-cards 2,5 --jobs 2 --parallel 4 [--ports 8714,8715,8716]
      Copies each source cell runtime once into R/runtime/<cell> (outputs = empty real dir),
      starts SAM3 / GraspNet / PyRoKi from that copy, runs job_runner.py per job (up to --jobs
      at once, each at --parallel seeds), stops every service session.  Result roots are read only.
"""
import argparse, json, os, shutil, signal, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).resolve().parent
PY = "/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3"
GRASPNET_ROOT = "/mnt/home/gewang/code/ASPIRE/aspire/sim/cap/third_party/contact_graspnet_pytorch"
GRASP_CKPT = GRASPNET_ROOT + "/checkpoints/contact_graspnet/checkpoints"
SAM3_CKPT = "/mnt/home/gewang/.cache/aspire/sam3/sam3.pt"
RESULT_ROOTS = ("/mnt/home/gewang/experiments/code-world-budget-p5", "/mnt/home/gewang/experiments/code-world-repair",
                "/mnt/home/gewang/experiments/code-world-worker-recovery")


def log(root, event, **kw):
    line = json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **kw}, default=str)
    print(line, flush=True)
    with open(Path(root) / "events.jsonl", "a") as f:
        f.write(line + "\n")


def task_dir(cell):
    for p in sorted(Path(cell).glob("outputs/libero_fix_loop/*/*")):
        if (p / "development_state.json").exists():
            return p
    raise SystemExit(f"no development_state under {cell}")


def versions(cell):
    td = task_dir(cell)
    ds = json.loads((td / "development_state.json").read_text())
    order, info = [], {}
    for t in ds["trials"]:
        b = t.get("bundle_sha256")
        if not b or t.get("phase") in ("snapshot", "probe", "diagnostic"):
            continue
        if b not in info:
            info[b] = {"bundle_sha256": b, "bundle": t.get("bundle"), "dirs": [], "phases": set()}
        info[b]["phases"].add(t["phase"])
        if t.get("executed") and (td / t["directory"] / "code.py").exists() and (td / t["directory"] / "world_program.py").exists():
            info[b]["dirs"].append(str(td / t["directory"]))
        if t["phase"] == "sweep" and b not in order:
            order.append(b)
    sel = (ds.get("selected") or {}).get("bundle_sha256")
    return td, [info[b] for b in order], sel


def build(a):
    sys.dont_write_bytecode = True
    jobs = []
    for spec in a.job:
        cell, vs, seeds = spec.rsplit(":", 2)
        rep = 1
        if "#" in seeds:
            seeds, rep = seeds.split("#")
            rep = int(rep)
        td, vers, sel = versions(cell)
        case = json.loads((Path(cell) / "case.json").read_text())
        sys.path.insert(0, case["sim"] + "/scripts/libero")
        from native_world_fixloop_state import code_hash  # noqa
        if vs == "frozen":
            pick = [(i, v) for i, v in enumerate(vers) if v["bundle_sha256"] == sel]
        elif vs == "all":
            pick = list(enumerate(vers))
        else:
            want = {int(x.lstrip("v")) for x in vs.split(",")}
            pick = [(i, v) for i, v in enumerate(vers) if i in want]
        for i, v in pick:
            d = Path(v["dirs"][0])
            pol, wor = d / "code.py", d / "world_program.py"
            ok = (code_hash(pol.read_text()) == v["bundle"]["policy"] and code_hash(wor.read_text()) == v["bundle"]["world"])
            if not ok:
                raise SystemExit(f"digest mismatch for {cell} v{i} in {d}")
            name = Path(cell).name
            tag = f"{name}__v{i}{'F' if v['bundle_sha256'] == sel else ''}__{seeds}__r{rep}"
            jobs.append({"id": tag, "cell_dir": cell, "cell": name, "version": i, "frozen": v["bundle_sha256"] == sel,
                         "bundle_sha256": v["bundle_sha256"], "policy": str(pol), "world": str(wor), "seeds": seeds, "rep": rep,
                         "policy_sha256": v["bundle"]["policy"], "world_sha256": v["bundle"]["world"],
                         "runtime": str(Path(case["python_root"]))})
    Path(a.out).write_text(json.dumps(jobs, indent=1) + "\n")
    for j in jobs:
        print(j["id"], j["bundle_sha256"][:8])


def copy_runtime(root, src):
    dst = Path(root) / "runtime" / Path(src).name
    if dst.exists():
        return dst
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    out = tmp / "aspire/sim/outputs"
    if out.is_symlink():
        out.unlink()
        out.mkdir()
    tmp.rename(dst)
    return dst


def stage(a):
    """build, then copy every runtime and bundle into the new root so the job reads no result root code."""
    sys.dont_write_bytecode = True
    root = Path(a.root)
    for r in RESULT_ROOTS:
        assert not str(root).startswith(r), "refusing to write under a result root"
    root.mkdir(parents=True, exist_ok=True)
    a.out = str(root / "plan.source.json")
    build(a)
    jobs = json.loads(Path(a.out).read_text())
    staged = []
    for j in jobs:
        rt = copy_runtime(root, j["runtime"])
        bdir = root / "bundles" / f"{j['cell']}__v{j['version']}"
        bdir.mkdir(parents=True, exist_ok=True)
        for key, name in (("policy", "fix_code.py"), ("world", "fix_world_program.py")):
            dst = bdir / name
            if not dst.exists():
                dst.write_bytes(Path(j[key]).read_bytes())
            j[key + "_source"] = j[key]
            j[key] = str(dst)
        j["runtime_source"] = j["runtime"]
        j["runtime"] = str(rt)
        staged.append(j)
    (root / "plan.json").write_text(json.dumps(staged, indent=1) + "\n")
    n = sum(int(j["seeds"].split("-")[1]) - int(j["seeds"].split("-")[0]) + 1 for j in staged)
    print("staged", len(staged), "jobs,", n, "trials")


def check(a):
    """--preflight: start nothing on a GPU; every staged digest, runtime freeze and launcher resolves."""
    sys.dont_write_bytecode = True
    root = Path(a.root)
    jobs = json.loads(Path(a.plan).read_text())
    problems = []
    for j in jobs:
        sys.path[:0] = [j["runtime"] + "/aspire/sim/scripts/libero", j["runtime"] + "/aspire/sim/scripts/common"]
        from native_world_fixloop_state import code_hash
        from native_cc_freeze import verify_runtime
        for key in ("policy", "world"):
            if code_hash(Path(j[key]).read_text()) != j[key + "_sha256"]:
                problems.append(f"{j['id']}: {key} digest")
        case = json.loads((Path(j["cell_dir"]) / "case.json").read_text())
        case.update(sim=j["runtime"] + "/aspire/sim", python_root=j["runtime"])
        try:
            verify_runtime(case, Path(case["sim"]))
        except Exception as e:
            problems.append(f"{j['id']}: runtime {e}")
        for n in ("sam3", "contact_graspnet", "pyroki"):
            if not (Path(case["sim"]) / f"cap/serving/launch_{n}_server.py").is_file():
                problems.append(f"{j['id']}: launcher {n}")
        del sys.path[:2]
    for f in (PY, SAM3_CKPT, GRASP_CKPT):
        if not Path(f).exists():
            problems.append(f"missing {f}")
    print(json.dumps({"jobs": len(jobs), "trials": sum(int(j["seeds"].split("-")[1]) - int(j["seeds"].split("-")[0]) + 1 for j in jobs),
                      "problems": problems}, indent=1))
    return 1 if problems else 0


def job_case(root, job, rt, card, ports, parallel):
    case = json.loads((Path(job["cell_dir"]) / "case.json").read_text())
    control = Path(root) / "jobs" / job["id"]
    control.mkdir(parents=True, exist_ok=True)
    url = lambda p: f"http://127.0.0.1:{p}"
    case.update(source_case=str(Path(job["cell_dir"]) / "case.json"), sim=str(rt / "aspire/sim"),
                python_root=str(rt), control=str(control), gpu=card, cuda_visible_devices=str(card), egl_device_id=card,
                service_ports=list(ports), sam3_url=url(ports[0]), graspnet_url=url(ports[1]), pyroki_url=url(ports[2]),
                heldout_parallel=parallel)
    for k in ("claude_config_dir",):
        case.pop(k, None)
    p = control / "case.json"
    p.write_text(json.dumps(case, indent=2) + "\n")
    return p, case


def env_for(case):
    sys.path.insert(0, case["sim"] + "/scripts/libero")
    from native_world_protocol import runtime_env
    keep = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TERM", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES")
    base = {k: os.environ[k] for k in keep if k in os.environ}
    base["PYTHONDONTWRITEBYTECODE"] = "1"
    env = runtime_env(case, Path(case["sim"]), base)
    env.update(HF_HUB_OFFLINE="1", SAM3_CHECKPOINT_PATH=SAM3_CKPT, CONTACT_GRASPNET_ROOT=GRASPNET_ROOT,
               CONTACT_GRASPNET_CHECKPOINT_DIR=GRASP_CKPT, PYTHONDONTWRITEBYTECODE="1")
    return env


def wait_health(urls, timeout=2400):
    t0 = time.time()
    pending = set(urls)
    while pending and time.time() - t0 < timeout:
        for u in list(pending):
            try:
                urllib.request.urlopen(u + "/health", timeout=5)
                pending.discard(u)
            except urllib.error.HTTPError as e:
                if e.code < 500:
                    pending.discard(u)
            except Exception:
                pass
        time.sleep(5)
    return not pending


def run(a):
    sys.dont_write_bytecode = True
    root = Path(a.root)
    for r in RESULT_ROOTS:
        assert not str(root).startswith(r), "refusing to write under a result root"
    root.mkdir(parents=True, exist_ok=True)
    jobs = json.loads(Path(a.plan).read_text())
    ports = [int(x) for x in a.ports.split(",")]
    sims = [int(x) for x in a.sim_cards.split(",")]
    rts = {j["runtime"]: copy_runtime(root, j["runtime"]) for j in jobs}
    first = jobs[0]
    p, case0 = job_case(root, {**first, "id": "_services"}, rts[first["runtime"]], a.sam3_card, ports, a.parallel)
    base = env_for(case0)
    procs = []
    (root / "logs").mkdir(exist_ok=True)
    for name, port, card, extra in (("sam3", ports[0], a.sam3_card, ["--device", "cuda"]),
                                    ("contact_graspnet", ports[1], a.graspnet_card, []),
                                    ("pyroki", ports[2], a.sam3_card, [])):
        env = base | {"CUDA_VISIBLE_DEVICES": str(card), "MUJOCO_EGL_DEVICE_ID": str(card)}
        out = open(root / "logs" / f"{name}-{int(time.time())}.log", "w")
        pr = subprocess.Popen([PY, "-u", f"cap/serving/launch_{name}_server.py", "--host", "127.0.0.1", "--port", str(port), *extra],
                              cwd=case0["sim"], env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                              start_new_session=True)
        procs.append(pr)
        log(root, "service_started", name=name, pid=pr.pid, card=card, port=port)

    def stop_all(*_):
        for pr in procs:
            for sig, grace in ((signal.SIGTERM, 30), (signal.SIGKILL, 10)):
                try:
                    os.killpg(pr.pid, sig)
                    pr.wait(timeout=grace)
                    break
                except (ProcessLookupError, PermissionError):
                    break
                except subprocess.TimeoutExpired:
                    continue
        log(root, "services_stopped", alive=[pr.pid for pr in procs if pr.poll() is None])

    signal.signal(signal.SIGTERM, lambda *_: (stop_all(), sys.exit(143)))
    try:
        ok = wait_health([f"http://127.0.0.1:{x}" for x in ports])
        log(root, "services_ready" if ok else "services_not_ready")
        if not ok:
            return 2
        todo = [j for j in jobs if not (root / "jobs" / j["id"] / "replay" / "replay_result.json").exists()]

        stop_at = time.time() + a.deadline_minutes * 60

        def work(idx_job):
            idx, j = idx_job
            if time.time() > stop_at:
                log(root, "job_skipped_deadline", job=j["id"])
                return None
            card = sims[idx % len(sims)]
            cp, case = job_case(root, j, rts[j["runtime"]], card, ports, a.parallel)
            env = env_for(case)
            folder = root / "jobs" / j["id"]
            log(root, "job_start", job=j["id"], card=card)
            t0 = time.time()
            with open(folder / "runner.stdout", "a") as o, open(folder / "runner.stderr", "a") as e:
                rc = subprocess.call(["timeout", "--signal=TERM", "--kill-after=60", str(max(60, int(stop_at + 2400 - time.time()))), PY, str(HERE / "job_runner.py"), "--case", str(cp), "--policy", j["policy"],
                                      "--world", j["world"], "--seeds", j["seeds"], "--label", f"v{j['version']}"],
                                     cwd=case["sim"], env=env, stdout=o, stderr=e)
            log(root, "job_end", job=j["id"], rc=rc, seconds=round(time.time() - t0))
            return rc

        with ThreadPoolExecutor(a.jobs) as ex:
            rcs = list(ex.map(work, enumerate(todo)))
        log(root, "all_jobs_done", rcs=rcs)
        return 0
    finally:
        stop_all()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build"); b.add_argument("--root"); b.add_argument("--out", required=True); b.add_argument("--job", action="append", required=True)
    r = sp.add_parser("run"); r.add_argument("--plan", required=True); r.add_argument("--root", required=True)
    r.add_argument("--sam3-card", type=int, required=True); r.add_argument("--graspnet-card", type=int, required=True)
    r.add_argument("--sim-cards", required=True); r.add_argument("--jobs", type=int, default=2); r.add_argument("--parallel", type=int, default=4)
    r.add_argument("--ports", default="8714,8715,8716"); r.add_argument("--deadline-minutes", type=float, default=1e9)
    st = sp.add_parser("stage"); st.add_argument("--root", required=True); st.add_argument("--job", action="append", required=True)
    pf = sp.add_parser("preflight"); pf.add_argument("--root", required=True); pf.add_argument("--plan", required=True)
    a = ap.parse_args()
    sys.exit({"build": build, "run": run, "stage": stage, "preflight": check}[a.cmd](a))
