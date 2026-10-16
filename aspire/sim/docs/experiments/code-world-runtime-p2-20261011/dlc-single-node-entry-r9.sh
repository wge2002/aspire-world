#!/usr/bin/env bash
set -eo pipefail
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh
set -u
[[ $# -ge 4 && $1 == --node-id && $3 == --node-count ]] || { echo 'r9 requires explicit global node id/count' >&2; exit 2; }
node_id=$2
node_count=$4
shift 4
[[ $node_id =~ ^[0-3]$ && $node_count =~ ^[1-4]$ && $node_id -lt $node_count ]] || { echo 'invalid global node id/count' >&2; exit 2; }
[[ ${RANK:-0} == 0 && ${WORLD_SIZE:-1} == 1 && ${LOCAL_RANK:-0} == 0 && ${LOCAL_WORLD_SIZE:-1} == 1 ]] || { echo 'r9 entry requires one 8GPU worker per job' >&2; exit 2; }
for argument in "$@"; do
  case "$argument" in --worker-rank*|--worker-count*|--queue*) echo 'global dispatch override refused' >&2; exit 2 ;; esac
done
unset RANK LOCAL_RANK WORLD_SIZE LOCAL_WORLD_SIZE MASTER_ADDR MASTER_PORT GROUP_RANK ROLE_RANK TORCHELASTIC_RUN_ID
entry_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3 -u "$entry_dir/dlc-supervisor.py" \
  --queue "$(cd "$entry_dir/.." && pwd)" --worker-rank "$node_id" --worker-count "$node_count" "$@"
