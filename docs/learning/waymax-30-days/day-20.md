# Day 20：从汇总指标定位第一处坏帧

## 状态

- 计划日期：2026-11-27。
- 学习状态：`NOT_STARTED`；命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`，先读已有 CSV。

## 已知坏例

已有 `learned-evaluation/trajectory.csv` 中，第一次 `offroad=1` 是：

```text
step=29, timestep=39
log_divergence≈12.0386 m
accel≈4.6711 m/s²
curvature≈0.07105 m⁻¹
```

之后到末尾共有 52 个越界帧，平均偏差约 15.748 m。
这是定位结果，不是新的运行。

## 只读定位命令

```bash
cd /home/my/workspace/autodrive/my-ad
CSV=artifacts/waymax/learning-20261107T105737Z-_rn5s941/learned-evaluation/trajectory.csv
awk -F, 'NR==1 || $9 != "0.0"' "$CSV" | head
```

再查看前后窗口，比较 timestep、动作和偏差。不要只看最后一行，
因为最后一行无法告诉你错误何时开始。

## 观察与推断分开

- 观察：第 29 步开始 `offroad=1`。
- 观察：此前偏差已逐渐增加。
- 推断：可能存在闭环误差积累。
- 未知：具体是哪个输入特征、模型容量或动力学误差造成。

## 验收

给出首次坏帧的 `step/timestep`，并写出一个事实和一个尚未证实的假设。
