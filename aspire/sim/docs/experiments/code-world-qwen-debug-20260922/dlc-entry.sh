#!/usr/bin/env bash
# Match the proven DLC identity/CPFS preamble before touching project files.
set -eo pipefail
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh
set -u
unset RANK LOCAL_RANK WORLD_SIZE LOCAL_WORLD_SIZE MASTER_ADDR MASTER_PORT GROUP_RANK ROLE_RANK TORCHELASTIC_RUN_ID
qwen_pilot_entry_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3 -u "$qwen_pilot_entry_dir/dlc-supervisor.py" "$@"
