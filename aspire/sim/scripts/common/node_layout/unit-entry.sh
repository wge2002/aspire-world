#!/usr/bin/env bash
# Single-node entry of the 2026-10-15 unit layout (4 GPUs per unit, 2 cell slots
# per unit). A 4-GPU worker runs one unit, an 8-GPU worker two; any other GPU
# count is refused. Frozen into <result root>/launch/ by the study's prepare.
#
#   unit-entry.sh --node-id N --node-count M --node-window-minutes W [--gpus 0,1,2,3] [supervisor args]
#
# Without --gpus the visible GPUs (nvidia-smi -L) are used: 0..3 or 0..7.
set -eo pipefail
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh
set -u
[[ $# -ge 6 && $1 == --node-id && $3 == --node-count && $5 == --node-window-minutes ]] || {
  echo 'unit entry requires --node-id N --node-count M --node-window-minutes W' >&2; exit 2; }
node_id=$2
node_count=$4
node_window=$6
shift 6
[[ $node_id =~ ^[0-9]+$ && $node_count =~ ^[1-9][0-9]*$ && $node_id -lt $node_count ]] || {
  echo 'invalid global node id/count' >&2; exit 2; }
[[ $node_window =~ ^[0-9]+$ && $node_window -ge 60 ]] || { echo 'invalid node window minutes' >&2; exit 2; }
[[ ${RANK:-0} == 0 && ${WORLD_SIZE:-1} == 1 && ${LOCAL_RANK:-0} == 0 && ${LOCAL_WORLD_SIZE:-1} == 1 ]] || {
  echo 'unit entry requires one worker per job' >&2; exit 2; }
gpus=
if [[ ${1:-} == --gpus ]]; then
  gpus=$2
  shift 2
else
  count=$(nvidia-smi -L | grep -c '^GPU ')
  case "$count" in
    4) gpus=0,1,2,3 ;;
    8) gpus=0,1,2,3,4,5,6,7 ;;
    *) echo "unit layout needs 4 or 8 visible GPUs, found $count" >&2; exit 2 ;;
  esac
fi
[[ $gpus =~ ^[0-9]+(,[0-9]+){3}$ || $gpus =~ ^[0-9]+(,[0-9]+){7}$ ]] || {
  echo "unit layout needs 4 or 8 GPUs, got '$gpus'" >&2; exit 2; }
for argument in "$@"; do
  case "$argument" in --root*|--node-id*|--node-count*|--node-window*|--gpus*|--plan)
    echo 'global dispatch override refused' >&2; exit 2 ;; esac
done
unset RANK LOCAL_RANK WORLD_SIZE LOCAL_WORLD_SIZE MASTER_ADDR MASTER_PORT GROUP_RANK ROLE_RANK TORCHELASTIC_RUN_ID
entry_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3 -u "$entry_dir/unit_supervisor.py" \
  --root "$(cd "$entry_dir/.." && pwd)" --gpus "$gpus" --node-id "$node_id" --node-count "$node_count" \
  --node-window-minutes "$node_window" "$@"
