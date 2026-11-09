# Day 19：低 loss 和闭环偏差为什么可以同时存在

## 状态

- 计划日期：2026-11-26。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`，复用已有完整档案。

## 已有结果

```text
训练 loss：0.108001925 -> 0.000078788

learned / scene 0：
  log_divergence.mean ≈ 4.439 m
  offroad.mean = 0.1875（15/80 帧）

learned / scene 1：
  log_divergence.mean ≈ 15.748 m
  offroad.mean = 0.65（52/80 帧）
```

工程链路是 PASS，但 `policy_quality_accepted=false`。

## 怎样理解

训练 loss 问的是：“在保存的 80 个监督样本上，预测动作和 expert 标签有多接近？”
闭环指标问的是：“模型用自己的动作反复推进后，状态离参考轨迹多远？”

一次小动作误差会改变下一次观测；后续模型输入就不再完全像训练样本，
错误可能积累。这个解释合理，但要通过更多受控实验验证，不能把它写成唯一根因。

## 验收

分别写出 loss、`log_divergence`、`offroad` 的测量对象和单位；
说明为什么不能只报告 `final_loss`。
