#!/usr/bin/env bash
# Copy exactly the files this study changed or added into the remote engineering
# checkout the stager overlays from. Dry run by default; pass --apply to copy.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SIM="$(cd "$HERE/../../.." && pwd)"
REMOTE="rbs-debug:/mnt/home/gewang/code/ASPIRE-world-revision-20260910-01a0894b/aspire/sim/"
FLAGS=(-av --relative --files-from="$HERE/SYNC_FILES.txt")
if [[ "${1:-}" != "--apply" ]]; then FLAGS+=(--dry-run); echo "dry run; pass --apply to copy"; fi
rsync "${FLAGS[@]}" "$SIM/" "$REMOTE"
