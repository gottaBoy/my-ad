# NAVSIM 集成与落地方案

校核日期：2026-09-10

## 1. 结论

NAVSIM 在本工程中定位为：

```text
官方 OpenScene/nuPlan 数据
        |
        v
NAVSIM Agent 训练、推理、缓存和离线规划评测
        |
        v
EPDMS/PDM Score、轨迹和实验报告
```

NAVSIM 不替代：

- AWSIM 的实时传感器渲染、真值和车辆控制闭环。
- Autoware 的 ROS 2 在线感知、规划和控制。
- Scenario Simulator 的 Autoware 场景回归。
- Isaac Sim 的高保真单机传感器仿真。

NAVSIM 也不直接读取本仓库的 ROS 2 bag。ROS bag、AWSIM ground truth 和
NAVSIM OpenScene 数据结构之间需要单独的适配器；当前
`dataset-converter` 只生成完整性 manifest，不能冒充 NAVSIM 数据转换器。

## 2. 官方运行基线

官方 NAVSIM 仓库的主分支包含 NAVSIM v2 代码，官方安装文档要求：

- Python 3.9 环境。
- `pip install -e .` 安装 devkit。
- nuPlan 地图。
- OpenScene logs 和 sensor blobs。
- `NUPLAN_MAP_VERSION`、`NUPLAN_MAPS_ROOT`、`NAVSIM_EXP_ROOT`、
  `NAVSIM_DEVKIT_ROOT`、`OPENSCENE_DATA_ROOT` 环境变量。
- 先执行 `scripts/evaluation/run_metric_caching.sh` 生成 metric cache。
- 再使用 `scripts/evaluation/run_cv_pdm_score_evaluation.sh` 或选定的
  v2 评测入口运行 Agent。

官方 requirements 仍包含固定的旧版 PyTorch/Torchvision 约束以及多个
Python/CUDA 扩展依赖。不能因为 DGX Spark 有 CUDA 13 和 ARM64，就假定
官方依赖可以直接安装。必须在目标 DGX Spark 上逐项验证：

```text
Python 版本
PyTorch/Torchvision ARM64 wheel 或源码构建
CUDA runtime 与驱动兼容性
nuPlan、GeoPandas、Rasterio、Shapely 等二进制依赖
NAVSIM import
最小 Agent forward
```

官方数据下载脚本涉及 nuPlan/OpenScene 数据许可和外部存储，数据下载前
必须按官方 LICENSE 和下载说明执行。生产环境建议在宿主机准备数据后只读
挂载到容器，评测容器默认使用 `network_mode=none`，避免运行时隐式下载。

参考：

- [NAVSIM 官方仓库](https://github.com/autonomousvision/navsim)
- [官方安装文档](https://github.com/autonomousvision/navsim/blob/main/docs/install.md)
- [官方数据格式说明](https://github.com/autonomousvision/navsim/blob/main/docs/cache.md)
- [官方数据 split 说明](https://github.com/autonomousvision/navsim/blob/main/docs/splits.md)
- [官方 Agent 接口说明](https://github.com/autonomousvision/navsim/blob/main/docs/agents.md)
- [官方 EPDMS 与评测说明](https://github.com/autonomousvision/navsim/blob/main/docs/metrics.md)

## 3. 在 my-ad 中的 Compose 设计

DGX Spark 上增加一个独立的 `navsim` profile：

```text
compose.dgx.yaml
  |
  `-- navsim [profile: navsim]
        |-- linux/arm64
        |-- NVIDIA GPU reservation
        |-- NAVSIM source，read-only
        |-- NAVSIM dataset，read-only
        |-- exp/checkpoint/report/cache，持久化
        `-- 一次性 evaluation/training Job
```

它是一次性 Job，不作为长期服务运行：

```bash
make navsim
```

命令退出码、评测 CSV/JSON、日志和镜像信息共同决定结果。容器处于
`running` 或 healthcheck 为 `healthy`，不能单独代表评测通过。

### 3.1 Compose 配置契约

`.env` 中必须显式设置：

| 配置 | 用途 |
|---|---|
| `NAVSIM_IMAGE` | 已验证的 ARM64 NAVSIM 镜像，生产环境固定 digest |
| `NAVSIM_COMMAND` | 评测或训练命令，必须使用固定 split 和 Agent |
| `NAVSIM_HEALTHCHECK_COMMAND` | import、数据目录和地图的正向检查 |
| `NAVSIM_GIT_REF` | 源码 provenance；预构建镜像也要记录对应 ref |
| `NAVSIM_SOURCE_DIR` | 宿主机 NAVSIM 源码目录 |
| `NAVSIM_DATASET_DIR` | `dataset/maps`、`navsim_logs`、`sensor_blobs` 根目录 |
| `NAVSIM_MAPS_DIR` | 宿主机地图目录 |
| `NAVSIM_EXP_DIR` | metric cache、配置和实验结果 |
| `NAVSIM_MODEL_DIR` | checkpoint，只读挂载 |
| `NAVSIM_REPORT_DIR` | 评测报告和比较结果 |
| `NAVSIM_CACHE_DIR` | PyTorch、Hugging Face 和运行时缓存 |
| `NUPLAN_MAP_VERSION` | nuPlan 地图版本 |
| `NAVSIM_SPLIT` | 证据中的数据 split，例如 `mini` 或 `navhard_two_stage` |

样例命令只用于首次基线，实际 Agent 和 v2 配置必须按锁定的 NAVSIM ref
调整：

```dotenv
NAVSIM_COMMAND='cd "$NAVSIM_DEVKIT_ROOT/scripts/evaluation" && ./run_cv_pdm_score_evaluation.sh'
NAVSIM_HEALTHCHECK_COMMAND='python3 -c "import navsim, os; assert os.path.isdir(os.environ[\"OPENSCENE_DATA_ROOT\"]); assert os.path.isdir(os.environ[\"NUPLAN_MAPS_ROOT\"]); print(navsim.__file__)"'
```

`NAVSIM_SPLIT` 是配置契约和证据字段；不会自动修改 NAVSIM Hydra 配置。
评测命令必须显式选择对应 split，避免出现“环境变量写了一个 split，
实际命令跑了另一个 split”的不可追溯结果。

### 3.2 预构建镜像和源码构建

优先顺序：

1. 在 DGX Spark 上验证内部 ARM64 镜像，再写入 `NAVSIM_IMAGE`。
2. 保存镜像 digest、源码 ref、Python/PyTorch/CUDA/TensorRT 版本。
3. 只有需要二开或没有可用镜像时，执行：

```bash
make build-navsim
```

本仓库的 `images/navsim/Dockerfile` 会：

1. 使用 `NAVSIM_BASE_IMAGE` 作为基础镜像。
2. 拉取 `NAVSIM_REPO_URL` 的固定 `NAVSIM_GIT_REF`。
3. 安装系统 GIS/编译依赖。
4. 检查 Python 3.9。
5. 执行 NAVSIM 的 editable install。

源码构建的必要条件：

- `NAVSIM_BASE_IMAGE` 必须是已验证的 `linux/arm64` 镜像。
- 基础镜像必须包含与 DGX Spark 匹配的 CUDA/PyTorch 运行时。
- 基础镜像必须能提供官方依赖要求的 Python 3.9。
- `NAVSIM_GIT_REF` 不能使用未锁定的 `main`。
- 如果官方旧版 PyTorch 或依赖没有 ARM64/CUDA 13 兼容包，应建立内部
  requirements lock 和派生 Dockerfile；不要使用 QEMU 把 x86 依赖结果当作
  DGX Spark 运行结果。

因此，源码 Dockerfile 是可复现构建入口，不是已经通过 DGX Spark 的兼容性
证明。构建失败应记录为 `BLOCKED`，而不是绕过依赖检查。

## 4. 数据和接口边界

### 4.1 官方数据优先

第一阶段先使用官方 `mini` 数据完成最小验证：

```text
源码 import
  -> mini logs/maps/sensor_blobs 可读取
  -> metric cache 生成
  -> ConstantVelocity 或官方 baseline 评测
  -> 两次运行报告一致
```

完成后再扩展到 `navtrain`、`navtest` 或 v2 的
`navhard_two_stage`/`warmup_two_stage`。挑战数据的训练使用限制和许可证
必须遵守官方说明。

### 4.2 AWSIM 数据适配

AWSIM 采集链和 NAVSIM 数据链属于两个不同的 schema：

```text
AWSIM ROS 2 bag + ground truth
  -> ROS/真值/标定/坐标系适配器
  -> OpenScene/NAVSIM scene、sensor blobs、metadata
  -> NAVSIM Agent
```

适配器至少要定义：

- 场景和 frame 唯一 ID。
- 传感器名称、图像格式、时间戳和历史帧。
- ego pose、速度、加速度和 driving command。
- 相机内外参、LiDAR 坐标系和地图坐标系。
- 目标轨迹、交通灯、可行驶区域等评测所需真值。
- 缺失传感器、时间不同步和无效标注的处理。
- 原始 bag、转换版本、地图版本和随机种子的 provenance。

没有这些契约前，禁止将 AWSIM 的检测精度、Autoware 的闭环表现和 NAVSIM
EPDMS 直接放在同一张指标表中比较。

### 4.3 NAVSIM 到 Autoware

NAVSIM Agent 的输出是局部坐标下的未来轨迹，通常由轨迹采样配置描述；
Autoware 在线规划则需要 ROS 2 trajectory 消息、时间戳、坐标系和控制
安全检查。两者之间需要独立的 planner/inference adapter：

```text
NAVSIM Agent
  -> Trajectory adapter
  -> ROS 2 trajectory message
  -> Autoware safety / planning contract
```

离线评测通过不等于在线控制闭环通过。在线接入必须单独验证 TF、时间戳、
轨迹采样、碰撞检查、限速、控制器和故障回退。

## 5. 操作步骤

### 5.1 准备源码和目录

在 DGX Spark：

```bash
make init
git clone https://github.com/autonomousvision/navsim.git third_party/navsim
cd third_party/navsim
git checkout <固定 tag 或 commit>
cd ../..
```

将官方数据放入：

```text
data/navsim/dataset/
├── maps/
├── navsim_logs/
│   ├── mini/
│   ├── trainval/
│   └── test/
└── sensor_blobs/
    ├── mini/
    ├── trainval/
    └── test/
```

实际 split 还可能需要 `navhard_two_stage`、`warmup_two_stage` 等目录，
以官方 split 文档和锁定 ref 为准。

### 5.2 锁定 `.env`

```bash
cp .env.example .env
```

补齐 NAVSIM 配置：

```bash
rg 'NAVSIM_|NUPLAN_' .env
```

必须替换：

```text
NAVSIM_IMAGE
NAVSIM_GIT_REF
NAVSIM_COMMAND
NAVSIM_HEALTHCHECK_COMMAND
NAVSIM_BASE_IMAGE（执行源码构建时）
```

### 5.3 静态和环境 Harness

```bash
make test-local
make test-compose
make harness-navsim
```

`harness-navsim` 会检查：

- 当前主机是 DGX Spark 角色。
- NAVSIM 镜像可以拉取或已存在。
- 镜像架构为 ARM64。
- 镜像在生产模式使用 digest。
- 源码目录存在 `setup.py` 或 `pyproject.toml`。
- dataset 和 maps 非空。
- Compose profile 可以展开。
- 容器内 `NAVSIM_HEALTHCHECK_COMMAND` 成功。

### 5.4 生成 metric cache

```bash
make navsim-cache
```

该命令只生成缓存，不代表 Agent 评测通过。缓存应落在
`NAVSIM_EXP_DIR`，并记录源码、数据和地图版本。

### 5.5 运行基线评测

```bash
make navsim
```

验收时保存：

```text
data/reports/navsim/
  <run-id>/
    evaluation.csv 或 evaluation.json
    command.txt
    environment.env
    metrics.txt
```

评测报告必须能回溯到唯一的：

```text
NAVSIM image digest
NAVSIM git ref
dataset checksum/version
map version
split
Agent config
checkpoint checksum
random seed
```

## 6. NAVSIM Harness 门槛

| 编号 | 断言 | 通过标准 | 状态 |
|---|---|---|---|
| H-NAV-01 | Compose 和架构 | `navsim` profile 为 `linux/arm64` 且 GPU 可声明 | `NOT-RUN` |
| H-NAV-02 | 镜像 provenance | tag、digest、源码 ref 可记录 | `NOT-RUN` |
| H-NAV-03 | Python 依赖 | `import navsim`、nuPlan 和关键 GIS 依赖成功 | `NOT-RUN` |
| H-NAV-04 | 数据可读取 | maps、logs、sensor blobs 与选定 split 完整 | `NOT-RUN` |
| H-NAV-05 | metric cache | 固定数据生成缓存，重复生成无结构差异 | `NOT-RUN` |
| H-NAV-06 | 最小 Agent 评测 | 官方 baseline 完成且生成报告 | `NOT-RUN` |
| H-NAV-07 | 结果可重复 | 相同输入和配置的关键指标在阈值内一致 | `NOT-RUN` |
| H-NAV-08 | AWSIM 数据适配 | 转换后 schema、时间戳、坐标系和真值抽样通过 | `BLOCKED` |
| H-NAV-09 | Autoware adapter | 轨迹消息、TF、安全约束和在线回退通过 | `BLOCKED` |

当前不允许的结论：

```text
NAVSIM 容器启动成功 = NAVSIM 评测通过
NAVSIM EPDMS 变高 = Autoware 闭环变好
官方 x86 依赖可安装 = DGX Spark ARM64/CUDA 13 可运行
ROS bag 存在 = NAVSIM 数据集已经生成
```

## 7. 资源和运行策略

DGX Spark 的 128GB 统一内存需要和 Autoware、编译、数据加载共同预算：

- 首次使用 `mini`，再扩大 split。
- `NAVSIM_NUM_WORKERS` 从 1 或 2 开始压测。
- `NAVSIM_BATCH_SIZE` 从 1 开始。
- 缓存、checkpoint 和报告分离挂载。
- 评测时停止不必要的 Autoware、编译和仿真服务。
- 记录峰值内存、GPU 使用、磁盘读吞吐和评测耗时。
- 不以 `torch.cuda.empty_cache()` 代替 worker、batch 和缓存控制。

## 8. 当前落地判定

| 项目 | 当前判定 |
|---|---|
| Compose profile 和目录契约 | `source / static-ready` |
| ARM64 NAVSIM 预构建镜像 | `NOT-RUN`，需在 DGX Spark 锁定 |
| 官方 Python/PyTorch 依赖在 DGX Spark | `NOT-RUN` |
| 官方 mini 数据读取 | `NOT-RUN` |
| 最小 baseline 评测 | `NOT-RUN` |
| AWSIM ROS bag 到 NAVSIM 转换 | `BLOCKED`，缺少 adapter 和 schema |
| NAVSIM Agent 在线接入 Autoware | `BLOCKED`，缺少 planner adapter 和安全验证 |
| NAVSIM 替代 AWSIM | `BLOCKED` |

因此，本仓库现在可以直接部署验证 NAVSIM 的 Compose/Harness 入口；不能
宣称 NAVSIM 已经在 DGX Spark 上通过，也不能宣称它已经替代 AWSIM 或
Autoware 在线闭环。
