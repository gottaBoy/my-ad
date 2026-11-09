# Day 18：只改变随机种子

## 状态

- 计划日期：2026-11-25。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS` 或 `FAIL_EXPECTED`。

## 实验

默认 `seed=0`，本日只用 `seed=1`：

```bash
cd /home/my/workspace/autodrive/my-ad
bash scripts/waymax/run-cpu.sh --stage all \
  --config config/waymax/experiments/seed-1.json
```

seed 影响参数初始化和可能的随机过程。两个 seed 不是充分统计，
但可以展示单次结果的敏感性。

比较时至少记录 checkpoint hash、最终 loss、训练场景和验证场景指标。
不要挑选较好的一次作为唯一结论。

## 验收

说明“可复现”与“统计稳定”不是同一个词；seed 对照只提供很小的稳定性线索。
