# DGX Spark 自动驾驶闭环部署验证方案

## 1. 文档目的

本文档用于指导以 NVIDIA DGX Spark 为算法、训练和推理主机，并以 Docker
Compose 为统一编排方式，逐步验证：

```text
仿真 -> Autoware -> 传感器数据采集 -> 真值标注 -> 数据回放
     -> 模型训练 -> TensorRT 部署 -> 闭环评估
官方 OpenScene/nuPlan -> NAVSIM Agent -> 离线规划评测 -> 模型迭代
```

本文档只描述部署方法、实施步骤和验收标准，不包含具体的 Compose 实现。

文档基线日期：2026-09-11。

## 2. 已确认结论

### 2.1 Autoware ARM64

Autoware 官方文档将 `arm64` 列为支持架构，并提供 amd64/arm64 容器镜像和 CUDA 变体。

结论：

- DGX Spark 可以作为 Autoware ARM64 的目标部署平台。
- 必须选择固定版本的官方镜像。
- 不能仅因为 Autoware 镜像支持 ARM64，就假设所有感知算法和 CUDA 扩展都支持 ARM64。

### 2.2 AWSIM ARM64

截至文档基线日期，AWSIM 官方发布物和官方示例仍使用
`AWSIM_demo.x86_64`，没有确认可直接用于 DGX Spark 的官方 ARM64
二进制。

仅有 AWSIM 源码不能推出可以在 DGX Spark 上完成 ARM64 构建：

- AWSIM 使用 Unity 6，标准 Linux Unity Editor 的官方目标架构是 x64。
- 标准桌面 Linux Player 不能视为可直接产出 ARM64 Player；Unity Embedded
  Linux ARM64 属于另一套授权和运行时路径。
- AWSIM 包含 Ros2ForUnity、RGL/OptiX 等原生插件，这些插件必须同时提供
  aarch64 构建，不能只重新编译 C# 项目。
- 社区 Docker 镜像即使可以在 x86_64 主机运行，也可能只是包装
  `AWSIM_demo.x86_64`，Docker 不会改变二进制的 CPU 架构。

结论：

- 不将 `ghcr.io/qaidvoid/awsim:latest` 作为已验证依赖。
- 不将“在 DGX Spark 上源码构建 AWSIM”列为可执行备用方案。
- AWSIM 默认运行在带 NVIDIA RTX GPU 的 x86_64 Linux 主机。
- DGX Spark 运行 Autoware、数据处理、训练和推理服务。
- 只有未来取得 ARM64 Unity Player、全部 aarch64 原生插件和可复现构建证据后，
  才重新开启 AWSIM ARM64 单机路线。

### 2.3 ROS 2 Humble 与 Jazzy

AWSIM 官方示例基于 ROS 2 Humble。若 DGX Spark 上的 Autoware 使用 Jazzy，不能把 Humble 与 Jazzy 的直接 DDS 通信作为默认方案。

推荐顺序：

1. 第一版优先统一 AWSIM、Autoware 和 Recorder 为 ROS 2 Humble。
2. 如果必须使用 AWSIM Humble 和 Autoware Jazzy，增加 Zenoh ROS 2 Bridge。
3. 将 Bridge 的连接、话题白名单、QoS 和 TF 转换作为独立验证项。

### 2.4 DGX Spark 单机仿真选择

DGX Spark 上保留两条不依赖 AWSIM ARM64 的单机路线：

1. 使用 Autoware Scenario Simulator 做规划、控制、接口和回归测试。
2. 使用 Isaac Sim 6.0.1 或后续锁定版本，逐步建设高保真传感器仿真。

Scenario Simulator 已提供 Docker Compose 示例，适合先验证 Autoware 生命周期、
地图、规划、控制、场景断言和 CI，但不能替代 AWSIM 的完整摄像头、LiDAR
渲染与真值数据链路。

Isaac Sim 6.0.1 的官方容器支持 x86_64/aarch64，并明确支持 DGX Spark 和
Docker Compose。它可以作为长期单机高保真替代，但需要单独实现和验证：

- Autoware 传感器话题适配。
- sensor kit、坐标系和时间同步。
- 车辆状态输入与控制指令回传。
- 地图、道路和场景格式转换。
- 真值和标注导出。

因此，Isaac Sim 是长期适配项目，不是 AWSIM 的无成本直接替换。

### 2.5 NAVSIM 离线规划评测

NAVSIM 在本方案中是独立的离线规划评测和模型迭代路线。它使用官方
OpenScene/nuPlan 数据、地图和 sensor blobs，对 Agent 输出的未来轨迹进行
PDM/EPDMS 类指标评测，并不提供 AWSIM 的实时渲染、ROS 2 控制闭环或
ground truth 采集能力。

实施边界：

- DGX Spark 上通过 `compose.dgx.yaml` 的 `navsim` profile 运行。
- 优先使用已验证的 ARM64 镜像；没有官方可直接假设的 DGX Spark 镜像。
- 可通过固定 NAVSIM git ref 的 Dockerfile 进行源码构建，但官方旧版
  PyTorch、GIS 和 Python 依赖必须在 DGX Spark 单独验证。
- 官方数据先以 `mini` 完成最小基线，再扩展到 `navtrain`、
  `navhard_two_stage` 或其他锁定 split。
- AWSIM ROS 2 bag 到 NAVSIM OpenScene schema、以及 NAVSIM 轨迹到 Autoware
  ROS 2 trajectory 都需要独立 adapter，不能直接连线。

### 2.6 结论状态

本文不把“官方文档支持”直接等同于“DGX Spark 已验证”。所有结论使用以下状态：

| 状态 | 含义 |
|---|---|
| `source` | 有官方文档、仓库或社区案例依据，但尚未在目标机器验证 |
| `local` | 已在 DGX Spark 或目标容器中完成单项验证 |
| `integration` | 已完成两个或多个服务之间的接口验证 |
| `e2e` | 已完成仿真、Autoware、数据采集和回放的完整链路验证 |
| `soak` | 已完成连续运行和重复运行验证 |
| `blocked` | 存在明确阻断条件，必须停止该路径或切换已定义的替代路线 |

当前关键结论状态：

| 断言 | 当前状态 | 允许的决策 |
|---|---|---|
| Autoware ARM64 容器可运行 | `local` | DGX Spark 上的 `planning_simulator`、地图加载和业务健康探针已通过 |
| AWSIM x86_64 官方发布物可运行 | `source` | 进入 x86_64 RTX 主机 `local` 验证 |
| AWSIM ARM64 可运行 | `blocked` | 不进入实施队列，不作为备用方案 |
| Scenario Simulator 可在 DGX Spark 运行 | `source` | 进入单机 `local` 验证 |
| Isaac Sim 6.0.1 可在 DGX Spark 运行 | `source` | 进入长期替代路线 `local` 验证 |
| NAVSIM 可用于 DGX Spark 离线规划评测 | `source` | 进入 ARM64 镜像、数据和最小评测 `local` 验证 |
| NAVSIM 可直接读取 AWSIM ROS 2 bag | `blocked` | 先实现 OpenScene/NAVSIM schema 适配器 |
| NAVSIM 可替代 AWSIM 实时闭环 | `blocked` | NAVSIM 只作为离线/伪闭环规划评测路线 |
| AWSIM Humble 与 Autoware Jazzy 可通信 | `source` | 必须经过 Zenoh `integration` 验证 |
| GPU 容器可用 | `local` | GB10 上的 ARM64 PyTorch CUDA 运算已通过 |
| bag 可录制并回放 | 待测 | 作为数据采集阶段的闭环门槛 |

目标机证据：

```text
Host:      artifacts/host/20260910T142240Z-spark-dba5
GPU:       artifacts/gpu/20260910T144100Z-spark-dba5
Autoware:  artifacts/runtime/20260911T055823Z-spark-dba5
```

## 3. 目标架构

### 3.1 正式主路径：x86_64 AWSIM + DGX Spark Autoware

```text
x86_64 Linux + NVIDIA RTX GPU
|
|-- AWSIM x86_64
|-- Ground Truth Collector
|-- Scenario Metadata Collector
`-- Recorder（网络带宽不足时）
            |
            | 10 GbE 优先
            | CycloneDDS / Zenoh
            v
DGX Spark ARM64 + CUDA 13
|
|-- Autoware ARM64 CUDA
|-- Recorder（网络验证通过时）
|-- Replay
|-- Foxglove Bridge
|-- Dataset Converter
|-- Model Training
|-- TensorRT Build
`-- Metrics and Experiment Tracking
```

这是第一版正式实施路径，不再作为 AWSIM ARM64 失败后的临时备用路径。

必须提前决定数据采集位置：

- 如果网络带宽不足，Recorder 和 Ground Truth Collector 放在 AWSIM 主机侧，先在源端落盘。
- 如果要求 Recorder 放在 DGX Spark，必须验证相机、LiDAR 和真值流量不会造成持续丢包。
- 两台主机必须同步系统时间，并固定网络接口、MTU、端口和防火墙策略。
- bag 传输到 DGX Spark 后，要保留原始校验值和场景元数据。

### 3.2 DGX Spark 单机验证路径

```text
DGX Spark ARM64
|
|-- Scenario Simulator
|-- Autoware ARM64 CUDA
|-- Scenario Runner
|-- Recorder
|-- Replay
`-- Metrics
```

该路径用于尽快确认 Docker Compose、Autoware、地图、规划、控制、场景测试和
结果落盘，不承担高保真传感器数据生成目标。

### 3.3 DGX Spark 长期单机高保真路径

```text
DGX Spark ARM64
|
|-- Isaac Sim 6.0.1
|-- Isaac Sim Web Viewer
|-- Autoware Adapter
|-- Autoware ARM64 CUDA
|-- Recorder and Ground Truth Exporter
`-- Metrics
```

该路径以独立里程碑推进。只有 Autoware Adapter 的消息、TF、`/clock`、
控制闭环和真值导出全部通过 Harness 后，才能替代主路径中的 AWSIM。

### 3.4 NAVSIM 离线规划路线

```text
DGX Spark ARM64
|
|-- NAVSIM evaluation/training container
|-- OpenScene/nuPlan dataset and maps
|-- metric cache
|-- Agent checkpoints
`-- reproducible EPDMS/PDM reports
```

该路线用于：

- 官方 NAVSIM baseline 的安装和数据读取验证。
- 规划 Agent 的训练、推理和模型对比。
- 固定 split、checkpoint、配置和随机种子的离线评测。
- 为后续在线 planner adapter 提供候选轨迹和离线回归证据。

该路线不承担：

- AWSIM 传感器渲染或 ground truth 生成。
- ROS 2 在线控制和 Autoware 生命周期。
- 将 EPDMS/PDM Score 直接解释为 Autoware 闭环成功率。

Compose 使用 `navsim` profile；`make harness-navsim` 只检查入口和数据契约，
`make navsim-cache` 生成 metric cache，`make navsim` 执行一次评测 Job。
三者均不代表 NAVSIM 已接入 Autoware。

### 3.5 不进入实施的实验路径

```text
DGX Spark ARM64 -> 从源码构建 AWSIM
```

默认状态为 `BLOCKED`。重新评估必须同时满足：

1. Unity 官方提供适用于该场景的 Linux ARM64 Editor/Player 构建链。
2. Ros2ForUnity、RGL/OptiX 和其他原生插件均有 aarch64 构建。
3. AWSIM 能在 DGX Spark 完成渲染、传感器、真值和控制闭环。
4. 构建过程、许可证和镜像可以固定并复现。

不使用 QEMU/x86 模拟运行 AWSIM 作为性能仿真方案。

### 3.6 Compose 拆分

正式主路径由两份 Compose 独立部署：

```text
compose.sim-x86.yaml
  - awsim
  - ground-truth
  - scenario-metadata
  - recorder-source（record-source profile）

compose.dgx.yaml
  - autoware
  - recorder（record profile）
  - replay
  - ros-probe（harness profile）
  - foxglove-bridge（viz profile）
  - scenario-simulator（scenario profile）
  - isaac-sim（isaac profile）
  - dataset-converter（data profile）
  - tensorrt-build（deploy profile）
  - navsim（navsim profile）
```

DGX 单机回归另设 profile 或覆盖文件：

```text
compose.dgx.yaml --profile scenario
compose.dgx.yaml --profile isaac
compose.dgx.yaml --profile data
compose.dgx.yaml --profile deploy
compose.dgx.yaml --profile navsim
```

Compose 负责生命周期、依赖和持久化，不假设一条 `docker compose up`
就能绕过跨主机网络、显示系统、NGC 登录或外部资产下载等前置条件。

跨 ROS 发行版或需要显式路由隔离时，Zenoh Bridge 不作为当前 Compose
内置服务，而是由已验证的 bridge 镜像或单独覆盖文件接入。这样不会把尚未
锁定的 bridge 镜像、配置和消息白名单误标为可直接运行。

### 3.7 Harness 运行原则

每个阶段只验证有限数量的断言，不跨阶段掩盖失败。每条断言至少记录：

- 断言编号。
- 测试日期和机器标识。
- 宿主机系统、驱动和 Docker 版本。
- 镜像 tag 和 digest。
- ROS 发行版与 RMW 实现。
- 输入数据或场景 ID。
- 验证动作。
- 通过标准。
- 证据文件位置。
- 失败日志和下一步处理。

证据优先级：

```text
版本与配置快照
    -> 单容器运行日志
    -> ROS 话题和 QoS 检查结果
    -> bag 元数据与回放结果
    -> 固定场景指标
    -> 连续运行和重复运行报告
```

只有达到对应等级的证据，才能将断言状态从 `source` 推进到 `local`、`integration`、`e2e` 或 `soak`。

## 4. 宿主机前置条件

### 4.1 系统和 GPU

DGX Spark 部署前确认：

- 系统为 ARM64，CUDA 13 和 DGX 软件栈状态已记录。
- NVIDIA 驱动正常加载。
- Docker Engine 正常运行。
- Docker Compose v2 可用。
- NVIDIA Container Toolkit 已配置。
- GPU 容器可以正常启动。
- CUDA Runtime 与镜像中的 CUDA 版本兼容。
- 系统时间已同步。
- 具备足够的 NVMe 或外部存储。

x86_64 RTX 仿真主机部署前确认：

- 系统为 x86_64 Linux。
- NVIDIA 驱动、Docker Engine、Compose v2 和 NVIDIA Container Toolkit 正常。
- OpenGL/Vulkan 图形栈可用。
- 本地显示、X11、Wayland 或 headless 模式已确定。
- 系统时间与 DGX Spark 同步。
- 具备足够的本地存储用于场景、资产和源端 bag。

DGX Spark 的统一内存需要单独监控。128GB 统一内存不是可以无限提升
batch size 的依据，Autoware、录包、数据加载、编译和训练会共同消耗系统资源。

### 4.2 目录和存储

两台主机分别准备持久化目录，并通过场景 ID 和采集清单建立对应关系：

```text
sim-x86/
  assets/
  scenarios/
  bags/
  ground_truth/
  logs/
  artifacts/

dgx/
  maps/
  bags/
  ground_truth/
  datasets/
  models/
  engines/
  logs/
  reports/
  cache/
```

目录要求：

- 容器删除后数据仍然保留。
- 容器用户对挂载目录具有读写权限。
- bag、数据集、模型和日志分开管理。
- x86 源端数据传输后保留校验值，不以文件名判断同步完成。
- 大文件不提交到 Git。
- 训练缓存和编译缓存设置清理策略。
- 为 bag 和数据集预留磁盘空间告警。

### 4.3 镜像兼容性矩阵

每个镜像都需要记录：

| 项目 | 要求 |
|---|---|
| CPU 架构 | `linux/arm64` 或 `linux/amd64` |
| ROS 发行版 | Humble 或 Jazzy |
| CUDA 版本 | 与宿主机驱动和框架兼容 |
| TensorRT 版本 | 与 engine 构建和运行环境一致 |
| GPU 架构 | 确认框架和自定义 CUDA 扩展可编译 |
| 镜像版本 | 固定 tag，必要时固定 digest |
| 来源 | 官方、内部构建或社区构建 |
| 验证状态 | 未验证、单元验证、闭环验证 |

禁止在正式部署中依赖未验证的 `latest` 镜像。

### 4.4 资源基线

首次部署前分别记录两台主机空载基线：

- 系统内存和统一内存使用情况。
- GPU 计算占用。
- CPU 各核心负载。
- 磁盘剩余空间和写入速度。
- 容器启动耗时。
- 网络延迟和丢包率。

之后每个阶段都与该基线比较，不用单次观测替代趋势判断。

## 5. Compose 服务分层

### 5.1 `sim-x86`：AWSIM 仿真核心

当前 Compose 已包含：

- AWSIM
- Ground Truth Collector
- Scenario Metadata Collector
- Recorder Source

后续按网络 Gate 接入：

- Zenoh Bridge x86

职责：

- 发布传感器数据。
- 接收车辆控制指令。
- 输出仿真时钟、真值和场景元数据。
- 在源端录制高带宽话题。

### 5.2 `autoware-dgx`：算法核心

当前 Compose 已包含：

- Autoware
- Foxglove Bridge
- Recorder DGX
- ROS Probe

跨发行版或需要显式路由隔离时再接入：

- Zenoh Bridge DGX

职责：

- 运行定位、感知、预测、规划和控制。
- 接收 AWSIM 或单机仿真器的传感器输入。
- 输出车辆控制指令。
- 提供可视化和调试接口。

### 5.3 `record`：数据记录

Recorder 的最终部署位置由网络基线测试决定，不在方案阶段硬编码。

职责：

- 记录相机、LiDAR、车辆状态、TF、时钟和真值。
- 生成场景 ID、地图版本、车辆模型和随机种子。
- 保证真值与传感器时间戳可对应。
- 记录采集主机、网卡、ROS 版本、RMW 和镜像 digest。

### 5.4 `scenario`：DGX 单机回归

包含：

- Scenario Simulator
- Scenario Runner
- Autoware
- Metrics

职责：

- 在不依赖 AWSIM 的条件下验证 Autoware。
- 执行固定场景、断言和回归测试。
- 为 Compose、地图、规划、控制和 CI 建立基础证据。

### 5.5 `isaac`：DGX 单机高保真替代

包含：

- Isaac Sim 6.0.1
- Autoware Adapter
- Ground Truth Exporter
- Web Viewer

职责：

- 验证 DGX Spark 单机高保真仿真。
- 对齐 AWSIM/Autoware 的消息、TF、时钟和控制接口。
- 形成可替换的仿真器边界。

### 5.6 `replay`：回放和回归

包含：

- ROS 2 bag 回放器
- 离线推理节点
- 指标计算节点

职责：

- 回放历史数据。
- 对比不同模型的输出。
- 重复运行标准场景。
- 生成可比较的指标报告。

### 5.8 `navsim`：离线规划评测和模型迭代

包含：

- NAVSIM ARM64 evaluation/training 容器。
- OpenScene/nuPlan 数据和 nuPlan 地图。
- metric cache。
- Agent 配置、checkpoint 和实验结果。
- EPDMS/PDM 报告及重复运行对比。

职责：

- 在 DGX Spark 上先跑官方 `mini` baseline，确认依赖、数据和报告链路。
- 再按需求运行 `navtrain`、`navtest`、`navhard_two_stage` 或锁定的内部 split。
- 固定源码 ref、镜像 digest、数据版本、地图版本、Agent 配置和随机种子。
- 将 NAVSIM 轨迹评测与 Autoware 在线闭环分开记账。

NAVSIM 官方安装文档使用 Python 3.9、editable install、OpenScene sensor
blobs 和 nuPlan maps。仓库中的 `images/navsim/Dockerfile` 只提供固定
基础镜像和 git ref 的构建入口，不保证官方旧版 PyTorch、GIS 依赖和 CUDA
扩展已经适配 ARM64/CUDA 13。

运行顺序：

```text
Compose 展开
  -> ARM64 image/import/data/maps Harness
  -> metric cache
  -> 官方 baseline evaluation
  -> 固定配置重复运行
  -> Agent 模型迭代
```

NAVSIM 不能直接读取本仓库 ROS 2 bag。若要使用 AWSIM 数据，必须先实现
ROS bag/ground truth 到 OpenScene/NAVSIM schema 的适配器，至少覆盖场景
ID、传感器历史、ego 状态、标定、坐标系、地图、轨迹和评测真值。

若要把 NAVSIM Agent 接入 Autoware，必须再实现独立 trajectory adapter，
验证消息字段、TF、时间戳、采样频率、碰撞检查和安全回退。NAVSIM 离线
评测通过不等于 Autoware 在线闭环通过。

#### 5.8.1 NAVSIM Harness 门槛

| 编号 | 断言 | 通过标准 | 证据 |
|---|---|---|---|
| H-NAV-01 | Compose 和架构 | `navsim` profile 为 `linux/arm64` 且 GPU 可声明 | Compose 和镜像快照 |
| H-NAV-02 | 镜像 provenance | tag、digest、源码 ref 可记录 | image/ref report |
| H-NAV-03 | Python 依赖 | `import navsim`、nuPlan 和关键 GIS 依赖成功 | import report |
| H-NAV-04 | 数据可读取 | maps、logs、sensor blobs 与选定 split 完整 | dataset report |
| H-NAV-05 | metric cache | 固定数据生成缓存，重复生成无结构差异 | cache report |
| H-NAV-06 | 最小 Agent 评测 | 官方 baseline 完成且生成报告 | evaluation report |
| H-NAV-07 | 结果可重复 | 相同输入和配置的关键指标在阈值内一致 | comparison report |
| H-NAV-08 | AWSIM 数据适配 | schema、时间戳、坐标系和真值抽样通过 | adapter report |
| H-NAV-09 | Autoware adapter | 轨迹消息、TF、安全约束和在线回退通过 | integration report |

### 5.9 `train`：模型训练

包含：

- 数据集构建器
- BEVFormer 训练环境
- 验证环境
- TensorBoard

训练容器与 Autoware 容器分离，代码、数据和 checkpoint 使用宿主机挂载。

#### 5.9.1 BEVFormer 依赖策略

原始蓝图中的以下命令不能直接作为 DGX Spark 部署基线：

```text
torch==2.5.1 + cu130
mmcv-full cu130/torch2.5 预编译 wheel
```

原因：

- PyTorch 官方版本矩阵中，2.5.1 对应的是 CUDA 11.8、12.1 和 12.4，
  不能把 `cu130` URL 拼接出来后视为存在。
- BEVFormer 官方安装基线较旧，依赖 PyTorch 1.9.1、`mmcv-full==1.4.0`、
  MMDetection 2.14.0 和 MMDetection3D 0.17.1。
- 旧版 OpenMMLab CUDA 扩展与 ARM64、现代 Python、CUDA 13 和 Blackwell
  的组合需要实际移植，不能依赖 x86_64 预编译 wheel。

训练镜像采用以下顺序：

1. 从 DGX Spark 官方可运行的 NVIDIA PyTorch/CUDA 容器建立 GPU 基线。
2. 锁定 Python、PyTorch、CUDA、OpenMMLab 和编译器版本矩阵。
3. 检查每个 wheel 是否包含 `linux_aarch64`。
4. 对没有 ARM64 wheel 的 CUDA 扩展执行源码构建。
5. 运行 CUDA 自定义算子的 import、单元测试和最小 forward。
6. 再移植 BEVFormer 配置和 checkpoint。

不要在配置中直接硬编码未经运行时确认的 GPU 架构号。先在目标 PyTorch
容器中读取设备 capability，再设置 CUDA 构建参数。设置架构参数只能告诉
编译器生成什么代码，不能修复不兼容的源码、编译器、ABI 或第三方库。

`torch.cuda.empty_cache()` 不是统一内存 OOM 的主要解决方案。资源控制以
batch size、数据加载 worker、梯度累积、混合精度、activation checkpoint、
缓存上限和训练期间停止非必要容器为主。

#### 5.9.2 BEVFormer Harness 门槛

| 编号 | 断言 | 通过标准 | 证据 |
|---|---|---|---|
| H-DEP-01 | PyTorch ARM64 CUDA 可用 | GPU tensor 运算成功 | 环境与 smoke log |
| H-DEP-02 | OpenMMLab 核心包可导入 | 版本矩阵和 ABI 无冲突 | import report |
| H-DEP-03 | CUDA 自定义算子可构建 | 所有必需扩展生成 aarch64 产物 | build log |
| H-DEP-04 | CUDA 自定义算子可执行 | 固定输入单元测试通过 | test report |
| H-DEP-05 | BEVFormer 最小 forward | 一个 batch 完成且无非法内存访问 | forward log |
| H-DEP-06 | 资源峰值受控 | 峰值内存低于锁定阈值 | metrics report |

### 5.10 `deploy`：模型部署

包含：

- ONNX 导出
- ONNX 数值验证
- TensorRT engine 构建
- 推理适配器

模型接入 Autoware 时，优先采用独立的 inference adapter，不直接修改 Autoware 核心模块。

### 5.11 `rl`：强化学习

包含：

- RL 训练
- 仿真评估
- 策略服务
- 安全约束层

RL 第一阶段只输出候选轨迹、速度建议或行为决策，不直接绕过 Autoware 的碰撞检查和安全约束。

Isaac Lab 不能只按一个 `pip install isaaclab` 命令判断 DGX Spark 已受支持。
必须按 Isaac Lab 与 Isaac Sim 的官方版本矩阵构建独立镜像，并分别验证：

- Isaac Sim 运行时。
- Isaac Lab 扩展加载。
- ARM64 Python/CUDA 依赖。
- headless 批量环境。
- Gym 接口与 AWSIM/Autoware Adapter。
- PPO 最小训练和 checkpoint 回放。

在感知、规划和数据 Gate 尚未通过时，不启动 RL 训练。

### 5.12 `ops`：实验和监控

包含：

- MLflow
- PostgreSQL
- MinIO
- Prometheus/Grafana

第一轮仿真验证不需要启动这一层，可在数据链路稳定后加入。

## 6. ROS 2 通信方案

### 6.1 同发行版方案

第一版主路径优先采用：

```text
AWSIM Humble
Autoware Humble
Recorder Humble
```

所有服务统一：

- ROS 2 发行版
- `ROS_DOMAIN_ID`
- RMW 实现
- DDS 配置
- 时间源

分布式主路径优先使用 CycloneDDS，并显式配置：

- 绑定 10 GbE 或指定业务网卡，不依赖自动选卡。
- 固定 `ROS_DOMAIN_ID`。
- 关闭只允许 localhost 的配置。
- 明确允许的 peer、多播、端口和防火墙规则。
- 对相机和点云分别测量带宽、抖动和丢包。

### 6.2 跨发行版方案

如果使用：

```text
AWSIM Humble
Autoware Jazzy
```

采用：

```text
AWSIM -> Zenoh Bridge -> Autoware
```

需要验证：

- 相机话题。
- LiDAR 话题。
- TF 和 TF Static。
- `/clock`。
- 车辆状态。
- 控制指令。
- QoS。
- 大消息传输。
- 网络断开后的恢复行为。

### 6.2.1 ROS Harness 断言

| 编号 | 断言 | 通过标准 | 证据 |
|---|---|---|---|
| H-ROS-01 | Bridge 能发现两侧 ROS 图 | 预期话题出现在两侧，且无重复命名空间 | 话题清单 |
| H-ROS-02 | `/clock` 能跨 Bridge 传递 | 时间单调递增，Autoware 使用仿真时间 | 时间戳抽样 |
| H-ROS-03 | TF 拓扑完整 | 传感器到车辆坐标链路可查询 | TF 树快照 |
| H-ROS-04 | 大消息稳定传输 | 相机和点云无持续丢包 | 频率和丢包报告 |
| H-ROS-05 | 控制指令可返回仿真器 | 固定场景车辆产生预期运动 | 场景录屏和控制日志 |

### 6.3 网络模式

同一主机内的首轮验证建议使用 host network，减少 DDS 发现和 UDP
传感器通信问题。跨主机时，host network 不能替代网卡、DDS peer、MTU、
防火墙和时间同步配置。

两台主机必须满足：

- 优先使用有线 10 GbE；1 GbE 只能在降低传感器负载后评估。
- 使用 NTP 或 PTP 同步系统时间，并记录同步偏差。
- 运行前测量吞吐量、延迟、抖动和丢包。
- 明确 DDS 直连和 Zenoh 路由二选一的边界，避免重复转发。
- Recorder 在链路两端只能有一个权威实例，防止生成语义不同的重复数据。

多实例仿真时再规划：

- 每个实例独立 `ROS_DOMAIN_ID`。
- 每个实例独立场景目录。
- 每个实例独立 bag 输出目录。
- 每个实例独立 Zenoh session。

## 7. 图形和 headless 方案

需要同时准备两种运行模式。

### GUI 模式

用于首次调试：

- AWSIM 窗口运行在 x86_64 RTX 主机。
- RViz 或 Foxglove 可运行在 DGX Spark 或操作终端。
- 本地显示器或远程 X11。
- OpenGL/Vulkan 验证。

### Headless 模式

用于批量采集：

- AWSIM 在 x86_64 主机无桌面运行。
- Autoware 在 DGX Spark 无桌面运行。
- 固定场景和随机种子。
- 自动启动和停止仿真。
- 自动录制 bag。
- 自动保存真值和场景元数据。

不要在首次部署时直接使用 headless 模式。应先通过 GUI 确认渲染、传感器和场景状态。

Isaac Sim 单机路线优先验证官方容器和 Web Viewer，不把 X11 映射作为唯一
可视化方案。

## 8. 数据采集方案

### 8.1 原始数据

保留完整 ROS 2 bag，至少包括：

```text
/clock
/tf
/tf_static
/sensing/camera/*
/sensing/lidar/*
/vehicle/status/*
/localization/*
/perception/*
/planning/*
/control/*
AWSIM ground truth topics
```

正式录制使用话题白名单，不建议无差别录制全部 ROS 话题。

### 8.2 真值数据

真值采集器需要输出：

- 目标 ID。
- 目标类别。
- 3D 位置。
- 3D 尺寸。
- 3D 朝向。
- 速度。
- 所属坐标系。
- 时间戳。
- 可见性或有效性状态。

### 8.3 内部数据格式

建议使用内部中间格式作为长期标准：

```text
原始 bag
  -> 中间数据格式
  -> nuScenes 适配器
  -> BEVFormer 数据格式
```

不要将 nuScenes 作为唯一数据源，因为转换过程可能丢失仿真特有的真值、传感器状态和场景元数据。

### 8.4 数据 Harness 断言

| 编号 | 断言 | 通过标准 | 证据 |
|---|---|---|---|
| H-DATA-01 | bag 可以完整关闭 | 关闭后元数据可读取，无损坏数据库 | bag 元数据 |
| H-DATA-02 | 传感器时间连续 | 时间戳无大段倒退或异常跳变 | 时间戳统计 |
| H-DATA-03 | 真值可对齐 | 每个样本能找到对应真值或明确缺失原因 | 对齐报告 |
| H-DATA-04 | 坐标系可复现 | 车辆、传感器、地图坐标转换一致 | TF 和标注抽样 |
| H-DATA-05 | 回放可重复 | 同一 bag 重放得到一致的关键话题和指标 | 两次回放对比 |

## 9. 实施步骤

### 第 0 阶段：双主机前置验证

目标：确认 DGX Spark 和 x86_64 RTX 仿真主机具备运行基础容器的条件。

步骤：

1. 在 DGX Spark 确认 ARM64、驱动、CUDA 容器和统一内存基线。
2. 在 x86_64 RTX 主机确认 amd64、驱动和 AWSIM 图形环境。
3. 两台主机分别确认 Docker Engine、Compose v2 和 NVIDIA Container Toolkit。
4. 两台主机分别启动最小 GPU 容器。
5. DGX Spark 验证 PyTorch GPU 运算。
6. x86_64 RTX 主机验证 OpenGL/Vulkan。
7. 验证宿主机目录挂载和文件权限。
8. 确认磁盘容量和日志位置。
9. 验证 10 GbE、CycloneDDS、NTP/PTP 和防火墙配置。

验收标准：

- 两台主机的 GPU 容器均可以运行。
- DGX Spark 容器内可以执行 CUDA 运算。
- 两台主机的挂载目录均可以读写。
- x86_64 RTX 主机的 GUI 或 headless 至少有一种可用。
- 跨主机网络满足第一版传感器带宽和丢包预算。

### 主机 Harness 断言

| 编号 | 断言 | 通过标准 | 证据 |
|---|---|---|---|
| H-HOST-01 | 主机架构正确 | DGX 为 arm64，AWSIM 主机为 amd64，镜像分别匹配 | 主机信息快照 |
| H-HOST-02 | GPU 容器可用 | 两台主机的容器均可访问 GPU | GPU smoke log |
| H-HOST-03 | 图形链路可用 | AWSIM 主机 GUI 或 headless 至少一种模式成功 | 渲染日志或截图 |
| H-HOST-04 | 持久化目录可用 | 容器写入文件后宿主机可读取 | 文件校验 |
| H-HOST-05 | 资源基线可记录 | 两端内存、GPU、CPU、磁盘数据可采集 | 基线报告 |
| H-HOST-06 | 网络链路达标 | 吞吐、延迟、抖动和丢包满足锁定阈值 | 网络基线报告 |
| H-HOST-07 | 时间同步达标 | 两端时钟偏差满足锁定阈值 | 时间同步报告 |

### 第 1 阶段：x86_64 AWSIM 验证

目标：确认官方 AWSIM 发布物可在 x86_64 RTX 主机运行。

步骤：

1. 锁定 AWSIM 官方发布版本和下载校验值。
2. 检查 AWSIM 可执行文件为 `x86_64`。
3. 启动最小 AWSIM 容器。
4. 验证图形初始化。
5. 验证 ROS 2 传感器话题。
6. 验证 ground truth 话题。
7. 验证车辆控制输入。

该阶段是正式主路径的阻断门。只要二进制架构、图形初始化、传感器话题、
真值或控制验证失败，就先修复 x86_64 AWSIM 环境，不切换到未经证实的
DGX Spark AWSIM 源码构建。

### AWSIM Harness 断言

| 编号 | 断言 | 通过标准 | 证据 |
|---|---|---|---|
| H-AWSIM-01 | 镜像架构匹配 | 镜像或构建环境声明为 `linux/amd64` | 镜像检查结果 |
| H-AWSIM-02 | 二进制架构匹配 | 官方可执行文件为 `x86_64` | 文件类型报告 |
| H-AWSIM-03 | 渲染初始化成功 | 无架构、Vulkan 或 OpenGL 致命错误 | 启动日志 |
| H-AWSIM-04 | 传感器话题发布 | 相机、LiDAR、车辆状态达到预期频率 | 话题统计 |
| H-AWSIM-05 | 真值话题发布 | 真值包含时间戳、类别和 3D 框 | 消息抽样 |
| H-AWSIM-06 | 控制输入有效 | 固定控制指令产生预期运动 | 控制日志和场景证据 |

### 第 1B 阶段：DGX 单机 Scenario Simulator 基线

目标：在不依赖 AWSIM 和跨主机网络的条件下，先确认 Autoware ARM64
基础闭环与 Compose 编排。

步骤：

1. 启动官方 Scenario Simulator Compose 示例或锁定后的内部镜像。
2. 启动 Autoware ARM64 CUDA 容器。
3. 加载地图、车辆模型和场景。
4. 执行固定路线和失败场景。
5. 保存场景断言、Autoware 日志和指标。
6. 重复执行同一场景，确认结果可复现。

该阶段可以与 x86_64 AWSIM 验证并行，不作为高保真感知数据验证结果。

### 第 2 阶段：分布式 Autoware 基础闭环

目标：验证 DGX Spark 上的 Autoware ARM64 容器能够接收远端 AWSIM
数据并返回控制。

步骤：

1. 在 x86_64 主机启动 `compose.sim-x86.yaml` 的最小 profile。
2. 在 DGX Spark 启动 `compose.dgx.yaml` 的 Autoware profile。
3. 加载地图、车辆模型和 sensor kit。
4. 设置仿真时间。
5. 验证定位。
6. 验证远端感知输入。
7. 设置目标点。
8. 验证规划和控制输出跨主机返回。
9. 观察车辆是否能够稳定运行。

### 第 3 阶段：通信和桥接验证

同发行版采用 CycloneDDS 直连测试。仅在 ROS 发行版不一致、DDS 无法满足
网络约束或明确需要路由隔离时加入 Zenoh。

步骤：

1. 启动 AWSIM Humble。
2. 启动 Zenoh Bridge。
3. 启动 Autoware Jazzy。
4. 验证传感器话题。
5. 验证 TF。
6. 验证 `/clock`。
7. 验证控制指令返回 AWSIM。
8. 测试大消息和连续运行稳定性。

### 第 4 阶段：数据采集和回放

步骤：

1. 启动 Recorder。
2. 启动真值采集器。
3. 运行固定场景。
4. 生成一份短 bag。
5. 检查 bag 元数据。
6. 检查传感器频率。
7. 检查真值和传感器时间戳。
8. 回放 bag。
9. 使用 Foxglove 或 RViz 检查回放结果。
10. 生成第一份数据质量报告。

### 第 5 阶段：模型推理

步骤：

1. 使用采集数据完成数据格式转换。
2. 先使用 PyTorch 完成 BEVFormer forward。
3. 在小数据集上验证训练。
4. 记录基线精度和延迟。
5. 导出 ONNX。
6. 验证 ONNX 与 PyTorch 输出一致性。
7. 构建 TensorRT engine。
8. 对比 PyTorch、ONNX 和 TensorRT 结果。
9. 接入 inference adapter。
10. 在固定场景中做回归测试。

### 模型 Harness 断言

| 编号 | 断言 | 通过标准 | 证据 |
|---|---|---|---|
| H-MODEL-01 | 训练容器可加载数据 | 小数据集可完成一个 batch | 训练日志 |
| H-MODEL-02 | PyTorch forward 正常 | 输入输出 shape 和类别定义正确 | 单元测试结果 |
| H-MODEL-03 | ONNX 数值一致 | 关键输出误差在预设阈值内 | 对比报告 |
| H-MODEL-04 | TensorRT 数值一致 | 与 ONNX/PyTorch 的误差可接受 | engine 验证报告 |
| H-MODEL-05 | Autoware 接口一致 | 消息字段、坐标系和时间戳正确 | 适配器测试 |
| H-MODEL-06 | 闭环没有回归 | 固定场景规划和控制指标不低于基线 | 回归报告 |

### 第 6 阶段：闭环评估

评估：

- 检测精度。
- 漏检率。
- 误检率。
- 推理延迟。
- 传感器到规划的端到端延迟。
- 仿真实时倍率。
- 规划成功率。
- 碰撞率。
- 运行稳定性。

### 9.6.1 连续运行和重复运行

闭环评估不只运行一次。至少执行：

1. 同一场景重复运行，确认结果可重复。
2. 多个场景运行，确认不是单场景偶然成功。
3. 连续运行 30 至 60 分钟，观察资源是否持续增长。
4. 重启单个服务，确认 Compose 可以恢复。
5. 中断录制后重新启动，确认数据目录不会互相覆盖。

达到 `e2e` 之前，不宣称系统已经跑通；达到 `soak` 之前，不将其作为长期批量采集服务。

## 10. 首次部署的最小服务集合

第一轮在 x86_64 RTX 主机只启用：

```text
awsim
ground-truth
scenario-metadata
```

第一轮在 DGX Spark 只启用：

```text
autoware
```

完成核心 Autoware `local` 验证后，按目标逐项启用：

```text
ros-probe（harness profile）
foxglove-bridge（viz profile）
scenario-simulator（scenario profile）
```

Recorder 的最终位置在网络测试后选择：

```text
recorder（DGX，当前已实现）
或 recorder-source（x86，当前已实现）
```

如果 AWSIM 与 Autoware ROS 版本不同，或需要显式路由隔离，再增加：

```text
zenoh-bridge-x86
zenoh-bridge-dgx
```

第一轮暂不启用：

```text
bevformer-train
isaaclab
tensorrt-build
mlflow
grafana
jupyter
```

### 10.1 Harness 执行方式

每次测试只使用一组明确的 Compose profile，并保存以下产物：

```text
artifacts/
  <test-id>/
    <UTC时间>-<主机>/
      host-info/
      image-info/
      compose-config/
      logs/
      metrics/
      decision.md
```

`topic-snapshot`、`bag-metadata` 等专项证据在对应集成 Harness 实现后加入，
不能因为目录名称已经规划就视为测试已经存在。

每个测试结果只能标记为：

```text
PASS
FAIL
BLOCKED
NOT-RUN
```

`BLOCKED` 必须附带：

- 阻断原因。
- 已确认的证据。
- 是否切换备用路径。
- 重新验证所需条件。

### 10.2 首轮 Harness 测试矩阵

| 测试编号 | 范围 | 结果要求 |
|---|---|---|
| T-00 | 双主机架构、驱动、Docker、Toolkit | `PASS` 才能继续 |
| T-01 | DGX Spark ARM64 CUDA 容器 | `PASS` 才能继续 |
| T-02 | x86_64 AWSIM 图形或 headless | 至少一种 `PASS` |
| T-03 | AWSIM x86_64 传感器、真值、控制 | `PASS` 才能进入主路径 |
| T-04 | DGX 单机 Scenario Simulator + Autoware | `e2e` 基线 |
| T-05 | 跨主机网络、ROS 话题、TF、`/clock` | `integration` |
| T-06 | 分布式 AWSIM + Autoware 固定场景 | `e2e` |
| T-07 | bag 录制、真值和回放 | `e2e` |
| T-08 | 连续运行和服务恢复 | `soak` |
| T-09 | Isaac Sim 6.0.1 容器 | 长期路线 `local` |
| T-10 | Isaac Sim + Autoware Adapter | 长期路线 `integration` |

AWSIM ARM64 不列入首轮测试矩阵，状态固定为 `BLOCKED`，直到第 3.4 节的
重新评估条件全部具备。

`scripts/harness/run.sh` 只接受 `host`、`gpu`、`network`、`clock`、
`runtime`、`ros` 和 `compose` 等原子检查名。上表的 `T-*` 是聚合测试，
必须汇总相关原子证据后人工或由后续 Gate 汇总器判定，不能把单个原子检查
直接命名为 `T-*`。

### 10.3 测试参数锁定

在 T-00 前填写以下参数。参数为空时，相关测试只能标记为 `NOT-RUN`，不能标记为 `PASS`：

| 参数 | 用途 |
|---|---|
| `HOST_ARCH` | 主机架构 |
| `SIM_HOST_ADDR` | x86_64 AWSIM 主机地址 |
| `DGX_HOST_ADDR` | DGX Spark 地址 |
| `IMAGE_DIGESTS` | 所有正式镜像的 digest |
| `ROS_DISTRO` | ROS 2 发行版 |
| `RMW_IMPLEMENTATION` | ROS 中间件实现 |
| `ROS_DOMAIN_ID` | ROS 域隔离 |
| `TIME_SYNC_MAX_OFFSET_MS` | 两台主机允许的最大时间偏差 |
| `MIN_NETWORK_THROUGHPUT_MBPS` | 最小可用网络吞吐量 |
| `MAX_NETWORK_JITTER_MS` | 最大网络抖动 |
| `CAMERA_RATE_HZ` | 相机目标频率 |
| `LIDAR_RATE_HZ` | LiDAR 目标频率 |
| `MAX_TOPIC_DROP_RATE` | 允许的最大话题丢包率 |
| `MAX_TIMESTAMP_GAP_MS` | 允许的最大时间戳间隔 |
| `MAX_E2E_LATENCY_MS` | 端到端延迟预算 |
| `MAX_MODEL_ERROR` | 模型转换误差阈值 |
| `SOAK_DURATION_MIN` | 连续运行时长 |
| `BAG_RETENTION_DAYS` | 原始 bag 保留周期 |

传感器频率和延迟预算必须来自实际 sensor kit 与业务要求，不使用“看起来正常”作为验收标准。

## 11. 验收门槛

### Gate 0：主机

- 两台主机 GPU 容器正常。
- DGX Spark ARM64 与 AWSIM 主机 x86_64 架构确认。
- 两台主机 Docker Compose 正常。
- 两端数据目录可写。
- 网络和时间同步达到锁定阈值。

### Gate 1：仿真

- AWSIM 在 x86_64 RTX 主机启动。
- 传感器数据持续发布。
- ground truth 可获取。
- 车辆控制指令生效。
- Scenario Simulator 在 DGX Spark 完成单机基线。

### Gate 2：Autoware

- 地图加载成功。
- 定位正常。
- 规划成功。
- 车辆能够完成固定路线。

### Gate 3：数据

- bag 正常生成。
- bag 可以回放。
- 真值与传感器可以对齐。
- 数据可以转换为训练格式。

### Gate 4：模型

- BEVFormer forward 成功。
- TensorRT engine 构建成功。
- 推理结果与 PyTorch 基线一致。

### Gate 5：闭环

- 模型接入 Autoware。
- 固定场景回归通过。
- 延迟和内存占用在预算内。
- 连续运行无明显资源泄漏。

## 12. 阻断条件

出现以下任一情况时，停止继续扩展服务，先处理阻断问题：

- AWSIM 二进制架构与 x86_64 仿真主机不匹配。
- 容器无法访问 GPU。
- 跨主机带宽、抖动、丢包或时间同步不达标。
- ROS 话题能发现但消息无法稳定传输。
- `/clock`、TF 或传感器时间戳不正确。
- bag 不能完整关闭或回放。
- 真值无法与传感器样本对齐。
- Autoware 固定场景无法重复完成。
- 训练容器和推理容器使用了无法复现的依赖版本。

## 13. 连续运行和重复运行

闭环评估不只运行一次。至少执行：

1. 同一场景重复运行，确认结果可重复。
2. 多个场景运行，确认不是单场景偶然成功。
3. 连续运行 30 至 60 分钟，观察资源是否持续增长。
4. 重启单个服务，确认 Compose 可以恢复。
5. 中断录制后重新启动，确认数据目录不会互相覆盖。

达到 `e2e` 之前，不宣称系统已经跑通；达到 `soak` 之前，不将其作为长期批量采集服务。

## 14. 主要风险和处理方式

| 风险 | 处理方式 |
|---|---|
| AWSIM 无 ARM64 构建 | 已采用 x86 AWSIM + DGX Spark 正式主路径 |
| AWSIM 源码无法在 ARM64 构建 | 不进入实施；使用 Scenario Simulator 或 Isaac Sim 路线 |
| 跨主机网络不足 | Recorder 放在 AWSIM 源端，降低负载或升级到 10 GbE |
| 双主机时间偏差 | 配置 NTP/PTP，未达阈值时停止数据采集 |
| Humble/Jazzy 不兼容 | 统一 ROS 版本或使用 Zenoh Bridge |
| GPU 扩展无法编译 | 建立独立 ARM64 训练镜像，逐个验证依赖 |
| 图形渲染失败 | 切换 X11、Wayland 或 headless 模式 |
| 统一内存耗尽 | 限制 batch、worker、缓存和并发容器 |
| DDS 发现失败 | 首轮使用 host network，配置固定 Domain ID |
| bag 文件过大 | 话题白名单、分场景录制、独立磁盘 |
| TensorRT engine 不兼容 | 固定 GPU、CUDA、TensorRT 和 ONNX 版本 |
| RL 策略不安全 | 增加碰撞检查和规则安全约束层 |
| Isaac Sim 与 Autoware 接口不完整 | 将 Adapter 作为独立项目，通过后再替代 AWSIM |
| `latest` 镜像发生变化 | 固定 tag 和 digest，保存镜像清单 |

## 15. Harness 审查后的执行顺序

```text
断言登记
  -> 双主机 local 验证
  -> 两端镜像 local 验证
  -> AWSIM x86_64 local 验证
  -> Scenario Simulator 单机 e2e 基线
  -> 跨主机网络和 ROS integration 验证
  -> 分布式 Autoware e2e 验证
  -> 数据 e2e 验证
  -> 模型 integration 验证
  -> 固定场景回归
  -> 连续运行 soak 验证
```

每一步都必须产生证据，不能以“容器启动成功”代替“功能验证成功”。

## 16. 当前建议

第一步只做以下工作：

```text
1. 在 DGX Spark 验证 ARM64 Autoware CUDA 容器
2. 在 DGX Spark 跑通 Scenario Simulator 单机基线
3. 在 x86_64 RTX 主机验证官方 AWSIM 发布物
4. 验证 10 GbE、CycloneDDS 和时间同步
5. 跑通分布式 AWSIM + Autoware 控制闭环
6. 根据带宽结果确定 Recorder 部署位置
7. 录制并回放第一份带真值 bag
8. 形成第一份数据质量和系统性能报告
```

在 Gate 3 通过之前，不进入 BEVFormer 正式训练；在 Gate 4 通过之前，不接入 TensorRT 和 RL。

AWSIM ARM64 源码构建不属于第一版行动项。Isaac Sim 6.0.1 在上述主路径稳定后，
以独立 profile 和 Adapter 项目启动。

## 17. 参考依据

所有外部结论均以文档基线日期能访问到的官方文档、官方仓库和官方发布物为
`source` 证据；上机前仍需重新锁定版本和 digest。

- NVIDIA DGX Spark 软件栈：
  <https://docs.nvidia.com/dgx/dgx-spark/software.html>
- NVIDIA DGX Spark Container Runtime：
  <https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html>
- Autoware Docker Installation：
  <https://docs.autoware.org/main/installation/autoware/docker-installation/>
- Autoware Docker README：
  <https://github.com/autowarefoundation/autoware/blob/main/docker/README.md>
- AWSIM 官方仓库：
  <https://github.com/autowarefoundation/AWSIM>
- AWSIM 官方发布物：
  <https://github.com/autowarefoundation/AWSIM/releases>
- AWSIM 使用的 RGLUnityPlugin：
  <https://github.com/RobotecAI/RGLUnityPlugin>
- AWSIM ROS Bridge：
  <https://github.com/tier4/awsim_ros_bridge>
- BEVFormer 官方仓库和原始依赖基线：
  <https://github.com/fundamentalvision/BEVFormer>
- PyTorch Previous Versions：
  <https://pytorch.org/get-started/previous-versions/>
- Unity 6 System Requirements：
  <https://docs.unity3d.com/6000.0/Documentation/Manual/system-requirements.html>
- Open AD Kit Scenario Simulation：
  <https://autowarefoundation.github.io/openadkit/deployments/samples/scenario-simulation/>
- Isaac Sim Requirements：
  <https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html>
- Isaac Sim DGX Spark 安装说明：
  <https://docs.isaacsim.omniverse.nvidia.com/6.0.0/installation/install_dgx_spark.html>
- Isaac Lab 官方仓库与版本发布：
  <https://github.com/isaac-sim/IsaacLab/releases>
- Isaac Lab 安装说明：
  <https://isaac-sim.github.io/IsaacLab/develop/source/setup/installation/index.html>
- NAVSIM 官方仓库：
  <https://github.com/autonomousvision/navsim>
- NAVSIM 安装说明：
  <https://github.com/autonomousvision/navsim/blob/main/docs/install.md>
- NAVSIM cache、数据 split、Agent 和 metrics 说明：
  <https://github.com/autonomousvision/navsim/tree/main/docs>

## 18. 实现文件与当前状态

方案已经在 `my-ad` 中落成 Compose 工程骨架。当前已增加：

```text
compose.sim-x86.yaml
compose.dgx.yaml
.env.example
images/
  dataset-converter/
  navsim/
config/
  cyclone/
  zenoh/
  autoware/
scripts/
  preflight/
  harness/
  collect/
  replay/
  ops/
  start-navsim.sh
artifacts/
third_party/
docs/
```

当前 Compose 已提供的可执行服务包括：

| Compose 文件 | 服务或 profile | 作用 |
|---|---|---|
| `compose.sim-x86.yaml` | `awsim` | x86_64 RTX 主机上的 AWSIM 入口 |
| `compose.sim-x86.yaml` | `collect` | ground truth 入口和场景元数据 |
| `compose.sim-x86.yaml` | `record-source` | 网络不足时在 AWSIM 源端录制 ROS 2 bag |
| `compose.dgx.yaml` | `autoware` | DGX Spark ARM64 Autoware 入口 |
| `compose.dgx.yaml` | `record` | ROS 2 bag 录制 |
| `compose.dgx.yaml` | `replay` | ROS 2 bag 回放 |
| `compose.dgx.yaml` | `harness` | ROS 2 必需话题发现探针 |
| `compose.dgx.yaml` | `viz` | Foxglove Bridge |
| `compose.dgx.yaml` | `scenario` | DGX 单机 Scenario Simulator |
| `compose.dgx.yaml` | `isaac` | Isaac Sim 长期替代路线 |
| `compose.dgx.yaml` | `data` | 中间数据 manifest 转换器 |
| `compose.dgx.yaml` | `deploy` | TensorRT 构建/部署入口 |
| `compose.dgx.yaml` | `navsim` | NAVSIM 离线规划评测/模型迭代 Job |

这些服务均通过 `.env` 注入镜像和启动命令。AWSIM、Autoware、Scenario
Simulator、Isaac Sim、Foxglove 和 TensorRT 的具体镜像仍必须在目标机器上
锁定 tag、digest 和实际启动命令后，才能进入运行态验证。

当前实现已完成：

- 双主机 Compose 文件静态校验。
- DGX 和 x86 主机的角色、网络、GPU、持久化目录定义。
- ROS 2、CycloneDDS、Zenoh、录包、回放和 profile 边界。
- 宿主机预检、Compose 配置展开和基础 Harness。
- 场景元数据写入和数据 manifest 转换器。
- NAVSIM 的 ARM64 profile、源码构建入口、数据/地图挂载和专属 Harness。
- Dockerfile、环境变量模板、Makefile 和运行手册。

当前仍需在目标机器补齐或验证：

- 真实 AWSIM x86_64 镜像、启动参数和 ground truth 消息适配器。
- 真实 ARM64 Autoware 镜像、地图、车辆模型和 sensor kit 启动命令。
- Scenario Simulator、Isaac Sim、Foxglove 和 TensorRT 的锁定镜像。
- NAVSIM ARM64 镜像、Python/GIS 依赖、官方数据和最小 baseline 评测。
- 完整 nuScenes 转换、BEVFormer 训练、ONNX/TensorRT 和 RL 实现。
- AWSIM/ROS bag 到 NAVSIM OpenScene 的数据适配器，以及 NAVSIM trajectory
  到 Autoware 的在线 planner adapter。
- 双主机网络、DDS/Zenoh、传感器频率、真值对齐和闭环指标。

实现文件必须遵循：

- 镜像固定 tag 和 digest，不使用未锁定的 `latest`。
- 所有源码二开通过 bind mount 或独立 Dockerfile 管理。
- 数据、模型、日志和 Harness 证据全部落在宿主机持久化目录。
- 主机特定路径和地址只写入 `.env`，不硬编码到 Compose。
- 核心服务提供 healthcheck；GPU、网络、存储和日志策略按目标机器的实际
  资源配置补齐，不把未验证的限制值伪装成生产基线。
- 每一阶段实现后运行对应 Harness，结果只允许
  `PASS/FAIL/BLOCKED/NOT-RUN`。
