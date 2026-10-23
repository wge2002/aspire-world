#!/usr/bin/env bash
# CC_TASK_14 p6d smoke on rbs-debug2: 1 unit (GPUs 0-3), 2 slots: drawer_p6d_smoke1, objtaskmilk_p6d_smoke2.
# Usage on rbs-debug2 as gewang:  bash launch-smoke.sh      (see launch-common.sh for guards and logs)
# After the supervisor exits: <python> tools/verify_p6d_smoke.py  -> <root>/verify-after-run.json
kind=smoke
root=/mnt/home/gewang/experiments/code-world-method-repair-p6d-20261022-smoke
gpus=0,1,2,3
window=2100
session=aspire-p6d-smoke
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/launch-common.sh"
main "$@"
