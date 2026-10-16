#!/usr/bin/env bash
# Queue entry (2026-10-05): the same identity/CPFS preamble as the pinned single-cell
# entry, then every worker of the job serves the shared cell queue of one experiment
# parent. RANK/WORLD_SIZE are read before the rendezvous variables are cleared, so
# independent model servers can start on every node.
set -eo pipefail
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh
set -u
worker_rank="${RANK:-0}"
worker_count="${WORLD_SIZE:-1}"
unset RANK LOCAL_RANK WORLD_SIZE LOCAL_WORLD_SIZE MASTER_ADDR MASTER_PORT GROUP_RANK ROLE_RANK TORCHELASTIC_RUN_ID
queue_entry_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3 -u "$queue_entry_dir/dlc-supervisor.py" \
  --queue "$(cd "$queue_entry_dir/.." && pwd)" --worker-rank "$worker_rank" --worker-count "$worker_count" "$@"
