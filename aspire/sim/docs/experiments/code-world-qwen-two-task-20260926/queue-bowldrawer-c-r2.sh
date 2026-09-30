#!/usr/bin/env bash
# One-shot serial continuation of the paired Qwen study.  Run in a detached
# tmux session on rbs-debug, after the independently verified C r2 preflight.
set -euo pipefail

study=/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926
launch="$study/launch-20260928-two-task-c-r2"
case_path="$study/bowldrawer_C_r2/case.json"
queue_dir="$study/coordination/queue-bowldrawer-c-r2"
dlc_run=/mnt/home/gewang/.local/bin/dlc-run
prior_job=dlc18m6wi2366tfs
name=gewang-two-task-bowldrawer-c-r2-01

mkdir -p "$queue_dir"
exec >>"$queue_dir/queue.log" 2>&1
echo "$(date -Is) queue started for $name after $prior_job"

# These inputs were frozen and checked after a separate dlc-preflight that
# executed no simulator trial.  A changed input requires a fresh review.
printf '%s  %s\n' \
  'dcee89eccdad4916e53e11b1d2cdd689263e71fd9ac3e83dc2e88c08d0be2b29' "$launch/launch-manifest.json" \
  'e66d24359921aec8ec0f788e4607f97d4f800d1a2eb2d37dffe425db5891ab99' "$case_path" \
  '941ee185c08a35c726d7f15610daebc7ac4b5c62056814261475312325282a7e' "$study/support/run_two_task_cell_c_r2.py" \
  'be74852f6100f75b2a9b741e94239964bd223668f75fdc2bead51858ec94abbf' "$study/support/two_task_import_c.py" \
  | sha256sum -c -

if [[ -e "$study/bowldrawer_C_r2/campaign_state.json" ]]; then
  echo "$(date -Is) C campaign already exists; refusing another submission"
  exit 1
fi
if [[ -e "$queue_dir/submitted-job.txt" ]]; then
  echo "$(date -Is) C job marker already exists; refusing another submission"
  exit 1
fi
if [[ -e "$queue_dir/submit-intent.txt" ]]; then
  echo "$(date -Is) C submit intent already exists; audit before retrying"
  exit 1
fi

while :; do
  if status_text=$("$dlc_run" status "$prior_job" 2>&1); then
    prior_status=$(printf '%s\n' "$status_text" | sed -n 's/^Status: //p' | head -n 1)
    case "$prior_status" in
      Running|EnvPreparing|Queued|Pending|Creating|Waiting)
        echo "$(date -Is) A status=$prior_status; waiting 10 min"
        sleep 600 ;;
      Succeeded|Failed|Stopped)
        echo "$(date -Is) A terminal status=$prior_status"
        break ;;
      Canceled|Cancelled)
        echo "$(date -Is) A was cancelled; C submission suppressed"
        exit 1 ;;
      *)
        echo "$(date -Is) unrecognized A status=$prior_status; waiting 10 min"
        sleep 600 ;;
    esac
  else
    echo "$(date -Is) A status query failed: $status_text; waiting 10 min"
    sleep 600
  fi
done

# Only the package's own output path may be created by the new DLC worker.
if [[ -e "$study/bowldrawer_C_r2/campaign_state.json" ]]; then
  echo "$(date -Is) C campaign appeared while waiting; refusing duplicate"
  exit 1
fi
echo "$(date -Is) submitting $name"
printf '%s\n' "$(date -Is) $name" >"$queue_dir/submit-intent.txt"
"$dlc_run" submit \
  --name "$name" --gpus 8 --max-minutes 882 --priority 9 \
  --workdir /mnt/home/gewang/code/ASPIRE-code-world-qwen-two-task-20260926 \
  --skip-preflight -- \
  bash "$launch/dlc-entry.sh" --case "$case_path" \
  >"$queue_dir/submit-output.txt" 2>&1
cat "$queue_dir/submit-output.txt"
job_id=$(sed -n 's/^JobId: //p' "$queue_dir/submit-output.txt" | tail -n 1)
if [[ -z "$job_id" ]]; then
  job_id=$(grep -oE 'dlc[a-z0-9]{10,}' "$queue_dir/submit-output.txt" | tail -n 1 || true)
fi
if [[ -z "$job_id" ]]; then
  echo "$(date -Is) submit returned without a parseable JobId; inspect submit-output.txt"
  exit 1
fi
printf '%s\n' "$job_id" >"$queue_dir/submitted-job.txt"
echo "$(date -Is) submitted C job $job_id"
