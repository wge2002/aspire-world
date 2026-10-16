"""Bind startup acceptance to a specific real attempt, never mutable initial paths.

The original startup checkpoint stays immutable. A separately recorded repair
checkpoint names the SAME worker and the exact latest charged execution. This
module is also the fresh pre-submit check; semantic reviews bind its evidence.
"""
import hashlib
import json
from pathlib import Path


class GateError(ValueError):
    pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def stable(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def require(condition, message):
    if not condition: raise GateError(message)


def attempt_sources(task, row):
    directory = (task / row["directory"]).resolve()
    require(directory.is_relative_to(task.resolve()), "attempt escapes current task")
    files = {"policy": directory / "code.py", "world": directory / "world_program.py"}
    hashes = {key: sha(path) for key, path in files.items()}
    require(hashes == row["bundle"], "actual executed policy/world snapshots differ from ledger bundle")
    require(stable(hashes) == row["bundle_sha256"], "ledger bundle identity is inconsistent")
    config = load(directory / "executable_world_config.json")
    require(config["policy_sha256"] == hashes["policy"] and config["world_program_sha256"] == hashes["world"],
            "execution config does not bind the selected pair")
    return directory, files, hashes


def original_checkpoint(control):
    source=Path(control)/'native_smoke_state.json'
    if load(source).get('status')=='native_smoke_checkpoint':return source,load(source)
    captured=Path(control)/'native_gate_first_pause.json'
    require(load(captured).get('source_state_sha256')==sha(source),'first paused capture lost original blocked state')
    return captured,load(captured)


def validate_history(case, ledger):
    """Reconstruct original admissions and exact smoke aliases, without writes."""
    import sys
    sys.path.insert(0,str(Path(case['sim'])/'scripts/libero'))
    from native_world_fixloop_state import NativeWorldState
    state=object.__new__(NativeWorldState);state.seeds=case['dev_seeds'];state.save=lambda:None
    for index,record in enumerate(ledger['trials']):
        state.data={**ledger,'stage1_complete':False,'trials':list(ledger['trials'][:index]),'aliases':[]}
        if record.get('alias_of'):
            expected=state.alias_smoke_as_initial(record['seed'])
            require(record==expected,'smoke alias changed original evidence or execution accounting')
        else:state.validate_admission(record['phase'],record['seed'],record['bundle_sha256'])


def binding(control, checkpoint_path=None):
    control = Path(control).resolve()
    case = load(control / "case.json")
    source_state=control/'native_smoke_state.json'
    original_path=source_state
    if load(source_state).get('status')!='native_smoke_checkpoint':
        original_path=control/'native_gate_first_pause.json'
        require(load(original_path).get('source_state_sha256')==sha(source_state),'first paused capture lost original blocked state')
    original = load(original_path)
    require(original.get("status") == "native_smoke_checkpoint" and original.get("coordinator_exit_code") == 0,
            "first native startup is not safely paused")
    require(original.get("cell") == case["id"], "startup cell differs")
    cp_path = Path(checkpoint_path) if checkpoint_path else control / "native_work_continuation_checkpoint.json"
    repaired = cp_path.exists()
    cp = load(cp_path) if repaired else original
    require(cp.get("native_session") == original.get("native_session") and cp.get("native_worker") == original.get("native_worker")
            and original.get("native_worker") and original.get("native_session"), "native worker/session changed")
    require(cp.get("status") == ("native_gate_repair_checkpoint" if repaired else "native_smoke_checkpoint"), "continuation is not safely paused")
    task = Path(case["sim"]) / "outputs/libero_fix_loop" / case["suite"] / case["task"]
    ledger_path = task / "development_state.json"; ledger = load(ledger_path)
    require(not ledger.get("stage1_complete") and not ledger.get("selected"), "startup prematurely selected/froze the cell")
    require(not any(row.get("status") == "running" for row in ledger["trials"]), "a trial is still running")
    all_charged = [row for row in ledger["trials"] if row.get("spends_retry")]
    charged = [row for row in all_charged if row.get("seed")==51]
    require(charged and len(charged)<=3 and all(row.get("seed") in case["dev_seeds"] for row in all_charged)
            and all(sum(row.get("seed")==seed for row in all_charged)<=3 for seed in case["dev_seeds"]), "original development attempt budget/seed boundary exceeded")
    validate_history(case, ledger)

    original_smoke=original["smoke_attempt"]
    require(all(charged[0].get(k)==v for k,v in original_smoke.items() if not (k in {'error','recovery'} and charged[0].get('status')=='infrastructure_error' and charged[0].get('recovery',{}).get('disposition')=='closed_without_usable_evidence')) and charged[0]["phase"] == "smoke", "original smoke evidence changed")
    require(len({row["directory"] for row in charged}) == len(charged), "duplicate charged execution")
    require([row.get("attempt") for row in charged] == list(range(1, len(charged)+1)), "attempt accounting is not sequential")
    require(all(not row.get('alias_of') for row in charged), 'an evidence alias cannot spend an execution slot')
    for seed in case['dev_seeds']:
        rows=[r for r in all_charged if r['seed']==seed]
        require([r['attempt'] for r in rows]==list(range(1,len(rows)+1)), 'per-seed charged attempt accounting is not sequential')
        require(len({r['directory'] for r in rows})==len(rows), 'duplicate charged development execution')
    if repaired:
        require(cp.get("coordinator_exit_code") == 0 and cp.get("original_checkpoint_sha256") == sha(original_path), "repair lost original checkpoint or did not end safely")
        before_path = Path(cp["ledger_before"]); before = load(before_path)
        require(sha(before_path) == cp["ledger_before_sha256"], "repair baseline ledger changed")
        require(ledger["trials"][:len(before["trials"])] == before["trials"], "repair changed/refunded prior trial history")
        rows=[r for r in all_charged if r['directory']==cp.get('selected_attempt')]
        require(len(rows)==1 and any(r['directory']==rows[0]['directory'] for r in ledger['trials'][len(before['trials']):]), 'checkpoint must select a specific real execution from its recovered stage')
        require(cp.get('attempt_counts',{})=={str(seed):sum(r['seed']==seed for r in all_charged) for seed in case['dev_seeds']}, 'continuation attempt accounting differs')
        require(cp.get("ledger_sha256") == sha(ledger_path), "continuation checkpoint is stale")
        row = rows[0]
        require(cp.get("selected_bundle") == row["bundle"], "continuation bundle differs from selected execution")
    else:
        require(len(all_charged) == 1, "additional attempts require an explicit continuation checkpoint")
        row = charged[0]
    require(row.get("executed") is True and row.get("status") == "complete" and row.get("phase") in {"smoke", "initial", "repair"}, "selected attempt is not completed policy/world execution")
    directory, sources, bundle = attempt_sources(task, row)
    initial = {"policy": task/"initial_code.py", "world": task/"initial_world_program.py"}
    require({k:sha(v) for k,v in initial.items()} == charged[0]["bundle"], "original initial pair was overwritten or misidentified")
    prompt_receipt = original.get("actual_worker_prompt_receipt", {})
    require(prompt_receipt.get("state") == "passed", "actual native prompt receipt missing")
    child = Path(prompt_receipt["actual_child_transcript"])
    first = next(x["message"]["content"] for x in map(json.loads, child.read_text().splitlines()) if x.get("type") == "user")
    if isinstance(first,list): first = "".join(b.get("text", "") for b in first if b.get("type") == "text")
    require(isinstance(first,str) and first.encode() == (control/"worker-prompt.md").read_bytes(), "actual native prompt differs from frozen assignment")
    import importlib.util,sys
    support=control.parent/'support';sys.path.insert(0,str(support))
    lineage_path=support/'lineage.py';spec=importlib.util.spec_from_file_location('fresh_startup_lineage',lineage_path)
    lineage=importlib.util.module_from_spec(spec);spec.loader.exec_module(lineage)
    transcripts=[Path(p) for p in cp['solver_transcripts']]
    require(all(str(p) in cp['solver_transcripts'] for p in original['solver_transcripts']), 'continuation dropped original native transcripts')
    audit=lineage.audit_assignment(transcripts,(control/'worker-prompt.md').read_text())
    require(audit.get('ok') and audit.get('primary_worker')==original['native_worker']
            and [r['session_id'] for r in audit.get('sessions',[])]==[original['native_session']], 'actual native transcript worker/session lineage changed')
    resolved_path = directory/"resolved-environment.json"; resolved=load(resolved_path)
    require(resolved.get("privileged") is False and list(resolved.get("constructed_apis",{})) == ["FrankaLiberoApiReducedSkillLibraryTraced"], "actual nonprivileged API not verified")
    world = directory/"judgment_world"; events_path=world/"events.jsonl"; manifest_path=world/"manifest.json"
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    functions=[e.get("function") for e in events if e.get("event")=="public_api" and not e.get("error")]
    mechanism=load(manifest_path).get("runtime_mechanism",{})
    import importlib.util
    auditor_path=Path(case["sim"])/"cap/world_model/runtime_mechanism_audit.py"
    spec=importlib.util.spec_from_file_location("startup_core_audit",auditor_path)
    auditor=importlib.util.module_from_spec(spec);spec.loader.exec_module(auditor)
    recomputed=auditor.audit_events(events_path,static_use={})
    require(mechanism.get("status")==recomputed.get("status") and
            mechanism.get("action_chains",[])==recomputed.get("action_chains",[]), "saved mechanism differs from actual event dataflow")
    paths=[control/"case.json", control/"worker-prompt.md",original_path,source_state,ledger_path,child,resolved_path,events_path,manifest_path,
           directory/"executable_world_config.json",auditor_path,lineage_path,support/"native_lineage_r2.py",*transcripts,*sources.values(),*initial.values()]
    if repaired:paths += [cp_path,before_path]
    files={str(p.resolve()):sha(p) for p in paths}
    return {"case":case,"checkpoint":cp,"repaired":repaired,"row":row,"directory":directory,"sources":sources,
            "bundle":bundle,"mechanism":mechanism,"events":events,"public_functions":functions,"files":files,
            "binding_sha256":stable(files),"attempt_count":len(charged),"attempt_counts":{str(seed):sum(r["seed"]==seed for r in all_charged) for seed in case["dev_seeds"]},"original":original}


def verify(control):
    try:
        b=binding(control);errors=[];m=b["mechanism"]
        if not {"get_observation","segment_sam3_text_prompt","mask_to_world_points"}.issubset(b["public_functions"]): errors.append("real observation/segmentation/point-cloud pipeline incomplete")
        chains=[c for c in m.get("action_chains",[]) if any(r.get("kind") in {"geometry","relation"} and r.get("status") in {"match","mismatch"} for r in c.get("results",[]))]
        if m.get("status") != "action_evidence" or not chains: errors.append("no physical prediction/check/decision/completed-call chain")
        if not any(e.get("event")=="state_write" and e.get("record",{}).get("status")=="known" and e.get("record",{}).get("kind") in {"geometry","relation"} for e in b["events"]): errors.append("no measured known physical template value")
        for chain in chains:
            require(any(e.get("event")=="public_api" and e.get("api_call")==chain.get("next_api_call") and not e.get("error") for e in b["events"]), "chain does not name a completed public action")
        return {"state":"passed" if not errors else "not_passed","errors":errors,"cell":b["case"]["id"],"seed":b["row"]["seed"],
                "native_session":b["checkpoint"]["native_session"],"native_worker":b["checkpoint"]["native_worker"],
                "selected_attempt":b["row"]["directory"],"attempt_count":b["attempt_count"],"attempt_counts":b["attempt_counts"],"bundle":b["bundle"],
                "directory":str(b["directory"]),"repaired":b["repaired"],"evidence_files":b["files"],"binding_sha256":b["binding_sha256"],
                "public_functions":b["public_functions"],"mechanism":m,"physical_action_chains":chains,
                "semantic_review_required":"Review actual generated sources and task entity association; bind review to this attempt, pair and evidence hash."}
    except (OSError,ValueError,KeyError,StopIteration,TypeError) as exc:
        return {"state":"not_passed","errors":[f"startup evidence refused: {exc}"]}


def require_fresh_review(control, saved):
    current=verify(control)
    require(current["state"]=="passed", "current real gate failed")
    for key in ("binding_sha256","selected_attempt","bundle","native_session","native_worker"):
        require(saved.get(key)==current.get(key), f"saved gate is stale or mismatched: {key}")
    review=saved.get("semantic_source_review",{})
    require(review.get("accepted") is True and review.get("binding_sha256")==current["binding_sha256"]
            and review.get("selected_attempt")==current["selected_attempt"] and review.get("bundle")==current["bundle"], "semantic source review is not bound to current evidence")
    return current
