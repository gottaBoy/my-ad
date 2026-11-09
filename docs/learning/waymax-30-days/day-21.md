# Day 21：第三周实验矩阵复盘

## 状态

- 计划日期：2026-11-28。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`，只有真实执行的行才能填结果。

## 实验矩阵

| 运行 | 唯一变量 | 其它条件 | 结果 |
| --- | --- | --- | --- |
| baseline | 无 | 默认配置 | 已有 |
| updates-100 | `training_updates` | 其它相同 | NOT_RUN |
| hidden-16 | `hidden_units` | 其它相同 | NOT_RUN |
| lr-0003 | `learning_rate` | 其它相同 | NOT_RUN |
| seed-1 | `seed` | 其它相同 | NOT_RUN |

每行必须有真实配置 hash、输入 hash、源码 revision、RUN_DIR、checkpoint hash。
没有运行就写 `NOT_RUN`，不能用“预计结果”填表。

## G3 门槛

1. 一次只改变一个字段。
2. 训练拟合和闭环质量分开。
3. 保存变差和失败结果。
4. 记住 scene 1 是开发/验证记录，不是严格盲测。

## 验收

解释为什么挑选最好的一行而隐藏其它实验会破坏结论。
