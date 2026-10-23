"""Read-only inventory of reusable helpers in code-world cells (CC_TASK_15).

For every cell under the ROOTS below that used the current API (runtime-manifest
libero_reduced.py sha fee6d84b...), pick the development-evidence program:
  - the selected bundle (development_state.selected), else
  - the swept bundle with the most development passes (>= 1),
recover its policy bytes from a development trial directory whose code.py hashes to the
bundle's policy sha (never from held-out), parse the top-level functions, tag them by
capability, and parse the worker's own "Generalizable patterns" in findings.md.

Evidence used: development_state.json (seeds 51-65 only), code.py of development trials,
findings.md, task_analysis.md.  Held-out is read only as a summary rate (for ordering);
no held-out per-seed outcome, trajectory or video is read.

Usage: python3 -I extract_candidates.py OUT_JSON
"""
import ast, glob, hashlib, json, os, re, sys
from collections import Counter, defaultdict

E = "/mnt/home/gewang/experiments"
ROOTS = {
    "p3": ["code-world-search-control-p3-20261013"],
    "p4": ["code-world-coverage-p4-20261016", "code-world-coverage-p4-20261016-rerun-a",
           "code-world-coverage-p4-20261016-rerun-b", "code-world-coverage-p4-20261016-rerun-c"],
    "p4n": ["code-world-hardest-p4n-20261018", "code-world-p4fix-20261018"],
    "p5": ["code-world-budget-p5-20261019"],
    "r3": ["code-world-repair-parallel-p6-20261020-r3"],
    "p6b": ["code-world-worker-recovery-p6b-20261021"],
    "ns": ["code-world-worker-recovery-p6b-20261021-ns"],
    "l10": ["code-world-worker-recovery-p6b-20261021-l10"],
}
API_SHA = "fee6d84b7d"  # cap/integrations/franka/libero_reduced.py prefix shared by all rounds above
DEV = set(range(51, 66))
API = ["get_observation", "segment_sam3_text_prompt", "segment_sam3_point_prompt",
       "mask_to_world_points", "get_oriented_bounding_box_from_3d_points", "plan_grasp",
       "solve_ik", "move_to_joints", "open_gripper", "close_gripper", "goto_pose",
       "goto_home_joint_position", "decompose_transform", "point_prompt_molmo", "task_language"]
WORLD_API = ["get_fact", "set_fact", "entity", "record_decision", "decision", "predict",
             "feedback", "observe", "update"]


def load(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return default


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def cells():
    for rnd, roots in ROOTS.items():
        for r in roots:
            for case in sorted(glob.glob(f"{E}/{r}/*/case.json")):
                yield rnd, r, os.path.dirname(case)


def task_dir(cell):
    for t in glob.glob(os.path.join(cell, "outputs", "libero_fix_loop", "*", "*")):
        if os.path.exists(os.path.join(t, "development_state.json")):
            return t
    return None


def versions(ds):
    """bundle -> dev seeds passed / tested, phases seen (development ledger only)."""
    v = defaultdict(lambda: {"passed": set(), "tested": set(), "phases": Counter(), "policy": None})
    for t in ds.get("trials", []):
        b, s = t.get("bundle_sha256"), t.get("seed")
        if not b or s not in DEV or t.get("status") != "complete":
            continue
        e = v[b]
        e["policy"] = (t.get("bundle") or {}).get("policy")
        e["phases"][t.get("phase")] += 1
        e["tested"].add(s)
        if t.get("task_completed") == 1:
            e["passed"].add(s)
    return v


def recover_policy(tdir, ds, bundle, policy_sha):
    """Return (path, source) of a development trial code.py whose sha == policy_sha."""
    for t in ds.get("trials", []):
        if t.get("bundle_sha256") != bundle or not t.get("directory"):
            continue
        p = os.path.join(tdir, t["directory"], "code.py")
        if os.path.exists(p) and sha(p) == policy_sha:
            return p, open(p).read()
    p = os.path.join(tdir, "fix_code.py")
    if os.path.exists(p) and sha(p) == policy_sha:
        return p, open(p).read()
    return None, None


# capability -> (regex on function name, regex on function source).  Name hits are strong;
# source hits only count for the listed API-shaped patterns.
CAPS = {
    "task_language": (r"task_lang|instruction|parse_task", r"task_language"),
    "sam3_candidates": (r"cand|segment|detect|masks?_|_masks?$|find_|sam", r"segment_sam3_(text|point)_prompt"),
    "identity_association": (r"assoc|identit|match|track|anchor|target|select|choose|resolve|which", None),
    "localize_3d": (r"locali|centroid|cloud|obb|bbox|pose_of|position", r"mask_to_world_points|get_oriented_bounding_box"),
    "segmented_move": (r"glide|segment|waypoint|goto|move_to|approach|travel|safe|path|lift|descend", None),
    "ik_reach_guard": (r"reach|clip|clamp|ik_", r"0\.7[45]\b|X_MAX|x_max"),
    "rim_pinch_grasp": (r"rim|pinch|lip", r"\brim\b|pinch"),
    "cgn_grasp": (r"cgn|graspnet|plan_grasp", r"plan_grasp\("),
    "grasp_verify": (r"held|hold|grasp_ok|lifted|is_grasp|verify_grasp|in_hand|gripping", None),
    "place_release": (r"place|release|drop|lower|put|deposit|set_down", None),
    "drawer": (r"drawer|handle|pull|push|slide", None),
    "door_hinge": (r"door|hinge|microwave|swing", None),
    "knob_switch": (r"knob|stove|burner|switch|turn|rotate|twist|dial|coil", None),
    "container_confirm": (r"inside|in_basket|contain|on_plate|on_top|placed|seated|landed", None),
    "subgoal_verify": (r"verify|confirm|check|ensure|assert|wait|settle|status|done|progress", None),
    "retry": (r"retry|attempt|recover|regrasp", r"for (attempt|_?try|tries)\b|retry"),
}
# Program-level code signatures (whole policy source): most worker logic is inline in the top
# level or main(), so helper-function tags alone undercount.  Coarse, regex-based.
PROGRAM_SIGS = {
    "task_language": r"task_language",
    "sam3_score_area_filter": r"segment_sam3_text_prompt[\s\S]{0,800}?(score|area)",
    "cross_frame_identity": r"anchor|_MEM\b|last_(fresh|known|seen)|prev(ious)?_(pos|mask|centroid)|nearest.*(prev|last)",
    "segmented_move": r"glide|def _?seg[a-z_]*\(|while [^\n]*(dist|norm)[^\n]*>[\s\S]{0,300}?(goto|_go\(|solve_ik)|for [^\n]*in [^\n]*(linspace|range\([^\n]*step|_steps|n_seg|segments)[^\n]*:[\s\S]{0,500}?(solve_ik|goto_pose|move_to_joints|_go\(|goto)",
    "ik_reach_guard": r"0\.7[45]\b",
    "rim_pinch_grasp": r"\brim\b|pinch",
    "cgn_plan_grasp": r"plan_grasp\(",
    "grasp_verify_lift": r"(lift|rise|risen|lifted)[^\n]*(>|<)|(width|aperture|jaw)[^\n]*(>|<)",
    "release_offset_comp": r"(off|offset)\s*=\s*\[?[^\n]*(gp|grasp|hand)[^\n]*-|carry_offset|hand_offset|grip_offset",
    "drawer_handle": r"handle",
    "door_hinge": r"\bdoor\b|hinge",
    "knob_rotation": r"knob|dial",
    "appearance_state_check": r"hsv|\bred(ness|dish)?\b|glow|\[\.\.\.,\s*0\]|rgb\[[^\]]*\][^\n]*mean",
    "container_inside_check": r"inside|in_basket|contain|within",
    "explicit_verify_retry": r"(verify|confirm|check)[_a-z]*\(|for (attempt|tries|_try|k) in [^\n]*:[\s\S]{0,300}?(close_gripper|goto)",
    "world_state_api": r"world\.(update|query|record_decision)|get_fact|set_fact",
}


MOTION = {"move_to_joints", "goto_pose", "open_gripper", "close_gripper", "goto_home_joint_position"}
SENSE = {"get_observation", "segment_sam3_text_prompt", "perceive", "get_fact", "_read", "observe"}


def calls(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.append(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "?")
    return out


def literals(node, task_words):
    strs, nums, vec3 = set(), 0, 0
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and 1 < len(n.value) < 40:
            strs.add(n.value)
        elif isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            if n.value not in (0, 1, -1, 2):
                nums += 1
        elif isinstance(n, (ast.List, ast.Tuple)) and len(n.elts) == 3 and all(
                isinstance(e, ast.Constant) and isinstance(e.value, float) for e in n.elts):
            vec3 += 1
    objs = sorted(s for s in strs if any(w in s.lower().split() for w in task_words))
    return objs, nums, vec3


def observes_after_act(node):
    """True if a sensing call follows a motion call and the function branches (if/while)."""
    seq = [c for c in calls(node)]
    seen_motion = False
    for c in seq:
        if c in MOTION:
            seen_motion = True
        elif seen_motion and (c in SENSE or c.startswith("_read") or c.startswith("perceive")):
            return any(isinstance(n, (ast.If, ast.While)) for n in ast.walk(node))
    return False


def tag(name, src):
    tags = []
    for cap, (nre, sre) in CAPS.items():
        if re.search(nre, name.lower()) or (sre and re.search(sre, src)):
            tags.append(cap)
    return tags


def functions(source, task_words):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.splitlines()
    out = []
    for top in tree.body:
        if not isinstance(top, ast.FunctionDef):
            continue
        nested = [n for n in ast.walk(top) if n is not top and isinstance(n, ast.FunctionDef)]
        for fn in [top] + nested:
            src = "\n".join(lines[fn.lineno - 1: fn.end_lineno])
            cs = calls(fn)
            objs, nums, vec3 = literals(fn, task_words)
            out.append({
                "name": fn.name, "lineno": fn.lineno, "n_lines": fn.end_lineno - fn.lineno + 1,
                "nested": fn is not top, "tags": tag(fn.name, src),
                "api": sorted(set(cs) & set(API)), "world_api": sorted(set(cs) & set(WORLD_API)),
                "observes_after_act": observes_after_act(fn),
                "task_object_strings": objs, "numeric_constants": nums, "vec3_literals": vec3,
                "sha12": hashlib.sha256(src.encode()).hexdigest()[:12],
            })
    return out


def findings_patterns(tdir):
    """Worker-authored 'Generalizable patterns' bullets (development evidence by construction)."""
    p = os.path.join(tdir, "findings.md")
    if not os.path.exists(p):
        return None
    text = open(p, errors="replace").read()
    m = re.search(r"^##\s*Generali[sz]able patterns\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not m:
        return []
    body = m.group(1)
    bullets = re.split(r"^- ", body, flags=re.M)[1:]
    out = []
    for b in bullets:
        trig = re.search(r"\*\*Trigger:?\*\*:?\s*(.*)", b)
        head = (trig.group(1) if trig else b.splitlines()[0]).strip()
        out.append({"trigger": head[:240], "has_code": "```" in b, "n_chars": len(b),
                    "tags": tag(head, head + " " + b[:600])})
    return out


def heldout_rate(cell):
    """Summary rate only (used to order cells; no per-seed outcome is read)."""
    d = load(os.path.join(cell, "heldout", "heldout_result.json"))
    if not d:
        return None
    for k in ("success_rate", "rate"):
        if isinstance(d.get(k), (int, float)):
            return d[k]
    c = d.get("counts") or {}
    n = sum(v for v in c.values() if isinstance(v, int))
    return round(c.get("success", 0) / n, 3) if n else None


def analyse(rnd, root, cell):
    case = load(os.path.join(cell, "case.json"), {})
    man = (load(os.path.join(cell, "runtime-manifest.json"), {}) or {}).get("files", {})
    api_sha = man.get("cap/integrations/franka/libero_reduced.py", "")[:10]
    tdir = task_dir(cell)
    rec = {"round": rnd, "root": root, "cell": os.path.basename(cell), "suite": case.get("suite"),
           "task": case.get("task"), "api_sha": api_sha, "current_api": api_sha == API_SHA,
           "heldout_rate_summary": heldout_rate(cell), "task_dir": tdir}
    if not tdir:
        return rec, []
    ds = load(os.path.join(tdir, "development_state.json"), {})
    vs = versions(ds)
    swept = {b: e for b, e in vs.items() if e["phases"].get("sweep")}
    sel = (ds.get("selected") or {}).get("bundle_sha256")
    pick, why = (sel, "selected") if sel in vs else (None, None)
    if not pick:
        best = max(swept.items(), key=lambda kv: len(kv[1]["passed"]), default=(None, None))
        if best[0] and len(best[1]["passed"]) >= 1:
            pick, why = best[0], "best_swept_unselected"
    counts = [len(e["passed"]) for e in swept.values()]
    rec.update({"n_versions_swept": len(swept), "version_dev_passes": counts,
                "selected_bundle": sel[:8] if sel else None, "program_bundle": pick[:8] if pick else None,
                "program_basis": why,
                "findings_md": os.path.exists(os.path.join(tdir, "findings.md")),
                "task_analysis_md": os.path.exists(os.path.join(tdir, "task_analysis.md")),
                "changelogs": sorted(os.path.relpath(p, tdir) for p in glob.glob(os.path.join(tdir, "*CHANGELOG*")) + glob.glob(os.path.join(tdir, "attempts", "*CHANGELOG*")))[:5],
                "patterns": findings_patterns(tdir)})
    if not pick:
        return rec, []
    e = vs[pick]
    rec.update({"dev_passed_seeds": sorted(e["passed"]), "dev_tested_seeds": sorted(e["tested"]),
                "dev_passed": len(e["passed"]), "dev_tested": len(e["tested"])})
    med = sorted(counts)[len(counts) // 2] if counts else None
    rec["selection_flags"] = {
        "multi_version_max": len(swept) >= 3 and med is not None and len(e["passed"]) - med >= 3,
        "version_median_dev_passes": med}
    path, src = recover_policy(tdir, ds, pick, e["policy"])
    rec["policy_path"] = path
    if not src:
        rec["policy_recovered"] = False
        return rec, []
    rec["policy_recovered"] = True
    rec["policy_sha12"] = e["policy"][:12]
    rec["policy_lines"] = src.count("\n") + 1
    rec["calls_plan_grasp"] = "plan_grasp(" in src
    words = {w for w in (case.get("task") or "").split("_") if len(w) > 2} - {
        "the", "and", "put", "pick", "place", "turn", "open", "close", "both", "into", "front", "top", "it"}
    rec["program_signatures"] = sorted(k for k, rx in PROGRAM_SIGS.items() if re.search(rx, src, re.I))
    fns = functions(src, words)
    for f in fns:
        f.update({"round": rnd, "cell": rec["cell"], "task": rec["task"], "bundle": pick[:8]})
    return rec, fns


def main(out_path):
    recs, fns = [], []
    for rnd, root, cell in cells():
        r, f = analyse(rnd, root, cell)
        recs.append(r)
        fns.extend(f)
    by_cap = defaultdict(lambda: {"cells": set(), "tasks": set(), "rounds": set(), "functions": []})
    for f in fns:
        if f["nested"] and not f["tags"]:
            continue
        for t in f["tags"]:
            c = by_cap[t]
            c["cells"].add(f["cell"]); c["tasks"].add(f["task"]); c["rounds"].add(f["round"])
            c["functions"].append({k: f[k] for k in ("cell", "bundle", "name", "lineno", "n_lines", "api",
                                                     "world_api", "observes_after_act",
                                                     "task_object_strings", "numeric_constants", "vec3_literals")})
    pat_cap = defaultdict(set)
    for r in recs:
        for p in r.get("patterns") or []:
            for t in p["tags"]:
                pat_cap[t].add(r["cell"])
    sig = defaultdict(list)
    for r in recs:
        if (r.get("dev_passed") or 0) >= 1:
            for k in r.get("program_signatures", []):
                sig[k].append(r["cell"])
    program_signatures = {k: {"n_cells": len(v), "cells": sorted(v)} for k, v in sorted(sig.items())}
    caps = {}
    for t, c in sorted(by_cap.items()):
        fs = c["functions"]
        caps[t] = {"n_cells": len(c["cells"]), "n_tasks": len(c["tasks"]), "rounds": sorted(c["rounds"]),
                   "n_functions": len(fs), "cells": sorted(c["cells"]),
                   "share_observes_after_act": round(sum(f["observes_after_act"] for f in fs) / len(fs), 2),
                   "share_world_api_coupled": round(sum(bool(f["world_api"]) for f in fs) / len(fs), 2),
                   "share_task_object_strings": round(sum(bool(f["task_object_strings"]) for f in fs) / len(fs), 2),
                   "median_lines": sorted(f["n_lines"] for f in fs)[len(fs) // 2],
                   "findings_pattern_cells": sorted(pat_cap.get(t, ())),
                   "functions": fs}
    summary = {
        "cells_total": len(recs),
        "cells_current_api": sum(r["current_api"] for r in recs),
        "cells_with_program": sum(bool(r.get("policy_recovered")) for r in recs),
        "cells_selected": sum(r.get("program_basis") == "selected" for r in recs),
        "cells_best_unselected": sum(r.get("program_basis") == "best_swept_unselected" for r in recs),
        "functions_total": len(fns),
        "findings_patterns_total": sum(len(r.get("patterns") or []) for r in recs),
    }
    for r in recs:
        pass  # task_dir kept: card writers need findings.md / task_analysis.md paths
    json.dump({"generated_by": "scripts/extract_candidates.py", "summary": summary,
               "program_signatures_dev_pass_cells": program_signatures, "capabilities": caps, "cells": recs}, open(out_path, "w"), indent=1, ensure_ascii=False)
    summary["cells_dev_pass_ge1"] = sum((r.get("dev_passed") or 0) >= 1 for r in recs)
    summary["cells_dev_pass_ge12"] = sum((r.get("dev_passed") or 0) >= 12 for r in recs)
    print(json.dumps(summary))
    for k, v in program_signatures.items():
        print(f"SIG {k:24s} {v['n_cells']}")
    for t, c in caps.items():
        print(f"{t:22s} cells={c['n_cells']:2d} tasks={c['n_tasks']:2d} fns={c['n_functions']:3d} "
              f"obs_after_act={c['share_observes_after_act']:.2f} world={c['share_world_api_coupled']:.2f} "
              f"objstr={c['share_task_object_strings']:.2f} findings={len(c['findings_pattern_cells'])}")


if __name__ == "__main__":
    main(sys.argv[1])
