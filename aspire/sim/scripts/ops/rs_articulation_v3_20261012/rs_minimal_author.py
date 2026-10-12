"""Public-image-only minimal RS fixture; not a generated task solution."""
import base64
import hashlib
import json
from pathlib import Path
import re
import sys
import time

B = Path('/mnt/workspace/aspire-world-validation-20261012-articulation-v3')
sys.path.insert(0, str(B/'source'))
from aspire.sim.cap.world_model.qwen_world_review import QwenWorldReviewer

image = (B/'engineering-cell/scene_snapshot.jpg').read_bytes()
config = {'endpoint':'http://127.0.0.1:8127','model':'qwen3.8-flash-next',
    'effort':'xhigh','context_tokens':1000000,'max_output_tokens':64000,'timeout_seconds':900}
reviewer = QwenWorldReviewer(config)
prompt = ('Choose just one clearly visible rigid physical region external to the robot '
    'in the current image. Return only JSON {"prompt":"a short specific noun phrase for text segmentation"}. '
    'This is an interface smoke fixture. Do not solve a robot task, infer joints, '
    'decompose the scene or enumerate objects. A single region is sufficient.')
payload = {'model':config['model'],'max_tokens':64000,'thinking':{'type':'adaptive'},
    'output_config':{'effort':'xhigh'},'temperature':1.,'top_p':.95,'top_k':20,
    'system':prompt,'messages':[{'role':'user','content':[{'type':'image','source':{
        'type':'base64','media_type':'image/jpeg','data':base64.b64encode(image).decode()}}]}]}
started = time.monotonic()
response = reviewer._request(payload)
text = '\n'.join(c['text'] for c in response.get('content',[]) if c.get('type')=='text')
if text.strip().startswith('```'):
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
answer = json.loads(text)
if response.get('stop_reason')!='end_turn' or set(answer)!={'prompt'} or not isinstance(answer['prompt'],str) or not answer['prompt'].strip():
    raise ValueError('No complete public-region proposal')
cfg = {'mode':'sam2-articulation-world-v3','camera':'agentview','review':config,
    'objects':[{'identity':'public_region','prompt':answer['prompt'],
        'selector':{'axis':'image_x','rank':0}}],
    'parts':[{'identity':'public_region','semantic':answer['prompt'],'tracked':True,
        'hypotheses':{'unknown':1.}}], 'joints':[],
    'sam2':{'source_root':'/mnt/workspace/aspire-world-assets/SAM2_streaming',
        'config_file':'sam2.1/sam2.1_hiera_s.yaml',
        'checkpoint':'/mnt/workspace/aspire-world-models/sam2.1_hiera_small.pt','device':'cuda'}}
with (B/'smoke-world-config.json').open('x') as f:json.dump(cfg,f,indent=2)
receipt = {'kind':'minimal_public_region_engineering_fixture','native_policy_generation':False,
    'duration_seconds':time.monotonic()-started,'usage':response.get('usage',{}),
    'request_sha256':hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),
    'scene_sha256':hashlib.sha256(image).hexdigest(),'prompt':answer['prompt'],
    'model':config['model'],'effort':'xhigh','context_config':1000000,'max_output_config':64000}
(B/'minimal-author-receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
