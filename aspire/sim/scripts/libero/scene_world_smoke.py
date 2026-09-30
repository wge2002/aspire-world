"""Explicit, offline scene-world construction and latency smoke test.

Uses saved public RGB-D observations only. Does not import or create a simulator.
Model requests and generated numeric workers are separate processes/trust scopes.
"""
from __future__ import annotations

import argparse
import ast
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import runpy
import shlex
import subprocess
import time

import httpx
import numpy as np
from PIL import Image


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)


class Model:
    def __init__(self, config, output):
        self.config, self.output = config, output
        settings = json.loads(Path(config["credential_settings"]).read_text())
        helper = shlex.split(settings["apiKeyHelper"])
        result = subprocess.run(helper, capture_output=True, text=True, timeout=20, check=True)
        self.key = result.stdout.strip()
        if not self.key or "\n" in self.key:
            raise ValueError("invalid protected credential helper output")

    def request(self, name, prompt, image_path=None, max_tokens=6000):
        content = []
        if image_path is not None:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg",
                "data": base64.b64encode(Path(image_path).read_bytes()).decode()}})
        content.append({"type": "text", "text": prompt})
        body = {"model": self.config["model"], "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}]}
        save(self.output / f"{name}-request.json", {
            "model": body["model"], "max_tokens": max_tokens, "prompt": prompt,
            "image_sha256": digest(image_path) if image_path else None,
            "base_url": self.config["base_url"], "automatic_retries": 0})
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=240, follow_redirects=False) as client:
                response = client.post(self.config["base_url"].rstrip("/") + "/v1/messages",
                    headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"}, json=body)
            elapsed = time.perf_counter() - started
            if response.status_code != 200:
                save(self.output / f"{name}-receipt.json", {
                    "status": "http_error", "http_status": response.status_code,
                    "elapsed_seconds": elapsed})
                raise RuntimeError(f"model request {name}: HTTP {response.status_code}")
            answer = response.json()
            save(self.output / f"{name}-response.json", answer)
            receipt = {"status": "complete", "elapsed_seconds": elapsed,
                       "response_model": answer.get("model"), "usage": answer.get("usage"),
                       "stop_reason": answer.get("stop_reason"),
                       "request_id": response.headers.get("request-id")}
            save(self.output / f"{name}-receipt.json", receipt)
            if answer.get("model") != self.config["model"]:
                raise RuntimeError("response model identity mismatch")
            if answer.get("stop_reason") != "end_turn":
                raise RuntimeError("model response did not finish normally")
            text = "\n".join(x["text"] for x in answer.get("content", []) if x.get("type") == "text")
            return text, receipt
        except Exception as exc:
            error_path = self.output / f"{name}-error.json"
            if not error_path.exists():
                save(error_path, {"error_type": type(exc).__name__,
                                  "elapsed_seconds": time.perf_counter() - started})
            raise


INVENTORY = """Inspect this one tabletop robot observation for an OFFLINE coarse scene modelling smoke test.
Return only JSON: {"objects":[{"id":"stable_short_id", "label":"SAM3 text prompt",
"role":"manipulated|target|context", "bbox_xyxy":[x1,y1,x2,y2],
"confidence":"high|medium|low", "shape_prior":"box|cylinder|sphere|plane|unknown"}],
"coverage_notes":["uncertainty or exclusions"]}.
The task is to put the bowl on the plate. Identify that bowl as manipulated and that plate as target.
Inventory ALL distinct visible physical scene items, including prominent background/support surfaces,
up to 12 items. Include only what you can see; do not invent hidden objects or infer simulator internals.
Image size is 800 by 512 pixels. Boxes are approximate image pixel bounds. Do not include the robot:
robot proprioception and the camera reference are shared context in both arms. Surface geometry and
physical parameters will be approximate with uncertainty. Explicitly note missed/ambiguous objects.
Use concise descriptive SAM3 prompts, not long reasoning. No markdown fences."""


def validate_inventory(value):
    items = value["objects"]
    if not isinstance(items, list) or not 2 <= len(items) <= 12:
        raise ValueError("inventory must contain 2..12 objects")
    ids = set()
    for item in items:
        if not isinstance(item["id"], str) or not item["id"].replace("_", "").isalnum():
            raise ValueError("invalid object identity")
        if item["id"] in ids:
            raise ValueError("duplicate object identity")
        ids.add(item["id"])
        if not isinstance(item["label"], str) or not 1 <= len(item["label"]) <= 160:
            raise ValueError("invalid segmentation prompt")
        x1, y1, x2, y2 = item["bbox_xyxy"]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in [x1,y1,x2,y2]):
            raise ValueError("invalid image bounds")
        if not (0 <= x1 < x2 <= 800 and 0 <= y1 < y2 <= 512):
            raise ValueError("image bounds outside image")
        if item["role"] not in {"manipulated", "target", "context"}:
            raise ValueError("invalid role")
    if sum(x["role"] == "manipulated" for x in items) != 1 or sum(x["role"] == "target" for x in items) != 1:
        raise ValueError("need one task bowl and one target plate")
    return value


def box_iou(a, b):
    lo = [max(a[0], b[0]), max(a[1], b[1])]
    hi = [min(a[2], b[2]), min(a[3], b[3])]
    inter = max(0, hi[0]-lo[0]) * max(0, hi[1]-lo[1])
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union > 0 else 0


def measure(config, image64, depth, intrinsic, extrinsic, item, output):
    started = time.perf_counter()
    receipt = {"object_id": item["id"], "label": item["label"]}
    try:
        with httpx.Client(timeout=120, follow_redirects=False) as client:
            response = client.post(config["sam3_url"].rstrip("/") + "/segment",
                json={"image_base64": image64, "text_prompt": item["label"]})
        response.raise_for_status()
        body = response.json()
        receipt["segmentation_seconds"] = time.perf_counter()-started
        candidates = body.get("results", [])
        eligible = []
        for candidate in candidates:
            score = candidate.get("score", 0)
            iou = box_iou(candidate["box"], item["bbox_xyxy"])
            if math.isfinite(score) and score >= 0.5 and iou >= 0.1:
                eligible.append((score*iou, candidate, iou))
        if not eligible:
            receipt.update(status="unknown", reason="no_mask_matching_prompt_and_inventory_box",
                           candidate_count=len(candidates))
            return receipt
        eligible.sort(key=lambda x: x[0], reverse=True)
        _, candidate, iou = eligible[0]
        # A near tie remains uncertain rather than binding a convenient identity.
        if len(eligible) > 1 and eligible[1][0] >= 0.9*eligible[0][0] and box_iou(eligible[0][1]["box"],eligible[1][1]["box"]) < 0.5:
            receipt.update(status="unknown", reason="ambiguous_identity")
            return receipt
        shape = tuple(candidate["shape"])
        if shape != depth.shape:
            raise ValueError("mask/depth shape mismatch")
        mask = np.frombuffer(base64.b64decode(candidate["mask_base64"]), dtype=np.uint8).reshape(shape).astype(bool)
        tick = time.perf_counter()
        yy, xx = np.where(mask)
        z = depth[yy, xx]
        good = np.isfinite(z) & (z > 0)
        z, xx, yy = z[good], xx[good], yy[good]
        if len(z) < 20:
            receipt.update(status="unknown", reason="insufficient_finite_depth")
            return receipt
        camera = np.column_stack(((xx-intrinsic[0,2])*z/intrinsic[0,0],
                                  (yy-intrinsic[1,2])*z/intrinsic[1,1],z))
        points = camera @ extrinsic[:3,:3].T + extrinsic[:3,3]
        lo, hi = np.quantile(points,[0.02,0.98],axis=0)
        center = np.median(points,axis=0)
        receipt.update(status="ok", position=center.tolist(), visible_bounds=[lo.tolist(),hi.tolist()],
                       point_count=len(points), mask_score=candidate["score"], box_iou=iou,
                       projection_and_quantile_seconds=time.perf_counter()-tick,
                       geometry_semantics="visible_surface_quantiles; not full object geometry",
                       uncertainty={"hidden_geometry":"unknown", "mass":"unspecified_simplified_parameter",
                                    "statistical_coverage":"uncalibrated"})
        np.save(output / f"{item['id']}-mask.npy",mask,allow_pickle=False)
        np.save(output / f"{item['id']}-points.npy",points[::max(1,len(points)//1500)],allow_pickle=False)
        return receipt
    except Exception as exc:
        receipt.update(status="error",error_type=type(exc).__name__)
        return receipt
    finally:
        receipt["total_seconds"] = time.perf_counter()-started


def perception(config, inventory, output):
    inputs = Path(config["input_dir"])
    rgb = Image.open(inputs / "rgb.jpg").convert("RGB")
    buffer = io.BytesIO()
    rgb.save(buffer,format="PNG")
    image64 = base64.b64encode(buffer.getvalue()).decode()
    depth = np.load(inputs / "depth.npy",allow_pickle=False)
    K = np.load(inputs / "intrinsics.npy",allow_pickle=False)
    T = np.load(inputs / "extrinsics.npy",allow_pickle=False)
    items = inventory["objects"]
    main = [x for x in items if x["role"] in {"manipulated","target"}]
    groups = {"main_serial":(main,1),"scene_serial":(items,1),"scene_concurrent4":(items,4)}
    # A separately recorded warmup is excluded from timed condition summaries.
    warm = output / "warmup"
    warm.mkdir()
    save(warm/"measurement.json",measure(config,image64,depth,K,T,main[0],warm))
    rounds = []
    orders = [["main_serial","scene_serial","scene_concurrent4"],
              ["scene_concurrent4","main_serial","scene_serial"],
              ["scene_serial","scene_concurrent4","main_serial"]]
    for repeat, order in enumerate(orders):
        for name in order:
            objects, workers = groups[name]
            folder = output / f"r{repeat}-{name}"
            folder.mkdir()
            started=time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                fs=[pool.submit(measure,config,image64,depth,K,T,x,folder) for x in objects]
                rows=[future.result() for future in fs]
            rows.sort(key=lambda x:x["object_id"])
            record={"condition":name,"repeat":repeat,"workers":workers,
                    "object_count":len(objects),"wall_seconds":time.perf_counter()-started,
                    "measurements":rows,"known_count":sum(r["status"]=="ok" for r in rows)}
            save(folder/"measurements.json",record)
            rounds.append(record)
            print(json.dumps({k:v for k,v in record.items() if k!="measurements"}),flush=True)
    summary={}
    for name in groups:
        rows=[r for r in rounds if r["condition"]==name]
        times=sorted(r["wall_seconds"] for r in rows)
        summary[name]={"seconds":times,"median_seconds":times[1],
                       "object_count":rows[0]["object_count"],"known_counts":[r["known_count"] for r in rows]}
    save(output/"perception-summary.json",summary)
    # One common acquisition is used for both generation arms, avoiding different masks.
    canonical=next(r for r in rounds if r["repeat"]==0 and r["condition"]=="scene_serial")
    metadata=json.loads((inputs/"metadata.json").read_text())
    measured={r["object_id"]:r for r in canonical["measurements"]}
    return {"schema":1,"coordinate_frame":"public_API_shared_robot_base_frame",
            "task":"put the bowl on the plate", "robot_state":metadata["robot_state"],
            "entities":[dict(item,measurement=measured[item["id"]]) for item in items],
            "camera":{"intrinsics":K.tolist(),"camera_to_reference":T.tolist()},
            "uncertainty":"visible geometry only; no exact mass, full shape or contact truth",
            "inventory_coverage_notes":inventory.get("coverage_notes",[])}, summary


WORLD_PROMPT = """Generate executable Python for a small, coarse, uncertain scene-world. This is a construction/latency smoke test, not a robot success experiment. Return ONLY Python source, no markdown or prose. Use only import math and builtins; all values must be finite JSON or None. Do not access files, images, APIs, environment variables, simulator internals or assets. The provided scene is one legal RGB-D measurement snapshot. Do not hardcode its coordinates; initialize must use its context argument.

Implement exactly these callable interfaces:
initialize(context) -> JSON state. Context has entities (each id, label, role, shape_prior, measurement), robot_state, camera and uncertainty. Preserve EVERY supplied entity, including unknown ones. You choose the internal representation. Separate observations, hypotheses and predicted state. Geometry is approximate visible surface, mass can be a stated simple prior; do not pretend hidden geometry is known.
advance(state, step) -> JSON state. Step contains action:{api, arguments}, robot_state:{position,orientation_wxyz,gripper}, budget:{remaining}. The position is measured, not a commanded goal. Handle no_motion, close_gripper, open_gripper and goto_pose. A closure alone must not prove attachment. Maintain conditional attachment/free predictions and uncertainty; include support/placement relations where the supplied geometry permits, otherwise unknown. Treat the common no_motion step as no change to object positions.
predict(state, step) -> {"objects": {id:{"position": [x,y,z] or None, "uncertainty": JSON}}, "relations": JSON, "request_query": boolean}. For initialization/no_motion, position must refer to the measured visible-surface representative, not an invented rigid center. Return ALL entity IDs. State what physical hypotheses and missing evidence limit predictions; keep nominal coordinates and uncertainty distinct.
assimilate(state, evidence) -> JSON state. Evidence has object_id, status:"ok"|"unknown", position:XYZ|None. Update numeric evidence without claiming that fitting the same sample verifies physical dynamics. Unknown stays unknown evidence, not zero or physical failure.

Use compact generic code. Scene entities vary at initialize time; the constructor must also work if all observed positions/bounds and robot positions are translated together. This is not a mandate for a particular predicate library. At runtime there is no LLM inside these four functions.

SCENE INPUT EXAMPLE (also supplied to initialize):
"""


def generated_arm(model, arm, scene, config, output):
    sub=output/arm
    sub.mkdir()
    started=time.perf_counter()
    text,receipt=model.request(f"world-{arm}",WORLD_PROMPT+json.dumps(scene,ensure_ascii=False),max_tokens=6000)
    source=text.strip()
    if source.startswith("```"):
        source=source.split("\n",1)[1].rsplit("```",1)[0]
    (sub/"world_program.py").write_text(source+"\n")
    result={"arm":arm,"generation_seconds":receipt["elapsed_seconds"],
            "response_model":receipt["response_model"],"source_sha256":digest(sub/"world_program.py"),
            "source_lines":len(source.splitlines()),"entity_count":len(scene["entities"]),
            "status":"generated_unvalidated"}
    try:
        ast.parse(source)
        worker=runpy.run_path(config["numeric_worker_source"])["FrozenPythonProgram"]
        program=worker(source,timeout_s=3)
        tick=time.perf_counter()
        state=program.call("initialize",scene)
        result["initialize_seconds"]=time.perf_counter()-tick
        step={"action":{"api":"no_motion","arguments":{}},"robot_state":scene["robot_state"],"budget":{"remaining":4}}
        tick=time.perf_counter()
        state2=program.call("advance",state,step)
        pred=program.call("predict",state2,step)
        result["advance_predict_seconds"]=time.perf_counter()-tick
        checks=[]
        expected={x["id"] for x in scene["entities"]}
        checks.append({"check":"all_entities_preserved","passed":set(pred["objects"])==expected})
        for entity in scene["entities"]:
            p=pred["objects"][entity["id"]]["position"]
            m=entity["measurement"]
            if m["status"]=="ok":
                passed=p is not None and np.allclose(p,m["position"],atol=1e-6)
            else:
                passed=p is None
            checks.append({"check":"initial_readout_and_unknown_semantics","id":entity["id"],"passed":bool(passed)})
        shifted=copy.deepcopy(scene)
        delta=np.array([0.11,-0.07,0.05])
        shifted["robot_state"]["position"]=(np.array(shifted["robot_state"]["position"])+delta).tolist()
        # Keep the camera and all world-frame anchors consistent in this synthetic check.
        for i in range(3): shifted["camera"]["camera_to_reference"][i][3]+=float(delta[i])
        for entity in shifted["entities"]:
            m=entity["measurement"]
            if m["status"]=="ok":
                m["position"]=(np.array(m["position"])+delta).tolist()
                m["visible_bounds"]=(np.array(m["visible_bounds"])+delta).tolist()
        shifted_step=copy.deepcopy(step)
        shifted_step["robot_state"]=shifted["robot_state"]
        sp=program.call("predict",program.call("initialize",shifted),shifted_step)
        for entity in scene["entities"]:
            if entity["measurement"]["status"]=="ok":
                op=pred["objects"][entity["id"]]["position"]
                npred=sp["objects"][entity["id"]]["position"]
                passed=op is not None and npred is not None and np.allclose(np.array(npred)-np.array(op),delta,atol=1e-6)
                checks.append({"check":"input_translation_equivariance","id":entity["id"],"passed":bool(passed)})
        save(sub/"state.json",state)
        save(sub/"prediction.json",pred)
        save(sub/"conformance-checks.json",checks)
        result["status"]="conformance_passed" if all(c["passed"] for c in checks) else "conformance_failed"
        result["checks_passed"]=sum(c["passed"] for c in checks)
        result["checks_total"]=len(checks)
    except Exception as exc:
        result.update(status="validation_error",error_type=type(exc).__name__)
    result["arm_wall_seconds"]=time.perf_counter()-started
    save(sub/"result.json",result)
    return result


def run(config_path):
    config=json.loads(Path(config_path).read_text())
    if config.get("mode")!="scene-world-offline-smoke-v1" or config.get("model")!="claude-opus-4-6":
        raise ValueError("explicit approved smoke mode/model required")
    if config["base_url"]!="https://llmapi.roboscience.xyz:18443" or config["sam3_url"]!="http://127.0.0.1:8114":
        raise ValueError("unexpected endpoint")
    output=Path(config["output_dir"])
    output.mkdir(parents=True,exist_ok=False)
    save(output/"config.json",config)
    save(output/"preflight.json",{"script_sha256":digest(__file__),"model_requests_cap":3,
        "max_output_tokens_per_request":6000,"new_simulator_trials":0,
        "input_hashes":{p.name:digest(p) for p in Path(config["input_dir"]).iterdir() if p.is_file()},
        "generated_worker":"JSON-only subprocess, empty environment, restricted imports; reliability isolation"})
    started=time.perf_counter()
    try:
        model=Model(config,output)
        text,inventory_receipt=model.request("inventory",INVENTORY,Path(config["input_dir"])/"rgb.jpg",max_tokens=3000)
        inventory=validate_inventory(parse_json(text))
        save(output/"inventory.json",inventory)
        print(json.dumps({"stage":"inventory","objects":len(inventory["objects"]),"seconds":inventory_receipt["elapsed_seconds"]}),flush=True)
        scene,perception_summary=perception(config,inventory,output)
        save(output/"scene-input.json",scene)
        major=copy.deepcopy(scene)
        major["entities"]=[x for x in scene["entities"] if x["role"] in {"manipulated","target"}]
        save(output/"main-scene-input.json",major)
        tick=time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(generated_arm,model,arm,value,config,output) for arm,value in [("main",major),("scene",scene)]]
            arms=[]
            for future in as_completed(futures):
                arms.append(future.result())
        summary={"status":"complete","inventory_seconds":inventory_receipt["elapsed_seconds"],
            "inventory_objects":len(inventory["objects"]),"perception":perception_summary,
            "generation_arms":sorted(arms,key=lambda x:x["arm"]),
            "two_arm_generation_and_validation_parallel_seconds":time.perf_counter()-tick,
            "total_benchmark_wall_seconds":time.perf_counter()-started,
            "interpretation":"one image and one generation per arm; latency/construction only, no physical prediction or task-success claim"}
        save(output/"summary.json",summary)
        print(json.dumps(summary),flush=True)
    except Exception as exc:
        save(output/"failure.json",{"status":"failed","error_type":type(exc).__name__,
            "elapsed_seconds":time.perf_counter()-started,"automatic_retries":0})
        raise


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--args.world-model-config",dest="config",required=True)
    args=parser.parse_args()
    run(args.config)
