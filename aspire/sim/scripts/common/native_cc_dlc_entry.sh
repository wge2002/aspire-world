#!/usr/bin/env bash
# The first root-only step restores DSW's CPFS alias for existing venv shebangs.
set -eo pipefail
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh
set -u
unset RANK LOCAL_RANK WORLD_SIZE LOCAL_WORLD_SIZE MASTER_ADDR MASTER_PORT TORCHELASTIC_RUN_ID
cc_matrix_entry_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -u "$cc_matrix_entry_dir/../libero/native_cc_campaign.py" "$@"
