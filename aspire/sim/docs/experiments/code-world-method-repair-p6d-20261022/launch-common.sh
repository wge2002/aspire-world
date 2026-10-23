# CC_TASK_14 p6d direct launch on rbs-debug2 (no DLC). Sourced by launch-smoke.sh / launch-trial.sh,
# which set: kind root gpus window session. Run the wrapper on rbs-debug2 as gewang (10011:10011):
#
#   bash <this dir>/launch-<kind>.sh            checks, then starts tmux session $session (detached)
#   tmux attach -t $session                    watch; detach with C-b d (never C-c the supervisor)
#
# Guards (each refuses with exit 2 and changes nothing):
#   - identity 10011:10011; tmux session $session must not exist;
#   - the host gate receipt <root>/preflight/host-gate/<hostname>.json exists with passed true and
#     the same kind (host-gate.py; same host as this launch);
#   - <root>/launch/receipts/start.log must not exist (written exclusively, set -C), and the node
#     registry <root>/queue/worker-registry/worker-node0.json must not exist (a root launches once);
#   - every card of $gpus has no compute process and < 1 GiB used (read-only nvidia-smi);
#   - trial only: the smoke's verify-after-run.json passed (or an explicit --accept-smoke "<reason>").
# Inside tmux the supervisor runs as unit-entry.sh --node-id 0 --node-count 1 --node-window-minutes
# $window --gpus $gpus; its stdout/stderr go to <root>/launch/logs/supervisor-<UTC>.log, the unit,
# service and cell logs to <root>/queue/worker-node0/attempts/<id>/ as in every earlier round.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
tmux_bin=$(command -v tmux || echo /mnt/home/gewang/.local/opt/tmux-3.2a/usr/bin/tmux)
py=/mnt/home/gewang/code/ASPIRE/aspire/sim/.venv-libero/bin/python3
die() { echo "launch-$kind: $*" >&2; exit 2; }

preflight_checks() {
  [ "$(id -u):$(id -g)" = "10011:10011" ] || die "run as gewang (10011:10011), not $(id -u):$(id -g)"
  [ -x "$tmux_bin" ] || die "no tmux found"
  if [ "${1:-}" != inside ] && "$tmux_bin" has-session -t "$session" 2>/dev/null; then
    die "tmux session $session already exists"
  fi
  local host gate
  host=$(hostname)
  gate="$root/preflight/host-gate/$host.json"
  [ -f "$gate" ] || die "no host gate receipt $gate; run host-gate.py --root $root --kind $kind first"
  "$py" -c 'import json,sys; g=json.load(open(sys.argv[1])); sys.exit(0 if g.get("passed") is True and g.get("kind")==sys.argv[2] else 1)' \
    "$gate" "$kind" || die "host gate $gate did not pass for kind $kind"
  [ ! -e "$root/launch/receipts/start.log" ] || die "already started: $root/launch/receipts/start.log"
  [ ! -e "$root/queue/worker-registry/worker-node0.json" ] || die "node registry exists: this root already ran"
  local busy
  busy=$("$py" - "$gpus" <<'PY'
import subprocess, sys
wanted = [int(x) for x in sys.argv[1].split(",")]
cards = {}
for line in subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, check=True).stdout.splitlines():
    index, uuid, used = [x.strip() for x in line.split(",")]
    cards[uuid] = (int(index), int(used))
apps = subprocess.run(["nvidia-smi", "--query-compute-apps=gpu_uuid", "--format=csv,noheader"],
                      capture_output=True, text=True, check=True).stdout.split()
taken = {cards[u][0] for u in apps if u in cards} | {i for i, used in cards.values() if used >= 1024}
print(",".join(str(i) for i in wanted if i in taken or i not in {i for i, _ in cards.values()}))
PY
)
  [ -z "$busy" ] || die "cards in use or missing: $busy (another job? inspect nvidia-smi; never stop it from here)"
}

start() {
  preflight_checks
  "$tmux_bin" new-session -d -s "$session" "bash $here/launch-$kind.sh --inside ${accept_smoke:+--accept-smoke \"$accept_smoke\"}"
  echo "started tmux session $session on $(hostname); attach: $tmux_bin attach -t $session"
  echo "supervisor log: $root/launch/logs/ ; unit logs: $root/queue/worker-node0/attempts/"
}

inside() {
  preflight_checks inside  # Re-checked in the session: the cards may have been taken meanwhile.
  mkdir -p "$root/launch/receipts" "$root/launch/logs"
  local stamp log
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  log="$root/launch/logs/supervisor-$stamp.log"
  set -C
  { echo "kind=$kind host=$(hostname) started_utc=$stamp session=$session"
    echo "command=bash $root/launch/unit-entry.sh --node-id 0 --node-count 1 --node-window-minutes $window --gpus $gpus"
    echo "log=$log"
    [ -n "${accept_smoke:-}" ] && echo "accepted_smoke_reason=$accept_smoke"
  } > "$root/launch/receipts/start.log" || die "start receipt exists (a second start was refused)"
  set +C
  set +e
  bash "$root/launch/unit-entry.sh" --node-id 0 --node-count 1 --node-window-minutes "$window" --gpus "$gpus" \
    2>&1 | tee -a "$log"
  status=${PIPESTATUS[0]}
  echo "exit_code=$status finished_utc=$(date -u +%Y%m%dT%H%M%SZ)" >> "$root/launch/receipts/start.log"
  echo "supervisor exited $status; log $log. This tmux window stays open; close it with 'exit'."
  exec bash
}

main() {
  accept_smoke=""
  local mode=start
  while [ $# -gt 0 ]; do
    case "$1" in
      --inside) mode=inside; shift ;;
      --accept-smoke) accept_smoke="$2"; shift 2 ;;
      *) die "unknown argument $1" ;;
    esac
  done
  if [ "$kind" = trial ] && [ -z "$accept_smoke" ]; then
    "$py" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("passed") is True else 1)' \
      "$smoke_root/verify-after-run.json" 2>/dev/null \
      || die "smoke not verified ($smoke_root/verify-after-run.json); run tools/verify_p6d_smoke.py, or pass --accept-smoke \"<reason>\""
  fi
  if [ "$kind" = trial ] && "$tmux_bin" has-session -t aspire-p6d-smoke 2>/dev/null; then
    die "the smoke session aspire-p6d-smoke is still open; the trial needs all 8 cards"
  fi
  "$mode"
}
