# CARLA + DGX Spark 审计记录

审计日期：2026-09-12

本记录把 CARLA 学习路线临时切换到 DGX Spark，并固定当前使用的 fork。
所有构建、依赖安装和运行时都应在 Docker 中完成；宿主机只提供 Docker、
NVIDIA 驱动、NVIDIA Container Toolkit、内核能力和磁盘空间。

## 1. 当前宿主机事实

```text
OS       Ubuntu 24.04.3 LTS
架构     aarch64 / linux/arm64
GPU      NVIDIA GB10
驱动     580.173.02
Docker   arm64，overlay2，root=/var/lib/docker
可用空间约 3.4 TB
```

Docker 隔离用户态依赖，但不会改变宿主机 CPU 架构，也不会替换内核和 GPU
驱动。`linux/amd64` 镜像在本机通过 QEMU 运行，只能作为实验，不作为 CARLA
Server、GPU 渲染或性能通过证据。

CARLA UE5.5 的官方 Linux 文档要求 Ubuntu 22.04 或更高版本，并不表示只能
使用 Ubuntu 22.04。当前宿主机保持 Ubuntu 24.04；构建容器选择 Ubuntu 22.04，
是为了复用官方 Docker 开发基线、减少编译器和 ROS 2 Humble 依赖漂移。该容器
本身仍是 ARM64，不能解决 Unreal/CARLA 的 ARM64 目标适配问题。

## 2. Fork 基线

| 仓库 | 分支/版本 | 当前 commit | 作用 |
|---|---|---|---|
| `gottaBoy/carla` | `dgx-arm64`（基于 `ue5-dev`） | `6afb2094a68939fcc0284c5f1cc42e38f07ab996` | UE5.5 / native ROS 2 / initial ARM64 patch |
| `gottaBoy/carla` | `ue4/0.9.16` | `1cd0f377a0632c788e98dfad4677e4daf8845c08` | 旧版 CARLA 对照线 |
| `gottaBoy/carla` | `ue58-dev` | `5684efc317185244c6474dfb88d4b3651e2f1924` | UE5.8 后续实验 |
| `gottaBoy/ros-bridge` | `master` | `e9063d97ff5a724f76adbb1b852dc71da1dcfeec` | CARLA 0.9.13 旧 bridge |
| `gottaBoy/autoware_carla_bridge` | `main` / v0.12.0 | `d1a135042d88ab72d55ea73b18b65351c81838d6` | Rust `rclrs` + `carla-rust` 适配器 |
| `gottaBoy/autoware_universe` | `humble` | `02a589200c1af644ca4b4cb3ed98695b4b62118b` | Autoware Humble 源码 |
| `gottaBoy/autoware_launch` | `humble` | `f942598d44b5769353167c76b784323d5c14c8c7` | Autoware Humble launch/config |
| `gottaBoy/nano-ros` | `main` | `9a477cc8177d431a1b007768275af8966bf00e28` | 嵌入式/RTOS ROS 2 客户端 |

## 3. 兼容性结论

### 3.1 CARLA `ue5-dev`

`dgx-arm64` 基于 `ue5-dev`，包含 native ROS 2 和 `LinuxArm64` 声明。本轮已在
该分支提交初始 ARM64 构建修复，但仍未形成可维护的 DGX ARM64 Server：

- `CMake/Toolchain.cmake` 已将 OpenSSL 路径改为目标 triple，但实际 ARM64
  Unreal sysroot 和第三方库尚未验证。
- `Util/Docker/Base.Dockerfile` 已按 Debian 架构选择 CMake 归档，但还需要
  真实 ARM64 Docker 构建验证。
- UBT/UAT 已区分 Linux host 脚本和 `LinuxArm64` target，但实际目标构建尚未验证。
- UE5 HostLinux sysroot、第三方库、DLSS、Shader 和 Vulkan 运行链需要分别验证。
- `ue5-dev` CI 没有 DGX ARM64 构建矩阵。

需要把 Unreal 的两个概念分开：Epic 文档把 `LinuxARM64` 列为项目目标平台，
但 Linux 开发要求同时说明其提供、测试的 Linux toolchain 和 libraries 主要是
`Linux-x86_64`。这证明的是 ARM64 可以作为某些项目的 target，不证明 Unreal
Editor、HostLinux 工具链或 CARLA Server 已能在 ARM64 Linux host 上原生构建。
DGX 社区 bring-up 还遇到 UBA、FBX、USD、OpenEXR、ISPC 和平台宏等缺口。

因此本项目的 Unreal Gate 必须拆为：

```text
UE ARM64 host tools
  -> UnrealEditor/ShaderCompileWorker
  -> CarlaUnreal target
  -> CARLA package/server
  -> GB10 Vulkan and sensor runtime
```

其中“LinuxArm64 target 声明存在”只能通过静态检查，不能替代后面的编译和运行证据。

因此状态应记录为：

```text
ARM64 Docker 基础环境       可行
CARLA CMake ARM64 配置      初始修复完成，待构建验证
LibCarla/Python API         待验证
UE5.5 + CarlaUnreal Server  高风险实验
GB10 Vulkan/离屏渲染        待验证
```

`ue58-dev` 暂不作为第一路线。它的 DLSS 路径仍固定 `Linux_x86_64`，适合
在 `ue5-dev` 的 G0-G4 通过后再做分支实验。

### 3.2 旧版 `ros-bridge`

你的 `gottaBoy/ros-bridge@master` 是上游旧版镜像：

```text
CARLA_VERSION=0.9.13
ROS 2 默认文档基线偏向 Foxy
Dockerfile 从 carlasim/carla:$CARLA_VERSION 复制 PythonAPI
不会构建 gottaBoy/carla 源码
```

它只能作为 `CARLA 0.9.13` legacy 学习 profile。不能直接接到
`ue5-dev`、CARLA 0.10.x 或 native ROS 2 数据平面。

### 3.3 `autoware_carla_bridge`

这是当前最有价值的 Autoware 适配器。它通过 Rust `carla-rust` 直接连接
CARLA Server，并发布 Autoware 标准 topic；它不是 native ROS 2 bridge 的
下游节点，也不依赖旧 Python `ros-bridge`。

当前源码和文档重点支持 CARLA 0.9.16，并包含：

```text
Rust rclrs
CARLA 直接 RPC
传感器配置和标定
车辆状态与控制
/clock、TF、GNSS、IMU、LiDAR、Camera
同步 20 Hz
延迟、控制、定位和回归 probe
```

当前缺口是 Docker/Compose。启动脚本仍使用宿主机 systemd，发布包 README
也以 `x86_64` tarball 为例，需要改成容器 entrypoint 和 Compose service。

### 3.4 `autoware_launch` 与 `autoware_universe`

当前 `autoware_launch` 的 `e2e_simulator.launch.xml` 在
`simulator_type:=carla` 时会启动官方 `autoware_carla_interface`。
如果使用 `acb_bridge`，必须关闭该内置 interface，否则可能出现重复的：

```text
/clock
/tf
/vehicle/status/*
/control/command/control_cmd
```

你的 ACB 已提供 `acb_launch/carla_simulator.launch.xml`，并明确注释替换
`autoware_carla_interface`。优先使用这个 launch，再将 `autoware_launch`
参数指向你的 Humble fork。

### 3.5 `nano-ros`

`nano-ros` 是 `no_std` Rust ROS 2 客户端，基于 Zenoh-pico，可运行在裸机、
Zephyr、FreeRTOS、NuttX、ThreadX 和 Linux。它适合未来连接嵌入式控制器、
ECU 或 MCU，不适合替换当前 DGX 上的 desktop ROS 2 Humble、Autoware 或
`rclrs` bridge。

## 4. 推荐数据平面

### 第一选择：ACB direct RPC

如果 CARLA Server 能在 DGX ARM64 运行，优先采用：

```text
CARLA Server
  <-> carla-rust
  <-> acb_bridge
  <-> Autoware Humble
```

这条路线不启动旧 `ros-bridge`，也不依赖 CARLA native ROS 2 的完整性。
它的前提是 `carla-rust` 与实际 CARLA Server 版本兼容。当前 ACB 的
`CARLA 0.9.16` 支持不能自动推广到 `ue5-dev`。

### 第二选择：CARLA native ROS 2

用于 `ue5-dev` 的最新源码学习：

```text
CARLA --ros2
  -> /carla/* sensor topics
  -> /clock / sensor TF
  <- CarlaEgoVehicleControl
```

它适合先验证 native ROS 2，不等于完整 Autoware vehicle interface。仍需
补充或适配 odometry、vehicle status、地图、完整 TF 和 Autoware control。

### 第三选择：旧 Python ros-bridge

只放在隔离的 legacy profile：

```text
CARLA 0.9.13
  <-> gottaBoy/ros-bridge@master
```

不与 native ROS 2、ACB 或同一个 Autoware domain 同时启动。

## 5. Docker 目录和服务

当前仓库暂不修改现有 AWSIM/DGX Compose。CARLA 应使用独立 Compose：

```text
compose.carla-arm64.yaml
images/carla-arm64/
scripts/carla/
config/carla/
artifacts/carla/
```

建议服务：

```text
carla-build
  ARM64 Ubuntu 22.04 build environment
  CARLA source and Unreal source

carla-server
  ARM64 experimental package
  offscreen / nosound / GPU

carla-client
  Python API examples and scenario scripts

acb-bridge
  Rust bridge and Autoware adapter

autoware
  Humble ARM64 stack

harness
  health, topics, TF, rate, latency, ground truth and regression
```

源码可以 bind mount，生成物和缓存使用 Docker volume：

```text
carla-build
unreal-engine
carla-content
cargo-cache
python-cache
artifacts
```

当前实现先提供两个开发 profile：`g0` 只读检查源码和工具链，`build` 使用
读写挂载运行 Unreal/CARLA 构建。执行 `make carla-shell` 不会修改源码；执行
`make carla-build-shell` 才进入允许生成 Build、Engine 和 Content 的容器。

构建不需要挂载 `/var/run/docker.sock`。Docker buildx 的 amd64 模拟也不应
用于 CARLA Server 正式构建。

## 6. 分阶段 Gate

```text
G0  ARM64 Docker 镜像、CMake、Clang、Python、Vulkan 工具链
G1  Unreal HostLinux sysroot 是否包含 ARM64 目标
G2  CARLA CMake configure，不编译完整 UE
G3  LibCarla/Python API ARM64 编译
G4  CarlaUnreal ARM64 Server 启动和 RPC 2000 端口
G5  GB10 Vulkan、离屏渲染、非黑图像和传感器输出
G6  CARLA native ROS 2 的 /clock、Image、PointCloud2、TF
G7  ACB Rust bridge 编译和 CARLA 版本握手
G8  Autoware Humble map、sensor kit、localization、planning、control
G9  Recorder、ground truth、A/A、A/B 和自动回归 Gate
```

低成本探针先执行：

```bash
docker run --rm --platform=linux/arm64 ubuntu:22.04 uname -m

find "$CARLA_UNREAL_ENGINE_PATH/Engine/Extras/ThirdPartyNotUE/SDKs" \
  -type d -name '*aarch64*'

file "$(find "$CARLA_UNREAL_ENGINE_PATH" \
  -path '*aarch64-unknown-linux-gnueabi/bin/clang' -print -quit)"

cmake -S . -B Build/arm64-probe -G Ninja \
  --toolchain=CMake/Toolchain.cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_CARLA_UNREAL=OFF \
  -DBUILD_PYTHON_API=OFF \
  -DBUILD_EXAMPLES=OFF \
  -DENABLE_ROS2=OFF \
  -DCARLA_DLSS_SDK_PATH=disabled
```

如果 ARM64 clang/sysroot 不存在，先停在 G1；如果 CMake 仍解析到
`x86_64-unknown-linux-gnu`，先修复 fork，不要继续全量 Unreal 编译。

## 7. 学习顺序

```text
L0  Docker 与源码锁定
L1  Client / World / Map / Snapshot
L2  Blueprint / Actor / Transform / 生命周期
L3  VehicleControl 与车辆状态
L4  Camera / LiDAR / GNSS / IMU
L5  同步模式 / fixed_delta / seed / Traffic Manager
L6  Recorder / replay / ground truth
L7  Scenario Runner / OpenSCENARIO / OpenDRIVE
L8  native ROS 2 topic / TF / QoS / /clock
L9  ACB Rust bridge 和 Autoware sensor kit
L10 localization / perception / planning / control
L11 延迟、频率、误差、丢帧和可重复性 Gate
```

每个案例只改变一个变量，并保存：

```text
CARLA commit
UE commit
bridge commit
Autoware commit
地图、车辆、传感器、天气和 seed
Docker image digest
运行命令、日志、bag、ground truth 和指标
```

## 8. 社区参考

- CARLA UE5 Linux/Docker：<https://carla-ue5.readthedocs.io/en/latest/build_linux_ue5/>
- CARLA native ROS 2：<https://github.com/carla-simulator/carla/blob/ue5-dev/Docs/ros2_native.md>
- CARLA ROS bridge：<https://github.com/carla-simulator/ros-bridge>
- Autoware CARLA interface：<https://github.com/autowarefoundation/autoware_universe/tree/main/simulator/autoware_carla_interface>
- CARLA Autoware extension：<https://github.com/autowarefoundation/carla-autoware-extension>
- TUMFTM Carla-Autoware Bridge：<https://github.com/TUMFTM/Carla-Autoware-Bridge>
- CARLOS Compose/ROS 集成参考：<https://github.com/ika-rwth-aachen/carlos>
- nano-ros：<https://github.com/NEWSLabNTU/nano-ros>

社区的 native ROS 2 + Autoware 方案多数依赖自定义 CARLA 分支或特定版本，
只能作为接口和测试设计参考，不能直接证明你的 DGX ARM64 路线可用。

## 最终判断

```text
Docker 化构建                 可行
DGX ARM64 构建实验            值得做，但尚未打通
CARLA UE5.5 Server 原生运行   待 G0-G5 验证
旧 ros-bridge 直接复用         不可行
ACB Rust 适配器复用            适合，但先锁定 CARLA 0.9.16
Autoware Humble 接入           可设计，需关闭重复 CARLA interface
nano-ros 进入主链路             暂不需要
```

在 G4 和 G5 通过前，不把结果描述为“DGX Spark 已支持 CARLA”；在 G8 之前，
不把 CARLA Server 启动描述为“Autoware 闭环”。
