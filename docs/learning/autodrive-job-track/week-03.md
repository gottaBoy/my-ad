# Week 03：PID、Pure Pursuit 和 Stanley

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`
- 前置：Week 02 的 bicycle model 测试通过

## 目标

理解控制器的闭环：

```text
当前状态 + 目标轨迹
  -> 误差
  -> 控制器
  -> acceleration/curvature
  -> vehicle model
  -> 新状态
```

## 关键词

| English | 中文 |
| --- | --- |
| longitudinal control | 纵向控制 |
| lateral control | 横向控制 |
| PID | 比例-积分-微分 |
| Pure Pursuit | 纯跟踪 |
| Stanley | 基于横向误差和航向误差的控制 |
| saturation | 饱和/限幅 |

## 练习顺序

1. 只做速度 PID，目标速度 8 m/s。
2. 只做直线横向控制。
3. 加入弯道参考点。
4. 组合纵向和横向控制。
5. 统计控制量触碰边界的次数。

不要一开始实现三种控制器。先让 PID 在直线上稳定，再复制接口：

```text
ControlCommand compute(const VehicleState&, const Reference&);
```

## 测试场景

- 初始速度低于目标：输出正加速度；
- 初始速度高于目标：输出负加速度；
- 车辆在轨迹左侧：曲率方向符合坐标约定；
- 空参考轨迹：返回明确错误；
- 目标距离为零：不产生 NaN；
- 控制输出超界：被限幅并计数。

## 验收

输出 `tracking_error.csv` 和 `controller_summary.json`，至少有：

```text
mean_abs_lateral_error_m
max_abs_lateral_error_m
mean_speed_error_mps
accel_saturation_count
curvature_saturation_count
```

能解释：控制器收敛、震荡、饱和分别是什么。
