# Week 06：Waymax 到 C++ 的离线评测管线

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`
- 前置：Waymax baseline 已存在

## 目标

建立边界清楚的桥：

```text
Waymax trajectory.csv
  -> schema 校验
  -> C++ VehicleState
  -> controller
  -> control_output.csv
  -> Python 指标脚本
```

这不是 CARLA bridge，也不是传感器驱动，只是离线文件管线。

## 输入契约

必须检查：

- 表头包含 `step,timestep,x_m,y_m`；
- 动作列存在且为有限数；
- timestep 严格递增；
- 单位列名明确；
- 输入文件 SHA256 被记录。

## 输出契约

至少输出：

```text
input_sha256
source_revision
config_sha256
rows
first_timestep
last_timestep
mean_tracking_error_m
invalid_rows
```

## 验收

对同一个 `expert-0/trajectory.csv` 运行两次，输出的结构化字段一致。
构造缺列、空文件、NaN、倒退 timestep 四种坏输入，程序必须拒绝并给原因。
