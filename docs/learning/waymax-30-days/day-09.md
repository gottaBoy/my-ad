# Day 09：观测里有什么，未来信息在哪里被挡住

## 状态

- 计划日期：2026-11-16。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`PASS`（一个明确干预下的检查）。

## 120 维输入怎么来的

当前实现 `features_from_state` 只取当前模拟状态和静态地图：

```text
自车特征：4 维
4 个邻居 × 5 维：20 维
16 个 roadgraph 点 × 6 维：96 维
总计：4 + 20 + 96 = 120 维
```

这 120 维不是 TFRecord 的 125 个字段，也不是 120 辆车。

邻居特征包括相对速度、坐标和有效标记；地图特征包括坐标、方向、类型和
有效标记。代码把无效项置零，并做简单缩放/裁剪。

## 为什么要做未来干预

检查器把 `log_trajectory` 的 future x/y 改成 `+1000/-1000`，但保持当前
`sim_trajectory` 和地图不变。如果特征不变，说明**对这一次指定干预**，
观测没有读取未来日志位置。

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/absolute/path/to/full-run
cat "$LAB_RUN/observation-check.json"
```

已有结果：

```text
feature_dimension = 120
max_feature_difference = 0.0
status = PASS
```

这不是“所有信息泄漏都被证明不存在”；它只覆盖写明的 future x/y 干预。

## 监督标签不是观测

expert 用下一帧日志反推动作，生成训练 `targets`；
模型运行时只从当前观测产生预测。把 target 塞进 feature 会造成标签泄漏，
loss 会看起来很好，但闭环运行没有真实答案可用。

## 验收

1. 120 维如何拆成 4、20、96？
2. 为什么 `features_from_state` 可以访问 `SimulatorState`，却不能把
   `log_trajectory[timestep+1]` 当作模型输入？
3. 这个 future check 的结论范围为什么有限？
