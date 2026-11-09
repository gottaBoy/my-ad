# Day 17：只改变学习率

## 状态

- 计划日期：2026-11-24。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS` 或 `FAIL_EXPECTED`。

## 实验

默认学习率 `0.01`，本日只用 `0.003`：

```bash
cd /home/my/workspace/autodrive/my-ad
bash scripts/waymax/run-cpu.sh --stage all \
  --config config/waymax/experiments/lr-0003.json
```

学习率是每次梯度更新的步幅，不是车辆速度，也不是动作幅度。
过大可能震荡，过小可能在 300 次更新内下降不够。

记录初始 loss、最终 loss、训练耗时和 rollout 指标；不要同时改 seed 或更新次数。

## 验收

解释为什么一个 loss 下降较慢的配置不一定在闭环轨迹上更差。
