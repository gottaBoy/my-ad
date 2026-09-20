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

## 最终判断

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
