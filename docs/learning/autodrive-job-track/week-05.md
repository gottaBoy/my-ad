# Week 05：ROS 2 节点、消息、时间和坐标

## 状态

- 学习状态：`NOT_STARTED`
- 实验状态：`NOT_RUN`；若本机没有 ROS 2，先完成离线接口设计

## 目标

理解 ROS 2 是通信和生命周期框架，不是规划算法：

```text
state_publisher
  -> /vehicle/state
controller_node
  -> /vehicle/control
metrics_node
  -> /vehicle/metrics
```

## 消息设计

至少写明：

```text
timestamp_ns
frame_id
x_m, y_m, yaw_rad, speed_mps
acceleration_mps2, curvature_inv_m
```

每个字段都要有单位和有效范围。不要用没有说明的 `float64 value`。

## 有 ROS 2 时

实现三个小节点，并用 CLI 核对：

```bash
ros2 node list
ros2 topic list
ros2 topic echo /vehicle/state
ros2 topic hz /vehicle/state
```

## 没有 ROS 2 时

实现同样的接口协议，用 newline-delimited JSON 或 CSV 离线模拟：

```text
publisher.py -> state.jsonl -> controller.py -> control.jsonl
```

以后迁移 ROS 2 时保持消息字段、时间戳和错误语义不变。

## 验收

- 时间戳不倒退；
- frame_id 有明确约定；
- 缺字段会拒绝；
- 无订阅者时发布端不崩溃；
- 回放相同输入可重现相同控制输出。
