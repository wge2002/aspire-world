#!/usr/bin/env bash
# CC_TASK_14 p6d 4-cell trial on rbs-debug2: 2 units (GPUs 0-7), 4 slots: mokastove_p6d_r1,
# objtaskmilk_p6d_r1, bowldrawerclose_p6d_r1, spatialtaskcookie_p6d_r1 (N=4 each, full budget).
# Usage on rbs-debug2 as gewang, after the smoke passed:  bash launch-trial.sh
#   (or bash launch-trial.sh --accept-smoke "<reason>" to start without a passing smoke verification)
kind=trial
root=/mnt/home/gewang/experiments/code-world-method-repair-p6d-20261022-trial
smoke_root=/mnt/home/gewang/experiments/code-world-method-repair-p6d-20261022-smoke
gpus=0,1,2,3,4,5,6,7
window=2100
session=aspire-p6d-trial
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/launch-common.sh"
main "$@"
