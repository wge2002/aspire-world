#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Use the native Claude Code harness with an existing vLLM Messages endpoint.
# This launcher does not start a model server or a simulation campaign.
#
# Usage (from aspire/sim in an approved experiment checkout):
#   CC_LOCAL_CONFIG_DIR=/path/to/fresh/campaign/cc-config \
#   CC_LOCAL_CONTEXT_TOKENS=32768 \
#   CC_LOCAL_MAX_OUTPUT_TOKENS=4096 \
#   bash scripts/common/claude_with_local_model.sh \
#     http://127.0.0.1:8120 deepseek-v4-flash-vision-exp [claude arguments...]
#
# The URL is the server root, WITHOUT /v1 or /chat/completions.
# Only unauthenticated loopback endpoints are supported by this launcher.
# Read docs/experiments/claude-code-local-models.md before a full campaign.

set -euo pipefail

if [[ $# -lt 2 || "${1:-}" == "--help" ]]; then
  echo "Usage: CC_LOCAL_CONFIG_DIR=DIR CC_LOCAL_CONTEXT_TOKENS=N CC_LOCAL_MAX_OUTPUT_TOKENS=N $0 BASE_URL MODEL [claude arguments...]"
  echo "Optional: CC_LOCAL_CLAUDE_BIN=/path/to/claude"
  exit 0
fi

cc_local_base_url="${1%/}"
cc_local_model="$2"
shift 2
if [[ ! "$cc_local_base_url" =~ ^http://(127\.0\.0\.1|localhost):[0-9]+$ ]]; then
  echo "Use a loopback server root, e.g. http://127.0.0.1:8120, without /v1." >&2
  exit 2
fi
: "${CC_LOCAL_CONFIG_DIR:?Set an experiment-specific Claude Code config directory}"
: "${CC_LOCAL_CONTEXT_TOKENS:?Set the context size actually advertised by the model server}"
: "${CC_LOCAL_MAX_OUTPUT_TOKENS:?Set an explicit per-request output budget}"
if [[ ! "$CC_LOCAL_MAX_OUTPUT_TOKENS" =~ ^[1-9][0-9]*$ || ! "$CC_LOCAL_CONTEXT_TOKENS" =~ ^[1-9][0-9]*$ ]]; then
  echo "Context and output budgets must be positive integers." >&2
  exit 2
fi
if (( CC_LOCAL_MAX_OUTPUT_TOKENS >= CC_LOCAL_CONTEXT_TOKENS )); then
  echo "The output budget must leave room for input within the context window." >&2
  exit 2
fi

# Route main, background, and subagent inference to the same served model.
# These variables apply only to this process; no user settings file is edited.
unset CLAUDE_CODE_USE_BEDROCK CLAUDE_CODE_USE_VERTEX CLAUDE_CODE_USE_FOUNDRY
unset CLAUDE_CODE_USE_MANTLE CLAUDE_CODE_USE_ANTHROPIC_AWS
unset ANTHROPIC_CUSTOM_HEADERS CLAUDE_CODE_OAUTH_TOKEN
export CLAUDE_CONFIG_DIR="$CC_LOCAL_CONFIG_DIR"
export ANTHROPIC_BASE_URL="$cc_local_base_url"
export ANTHROPIC_API_KEY=local-vllm-no-key
export ANTHROPIC_AUTH_TOKEN=local-vllm-no-key
export ANTHROPIC_MODEL="$cc_local_model"
export ANTHROPIC_CUSTOM_MODEL_OPTION="$cc_local_model"
export ANTHROPIC_DEFAULT_FABLE_MODEL="$cc_local_model"
export ANTHROPIC_DEFAULT_OPUS_MODEL="$cc_local_model"
export ANTHROPIC_DEFAULT_SONNET_MODEL="$cc_local_model"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="$cc_local_model"
export ANTHROPIC_SMALL_FAST_MODEL="$cc_local_model"
export CLAUDE_CODE_SUBAGENT_MODEL="$cc_local_model"
export CLAUDE_CODE_MAX_CONTEXT_TOKENS="$CC_LOCAL_CONTEXT_TOKENS"
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="$CC_LOCAL_MAX_OUTPUT_TOKENS"
# An explicit window enables native proactive compaction. In CC 2.1.220,
# auto mode relies on recognizing a provider's prompt-too-long error; vLLM's
# generic HTTP 400 context error does not enter that recovery path.
export CLAUDE_CODE_AUTO_COMPACT_WINDOW="$CC_LOCAL_CONTEXT_TOKENS"
# Print mode otherwise kills a still-running background agent after ten minutes
# of parent idle time. Match the campaign's existing twelve-hour watchdog.
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=43200000
if [[ -n "${CC_LOCAL_EFFORT:-}" ]]; then
  export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1
  export CLAUDE_CODE_EFFORT_LEVEL="$CC_LOCAL_EFFORT"
fi
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export ENABLE_TOOL_SEARCH=false

exec "${CC_LOCAL_CLAUDE_BIN:-claude}" --model "$cc_local_model" "$@"
