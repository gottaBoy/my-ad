# Week 07：PyTorch 小模型和 checkpoint

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`
- 前置：能解释现有 TinyPolicy 的输入、标签和归一化

## 目标

用一个极小数据集练习训练工程，而不是追求大模型：

```text
Dataset -> DataLoader -> model -> loss -> optimizer
        -> checkpoint + normalization + report
```

## 练习

用 CSV 或固定 numpy fixture 构造：

```text
features: N x 4
targets:  N x 2
```

实现：

- train/validation split；
- `state_dict` 保存；
- `normalization.json`；
- `training.json`；
- 新 Python 进程重载预测比较；
- checkpoint hash。

## 负测

- 删除 normalization；
- 修改 feature dimension；
- 修改 checkpoint 一个字节；
- 用训练集之外的列顺序加载。

每个负测都必须给出明确失败原因。

## 验收

报告初始/最终 loss、验证 loss 和预测最大绝对差。
不要用低 loss 代替闭环 rollout 指标。
