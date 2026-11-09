# Week 02：坐标系与 bicycle model

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`
- 前置：Week 01 的 C++ 测试通过

## 目标

能解释并实现：

```text
map/global frame：全局位置
vehicle frame：车辆自身坐标
yaw：车辆朝向
curvature：转向曲率
dt：时间间隔
```

## 关键公式

使用本仓库 Day 05 的模型：

```text
new_x   = x + vel_x*dt + 0.5*a*cos(yaw)*dt²
new_y   = y + vel_y*dt + 0.5*a*sin(yaw)*dt²
new_yaw = yaw + k*(speed*dt + 0.5*a*dt²)
new_v   = speed + a*dt
```

其中 `a` 单位为 `m/s²`，`k` 单位为 `m⁻¹`，`dt` 单位为秒。

## 练习

在 `projects/job-track/cpp-basics/` 旁边创建
`projects/job-track/bicycle-model/`，包含：

```text
include/bicycle_model.hpp
src/bicycle_model.cpp
tests/bicycle_model_test.cpp
CMakeLists.txt
```

实现 `step`，并满足：

- `dt > 0`，否则拒绝；
- 加速度范围 `[-6, 6]`；
- 曲率范围 `[-0.3, 0.3]`；
- 非有限输入拒绝；
- 速度小于零时明确选择“拒绝”或“裁剪”，并写测试。

## 手算验证

初始状态 `(x=0,y=0,yaw=0,speed=8)`、`dt=0.1`：

```text
[0, 0]     -> x 增加约 0.8 m
[-2, 0]    -> 速度约 7.8 m/s
[0, 0.05]  -> yaw 改变约 0.04 rad
```

数值允许有浮点误差，但必须解释误差来源。

## 验收

至少覆盖直行、减速、转弯、超限、NaN、`dt=0` 六类测试。
把 80 步状态写成 CSV，列名必须带单位。
