# Day 11：TinyPolicy 的前向计算

## 状态

- 计划日期：2026-11-18。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`。

## 模型结构

源码中的模型只有两层全连接：

```text
120 维观测
   -> Dense(32) -> tanh
   -> Dense(2)  -> tanh
   -> [归一化加速度, 归一化曲率]
```

`tanh` 把输出压到约 `[-1, 1]`，再乘 `[6.0, 0.3]` 恢复物理单位。
这不是 PPO、SAC，也不是完整自动驾驶模型；它是行为克隆教学模型。

## 参数数量

默认 `hidden_units=32`：

```text
第一层：120 × 32 + 32 bias = 3872
第二层：32 × 2 + 2 bias     = 66
总计：3938 个参数
```

参数量小不等于策略一定简单或安全；闭环 rollout 仍可能逐步偏离。

## 读代码

```bash
cd /home/my/workspace/autodrive/my-ad
sed -n '20,38p' scripts/waymax/lab_runtime.py
```

逐行理解：

- `Dense`：线性变换。
- `tanh`：非线性激活和输出范围限制。
- `Dense(2)`：两个动作维度。

## 验收

手写出 `120 -> 32 -> 2`，并解释为什么模型输出不能直接当作米制动作。
