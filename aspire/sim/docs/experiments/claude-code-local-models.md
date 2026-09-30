# Native Claude Code with local models

The simulation reference Fix Loop uses Claude Code as the coding-agent harness.
It can also connect directly to a vLLM server implementing the Anthropic
Messages API. In that configuration Claude Code still owns the agent loop,
file and shell tools, conversation management, and subagent orchestration;
ASPIRE supplies the robot APIs, replay scripts, skills, and experiment protocol.
No ASPIRE Python model worker or extra protocol proxy is needed on this path.

This is a vLLM-supported integration, not an Anthropic guarantee for non-Claude
models. See [vLLM's Claude Code guide](https://docs.vllm.ai/en/stable/serving/integrations/claude_code/)
and [Anthropic's gateway support scope](https://code.claude.com/docs/en/llm-gateway).

## Connect to an existing server

Use `scripts/common/claude_with_local_model.sh`. It sets process-local routing
variables and starts the installed, unmodified `claude` executable. It does not
edit the user's global Claude settings, start a model server, or select a robot
experiment. It supports the unauthenticated loopback services used here.

Run it on the model server host, or use an explicitly configured SSH tunnel.
The base URL is the server root; Claude Code appends `/v1/messages` itself.
For example, from `aspire/sim` in a fresh approved checkout:

```bash
CC_LOCAL_CONFIG_DIR="$CAMPAIGN_ROOT/cc-config" \
CC_LOCAL_CONTEXT_TOKENS=1000000 \
CC_LOCAL_MAX_OUTPUT_TOKENS=64000 \
CC_LOCAL_EFFORT=high \
CC_LOCAL_CLAUDE_BIN=/mnt/home/gewang/.local/share/claude/versions/2.1.220 \
bash scripts/common/claude_with_local_model.sh \
  http://127.0.0.1:8120 deepseek-v4-flash-vision-exp
```

`CAMPAIGN_ROOT` must be the selected fresh output directory. This example uses
the September 7 Opus-aligned experiment budget: 1,000,000 context tokens and
64,000 maximum output tokens. The latter was measured in the actual request
from pinned CC 2.1.220 with Opus 5; it is not the model API's maximum output
capacity. The former 4096 setting was a connectivity-test budget chosen in
our launcher configuration, not a model limit. Verify the selected server's
`/v1/models` response and capacity before declaring a 1M window to CC.

For Qwen, the configured service route is `http://127.0.0.1:8121` with served
model `qwen3.8-flash-next`. Verify `/v1/models` before using it. The launcher
does not start or swap GPU services when a route is unavailable.
This Qwen deployment accepts `xhigh`, `medium`, and `low`, but rejects `high`;
use `CC_LOCAL_EFFORT=xhigh` for its recorded configuration. Its native context
is 262,144 tokens; the tested 1M server uses the official static YaRN override.
DeepSeek's model configuration already supports 1,048,576 tokens. These are
model-side deployment differences, even with identical CC context/output
budgets. Keep the explicit tested server configuration in each case manifest.

All main, Opus/Sonnet/Haiku/Fable alias, legacy small-model, and subagent model
variables point to the same served model. A separate config directory avoids
reusing the user's existing Claude conversations and provider configuration.
Review project/managed settings and any explicitly passed CLI overrides, which
can still change Claude Code behavior. The launcher preserves normal Claude
permissions; it does not enable permission bypass.

## Check compatibility before a Fix Loop campaign

A working `/v1/chat/completions` endpoint alone is insufficient. Check the
Anthropic-format endpoint, token counting, streaming, and tool-result round
trips, including images returned by Claude Code's Read tool. Also validate
subagent routing and a conversation long enough to exercise compaction before
claiming the whole Fix Loop runtime has been validated.

The DeepSeek deployment inspected on September 6 advertises a **32,768-token**
context window. The first native smoke test reported a 200K window in Claude
Code's metadata, despite the server's 32K window. The launcher therefore
requires `CC_LOCAL_CONTEXT_TOKENS` and exports it as
`CLAUDE_CODE_MAX_CONTEXT_TOKENS`. Do not assume the usual 200K or 1M applies.
The second smoke test on Claude Code 2.1.220 confirmed a reported 32,768-token
window with the full default native tool definitions loaded. It did not fill
that window or exercise automatic compaction.
Input, tool definitions/results, images, thinking, and output must fit the
actual served window. Claude Code documents `CLAUDE_CODE_MAX_CONTEXT_TOKENS`
for custom model IDs, but behavior is version-dependent: validate the declared
window and compaction on the exact installed binary. An auto-compaction window
alone does not declare the model's capacity. CC 2.1.220 clamps its configured
auto-compaction window to at least 100K, then caps the effective window at the
declared model capacity. See the
[custom-model context documentation](https://code.claude.com/docs/en/model-config#correct-the-window-for-a-gateway-or-custom-model-id).

The September 7 long-context fixture exposed another issue: default window
mode routed to reactive compaction, but vLLM's context-limit HTTP 400 was
classified as a generic API error. The launcher now sets **both**
`CLAUDE_CODE_MAX_CONTEXT_TOKENS` and `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, enabling
native proactive compaction without adding another agent loop. The full native
tool configuration used about 20K input tokens before substantive work. With
CC's 13K compaction reserve and a 4096 output budget, that deployment's 32K
window was too small; the earlier September 7 matrix used 64K for DeepSeek and
256K for Qwen. The subsequent Opus-aligned matrix explicitly uses 1M/64K for
both models, after successful four-GPU capacity tests.
The model servers also allow 32 images per prompt; the former DeepSeek limit
of four broke accumulated image evidence. See the
[September 7 matrix log](../logs/2026-09-07-native-cc-matrix.md) for verification
outcomes, including failed integration fixtures.

Long-running background workers also require an explicit print-mode wait
ceiling. The launcher sets `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=43200000`,
matching the campaign's twelve-hour watchdog. Otherwise CC defaults to ten
minutes of parent idle waiting, then terminates unfinished background work
even while the worker is making progress. See the
[native background exit behavior](https://code.claude.com/docs/en/headless#background-tasks-at-exit).
An interrupted campaign can use `native_cc_campaign.py --resume-session ID
--resume-worker ID` after its infrastructure evidence is resolved; this keeps
the existing native context, trial ledger and remaining budgets and writes
fresh invocation logs.

For a headless campaign, keeping stdin open is necessary but insufficient.
Our pinned CC 2.1.220 fixture emitted a completed `task_notification` on stdout
without delivering it to the waiting parent model. The child and compaction
could finish correctly while the parent exited waiting for a result it had
never received. This also occurred with stream input kept open.
`scripts/common/native_cc_stream.py` now supplies stream-json input, waits for
the parent turn boundary, and returns the native task ID, status, and summary
to the same CLI session. Both the compatibility fixture and campaign use this
transport. CC continues to own inference, tool execution, subagents, and
compaction; the transport does not read child transcripts or generate robot
programs. Offline regression evidence verifies that a native background
completion reaches the parent's actual model input and that the parent
finishes. Real-model integration is checked separately before scored trials.
See the [native task notification types](https://code.claude.com/docs/en/agent-sdk/typescript)
and the [September 7 capacity and integration log](../logs/2026-09-07-opus-standard-capacity.md).

Reasoning controls also need validation: Claude Code's effort/thinking fields
and vLLM's `chat_template_kwargs` are different interfaces. Record what the
deployed adapter actually forwards. An HTTP success or a displayed effort
label does not prove equivalence to the previous Python worker's sampling and
reasoning configuration.

## Experiment accounting

For a comparison that holds the agent harness fixed, use the same pinned
Claude Code version, ASPIRE protocol and skill snapshot, tool configuration,
development/held-out seed partitions, repair budgets, and completion checks.
Record model weights, vLLM revision/parsers, served context size, sampling and
reasoning settings, and any model-specific compatibility switches separately.
Changing the backend still changes those model-side conditions.

The prior Qwen/DeepSeek worker campaigns used a different agent harness from
the native-CC Opus campaigns. Keep those original results and provenance as
recorded; connecting through CC does not retroactively make them comparable.
The [shared Python worker](model-fix-loop-worker.md) remains an alternative
harness, not part of the native Claude Code connection.

Full ASPIRE experiments still follow the suite's preflight, authorization,
fresh-output, forbidden-API, seed-partition, and skill-promotion requirements.
A small connectivity check is not a robot experiment or benchmark result.

`scripts/common/native_cc_compat.py` exercises native background delegation,
five Read images, and native compaction with synthetic files. It makes no
robot trial and preserves each test in a fresh directory. The fixture alone
lowers CC's compaction threshold to approximately 48K tokens to exercise that
path promptly; it retains the declared 1M/64K request budgets. The scored
campaign does not inherit this fixture-only threshold override.
`scripts/libero/native_cc_campaign.py` launches one approved case, checks that
fixture, starts a native CC coordinator and task subagent, verifies the
development ledger and model provenance, and then runs the canonical frozen
held-out script. `native_cc_protocol.py` is the deterministic replay/accounting
CLI invoked by CC's Bash tool; it has no model client or context manager.

The RBS-specific `scripts/common/native_cc_dlc_entry.sh` uses the existing DLC
identity prelude and CPFS paths. Run its `--preflight` mode through
`dlc-preflight` before submitting the long-running entry. A case JSON supplies
the selected task, model, server config, exact context/output budgets, GPU/EGL
mapping, seed partitions, and a fresh control directory. See the recorded
matrix's `case.json` and `dlc-plan.txt` for concrete examples.

The case's `compat_timeout_seconds` controls the infrastructure fixture
watchdog independently of scored trial and repair budgets. It defaults to
900 seconds. The Qwen cells use 3600 seconds: both first fixtures had correctly
read all five images, completed nine of twelve text chunks, and compacted
before the former 15-minute limit killed them. The fixture summary records
the configured timeout and whether it expired. This changes no admission
criterion or benchmark budget.

Per Wang Ge's updated September 7 instruction, DLC jobs request a total of
**4, 8, 16, or 24 GPUs**. When convenient, consolidate independent experiments
into one 16- or 24-GPU job to reduce the number of DLC jobs. The persistent
host guide is `rbs-debug:/mnt/home/gewang/DSW_DLC_WORKFLOW.md`; the remote
`dlc-run` requires an explicit `--gpus` total and rejects other counts before
submission. It maps 4/8 to one worker, 16 to two 8-GPU workers, and 24 to three
8-GPU workers. CPU, memory, and shared memory are specified per worker.

A grouped entry point must assign each worker its own case and output paths
before stripping DLC's distributed environment variables for independent
model servers. Each node has its own local GPU indices; allocation size does
not change model tensor-parallel degree or create a single 16/24-GPU CUDA
device namespace. The prepared Opus-aligned matrix has a verified group
dispatcher for three workers; each worker also holds an exclusive case lock.
Each model uses four local GPUs, with perception and simulation on a separate
fifth GPU, so this layout requests eight GPUs per worker. A four-GPU model
capacity result is not proof that the whole experiment fits a four-GPU job.

`scripts/common/submit_dlc_once.py` claims a persistent submission record
before calling the DLC CLI. Existing claims, ambiguous responses, and timeouts
block automatic resubmission; they require read-only reconciliation with DLC.
Debugging a case does not create another allocation. The prepared grouped
24-GPU plan remains unsubmitted until its exact paid allocation is authorized
and the real-model integration prerequisites pass.

## Verification record

See [the September 6 connection log](../logs/2026-09-06-claude-code-local-models.md)
for the initial connection checks and
[the September 7 capacity and integration log](../logs/2026-09-07-opus-standard-capacity.md)
for the current budget, deployment differences, four-GPU evidence, transport
fix, and actual submission state. Capacity probes are not benchmark scores.
