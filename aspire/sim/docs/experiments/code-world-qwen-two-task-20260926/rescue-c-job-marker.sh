#!/usr/bin/env bash
# The C queue is already running and must not be interrupted.  This observer
# handles a submit response that contains a job ID outside its simple parser.
# It never submits or stops a job.
set -euo pipefail

study=/mnt/home/gewang/experiments/code-world-qwen-two-task-20260926
queue_dir="$study/coordination/queue-bowldrawer-c-r2"
marker="$queue_dir/submitted-job.txt"
output="$queue_dir/submit-output.txt"
dlc_run=/mnt/home/gewang/.local/bin/dlc-run
expected_name=gewang-two-task-bowldrawer-c-r2-01

exec >>"$queue_dir/marker-rescue.log" 2>&1
echo "$(date -Is) C marker observer started; never submits a job"

# The A job's maximum runtime is 882 minutes; this exceeds it plus queue lag.
for ((n=0;n<160;n++)); do
  if [[ -s "$marker" ]]; then
    echo "$(date -Is) original queue wrote the marker; done"
    exit 0
  fi
  if [[ -s "$output" ]]; then
    job_id=$(sed -n 's/^JobId: //p' "$output" | tail -n 1)
    if [[ -z "$job_id" ]]; then
      job_id=$(grep -oE 'dlc[a-z0-9]{10,}' "$output" | tail -n 1 || true)
    fi
    if [[ -n "$job_id" ]]; then
      if status_text=$("$dlc_run" status "$job_id" 2>&1); then
        actual_name=$(printf '%s\n' "$status_text" | sed -n 's/^DisplayName: //p' | head -n 1)
        if [[ "$actual_name" == "$expected_name" ]]; then
          if ( set -C; printf '%s\n' "$job_id" >"$marker" ) 2>/dev/null; then
            echo "$(date -Is) verified $job_id as $expected_name; marker rescued"
          else
            echo "$(date -Is) marker was written concurrently by original queue"
          fi
          exit 0
        fi
        echo "$(date -Is) parsed $job_id but name=$actual_name; refusing marker"
        exit 1
      fi
      echo "$(date -Is) parsed $job_id; DLC status not available yet"
    fi
  fi
  sleep 600
done
echo "$(date -Is) no verified C job ID within observer window"
exit 1
