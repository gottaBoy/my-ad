# Day 13：checkpoint 不只是网络权重

## 状态

- 计划日期：2026-11-20。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`。

## checkpoint 包含什么

`checkpoint.npz` 保存：

```text
feature_mean    训练集输入均值
feature_scale   训练集输入尺度
params/...      TinyPolicy 的参数树
```

如果只保存 `params`，新进程不知道怎样归一化 120 维输入，
即使网络权重相同，预测也可能不同。

`training.json` 另外记录 feature schema、维度、训练/评估 record、
loss 和 checkpoint SHA256。

## 读取与验证

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T105737Z-_rn5s941
sha256sum "$LAB_RUN/checkpoint.npz"
cat "$LAB_RUN/training.json"
```

加载器会拒绝：

- schema 不匹配；
- checkpoint 是符号链接或逃逸到 run 外；
- SHA256 不匹配；
- 参数形状不匹配；
- 均值/尺度/参数含 NaN 或无效值。

这是证据完整性门禁，不是模型质量门禁。

## 验收

说明为什么 checkpoint 需要保存归一化信息，以及 hash 校验失败时为什么
不能“先继续跑，结果再说”。
