#!/usr/bin/env bash
# Supplementary held-out entry (2026-10-07): the same identity/CPFS preamble as the
# study's pinned entries, then one stopped cell's held-out evaluation. No solver,
# no ability check, no compat fixture: the development ledger is already written.
set -eo pipefail
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh
set -u
unset RANK LOCAL_RANK WORLD_SIZE LOCAL_WORLD_SIZE MASTER_ADDR MASTER_PORT GROUP_RANK ROLE_RANK TORCHELASTIC_RUN_ID
entry_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case_path="${1:?usage: run-supplementary-heldout.sh <absolute case.json path>}"
shift
exec /mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3 -u \
  "$entry_dir/support/supplementary_heldout_run.py" --case "$case_path" "$@"
