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
[`navsim-integration.md`](navsim-integration.md)。

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
