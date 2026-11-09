# Day 12：loss 下降，为什么还可能开偏

## 状态

- 计划日期：2026-11-19。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`。

## loss 在优化什么

训练代码使用监督均方误差：

```text
loss = mean((model_output - expert_target)²)
```

`model_output` 和 `expert_target` 都是归一化的两个动作数。
每次更新由 `jax.value_and_grad` 计算梯度，再由 Adam 更新参数。
默认训练 300 次，不是 300 次新的驾驶场景。

已有训练档案：

```text
initial_loss = 0.108001925
final_loss   = 0.000078788
```

这证明训练集上的数值拟合下降，不能单独证明闭环轨迹好。

## 为什么闭环可能变坏

训练时样本来自 expert 状态；运行 learned policy 后，自己的小误差会改变
下一状态，下一次输入也随之改变。误差可能累积，这通常称为
covariate shift（状态分布偏移）或 compounding error（误差累积）。
在本实验中只能把它作为解释假设，不能仅凭一个小 fixture 断言唯一原因。

## 读取结果

```bash
LAB_RUN=/absolute/path/to/full-run
cat "$LAB_RUN/training.json"
```

验收时要同时报告 `initial_loss/final_loss` 和 rollout 的
`log_divergence/offroad`，不能只挑 loss。
