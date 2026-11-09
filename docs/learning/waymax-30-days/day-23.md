# Day 23：同配置重复一次，比较证据而不是感觉

## 状态

- 计划日期：2026-11-30。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS`，若出现差异则 `REVIEW_REQUIRED`。

## 执行原则

在 CPU wrapper 下用完全相同配置重复完整实验：

```bash
cd /home/my/workspace/autodrive/my-ad
make waymax-learning
```

第二次产生新的 RUN_DIR。不要覆盖旧目录，也不要只比较终端最后一行。
比较：

- `training.json` 的 loss；
- `expert-0/trajectory.csv` 与 `learned-evaluation/trajectory.csv`；
- checkpoint SHA256；
- `environment.json` 中版本、输入 hash 和 affinity。

当前课程的重复性目标是相同条件下的可重现，不是证明跨机器、跨版本永远相同。
若不一致，保留两次结果并调查，不能放宽容差后宣布一致。

## 验收

解释为什么“退出码 0”不足以证明两个运行完全相同。
