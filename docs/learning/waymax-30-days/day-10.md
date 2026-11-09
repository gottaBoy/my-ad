# Day 10：训练样本和验证样本怎样分开

## 状态

- 计划日期：2026-11-17。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`。先读已有 `training.json`。

## 本地划分

```text
scene 0 / d27157fded0ddd5e
  -> expert rollout
  -> 80 个有效观测 + 80 个动作标签
  -> 计算 feature mean/scale
  -> 训练 TinyPolicy

scene 1 / 543908aa309f5a0b
  -> 不进入训练
  -> 评估 expert 与 learned rollout
```

这是开发/验证划分，不是严格盲测：场景 1 已被我们反复查看。
因此可以分析错误，但不能据此声称正式泛化能力。

## 读取证据

```bash
cd /home/my/workspace/autodrive/my-ad
LAB_RUN=/home/my/workspace/autodrive/my-ad/artifacts/waymax/learning-20261107T105737Z-_rn5s941
cat "$LAB_RUN/training.json"
```

重点字段：

- `training_samples=80`：实际进入训练的样本数。
- `training_record.index=0`：训练场景。
- `evaluation_record.index=1`：评估场景。
- `feature_dimension=120`：输入维度。
- `scope`：明确没有泛化声明。

## 为什么不能随机打散后混在一起

如果同一场景的相邻帧同时出现在训练和验证里，验证可能只是记住相似片段，
不能代表换一段场景仍然有效。小样本课程不追求统计完美，但必须诚实记录划分。

## 验收

解释 `training_samples`、`training_record`、`evaluation_record` 的区别；
说明为什么“验证场景不是训练样本”仍不等于“严格盲测”。
