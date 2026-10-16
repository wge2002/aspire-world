"""Plan only by default. Submit exactly one reviewed additional node; no stop API."""
import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
from schedule_add_node3 import (ROOT, LAUNCH, JOB, command, require_release, require_unused, available_cells, listed_job_ids)
from schedule_r9 import write_once

def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if not args.execute:
        result=subprocess.run(command('plan'),capture_output=True,text=True,check=True)
        (ROOT/'coordination/concrete-dlc-plan-add-node3.log').write_text(result.stdout)
        print(result.stdout);return 0
    lock=(ROOT/'coordination/add-node3-submit.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    require_release(ROOT,ROOT/LAUNCH);require_unused(ROOT)
    result=subprocess.run(['/mnt/home/gewang/.local/bin/dlc','get','job','--workspace_id=1000309',
                           '--display_name',JOB,'--page_size=20'],capture_output=True,text=True,check=True)
    (ROOT/'coordination/pre-submit-add-node3-list.log').write_text(result.stdout)
    if listed_job_ids(result.stdout): raise RuntimeError('additional job name exists; never submit again')
    # Frozen bytes and exact release are checked again immediately before paid submission.
    require_release(ROOT,ROOT/LAUNCH);require_unused(ROOT)
    helper=Path(__file__).resolve().parent/'submit_dlc_once.py'
    spec=importlib.util.spec_from_file_location('add_node_submit_once',helper)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    receipt=module.submit_once(ROOT/'coordination/submissions/add-node3',command('submit'))
    write_once(ROOT/'coordination/add-node3-submission-receipt.json',
               {'global_node_id':3,'unclaimed_before_submit':available_cells(ROOT),**receipt})
    print(json.dumps(receipt),flush=True)
    return 0 if receipt['state']=='submitted' else 1

if __name__=='__main__':raise SystemExit(main())
