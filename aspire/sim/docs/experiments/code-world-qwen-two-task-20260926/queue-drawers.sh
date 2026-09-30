#!/usr/bin/env bash
# Continue the frozen serial Qwen study after bowldrawer_C_r2.  Both drawer
# cells passed the separate environment-only platform preflight on GPU 7.
set -euo pipefail

study=/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926
launch="$study/launch-20260928-two-task-c-r2"
queue_dir="$study/coordination/queue-drawers"
c_marker="$study/coordination/queue-bowldrawer-c-r2/submitted-job.txt"
dlc_run=/mnt/home/gewang/.local/bin/dlc-run
mkdir -p "$queue_dir"
exec >>"$queue_dir/queue.log" 2>&1
echo "$(date -Is) serial drawer queue started"

printf '%s  %s\n' \
  'dcee89eccdad4916e53e11b1d2cdd689263e71fd9ac3e83dc2e88c08d0be2b29' "$launch/launch-manifest.json" \
  '6d5cc7867aa6d054572621d5873c8c535f0cd657c2eb838d351fcc8a51f82c3a' "$study/drawer_A/case.json" \
  'a384ddb9188bcc840a895d010a11b01d594435b59d808d0821b0f9fdf7013a0f' "$study/drawer_C/case.json" \
  '11f5b9c790cc454c05b06e63bf5c217f1bb6920a943f81dc89e13ea0305159fb' "$study/support/run_two_task_cell.py" \
  | sha256sum -c -

wait_terminal() {
  local job_id=$1 label=$2 output status
  while :; do
    if output=$("$dlc_run" status "$job_id" 2>&1); then
      status=$(printf '%s\n' "$output" | sed -n 's/^Status: //p' | head -n 1)
      case "$status" in
        Running|EnvPreparing|Queued|Pending|Creating|Waiting)
          echo "$(date -Is) $label status=$status; waiting 10 min"
          sleep 600 ;;
        Succeeded|Failed|Stopped)
          echo "$(date -Is) $label terminal status=$status"
          return 0 ;;
        Canceled|Cancelled)
          echo "$(date -Is) $label cancelled; later cells suppressed"
          return 1 ;;
        *)
          echo "$(date -Is) $label status=$status unrecognized; waiting 10 min"
          sleep 600 ;;
      esac
    else
      echo "$(date -Is) $label status query failed: $output; waiting 10 min"
      sleep 600
    fi
  done
}

submit_once() {
  local cell=$1 name=$2 marker=$3 intent=$4 output=$5 job_id
  if [[ -s "$marker" ]]; then
    cat "$marker"
    return 0
  fi
  if [[ -e "$intent" || -e "$study/$cell/campaign_state.json" ]]; then
    echo "$(date -Is) $cell has an intent or campaign without a job marker; manual audit needed"
    return 1
  fi
  printf '%s\n' "$(date -Is) $name" >"$intent"
  echo "$(date -Is) submitting $name"
  "$dlc_run" submit \
    --name "$name" --gpus 8 --max-minutes 882 --priority 9 \
    --workdir /mnt/home/gewang/code/ASPIRE-code-world-qwen-two-task-20260926 \
    --skip-preflight -- \
    bash "$launch/dlc-entry.sh" --case "$study/$cell/case.json" \
    >"$output" 2>&1
  cat "$output"
  job_id=$(sed -n 's/^JobId: //p' "$output" | tail -n 1)
  if [[ -z "$job_id" ]]; then
    job_id=$(grep -oE 'dlc[a-z0-9]{10,}' "$output" | tail -n 1 || true)
  fi
  if [[ -z "$job_id" ]]; then
    echo "$(date -Is) $cell submit returned without a parseable job ID; audit $output"
    return 1
  fi
  printf '%s\n' "$job_id" >"$marker"
  echo "$(date -Is) submitted $cell as $job_id"
  printf '%s\n' "$job_id"
}

# The preceding queue writes its marker only after a successful C submit.
# A one-day guard covers the A job's 882-minute watchdog plus startup margin.
for ((n=0;n<144;n++)); do
  if [[ -s "$c_marker" ]]; then break; fi
  echo "$(date -Is) waiting for C submission marker"
  sleep 600
done
if [[ ! -s "$c_marker" ]]; then
  echo "$(date -Is) C was not submitted within 24 hours; stopping drawer queue"
  exit 1
fi
c_job=$(cat "$c_marker")
wait_terminal "$c_job" bowldrawer_C_r2

a_job=$(submit_once drawer_A gewang-two-task-drawer-a-01 \
  "$queue_dir/drawer-a-job.txt" "$queue_dir/drawer-a-intent.txt" \
  "$queue_dir/drawer-a-submit.txt" | tail -n 1)
wait_terminal "$a_job" drawer_A

submit_once drawer_C gewang-two-task-drawer-c-01 \
  "$queue_dir/drawer-c-job.txt" "$queue_dir/drawer-c-intent.txt" \
  "$queue_dir/drawer-c-submit.txt"
echo "$(date -Is) serial drawer submissions complete"
