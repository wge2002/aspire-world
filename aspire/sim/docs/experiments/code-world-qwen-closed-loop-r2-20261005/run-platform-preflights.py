from pathlib import Path
import json,subprocess,time
root=Path(__file__).resolve().parent
output=Path("/mnt/home/gewang/experiments/code-world-qwen-closed-loop-r2-20261005")
state={"state":"verifying","started_at":time.time(),"cells":{}}
def save():
 p=root/"platform-status.json"; tmp=p.with_suffix(".tmp"); tmp.write_text(json.dumps(state,indent=2)+"\n"); tmp.replace(p)
save()
with (root/"verify.log").open("x") as log:
 rc=subprocess.call(["/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3",str(root/"prepare-foundation.py"),"--verify"],stdout=log,stderr=subprocess.STDOUT)
if rc: state["state"]="verification_failed"; save(); raise SystemExit(rc)
state["state"]="running"; save()
for cell in ("bowl","bowldrawer","drawer"):
 state["active"]=cell; save()
 with (root/("platform-"+cell+".log")).open("x") as log:
  rc=subprocess.call(["/mnt/home/gewang/.local/bin/dlc-preflight","--timeout","300","bash",str(output/"launch-20261005/dlc-entry.sh"),"--case",str(output/(cell+"_C")/"case.json"),"--preflight","--preflight-gpu","2"],stdout=log,stderr=subprocess.STDOUT)
 state["cells"][cell]={"exit_code":rc,"finished_at":time.time()}; save()
 if rc: state["state"]="failed"; save(); raise SystemExit(rc)
state["state"]="passed"; state["finished_at"]=time.time(); save()
