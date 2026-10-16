"""No live cloud/GPU operations: actual multi-process claims and entry dispatch."""
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
import pytest

DOC = Path(__file__).resolve().parents[1] / "docs/experiments/code-world-runtime-p2-20261011"
sys.path.insert(0, str(DOC))
sys.path.insert(0, str(DOC.parents[2] / "scripts/common"))
import schedule_r9 as schedule


def module(name):
    spec = importlib.util.spec_from_file_location(name, DOC / name)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


supervisor = module("dlc-supervisor-r9.py")
submit = module("submit-runtime-study-r9.py")


def cells(root):
    (root / "coordination").mkdir()
    for name in schedule.CELL_ORDER:
        p = root / name; p.mkdir(); (p / "case.json").write_text("{}")


def claim_all(root, worker):
    found = []
    while True:
        p = supervisor.claim_next(Path(root), worker)
        if p is None: return found
        found.append(p.name)


def test_r1_breadth_first_order_and_sequential_claims(tmp_path):
    cells(tmp_path)
    assert [p.name for p in supervisor.queue_cells(tmp_path)] == schedule.CELL_ORDER
    assert claim_all(str(tmp_path), "worker-r9-node0") == schedule.CELL_ORDER


def test_concurrent_processes_never_reclaim(tmp_path):
    cells(tmp_path)
    with ProcessPoolExecutor(4, mp_context=multiprocessing.get_context("fork")) as pool:
        results = list(pool.map(claim_all, [str(tmp_path)] * 4, [f"worker-r9-node{i}" for i in range(4)]))
    flat = sum(results, [])
    assert len(flat) == 10 and len(set(flat)) == 10 and set(flat) == set(schedule.CELL_ORDER)
    assert supervisor.claim_next(tmp_path, "late") is None


def test_started_cell_is_never_claimed(tmp_path):
    cells(tmp_path); (tmp_path / schedule.CELL_ORDER[0] / "campaign_state.json").write_text("{}")
    assert schedule.CELL_ORDER[0] not in claim_all(str(tmp_path), "worker-r9-node0")


def test_three_independent_unique_jobs_with80h_and_explicit_global_ids():
    commands = [schedule.command("plan", i, 3, Path("/campaign")) for i in range(3)]
    assert len({c[c.index("--name") + 1] for c in commands}) == 3
    for i, c in enumerate(commands):
        assert c[c.index("--gpus") + 1] == "8"
        assert c[c.index("--max-minutes") + 1] == "4800"
        assert c[-4:] == ["--node-id", str(i), "--node-count", "3"]
        assert "launch-20261011-r9/dlc-single-node-entry.sh" in c[-5]


@pytest.mark.parametrize("node,nodes", [(0,0),(0,5),(3,3),(-1,3),(True,3)])
def test_invalid_global_node_identity(node, nodes):
    with pytest.raises(ValueError): schedule.node_identity(node, nodes)


def table(state, running="", job=schedule.OLD_JOB):
    return f"| name | {job} | user | {state} | create | submitted | {running} | finish |\n"


def test_old_job_requires_exact_unique_listing():
    assert schedule.job_state(table("Stopped")) == "Stopped"
    for text in ("Stopped", table("Stopped",job="other"),table("Stopped")*2):
        with pytest.raises(ValueError): schedule.job_state(text)


@pytest.mark.parametrize("marker", ["queue", "campaign_state.json", "queue-result.json"])
def test_no_started_work_blocks_unsafe_replacement(tmp_path, marker):
    cells(tmp_path)
    if marker == "queue": (tmp_path/marker).mkdir()
    else: (tmp_path/schedule.CELL_ORDER[0]/marker).write_text("{}")
    with pytest.raises(RuntimeError):schedule.no_started_work(tmp_path)


def test_stopped_proof_and_global_registration_are_exclusive(tmp_path):
    cells(tmp_path); launch=tmp_path/"launch";launch.mkdir();(launch/"launch-manifest.json").write_text("{}")
    p=tmp_path/"coordination/replacement-r9-old-job-stopped.json"
    proof={"old_job_id":schedule.OLD_JOB,"old_status":"Stopped","nodes":3,
           "no_old_queue_or_cell_started":True,"launch_manifest_sha256":schedule.sha(launch/"launch-manifest.json")}
    p.write_text(json.dumps(proof));schedule.register_worker(tmp_path,launch,0,3)
    with pytest.raises(FileExistsError):schedule.register_worker(tmp_path,launch,0,3)
    proof["old_status"]="Queuing";p.write_text(json.dumps(proof))
    with pytest.raises(RuntimeError):schedule.register_worker(tmp_path,launch,1,3)


@pytest.mark.parametrize("mutation", ["manifest","binding","oldjob","nodes","released"])
def test_exact_release_required(mutation):
    r={"released":True,"reviewer":"main","nodes":3,"old_job_id":schedule.OLD_JOB,
       "source_manifest_sha256":"m","gate_binding_sha256":"b"}
    submit.require_release(r,{"binding_sha256":"b"},"m")
    key={"manifest":"source_manifest_sha256","binding":"gate_binding_sha256","oldjob":"old_job_id","nodes":"nodes","released":"released"}[mutation]
    r[key]="wrong"
    with pytest.raises(RuntimeError):submit.require_release(r,{"binding_sha256":"b"},"m")


@pytest.mark.parametrize("state,running", [("Running","now"),("Stopped","earlier"),("EnvPreparing","")])
def test_old_late_start_refused_without_stop_call(tmp_path,state,running):
    cells(tmp_path); calls=[]
    def runner(argv,**kwargs):
        calls.append(argv);return subprocess.CompletedProcess(argv,0,table(state,running),"")
    with pytest.raises(RuntimeError):submit.stop_unstarted_old(tmp_path,3,"m",runner)
    assert len(calls)==1 and "stop" not in calls[0]


def test_stop_exactly_once_and_require_platform_confirmation(tmp_path):
    cells(tmp_path);calls=[];states=iter(["Queuing","Stopped"])
    def runner(argv,**kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv,0,"ok" if argv[1]=="stop" else table(next(states)),"")
    p=submit.stop_unstarted_old(tmp_path,3,"m",runner)
    assert p["old_status"]=="Stopped" and sum(c[1]=="stop" for c in calls)==1


def test_ambiguous_stop_is_not_reissued(tmp_path):
    cells(tmp_path);j=tmp_path/"coordination/replacement-r9-stop";j.mkdir();(j/"stop-attempt.json").write_text("{}")
    def runner(argv,**kwargs):return subprocess.CompletedProcess(argv,0,table("Queuing"),"")
    with pytest.raises(FileExistsError):submit.stop_unstarted_old(tmp_path,3,"m",runner)


def fake_entry(tmp_path):
    script=(DOC/"dlc-single-node-entry-r9.sh").read_text()
    script=script.replace('. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh',':')
    # These tests vary only test host paths, not the argument/rank validation.
    py=tmp_path/"fake-python";py.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nprintf "RANK=%s\\n" "${RANK-unset}"\n');py.chmod(0o755)
    script=script.replace('/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3',str(py))
    f=tmp_path/"entry.sh";f.write_text(script);return f


def test_real_entry_translates_global_id_not_local_rank(tmp_path):
    f=fake_entry(tmp_path)
    r=subprocess.run(["bash",str(f),"--node-id","2","--node-count","3"],env={**os.environ,"RANK":"0","WORLD_SIZE":"1"},capture_output=True,text=True)
    assert r.returncode==0 and "--worker-rank\n2\n--worker-count\n3" in r.stdout and "RANK=unset" in r.stdout


@pytest.mark.parametrize("extra,env", [(["--worker-rank","0"],{}),([],{"RANK":"1"}),([],{"WORLD_SIZE":"3"})])
def test_entry_rejects_multiworker_and_duplicate_dispatch(tmp_path,extra,env):
    r=subprocess.run(["bash",str(fake_entry(tmp_path)),"--node-id","0","--node-count","3",*extra],env={**os.environ,**env},capture_output=True,text=True)
    assert r.returncode==2


def test_time_window_stops_before_claim_without_shortening_cell_deadline(tmp_path,monkeypatch):
    from types import SimpleNamespace
    args=SimpleNamespace(worker_rank=0,queue=tmp_path,node_stop_at=1)
    # No actual services/driver launch; exercise admission guard with expired time.
    (tmp_path/"prepare-receipt.json").write_text("{}")
    events=[]
    monkeypatch.setattr(supervisor,"full_deadlines_module",lambda:SimpleNamespace(DEVELOPMENT=14*3600,HELDOUT=5*3600,FINALIZE_MARGIN=1800))
    monkeypatch.setattr(supervisor,"queue_cells",lambda root:[])
    monkeypatch.setattr(supervisor,"claim_next",lambda *a:pytest.fail("must not claim when full cell cannot fit"))
    assert supervisor.run_queue(args,{},None,tmp_path,None,None,None,None,lambda state,**kw:events.append(state),{},tmp_path)==0
    assert events[-1]=="node_time_window_exhausted"
