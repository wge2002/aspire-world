"""Service health only on an existing public dev image; NO robot trial/gate."""
import base64,hashlib,json,time,urllib.request
from pathlib import Path
import numpy as np
from PIL import Image
ROOT=Path('/mnt/home/gewang/experiments/code-world-runtime-p2-20261011')
CONTROL=ROOT/'bowl_p2_r1'
case=json.loads((CONTROL/'case.json').read_text())
ready=json.loads((ROOT/'coordination/model-smoke-headroom-r1/ready.json').read_text())
assert ready['state']=='ready' and ready['hashes_unchanged']
assert case['context_tokens']==1000000 and case['max_output_tokens']==64000 and case['effort']=='xhigh'
task=Path(case['sim'])/'outputs/libero_fix_loop'/case['suite']/case['task']
ledger=json.loads((task/'development_state.json').read_text())
row=next(r for r in ledger['trials'] if r['phase']=='smoke')
assert row['seed']==51 and row['status']=='complete'
source=Path(row['trial_dir'])/'keyframes/step_000_obs_agentview.jpg'
assert source.resolve().is_relative_to(task.resolve())
blob=source.read_bytes();image=Image.open(source).convert('RGB');width,height=image.size
out=ROOT/'coordination/sam3-real-public-health-after-headroom-r1';out.mkdir()
report={'state':'not_passed','kind':'service_health_only','robot_trials_added':0,'attempts_refunded':0,'real_mechanism_gate':False,
        'input_source':str(source),'input_sha256':hashlib.sha256(blob).hexdigest(),'public_development_seed':51,
        'image_shape':[height,width,3],'SAM3_endpoint':case['sam3_url'],'shared_service_PID':45830,'results':[],
        'own_model_gpu_memory_utilization':0.80,'recorded_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
(out/'public-source.jpg').write_bytes(blob)
try:
    for prompt in ['bowl','plate']:
        request=urllib.request.Request(case['sam3_url']+'/segment',data=json.dumps({'image_base64':base64.b64encode(blob).decode(),'text_prompt':prompt}).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request,timeout=120) as response:
            assert response.status==200;raw=response.read()
        (out/(prompt+'-response.json')).write_bytes(raw)
        result=json.loads(raw);summaries=[]
        for i,m in enumerate(result['results']):
            assert m['shape']==[height,width]
            mask=np.frombuffer(base64.b64decode(m['mask_base64']),dtype=np.uint8).reshape(height,width)>0
            y,x=np.nonzero(mask);assert 0<len(x)<height*width and np.isfinite(float(m['score']))
            assert len(m['box'])==4 and np.isfinite(np.asarray(m['box'],dtype=float)).all()
            summary={'index':i,'score':m['score'],'mask_area_pixels':len(x),'mask_bbox_xyxy':[int(x.min()),int(y.min()),int(x.max()),int(y.max())],
                     'mask_center_xy':[float(x.mean()),float(y.mean())],'reported_box':m['box'],'mask_sha256':hashlib.sha256(mask.tobytes()).hexdigest()}
            summaries.append(summary)
            # Derived mask visualization for association review, original untouched.
            rgb=np.asarray(image).copy();color=np.array([30,230,80] if prompt=='bowl' else [30,150,245],dtype=np.uint8)
            rgb[mask]=(0.55*rgb[mask]+0.45*color).astype(np.uint8)
            Image.fromarray(rgb).save(out/f'{prompt}-mask-{i}.png')
        assert summaries,'actual SAM3 returned no usable masks'
        report['results'].append({'prompt':prompt,'http_status':200,'mask_count':len(summaries),'masks':summaries})
    report['state']='inference_passed_association_review_required'
except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
raise SystemExit(report['state']!='inference_passed_association_review_required')
