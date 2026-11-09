# Week 04：路径、轨迹、速度和碰撞检查

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`

## 目标

把三个层次分开：

```text
Path       几何路线
Speed      路线上每个时刻的速度
Trajectory 带时间的 x/y/yaw/speed/action
```

## 练习

实现一个离线 planner：

```text
输入：
  当前 VehicleState
  一条带点的参考路径
  障碍物矩形列表

输出：
  3 秒、dt=0.1 的候选轨迹
  或明确的 NoFeasibleTrajectory 错误
```

按以下顺序完成：

1. 最近路径点；
2. 前视点；
3. 目标速度；
4. 调用 Week 03 controller；
5. 每步碰撞检查；
6. 失败原因和首次失败 timestep。

## 碰撞检查约定

先明确这是教学近似：

- 车辆用轴对齐矩形；
- 障碍物也用轴对齐矩形；
- 不声称等价于真实碰撞检测；
- 单位统一为米。

## 验收

必须有三个 fixture：

1. 无障碍物，轨迹成功；
2. 正前方障碍物，返回不可行；
3. 旁边障碍物，不误报。

输出 `planner_result.json` 和一张轨迹图。
