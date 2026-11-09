# Day 06：指标不是一个“总分”

## 状态

- 计划日期：2026-11-13。
- 学习状态：`NOT_STARTED`；学习者命令：`NOT_RUN`；理解：`NOT_VERIFIED`。
- 实验预期：`READING_ONLY`。不新增仿真目录。

## 今天学什么

每一步 rollout 都会得到四个 SDC 指标。它们回答不同问题，不能把四个数
相加后称为“驾驶总分”：

| 指标 | 中文 | 单位/取值 | 问题 |
| --- | --- | --- | --- |
| `log_divergence` | 日志轨迹偏差 | 米 `m` | 仿真自车离同一时刻日志位置多远？ |
| `offroad` | 越出道路 | `0` 或 `1` | 这一帧是否被标记为越界？ |
| `overlap` | 包围盒重叠 | `0` 或 `1` | 这一帧是否与其它对象重叠？ |
| `kinematic_infeasibility` | 运动学不可行 | 本地实现的数值 | 这一步是否超过动力学可行范围？ |

`summary.json` 的 `mean` 是**有效帧的平均值**，`max` 是有效帧最大值，
`valid_frames` 告诉你这个平均值到底用了多少帧。

例如已有 expert-0：

```text
log_divergence.mean ≈ 0.016418 m
offroad.mean = 0
overlap.mean = 0
每项 valid_frames = 80
```

这里的 `offroad.mean=0` 表示 80 个有效帧中平均指示为 0；
不能把它读成“现实世界永远安全”。

## 只读操作

```bash
cd /home/my/workspace/autodrive/my-ad
RUN=artifacts/waymax/learning-20261107T163242Z-3nn37_xl
cat "$RUN/expert-0/summary.json"
cat "$RUN/expert-1/summary.json"
```

建议再用普通 Python 只读 CSV 复算 `log_divergence` 平均值，目标是确认
`summary.json` 的数字来自 CSV，而不是手工填写。不要把 `null` 转成 0。

## 重要边界

- `valid_frames=80` 是本实验 SDC 统计的有效帧数，不是对象总数。
- `log_divergence` 是位置距离，不是速度误差、碰撞概率或成功率。
- `offroad` 和 `overlap` 是本地指标定义下的指示量，不是法律或安全认证。
- `is_done=true` 只说明到达时间末尾；指标要另外读取。

## 验收

1. 为什么 `mean=0` 仍要看 `valid_frames`？
2. `0.016 m` 和 `0.016` 的单位是什么？
3. `is_done=true` 与 `offroad.mean=0` 分别说明什么？

本课只读已有 baseline；下一课汇总第一周的完整链路。
