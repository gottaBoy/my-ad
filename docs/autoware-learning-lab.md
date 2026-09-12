# Autoware 模块学习实验

这份手册把仓库里的可执行 demo 映射到 Autoware 的数据流。每次实验都回答：

1. 当前模块收到了什么输入？
2. 当前模块发布了什么输出？
3. 这次运行证明了什么，哪些内容仍然没有验证？

学习顺序建议是：

```text
sensing -> localization -> perception -> fusion -> planning -> control
```

不要一开始同时修改地图、传感器、模型和控制器。每次只改变一个模块，
结果才容易定位。

## 1. 这些例子分别学习什么

仓库中的例子不是同一个层级的自动驾驶闭环。下面这张表规定每个例子
应该观察哪些模块，以及通过后能下什么结论：

| 例子 | 主要模块 | 输入 | 主要输出 | 能证明什么 |
|---|---|---|---|---|
| Autoware `planning_simulator` | localization、planning、control | 地图和内部运动学仿真 | TF、车辆状态、轨迹、控制命令 | DGX 上的 Autoware 基线和规划/控制接口 |
| Scenario Simulator | localization、planning、control | OpenSCENARIO、车辆状态、场景事件 | 场景状态、轨迹、控制接口 | 固定场景的 ROS 2 交互和场景回归 |
| AWSIM 或 CARLA | sensing、localization、perception、fusion、planning、control | 相机、LiDAR、GNSS/IMU、车辆状态 | 检测/融合对象、轨迹、车辆控制 | 真实传感器输入到车辆控制的在线闭环 |
| rosbag 回放 | sensing、localization、perception、fusion、planning | 已录制的传感器和车辆状态 | 可重复的离线 ROS 2 输出 | 算法输入、时间戳、TF 和结果的可重复调试 |
| NAVSIM | perception 输入、planning | OpenScene/nuPlan 数据和地图 | 离线轨迹与评测指标 | 规划 Agent 的离线评测，不是 ROS 在线闭环 |
| BEVFormer | perception、fusion | 多相机图像、标定和 checkpoint | BEV 特征、检测/跟踪结果 | 感知模型 forward 和部署性能 |
| Isaac Lab + RL | planning、control | 仿真状态、动作空间、奖励 | policy、动作、控制轨迹 | 策略训练和控制研究，不等于 Autoware 生产闭环 |
| `carla-air` | 取决于其内部封装 | 先确认 CARLA bridge 和传感器 schema | 先按 CARLA 契约接入 | 目前按 CARLA 适配器处理，仓库没有默认实现 |

因此，当前 DGX 单机最短路径是：

```text
Autoware planning_simulator
  -> localization / planning / control 观察
  -> Scenario Simulator 固定场景
  -> rosbag 回放或 AWSIM/CARLA 传感器
  -> perception / fusion
  -> NAVSIM、BEVFormer、Isaac Lab + RL 的专项实验
```

## 2. 第一次运行

以下命令都在 DGX Spark 的仓库目录执行：

```bash
cd ~/workspace/autodrive/my-ad

# 只在第一次执行；已有 .env 时不要覆盖自己的配置
test -f .env || cp .env.example .env
make init

make preflight
make harness-host
make harness-gpu
make harness-clock
```

每一步都应看到 `PASS`。没有 x86 AWSIM 主机时，网络 Harness 保持
`NOT-RUN`，不要把 `SIM_HOST_ADDR` 填成本机回环地址来伪造跨主机结果。

## 3. 跑通 Autoware 常驻 demo

```bash
make up-dgx
make harness-runtime SERVICE=autoware
```

预期的关键结果：

```text
PASS DGX mode=planning autoware is healthy
container_status=running
health_status=healthy
PASS harness test=runtime
```

查看容器和日志：

```bash
docker compose --env-file .env -f compose.dgx.yaml ps -a
docker compose --env-file .env -f compose.dgx.yaml \
  logs --no-color --tail=200 autoware
```

`make up-dgx` 启动的是 headless `planning_simulator`。它适合确认地图加载、
Autoware 进程、ROS 2/DDS 和业务健康探针，不会自动打开 RViz，也不等于
相机、LiDAR 和完整感知闭环。

## 4. 观察当前 ROS 图

```bash
make inspect-modules
```

这个命令按 sensing、localization、perception、fusion、planning、control
分组显示话题。它是观察工具，不是聚合通过 Gate：

- `PRESENT`：话题当前可发现。
- `MISSING`：当前运行模式没有这个话题。
- `module_status=OBSERVED`：至少有一个该模块话题被发现。
- `PRESENT` 不代表消息内容正确，也不代表算法已经产生有效结果。

只学习某个模块时，可以缩小输出：

```bash
make inspect-modules MODULE=sensing
make inspect-modules MODULE=localization
make inspect-modules MODULE=perception
make inspect-modules MODULE=fusion
make inspect-modules MODULE=planning
make inspect-modules MODULE=control
```

推荐按下面的顺序运行这些命令，而不是把六个模块一次性当作一个
“完整通过”：

1. `localization`：先看 TF、车辆位姿和速度。
2. `planning`：再看地图、路线和轨迹。
3. `control`：最后看车辆状态和控制命令。
4. 有相机或 LiDAR 输入后，再看 `sensing`。
5. 传感器检测结果出现后，再看 `perception`。
6. 同时存在多个传感器结果和标定 TF 后，才看 `fusion`。

查看完整节点和话题：

```bash
docker compose --env-file .env -f compose.dgx.yaml \
  --profile harness run --rm --no-deps ros-probe \
  bash -lc 'source /opt/ros/humble/setup.bash; ros2 node list; echo "--- topics ---"; ros2 topic list'
```

### 4.1 从“执行命令”变成“观察效果”

`inspect-modules` 只回答“ROS 图上有没有这些接口”。真正学习一个模块时，
继续运行：

```bash
make learn-module MODULE=localization DURATION=5
make learn-module MODULE=planning DURATION=5
make learn-module MODULE=control DURATION=5
```

命令结束时会打印类似：

```text
OBSERVED harness test=learning artifacts=artifacts/learning/20260912T080000Z-spark-dba5 detail=...
```

这就是本次学习结果所在目录。按以下顺序阅读：

```bash
latest="$(
  find artifacts/learning -mindepth 1 -maxdepth 1 -type d \
    | sort | tail -n 1
)"

# 1. 先看本轮结论和运行状态
cat "$latest/decision.md"

# 2. 再看提取后的关键指标
cat "$latest/metrics/"*-summary.txt

# 3. 最后看完整 publisher、subscriber、QoS 和消息摘要
less "$latest/logs/"*-observation.txt
```

目录内三个文件的职责不同：

| 文件 | 什么时候看 | 回答的问题 |
|---|---|---|
| `decision.md` | 第一眼 | 本轮观察的是哪个模块、是否完成、数据状态是什么 |
| `metrics/<module>-summary.txt` | 日常学习和前后对比 | 哪些 topic 有数据、频率多少、消息内容有什么 |
| `logs/<module>-observation.txt` | 深入排障 | 谁发布、谁订阅、QoS 是否兼容、完整观察过程是什么 |

这里使用 `OBSERVED`，而不是 `PASS`。观察到消息只能证明数据链路正在运行，
不能单独证明算法正确。

`learn-module` 在 Autoware 容器内完成六步观察：

```text
发现 topic
  -> 查看发布者和订阅者
  -> 抓取一条有界消息摘要
  -> 测量观测频率
  -> 改变一个输入
  -> 比较下游输出
```

先这样解读输出：

| 输出 | 含义 |
|---|---|
| `status=MISSING` | 当前 ROS 图没有该接口 |
| `Publisher count: 0` | 接口可能被发现，但没有节点发布 |
| `sample_status=NO_MESSAGE` | 观测窗口内没有收到真实消息 |
| `sample_status=RECEIVED` | 已收到消息，可以开始检查内容 |
| `message_count` | 窗口内收到的消息数量 |
| `observed_rate_hz` | 实测发布频率，不是配置文件中的目标值 |
| `sample_summary` | 首条消息的结构化摘要；大块图像和点云数据只显示长度 |
| `module_status=TOPICS_PRESENT_NO_MESSAGES` | 有接口，但本次没有采到运行时数据 |
| `module_status=MESSAGES_RECEIVED` | 至少一个该模块 topic 有真实消息 |

不同消息重点看：

- Camera：`header.stamp`、`frame_id`、`width`、`height`、`encoding`、
  `step` 和 `data.length`。
- LiDAR：`header.stamp`、`frame_id`、`width`、`height`、`fields.count`、
  `point_step`、`row_step` 和 `data.length`。
- Localization：位置、姿态、线速度、角速度以及 `map`、`odom`、
  `base_link` 是否属于同一 TF 树。
- Perception/Fusion：`objects.count`、首个对象的类别、位置、尺寸、速度，
  并通过 publisher 节点确认它究竟是单传感器结果还是融合结果。
- Planning：`route` 是否已生成、`trajectory.points.count`、首个轨迹点和
  速度字段。
- Control：控制命令中的转向、速度、加速度、制动字段及发布频率。

topic 存在只是接口证据，不是算法通过。一次有效实验至少保存两组输出：
改变输入前一组，改变输入后一组，然后比较消息数量、频率和内容变化。

### 4.2 当前 DGX 可以立即做的实验

常驻 `planning_simulator` 适合先看定位、规划和控制：

```bash
# 终端 1
make up-dgx

# 终端 2
make learn-module MODULE=localization DURATION=5
make learn-module MODULE=planning DURATION=5
make learn-module MODULE=control DURATION=5
```

这组实验能看到 TF、车辆运动状态、规划接口和控制接口。某个规划 topic
存在但没有消息时，通常表示路线、初始位姿或车辆状态还没准备好，应结合
publisher、日志和上游 topic 判断，不能只看名字。

要观察场景输入变化，使用两个终端并在场景仍运行时采样。一次场景先观察
一个模块，避免示例场景已经结束后才开始采样：

```bash
# 终端 1
make scenario-up

# 终端 2，按本轮目标选择一个
make learn-module MODULE=planning DURATION=8
before="$(
  find artifacts/learning -mindepth 1 -maxdepth 1 -type d \
    | sort | tail -n 1
)"
# 或
make learn-module MODULE=control DURATION=8
```

先保留一份原始输出。然后把
`config/scenario/sample-scenario.yaml` 中目标车道 `laneId: '112'` 对应的
两处目标 `s: 18` 同时改成同一条车道内较近的值，例如 `s: 14`，重新运行
场景和同一模块：

```bash
make scenario-up
make learn-module MODULE=planning DURATION=8
after="$(
  find artifacts/learning -mindepth 1 -maxdepth 1 -type d \
    | sort | tail -n 1
)"
diff -u \
  "$before/metrics/planning-summary.txt" \
  "$after/metrics/planning-summary.txt"
```

比较两次 `trajectory.points`、速度字段和 `control_cmd`，这才是在观察
规划/控制对输入变化的反应。完成实验后恢复场景基线，避免后续结果混用
不同场景。

当前 `planning_simulator` 和 Scenario baseline 没有真实相机/LiDAR 输入，
所以：

```bash
make learn-module MODULE=sensing DURATION=8
```

预期可能得到：

```text
module_status=NO_RUNTIME_INPUT
conclusion=no effect can be observed for this module in the current runtime
```

这不是 sensing 失败，而是当前 demo 没有传感器数据源。要观察 sensing、
perception 和 fusion，必须接入 AWSIM/CARLA，或回放包含相机、点云、标定
和 TF 的 rosbag。

### 4.3 理解概念和逻辑的五层阅读法

不要直接从算法源码开始。每个模块都按下面五层向下学习：

| 层次 | 核心概念 | 在结果里看什么 | 理解标准 |
|---|---|---|---|
| 1. ROS 图 | node、topic、publisher、subscriber、message type | `topic info --verbose` | 能说清楚谁产生数据、谁消费数据 |
| 2. 数据契约 | 字段、单位、时间戳、`frame_id` | `sample_summary` | 能解释主要字段代表的物理意义 |
| 3. 时空关系 | ROS time、频率、延迟、TF、标定 | `observed_rate_hz`、`header.stamp`、`/tf` | 能判断数据是否在同一时间和坐标系 |
| 4. 算法因果 | 输入、参数、输出、下游影响 | 修改前后的 summary diff | 能预测改变一个输入后哪些输出应变化 |
| 5. 工程质量 | 抖动、丢帧、超时、资源、可重复性 | 多轮 artifacts、日志、GPU/CPU 指标 | 能建立阈值并做自动回归 |

最重要的逻辑不是记 topic 名，而是画出模块的因果链：

```text
传感器消息
  -> 定位和感知
  -> 融合后的环境状态
  -> route / behavior / trajectory
  -> control command
  -> 车辆状态变化
  -> 新一轮传感器消息
```

排障时反向追踪。例如没有 `control_cmd`：

```text
control_cmd 没消息
  <- trajectory 是否有消息
  <- route 和 ego state 是否准备好
  <- localization 和 TF 是否连通
  <- simulator/rosbag 是否真的在发布输入
```

### 4.4 从入门到精通的学习路线

| 阶段 | 动手内容 | 需要掌握 | 完成标志 |
|---|---|---|---|
| 入门 1 | `inspect-modules` | ROS node/topic/type | 能解释 `PRESENT` 和 `MISSING` |
| 入门 2 | `learn-module` | publisher/subscriber、消息字段、频率 | 能从 artifacts 找到一条真实消息 |
| 基础 | localization -> planning -> control | TF、位姿、轨迹、控制量 | 能手工画出三个模块的数据链 |
| 进阶 | 修改 Scenario 单一变量并做 diff | 控制变量、上游/下游、因果关系 | 能解释轨迹或控制为何改变 |
| 高阶 | rosbag 回放 sensing/perception/fusion | 时间同步、QoS、标定、检测和融合 | 同一 bag 多次运行结果可比较 |
| 专家 | 调参数、替换节点/模型、建立指标 | 算法原理、延迟预算、故障模式、回归 Gate | 变更有量化指标和自动化证据 |

建议一次只学一个问题：

1. 数据有没有？
2. 数据是谁产生的？
3. 字段和坐标系是什么意思？
4. 频率和时间戳是否合理？
5. 改一个输入后，哪个下游输出改变？
6. 改变是否符合算法预期？
7. 多次运行能否复现？

### 4.5 什么时候使用可视化

终端和 artifacts 用来确认事实，可视化用来建立空间直觉。配置好可选
Foxglove profile 后执行：

```bash
make viz
```

在 Foxglove 客户端中逐步添加：

```text
/map/vector_map
/tf 和 /tf_static
/localization/kinematic_state
/planning/trajectory
/perception/object_recognition/objects
相机 Image
LiDAR PointCloud2
/control/command/control_cmd 的 Plot
```

先只显示地图、TF 和轨迹；确认坐标关系后，再叠加对象、点云和图像。
画面“看起来正常”仍然要用 artifacts 中的频率、时间戳和发布者信息验证。

## 5. 先学规划和控制

这是当前 DGX 上最容易复现的模块学习路径。准备并执行固定场景：

```bash
make scenario-prepare
make scenario
```

成功标准：

```text
[simulation.openscenario_interpreter]: Passed
my-ad-scenario exited with code 0
```

场景结束后查看报告：

```bash
docker compose --env-file .env -f compose.dgx.yaml ps -a
find data/reports/scenario -maxdepth 3 -type f -print
```

### 5.1 规划输出

规划主要关注地图、定位、路线、障碍物和轨迹：

```text
/map/vector_map
/localization/kinematic_state
/planning/mission_planning/route
/planning/trajectory
```

场景运行期间，在另一个终端执行：

```bash
docker compose --env-file .env -f compose.dgx.yaml \
  exec autoware bash -lc '
    source /opt/autoware/setup.bash
    ros2 topic info /map/vector_map
    ros2 topic info /planning/trajectory
    timeout 30 ros2 topic echo --once /planning/trajectory
  '
```

如果 `/planning/mission_planning/route` 没有消息，不要立刻认为 Autoware
坏了。Mission Planner 需要收到起点和目标点后才会产生路线。先以固定场景
结果和 `/planning/trajectory` 的实际消息为学习入口。

### 5.2 控制输出

控制把规划轨迹和车辆运动状态转换成车辆控制命令：

```bash
docker compose --env-file .env -f compose.dgx.yaml \
  exec autoware bash -lc '
    source /opt/autoware/setup.bash
    ros2 topic info /vehicle/status/velocity_status
    ros2 topic info /control/command/control_cmd
    timeout 30 ros2 topic echo --once /control/command/control_cmd
  '
```

重点观察：

- 控制命令是否有时间戳。
- 转向、速度、加速度或制动字段是否有有效值。
- 命令是否持续发布，而不是只出现一次。
- 车辆状态和 TF 是否与控制周期同步。

查看发布频率：

```bash
docker compose --env-file .env -f compose.dgx.yaml \
  exec autoware bash -lc '
    source /opt/autoware/setup.bash
    timeout 10 ros2 topic hz /planning/trajectory
    timeout 10 ros2 topic hz /control/command/control_cmd
  '
```

当前 Scenario Simulator demo 证明的是场景解释器与 Autoware 的 ROS 2
交互以及规划/控制接口存在。只有看到控制命令被仿真车辆消费并产生预期
运动，才能进一步称为控制闭环。

### 5.3 学习定位

在 `make up-dgx` 或 Scenario 运行期间，先观察定位相关输出：

```bash
make inspect-modules MODULE=localization

docker compose --env-file .env -f compose.dgx.yaml \
  exec autoware bash -lc '
    source /opt/autoware/setup.bash
    ros2 topic info /tf
    ros2 topic info /tf_static
    timeout 10 ros2 topic echo --once /tf
    timeout 10 ros2 topic echo --once /localization/kinematic_state
  '
```

这里先验证的是坐标系、时间戳、车辆位姿和速度是否存在。当前
`planning_simulator` 使用内部运动学输入，不能把它等同于 NDT、GNSS、
IMU 或视觉定位算法已经通过。

## 6. 再学感知

当前 DGX 单机 Scenario Simulator 合约故意不要求相机和 LiDAR 话题。因此
下面的命令在现有 demo 中可能显示 `MISSING`，这是当前 demo 的边界。

有 AWSIM、CARLA 或包含真实传感器话题的 rosbag 后，先检查原始输入：

```bash
make harness-ros

docker compose --env-file .env -f compose.dgx.yaml \
  exec autoware bash -lc '
    source /opt/autoware/setup.bash
    ros2 topic info /sensing/camera/camera0/image_raw
    ros2 topic info /sensing/lidar/top/pointcloud_raw
    timeout 10 ros2 topic echo --once /sensing/camera/camera0/image_raw
    timeout 10 ros2 topic echo --once /sensing/lidar/top/pointcloud_raw
  '
```

感知输出可以从对象话题开始检查：

```bash
docker compose --env-file .env -f compose.dgx.yaml \
  exec autoware bash -lc '
    source /opt/autoware/setup.bash
    ros2 topic info /perception/object_recognition/objects
    timeout 10 ros2 topic echo --once /perception/object_recognition/objects
    timeout 10 ros2 topic hz /perception/object_recognition/objects
  '
```

学习感知时按这个顺序：

1. 先确认图像或点云消息真实到达。
2. 再确认消息时间戳、`frame_id` 和频率。
3. 再确认检测目标的类别、位置、尺寸、速度和置信度。
4. 最后比较模型精度、延迟和 GPU 占用。

没有原始传感器输入时，不能把对象话题的缺失或静态话题当成感知模型通过。

## 7. 最后学融合

融合不是一个固定名字的单一 ROS 话题。不同 sensor kit 可能采用不同的
检测、跟踪、关联和融合节点，最终仍可能输出到同一个
`/perception/object_recognition/objects`。

先看候选节点和发布者：

```bash
docker compose --env-file .env -f compose.dgx.yaml \
  --profile harness run --rm --no-deps ros-probe \
  bash -lc '
    source /opt/ros/humble/setup.bash
    ros2 node list | grep -Ei "fusion|detection|tracking|association" || true
    ros2 topic info --verbose /perception/object_recognition/objects
  '
```

融合实验至少要同时记录：

```text
相机或 LiDAR 原始消息
预处理后的传感器消息
各传感器到 base_link 的 TF
检测结果和融合结果
时间戳、QoS、频率和延迟
```

推荐依次做：

1. 单传感器检测。
2. LiDAR 加相机融合。
3. 融合结果进入规划。
4. 控制命令返回仿真车辆。

`fusion` 的学习入口必须放在 `sensing` 和 `perception` 之后。仅仅发现
`/perception/object_recognition/objects` 这个名字，不能证明它是多传感器
融合结果；必须通过 `ros2 topic info --verbose` 找到发布节点，并同时检查
多路输入、时间同步、TF 和目标数量/置信度变化。

## 8. 把模块放回各个专项例子

### 8.1 AWSIM 或 CARLA

有 x86 AWSIM 主机或 CARLA bridge 后，运行期间在 DGX 观察：

```bash
make inspect-modules MODULE=sensing
make inspect-modules MODULE=localization
make inspect-modules MODULE=perception
make inspect-modules MODULE=fusion
make inspect-modules MODULE=planning
make inspect-modules MODULE=control
```

逐项确认：

```text
相机/LiDAR原始话题
  -> 预处理话题
  -> 检测和跟踪对象
  -> 融合对象
  -> trajectory
  -> control_cmd
  -> 仿真车辆状态
```

若使用 CARLA，先做 CARLA 单机、传感器、ROS 2 bridge、车辆控制四个
小实验，再接 Autoware。`carla-air` 如果是内部封装，也必须先把它归入
这条 CARLA bridge 路线，补齐实际 topic、版本和镜像后才能成为仓库 demo。

### 8.2 rosbag 回放

当没有可用仿真器时，可以用带传感器话题的 rosbag 学习感知和融合：

```bash
# 先把 REPLAY_BAG=/data/bags/<run> 写入 .env
# 终端 1
make up-dgx

# 终端 2
make replay

# 终端 3，在回放期间采样
make learn-module MODULE=sensing DURATION=10
make learn-module MODULE=localization DURATION=10
make learn-module MODULE=perception DURATION=10
make learn-module MODULE=fusion DURATION=10
```

回放期间另开终端：

```bash
make inspect-modules MODULE=sensing
make inspect-modules MODULE=localization
make inspect-modules MODULE=perception
make inspect-modules MODULE=fusion
```

rosbag 必须包含原始图像/点云、`camera_info`、车辆状态和 TF；只有规划
轨迹的 bag 不能用来学习感知或融合。

### 8.3 NAVSIM、BEVFormer、Isaac Lab + RL

这三个例子分别服务不同模块：

```text
NAVSIM       -> 离线 perception 输入和 planning 轨迹评测
BEVFormer    -> perception/fusion 模型 forward、导出和部署
Isaac Lab RL -> planning/control policy 训练和动作约束
```

它们的最小进入顺序是：

1. NAVSIM：先跑固定 `mini` split，再比较轨迹指标。
2. BEVFormer：先单帧 checkpoint forward，再做多相机时间序列和 ROS 2 节点。
3. Isaac Lab + RL：先验证 `reset()`/`step()`，再训练低维控制策略，最后做
   planner/controller adapter。

这些实验的结果要分别保存为离线评测、模型推理和策略训练证据，不能用
`make harness-runtime SERVICE=autoware` 或 `make scenario` 代替。

## 9. 模块和当前 demo 的对应关系

| 模块 | 先看什么 | 当前入口 | 当前边界 |
|---|---|---|---|
| sensing | Image、PointCloud2、时间戳、TF | `make learn-module MODULE=sensing DURATION=8` | 需要 AWSIM/CARLA 或带原始传感器话题的 rosbag |
| localization | `/tf`、`/localization/kinematic_state`、地图坐标 | `make learn-module MODULE=localization DURATION=5` | 当前 baseline 是内部运动学输入，真实 NDT/GNSS/IMU 仍需传感器 |
| perception | 检测、分类、跟踪对象 | `make learn-module MODULE=perception DURATION=8` | 当前单机场景不提供 AWSIM 相机/LiDAR |
| fusion | 多传感器同步、标定、关联、融合对象 | `make learn-module MODULE=fusion DURATION=8` | 需要锁定 sensor kit 和融合节点 |
| planning | route、behavior、motion、trajectory | `make scenario-up`、`make learn-module MODULE=planning DURATION=8` | 路线请求和动态障碍要单独构造 |
| control | trajectory、车辆状态、control command | `make scenario-up`、`make learn-module MODULE=control DURATION=8` | 需要验证命令被仿真车辆消费 |

## 10. 逐级进阶

```text
L1 地图 + 定位 + 规划轨迹
L2 轨迹 + 控制命令
L3 单 LiDAR 感知
L4 单相机感知
L5 LiDAR/相机融合
L6 感知结果进入规划
L7 控制命令返回 AWSIM/CARLA 车辆
L8 录包、回放和重复运行
L9 BEVFormer 替换感知节点
L10 Isaac Lab/RL 替换或增强规划/控制模块
```

每一级都保留 `artifacts/` 证据。不要用容器 `Up`、单个 topic 可发现或模型
forward 成功替代完整闭环证据。
