# my-ad

DGX Spark 自动驾驶闭环的 Docker Compose 工程骨架。

该仓库采用两台主机的正式主路径：

```text
x86_64 NVIDIA RTX 主机
  AWSIM + ground truth + 场景元数据
          |
          | CycloneDDS 或 Zenoh，优先有线 10 GbE
          v
DGX Spark ARM64
  Autoware + Recorder + Replay + 数据处理 + TensorRT
```

DGX Spark 单机验证使用 `Scenario Simulator`，长期高保真替代路线使用
Isaac Sim。AWSIM ARM64 源码构建没有作为默认实现，因为官方 AWSIM 发布物、
Unity Linux Player 和原生插件的架构约束尚未形成可复现的 ARM64 构建链。

## 目录

```text
compose.sim-x86.yaml       # x86_64 RTX 主机
compose.dgx.yaml           # DGX Spark ARM64
.env.example               # 环境变量模板
config/                    # ROS、CycloneDDS、Zenoh、录包配置
images/                    # 可在本仓库构建的辅助镜像
scripts/                   # 预检、录包、回放和 Harness
data/                      # 宿主机持久化数据
artifacts/                 # Harness 证据
docs/                      # 方案和验证文档
```

## 前置条件

### DGX Spark

- ARM64 系统。
- NVIDIA 驱动、Docker Engine、Docker Compose v2。
- NVIDIA Container Toolkit。
- 已验证的 ARM64 Autoware CUDA 镜像。
- 足够的 NVMe 或外部存储。

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

编辑 `.env`：

- x86_64 主机设置 `HOST_ROLE=sim-x86`。
- DGX Spark 设置 `HOST_ROLE=dgx`。
- 替换全部 `REPLACE_` 镜像和命令。
- 固定正式镜像的 tag 和 digest。
- 两台主机使用相同的 `ROS_DOMAIN_ID`、ROS 发行版和 RMW。
- 配置 `SIM_HOST_ADDR`、`DGX_HOST_ADDR`。

检查未替换项：

```bash
rg 'REPLACE_' .env
```

没有输出后才进入预检。

## 预检和 Compose 校验

```bash
make preflight
make test-compose
```

`make test-compose` 只做 Compose 静态展开，不代表容器、GPU、ROS 图或
AWSIM 已经验证通过。

## 启动顺序

### 1. x86_64 主机启动 AWSIM

```bash
make up-sim
```

确认：

```bash
docker compose --env-file .env -f compose.sim-x86.yaml ps
docker compose --env-file .env -f compose.sim-x86.yaml logs -f awsim
```

### 2. DGX Spark 启动 Autoware

```bash
make up-dgx
```

`AUTOWARE_COMMAND` 必须是所选 Autoware 镜像中已验证的启动命令。仓库不会
猜测地图、车辆模型、sensor kit 或 launch 文件名。

### 3. 启动可选服务

```bash
# DGX 上录包
make record

# DGX 上回放，先设置 REPLAY_BAG=/data/bags/<run>
make replay

# 构建并运行内部中间数据 manifest 转换器
make data

# 可视化
docker compose --env-file .env -f compose.dgx.yaml --profile viz up -d foxglove-bridge
```

Recorder 使用 `config/record/topics.txt` 白名单，不会默认录制全部 ROS 话题。
正式采集前必须根据实际 sensor kit 修改话题清单，并保存对应的场景元数据。

## Compose Profiles

```text
record    DGX Recorder
replay    ROS 2 bag 回放
viz       Foxglove Bridge
scenario  DGX Scenario Simulator
isaac     Isaac Sim 长期替代路线
data      中间格式 manifest 转换器
deploy    TensorRT 构建容器
```

使用 profile 前必须先在 `.env` 配置对应镜像和启动命令。

## 停止服务

```bash
make down-dgx
make down-sim
```

## Harness 证据

```bash
./scripts/harness/run.sh host
./scripts/harness/run.sh gpu
./scripts/harness/run.sh network
./scripts/harness/run.sh compose
```

运行时证据写入 `artifacts/`。只有拿到容器日志、ROS 话题快照、bag 元数据
和固定场景指标后，才能将测试标记为 `PASS`、`integration` 或 `e2e`。

## 当前边界

以下内容已定义接口，但必须在目标机器锁定依赖后实现或接入：

- AWSIM ground truth 消息到内部标注格式的具体适配器。
- 完整 nuScenes 数据转换。
- BEVFormer 的 ARM64/CUDA 13 依赖移植。
- ONNX/TensorRT engine 构建和数值一致性验证。
- Isaac Sim 到 Autoware 的 sensor adapter。
- PPO/RL 训练与安全约束层。

仓库中的 `dataset-converter` 当前只生成可复现的输入文件 manifest，不冒充
完整 nuScenes 转换器。这样可以先验证数据目录、校验值和流水线边界，再锁定
具体消息 schema 后补齐转换逻辑。
