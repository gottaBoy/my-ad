# 仿真与模型路线

校核日期：2026-09-11

本文把本仓库的 Autoware 运行方式和后续仿真、感知、训练路线分开记录。
每一级都说明它证明了什么、没有证明什么，以及下一步如何执行。不要用较低
级别的 `PASS` 替代更高级别的闭环证据。

## 1. 先看结论

如果目标是边运行边学习 sensing、localization、perception、fusion、
planning 和 control，请先看
[`autoware-learning-lab.md`](autoware-learning-lab.md)。本文保留运行模式、
证据等级和仿真器边界；学习手册提供逐条命令和 topic 观察方法。

当前 DGX Spark 已经有以下实机证据：

| 能力 | 证据 | 结论 |
|---|---|---|
| DGX 主机和 Docker | `artifacts/host/20260910T142240Z-spark-dba5` | `PASS` |
| NVIDIA 容器 CUDA 运算 | `artifacts/gpu/20260910T144100Z-spark-dba5` | `PASS` |
| Autoware ARM64 运行态 | `artifacts/runtime/20260911T055823Z-spark-dba5` | `PASS` |
| `planning_simulator` 地图与业务探针 | 同一 runtime 证据 | `PASS` |
| DGX 时钟同步 | `artifacts/clock/20260911T110705Z-spark-dba5` | `PASS` |
| x86_64 AWSIM 与 DGX 的跨主机闭环 | 尚无对端主机 | `NOT-RUN` |
| BEVFormer 接入 Autoware | 尚无模型、推理节点和话题契约 | `BLOCKED` |
| Isaac Lab RL 闭环 | 尚无环境、策略和 adapter | `BLOCKED` |

因此，当前最稳妥的路线是：

```text
L0 环境证据
  -> L1 DGX 单机 Autoware planning_simulator
  -> L2 DGX 单机 Scenario Simulator
  -> L3 x86_64 AWSIM + DGX Spark Autoware
  -> L4 CARLA + Autoware
  -> L5 NAVSIM 离线规划评测
  -> L6 BEVFormer 感知模型
  -> L7 Isaac Lab + RL
```

`planning_simulator` 能证明 Autoware、地图、DDS、GPU 容器和业务健康探针
能够在 DGX Spark 上运行；它不能证明 AWSIM、相机、LiDAR、完整感知、跨主机
网络或真实传感器闭环。

各例子与学习模块的对应关系如下：

| 例子 | sensing | localization | perception | fusion | planning | control |
|---|---|---|---|---|---|---|
| Autoware `planning_simulator` | - | 可观察 | 可观察接口 | - | 可运行 | 可观察 |
| Scenario Simulator | 场景输入有限 | 可观察 | - | - | 固定场景 | 场景接口 |
| AWSIM / CARLA | 原始传感器 | 可运行 | 可运行 | 可运行 | 可运行 | 可闭环 |
| rosbag 回放 | 可重复输入 | 可重复调试 | 可重复调试 | 可重复调试 | 可重复调试 | 可重复调试 |
| NAVSIM | 离线输入 | 数据提供 | 离线输入 | 取决于 Agent | 离线评测 | 不直接接入 |
| BEVFormer | 图像输入 | 依赖外部 TF | 模型主体 | 可扩展 | 不负责 | 不负责 |
| Isaac Lab + RL | 环境状态 | 环境提供 | 不负责 | 不负责 | 可训练 | 可训练 |

“可观察”只表示当前运行态有对应 ROS 接口，不表示算法结果已经正确；
`-` 表示该例子默认不提供这类输入或模块。逐条命令、观察 topic 和证据
边界见 [`autoware-learning-lab.md`](autoware-learning-lab.md)。

## 2. 通用规则

每一级使用独立的镜像、命令、健康探针和证据目录。容器显示 `Up` 只表示
进程还在，不能单独作为业务通过标准。

运行前固定：

```text
镜像 tag 和 digest
源码 git ref
地图和数据版本
ROS_DOMAIN_ID、RMW 和 DDS 配置
车辆模型和 sensor kit
随机种子
评测 split、checkpoint 和命令
```

出现 `FAIL` 或 `BLOCKED` 时，保留 `artifacts/` 证据并停在当前级别。不要
为了让后续命令通过而把真实失败改写成 `PASS`。

## 3. L0：宿主机和容器基线

目标是确认 DGX Spark 具备运行条件，不启动完整自动驾驶闭环。

在 DGX Spark 执行：

```bash
make collect-env
make test-local
make test-compose
make preflight
make harness-host
make harness-gpu
make harness-clock
```

网络 Gate 需要另一台主机运行：

```bash
iperf3 -s -p 5201
```

然后 DGX 执行：

```bash
make harness-network
```

如果没有 x86 主机，网络 Gate 应标记为 `NOT-RUN`，不要填入虚假的
`SIM_HOST_ADDR` 或用本机回环地址代替对端。

## 4. L1：DGX 单机 Autoware planning_simulator

这是当前已经通过的最低可用运行基线。

```bash
make preflight
make up-dgx
make harness-runtime SERVICE=autoware
```

推荐同时保存：

```bash
docker compose --env-file .env -f compose.dgx.yaml ps -a
docker compose --env-file .env -f compose.dgx.yaml logs --no-color --tail=200 autoware
make harness-ros
```

当前样例配置使用：

```dotenv
AUTOWARE_IMAGE=<已验证的 ARM64 Autoware 镜像>@sha256:<digest>
AUTOWARE_COMMAND='source /opt/autoware/setup.bash && exec ros2 launch autoware_launch planning_simulator.launch.xml map_path:=/data/maps vehicle_model:=sample_vehicle sensor_model:=sample_sensor_kit rviz:=false'
AUTOWARE_HEALTHCHECK_COMMAND='source /opt/autoware/setup.bash && ros2 topic list | grep -qx /map/vector_map'
```

通过后可以说：

- Autoware ARM64 容器能够启动。
- 地图可以加载并发布。
- DDS/ROS 2 进程可以在当前 DGX 容器网络配置中建立通信。
- GPU 容器能力已通过单独的 CUDA smoke test。
- Autoware 的配置业务探针为健康。

还不能说：

- AWSIM 已经运行。
- 相机或 LiDAR 输入已经接入。
- 感知、规划和控制形成真实传感器闭环。
- 跨主机 ROS 2 通信已经通过。

## 5. L2：DGX 单机 Scenario Simulator

Scenario Simulator 适合在没有 x86 AWSIM 主机时验证 Autoware 的场景级
回归。它与 `planning_simulator` 是两种不同的仿真输入路径。

准备并运行一个场景：

```bash
make scenario-prepare
make scenario
```

如果要观察场景运行期间的话题，应在另一个终端执行
`make harness-ros-scenario` 或 `make inspect-modules`。`make scenario` 是一次性
前台命令，场景结束后容器会停止；场景结束后再运行 ROS discovery probe，
通常已经没有可发现的场景话题。

`make scenario` 的行为是：

1. 使用 `AUTOWARE_SCENARIO_COMMAND` 启动 Autoware。
2. 让 Autoware 使用 `scenario_simulation:=true`。
3. 等待 Autoware 的业务健康探针。
4. 使用 `launch_autoware:=false` 启动外部 Scenario Simulator。
5. 前台执行单个场景，并返回场景进程退出码。

默认配置入口在 `.env.example`：

```dotenv
SCENARIO_IMAGE=ghcr.io/tier4/scenario_simulator_v2:humble-25.0.22-runtime
AUTOWARE_SCENARIO_COMMAND='source /opt/autoware/setup.bash && exec ros2 launch autoware_launch planning_simulator.launch.xml map_path:=/data/maps vehicle_model:=sample_vehicle sensor_model:=sample_sensor_kit scenario_simulation:=true rviz:=false'
SCENARIO_COMMAND='exec ros2 launch scenario_test_runner scenario_test_runner.launch.py architecture_type:=awf/universe/20250130 record:=false use_custom_centerline:=true scenario:=/config/scenario/sample-scenario.yaml vehicle_model:=sample_vehicle sensor_model:=sample_sensor_kit launch_autoware:=false launch_rviz:=false initialize_duration:=120 output_directory:=/data/reports/scenario'
```

正式使用前在 DGX Spark 验证镜像架构并固定 digest：

```bash
docker pull "$SCENARIO_IMAGE"
docker image inspect "$SCENARIO_IMAGE" \
  --format 'os={{.Os}} arch={{.Architecture}} digests={{json .RepoDigests}}'
```

Scenario contract 当前检查：

```text
/clock
/tf
/tf_static
/vehicle/status/velocity_status
/planning/trajectory
/control/command/control_cmd
```

这一级证明的是场景解释器与 Autoware 的基本 ROS 2 场景交互，不等于
AWSIM 的相机/LiDAR、Unity 渲染或完整真值链路。不要同时启动 Autoware
内部的 planning simulator 输入和外部 Scenario Simulator 输入来冒充闭环；
Autoware 必须使用 scenario 模式，Scenario Simulator 必须设置
`launch_autoware:=false`。

## 6. L3：x86_64 AWSIM + DGX Spark Autoware

这是需要两台机器的正式高保真主路径。

### 6.1 x86_64 Linux AWSIM 主机

前置条件：

```text
x86_64 Linux
NVIDIA RTX 或兼容 GPU
Docker、Compose v2、NVIDIA Container Toolkit
已验证的 AWSIM x86_64 Linux Player 镜像
与 DGX Spark 的可达网络
```

启动并检查：

```bash
make preflight
make harness-host
make harness-gpu
make up-sim
make harness-runtime SERVICE=awsim
```

### 6.2 DGX Spark

确保 `.env` 中：

```dotenv
HOST_ROLE=dgx
SIM_HOST_ADDR=<x86 主机真实可达 IP>
DGX_HOST_ADDR=<DGX Spark 真实可达 IP>
ROS_DOMAIN_ID=42
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

启动 Autoware：

```bash
make preflight
make up-dgx
make harness-runtime SERVICE=autoware
```

### 6.3 跨主机 Gate

x86 主机：

```bash
iperf3 -s -p 5201
```

DGX：

```bash
make harness-network
make harness-clock
make harness-ros
```

完整闭环还必须确认：

```text
AWSIM 相机和 LiDAR 话题
AWSIM /clock
TF 树和 frame_id
车辆状态与 odometry
Autoware trajectory
Autoware control command 返回 AWSIM
固定场景可重复运行
```

在这些证据出现前，L3 状态只能是 `NOT-RUN` 或 `CONDITIONAL`。没有
x86_64 主机时，L3 无法在 DGX 单机上凭空完成。

## 7. L4：CARLA + Autoware

CARLA 是 AWSIM 的替代仿真路线之一，不是 AWSIM 的同义词。推荐按以下
顺序逐步增加复杂度。

### 7.1 CARLA 单机

先只验证：

```text
CARLA Server 能启动
Python API 能连接
固定地图能加载
固定随机种子和同步模式生效
车辆能生成、重置和销毁
```

此阶段不接 Autoware，不接 BEVFormer。

### 7.2 CARLA 传感器和控制

增加：

```text
camera、LiDAR、GNSS、IMU
传感器时间戳
坐标系和外参
车辆控制输入
ground truth 导出
```

先用 CARLA Python API 验证传感器，再验证 ROS 2 bridge。固定
`world.tick()`、同步模式、天气、地图、车辆 blueprint 和 scenario seed。

### 7.3 CARLA + Autoware

优先采用与目标 Autoware/ROS 发行版匹配的
`CARLA 0.9.15 + autoware_carla_interface` 组合，并在目标架构上分别
验证每个组件。不要把 x86 容器在 QEMU 下跑通当成 DGX Spark ARM64
通过。

验收顺序：

1. CARLA 传感器话题被 Autoware 发现。
2. `/clock`、TF、odometry 和 vehicle status 正常。
3. Autoware 产生 trajectory。
4. 控制命令回到 CARLA 车辆。
5. 固定场景能重复运行。
6. 记录 rosbag、CARLA ground truth 和标定元数据。

明确区分：

```text
CARLA + Autoware 闭环 != NAVSIM
CARLA 实时仿真 != AWSIM
CARLA ground truth != NAVSIM OpenScene，除非经过明确转换
```

`carla-air` 目前不是本仓库已确认的官方依赖名；如它指的是某个内部
封装或项目，应先补充其仓库地址、版本、镜像架构和接口契约，再加入
Compose。当前文档按 CARLA + Autoware 处理。

## 8. L5：NAVSIM 离线规划评测

NAVSIM 与实时仿真是并行路线。它适合固定数据集上的规划 Agent 评测和
训练，不直接替代 Autoware 在线闭环。

最小顺序：

```bash
make harness-navsim
make navsim-cache
make navsim
```

开始时固定：

```text
mini split
单 worker
batch size 1
固定 seed
固定 nuPlan map version
固定 Agent 配置和 checkpoint
```

NAVSIM 需要官方 OpenScene/nuPlan 数据、地图和 sensor blobs。AWSIM 或
CARLA 的 ROS bag 不能直接当 NAVSIM 数据集使用，需要：

```text
ROS bag + ground truth + 标定
  -> 时间戳/坐标系/传感器 schema 适配器
  -> OpenScene/NAVSIM scene、sensor blobs、metadata
  -> NAVSIM Agent
```

离线评测完成不表示 Agent 已经能接入 Autoware。若要在线接入，还需要：

```text
NAVSIM trajectory
  -> planner/inference adapter
  -> Autoware trajectory message
  -> TF、碰撞检查、限速和故障回退
```

详细 NAVSIM 配置仍以 [`navsim-integration.md`](navsim-integration.md) 为准。

## 9. L6：BEVFormer，从 forward 到 Autoware 感知节点

BEVFormer 是感知模型路线，不是仿真器，也不是 Autoware planning
simulator 的替代品。不要因为 DGX GPU smoke test 通过，就声称
BEVFormer 已接入。

### 9.1 只跑官方 inference/demo

先固定：

```text
BEVFormer 源码 commit
Python 版本
PyTorch/Torchvision 版本
MMDetection/MMCV/MMEngine 版本
CUDA 和编译器版本
checkpoint
数据集 split
```

验收只要求：

```text
官方 demo 能加载 checkpoint
单张或小 batch forward 成功
输出尺寸和类别正确
结果可保存
```

### 9.2 在 DGX Spark 上锁定依赖

建立独立的 ARM64 镜像和 lock 文件，逐项记录：

```text
pip wheel 是否有 ARM64 包
CUDA 扩展是否能编译
PyTorch 与驱动/runtime 是否匹配
峰值统一内存
单帧延迟和吞吐
```

不要用 x86 预编译扩展、QEMU 成功结果或未固定的 `pip install` 作为
DGX Spark 证据。

### 9.3 导出和部署

逐步做：

1. 固定输入，保存 PyTorch 输出。
2. 尝试 ONNX 导出。
3. 在目标 TensorRT 版本构建 engine。
4. 对比 PyTorch、ONNX 和 TensorRT 的数值误差。
5. 记录 warm-up、延迟、吞吐和显存/统一内存。
6. 再包装为 ROS 2 perception node。
7. 对齐 Autoware 的 camera、TF、时间戳、检测结果 QoS 和消息类型。

最后才接入 CARLA 或 AWSIM 传感器话题。模型 forward 通过不等于在线
感知通过；在线还要验证时间预算、丢帧、TF 延迟、生命周期和故障回退。

## 10. L7：Isaac Lab + RL

Isaac Lab RL 适合训练控制或规划策略，是独立研究/训练路线。它不能直接
替代 Autoware planner，也不能把训练成功当作车辆闭环通过。

推荐顺序：

1. 在目标机器上运行 Isaac Lab 官方 example。
2. 验证单环境 `reset()`/`step()`。
3. 验证多环境并行和 GPU pipeline。
4. 固定 seed、环境版本、任务配置和 checkpoint。
5. 先训练简单低维控制策略。
6. 增加车辆动力学、传感器延迟、噪声和动作约束。
7. 增加碰撞、越界、舒适性和规则安全约束。
8. 导出 policy，验证独立推理。
9. 接入 CARLA 或 Isaac Sim 环境。
10. 通过 planner/controller adapter 接入 Autoware。
11. 做固定场景、扰动场景和故障回退回归。

RL 接入 Autoware 至少要定义：

```text
observation schema
action schema
坐标系和单位
控制频率
延迟和超时
动作限幅
安全接管策略
策略版本和 checkpoint checksum
```

明确区分：

```text
Isaac Lab RL policy != Autoware planner
RL 训练成功 != CARLA/Autoware 闭环通过
reward 上升 != 安全性通过
```

## 11. 建议的执行顺序

没有 x86 AWSIM 主机时，在 DGX Spark 先执行：

```bash
make preflight
make harness-host
make harness-gpu
make harness-clock
make up-dgx
make harness-runtime SERVICE=autoware
make scenario-prepare
make scenario
```

如果要在场景运行期间观察 ROS 话题，应在另一个终端执行：

```bash
make harness-ros-scenario
make inspect-modules
```

Scenario Simulator 通过后再开始 NAVSIM 或 BEVFormer 的独立实验。不要在
Scenario Simulator、BEVFormer、NAVSIM 和 RL 都未固定输入时同时改动它们，
否则无法判断失败来自仿真、消息、模型还是训练环境。

有 x86 主机后，再执行 L3：

```bash
# x86 主机
make up-sim
iperf3 -s -p 5201

# DGX Spark
make up-dgx
make harness-network
make harness-clock
make harness-ros
```

最终报告按以下标签记录：

```text
PASS         当前 Gate 的证据齐全
CONDITIONAL  依赖外部主机、未锁定输入或只完成部分证据
NOT-RUN      前置条件不存在，尚未执行
BLOCKED      已知依赖或接口阻塞
```

当前最有价值的下一步不是继续修改 Autoware 核心，而是先在 DGX Spark
执行一次 `make scenario-prepare && make scenario`，把 L2 的场景退出码、
ROS 话题、`/clock`、轨迹、控制话题和报告目录保存下来。
