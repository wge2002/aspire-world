"""Notify the coordinator of CC permission UI events. Never approve anything."""
import os,re,sys,json,time,hashlib,subprocess
from pathlib import Path
TMUX="/mnt/home/gewang/.local/opt/tmux-3.2a/usr/bin/tmux"
PANE="%142"
ROOT=Path("/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim/docs/experiments/code-world-pipeline-repair-20261007")
CHANNEL="aspire-r3-cc-permission"
ansi=re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)")
tail=""; last=None
while True:
    raw=os.read(0,65536)
    if not raw:break
    tail=(tail+raw.decode("utf-8",errors="replace"))[-100000:]
    cleaned=ansi.sub("",tail)
    compact=re.sub(r"\s+", "", cleaned)
    if "Doyouwantto" not in compact and "Allowthis" not in compact:
        continue
    tail=""
    time.sleep(0.25)
    screen=subprocess.check_output([TMUX,"capture-pane","-p","-t",PANE,"-S","-140"],text=True)
    markers=list(re.finditer(r"Do you want to (?:proceed|create|make|edit|allow)|Allow this",screen))
    if not markers:continue
    pos=markers[-1].start()
    start=max(screen.rfind(" Bash command",0,pos),screen.rfind(" Read file",0,pos))
    excerpt=screen[start if start>=0 else max(0,pos-12000):]
    digest=hashlib.sha256(excerpt.encode()).hexdigest()
    if digest==last:continue
    last=digest
    record={"detected_at":time.time(),"pane":PANE,"sha256":digest,"prompt":excerpt}
    tmp=ROOT/"CC_PENDING_PERMISSION.tmp"
    tmp.write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\n")
    tmp.replace(ROOT/"CC_PENDING_PERMISSION.json")
    with (ROOT/"CC_PERMISSION_EVENTS.jsonl").open("a") as f:
        f.write(json.dumps({"detected_at":record["detected_at"],"sha256":digest})+"\n")
    subprocess.run([TMUX,"wait-for","-S",CHANNEL],check=True)
