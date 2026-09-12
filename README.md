# my-ad

DGX Spark 自动驾驶闭环的 Docker Compose 部署验证基线。

该仓库采用两台主机的正式主路径：

```text
x86_64 NVIDIA RTX 主机
  AWSIM + ground truth + 场景元数据
          |
          | CycloneDDS 或 Zenoh，优先有线 10 GbE
          v
DGX Spark ARM64
  Autoware + Recorder + Replay + 数据处理 + TensorRT
  NAVSIM 离线规划评测/模型迭代
```

DGX Spark 单机验证使用 `Scenario Simulator`，长期高保真替代路线使用
Isaac Sim。AWSIM ARM64 源码构建没有作为默认实现，因为官方 AWSIM 发布物、
Unity Linux Player 和原生插件的架构约束尚未形成可复现的 ARM64 构建链。
NAVSIM 是独立的离线规划评测路线，不读取 ROS 2 bag，也不替代 AWSIM、
Autoware 在线闭环或 Scenario Simulator。

不同运行模式以及 NAVSIM、CARLA、BEVFormer、Isaac Lab、RL 的分级执行路线见
[`docs/simulation-modes.md`](docs/simulation-modes.md)。

## 目录

```text
compose.sim-x86.yaml       # x86_64 RTX 主机
compose.dgx.yaml           # DGX Spark ARM64
.env.example               # 环境变量模板
config/                    # ROS、CycloneDDS、Zenoh、录包配置
images/                    # 可在本仓库构建的辅助镜像
third_party/navsim/        # 固定 ref 的 NAVSIM 源码，不提交第三方源码
scripts/                   # 预检、录包、回放和 Harness
tests/                     # 确定性数据和 Harness 解析器测试
data/                      # 宿主机持久化数据
artifacts/                 # Harness 证据
docs/                      # 方案和验证文档
```

按 sensing、localization、perception、fusion、planning、control 逐级学习
现有 demo，见 [`docs/autoware-learning-lab.md`](docs/autoware-learning-lab.md)。

## 前置条件

### DGX Spark

- ARM64 系统。
- NVIDIA 驱动、Docker Engine、Docker Compose v2。
- NVIDIA Container Toolkit。
- 已验证的 ARM64 Autoware CUDA 镜像。
- 足够的 NVMe 或外部存储。
- 启用 NAVSIM 时准备 OpenScene/nuPlan 数据、地图和可追溯 checkpoint。

### x86_64 仿真主机

- x86_64 Linux。
- NVIDIA RTX GPU 和驱动。
- Docker Engine、Docker Compose v2、NVIDIA Container Toolkit。
- 已验证的 AWSIM x86_64 Linux Player 镜像。
- 与 DGX Spark 的有线网络连接。

## 初始化

在每台主机分别获取本仓库，并执行：

```bash
cp .env.example .env
make init
```

`.env.example` 默认就是已在 DGX Spark 验证过的 ARM64 核心基线：

- `HOST_ROLE=dgx`、`PREFLIGHT_SCOPE=core`。
- 固定 digest 的 Autoware CUDA 镜像和 GPU smoke 镜像。
- headless `planning_simulator` 启动命令和 `/map/vector_map` 健康探针。
- `MAPS_DIR=./data/maps/sample-map-planning`。

将 `lanelet2_map.osm` 和 `pointcloud_map.pcd` 放入上述目录后，DGX 核心路径
不需要再修改 `.env`。其他情况按需编辑：

- x86_64 主机设置 `HOST_ROLE=sim-x86`，并配置 AWSIM 相关 `REPLACE_*` 项。
- 启用 Foxglove、Isaac、NAVSIM、TensorRT 或 ground-truth profile 前，
  替换该 profile 对应的 `REPLACE_*` 项。
- 固定正式镜像的 tag 和 digest。
- 两台主机使用相同的 `ROS_DOMAIN_ID`、ROS 发行版和 RMW。
- 运行跨主机 Gate 时，配置真实的 `SIM_HOST_ADDR`、`DGX_HOST_ADDR`。

`.env` 按普通 `KEY=value` 解析，不作为 Shell 执行。包含空格的命令必须使用
单引号或双引号包围。

DGX 核心路径可以保留未启用 profile 的占位项。正式全链路部署前检查：

```bash
rg 'REPLACE_' .env
```

生产模式要求核心镜像使用 digest：

```text
DEPLOYMENT_MODE=production
AUTOWARE_IMAGE=<registry>/<image>:<tag>@sha256:<digest>
```

## Gate 0：静态和宿主机预检

如果需要收集 DGX Spark 主机信息（架构、GPU、Docker、磁盘、网络）用于
配置 `.env`，先运行：

```bash
make collect-env
```

报告同时输出到终端和 `artifacts/env-report/`。把报告内容发回给开发者，
可以快速确认哪些镜像和命令需要配置。

```bash
make test-local
make test-compose
make preflight
make harness-host
make harness-gpu
```

`make test-compose` 只做 Compose 静态展开，不代表容器、GPU、ROS 图或
AWSIM 已经验证通过。只有以上命令在目标机器得到 `PASS` 后，才启动核心服务。

网络测试需要在对端先运行：

```bash
iperf3 -s -p 5201
```

然后在当前主机执行：

```bash
make harness-network
make harness-clock
```

## 启动顺序

### 1. x86_64 主机启动 AWSIM

```bash
make up-sim
```

确认：

```bash
docker compose --env-file .env -f compose.sim-x86.yaml ps
docker compose --env-file .env -f compose.sim-x86.yaml logs -f awsim
make harness-runtime SERVICE=awsim
```

### 2. DGX Spark 启动 Autoware

```bash
make build-tools
make up-dgx
make harness-runtime SERVICE=autoware
make harness-ros
make inspect-modules
# 只观察一个模块时：
make inspect-modules MODULE=localization
make inspect-modules MODULE=planning
make inspect-modules MODULE=control
```

模板中的 `AUTOWARE_COMMAND` 是当前已验证的 sample map、`sample_vehicle`
和 `sample_sensor_kit` 基线。切换生产地图、车辆或 sensor kit 时必须同步
覆盖命令并重新执行 Gate。

`make inspect-modules` 是学习和排障用的 ROS 观察命令，会按模块列出当前发现
的话题和候选处理节点。它不会把缺失的相机、LiDAR 或融合输出误报为完整
感知闭环通过。

### 3. 启动可选服务

```bash
# DGX 上录包
make record

# 网络不足时，在 x86_64 AWSIM 源端构建工具并录包
make build-tools-sim
make record-sim

# x86_64 主机采集 ground truth 和场景元数据
make collect-sim

# DGX 上回放，先设置 REPLAY_BAG=/data/bags/<run>
make replay

# 构建并运行内部中间数据 manifest 转换器
make data

# 可视化、单机 Scenario Simulator、Isaac 和 TensorRT
make viz
make scenario-prepare
make scenario
make isaac
make deploy

# NAVSIM 离线缓存和评测
make harness-navsim
make build-navsim   # 仅在采用本仓库源码构建入口时执行
make navsim-cache
make navsim
```

Recorder 使用 `config/record/topics.txt` 白名单，不会默认录制全部 ROS 话题。
正式采集前必须根据实际 sensor kit 修改话题清单，并保存对应的场景元数据。

## Compose Profiles

```text
record    DGX Recorder
record-source  x86_64 AWSIM 源端 Recorder
replay    ROS 2 bag 回放
harness   ROS 2 发现探针
viz       Foxglove Bridge
scenario  DGX Scenario Simulator
isaac     Isaac Sim 长期替代路线
data      中间格式 manifest 转换器
deploy    TensorRT 构建容器
navsim    NAVSIM 离线规划评测/训练 Job
```

使用 profile 前必须先在 `.env` 配置对应镜像和启动命令。
NAVSIM 的详细数据目录、源码构建、评测命令和 Harness 门槛见
[`docs/navsim-integration.md`](docs/navsim-integration.md)。

## 停止服务

```bash
make down-dgx
make down-sim
```

## Harness 证据

```bash
make harness-host
make harness-gpu
make harness-network
make harness-clock
make harness-runtime SERVICE=autoware
make harness-ros
make harness-navsim
```

每次运行写入独立的
`artifacts/<test-id>/<UTC时间>-<主机>/`，不会覆盖上一轮证据。测试状态只允许
`PASS`、`FAIL`、`BLOCKED`、`NOT-RUN`。`integration`、`e2e` 和 `soak`
是证据等级，不是状态。

当前能否直接部署的结论见
[`docs/landing-readiness-audit.md`](docs/landing-readiness-audit.md)。

## 当前边界

以下内容已定义接口，但必须在目标机器锁定依赖后实现或接入：

- AWSIM ground truth 消息到内部标注格式的具体适配器。
- ROS 2 bag/ground truth 到 NAVSIM OpenScene 数据结构的适配器。
- 完整 nuScenes 数据转换。
- BEVFormer 的 ARM64/CUDA 13 依赖移植。
- ONNX/TensorRT engine 构建和数值一致性验证。
- Isaac Sim 到 Autoware 的 sensor adapter。
- NAVSIM Agent 到 Autoware trajectory 的 planner/inference adapter。
- PPO/RL 训练与安全约束层。

仓库中的 `dataset-converter` 当前只生成可复现的输入文件 manifest，不冒充
完整 nuScenes 转换器。这样可以先验证数据目录、校验值和流水线边界，再锁定
具体消息 schema 后补齐转换逻辑。
