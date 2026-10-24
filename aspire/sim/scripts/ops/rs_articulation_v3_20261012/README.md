# 2026-10-12 RS articulation v3 操作辅助代码

这些是本次 RS 工程验证与目录准备使用的源代码，作为交接资料发布。
它们包含该部署的固定绝对路径，不是新的通用实验入口，不应盲目执行。
先读仓库 [RS交接](../../../../../docs/RS_HANDOFF_20261012.md)。

- `rs_minimal_author.py`：从该工程cell的公开截图生成一个区域文字描述，
  用于最小接口验证；不生成任务解法，不应将其产物共享给新cell。
- `rs_smoke.py`：纯初始截图或短诊断程序，通过原 NativeWorldState 记账，
  程序执行累计消耗seed51尝试，resume不重置。已有两次尝试，记录不能清空。
- `prepare_rs_campaign.py`：准备只读runtime、独立账号、case及freeze manifest，
  **不启动**任务；已有目录时fresh模式会拒绝，部分准备失败要先核对再补齐。

- `pilot_runner.py`：用户要求的短pilot（drawer→stove→wine串行；先筛51、52、53，
  无真实成功即early_stopped；有成功则走原15-seed开发流程，再只评held-out 1、2、3，
  4-50保持not_run、分母50、incomplete）。外层适配器，不改冻结runtime。部署为
  root:999 0640的副本，放在与cell输出分离的operator目录：
  `start|status --queue <queue.json> --operator-dir <op>`（root，tmux串行监督）；
  `--case <case> preflight|cell|gate-hook|heldout`（cell用户经runuser）。
  已有准备目录只认`claude-config`与`prelaunch-route-probe-YYYYMMDD`；其他内容拒绝接管。
  CPU测试：`tests/test_rs_v3_pilot.py`。

所有运行日志、生成配置、账本及权重均在Git之外。
