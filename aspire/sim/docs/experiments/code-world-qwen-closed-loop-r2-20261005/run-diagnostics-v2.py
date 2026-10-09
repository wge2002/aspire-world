from pathlib import Path
import json,subprocess,time
root=Path(__file__).resolve().parent
state={"state":"running","started_at":time.time(),"cells":{}}
def save():
 p=root/"diagnostics-status-v2.json"; tmp=p.with_suffix(".tmp"); tmp.write_text(json.dumps(state,indent=2)+"\n"); tmp.replace(p)
save()
for cell in ("bowl","bowldrawer","drawer"):
 state["active"]=cell; save()
 with (root/("diagnostic-v2-"+cell+".log")).open("x") as log:
  rc=subprocess.call(["/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3","-u",str(root/("run-dsw-preflight-"+cell+"-c.py"))],stdout=log,stderr=subprocess.STDOUT)
 state["cells"][cell]={"exit_code":rc,"finished_at":time.time()}; save()
 if rc: state["state"]="failed"; save(); raise SystemExit(rc)
state["state"]="passed"; state["finished_at"]=time.time(); save()
