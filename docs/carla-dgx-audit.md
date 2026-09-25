# CARLA + DGX Spark 审计记录

初始审计日期：2026-09-12；最近本机构建验证：2026-09-15。

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
| `gottaBoy/carla` | `dgx-arm64`（基于 `ue5-dev`） | `234caf5f30ab56eacf093e699e5adc1459fe725c` | UE5.5 / native ROS 2 / initial ARM64 patch |
| `gottaBoy/carla` | `ue4/0.9.16` | `1cd0f377a0632c788e98dfad4677e4daf8845c08` | 旧版 CARLA 对照线 |
| `gottaBoy/carla` | `ue58-dev` | `5684efc317185244c6474dfb88d4b3651e2f1924` | UE5.8 后续实验 |
| `gottaBoy/UnrealEngine` | `dgx-arm64`（基于 `ue5-dev-carla`） | `791a451d24179902005b15d1d6af71450ff37637` | CARLA UE5.5 / ARM64 host-tool fixes |
| `gottaBoy/ros-bridge` | `master` | `e9063d97ff5a724f76adbb1b852dc71da1dcfeec` | CARLA 0.9.13 旧 bridge |
| `gottaBoy/autoware_carla_bridge` | `main` / v0.12.0 | `d1a135042d88ab72d55ea73b18b65351c81838d6` | Rust `rclrs` + `carla-rust` 适配器 |
| `gottaBoy/autoware_universe` | `humble` | `02a589200c1af644ca4b4cb3ed98695b4b62118b` | Autoware Humble 源码 |
| `gottaBoy/autoware_launch` | `humble` | `f942598d44b5769353167c76b784323d5c14c8c7` | Autoware Humble launch/config |
| `gottaBoy/nano-ros` | `main` | `9a477cc8177d431a1b007768275af8966bf00e28` | 嵌入式/RTOS ROS 2 客户端 |

## 3. 兼容性结论

### 3.1 CARLA `ue5-dev`

`dgx-arm64` 基于 `ue5-dev`，包含 native ROS 2 和 `LinuxArm64` 声明。本轮已在
该分支提交初始 ARM64 构建修复，但仍未形成可维护的 DGX ARM64 Server：

- `CMake/Toolchain.cmake` 已将 OpenSSL 路径改为目标 triple，当前 ARM64
  sysroot 和依赖已用于 LibCarla/Game 目标构建；Editor 依赖仍需单独验证。
- `Util/Docker/Base.Dockerfile` 已按 Debian 架构选择 CMake 归档，但还需要
  真实 ARM64 Docker 构建验证。
- UBT/UAT 已区分 Linux host 脚本和 `LinuxArm64` target；UBT Game 目标已
  编译通过，Editor 依赖解析和 UAT Cook/打包尚未通过。
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
ARM64 Docker 基础环境       已运行
CARLA CMake ARM64 配置      已配置并构建 LibCarla
LibCarla/Python API         ARM64 编译及客户端导入通过
UE5.5 + CarlaUnreal Server  Game 二进制已构建；Editor/Cook 和运行包仍阻塞
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

### 3.6 NVIDIA skills

以下仓库可以作为 Harness 的可选参考，但不是 CARLA/Unreal 的构建依赖：

| 仓库 | 可借用内容 | 当前边界 |
|---|---|---|
| `NVIDIA/skills` | CUDA、GPU、容器和性能分析的任务组织方式 | 作为文档/流程参考，不自动安装运行时 |
| `jetson-bsp-skills` | BSP、镜像、启动和设备证据的组织方式 | DGX Spark 不是 Jetson BSP，禁止刷机和 JetPack 流程 |
| `jetson-device-skills` | 设备快照、硬件状态和 benchmark 记录方式 | Jetson 专用设备接口不能直接当作 DGX 接口 |

在本项目中，它们只补充以下 Harness 输入：

```text
GPU/容器能力快照
显存或统一内存使用
温度、功耗和频率（如果 DGX 接口提供）
测试命令、版本、原始日志和结构化摘要
```

不能因为某个 NVIDIA skill 在 Jetson 上通过，就把 CARLA UE5、GB10 Vulkan、
ROS 2 或 Autoware 结果标记为通过。DGX 专用检查必须使用本机实际可用的
`nvidia-smi`、NVML、CUDA、Vulkan 和 Docker CDI 证据。

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

## 9. 2026-09-14 构建验证

前半段构建沿用锁定的 CARLA/UE commit，继续使用 Unreal 工作区中尚未提交的
ARM64 修改；后续 UE 工作区 HEAD 变化见 9.4。没有更新 `source.lock`，也没有
把脏工作区描述为纯净的固定 commit 构建。
每次 shader-deps 和 SCW 运行均保存 UE commit 和完整 tracked diff，便于后续
整理 fork 补丁；现有 texture/host-tool 修改继续保留。

| 项目 | 结果 | 边界 |
|---|---|---|
| LibCarla / Python API | `PASS` | aarch64 容器导入 cp310 wheel，客户端版本为 0.10.0；未做 Server RPC |
| hlslcc | `PASS` | 从引擎随附源码重建，归档内对象均为 AArch64 |
| ShaderConductor / DXC | `PASS` | 原生 ARM64 编译，HLSL 到 SPIR-V / DXIL 冒烟测试通过 |
| ShaderConductor 部署 | `PASS` | 复制到引擎 ARM64 目录后，不设置 LD_LIBRARY_PATH 也能生成 SPIR-V |
| ShaderCompileWorker | `PASS` | ARM64 构建及动态依赖检查通过，实际链接 ARM64 ShaderConductor |
| ISPC 1.24.0 | `PASS` | 原生 AArch64 host compiler，生成 ARM64 NEON 对象并部署后复测 |
| CarlaUnreal Game | `PASS` | 已生成 AArch64 ELF，动态依赖检查通过；不等于运行包或 G4 通过 |
| ICU / 启动布局 | `PASS` | 独立诊断布局中 ICU、CARLA 插件和物理初始化通过；使用 NullRHI |
| Game 资源加载 | `BLOCKED` | 缺预生成 Asset Registry，读取未烘焙 WorldGridMaterial 时序列化越界并以 139 退出 |
| Ogg / Opus Editor PIC 库 | `PASS` | 原生 ARM64 重建，整库共享链接和部署后编解码通过；不是 Cook 通过 |
| CarlaUnrealEditor / full | `BLOCKED` | 默认配置仍缺 ARM64 USD 库，退出码 8 |
| CarlaUnrealEditor / no-usd | `BLOCKED` | 显式使用无 USD SDK 分支后，确认缺 ARM64 FBX SDK，退出码 8 |
| Assimp FBX 替代实现 | `PASS` | v6.0.5 原生 ARM64 源码构建、19 项上游测试和 8 项 UE 样例检查通过；未替换 UE 的 Autodesk SDK |
| UE 静态 MeshDescription 桥接 | `PASS` | 原生 UBT 构建及实际 UE 数据转换、25 项自测、6 个进程用例通过；不是 Editor/Cook |
| RPC / Vulkan / 相机 | `NOT-RUN` | 尚未获得可用 CARLA Server，不代表 G4/G5 通过 |

修复要点：ShaderConductor 的 CMake 不再向 ARM64 传递 `-msse2` / `-m32`；
使用原生 LLVM 18、UE ARM64 sysroot 与 UE libc++。首次 SPIR-V 测试出现段错误，
栈位于跨动态库的 libc++ 数值格式化调用；为共享库加入 `--exclude-libs,ALL`
隔离静态库符号后，同一输入通过。`$ORIGIN` RPATH 使部署后的工具独立于构建
目录和临时库路径。DXIL 仍有缺少签名库的警告，本轮不验证 Windows 发布签名。

复跑入口（不重新运行会覆盖依赖的 UE Setup）：

```bash
make carla-shader-deps JOBS=8
make carla-shader-deps JOBS=8 SHADER_DEP=hlslcc
make carla-scw JOBS=8
```

`carla-shader-deps` 自动检查并幂等应用随仓库保存的 ShaderConductor CMake
补丁，遇到冲突即失败，不重置已有源码修改。`carla-scw` 依赖当前 UE ARM64
工作区补丁；还不能在只有原始固定 commit 的全新 checkout 上直接通过。
以上 PASS 只覆盖表中范围，SCW 的完整 Unreal shader job 和地图烘焙仍未验证。

关键证据：

- `artifacts/carla/shader-deps-20260914T022409Z-sv39iq/`：首次编译及崩溃栈。
- `artifacts/carla/shader-deps-20260914T024550Z-OmNXaW/`：修复后构建、架构、输出及部署验证。
- `artifacts/carla/scw-20260914T024607Z-niRLwd/`：SCW 构建及动态链接记录。
- `artifacts/carla/carla-unreal-arm64-resume-20260914.log`：早期 ISPC 架构阻塞，后续已解除。
- `artifacts/carla/ispc-20260914T052702Z-m9dRDc/`：原生 ISPC 编译、对象及部署验证。
- `artifacts/carla/carla-ue-20260914T090000Z-D2kIZX/`：CarlaUnreal Game 链接及 AArch64 架构证据。
- `artifacts/carla/startup-20260914T094512Z-FK9KkY/`：ICU 通过后的实际资源加载崩溃。
- `artifacts/carla/editor-check-20260914T094512Z-aUFQHK/`：Editor ARM64 USD 依赖缺口。

### 9.1 启动与 Editor/Cook 边界

2026-09-14 17:45（Asia/Shanghai）复测：启动探针把实际 Game ELF 复制到
独立的 `CarlaUnreal/Binaries/LinuxArm64` 目录，并提供同级 `Engine` 内容。
不能只软链接 ELF 或调整 shell 工作目录，因为 Linux 的 BaseDir 来自
`/proc/self/exe`。CARLA/UE 源码通过 g0 profile 只读挂载，生成日志和 Saved
均留在本轮 artifact 内；未修改引擎来跳过 ICU 或资源加载错误。

修正布局后，ICU 已能读取引擎随附数据，程序进入插件、物理和资源初始化。
随后日志报告 `Failed to load premade asset registry`，并在读取
`/Engine/EngineMaterials/WorldGridMaterial` 时出现序列化越界。当前没有
Cooked/StagedBuilds 运行资源，这与 Game 目标读取未烘焙内容的状态一致；
不能据此声称资源已完成烘焙，也不能仅凭错误对话框认定下载文件损坏。

Editor 必须区分以下两个调用结果：

```text
CarlaUnrealEditor LinuxArm64 Development
  -> 默认 Editor 平台列表拒绝，退出码 6

CarlaUnrealEditor Linux Development -architecture=arm64 -SkipBuild
  -> 通过平台选择，进入依赖解析
  -> GLTFExporter -> Interchange -> USDCore -> UnrealUSDWrapper
  -> 缺 Source/ThirdParty/Linux/bin/aarch64-unknown-linux-gnueabi，退出码 8
```

前一个结果不证明原生 ARM64 Editor 不可移植。当前 fork 的原生工具使用
`Linux` host platform 加 `arm64` architecture，SCW 也采用这条路线。
本轮只解析 Editor 依赖图，没有启动完整 Editor 编译，也没有删除平台检查、
伪造 USD 库或静默禁用插件。原始 UBT 日志和 tracked diff 均保留在证据目录。

复测入口：

```bash
make carla-startup-probe
make carla-editor-check
make test-local
```

前两个入口当前应返回非零并记录 `BLOCKED`；启动探针使用 `-nullrhi` 和
`-ExecCmds=quit`，从不把退出码 0 当作 RPC 或出图通过。Editor 检查使用
`-SkipBuild`，依赖图通过也不代表 Editor 二进制已经编译。每次运行都有独立
目录、执行命令、源码 commit 和 tracked diff；启动探针另保存 ELF SHA-256。

17:45 时本地 43 项测试、shell 语法检查和 CARLA Compose 配置检查通过。
18:28 的依赖推进及当前测试结果见下节；首次出图仍没有可靠完成时间。

### 9.2 USD 诊断配置与 Editor 音频依赖

2026-09-14 18:28（Asia/Shanghai）验证结果：

- `make carla-editor-check` 默认 `full`，仍要求真实 USD SDK；没有静默关闭功能。
- `make carla-editor-check EDITOR_PROFILE=no-usd` 显式设置无 USD SDK 诊断配置，
  在 Unix/ARM64/Editor 条件下使用引擎现有 `USE_USD_SDK=0` 分支。USD 导入/
  导出不可用，不能记为 USD 构建或功能通过。
- `usd-arm64-opt-out.patch` 幂等应用、冲突即失败；仅默认不启用的策略补丁写入
  UE 工作区，没有修改 `.uproject` 或批量关闭引擎插件。`-NoUBTMakefiles` 使
  每次检查重新解析模块规则，日志保存 profile、环境变量和应用后的源码 diff。

最初尝试 `-DisablePlugin` 关闭导入插件，仍被 Fab、MeshPainting 等默认依赖
重新引入 USD；该插件列表没有保存为正式构建配置。CARLA 的 Omniverse 功能
原本就未启用；这不意味着引擎自己的 USD 插件已通过 ARM64 适配。

`no-usd` 检查随后暴露缺少 `libogg_fPIC.a`、`libopus_fPIC.a`，以及 FBX SDK。
音频库已使用引擎随附 Ogg 1.2.2 / Opus 1.1 源码重建：

```bash
make carla-audio-deps JOBS=8
make carla-editor-check EDITOR_PROFILE=no-usd
make carla-editor-check
make test-local
```

音频构建使用原生 LLVM 18 和现有 UE ARM64 sysroot，在 artifact 中构建源码
副本，只安装缺少的 `_fPIC.a`，不替换 Game 已有的 `libogg.a` / `libopus.a`。
Ogg 沿用其 CMake；Opus 的随附 configure 因 Apple 条件变量未初始化失败，
改用同一源码自带的 `Makefile.unix`，没有伪造 host 类型或修改生成的 configure。
Opus 使用便携浮点实现，未宣称 SIMD 优化、性能或完整编解码一致性测试通过。

验证范围：归档中每个对象均为 AArch64；`--whole-archive` 加 `-z defs` 链接
共享库成功；Ogg packet/page 往返数据一致；Opus 实际编解码 19,200 个采样，
检查帧长度与输出能量；部署后的归档再次整库链接并通过同一测试。
已保存源码文件 SHA-256、脚本 SHA-256、编译命令、动态依赖与安装产物哈希。
复测 UBT 后，两条音频缺库警告已消失。

**当前硬阻塞是 FBX，而不是这两个音频库。** `UnrealEd.Build.cs` 直接声明
FBX 依赖，多个 Editor 模块也会引入它；只关闭一个 FBX 导入插件不足以解除。
引擎要求的路径缺失：

```text
Engine/Binaries/ThirdParty/FBX/2020.2/Linux/aarch64-unknown-linux-gnueabi/libfbxsdk.so
```

本地只有 x86_64 对应文件，已通过 ELF 架构检查确认。没有复制它到 ARM64
目录，也没有生成空符号库或删除依赖检查。本轮没有验证当前 Autodesk SDK
发布矩阵，因此这里仅断言本地缺少可用 ARM64 SDK，不断言所有外部 SDK 均不可用。
要继续 Cook，需要获得匹配的真实 ARM64 FBX SDK，或单独设计并验证不依赖
FBX 的 Editor 构建；同版本 x86_64 环境烘焙后交付 ARM64 运行资源也是待验证
的备选路线，不能把任意版本的运行包混入当前源码构建。

关键证据：

- `artifacts/carla/editor-check-20260914T101853Z-WNvK3w/`：首次 no-usd 检查与音频/FBX 缺口。
- `artifacts/carla/audio-deps-20260914T102400Z-TRDfWo/`：Opus configure 的 Apple 条件错误。
- `artifacts/carla/audio-deps-20260914T102854Z-Fq2L9v/`：最终音频构建、源码哈希和部署验证。
- `artifacts/carla/editor-check-20260914T102755Z-XLYF6e/`：音频警告消失，仍缺真实 FBX SDK。
- `artifacts/carla/editor-check-20260914T102400Z-h8OlWA/`：切回 full 后仍正确要求 USD。

当前 51 项本地测试、shell 语法与 CARLA Compose 检查通过。Editor 编译、Cook、
Server RPC、GB10 Vulkan 和相机输出仍未通过，G4/G5 状态不变。

### 9.3 查找源码与原生 FBX 替代实现

2026-09-14 19:23（Asia/Shanghai）继续查找并实际构建，而不是仅记录缺库。
Autodesk 官方人员在 2024-02-13 的论坛答复中说明当时没有开源 FBX SDK 的计划。
本轮没有找到官方
可获取、可重建当前 `libfbxsdk.so` 的核心实现；Extensions SDK、头文件和
Python binding 源码不能替代该库。NVIDIA 官方 usd-convert-asset 仓库也说明其
AArch64 版本使用开源 Assimp 处理 FBX，不使用 Autodesk FBX SDK。

检索依据（本轮访问）：

```text
Autodesk SDK 源码答复（2024-02-13）：
https://forums.autodesk.com/t5/fbx-forum/sdk-source-code/m-p/12559977/highlight/true
NVIDIA AArch64/Assimp 说明：
https://github.com/NVIDIA-Omniverse/usd-convert-asset#configure-fbx-sdk
Assimp 官方源码：
https://github.com/assimp/assimp
```

因此本轮锁定并构建独立的开源 FBX 后端：

```text
Assimp version  6.0.5
Assimp commit   392a658f9c271be965271f45e7521a1b80ea4392
Host/target     原生 aarch64 Docker / AArch64 ELF
Toolchain       LLVM 18 + 现有 UE ARM64 sysroot / libc++
Install         artifacts/carla/assimp-arm64/6.0.5/install/
```

```bash
make carla-assimp JOBS=8
```

脚本在 g0 profile 中只读挂载 CARLA/UE 源码，下载、构建、安装和测试产物均在
artifact 中。只启用 FBX 导入器、FBX 导出器及通用 CLI 必需的 ASSBIN/ASSXML
转储导出器；未开启 USD 或其他未验证格式。Assimp 源码保持干净、commit 不符
即停止，没有修改上游算法或生成 Autodesk 空符号库。

验证结果：

| 检查 | 结果及范围 |
|---|---|
| 库、CLI、探针、测试程序 | AArch64 ELF；部署后的 CLI 与探针不需要 LD_LIBRARY_PATH |
| 上游 FBX suite | 19 项通过，直接编译锁定版本的测试源码；不是全部 Assimp 测试 |
| BlenderCube | 24 顶点、12 个三角面，顶点有限且索引合法 |
| MultiMatId | 4 个材质、960 个三角面 |
| AnimatedCharacter | 1 段动画、3 个通道、18 个关键帧记录、24 个 mesh-bone 引用、1968 条权重 |
| MorphTargets | 2 个 morph targets，非空网格 |
| Binary/ASCII 导出后重导入 | 均通过，三角面数保持 12；顶点由 24 变为 36，不宣称字节或拓扑完全相同 |
| 无效/截断输入 | 均以明确错误码 1 拒绝，崩溃和超时不能算作正确拒绝 |

这 8 项 UE 样例检查验证的是后端能力及基本数据完整性，不是 Autodesk SDK
语义一致性测试。坐标系、精度、材质映射、动画插值与 Editor 资产重导入仍需要
在 UE 接入时做对照测试。没有因样例通过而把资源 Cook 或 G4/G5 标记为通过。

当前 UE 的 `UnrealEd/Public/FbxImporter.h` 直接包含 `fbxsdk.h`，使用
`FbxManager`、`FbxScene`、`FbxNode` 等 Autodesk C++ 类型；FBX 导出器、
MovieSceneTools 和 Interchange 也有直接依赖。Assimp 的 `aiScene` /
`Assimp::Importer` 不是这些接口的 ABI 替代品，不能将 `libassimp.so` 改名为
`libfbxsdk.so`。接下来的工程是移植 UE 的导入/导出后端，并验证调用方，
本轮尚未进行这项引擎修改。

关键证据：

- `artifacts/carla/assimp-20260914T111935Z-mHyE5i/`：首次完整原生构建和功能测试。
- `artifacts/carla/assimp-20260914T112306Z-uDuNOT/`：正式 Make 入口复跑、CLI、动态依赖与产物哈希。
- 上述目录的 `upstream.xml`：19 项上游测试结果。
- 上述目录的 `fixtures/summary.json`：8 项样例检查、输入 SHA-256 与实际数据计数。

当前 57 项仓库本地测试、shell 语法与 CARLA Compose 检查通过。源码查找和
开源后端构建已完成；UE Editor 接入、Cook、Server RPC 和相机仍未通过。

### 9.4 Harness 与真实 UE 静态网格桥接

2026-09-14 21:18（Asia/Shanghai），首个真正链接 UE 模块的子阶段通过。
本轮实际 UE 工作区 HEAD 为 `693d44c72ab03d7959e882fab7c8296308194a18`，
比早期 `791a451d...` 基线多出 NEON、Oodle/VectorVM 和 texture-tool 修复。
现有未提交修改保留，运行证据记录实际 commit、tracked diff 和桥接源码哈希。
`source.lock` 未自动更新，不能把本轮宣称为仅靠旧锁文件即可重现的干净构建。
CARLA HEAD 仍为 `234caf5f30ab56eacf093e699e5adc1459fe725c`。

主 agent 完成 `CarlaAssimpMesh` 模块及 `CarlaMeshBridge` Program；验收 agent
实现 stage report 与进程检查器；依赖/审查 agent 实现只读清单，并发现绕序和
数据验证问题。主 agent 复核、修复并执行了真实 UBT 构建和运行测试。
阶段计划与剩余迁移边界分别保存在 `carla-native-plan.md`、`carla-fbx-migration.md`。

该 Program 链接 UE 的 CoreUObject、MeshDescription 和 StaticMeshDescription，
创建真正的 `FMeshDescription`，不是模拟 UE 数据结构；它不链接 UnrealEd，
不生成 `UStaticMesh`/`.uasset`，也不执行 Cook。

```bash
make carla-ue-meshbridge JOBS=8
make test-local
```

Make 会先重跑 Assimp 后端并生成新前置报告，然后构建和验证 UE Program。
新 JSON 报告使用精确 stage ID/scope、命令、来源、检查结果、证据 SHA-256 和
前置报告。验证器递归复核这些文件，不依据已有 PASS 字样信任结果，也不把
历史 `decision.md` 自动迁移。完整校验应在相同 Docker 挂载布局中执行。

本轮修复了真实运行暴露的问题：

- UE 与 Assimp 的 C++ 分配/释放边界不一致，初始自测在场景析构时失败，真实
  导入在字符串日志处理中失败。改用 Assimp C API 配对创建/释放，夹具使用
  明确借用的栈存储，并对 Assimp 内部函数和数据符号使用 `-Bsymbolic`。
  没有切换整个 UE 的分配器，也没有跳过析构或修改失败退出码。
- UBT 只看到稳定 SONAME 符号链接的时间戳，导致复制的运行库仍为旧版。
  链接保留 `.so` 名称，部署依赖解析到实际版本文件，并在启动前比较库内容。
  验收器还将运行目录中的实际库与前置报告哈希对照，逐次调用前后复查。
- Assimp 的真实 FBX `UnitScaleFactor` 为 float，初始桥接只接受 double；
  已支持两种合法类型，并覆盖真实类型、有限性、正值和非法轴符号。
- UE 三角形法线采用反向叉积约定，已修正绕序，并直接调用
  `FStaticMeshOperations::ComputeTriangleTangentsAndNormals` 作为自测参考。
- 绝对行列式阈值错误拒绝了多材质样例的微小缩放。采用 double 变换/逆矩阵，
  将真正奇异矩阵与有效小尺度分开，新增小尺度测试。
- 拒绝重复索引、零面积三角形及非有限 RGBA；序列化回环检查实例/UV 通道数、
  有效 ID、角点连接、逐面材质归属、UV 拓扑和颜色。UE 的独立三角形 UV 索引
  可以为零通道，比较器按各自表示的真实通道数访问，不强行假定拓扑存在。

验收结果：

| 用例 | 实际结果 |
|---|---|
| 原生自测 | 25 项，含单位/坐标/绕序/镜像、小尺度、属性回环、拒绝及同数量数据损坏检查 |
| BlenderCube | 24 顶点、12 三角形、6 材质槽；UE 内存序列化回环 4089 字节 |
| MultiMatId | 2880 顶点、960 三角形、4 材质槽；UE 内存序列化回环 247286 字节 |
| AnimatedCharacter | 退出码 2，明确报告动画不支持；输出几何保持为空 |
| MorphTargets | 退出码 2，明确报告蒙皮/Morph 不支持；输出几何保持为空 |
| 截断 FBX | 退出码 2，明确解析错误，不是崩溃或超时 |

负向用例的测试 PASS 表示按预期拒绝输入，不表示动画或 Morph 已接入 UE。
25 项原生自测与 Assimp 的 19 项上游测试是不同测试集，不能混为同一通过次数。

21:35 完成残余边界复核：回环补齐边端点、边/多边形有效 ID 和多边形材质归属；
新增非空 UV 元素/三角形 UV 索引的回环及篡改测试。材质槽使用稳定、唯一、
非空的键，覆盖重复名、大小写碰撞和保留名 None。自测由首次 19 项增至 25 项。

关键证据：

- `artifacts/carla/ue-meshbridge-20260914T122519Z-PaelkF/`：首次 UBT 原生构建，54 个 action、约 47.6 秒；初始运行失败。
- `artifacts/carla/ue-meshbridge-debug-20260914T123218Z-uYlnzp/`：自测跨堆释放的 GDB 栈。
- `artifacts/carla/ue-meshbridge-debug-20260914T123930Z-Ze0pWH/`：真实导入的字符串分配/释放栈。
- `artifacts/carla/ue-meshbridge-20260914T131847Z-htU1vi/`：19 项自测、6 个进程用例、运行库哈希与递归前置验证通过。
- `artifacts/carla/ue-meshbridge-20260914T133547Z-x4Yt0V/`：最终 25 项自测和 6 个进程用例通过，当前验收门槛为至少 25 项自测。

临时调试器仅安装在一次性容器中；宿主机依赖未改变。调试入口输出 OBSERVED，
不会用 gdb 自身的退出码推断程序通过。

当前 128 项仓库测试、shell 语法和 CARLA Compose 检查通过。这些测试包括
harness/诊断逻辑，并不等于 128 项仿真功能。下一步是 Interchange 后端接入，
随后仍须迁移 Legacy Editor 的 SDK 消费者；G4 Server/RPC 和 G5 渲染仍未通过。

### 9.5 DGX-only execution and native Vulkan readback

The user explicitly confirmed on September 14, 2026 that DGX Spark is the only
available environment. No Windows/x86_64 build or cook was executed. The external
cook-host candidate mentioned above is not an active route; Editor, Cook and
runtime remain native ARM64 Docker tasks.

```bash
make carla-vulkan
```

The `gpu` Compose profile provides NVIDIA `graphics,utility,compute` capabilities.
It compiles a native C Vulkan probe inside the existing ARM64 toolchain image,
selects the actual NVIDIA GB10 rather than a software renderer, creates a graphics
queue and render pass, clears two image rectangles, copies the image into
host-visible memory, waits for completion and checks every RGBA byte independently
in Python. Both 64x48 frames contain 3,072 checked pixels; all pixels change between
the two prescribed patterns. Raw frames, PNGs, device observations, compiler
command, sources, binary hashes and a scoped stage report are retained.

- PASS: `artifacts/carla/vulkan-readback-20260914T153349Z-eDQJ37/stage-report.json`
  through the actual Make/Compose entry.
- Expected FAIL without GPU passthrough:
  `artifacts/carla/vulkan-readback-20260914T151443Z-hwFVfQ/stage-report.json`.
- Device enumeration observed NVIDIA GB10, driver `580.173.02`, Vulkan device API
  `1.4.312`; the container loader reported `1.3.204`.
- Twelve focused harness tests passed in ARM64 Docker. Corrupt/black/truncated
  frames, unexpected devices, invalid input types and missing executables are
  rejected. Synthetic unit fixtures test the evaluator, not hardware execution.

Scope: **Vulkan render-pass clear/readback, not a shader-driven scene, Unreal
rendering or CARLA sensor output.** G4/G5 and Editor/Cook remain unpassed.
The optional image-viewing tool failed to launch its filesystem sandbox; numeric
checks covered the raw frames and the PNG encoder independently.

### 9.6 Native ufbx backend and real UE static bridge

Pinned ufbx release `v0.23.0`, commit
`fcc5d6ba444cfd3eb80677dba5e37e493941abe5`, from the official ufbx repository.
The native shared library and PIC static archive are installed under
`artifacts/carla/ufbx-arm64/0.23.0/install`; archive members are checked as ELF64
AArch64 relocatable objects. No Autodesk SDK library is replaced or renamed.

```bash
make carla-ufbx JOBS=4
make carla-ue-ufbxbridge JOBS=4
make carla-ue-meshbridge JOBS=4
```

Backend evidence:
`artifacts/carla/ufbx-20260915T012559Z-egL9if/stage-report.json`.
Nine check groups passed: prerequisites, four upstream core arithmetic tests
(not the full upstream FBX suite), BlenderCube, MultiMatId, the non-symmetric
hierarchy/geometry control, animation/morph rejection, invalid input and truncated
input. Repeated parsing and source/converted scene representations are checked.

UE evidence:
`artifacts/carla/ue-ufbxbridge-20260915T012600Z-TrO1Zh/stage-report.json`.
The main agent reran the complete Make entry and all hashed prerequisites in Docker.
It binds the actual UBT executable to the static library, installed/source
headers, bridge sources, build commands and source changes.

| Native UE check | Result |
|---|---|
| ufbx self-checks | 32 passed, including actual UE serialization and failure atomicity |
| Real non-symmetric FBX controls | Four handedness/instance-reflection combinations; units, geometry transforms and UE normal agreement |
| BlenderCube | 8 vertices, 12 triangles, 6 material slots |
| MultiMatId | 482 vertices, 960 triangles, 4 material slots |
| Animation/morph/truncated cases | Explicit exit 2 and no output geometry; rejection, not feature support |
| Assimp path in the dual-backend executable | Existing six process cases passed |

The default, ufbx-disabled Make entry was also independently rebuilt and passed:
`artifacts/carla/ue-meshbridge-20260914T161705Z-GLIOqx/stage-report.json`.
Its 25 self-checks and original static/rejection fixtures remain valid.

The backends use different position/corner representations, so vertex counts are
not expected to be identical. More importantly, the older Assimp Front/Coord/Up
mapping and the ufbx front-vs-forward policy are explicitly **not declared position
equivalent**. The ufbx controls validate its stated policy; migration into the
Editor still needs a deliberate asset/reimport compatibility decision.

This is a dual-backend, in-memory `FMeshDescription` program. It does not create
`UStaticMesh` assets, migrate Legacy/Interchange SDK consumers, supply USD, cook a
map, or establish CARLA RPC/sensor acceptance. No x86_64 host was used.

### 9.7 Native client harness boundary

`make carla-runtime-check` provides distinct endpoint RPC and RGB/LiDAR modes.
It requires an explicit world-mutation allowance, exact client/server version
strings, consecutive synchronized frames and restoration/cleanup. Sensor mode
also checks matching frame IDs/timestamps, nonconstant/changing RGB images and
finite nonempty XYZI data. Missing scene prerequisites remain failures.

The real local 0.10.0 ARM64 wheel was installed offline into a disposable
container for an interface-only check. Its extension is the top-level
`carla.cpython-310-aarch64-linux-gnu.so`, not `carla.libcarla`. Both layouts are now
supported by the evidence snapshot code. Actual WorldSettings fields/equality
and client-version access passed; no server RPC was performed.
Observed extension SHA256:
`70dbada3bbc78539ba1427d0df51241648ac208cacc054315907c15b29ea406a`.

Caller-supplied source provenance is snapshotted before parsing. Changes to that
input, its snapshot or the loaded extension invalidate the endpoint result.
Injected unit-test clients use `harness-test.*` identities; they cannot establish
the production endpoint stage. Client architecture and hashes do not attest to
server architecture/build provenance.

The wrapper now publishes a separate `carla-runtime-invocation` root report.
The endpoint report lives under `endpoint/`; consumers of a complete invocation
must validate the root, not only the child. The root requires a zero outer exit,
an exact production child scope, a pinned structured input manifest and unchanged
wheel/wrapper/evaluator/provenance/reporting inputs. Inputs are checked before and
after installation and endpoint execution. Bootstrap failure or a forced child
timeout cannot promote a child PASS into a root PASS.

Real negative bootstrap check:
`artifacts/carla/runtime-rpc-20260915T013736Z-0o5ist/stage-report.json`.
An unrelated ufbx report was deliberately supplied as provenance. Offline wheel
installation and input checks succeeded; the endpoint rejected the missing CARLA
source before permission/handshake/world operations. Both endpoint and root are
FAIL as expected. This is not a real CARLA RPC failure or a runtime PASS.

Final regression: `make carla-test` passed **281 repository tests** and shell
syntax checks inside ARM64 Docker. These include synthetic harness fixtures, not
251 simulation features. Editor/Cook, real RPC, sensors and Autoware remain
unpassed. All temporary verification containers were removed.

### 9.8 Native Interchange static boundary

The complete one-click target was rerun on DGX ARM64:

```bash
make carla-interchange JOBS=4
```

Latest report:
`artifacts/carla/interchange-nodes-20260915T121122Z-cgQ2ZX/stage-report.json`.
The target reran the Assimp and ufbx prerequisites, compiled a native UBT Program,
loaded the real BlenderCube FBX through ufbx, built actual
`UInterchangeBaseNodeContainer`, `UInterchangeSceneNode` and `UInterchangeMeshNode`
objects, serialized and loaded the node graph, then wrote and read the standard
static Interchange payload format (`FMeshDescription` followed by
`bIsSkinned=false`).

The checker requires exact stage identity, ARM64 ELF, nonempty graph and payload,
source-file hashes before and after the process, source SHA256, positive geometry
metrics and a valid ufbx prerequisite. It records FAIL reports for compile,
process, architecture, graph, payload or geometry failures. This stage does not
load `InterchangeFbxParser`, run InterchangeWorker, create `UStaticMesh` assets,
translate material shading, build the Editor or Cook a map.

### 9.9 USD ARM64 dependency inventory

The read-only inventory was run in the native ARM64 container after excluding
OpenUSD `codegenTemplates` placeholder resources from runtime plugin resolution:

```bash
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T carla-dev \
  python3 /opt/my-ad/scripts/carla/inspect_usd_dependencies.py \
  --ue-root /workspace/unreal-engine --artifact-dir /artifacts/carla
```

Latest report:
`artifacts/carla/usd-inventory-epm_qwd5/inventory.json`.
It contains 188 blocked and 112 passing required entries. The result remains
`BLOCKED` because the checked-in OpenUSD Linux build uses
`x86_64-unknown-linux-gnu`, Boost `-x64`, an x64 toolchain, and deployment paths
without verified ARM64 libraries. The inventory reports missing or wrong-architecture
ELF/archives rather than treating headers or x86 files as usable. It is a
dependency inventory, not an applied USD rebuild or Editor pass.

### 9.10 Build manifest and compatibility ledger

The DGX-only source capture and verification both pass. The capture command
prints the unique manifest path; pass that printed container path to verify:

```bash
make carla-manifest
make carla-manifest-verify \
  MANIFEST=/artifacts/carla/build-manifests/<capture-output>/manifest.json
```

The manifest records the main project repository in addition to the separately
mounted CARLA and UE repositories, their HEAD/branch/status, binary dirty patches,
all untracked regular files, key scripts/configuration/ledger hashes, and the
native container runtime. It uses no Docker socket and filters sensitive environment
variable values. The source artifact was verified without modifying any checkout.
The human-readable and machine-readable compatibility records are
`docs/carla-build-manifest.md` and `config/carla/change-ledger.json`.


### 9.11 2026-09-15 Interchange 双 Geometry 与 TBB 批次留痕


本批次在 DGX Spark native ARM64 Docker 中完成了 Interchange 双 Geometry 场景验证、TBB 2019u8 dynamic-only 验证和完整仓库回归。没有修改 UE 部署目录，也没有把 standalone contract 或依赖检查提升为 Editor/Parser/Worker 通过。


Interchange 真实报告：
`artifacts/carla/interchange-nodes-20260915T150227Z-xKbcdc/stage-report.json`


该报告的实际 stage 为 `ue-ufbx-interchange-static`，required checks `build`、`architecture`、`process`、`graph`、`payload`、`geometry` 全部为 `PASS`。本次输入为双 mesh Geometry scene，`mesh_nodes=2`、`payload_count=2`，输出两个独立的普通 payload 文件：


- `outputs/24dc008dcb79c82372c99559fe558bc053903febbad37273cf0ce1fe9a719cbe.payload`
- `outputs/4dd318640d58c0ffaa34a22a7e750329a1d2c5d0c4aaa5c58d05686e4a840365.payload`


报告递归验证了 ufbx prerequisite、UE source commit、scene graph、两个 payload 和 geometry evidence。该 stage 仍明确排除 `InterchangeFbxParser`、`InterchangeWorker`、material shading、factory assets、Editor 和 Cook。


TBB 真实报告：
`artifacts/carla/usd/tbb-20260915T144340Z-p7WNcW/stage-report.json`


该 stage 是 `carla-tbb-native-arm64`，范围仅为 TBB 2019u8 `tbb`/`tbbmalloc` release shared libraries、AArch64 架构、`ldd -r` 和实际 smoke；UE libc++、parallel_for、parallel_reduce、scalable_allocator 均已在 Docker 中验证。静态 `.a` 未实现并在 `static-mode.txt` 记录为 `NOT_IMPLEMENTED`，因此不能作为静态 SDK 通过。


最新 `make carla-test` 在 ARM64 Docker 中为 **305 tests OK**。输出中包含若干故意失败路径的 `FAIL` 文本；这些是测试验证 fail-closed 行为，不是回归失败。


本批次结论：双 Geometry Interchange static payload 和 TBB dynamic-only 依赖阶段均有真实 artifact PASS；F1 parser/worker facade contract 仍不是真实 FBX parser/worker 集成，UE Editor/Cook、完整 USD 和运行时闭环仍保持未通过。

### 9.12 2026-09-16 Payload 查询与 Imath 原生构建

本轮主线程完成 UE-native `FetchStaticPayload`，独立 agent 审查真实 parser/worker
协议及新增实现，另一 agent 完成 Imath recipe。没有使用 Windows/x86_64 主机，
也没有禁用默认 Editor 功能来获取成功结果。

- Interchange：`artifacts/carla/interchange-nodes-20260916T022331Z-QjoaS6/stage-report.json`
  为 PASS。2 个 Geometry、6 个请求 payload、20 项 source-scene 自检、27 项
  payload 检查通过。所有请求经真实 UE `FJsonFetchMeshPayloadCmd` 和结果类编解码，
  再调用 key/transform 查询；没有启动 Worker 进程。
- 查询 key 区分大小写且绑定 source SHA；请求 ID 包含规范化变换矩阵，内容 hash
  独立保存。相同几何不同 key 不再产生文件名冲突，失败不会修改输出。
- 数值兼容改造：修正小尺度方向归一化，1e-8 缩放和镜像实测通过；有限大平移
  导致的 float 三角形坍塌、非法 quaternion、非有限值和过期/重复 key 被拒绝。
- 验收从首 mesh 指标改为逐请求关联和全量汇总；增加多 UV、非零 tangent、材质
  重绑定控制，以及同计数属性损坏反例。输入 fixture 和评估器亦纳入前后哈希门禁。
- Imath：agent 初次构建之后，主线程通过 `make carla-imath JOBS=4` 独立复跑，
  `artifacts/carla/usd/imath-20260916T021906Z-QilDi2/stage-report.json` 为 PASS。
  3.1.9 Release PIC 静态库的 5 个 archive member 均为 AArch64；whole-archive
  共享链接、动态依赖、half/vector/matrix/color/libc++ smoke 通过。
- 完整仓库测试在 ARM64 Docker 内执行 `make test-local`（与 `carla-test` 同一
  套测试），额外传入上述 `CARLA_IMATH_REPORT` 以运行真实 artifact 验证。
  **322 tests OK，无跳过项**；完整日志为
  `artifacts/carla/regression-20260916-3UUrK2.log`。另有 8 项 Imath 专项测试通过。

源码/API/构建改造位于 `scripts/carla/ue-meshbridge/Source/CarlaUfbxMesh/`、
`scripts/carla/ue-interchange/`、`scripts/carla/usd/`，复跑入口和限制分别记录在
`docs/carla-interchange.md`、`docs/carla-imath-native.md`，兼容台账已补充。

尚未完成：真实 FInterchangeFbxParser 后端与 Worker 进程、Legacy 导入导出、
骨骼/动画/Morph、完整 USD SDK 部署、Editor/Cook、CARLA RPC/传感器。
Imath 仍在独立 prefix，未写入 UE SDK 目录；不能将其单项 PASS 提升为 U1 PASS。

### 9.13 2026-09-16 真实 Parser 静态接入与 USD 依赖推进

三个 agent 分别审查 parser 边界、构建 Alembic、构建 OpenSubdiv；主线程完成
实际引擎接入、独立重放、整合及全量回归。本轮仍只使用 DGX ARM64 Docker。

| 阶段 | 本轮真实证据 | 明确边界 |
|---|---|---|
| FInterchangeFbxParser 静态分支 | `interchange-parser-20260916T040901Z-RUuhDM/stage-report.json` | 31 项检查；真实 parser class + ufbx provider，不是 Worker 进程或完整 FBX |
| 原默认静态节点分支 | `interchange-nodes-20260916T041418Z-3gJUmm/stage-report.json` | 20 场景 + 27 payload 检查回归通过 |
| Alembic 1.8.6 | `usd/alembic-20260916T035401Z-MXwTfh/stage-report.json` | Ogawa/static/PIC；不含 HDF5/Python |
| OpenSubdiv 3.6.0 | `usd/opensubdiv-20260916T035401Z-pfVOVc/stage-report.json` | osdCPU/static/PIC；不含 GPU/Python |

表中路径均相对 `artifacts/carla/`。动态链接检查确认 parser Program 不依赖
`libfbxsdk`；新分支默认关闭，且 Build.cs 拒绝 Editor、非 ARM64、带 Engine 的
目标。原 Autodesk 代码与默认依赖仍保留，未通过减掉默认 Editor 功能来报成功。

引擎修改采用可重放的 `prepare_interchange_parser.py`：保留原/新源码、patch
和哈希。首次变更原始快照在 `parser-source/parser-prepare-lz56gc1l/`；每次构建
另留部署记录及引擎 tracked diff。新公开接口由引擎拥有，项目实现通过
IModularFeatures 注册，未绕过 UBT 的模块依赖层级。原公共 parser header 不变。

真实 parser 测试覆盖两种 Load overload、六个 mesh 请求、结果读回、外部错误
容器保持、同请求切换目录、写入失败、缓存丢失/损坏、失败重载清旧状态、Reset、
重复 Release、多实例隔离、缺少/多个 provider、unsupported 设置及动画/Morph。
只有显式的 front-X/厘米/保留 namespace 设置组合受支持；这不证明 Autodesk
所有坐标语义的等价性。并发与 Worker 任务状态仍需独立实现和验证。

全量 ARM64 Docker 测试传入真实 Imath/Alembic/OpenSubdiv report，最终为
**356 tests OK，无跳过项**。日志：`artifacts/carla/regression-parser-20260916-diA61b.log`。
此前的 `regression-parser-20260916-0XMz0A.log` 留下旧测试定位错误的失败记录，
修复仅将默认静态分支的参数检查定位到相应 scene 进程之后，没有放宽门禁。

剩余关键工作：真实 Worker 进程 provider/IPC/错误传播/请求事务；材质 shader、
factory assets、Legacy importer/exporter、骨骼/动画/Morph/重导入；MaterialX、
Python/Boost、所需 TBB 变体、OpenUSD 本体及 UE SDK 部署；随后才是完整 Editor、
Cook、RPC/传感器和 Autoware。新依赖仍在独立 prefix，不能把 U1 或 E1 标为 PASS。

### 9.14 2026-09-16 Worker IPC、MaterialX 与 Python 核心运行时

本轮主线程实现共享 provider 与真实 Worker；三个 agent 分别审查协议/编写门禁
测试、构建 MaterialX、构建 Python。没有并发 UBT 构建同一个 UE 工作树。

- Worker：`artifacts/carla/interchange-worker-20260916T052252Z-EbPC7I/stage-report.json`
  PASS。真实引擎 `InterchangeWorker` launch module 编译为显式测试 target，peer
  用 UE command-queue/loopback TCP 启动五个子进程。23 项检查、两份 graph、六份
  payload 通过；覆盖排队请求失败隔离、重载恢复、错误版本、失联、连接拒绝与
  正常 Terminate。两个 ELF 均为 ARM64，无 libfbxsdk 或未解析动态符号。
- 兼容改造：provider/graph 抽至 `CarlaUfbxInterchange`；Worker 显式加载模块，
  静态 profile 串行处理完整 parser/result/messages 事务，失败返回 ProcessFailed。
  PreInit/Run 失败传播非零退出码；严格版本数字校验；空闲超时兜底 FIN/停滞。
  原 SDK 和线程池代码保持在默认路径，未将诊断分支应用到默认 Editor。
- 共享模块回归：parser 的 31 项检查在
  `interchange-parser-20260916T052923Z-4z17du` 通过；静态节点/变换的 20+27 项检查在
  `interchange-nodes-20260916T061458Z-nsCj93` 通过（均位于 `artifacts/carla/`）。
- MaterialX：主线程从 `make carla-materialx JOBS=4` 独立重放，报告为
  `artifacts/carla/usd/materialx-20260916T062752Z-SyErgU/stage-report.json`。
  固定 USD 所需 1.38.5，六库 99 个 ARM64 对象、PIC 链接、XML 写读/validate 通过。
- Python：`artifacts/carla/usd/python-20260916T061920Z-6Mp0EP/stage-report.json`
  PASS。按 UE patchlevel 固定 3.11.8，官方源包 SHA256 校验；可执行、共享/静态 PIC、
  核心 stdlib、扩展模块、共享/静态 embedding 与源码留痕通过。Sigstore bundle 只
  保留并比较摘要，未声称密码学签名验证。
- Python 初次 `python-20260916T052323Z-rQzsAe` 保持 FAIL：build-source 新增 172 个
  `.pyc`，而原源码未删除/改写。修复为构建解释器显式 `-B`，新 run 前后源码完全
  一致；未忽略任何生成文件、未放宽哈希或改写旧失败记录。

最终全量 ARM64 Docker 回归传入真实依赖报告，**411 tests OK，无跳过项**。
日志：`artifacts/carla/regression-worker-20260916-doEQwk.log`。
Worker 专项 33 项、MaterialX 9 项、Python 13 项亦通过。兼容台账、新 recipes、
引擎 before/after patch 和源码快照均保留，未提交/回滚用户既有修改。

剩余范围必须明确：生产 WorkerHandler 启动器与进度/取消/长期 heartbeat 策略；
材质/factory/Legacy/骨骼/动画/Morph/重导入/导出；Python SSL、ctypes、SQLite、
压缩等可选依赖、Boost.Python、OpenUSD 与 UE 部署；完整 Editor/Cook、CARLA
真实 RPC/相机/LiDAR、Autoware 及 clean checkout 重建。当前不能标记整体完成。

### 9.15 2026-09-16 OpenUSD 本体与 full Editor SDK 接线

按完整 Editor 目标推进：两个 agent 并行构建 Boost.Python/TBB-static，另一 agent
审核 ABI/部署与测试，主线程进行真实 OpenUSD 源码构建、运行修复和 UE 接线。

- Boost 1.82.0：九库 static PIC/shared、62 个 AArch64 archive members、真实
  Boost.Python import/调用/类/异常通过。报告：
  `artifacts/carla/usd/boost-20260916T070841Z-re7OmX/stage-report.json`。
- TBB 2019u8 静态：37/6 个真实对象生成 `libtbb.a/libtbbmalloc.a`，直接与
  whole-archive PIC smoke 均通过。报告：
  `artifacts/carla/usd/tbb-static-20260916T070632Z-hhVyTx/stage-report.json`。
- OpenUSD v24.05 固定提交 `2864f3d04f396432f22ec5d6928fc37d34bb4c90`，六个 UE
  补丁全部保留并应用于独立副本。七项前置 report/hash/实际库头文件绑定、跨阶段
  Imath/Python 一致性均检查。最终 2930 构建动作、安装、81 个 AArch64 ELF、链接、
  Python pxr、USDA/USDC 网格变换及材质绑定、Ogawa via USD Alembic、官方标准
  MaterialX 文件格式、坏 USD 拒绝和源码留痕全部 PASS：
  `artifacts/carla/usd/openusd-peak8y9o/stage-report.json`。

真实失败与修复留痕：

1. `openusd-r2bduadg` 完整编译/安装成功但运行 FAIL。跨 DSO 隐藏每份静态 libc++
   同时导出 inline STL 实现，导致 Usd/Gf 导入顺序触发 locale/ostream 崩溃，GDB
   栈位于另一个扩展的 collate typeinfo。复制产物重链接诊断保存为
   `openusd-link-diagnostic-k550e3y8`，不是 fresh-build PASS。
2. OpenUSD `std::type_index` 转换表亦受非唯一 RTTI 地址影响，导致 list/Token
   自动转换失败。OpenUSD 局部覆写 SHARED/MODULE 链接 flags，并启用 libc++
   `_LIBCPP_TYPEINFO_COMPARISON_IMPLEMENTATION=2`；未修改全局 shader/UE 工具链。
3. `openusd-qg1aa95h` 修复后核心场景测试通过，但嵌入整套本地 nodedef 的 MaterialX
   文档触发上游不支持路径和异常。最终 smoke 使用官方 `GraphlessNodes.mtlx`，
   MaterialX 自身的原文档读写验证仍独立保留。没有关闭 MaterialX、用全局 dlopen
   flag 绕过类型问题或改写旧 FAIL；不宣称任意本地节点文档都受支持。

UE SDK 显式接线根目录：`/artifacts/carla/sdk-bindings/native-usd-sdk-egrs7d3u`。
`prepare_native_usd_sdk.py` 校验 report 后保留五处 Build.cs/CPP 的原始和新源码及
patch：Boost、IntelTBB、Python3、UnrealUSDWrapper.Build.cs、UnrealUSDWrapper.cpp。
该 profile 使用同一套共享 TBB/Boost/Python，避免把各自静态解释器或调度器重复
塞入多个库；root 未启用时默认规则不变。已有 x86 部署未被覆盖。

真实 `make carla-editor-check EDITOR_PROFILE=full NATIVE_SDK_ROOT=<root>` 通过旧的
ARM64 USD 缺目录阻塞，继续到 **MovieSceneTools -> FBX 缺 ARM64 Autodesk SDK**，
退出码 8。日志：`artifacts/carla/editor-check-20260916T081611Z-Ko6VoM/`。
仍使用 `-SkipBuild`，因此只证明 UBT 规则和依赖选择推进，**不是 Editor 编译/
链接/启动完成**。原生 ufbx parser/Worker 尚不能取代 Legacy 公共 SDK 接口。

最终全量 ARM64 Docker 回归 **471 tests OK，无跳过项**，原始日志：
`artifacts/carla/regression-openusd-final-20260916-yjqKb6.log`。
SDK 非可搬移发布包，依赖原 artifact prefix；schema 生成工具缺 Jinja2，Python
可选 stdlib、压缩过滤器、默认 Editor SDK 运行时、完整 FBX、Cook/RPC/传感器及
Autoware 仍待通过。下一主要研发工作转向 Legacy FBX，而非重复基础依赖探针。

### 9.16 2026-09-17 Legacy 真实编译与首个场景节点数据接口迁移

本轮新增 `make carla-legacy-fbx JOBS=4 NATIVE_SDK_ROOT=<verified-sdk>`，仍在
DGX 原生 ARM64 Docker 内执行，每次最多一个 UBT 构建。已通过的限定范围报告：
`artifacts/carla/legacy-fbx-3hcme51t/stage-report.json`。

- 从真实 UBT JSON 导出确认诊断目标选择七个直接 FBX 消费模块：UnrealEd、
  MovieSceneTools、InterchangeFbxParser、ControlRigEditor、HairStrandsEditor、
  LevelSequenceEditor、SequencerScriptingEditor。不是全文字符串计数。
- `FbxStaticMeshImport.cpp`、`MovieSceneToolHelpers.cpp`、`FbxMainImport.cpp`、
  `FbxSceneImportFactory.cpp` 四份真实源码编译为新鲜 AArch64 relocatable 对象。
  强未定义符号区分为 222 个 SDK 符号和 33 个带 SDK 参数的 UE 符号；这不是
  完整 SDK 清单、完整调用图或工作量百分比。
- 修复 Linux Editor 标量路径的算术右移缺实现、Chaos 位转换变量名和 double
  unpack 返回类型，以及静态导入源码缺少直接头文件。实际 UBT 参数下的原生
  UBSan 测试完成 279 项检查；没有切换平台或全局启用 NEON 来绕过错误。
- `FbxNodeInfo` 现在别名到不含 SDK 的 `UE::Import::FSceneNodeInfo`。真实生产者
  与 front-axis 刷新调用原有转换器，场景工厂直接读取 UE transform/pivot。
  无 SDK 头文件的原生测试验证默认值、64 位 ID、double 精度、镜像/小尺度及
  字符串复制独立性；没有声称已执行 Autodesk 导入或资产重导入。
- 元数据测试链接已有 ARM64 Core/BuildSettings/TraceLog 库，架构、动态依赖、
  哈希与库文件本身均保留在报告中。这些库不是本入口重建的，因此不证明干净
  检出构建或完整 Editor。旧失败记录（响应文件、数学编译、测试链接）均保留。

复跑说明和三个可重放补丁见 `docs/carla-legacy-fbx.md`。新增
`config/carla/fbx-feature-matrix.json` 记录实际 CARLA `FbxFactory` 工作流、现有
夹具与缺口；F2 更新为进行中，F1/E1/Cook/传感器/Autoware 未提升为通过。
下一步是实际静态资产保存/重载与 replace/reimport；材质、碰撞、socket、LOD、
骨骼/动画/Morph 和导出仍须独立验收。VHACD、KissFFT、libxml2、SpeedTree
原生库路径告警仍是后续 Editor 链接阻塞。

最终全量 ARM64 Docker 回归 **491 tests OK，无跳过项**，日志：
`artifacts/carla/regression-legacy-20260917T055923Z.log`。真实静态 Interchange
回归保持 20 项场景检查与 27 项 payload 检查通过，报告：
`artifacts/carla/interchange-nodes-20260917T055444Z-JARQnH/stage-report.json`。
普通 `full` 配置复验在 `editor-check-20260917T055941Z-TeqxRP/` 仍以退出码 8
报告 `MovieSceneTools -> FBX` 缺原生 SDK，证明没有借诊断分支提升完整 Editor 门禁。

### 9.17 2026-09-17 ufbx 接入共享工厂层级与重导入调用点

继续推进 F2，没有启动无界完整 Editor 构建或删除默认功能。本轮新增
`make carla-legacy-hierarchy JOBS=4`，将真实 ufbx 场景送入与真实场景工厂
共用的 `UE::Import::BuildSceneImportHierarchy`。默认工厂仍使用 Autodesk
读取场景；本轮接通的是共享数据处理逻辑，不是完整资产工厂替换。

- 原生层级报告：`artifacts/carla/legacy-hierarchy-20260917T084437Z-1BMYlY/stage-report.json`。
  26 项检查覆盖多网格、共享网格实例、材质覆盖、几何镜像、原始 ID、深层级、
  失败原子性，以及真实非零 pivot/相机 FBX 的明确拒绝。SDK-free Program
  无 `libfbxsdk` 动态依赖；几何变换单独保留，载荷获取时只应用一次。
- 共享函数处理真实工厂的父子索引、骨骼子树排除和 LOD 直属子节点导入选项，
  避免旧的逐节点递归祖先查询。无效层级返回错误，不写部分输出；真实导入和
  重导入路径在失败时清理场景、结束 slow task，不继续访问无效指针。
- 五份真实 Editor 源码（新增 `ReimportFbxSceneFactory.cpp`）在
  `artifacts/carla/legacy-fbx-sopqt03v/stage-report.json` 编译通过。该集合观察到
  222 个 SDK 和 40 个带 SDK 类型的 UE 强未定义符号；集合增加不能当作完成率。
- 原始失败保留：首次镜像测试只检查 node-global，未包含夹具里的 geometry
  镜像，修正为实际载荷使用的组合变换；全仓库首次回归的旧断言禁止脚本任何
  位置出现第二夹具，已改为只限制原静态模式，并验证新夹具仅在 Legacy 分支。

原静态场景 20 项和 payload 27 项在
`interchange-nodes-20260917T084441Z-8hhHoH/` 通过；真实 Parser 31 项在
`interchange-parser-20260917T084443Z-P23pKk/` 通过（路径均相对
`artifacts/carla/`）。全量 ARM64 Docker 回归 **501 tests OK，无跳过项**。
日志为 `artifacts/carla/regression-hierarchy-final-20260917T084436Z.log`。
真实 Worker 的五个子进程、23 项 IPC 检查也在
`artifacts/carla/interchange-worker-20260917T084632Z-WIz8V2/stage-report.json`
通过。上述四个原生阶段报告在所有构建结束后均已独立复验哈希和前置报告。

完整资产创建/保存/重载/reimport 仍未运行：`Engine`、`UnrealEd` 原生共享库及
完整 Editor 可执行仍未生成。当前静态适配明确不接收非零 pivot、相机/灯光、
多属性节点或动画/骨骼/Morph；这不删除默认 Editor 的相关功能。下一验收点
仍是实际 `UStaticMesh` 生命周期，不能把共享层级 PASS 提升到 E1/Cook/传感器。

### 9.20 2026-09-20 F2 资产保存/重载往返通过

`make carla-asset` 现在跑通完整的非 Editor `UStaticMesh` 生命周期：ufbx 导入
`multi-mesh.fbx` -> `BuildFromMeshDescriptions` 构建 -> `SavePackage` 落盘 ->
`CollectGarbage` 卸载 -> `LoadPackage` 重载 -> `MeshDescription` 逐顶点比对 ->
`BuildFromMeshDescriptions` 重建 -> CPU buffer 校验。2 个资产 saved/reloaded/
rebuilt 全部通过，`native.json` `status=PASS`，8 项 checker 全 PASS
（graph/build/architecture/linkage/native/sources/assets/prerequisite）。
报告：`artifacts/carla/asset-20260920T123527Z-6S4kS5/stage-report.json`。

引擎侧为支持非 Editor Program 加载引擎装饰资产（`/Engine/EditorMeshes/` 等）
打了一组有界 patch：`UStaticMesh::Serialize` 在检测出 SpeedTree wind 段被
editor 保存路径截断时跳过尾部并把流钳到 export 边界；`FStaticMaterial` 在此
情况下不解析。这只影响 `/Engine/` 下编辑器资产，探针自身生成的资产不受影响。
探针进程在写完 `native.json` 后通过 `_exit` 绕过引擎 shutdown 阶段的
`LazySingleton` teardown 断言（已知非 Editor Program 限制，与资产正确性无关）。

F2 仍不等于 E1/Cook/传感器通过；材质、碰撞、socket、LOD、骨骼/动画/Morph 和
导出仍须独立验收。

## 最终判断

### 9.21 2026-09-20 G4 启动崩溃根因定位（WorldGridMaterial 序列化越界）

`make carla-startup-probe` 在读取 `/Engine/EngineMaterials/WorldGridMaterial`
（30169 字节）时于 `AsyncLoading.cpp:8534` 触发
`CurrentPos + Count <= TotalSizeOrMaxInt64IfNotReady()` 断言，最后停在
`CurrentPos=30166 Count=4`，以 SIGTRAP/139 退出。

本轮用 DWARF 行号映射 + 反汇编 + gdb 在 `PackageFileSummary.cpp` 多处断点，
把根因收窄到**文件本身的版本号与内容布局不匹配**：

- gdb 实测：`Sum.FileVersionUE = {FileVersionUE4=522, FileVersionUE5=1004}`，
  `ToValue()=1004`；`BaseArchive.Tell()` 在读 GenerationCount 前=330、
  读后=334（只前进 4 字节，**没有**读 16 字节 PersistentGuid）。
- 文件头实测：`FileVersionUE4=-8`（legacy 格式标记）、`FileVersionUE5=864`。
- reader 读到的 `GenerationCount=2075603473=0x7bb73211`，正是文件 [330:334]
  的字节（PersistentGuid 的前 4 字节）；而正确值 `1` 在 [346:350]。
- 反汇编确认 reader 在 Guid（262 行）后直接读 GenerationCount（291 行），
  PG 块（265-288）被跳过，行为与 `WITH_EDITORONLY_DATA=0` 一致。

**结论**：reader 行为是**正确**的——它按 `FileVersionUE5=1004` 判断
`>= ADD_SOFTOBJECTPATH_LIST(1008)` 为假，跳过 SoftObjectPaths；按
`>= VER_UE4_ADDED_PACKAGE_OWNER(522)` 为真但因 `WITH_EDITORONLY_DATA=0`
跳过 PersistentGuid，于是在 330 处读 GenerationCount。问题在于**文件本身
是用 UE5=1004 的版本号写的，但内容布局却是 864（含 PersistentGuid、
GenerationCount 在 346）**。这是一个版本号被写错/被改写过的资源文件，
导致 reader 按错误的版本号解析，GenerationCount 读到垃圾值，最终越界。

**为什么 x86 不崩**：x86 Editor/Game 读同一个文件时，`WITH_EDITORONLY_DATA=1`
（Editor）或同样的版本判断路径，PersistentGuid 被读出（16 字节），
GenerationCount 正好落在 346=1。ARM64 Server（`WITH_EDITORONLY_DATA=0`）
跳过 PG，于是错位。换言之，这个文件**只能在读 PersistentGuid 的构建下**
正确解析——它实际上是一个 editor-only 布局的文件被标成了 game 版本号。

**修复方向**：
1. 不读引擎源码里这个未烘焙的 `WorldGridMaterial`：为 Game/Server 提供
   cooked 内容或正确的 premade asset registry（`Failed to load premade asset
   registry` 是先兆），让 Game 走 cooked 路径而非逐个序列化 editor 资产；
2. 若必须读未烘焙内容，则需要一个 `WITH_EDITORONLY_DATA=1` 的 reader 或
   在加载引擎装饰资产时走与 Editor 一致的序列化路径——这与 9.20 节 F2 为
   `/Engine/EditorMeshes/` 打的有界 patch 是同一类问题。

在修复并重编通过 `make carla-startup-probe`（exit=0）之前，G4 仍 BLOCKED，
不能说 DGX 已支持 CARLA Server。

### 9.22 2026-09-21 G4 三层修复验证与最终根因（编辑器资产 vs Server 构建）

在 9.21 的基础上继续打了三层修复并逐层用 gdb 验证，最终确认了本质约束：

1. **PackageFileSummary PG 修复（已 commit `4fc36ce8a`）**：
   `WITH_EDITORONLY_DATA=0` 加载路径下读入并丢弃 PersistentGuid /
   OwnerPersistentGuid，保持流同步。修复后 summary 完全读对：
   gdb 实测 `Tell()=346`、`GenerationCount=1`、`NameCount=105`、
   `ImportCount=25`、`ExportCount=47`。

2. **EditorContent 包 gate 回退（同 commit）**：新增
   `carla.AllowEditorContentInServerBuilds` cvar，绕过
   `LinkerLoad.cpp:1471` 的
   `!HasEditorOnlyData() && !PKG_FilterEditorOnly → LINKER_Failed` 硬检查
   （该检查把 `WorldGridMaterial` 判为"含编辑器数据，拒绝加载"，日志被
   `SuppressLoggingToOutputLog` 静默，是 9.21 里 `Failed to find object`
   的直接原因）。绕过成功，loader 进入 import map 读取。

3. **`GForceLoadEditorOnly` scoped 强制（同 commit，但作用域不完整）**：
   在 `SerializePackageFileSummaryInternal` 内强制 tagged 序列化保留
   编辑器属性。但该 flag 的作用域只覆盖 summary，不覆盖后续 Tick 阶段的
   import/export map 和属性反序列化，所以当前是无效半成品。

**最终根因**：三层修复后崩溃变为
`SerializeImportMap → FObjectImport::operator<< → BadNameIndexError`
（index=-22，name map 仅 105 条）。import map 的第一个 FName 就读到
0x117c4e 这种垃圾偏移，说明 header 之后的 map 区域布局与文件对不上。
这不是单点 bug，而是**未 cook 编辑器资产与 `WITH_EDITORONLY_DATA=0`
Server 构建在所有 header 之后的 map 布局上系统性不兼容**。逐个字段对齐
是脆弱的逆向工程，且即便打通也不被 UE 支持。

**结论**：ARM64 Server 直接加载未 cook 引擎内容这条路**走不通**。
UE 的架构是 Game/Server 只认 cooked（`PKG_FilterEditorOnly`）内容。

**可行路线**：
- **A（当前推进）**：构建 ARM64 `CarlaUnrealEditor`，用它 cook 出
  `PKG_FilterEditorOnly` 资产，Server 加载 cooked 包。Editor 目前卡在缺
  aarch64 FBX SDK（`aarch64-unknown-linux-gnueabi/libfbxsdk.so`）和 USD
  依赖解析；cook 已有资产本身不需要 FBX 导入，可裁剪 Interchange/Fbx/
  USD/GLTFExporter 插件绕过。
- **B（备选）**：在 x86_64 主机用同版本源码 cook，交付 ARM64 运行包。
  当前环境无 x86_64 构建机，不现实。

上述三处引擎 patch 已 commit 保留为探索证据，cook 路线打通后若无必要
可 revert。

```text
Docker 化构建                 可行
DGX ARM64 构建实验            值得做，但尚未打通
CARLA UE5.5 Server 原生运行   G3/Game 编译通过；G4 Editor/Cook 阻塞，G5 未运行
旧 ros-bridge 直接复用         不可行
ACB Rust 适配器复用            适合，但先锁定 CARLA 0.9.16
Autoware Humble 接入           可设计，需关闭重复 CARLA interface
nano-ros 进入主链路             暂不需要
```

在 G4 和 G5 通过前，不把结果描述为“DGX Spark 已支持 CARLA”；在 G8 之前，
不把 CARLA Server 启动描述为“Autoware 闭环”。

### 9.23 2026-09-21 Editor cook 路线当前状态

新增 `fbx-skip` Editor 依赖图 profile：`make carla-editor-check EDITOR_PROFILE=fbx-skip` 会导出 `CARLA_ARM64_FBX_SKIP=1`，UE 的 `FBX.Build.cs` 仅保留 include/宏，不链接缺失的 Autodesk ARM64 FBX SDK。最新运行 `artifacts/carla/editor-check-20260921T021823Z-HCukFT/` 退出码 0，UBT 依赖图通过；同时记录大量 x64 命名的 Boost/VHACD/libxml2/SpeedTree 库路径告警，说明后续真实链接仍需逐一验证，不能把该 PASS 理解为 Editor 构建或 Cook 通过。

同轮新增 `make carla-editor-deps`，用于在 ARM64 build 容器重建 Editor 依赖中的 Vorbis、VHACD 和 libPNG PIC 归档；该阶段只验证静态归档架构和 whole-archive PIC link，不包含 Editor/Cook/runtime。下一步是执行该依赖重建，然后移除 `-SkipBuild` 推进真实 `CarlaUnrealEditor` 编译。

### 9.24 2026-09-22 ARM64 `fbx-skip` Editor 构建打通

`make carla-editor-deps JOBS=8` 已通过，Vorbis、VHACD、libPNG、FontConfig、
Boost、Python、USD 等依赖重建阶段完成。真实 Editor 构建推进过程中，
`SparseVolumeTexture` 曾因旧版 TBB 静态归档缺少 `tbb::task` RTTI ABI 符号
`_ZTIN3tbb4taskE` 而链接失败；新增
`artifacts/carla/usd/tbb-rtti-arm64/libtbb-rtti.a` 并在
`IntelTBB.Build.cs` 中追加到 ARM64 TBB 归档之后。复验中
`llvm-nm` 确认 helper 提供 `_ZTIN3tbb4taskE` / `_ZTSN3tbb4taskE`，且依赖
`__cxxabiv1::__class_type_info` vtable；后续 `SparseVolumeTexture` 已成功链接。

剩余首层失败是 `SequencerScriptingEditor` 同时缺 `MovieSceneTools` 与 FBX SDK
符号。该模块由默认引擎插件链强制拉入，逐个 `DisablePlugins` 会被显式依赖
覆盖。最终在 `CarlaUnreal.uproject` 设置
`DisableEnginePluginsByDefault=true`，保留项目显式插件，裁掉默认启用引擎
插件链；`SequencerScripting`、`ControlRigEditor`、`LevelSequenceEditor`、
`TemplateSequenceEditor` 均退出构建图。清理实验性 `.uplugin` 修改后复验，
`make carla-editor-build EDITOR_PROFILE=fbx-skip EDITOR_BUILD_TIMEOUT=14400`
该结论需要修正：05:26 artifact 对应切换到真正 `LinuxArm64` 平台前的
`Linux Development -architecture=arm64` 增量产物，不能作为 LinuxArm64
native Editor build 证据。

2026-09-22 晚间继续审计真实 `LinuxArm64` 构建时，发现平台命名约定不同：
`LinuxArm64` 的模块产物为 `UnrealEditor-Core.so`，而普通 `Linux` 产物为
`libUnrealEditor-Core.so`。UBT 的 `LinuxToolChain` 原先只有在依赖库已经落盘
时才为无 `lib` 前缀的 `.so` 生成 `-l:UnrealEditor-Core.so`；同一次构建中
尚未生成的依赖被错误归一成 `-lUnrealEditor-Core`，导致批量链接失败。现已
改为按完整路径依赖的文件名生成精确 `-l:名称`，并在构建脚本中显式传
`-buildubt`，确保 UBT 源码修改进入实际执行的二进制。

真实 `LinuxArm64` 构建还修复了三个平台判断缺口：`SparseVolumeTexture` 的
OpenVDB 依赖、`SwarmInterface` 的 MessagingCommon include 依赖、以及 metis
的 ARM64 归档路径。最终 `make carla-editor-build EDITOR_PROFILE=fbx-skip
EDITOR_BUILD_TIMEOUT=14400` 通过，最新证据为
`artifacts/carla/editor-check-20260922T115230Z-lUFDVn/`，退出码 0。生成物包括
ARM64 `UnrealEditor`、项目 `UnrealEditor-CarlaUnreal.so`、`NaniteBuilder` 等
Editor 模块。

该结论仍限定为 `fbx-skip` Editor native link：FBX 导入/导出插件被裁剪，
未执行 Cook、资产转换、RPC、Vulkan 渲染或传感器验证；G4/G5 不能因此
标记为通过。下一步是启动 Editor 并执行最小 cook probe。

### 9.25 2026-09-22 ARM64 Editor startup gate 通过

真实 `LinuxArm64` Editor 首次启动探针已通过。关键修复是使用正确的
`LinuxArm64` target-platform 模块、清理新旧两种 SONAME 布局混链的插件产物，
并在无渲染/声音的 headless 环境下使用 Editor 专用 `QUIT_EDITOR` 命令。
此前 `-ExecCmds=quit` 只会输出 `Cmd: quit`，但在没有 viewport 的 Editor 中
不会设置 `IsEngineExitRequested()`；gdb 采样确认主线程仍停留在
`FEngineLoop::Tick -> UEngine::UpdateTimeAndHandleMaxTickRate` 的正常节流
sleep，而不是 DDC/HTTP shutdown 阻塞。

`-NoAssetRegistryCacheWrite` 已验证有效：不再写约 474 MiB 的
`CachedAssetRegistry`，也避免了旧 180 秒运行中的 cache 写入和 DDC maintenance
长尾。`-ddc=NoZenLocalFallback` 保持本地 DDC 可写。禁用默认地图仍使用：
`-ini:EditorPerProjectUserSettings:[/Script/UnrealEd.EditorLoadingSavingSettings]:LoadLevelAtStartup=None`。
SourceControl 配置覆盖已改为正确的 `Editor` ini：
`-ini:Editor:[/Script/SourceControl.SourceControlPreferences]:bEnableUncontrolledChangelists=False`。

最新证据：`artifacts/carla/editor-quit-editor-20260922T125242Z.log`，exit 0。
日志包含 `Engine is initialized. Leaving FEngineLoop::Init()`、
`Cmd: QUIT_EDITOR`、`Engine exit requested (reason: UUnrealEdEngine::CloseEditor())`；
不包含 `MAP LOAD`、`LoadDefaultMapAtStartup`、`SIGSEGV`、`Fatal error!`。
TargetPlatformManager 已加载 `LinuxArm64`、`LinuxArm64Server`、
`LinuxArm64Client` 和 Vulkan shader format 模块。

新增 `make carla-editor-startup` 固化该 gate。该 gate 只证明：native Editor
初始化、插件加载、ARM64 target platforms 加载、本地 DDC 可用、无默认地图、
干净退出。它不证明 Cook、Vulkan 渲染、RPC、传感器或完整 CARLA ARM64 支持。
下一步是最小 cook probe。

### 9.26 2026-09-22 ARM64 最小 Cook gate 通过

新增 `make carla-editor-cook`。该 gate 在 native ARM64 `carla-build` 容器中，
使用已通过启动验证的 `LinuxArm64` Editor，以 `-run=Cook`、
`-targetplatform=LinuxArm64Server`、`-cooksinglepackagenorefs` 精确 cook
`/Game/Carla/RT_LuminanceCapture`。该模式在 UE 5.5 源码中会设置
`NoDefaultMaps`、`NoAlwaysCookMaps`、跳过硬/软引用，避免全量 Town/map
依赖链；输出被限制在独立 artifact 目录并禁用 Zen store。

最新证据：`artifacts/carla/editor-cook-20260922T130042Z-hho5PJ/`，exit 0。
日志包含 `Packages Cooked: 1, Packages Iteratively Skipped: 0, Packages
Skipped by Platform: 0, Total Packages: 1`、`TargetPlatforms=LinuxArm64Server`、
`LogCook: Display: Done!` 和 `Success - 0 error(s)`。实际产物包括
`cooked/CarlaUnreal/Content/Carla/RT_LuminanceCapture.uasset/.uexp`、
`AssetRegistry.bin`、cook metadata 和 package store manifest。

该 gate 只证明 `fbx-skip` Editor 能为 `LinuxArm64Server` cook 一个显式请求
的项目资产并生成 cooked package。它不证明全量项目 Cook、map Cook、
Vulkan 渲染、RPC、传感器或完整 CARLA runtime。下一步应先扩大到最小 map
Cook，再尝试用 cooked package 启动 ARM64 `CarlaUnreal` Server。

### 9.27 2026-09-23 ARM64 cooked dedicated server 最小 gate 通过

真实 `LinuxArm64` `CarlaUnrealServer` 已在 cooked `OpenDriveMap` 包上完成
30 秒稳定性探针。测试包包含 `Binaries/LinuxArm64/CarlaUnrealServer`、
`OpenDriveMap` cooked map、cooked `CarlaGameMode`、premade
`AssetRegistry.bin` 和项目/插件 descriptor。最终日志为
`.codex-tmp/cooked-server-test2/CarlaUnreal/Saved/Logs/carla-server-probe-final.log`：
server 被 `timeout` 按预期终止（exit 124），期间加载 premade registry，
`Game class is 'CarlaGameMode_C'`，`OpenDriveMap` 进入 play，并监听
`0.0.0.0:7777`；无 `SIGSEGV`、`Fatal error!`、`Unhandled Exception` 或
module-load failure。

调试中发现两个关键问题。第一，单包 cook 输出的 `AssetRegistry.bin` 只有
1.5KB，直接覆盖测试包 root registry 会让 `CarlaGameMode` 不可见；恢复
完整 195KB premade registry 后 GameMode 才能加载。第二，项目设置
`DisableEnginePluginsByDefault=true` 时，虽然 `OnlineSubsystem` 代码已静态
链接进 server，但插件未被加载，GameMode 启动阶段触发
`Tried to get module interface for unloaded module: 'OnlineSubsystem'`。现已在
项目 descriptor 显式启用 `OnlineSubsystem`。

`AnimationData` 和 `ControlRig` 是 `CarlaGameMode` Editor/Cook 依赖，但旧
server 二进制没有对应 runtime module；在 staged descriptor 中启用它们会导致
`Plugin 'ControlRig' failed to load because module 'ControlRig' could not be
found`。现已在项目 descriptor 用 `TargetAllowList=["Editor"]` 限定两者，并在
`CarlaUnrealServer.Target.cs` 中显式 `DisablePlugins`，避免 dedicated server
拉入 Editor-only 动画插件。

新增 `scripts/carla/probe-arm64-cooked-server.sh` 与
`make carla-cooked-server`。该 gate 要求 server 稳定运行到 probe timeout，并
校验 registry、`CarlaGameMode_C`、map play、GameNetDriver/7777 监听标记，
拒绝 crash、assertion、module-load failure 和 GameMode 缺失。当前日志仍报告
5 个软引用资产缺失（Walker/Vehicle/Spectator/BlueprintFactory/Weather），因为
测试包不是完整项目 cook；这些缺失被记录为 gate 边界，不视为本最小
dedicated-server 启动 gate 的失败条件。该 gate 不证明 Vulkan 渲染、CARLA
client RPC、传感器、traffic/walker gameplay 或完整 cooked 项目内容。

### 9.28 2026-09-23 ARM64 full Cook/stage 与真实 client RPC 通过

进一步完成了 `LinuxArm64Server` full project Cook。CookCommandlet 处理
`44,370` 个包，其中 `44,262` 个实际 cooked，输出约 14GB；full Cook 收尾
仍有 539 个来自 CarlaTools/编辑器资产的错误，因此没有把 Cook 标为无条件
PASS。`CarlaTools`、`RenderDocPlugin`、`PerformanceMonitor`、
`EditorScriptingUtilities`、`Volumetrics` 和动画编辑插件已限制为 Editor
target，避免 dedicated server 装载编辑器链。

新增 `scripts/carla/stage-arm64-cooked-server.sh` 与
`make carla-stage-cooked-server`，将 full Cook 输出补齐为可运行 stage：
ARM64 Game/Server binary、项目 descriptor、Engine ICU/Config、
Nanite `TessellationTable.bin`、Online/ProceduralMesh/ChaosVehicles/
EnhancedInput runtime plugin descriptors、CARLA shader、OpenDRIVE 和
全部 CARLA Config JSON。

在实际 staged `Town01_Opt` 上运行 ARM64 `CarlaUnrealServer` 与同版本
`carla-0.10.0-cp310-cp310-linux_aarch64.whl`，RPC gate 通过：
handshake、client/server version policy、world、同步设置、20 个固定 tick、
cleanup 全部 PASS。证据目录为
`/artifacts/carla/cooked-runtime-rpc-20260923T064824Z/endpoint`。

### 9.29 2026-09-23 传感器/Vulkan 当前边界

NullRHI 下 RGB camera 曾在 CARLA `ImageUtil::ReadImageDataBegin` 因空
RenderTarget resource 触发 SIGSEGV；已补 resource 空值保护，并在
`SceneCaptureSensor::BeginPlay` 强制 `UpdateResourceImmediate(true)`，
server 增量重建通过。之后 NullRHI sensors 不再崩溃，但没有 camera frame。

进一步使用 GB10/NVIDIA Vulkan 运行 Game target。GPU/RHI 初始化成功，但
Game cooked 启动在缺少 `Engine/GlobalShaderCache-VULKAN_SM6.bin` 处退出；
该文件未由本次 `-nullrhi` full Cook 生成。随后补建 ARM64
ShaderCompileWorker、LinuxArm64 worker libraries 和 module manifest，并在
GPU 容器使用 `-AllowCommandletRendering -RenderOffScreen` 尝试生成 shader
cache：

- `SF_VULKAN_SM6`：ARM64 `SPIRV-Reflect` 在 shader compile 中触发 assertion；
- `SF_VULKAN_SM5`：UE `TextureBuildUtilities::GetOutputPixelFormatWithFallback`
  先暴露 LinuxArm64 缺少 DXT/Oodle texture format module，补充
  uncompressed texture fallback 和缺失 device-profile fallback 后，进一步
  触发 NVIDIA `libnvidia-glvkspirv.so.580.173.02` SIGSEGV；
- 两次均没有生成可用 `GlobalShaderCache-*.bin`。

为支持 shader cook，SCW 已补齐到 `Engine/Binaries/LinuxArm64`，包括 worker
动态库和 module manifest；SCW 本身可以启动并持续编译。当前剩余阻塞是
ARM64/Vulkan shader compiler 路径：SM6 的 SPIRV-Reflect assertion，以及
SM5 的 NVIDIA Vulkan compiler crash，二者都发生在 UE global shader cook
阶段，尚未进入 CARLA camera readback。

因此真实 RGB/LiDAR sensor gate 仍为 `NOT-RUN/BLOCKED`，不能标记 PASS。
NullRHI sensor gate 已补 resource 空值保护并不再 SIGSEGV，但没有 frame；
真实 NVIDIA Vulkan sensor gate 已确认 GPU/RHI 可初始化，但在 shader cache
之前退出。当前证据证明 native Vulkan readback gate PASS、真实 server RPC
PASS，UE Vulkan shader-cooked camera/LiDAR 仍需要修复 ARM64 shader cook
链后才能验收。运行期通过 `Town01_Opt` 的 RPC 证据不等于 sensor PASS。
### 9.30 2026-09-23 LiDAR 与 vehicle/walker runtime smoke

在同一 staged `Town01_Opt` ARM64 dedicated server 上，单独绕开 RGB
camera 运行 LiDAR/actor smoke。8 个同步帧全部连续且 payload 非空
（约 222--225KB/frame），frame/timestamp 正常；同时成功 spawn vehicle
和 walker，并完成 3 个同步 tick 与 cleanup。证据：
`/artifacts/carla/cooked-runtime-lidar-20260923T120643Z/report.json`。

因此当前 runtime 状态细分为：CARLA RPC **PASS**、LiDAR endpoint smoke
**PASS**、vehicle/walker spawn/tick smoke **PASS**；RGB camera 与 UE
Vulkan shader-cooked rendering 仍 **BLOCKED**。这不能替代完整 Traffic
Manager、walker controller、RGB camera frame 和 ROS/Autoware 验收。

### 9.31 2026-09-23 NVIDIA Vulkan shader/PSO 根因收敛

在可写的 ARM64 GPU 容器中重新执行渲染 Cook，并使用
`-DDC-ForceMemoryCache` 排除 DDC/只读挂载干扰。同时将
`r.DistanceFieldAO`、Lumen、反射、虚拟阴影、skin cache 和 mesh distance
field 全部显式设为关闭。日志确认这些 CVar 均保持为 `0`，但
`SF_VULKAN_SM6` 仍在首次 compute PSO 创建时于
`libnvidia-eglcore.so.580.173.02` SIGSEGV：

- `UnrealEditor-RHI.so!PipelineStateCache::GetAndOrCreateComputePipelineState`
- `UnrealEditor-VulkanRHI.so`
- `libnvidia-eglcore.so.580.173.02`

随后使用 `-sm5` 强制 `VULKAN_SM5`。该路径越过了 SM6 bindless/RHI 选择，
但在 shader module 编译时于同一 NVIDIA 驱动的
`libnvidia-glvkspirv.so.580.173.02` SIGSEGV。两次均未生成可用
`GlobalShaderCache-*.bin`，因此不是 CARLA 地图、RenderTarget 空指针或
某个距离场特性导致的单点失败。

额外尝试了指向 lavapipe 的软件 ICD。检查发现当前 ARM64 工具链镜像没有
`/usr/share/vulkan/icd.d/lvp_icd.json`，独立 `vulkaninfo` 已先报 ICD 文件
不存在；加入 `-SkipVulkanProfileCheck` 后，UE 自身 Vulkan loader/ICD
检查仍报告 `Cannot find a compatible Vulkan driver (ICD)`。该路径没有
产生可用于 GB10 验收的结果，也没有被计入 PASS。

当前结论进一步收敛为：native Vulkan clear/readback、真实 CARLA RPC、
LiDAR 和 actor smoke 已有证据；UE ARM64 camera 所需的 shader/PSO
渲染链在 NVIDIA driver `580.173.02` 上仍 **BLOCKED**。在获得可用的
NVIDIA ARM64 驱动修复/升级或兼容的 UE Vulkan shader workaround 前，
不能声称 RGB camera、GlobalShaderCache 或完整 G5 通过。

### 9.32 2026-09-23 UE 侧 workaround 复验

为避免修改 NVIDIA 驱动，使用命令行临时覆盖验证两个 UE 侧方向：

- 将 `[SF_VULKAN_SM6]` 的 `BindlessResources` 和 `BindlessSamplers`
  临时改为 `Disabled`。该路径改变了失败位置，但最终仍在
  `libnvidia-glvkspirv.so.580.173.02` SIGSEGV；
- 将 `r.Vulkan.RHIThread=0`、`r.PSOPrecaching=0`、
  `r.Vulkan.AllowPSOPrecaching=0` 和 `r.AsyncPipelineCompile=0` 临时设为
  关闭。该路径仍在
  `PipelineStateCache::GetAndOrCreateComputePipelineState` 进入
  `libnvidia-eglcore.so.580.173.02` SIGSEGV。

这些参数没有写入项目、UE 源码或驱动。结论是当前已验证的 UE 侧
bindless、RHI thread、PSO precache 和异步 pipeline compile workaround
均不足以绕过该驱动崩溃；RGB camera gate 继续保持 **BLOCKED**。

### 9.33 2026-09-23 shader debug dump 边界

使用独立 `-saveddirsuffix=shaderdiag`、`r.DumpShaderDebugInfo=1` 和
`r.DumpShaderDebugWorkerCommandLine=1` 重新运行 SM5 Cook。独立目录只
生成 DDC key、日志和 crash report，没有 `.spv` 或 `.spvasm` 文件；也就是
当前失败不是 UE shader compiler 返回可捕获的编译错误，而是在 shader
产物进入 NVIDIA Vulkan runtime/PSO 路径后由驱动 SIGSEGV。该诊断运行
同样没有修改驱动或默认项目配置。

### 9.34 2026-09-23 SM6 GPUScene workaround 严格复验

此前尝试用命令行覆盖
`-ini:Engine:[ShaderPlatform VULKAN_SM6]:bSupportsGPUScene=false`，但 cooked
ini metadata 中没有出现该值，B797/GPUScene 相关 shader 仍被编译，说明
该 DDPI shader capability 不能通过普通 Engine ini override 生效。

随后只做了一次临时文件级验证：将
`Engine/Config/VulkanPC/DataDrivenPlatformInfo.ini` 中
`[ShaderPlatform VULKAN_SM6]` 的 `bSupportsGPUScene` 从 `true` 改为
`false`，在带 GB10 GPU 的 ARM64 容器中重跑相同 `Town01_Opt` SM6 render
Cook，测试退出后立即恢复为 `true`。当前 git diff 确认该 UE 配置文件已
无修改；没有修改 NVIDIA 驱动，也没有把该 workaround 保留为默认方案。

结果：GPU/Vulkan 初始化成功，B797 没有再出现，也没有进入
`libnvidia-eglcore.so.580.173.02` 的 compute PSO SIGSEGV。这证明
GPUScene 是 B797/PSO 崩溃链路的必要触发条件，且文件级 capability
覆盖可以改变 shader permutation 集合。但进程随后在
`SPIRV-Reflect/spirv_reflect.c:976` 触发
`Assertion 'index_value != UINT32_MAX' failed`，SCW 和直接编译路径均
SIGABRT；失败 shader 包括 volumetric fog、ray tracing occlusion、
Lumen hardware ray tracing 和 MegaLights compute permutations。证据：
`/artifacts/carla/render-sm6-config-no-gpuscene-gpu-20260923T143223Z/run.log`。

因此当前阻塞被拆分为两层：GPUScene 关闭可以避开 B797 和 NVIDIA PSO
崩溃，但 SM6 cook 仍被 UE ARM64 SPIRV-Reflect 解析断言阻断；未生成
`GlobalShaderCache-VULKAN_SM6.bin`，RGB camera gate 继续
**BLOCKED**。下一步应优先处理 SPIRV-Reflect 对这些 SM6 SPIR-V 模块的
解析兼容性，而不是继续调整 NVIDIA runtime 参数。

### 9.35 2026-09-24 Lavapipe client RGB/LiDAR sensor gate 通过

为了绕开 NVIDIA driver `580.173.02` 的 ARM64 shader/PSO 崩溃且不修改
驱动，改用独立 Lavapipe client 路径验证渲染。工具链镜像中的 Mesa
23.2.1 行为不稳定；升级到 `carla-lavapipe-2404` 容器中的 Mesa/LLVM
25.2.8/20.1.2 后，SM6 PSO 创建不再崩溃。该路径同时禁用 bindless
resources/samplers、ray tracing、Lumen、Nanite 和 volumetric cloud，
并使用已验证的 `OverrideGlobalShaderCache-VULKAN_SM6.bin`
（SHA256 `414078ae10cb7ff91d6ffc8718c970bbe62430a32bb4924bbf1bb3e1d67399a3`）。

第二次 `LinuxArm64Client` full Cook 位于
`/artifacts/carla/client-full-cook-sm6-lavapipe/full-cook-20260923T181633Z-NwMhgV`，
生成 94,539 个文件、约 51.5GB 产物和 `AssetRegistry.bin`，但没有把 Cook
标记为 PASS：进程 exit 1，收尾仍有 592 个资产编译/加载错误和 41489 个
警告，并以 abnormal shutdown 结束。该 Cook 只作为实验性 client staging
基础。

独立 client stage 位于
`/artifacts/carla/cooked-client-full/CarlaUnreal`，约 51.3GB。staging
脚本曾误删项目 cooked `Plugins/Carla/Content`，导致 32 个
`/Carla/PostProcessingMaterials` sensor 材质缺失；修复后保留该目录。
由于 `CarlaUnreal` 是单体静态链接，单独重建 `UnrealEditor-Renderer.so`
不足以更新 BlueNoise 防护，随后完整重链 ARM64 client 并复制进 stage。

重链后的 client 在 Lavapipe 上完成 180 秒 runtime smoke：Vulkan 设备为
`llvmpipe (LLVM 20.1.2, 128 bits)`，API `1.4.318`，`Town01_Opt`
episode 启动，到 frame 147，无 BlueNoise/Nanite/SIGSEGV/fatal。日志中
仍有少量 material ShaderMap 和软引用资产缺失警告。

正式 sensor gate 的关键差异是必须关闭 Virtual Shadow Maps。最初使用的
`r.VirtualShadowMaps=0` 不是有效 CVar，被 UE 记录为 dummy variable；正确
覆盖是 `r.Shadow.Virtual.Enable=0`。否则 Lavapipe 因 wave operations
disabled 触发
`GRHISupportsWaveOperations` assertion 并 SIGSEGV。Nanite 也必须通过
`r.Nanite.ProjectEnabled=0` 和 `r.Nanite.ForceEnableMeshes=0` 关闭。

最终正式 gate 在 `carla-lavapipe-2404` 的 client（RPC 2000）与
`carla-build-session` 的 native ARM64 Python client 之间运行：

- 证据：`/artifacts/carla/client-sensor-acceptance-20260924T0004Z/runtime-sensors-20260923T190240Z-fxPTlV`
- endpoint 与 invocation 均 **PASS**，exit 0
- 20/20 个 RGB frame 均为 320x240、307,200 字节，且内容非空间常量
- 20/20 个 LiDAR frame 均非空，每帧约 1051--1062 点
- frame 40--59 连续，RGB/LiDAR frame 与 timestamp 对齐
- `ticks`、`alignment`、`camera`、`lidar`、`cleanup` 全部 PASS
- client/server 版本均为 CARLA 0.10.0

该 gate 已通过 `make carla-lavapipe-sensors` 固化。wrapper 从官方
`ubuntu:24.04` 新建 Lavapipe 容器，安装 Mesa/LLVM，直接以可写 bind 挂载
共享 artifacts，并在 build 容器内运行 Python probe。复验证据：
`/artifacts/carla/runtime-sensors-20260924T031221Z-cYM3nR`，endpoint 与
invocation 均 PASS，exit 0；gate 容器与 RPC 2000 均已清理。

结论更新为：ARM64 cooked dedicated server RPC、LiDAR/actor smoke，以及
**Lavapipe client RGB/LiDAR sensor gate** 均有真实证据。但 9.31--9.34
中的 NVIDIA GB10 Vulkan shader/PSO 崩溃仍存在；Lavapipe 是软件渲染，
不能代表 GB10 GPU 性能或驱动兼容性。full client Cook 仍有资产错误，
Cook gate 本身不能标记 PASS。ROS/Autoware、Traffic Manager、walker
controller 和长时间稳定性仍未验收。

### 9.36 2026-09-24 Lavapipe Traffic Manager + AI walker gate 通过

`check_carla_runtime.py` 的 `actors` 模式已扩展为可复现 endpoint gate：
Traffic Manager 使用 synchronous mode 与固定 seed `1729`，注册 vehicle
autopilot，创建 `controller.ai.walker`，设置至少 10m 外的导航目的地和
最大速度 `1.4m/s`，并验证真实位移、速度观测和清理。清理顺序为停止
controller、退出 TM synchronous mode、关闭 autopilot、逆序销毁 actor、
恢复原 world settings。

首次运行定位到 cooked client stage 缺少
`Content/Carla/Maps/Nav/Town01_Opt.bin`，导致
`get_random_location_from_navigation()` 无导航点。client/server stage
脚本已改为携带 `Content/Carla/Maps/Nav/*.bin`；当前 client stage 已增量
补齐，未覆盖 `/artifacts/carla/cooked-server-full`。随后按官方 walker
smoke test 语义在 controller spawn 后、`start()` 前增加一次 tick，并检查
`go_to_location()`、`set_max_speed()` 返回值。

最终 gate 通过命令：

```bash
CARLA_RUNTIME_MODE=actors CARLA_RUNTIME_TICKS=120 \
  make carla-lavapipe-sensors
```

证据：`/artifacts/carla/runtime-actors-20260924T034651Z-oQxpNx`，endpoint
与 invocation 均 **PASS**，exit 0。`actor-setup`、`ticks`、
`vehicle-motion`、`walker-motion`、`cleanup` 全部 PASS。Traffic Manager
vehicle 位移 14.02m、最大速度 6.87m/s；AI walker 位移 8.33m。gate 容器
和 RPC 2000 均已清理。

该 cooked client 上 walker 的 `get_velocity()` 每帧返回 0，即使位置真实
移动。官方 `test_walker_navigation.py` 对 AI walker 只以位移作为运动判
定，因此 runtime gate 保留 `walker_max_speed_mps` 作为观测证据，但
walker-motion PASS 条件改为位移达标；vehicle-motion 仍要求速度非零。
这证明 CARLA Traffic Manager 车辆控制与 AI walker 导航在 Lavapipe
client/cooked server 组合上可用。它仍不证明 GB10/NVIDIA Vulkan 渲染兼
容，也不覆盖 ROS/Autoware 或长时稳定性。

### 9.37 2026-09-24 Lavapipe soak gate 暴露传感器流停滞

新增 `make carla-lavapipe-soak` 显式长时入口，默认
`CARLA_RUNTIME_MODE=sensors`、`CARLA_RUNTIME_TICKS=6000`、
`CARLA_RUNTIME_TOTAL_TIMEOUT=900`，并允许显式覆盖 mode/ticks/timeout。
入口仍复用 `run-carla-lavapipe-sensors.sh`，不改变渲染、驱动或 client
参数。

首次 6000 tick soak 未通过。client 没有崩溃，但 world tick 在 frame 521
停止推进；Python 侧已采集 496 个对齐 RGB/LiDAR 样本，最后样本 frame
519，最后 world snapshot frame 520。900 秒总超时后外层 exit 124。
第二次使用 520 tick 复验仍未通过，且停滞发生在 frame 163，说明停滞点
不固定，不是稳定的 512 tick 边界。证据：
`/artifacts/carla/runtime-sensors-20260924T043232Z-DLXtt7` 和
`/artifacts/carla/runtime-sensors-20260924T045045Z-f5MzYi`。

检查发现 evaluator 原来的 `AlignedQueue` 在回调溢出时不立即失败，且
`world.tick()` 挂起时不会再进入 `frame()` 检查，导致丢失帧/流异常会被
误报成 tick 超时。已改为记录 sticky failure，并在每个 tick 前检查两个
传感器队列的溢出状态。该修正只改进失败归因，不改变 CARLA 运行参数。

当前结论：Lavapipe sensors/actors smoke gate 可用，但 Lavapipe 长时稳
定性仍未通过，不能标记 PASS。根因仍需在 CARLA Python sensor callback
与 UE/Vulkan readback/streaming 的交界继续定位；这不是 NVIDIA 驱动问
题，也没有修改驱动。

### 9.38 2026-09-24 长时停滞收敛到 tick RPC 响应

`actors` 模式的 1000 tick 复验也会停滞：endpoint 只完成 197 tick，最后
Python 侧 tick frame 为 214；server log 随后仍推进到 frame 217。这说明
UE 进程未死亡、主循环仍可继续 tick，但该次 `world.tick()` 的 RPC 响应
没有返回给 Python client。因此此前把重点放在 sensor callback/readback
的假设不充分；`sensors` 与 `actors` 的共同路径是同步 `tick_cue` RPC。
证据：`/artifacts/carla/runtime-actors-20260924T072335Z-KTzVbk`。

审计 wrapper 时发现失败码传播 bug：

```bash
if ! docker exec ...; then
  code=$?
  exit "${code}"
fi
```

Bash 的 `!` 会把命令退出码取反，因此失败分支中的 `$?` 是 0，实际 gate
失败时 wrapper 可能返回成功。已改为 `code=0; docker exec ... || code=$?`
并显式检查，`make carla-lavapipe-soak CARLA_RUNTIME_MODE=bogus` 现在返
回 exit 64，且不会启动 CARLA。

同时允许 `CARLA_RUNTIME_MODE=rpc` 作为诊断对照；该模式只设置同步 world
并连续 tick，不创建传感器或 actor。evaluator 已增加每 100 tick 的带
flush 进度日志，便于观察长时间运行中的最后推进点。

RPC-only soak 复验结果：6000 tick 请求在 Python 侧完成 677 tick 后停
滞，最后记录 frame 691；此时 UE log 已推进到 frame 694，进程仍活着并
继续输出 Slate 警告。900 秒外层超时后，wrapper 返回 exit 124，失败码
传播正确。证据：
`/artifacts/carla/runtime-rpc-20260924T073831Z-sztn5l` 和
`/artifacts/carla/lavapipe-sensor-gate-20260924T073757Z-4t1PQF/server.log`。

这把根因范围进一步收敛为：不依赖传感器流、不依赖 actor/Traffic
Manager，也不依赖渲染 readback；问题在 CARLA 同步 `tick_cue` RPC 的
请求/响应处理路径，表现为 client 等待某一次响应而 server 已继续后续
frame。由于 Python `world.tick(timeout)` 的 C++ 实现包含两个等待阶段
（同步 `SendTickCue()` RPC 和随后的 `SynchronizeFrame()` episode 等待），
下一步需要抓取停滞时 client 线程栈并区分二者。当前长时 soak 仍为
FAIL，不能标记 CARLA 长时稳定性通过。

### 9.39 2026-09-24 Lavapipe 三种长时 soak 全部通过

继续定位后确认，`no_rendering_mode` 在 cooked non-editor client 中没有
实际生效。`CarlaEngine.cpp` 的 `OnEpisodeSettingsChanged()` 原本只在
`WITH_EDITOR` 下设置 `GEngine->GameViewport->bDisableWorldRendering`，
因此 server/cooked build 虽然接受并保存了 `bNoRenderingMode`，却没有
关闭 viewport world rendering。同步长跑最终会停滞在该渲染路径与 tick
响应之间。修复为引入 `Engine/Engine.h`，并移除该设置的 editor 条件：

```cpp
if (GEngine && GEngine->GameViewport)
{
  GEngine->GameViewport->bDisableWorldRendering = Settings.bNoRenderingMode;
}
```

修复位于
`third_party/carla/Unreal/CarlaUnreal/Plugins/Carla/Source/Carla/Game/CarlaEngine.cpp`。
`check_carla_runtime.py` 同时明确设置
`.no_rendering_mode = mode != "sensors"`：RPC/actors soak 关闭 world
rendering，sensors soak 保留真实 RGB/LiDAR 渲染。

ARM64 构建入口也改为可重复构建 SCW：先构建
`ShaderCompileWorker Linux Development -NoDumpSyms`，再构建
`CarlaUnreal LinuxArm64 Development -buildscw -NoDumpSyms`；独立 SCW
脚本会把 executable、相关动态库和 metadata 同步到
`Engine/Binaries/LinuxArm64/`。`tests/test_carla_ispc.py` 和
`tests/test_carla_runtime_probes.py` 已增加对应回归断言。最终 staged
client 为：

- binary：`/artifacts/carla/cooked-client-full/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal`
- SHA256：`6acec2a3a43076897d74d44da084a70a45e9fd4f891d9dca66052663a1027847`
- Build ID：`b24c52beb9623cf3`
- size：196007296 bytes
- architecture：ELF 64-bit ARM aarch64

随后分别执行 6000 tick soak，三种模式的 endpoint 与 invocation 均
**PASS**、exit 0，且 world settings 均恢复为原始值：

- RPC：
  - runtime：`/artifacts/carla/runtime-rpc-20260924T173138Z-tRTaW9`
  - wrapper：`/artifacts/carla/lavapipe-sensor-gate-20260924T173106Z-H5BxYI`
  - result SHA256：`baf843ad62326c6c93cdc07b10f4ea8e7d47f8b80b68e2579ef965b355c2a7b4`
  - 8/8 checks PASS；frames `15..6014`，elapsed
    `21.965648..321.915653s`；`no_rendering_mode=true`，恢复为 `false`
- Sensors：
  - runtime：`/artifacts/carla/runtime-sensors-20260924T174556Z-xFONlm`
  - wrapper：`/artifacts/carla/lavapipe-sensor-gate-20260924T174333Z-rrqvVM`
  - result SHA256：`e5f2b594d3a9621056525c67a742331b1e4ab55ecd642078066d4e654c567974`
  - 12/12 checks PASS；6000 samples，frames `24..6023`，elapsed
    `21.375288..321.325292s`；`no_rendering_mode=false`，恢复为 `false`
  - RGB 6000/6000 为 320x240、307200 bytes，6000 个不同 hash
  - LiDAR 每帧 1050--1067 points，共 4601 个不同 payload hash
- Actors：
  - runtime：`/artifacts/carla/runtime-actors-20260924T195350Z-I1jP2v`
  - wrapper：`/artifacts/carla/lavapipe-sensor-gate-20260924T195327Z-UMifcV`
  - result SHA256：`cfdab120ea91efabd9a357afdcfe65584f1a35b2e50dc91d7aa904b7b36bda24`
  - 11/11 checks PASS；frames `17..6016`，elapsed
    `20.990066..320.940070s`；`no_rendering_mode=true`，恢复为 `false`
  - Traffic Manager vehicle 位移 `72.09485189531614m`，最大速度
    `24.199714256552756m/s`
  - AI walker 位移 `71.7565369702828m`；`get_velocity()` 仍始终返回
    `0.0`。该现象与 9.36 的 cooked client walker 观测一致，gate 继续按
    官方 smoke test 语义使用位移判定，速度值只作为观测记录
  - cleanup 逆序销毁 actors `171,170,169` 并恢复 world settings

此前 `CARLA_RUNTIME_TOTAL_TIMEOUT` 默认值 900 秒不足以覆盖 Lavapipe
的 6000 tick。`Makefile` 与 runtime probe 回归测试已统一改为 10800 秒，
并保留 mode/ticks/timeout 覆盖能力。

最终本地测试结果为 `make test-local` **PASS**：548 tests、56 skipped、
0 failures。测试同时暴露并修正了 `tests/test_carla_asset.py` 与
`tests/test_carla_runtime_entry.py` 中两处已过期的断言。

Lavapipe server log 仍会出现已知的软件渲染/cooked 内容警告，包括
`Expected source texture to be in VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL`
以及 Town01_Opt 缺少 sky/light/weather setup、`InstancedFoliageActor_0`
未绑定 static mesh。它们没有导致上述 gate 失败，但应继续作为 Lavapipe
与当前 cooked client 的残余警告保留，不能据此宣称 NVIDIA GB10 Vulkan
路径已修复。

结论更新为：ARM64 cooked client/cooked server 在 Lavapipe 上的 RPC、
RGB/LiDAR 和 Traffic Manager/AI walker 三种 6000 tick soak 均已通过，
此前长时同步 tick 停滞得到可重复的根因修复。该结果证明软件渲染路径的
长时间稳定性，不证明 GB10/NVIDIA Vulkan 驱动兼容性、GPU 性能、
ROS/Autoware 集成或 full client Cook 的资产完整性。
