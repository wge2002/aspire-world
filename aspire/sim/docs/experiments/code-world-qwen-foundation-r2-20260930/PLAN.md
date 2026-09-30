# Foundation R2: basic harness repair and three fresh reruns

User authorization: “ok修复一下基本bug，然后重新三个dlc吧。” This authorizes a fresh batch of at most three DLC submissions, independently of the previous exhausted three-job batch. No automatic replacements beyond these three. No push or overwriting earlier outputs.

Scope: correct offline policy exit/report handling and the native required-report writing contract, with meaningful regression tests. Preserve the existing foundation 1/2/3/8 method and deferred backlog. Fresh independent C/full generation for bowl-on-plate, bowl-in-top-drawer, and open-middle-drawer. No earlier solution, held-out observations, or geometry/thresholds are solver inputs.

Protocol: local Qwen3.8-Flash-Next, native CC2.1.220, xhigh, 1M context and 64K configured output. Development51–65, three charged simulator executions per seed including diagnostics; initial generation/sweep, per-failure diagnosis/repair, tested-bundle selection, frozen outer evaluation1–50. Nonprivileged traced APIs with real SAM3/GraspNet/PyRoKi; no physical robot access. Same process watchdogs: development10h, heldout5h, total1002min.

Compute: three independent 8-L20Z DLC jobs, priority9. Per node model TP4 devices0–3, SAM3 device4, GraspNet5, PyRoKi/simulator6, device7spare. DSW available device2 is used for sequential real-work preflights; existing services are reused after actual calls verify them. No unrelated job/service is stopped. Both platform and real-work gates must pass before any submission. Each task's seed51 preflight consumes attempt1, imported exactly once. Estimated8–16h from scheduling.

Fresh outputs: /mnt/home/gewang/experiments/code-world-qwen-foundation-r2-20260930

Fresh runtimes: /mnt/home/gewang/code/ASPIRE-code-world-qwen-foundation-r2-20260930

Engineering CC: existing ASPIRE Opus5/high session. Experiment solver stays local Qwen. All fixed sources/support, exact launch commands and manifests will be frozen before submission. Final handoff includes job IDs, verified state, artifacts and ETA; no recurring automation requested.
