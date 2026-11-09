# Week 08：闭环错误分析

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`

## 目标

将“模型效果不好”拆成可定位证据：

```text
summary 指标
  -> CSV 逐帧
  -> 首次坏帧
  -> 动作/状态窗口
  -> 假设
  -> 下一项单变量实验
```

## 练习

对照四条轨迹：

```text
expert
brake
learned training
learned evaluation
```

逐帧导出：

```text
timestep
accel_m_s2
curvature_inv_m
log_divergence_m
offroad
overlap
```

生成 `failure-report.md`，分成：

1. 观察事实；
2. 当前推断；
3. 未知项；
4. 下一实验。

## 验收

必须定位第一次 `offroad=1` 或偏差超过指定阈值的帧。
没有坏帧时也要如实写“未发现”，不能伪造失败。
