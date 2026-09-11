# DGX Spark 落地就绪审计

审计日期：2026-09-11

## 结论

DGX Spark 侧的宿主机、Docker、GPU CUDA 运算、Autoware ARM64
`planning_simulator`、地图加载和业务健康探针已在目标机通过。x86_64 AWSIM、
跨主机网络、完整 ROS 话题契约、录包和回放仍需分阶段验证，不能据此宣称
完整自动驾驶闭环已经落地。

结论分为四种场景：

| 场景 | 结论 | 原因 |
|---|---|---|
| DGX Spark 单机运行 AWSIM + Autoware | `BLOCKED` | AWSIM ARM64 Player 和原生插件构建链未锁定 |
| x86_64 AWSIM + DGX Spark Autoware | `CONDITIONAL` | DGX Autoware 侧已通过本地验证；AWSIM、网络和跨主机 ROS 契约待验证 |
| DGX Spark 单机 Scenario Simulator | `CONDITIONAL` | Compose profile 已具备，Scenario Simulator ARM64 镜像和命令待锁定 |
| DGX Spark NAVSIM 离线规划评测 | `CONDITIONAL` | Compose/Harness 已具备，ARM64 镜像、官方数据和最小评测待目标机验证 |

因此，可直接落地的是“部署与验证 Harness”，不是尚未验证的完整自动驾驶闭环。

## Harness 原则映射

本次按以下原则审查：

1. 接口显式：镜像、启动命令、健康探针、话题白名单和数据目录均通过配置契约接入。
2. 可替换：AWSIM、Autoware、NAVSIM、Scenario Simulator、Isaac Sim、数据转换和推理服务按 Compose 服务隔离。
3. 不伪造通过：静态展开、进程存活、ROS 发现、跨主机网络和端到端闭环分别判定。
4. 证据追加：每次测试写入独立时间目录，并生成 `decision.md`。
5. 确定性：数据 manifest 排序、校验并采用原子写入，重复输入得到相同结果。
6. 生命周期完整：核心进程以前台 PID 1 运行，停止信号可以传递，Recorder 有关闭宽限期。
7. 失败可定位：结果限定为 `PASS/FAIL/BLOCKED/NOT-RUN`，缺前置条件不能标记为 `PASS`。

## 当前 Gate 状态

| Gate | 范围 | 当前状态 | 说明 |
|---|---|---|---|
| G-00 | Shell、Python、Compose 全 profile 静态校验 | `PASS` | 两套 Compose 可展开，单元测试通过 |
| G-01 | DGX Spark Linux、ARM64、内存、磁盘、Docker | `PASS` | DGX 目标机验证通过；证据：`artifacts/host/20260910T142240Z-spark-dba5` |
| G-02 | DGX GPU 容器 CUDA 运算 | `PASS` | GB10 上 PyTorch CUDA 运算通过；证据：`artifacts/gpu/20260910T144100Z-spark-dba5` |
| G-03 | Autoware ARM64 镜像和业务健康探针 | `PASS` | ARM64 镜像运行且 `/map/vector_map` 健康探针通过；证据：`artifacts/runtime/20260911T055823Z-spark-dba5` |
| G-04 | AWSIM x86_64 镜像和业务健康探针 | `BLOCKED` | `.env.example` 仍是占位值 |
| G-05 | 地图、车辆模型、sensor kit | `PASS`（验证基线） | 官方 sample map、`sample_vehicle` 和 `sample_sensor_kit` 已加载；生产目标配置仍待锁定 |
| G-06 | 双主机吞吐、丢包、抖动和时间同步 | `NOT-RUN` | 需要两台目标机、iperf3 和 chrony |
| G-07 | ROS 话题、TF、`/clock` 和控制返回 | `NOT-RUN` | 需要 AWSIM 与 Autoware 运行态 |
| G-08 | ROS bag 录制与回放 | `NOT-RUN` | DGX 与 x86 源端 Recorder 均已编排，ROS tools 镜像尚未在目标机实际构建 |
| G-09 | 数据完整性 manifest | `PASS` | 本地确定性和原子写入测试通过 |
| G-10 | 完整 nuScenes 转换 | `BLOCKED` | AWSIM ground truth schema 和标定契约未锁定 |
| G-11 | BEVFormer、ONNX、TensorRT | `BLOCKED` | ARM64/CUDA 13 依赖矩阵和模型接口未锁定 |
| G-12 | NAVSIM ARM64 镜像和官方数据 | `NOT-RUN` | Compose/Harness 已增加，需在 DGX Spark 锁定镜像、源码 ref、地图和 split |
| G-13 | NAVSIM 最小评测可重复 | `NOT-RUN` | 需生成 metric cache，并重复运行官方 baseline |
| G-14 | AWSIM/Autoware 与 NAVSIM 适配 | `BLOCKED` | 缺少 ROS bag 到 NAVSIM schema 及 trajectory 到 ROS 2 的适配器 |

## 已修复的落地问题

- 删除启动前写 ready 文件的假健康状态，改为显式业务健康命令。
- Recorder 等非 GPU 服务不再无条件申请 GPU。
- Recorder 等待 Autoware 健康，而不是只等待容器启动。
- 增加可源码构建的 ARM64 ROS tools 镜像，明确安装 MCAP 和 CycloneDDS。
- 修复 dataset-converter 重复传入 Python 入口的 Compose 命令错误。
- 网络 Harness 从仅 ping 升级为 ping、iperf3、丢包、抖动和吞吐阈值判断。
- 增加 chrony 同步状态、时间源、stratum 和时钟偏差 Harness。
- Harness 自动加载 `.env`，但不会执行其中的 Shell 内容。
- Harness 证据按运行时间追加，避免覆盖历史结果。
- 增加核心服务运行态健康检查和 ROS 必需话题发现测试。
- 原子 Harness 命令不再接受聚合测试编号别名，避免单项 `PASS` 被误报为
  整组 Gate 通过。
- 增加 x86 AWSIM 源端 Recorder，使网络不足时的源端落盘回退路径不再只存在
  于文档。
- 增加 GitHub Actions 静态 Harness。
- 增加 DGX `navsim` profile、可选源码构建入口、持久化目录和专属 Harness。
- 将 NAVSIM 明确限制为离线规划评测，避免将 EPDMS 冒充 Autoware 在线闭环指标。

## 目标机必须补齐的输入

DGX Spark 核心验证已锁定：

```text
AUTOWARE_IMAGE
AUTOWARE_COMMAND
AUTOWARE_HEALTHCHECK_COMMAND
GPU_SMOKE_IMAGE
DGX_HOST_ADDR
```

接入生产车辆、AWSIM 或 NAVSIM 时还要补齐：

```text
生产 MAPS_DIR
生产 VEHICLE_MODEL
生产 SENSOR_MODEL
SIM_HOST_ADDR
NAVSIM_IMAGE
NAVSIM_GIT_REF
NAVSIM_COMMAND
NAVSIM_HEALTHCHECK_COMMAND
NAVSIM_SOURCE_DIR 中固定 ref 的源码
NAVSIM_DATASET_DIR 中的 OpenScene logs 和 sensor blobs
NAVSIM_MAPS_DIR 中的 nuPlan 地图
NAVSIM Agent 配置和 checkpoint
```

x86_64 AWSIM 主机：

```text
AWSIM_IMAGE
AWSIM_COMMAND
AWSIM_HEALTHCHECK_COMMAND
GROUND_TRUTH_IMAGE
GROUND_TRUTH_COMMAND
GROUND_TRUTH_HEALTHCHECK_COMMAND
DISPLAY 或 headless 渲染参数
```

启用 `scenario`、`isaac`、`viz`、`deploy` 或 `navsim` profile 时，还要补齐
对应镜像、命令和健康探针。

## 可执行落地顺序

两台主机都先执行：

```bash
make init
make test-local
make test-compose
make preflight
make harness-host
make harness-gpu
```

对端启动 iperf3 server 后，两台主机分别执行：

```bash
make harness-network
make harness-clock
```

x86_64 主机：

```bash
make up-sim
make harness-runtime SERVICE=awsim
make collect-sim
```

DGX Spark：

```bash
make build-tools
make up-dgx
make harness-runtime SERVICE=autoware
make harness-ros
make record
```

DGX Spark 上的 NAVSIM 离线路线独立执行：

```bash
make harness-navsim
make navsim-cache
make navsim
```

这三步不证明 NAVSIM 已接入 Autoware。AWSIM bag 转换和 NAVSIM 轨迹接入
Autoware 必须作为后续 adapter Gate 单独验证。

如果网络 Gate 不满足高带宽话题远端录制要求，则在 x86_64 主机改为：

```bash
make build-tools-sim
make record-sim
```

任何一步出现 `FAIL` 或 `BLOCKED`，都停止进入下一 Gate，并保留
`artifacts/` 中的证据。只有固定场景的传感器输入、规划控制返回、bag 关闭、
回放和重复运行均通过后，才能把闭环等级标记为 `e2e`。

NAVSIM 当前详细边界和操作见
[`navsim-integration.md`](navsim-integration.md)。
