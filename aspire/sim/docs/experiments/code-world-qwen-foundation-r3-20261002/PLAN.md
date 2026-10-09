# Foundation R3 — repair and rerun the two blocked tasks

Authorization 2026-10-02: 修复了补跑这两个吧. This is a fresh batch of exactly two submissions, bowl_C and drawer_C; no bowldrawer rerun, no automatic replacement submissions. Preserve R2 outputs and frozen runtimes.

Only native compatibility session completion is repaired. Existing foundation method1/2/3/8, original Fix Loop, task prompts, C/full condition, native CC2.1.220, local Qwen3.8-Flash-Next xhigh,1M context/64K configured output stay unchanged. Do not use historical task solutions or heldout data. Engineering CC Opus5/high is separate from the experiment solver.

Develop on51–65 with3 total charged executions per seed including diagnostics, then freeze and evaluate all1–50. Run a fresh real-work nonprivileged perception/IK preflight for each task on DSW GPU2, charge seed51 attempt1 and import exactly once. Verify constructed API classes and live calls. Independently verify native transport regression and both DLC platform preflights. No simulator or world mechanism redesign.

Compute: two independent8-L20Z jobs, priority9, max1002minutes. Each node Qwen TP4 GPUs0–3, SAM3=4, GraspNet=5, simulator/PyRoKi=6, spare=7. Development10h, heldout5h, expected8–16h after scheduling. Reuse existing protected authentication, cached gated weights, pinned environments and verified entry prelude (UID/GID10011:10011). Do not stop unrelated jobs or services.

Fresh results: /mnt/home/gewang/experiments/code-world-qwen-foundation-r3-20261002
Fresh runtimes: /mnt/home/gewang/code/ASPIRE-code-world-qwen-foundation-r3-20261002
Engineering checkout protocol HEAD:7ba73d3bcac8f6b6d4a7d67ed4040988f768d282; exact staged content pinned by manifests.

Use persistent launches and record job IDs/state/logs/ETA. No recurring automation requested. Transport changes must be tested before freezing and submission.
