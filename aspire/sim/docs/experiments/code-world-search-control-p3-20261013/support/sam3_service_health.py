"""Real SAM3 inference health outside solver inputs and robot attempt accounting."""
import base64,gzip,hashlib,io,json,math,time,urllib.error,urllib.request
from pathlib import Path
from urllib.parse import urlsplit
import numpy as np
from PIL import Image


class Sam3HealthFailure(RuntimeError):
    def __init__(self,report):
        self.report=report
        super().__init__(f"SAM3 inference health failed before cell admission: {report.get('error')}; report={report['report_path']}")


def sha_bytes(blob):return hashlib.sha256(blob).hexdigest()


def select_valid_mask(response,height,width):
    """Select highest-score VALID proposal. Empty low-score proposals are legal."""
    rows=response.get('results')
    if not isinstance(rows,list) or not rows:raise ValueError('no SAM3 mask proposals')
    ranked=[]
    for index,item in enumerate(rows):
        if not isinstance(item,dict):raise ValueError('malformed proposal schema')
        score=float(item['score'])
        if not math.isfinite(score):raise ValueError('nonfinite proposal score')
        ranked.append((score,index,item))
    rejected=[]
    for score,index,item in sorted(ranked,key=lambda row:row[0],reverse=True):
        try:
            if item['shape']!=[height,width]:raise ValueError('mask dimensions differ from input image')
            raw=base64.b64decode(item['mask_base64'],validate=True)
            if len(raw)!=height*width:raise ValueError('invalid decoded mask length')
            mask=np.frombuffer(raw,dtype=np.uint8).reshape(height,width)>0
            y,x=np.nonzero(mask)
            if not 0<len(x)<height*width:raise ValueError('empty/full-image proposal')
            box=[float(v) for v in item['box']]
            if len(box)!=4 or not all(math.isfinite(v) for v in box):raise ValueError('invalid finite bounding box')
            if not (0<=box[0]<box[2]<=width and 0<=box[1]<box[3]<=height):raise ValueError('bounding box outside input image')
            return mask,{'proposal_index':index,'score':score,'mask_area_pixels':len(x),'reported_box':box,
                         'mask_bbox_xyxy':[int(x.min()),int(y.min()),int(x.max()),int(y.max())],
                         'mask_center_xy':[float(x.mean()),float(y.mean())],'mask_sha256':sha_bytes(mask.tobytes()),
                         'raw_proposals':len(rows),'higher_ranked_invalid_proposals':rejected}
        except (KeyError,ValueError,TypeError) as exc:rejected.append({'proposal_index':index,'score':score,'reason':str(exc)})
    raise ValueError(f'no valid object mask among {len(rows)} proposals: {rejected[:4]}')


def service_identity(pid,expected_cuda,port):
    import psutil
    process=psutil.Process(pid)
    if not process.is_running():raise ValueError('owned SAM3 service is not running')
    actual=process.environ().get('CUDA_VISIBLE_DEVICES')
    if actual!=str(expected_cuda):raise ValueError(f'owned SAM3 CUDA mapping differs: {actual}')
    if not any(c.status==psutil.CONN_LISTEN and c.laddr.port==port for c in process.net_connections(kind='inet')):
        raise ValueError('owned SAM3 PID does not own the inference listener')
    return {'pid':pid,'create_time':process.create_time(),'CUDA_VISIBLE_DEVICES':actual,'owns_listener':True}


def check_inference(url,image_path,expected_sha256,output,*,service_pid=None,expected_cuda=None,timeout=120):
    output=Path(output);output.mkdir(parents=True)  # Never overwrite a prior report.
    report={'state':'failed','kind':'actual_service_health_only','robot_trials_added':0,'attempts_refunded':0,
            'solver_input':False,'real_mechanism_gate':False,'endpoint':url,'source_image':str(image_path),
            'expected_source_sha256':expected_sha256,'results':[],'report_path':str(output/'report.json'),'recorded_at':time.time()}
    try:
        target=urlsplit(url)
        if target.scheme!='http' or target.hostname not in {'127.0.0.1','localhost'} or target.username or target.password:
            raise ValueError('inference health must use the actual local service')
        blob=Path(image_path).read_bytes();actual_sha=sha_bytes(blob)
        report['source_image_sha256']=actual_sha
        if actual_sha!=expected_sha256:raise ValueError('pinned public development image changed')
        image=Image.open(io.BytesIO(blob)).convert('RGB');width,height=image.size;report['image_shape']=[height,width,3]
        if service_pid is not None:report['service_identity']=service_identity(service_pid,expected_cuda,target.port)
        for prompt,color in [('bowl',(30,230,80)),('plate',(30,150,245))]:
            payload=json.dumps({'image_base64':base64.b64encode(blob).decode(),'text_prompt':prompt}).encode()
            request=urllib.request.Request(url.rstrip('/')+'/segment',data=payload,headers={'Content-Type':'application/json'},method='POST')
            try:
                with urllib.request.urlopen(request,timeout=timeout) as response:status=response.status;raw=response.read()
            except urllib.error.HTTPError as exc:
                status=exc.code;raw=exc.read()
            saved=output/(prompt+'-response.json.gz');compressed=gzip.compress(raw,compresslevel=1);saved.write_bytes(compressed)
            result={'prompt':prompt,'http_status':status,'response_sha256':sha_bytes(raw),'response_path':str(saved),'saved_response_sha256':sha_bytes(compressed)}
            report['results'].append(result)
            if status!=200:raise ValueError(f'{prompt} inference HTTP{status}')
            mask,selected=select_valid_mask(json.loads(raw),height,width);result['selected_mask']=selected
            rgb=np.asarray(image).copy();rgb[mask]=(0.55*rgb[mask]+0.45*np.asarray(color)).astype(np.uint8)
            overlay=output/(prompt+'-selected-mask.png');Image.fromarray(rgb).save(overlay)
            result.update(overlay=str(overlay),overlay_sha256=sha_bytes(overlay.read_bytes()))
        if service_pid is not None:
            after=service_identity(service_pid,expected_cuda,target.port)
            if after!=report['service_identity']:raise ValueError('owned SAM3 identity changed during inference')
        report['state']='passed'
        report['association_scope']='highest-score valid masks on pinned public dev image; service health only, never real solver mechanism or task success'
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    if report['state']!='passed':raise Sam3HealthFailure(report)
    return report
