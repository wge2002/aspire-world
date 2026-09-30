#!/usr/bin/env bash
# DLC allocates two 8-GPU workers. Dispatch a distinct frozen drawer cell on each.
set -euo pipefail

# The DLC image starts as root. Match the proven identity/CPFS entry sequence.
if [[ "$(id -u)" == 0 && ! -e /mnt/workspace/gewang ]]; then
  mkdir -p /mnt/workspace
  ln -s /mnt/home/gewang /mnt/workspace/gewang
fi
RBS_DLC_WORKDIR=/mnt/home/gewang
. /mnt/home/gewang/.config/rbs-dlc/dlc_entry_prelude.sh

study=/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926
source_launch="$study/launch-20260928-two-task-c-r2"
expected_world_size=2
if [[ "${WORLD_SIZE:-}" != "$expected_world_size" ]]; then
  echo "expected WORLD_SIZE=2, got ${WORLD_SIZE:-<unset>}" >&2
  exit 2
fi
case "${RANK:-}" in
  0) cell=drawer_A ;;
  1) cell=drawer_C ;;
  *) echo "expected distinct DLC worker RANK 0 or 1, got ${RANK:-<unset>}" >&2; exit 2 ;;
esac

# Pin the already preflighted launch and independent case files. The source
# launch performs its own complete support/runtime receipt verification.
printf '%s  %s\n' \
  'dcee89eccdad4916e53e11b1d2cdd689263e71fd9ac3e83dc2e88c08d0be2b29' "$source_launch/launch-manifest.json" \
  '6d5cc7867aa6d054572621d5873c8c535f0cd657c2eb838d351fcc8a51f82c3a' "$study/drawer_A/case.json" \
  'a384ddb9188bcc840a895d010a11b01d594435b59d808d0821b0f9fdf7013a0f' "$study/drawer_C/case.json" \
  | sha256sum -c -

echo "DLC worker rank=$RANK/$WORLD_SIZE assigned $cell; output=$study/$cell"
exec bash "$source_launch/dlc-entry.sh" --case "$study/$cell/case.json" "$@"
