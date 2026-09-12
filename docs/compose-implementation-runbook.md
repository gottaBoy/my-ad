# Compose 实施运行手册

## 1. 角色分工

同一份仓库复制到两台机器：

| 主机 | `.env` | Compose | 主要服务 |
|---|---|---|---|
| x86_64 RTX | `HOST_ROLE=sim-x86` | `compose.sim-x86.yaml` | AWSIM、ground truth、场景元数据 |
| DGX Spark | `HOST_ROLE=dgx` | `compose.dgx.yaml` | Autoware、录包、回放、NAVSIM、验证、部署入口 |

两端必须使用相同的 `ROS_DOMAIN_ID`、ROS 发行版和 RMW。主路径默认使用
CycloneDDS；跨发行版或需要路由隔离时才启用 Zenoh。

## 2. 镜像契约

Compose 不负责猜测外部项目的镜像 tag。`.env` 必须提供：

- `AWSIM_IMAGE`：包含官方 x86_64 Linux Player。
- `AUTOWARE_IMAGE`：包含已验证的 ARM64 Autoware。
- `ROS_BASE_IMAGE`：与 Recorder 的 ROS 发行版一致。
- `FOXGLOVE_IMAGE`：包含 `foxglove_bridge`。
- `SCENARIO_IMAGE`：包含 Scenario Simulator。
- `ISAAC_IMAGE`：包含与 DGX Spark 匹配的 Isaac Sim。
- `TENSORRT_IMAGE`：与导出模型和目标 runtime 匹配。
- `NAVSIM_IMAGE`：包含已在 DGX Spark 验证的 ARM64 NAVSIM 环境。

正式部署时所有值都要固定 tag 和 digest。没有 digest 的值只允许用于
预检阶段，不能用于数据生产或模型基线。

外部运行镜像必须包含 `bash` 和 `/usr/bin/env`，因为 Compose 挂载的启动与
健康检查包装脚本依赖它们。每个运行服务必须分别配置启动命令和正向业务探针：

```text
<SERVICE>_COMMAND
<SERVICE>_HEALTHCHECK_COMMAND
```

健康探针必须检查 ROS 节点、必需话题、生命周期状态或服务端点；只检查 PID
存在不能作为集成通过证据。

Recorder、Replay 和 ROS Probe 使用仓库内的
`images/ros-tools/Dockerfile`。该镜像安装 CycloneDDS RMW、rosbag2 和 MCAP
存储插件；x86 源端 Recorder 复用同一 Dockerfile 并构建 amd64 变体。首次
构建需要可访问 ROS 软件源或已配置内部镜像源。

AWSIM 推荐把官方 x86_64 Linux 发布包封装成内部 amd64 运行镜像，再将
固定 tag 和 digest 写入 `AWSIM_IMAGE`。这属于发布包容器化，不等于在 ARM64
上源码构建 AWSIM。当前工程不提供未经验证的 Unity/原生插件 ARM64 构建链。

NAVSIM 使用独立的 `navsim` profile 和一次性 Job。优先使用内部验证后的
ARM64 镜像；需要二开时可通过 `images/navsim/Dockerfile` 从固定 git ref
构建。官方依赖包含旧版 PyTorch 和 GIS 二进制包，源码 Dockerfile 只是构建
入口，不代表 ARM64/CUDA 13 已验证。详细步骤见
[`navsim-integration.md`](navsim-integration.md)。不同运行模式以及 NAVSIM、
CARLA、BEVFormer、Isaac Lab、RL 的分级执行路线见
[`simulation-modes.md`](simulation-modes.md)。

## 3. 运行证据

每个测试至少保存：

```text
artifacts/<test-id>/<UTC时间>-<主机>/
  host-info/
  image-info/
  compose-config/
  logs/
  metrics/
  decision.md
```

服务显示 `running` 不等于系统通过。必须另外验证：

- GPU 能力。
- ROS 话题、QoS、TF 和 `/clock`。
- 传感器频率和丢包。
- 真值时间戳对齐。
- 控制指令往返。
- bag 关闭、回放和重复运行。
- NAVSIM 的数据版本、Agent 配置、checkpoint、评测报告和重复运行结果。

## 4. Gate 执行顺序

目标机器按以下顺序执行，前一 Gate 不是 `PASS` 时停止：

```bash
make init
make test-local
make test-compose
make preflight
make harness-host
make harness-gpu
make harness-network
make harness-clock
```

网络 Harness 依赖对端运行 `iperf3 -s -p 5201`。时钟 Harness 依赖
`chronyc tracking`，不能仅凭 `timedatectl` 的 synchronized 布尔值判断
两台主机满足毫秒级偏差阈值。

核心服务启动后执行：

```bash
make harness-runtime SERVICE=awsim
make harness-runtime SERVICE=autoware
make harness-ros
```

没有 x86_64 AWSIM 主机时，在 DGX Spark 使用 Scenario Simulator：

```bash
make scenario-prepare
make scenario
```

Scenario Simulator 使用 `scenario_simulation:=true` 启动 Autoware，并使用
`launch_autoware:=false` 启动外部场景解释器；它验证的是场景级 ROS 2 交互，
不等于 AWSIM 的相机、LiDAR 和 Unity 渲染闭环。

需要观察场景运行期间的 ROS 话题时，在另一个终端执行：

```bash
make harness-ros-scenario
make inspect-modules
```

`make scenario` 是一次性前台运行，场景成功退出后容器会停止；不要把场景
结束后才执行的 discovery probe 当成场景运行期间的 ROS 证据。

NAVSIM 是独立离线 Gate，不依赖 AWSIM/Autoware 同时运行：

```bash
make harness-navsim
make navsim-cache
make navsim
```

`harness-navsim` 只证明镜像、源码、数据、地图和 import 契约通过。
`make navsim` 成功退出并生成可追溯评测报告后，才能判定最小评测通过。

网络 Gate 不满足远端高带宽录包要求时，在 x86_64 主机执行：

```bash
make build-tools-sim
make record-sim
```

`make harness-ros` 读取 `config/harness/required-topics.txt`。实际 sensor kit
确定后必须修改该文件，否则只能验证示例话题契约。

这些命令是原子检查，不直接等同于方案中的聚合测试编号。比如 `T-00`
要求两台主机分别完成 `harness-host`、GPU 和目录等前置检查；`T-05`
要求网络、时钟、ROS 话题、TF 和 `/clock` 的证据全部存在。不得用单个原子
检查的 `PASS` 代替整组 Gate 结论。

## 5. 失败处理

任何前置项失败时，结果标为 `BLOCKED`，不要继续启动训练、TensorRT 或 RL。

典型处理：

| 失败 | 处理 |
|---|---|
| AWSIM ARM64 不可用 | 使用 x86_64 AWSIM + DGX Spark 主路径 |
| DGX 无 GPU 容器能力 | 修复驱动和 NVIDIA Container Toolkit |
| DDS 发现失败 | 检查 host network、接口、Domain ID 和 CycloneDDS |
| 大消息丢包 | 录包移到 x86 源端或升级网络 |
| bag 无法回放 | 检查 storage plugin、QoS 和完整关闭流程 |
| BEVFormer 扩展无法编译 | 锁定依赖矩阵并单独构建 ARM64 训练镜像 |
| NAVSIM 官方依赖无法在 ARM64 安装 | 锁定基础镜像和源码 ref，建立内部 requirements lock，保留失败证据 |
| NAVSIM 无法读取 AWSIM bag | 停止直连假设，先实现 ROS/真值到 OpenScene/NAVSIM schema 的适配器 |

## 6. DGX Spark 实机操作顺序

以下步骤是 2026-09-10 记录的目标机落地方法。执行时必须在 DGX Spark
上逐个 Gate 验证；前一个 Gate 不是 `PASS` 时停止，不得用 Compose 静态
展开结果替代容器实机结果。

### 6.1 获取仓库和确认主机

在 DGX Spark 上获取本仓库并初始化目录：

```bash
cd ~
git clone <仓库地址> my-ad
cd my-ad

cp .env.example .env
make init
```

如果仓库已经复制到 DGX Spark，只执行：

```bash
cd ~/my-ad
make init
```

检查目标机：

如果需要收集主机信息用于配置 `.env`，先运行：

```bash
make collect-env
```

报告输出到终端和 `artifacts/env-report/`，包含架构、OS、Docker、GPU、
容器 GPU 访问、内存、磁盘、网络接口和 `.env` 占位符计数。

确认以下关键项：

```bash
uname -m
docker info --format '{{.Architecture}}'
docker compose version
nvidia-smi
```

DGX Spark 预期为 `aarch64`，Docker 预期为 `arm64` 或 `aarch64`，并且
`nvidia-smi` 能看到可用 GPU。

### 6.2 配置 DGX `.env`

`.env.example` 默认包含 2026-09-11 已在 DGX Spark 验证通过的核心配置：

```dotenv
HOST_ROLE=dgx
PREFLIGHT_SCOPE=core
MAPS_DIR=./data/maps/sample-map-planning
```

Autoware ARM64 镜像、planning simulator 命令、业务健康探针和 GPU smoke
镜像也已提供默认值。只需确认以下地图文件存在：

```text
data/maps/sample-map-planning/lanelet2_map.osm
data/maps/sample-map-planning/pointcloud_map.pcd
```

AWSIM、Foxglove、Isaac、NAVSIM、TensorRT 和 ground-truth 的 `REPLACE_*`
配置属于可选 profile，不影响 DGX 核心 preflight 和 planning baseline。

启用 NAVSIM 时，建议保留以下保守运行参数：

```dotenv
NAVSIM_SPLIT=mini
NAVSIM_NUM_WORKERS=1
NAVSIM_BATCH_SIZE=1
NAVSIM_RUN_ID=baseline-20260910
NAVSIM_NETWORK_MODE=none
```

必须补齐以下 NAVSIM 项，不能保留 `REPLACE_`：

```dotenv
NAVSIM_IMAGE=<已验证的ARM64镜像>@sha256:<digest>
NAVSIM_GIT_REF=<固定tag或commit>
NAVSIM_COMMAND=<固定NAVSIM版本对应的官方评测命令>
NAVSIM_HEALTHCHECK_COMMAND=<import、数据和地图检查命令>
```

`NAVSIM_COMMAND` 应根据锁定源码中的官方入口填写。当前 metric cache
入口为：

```text
/workspace/navsim/scripts/evaluation/run_metric_caching.sh
```

NAVSIM 使用 `network_mode=none` 时，数据、地图、checkpoint 和 Python
缓存必须事先准备在宿主机；运行期间不会依赖外网下载文件。

### 6.3 准备固定 NAVSIM 源码和数据

源码必须固定到可追溯 ref，不直接使用未固定的 `main`：

```bash
git clone https://github.com/autonomousvision/navsim.git third_party/navsim
cd third_party/navsim
git checkout <固定tag或commit>
git rev-parse HEAD
cd ../..
```

官方数据按对应 NAVSIM 版本和许可证准备到：

```text
data/navsim/dataset/
├── maps/
├── navsim_logs/
└── sensor_blobs/
```

确认目录确实有内容：

```bash
find data/navsim/dataset/maps -mindepth 1 -print -quit
find data/navsim/dataset/navsim_logs -mindepth 1 -print -quit
find data/navsim/dataset/sensor_blobs -mindepth 1 -print -quit
```

数据下载命令、split 名称和目录结构以锁定 NAVSIM ref 的官方文档为准；
不能用空目录或自造文件通过数据 Gate。

### 6.4 执行 NAVSIM Gate

先执行静态和主机检查：

```bash
make test-local
ENV_FILE=.env make test-compose
make preflight
make harness-host
```

再执行 NAVSIM 检查：

```bash
make harness-navsim
```

`harness-navsim` 通过后，生成 metric cache：

```bash
make navsim-cache
```

最后运行最小 baseline：

```bash
make navsim
find data/reports/navsim -maxdepth 3 -type f -print
```

评测报告至少要能回溯到：

```text
NAVSIM image digest
NAVSIM git ref
dataset checksum/version
map version
split
Agent configuration
checkpoint checksum
random seed
```

如果没有可用的已验证 ARM64 预构建镜像，再采用源码构建：

```dotenv
NAVSIM_IMAGE=my-ad/navsim:dgx-spark-fixed
NAVSIM_BASE_IMAGE=<已验证的linux/arm64、Python3.9、CUDA/PyTorch基础镜像>
NAVSIM_GIT_REF=<固定commit>
```

然后执行：

```bash
make build-navsim
make harness-navsim
```

源码构建或官方依赖在 ARM64 上失败时，结果记录为 `BLOCKED`，不能用
QEMU 或 x86_64 结果代替 DGX Spark 通过。

### 6.5 启动 Autoware 和 AWSIM 主链路

DGX 核心默认值无需再次填写。生产部署或替换地图、车辆、sensor kit 时，
再覆盖 `AUTOWARE_IMAGE`、`AUTOWARE_COMMAND` 和健康探针并重新验证。

在 DGX Spark 执行：

```bash
make preflight
make harness-gpu
make harness-network
make harness-clock
make build-tools
make up-dgx
make harness-runtime SERVICE=autoware
make harness-ros
```

AWSIM 默认运行在独立的 x86_64 RTX 仿真主机，不作为 DGX Spark ARM64
服务启动。x86 主机使用独立 `.env`，设置：

```dotenv
HOST_ROLE=sim-x86
```

然后执行：

```bash
make preflight
make harness-host
make harness-gpu
make up-sim
make harness-runtime SERVICE=awsim
```

两台主机的 `ROS_DOMAIN_ID`、ROS 发行版、RMW、`SIM_HOST_ADDR` 和
`DGX_HOST_ADDR` 必须一致或正确指向对端。网络和时钟 Gate 在两端都通过后，
再开始传感器数据采集。

### 6.6 录包、回放和停止

根据实际 sensor kit 修改：

```text
config/record/topics.txt
config/harness/required-topics.txt
```

DGX 录包：

```bash
make record
```

网络无法稳定承载大点云时，在 x86 源端录包：

```bash
make build-tools-sim
make record-sim
```

停止服务：

```bash
make down-dgx
make down-sim
```

每次运行都保留 `artifacts/<test-id>/<UTC时间>-<主机>/` 下的 Compose、
镜像、日志、指标和决策文件。

### 6.7 当前结论边界

以下结论不能互相替代：

```text
Compose 展开成功       != DGX Spark 实机通过
容器启动成功           != NAVSIM 评测通过
NAVSIM 评测成功        != Autoware 在线闭环通过
ROS 2 bag 存在         != NAVSIM 数据集已经生成
```

AWSIM ROS 2 bag 到 NAVSIM OpenScene 的转换，以及 NAVSIM trajectory 到
Autoware ROS 2 trajectory 的适配器，当前仍为 `BLOCKED`，需要单独实现和
验证。
