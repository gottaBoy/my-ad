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
`artifacts/codex-tmp-archive/20261002T090908Z/cooked-server-test2-saved-logs/carla-server-probe-final.log`
（原在 `.codex-tmp/cooked-server-test2/CarlaUnreal/Saved/Logs/`，该临时目录已于
2026-10-02 归档并移除，原因见 9.87）：
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

### 9.40 2026-09-25 ARM64 LinuxArm64Client full Cook 通过

复核 9.35 中失败的 `cookall` 证据后确认，该运行发生在
`DefaultGame.ini` 的 `DirectoriesToNeverCook` 修复提交之前：当时 run 的
`carla-tracked.patch` 为空，而当前配置已排除 `/CarlaTools`、
`/Game/Carla/HoudiniEngine`、`PedestrianOnSidecar`、
`BP_multipleLights`、`WD_RepSplinePlaneCreator`、`BP_Signs` 和
`TrafficLights2025/DataTables`。旧日志中的主要错误正是这些 editor-only、
失效 rig/skeleton、Houdini 和 DataTable 资产。

使用当前配置和 Lavapipe ARM64 ICD 重新执行
`scripts/carla/probe-arm64-full-cook.sh` 后，旧错误集全部消失。第一轮
复验 Cook body 已完成，但 UE 因唯一的
`KnownCompositeTexture == CompositeTexture` ensure 退出 1：
`T_Concrete_04_d` 的 stale `CompositeTexture` 指向
`Pathway_Block_Mat_BaseColor`。该 ensure 本身是 UE 的 legacy 资产兼容性
检查，随后代码会调用 `NotifyIfCompositeTextureChanged()` 自动修复内存
状态；它不是 ARM64 Cook 失败。因此 probe 在 unattended commandlet 中
显式加入 UE 支持的 `-handleensurepercent=0`，只抑制该类 handled ensure
报告，不修改资产、驱动或 runtime 配置。

同时修正 probe 的 sentinel package 断言。原断言误以为
`SM_PlasticBag`、`SM_StreetAD01`、`SM_calibration` 位于
`Content/Carla` 根目录；实际 cooked 输出必须保持源目录层级。现在检查：

```text
Static/Dynamic/00_LegacyAssets/PedestrianProps/SM_PlasticBag.uasset
Static/Static/00_LegacyAssets/SM_StreetAD01.uasset
Static/Static/Materials/Calibrator/SM_calibration.uasset
```

最终正式证据：

- run：
  `/artifacts/carla/full-cook-20260925T021502Z-56QjA2`
- status：`PASS`，step `complete`，probe exit 0
- target：`LinuxArm64Client`，rendering cook enabled，Lavapipe ICD
- Cook：43,779 packages cooked，88 skipped by platform，total 43,867
- UE summary：`Success - 0 error(s), 36978 warning(s)`
- output：93,374 files，48,469,129,218 bytes，约 46GB
- `cook.log` SHA256：
  `44e4b95c1db55f73438d849658f65d313d64b870bcc2502cf4eb98564cd83dd2`
- `output-files.txt` SHA256：
  `d83aa0034a51fc53fae438663ec45d458c801962942c9ece52c4eb7be915e4fc`

该 PASS 只覆盖 `Content/Carla` 的完整 ARM64 client Cook 及三个 sentinel
package 存在性检查，不包含 staging、client RPC、传感器或 traffic/walker
gate。日志仍有 36,978 个 warning，主要是 legacy material sampler 类型、
缺少引用、软件渲染/Nanite card generation 和 UE editor material 序列化
警告；因此它证明 Cook/资产编译路径通过，不能宣称资产质量或视觉正确性
全部通过。

### 9.41 2026-09-25 9.40 的 full Cook 是假通过：-COOKDIR 不 cook 任何地图

复核 9.40 的产物后确认该 PASS 不成立，并已定位到 UE 侧的确定性原因：
`-COOKDIR` 的目录枚举只匹配 `*.uasset`。
`UCookOnTheFlyServer::CollectFilesToCook` 对每个 cook directory 调用
`IFileManager::Get().FindFilesRecursive(Files, *DirectoryName,
*(FString(TEXT("*")) + FPackageName::GetAssetPackageExtension()), ...)`
（`third_party/unreal-engine/Engine/Source/Editor/UnrealEd/Private/CookOnTheFlyServer.cpp:9534`），
而地图扩展名是 `FMapPackageExtension`（`.umap`）。因此 `*.umap` 永远不会被
cook directory 收集，插件 `Content` 也在项目 `Content/Carla` 目录之外。

实测差异：9.40 的 cook 产物只有 5 个 `.umap`
（`Mine_01`、`OpenDriveMap`、`TestMaps/EmptyMap`、`Town10HD_Opt`、
`Town15/Town15`，全部来自 `DefaultGame.ini` 的 `MapsToCook`），
`Town01_Opt`–`Town07_Opt`、`Town_C` 以及全部
`Content/Carla/Maps/Sublevels/**` 都未 cook。与旧的 `-cookall` 产物逐文件对比：
9.40 cook 为 93,374 个文件，是旧 94,530 个文件产物的严格子集
（少 1,156 个、多 0 个），其中包括
`Plugins/Carla/Content/PostProcessingMaterials` 的 `LensDistortion`、
`AnnotationColor`、`DepthEffectMaterial` 等 sensor 材质。

影响范围需要说清楚。9.35/9.39 的 stage 来源是那一次旧的 `-cookall` cook
（`NwMhgV`），内容完整，所以这两节的 runtime 结论本身仍然成立；把 runtime
client 换成缺地图版本的是 9-25 之后用 `-COOKDIR` 重新 stage 的两次
（`vI4bTP`：产物 12,565 个文件、stage 11,800 个文件；`8nNCyN`：产物 93,521
个文件、stage 92,649 个文件）。修复后的 `-cookall` cook（`z2Sdhk`）为 94,104
个文件，同样是旧产物的子集（少 426 个、多 0 个），且这 426 个已逐项核对为
有意排除：210 个 `/Game/Carla/HoudiniEngine`、120 个
`/CarlaTools` 插件内容（含 4 个 CarlaTools 自带 `.umap`）、14 个
`BP_multipleLights`/`BP_Signs`/`TrafficLights2025`/`PedestrianOnSidecar`
`DirectoriesToNeverCook` 条目，以及 82 个 `.ubulk` 派生数据（76 个位于
`Content/Carla/Static` 下、其所属 `.uasset/.uexp` 在修复产物中均存在，
6 个为 `Engine/Content/EngineMaterials/*_VT.ubulk`）。修复产物包含全部
330 个源地图 `.umap`、87 个 streaming sublevel 与 32 项 Carla sensor
材质。

该缺陷在 runtime 已暴露，并且有两种不同的失败模式。需要先澄清对应关系：
9.40 的 cook（`56QjA2`）本身从未 stage、也从未跑过 runtime gate，它只声明了
Cook 通过；真正被 stage 过的是同一天更早一次同 `-COOKDIR` 作用域的
`vI4bTP`（`cooked-client-stage-20260925T003858Z-T7l5d4`，仅 11,800 个文件），
它的 client 在两种地图上都以不同方式失败。

其一是地图完全不存在：该 client 在 `Town01_Opt` 上连 episode 都起不来，
`lavapipe-sensor-gate-20260925T003921Z-lWGzzJ` 的 UE log 报
`LogStreaming: Error: Couldn't find file for package /Game/Carla/Maps/Town01_Opt`
（Found 0 dependent packages），随后 `UGameInstance::StartGameInstance.Cancelled`
正常退出，全程没有 `New episode` 标记，wrapper 在 startup gate 以 exit 3 失败。
其二是地图能加载但内容不完整：`Town10HD_Opt` 在 `MapsToCook` 内，episode
起来后 client 立即自杀，`lavapipe-sensor-gate-20260925T004551Z-7HUy2x` 的 gate
**FAIL**（`endpoint=FAIL`、`world=FAIL`、其余 check `MISSING`），client log 为
`world: RuntimeError: std::exception`，UE log 出现
`LogCarla: Error: Actor Name: Actor_29 has no SM assigned to the ISM`
并紧接 `FUnixPlatformMisc::RequestExitWithStatus`。两种表现都不再出现在本节
修复后的 `-cookall` client 上。

结论是：9.40 的 Cook PASS 不能当作 runtime 证据（它连 stage 都没做过，且作用域
本身漏掉全部地图与插件内容），而当时真正在跑的 staged client 是失败的；9.39 的
soak 证据来自此前那次旧的 `-cookall` stage，因此 9.39 的 runtime 结论仍然有效，
但 9.40 的“full Cook”表述作废，由本节取代。

修复分三部分。`scripts/carla/probe-arm64-full-cook.sh` 改用真实的工程级
`-cookall` 作用域，并显式追加 `-MAP=` 请求：map 集合由
`Content/Carla/Maps/OpenDrive/*.xodr` 推导出的同名地图并上
`DefaultGame.ini` 的 `MapsToCook` 合并去重得到（13 个），因为传入 `-MAP`
会替换而非扩展 ini 的 `MapsToCook`；`-NEVERCOOKDIR`/`DirectoriesToNeverCook`
仍然排除 editor-only 资产。gate 新增三条覆盖断言，使“静默丢地图”不可能再
PASS：每个请求的 map 必须出现在产物中、源树每个 `Maps/Sublevels/**/*.umap`
必须产出、每个 `Plugins/Carla/Content/PostProcessingMaterials/*.uasset`
必须产出，并把 map 清单写入 `cook-maps.txt`/`cooked-maps.txt`。

第二部分修正 cook 与 runtime 的 shader 作用域不一致。9.40 的 cook 关闭
`r.VirtualTextures`，而 client 启动参数没有关闭它；`r.VirtualTextures` 与
`r.RayTracing` 是 `ECVF_ReadOnly`，只能在 engine init 从 ini 读取，因此 cooked
client 仍按 VT 开启去取 shader map，会在地图加载完成后于
`MaterialShared.cpp:2973` SIGSEGV：
`Failed to find shader map for default material DefaultDeferredDecalMaterial`
（`Incomplete material ... missing (TVirtualTextureVSBaseColorNormalRoughness, 0)`）。
新增 `scripts/carla/arm64-renderer-scope.sh` 作为两侧共用的唯一作用域定义，
`probe-arm64-full-cook.sh` 与 `run-carla-lavapipe-sensors.sh` 都 source 它，
避免任一侧单独增删 feature。注意 `-cookall` 且保持 VT 开启会产生 39 个
`LogMaterial: Error: ... requires non-virtual texture`，因此采用“两侧同时关闭
VT”这一与旧证据一致的方案；这属于 Lavapipe/ARM64 的渲染 workaround，不改变
CARLA 功能。

第三部分修两个 wrapper 缺陷。其一，
`${client_command@Q}` 不带下标时只展开第 0 个元素，导致历次 gate 归档的
`client-command.json` 内容都是 `"timeout"`，不含真实启动参数；现改为把数组
经 argv 传给 JSON writer。其二，wrapper 默认地图是 `Town10HD_Opt`，而该地图在
本 cooked client 上从未通过：episode 启动约 0.5 秒后 client 自行退出，RPC 握手
成功但 `get_world()` 失败；`rpc` 与 `actors` 两种模式复现一致
（`runtime-rpc-20260925T062733Z-QcaIGF`、
`runtime-actors-20260925T062904Z-e6orlr`，均为 `handshake=PASS`、`world=FAIL`、
`errors=["world: RuntimeError: std::exception"]`，UE log 无 Fatal/SIGSEGV），
早先的 30 tick gate `lavapipe-sensor-gate-20260925T004551Z-7HUy2x` 症状相同。该退出不是
内容缺失造成的：`Town10HD_Opt` 未解析的可选引用
（`Sm_ConstructionDebrie`、
`/Volumetrics/.../M_VolumetricCloud_03_Profiles_Billowy`）是 `Town01_Opt` 那一份
集合的子集，而 `Town01_Opt` 在同样的缺失下可以正常跑完 gate。默认值因此改为
已有证据的 `Town01_Opt`；`Town10HD_Opt` 记为未解决的残留缺陷，需要继续定位，
不能视为已支持地图。

修复后的正式证据（client binary 与 9.39 相同，
SHA256 `6acec2a3a43076897d74d44da084a70a45e9fd4f891d9dca66052663a1027847`；
CARLA `b56ce1a3`、UE `525b5451`）：

- Cook **PASS**：`/artifacts/carla/full-cook-20260925T050033Z-z2Sdhk`，
  step `complete`，exit 0，target `LinuxArm64Client`，rendering cook 1，
  Lavapipe ICD；44,142 packages cooked / 88 skipped by platform / total 44,230，
  `Success - 0 error(s), 39380 warning(s)`；94,104 files，50,588,385,813 bytes；
  产物含 330 个 `.umap`（等于源树全部地图）与 87 个 streaming sublevel 地图、
  32 项 Carla sensor 材质；`cook.log` SHA256
  `fa784ec06472c961e16abadf950510d368fde89e9279dbf47c02f42ceb5c08b3`，
  `output-files.txt` SHA256
  `ae1305d0d788af265b642a7b13c7fbc96620f2b2c2aefaabaa7dc3b05712a71d`
- Stage：`/artifacts/carla/cooked-client-stage-20260925T053037Z-49AALd`，
  93,175 files，50,662,966,810 bytes
- 6000 tick soak 三模式均 **PASS**（exit 0，`Town01_Opt`）：
  - RPC：`runtime-rpc-20260925T064010Z-Xctgq5`（gate
    `lavapipe-sensor-gate-20260925T063744Z-Wqunz3`），result SHA256
    `fceba982c0f7e17526a8505cf6d57591ecb9cc339a64d89f56227cb7cbc1b5c9`，
    frames `14..6013`
  - Sensors：`runtime-sensors-20260925T064316Z-7IHCrE`（gate
    `lavapipe-sensor-gate-20260925T064248Z-dsi2Va`），result SHA256
    `2661fa5187ed3aaf383048571c2e1034cd265629832c516c9c887c04055790d6`，
    12/12 checks PASS，6,000 个 RGB/LiDAR 对齐样本，frames `25..6024`，
    RGB 全部 320x240/307200 bytes 且 6000 个不同 hash，LiDAR 每帧
    1050–1068 points、4631 个不同 payload hash
  - Actors：`runtime-actors-20260925T064153Z-gjUZVg`（gate
    `lavapipe-sensor-gate-20260925T064054Z-e0aBDk`），result SHA256
    `a3fefa3a7bd3ebfcfa3c00a74aaaae99eb43dcc8f160d46a5fb815101fcde677`，
    11/11 checks PASS，frames `18..6017`，Traffic Manager vehicle 位移
    `72.094851m`、最大速度 `24.199714m/s`，AI walker 位移 `71.756537m`
    （`get_velocity()` 仍恒为 0，判定沿用官方 smoke test 的位移语义）
  - 三者 vehicle/walker 位移与最大速度与 9.39 在 6 位小数内一致，说明完整
    地图内容改变了资产覆盖但没有改变仿真动力学结果
- 默认一键入口复验：`make carla-lavapipe-sensors`（无任何覆盖变量，
  `CARLA_RUNTIME_TICKS=20`）**PASS**，gate
  `lavapipe-sensor-gate-20260925T144115Z-H8wxja`，invocation
  `runtime-sensors-20260925T145000Z-vJ4yer`，且 `client-command.json` 现在
  完整记录 30 个启动参数（含共用的 `[SystemSettings]` 作用域）
- `make test-local` **PASS**：549 tests、56 skipped、0 failures

结论更新：ARM64 cooked client 的 Cook gate 现在覆盖真实地图、streaming
sublevel 与 Carla sensor 材质，并在此基础上重跑通过 RGB/LiDAR、
Traffic Manager/AI walker 与 6000 tick 长时 soak；9.40 的 PASS 作废并以本节
取代。该结果仍只证明 Lavapipe 软件渲染路径下的 Cook 与长时间稳定性，不证明
GB10/NVIDIA Vulkan 驱动兼容性、GPU 性能、`Town10HD_Opt` 地图可用性、
ROS/Autoware 集成，也不证明 39,380 个 warning 背后的资产视觉正确性。

### 9.42 2026-09-25 运行证据的残余错误边界与 Town10 根因收敛

继续复核 9.41 之后的原始日志，得到三条需要从 PASS 标签中分离出来的
边界。

第一，`Town10HD_Opt` 的失败不是“地图 `.umap` 没有 cook”。修复产物实际
包含 `Town10HD_Opt.umap`、对应 OpenDRIVE 和 `SM_DebrisContainer`；但 CARLA
运行配置 `Content/Carla/Config/PropParameters.json` 与
`Default.Package.json` 仍引用不存在的
`/Game/Carla/Static/Dynamic/Construction/Sm_ConstructionDebrie`。Town10
加载时因此报告 `Couldn't find file for package`，随后环境物体注册路径在
`Plugins/Carla/Source/Carla/Util/BoundingBoxCalculator.cpp:374-376`
对两个 ISM 打出 `has no SM assigned to the ISM`。该日志来自包围盒注册的
错误处理，不是 Cook 覆盖断言本身。已在 client/server stage 复制配置后加入
结构化 JSON 校验与迁移，把旧引用迁移到实际存在的 `SM_DebrisContainer`，
并要求该 Cook 包存在；接下来需要重新 stage 并跑三种 runtime gate。在新
stage 证据产生前，`Town10HD_Opt` 仍是 **BLOCKED**。

第二，Town01 的 6000 tick RGB/LiDAR endpoint 检查确实通过，且有 6000 个
不同 RGB hash 和完整 LiDAR payload；但同一 client log 仍出现 UE Vulkan
`VulkanTexture.cpp:2515` 的 handled ensure：
`Expected source texture ... TRANSFER_SRC_OPTIMAL`，实际布局为
`SHADER_READ_ONLY_OPTIMAL`。这说明数据面在该次 Lavapipe soak 中完成了，
不能把它升级为“无渲染错误”或“Vulkan 同步正确”。当前 gate 保留 endpoint
PASS，同时从后续运行开始在 `server-diagnostics.txt` 归档 missing-package、
ISM、ensure、layout、fatal 和退出标记，避免把日志残余静默丢失。

第三，Town01/Town10 都出现 `InputSettings.cpp` 的
`Invalid InputComponent class` / `Invalid PlayerInput class` handled ensure。
两张地图共享该配置，因此它不是 Town10 单独退出的充分根因；但说明
`/Script/EnhancedInput.*` 在 cooked client 的运行时类解析仍未形成干净证据。
这属于下一轮 staging/plugin 依赖检查项，不能用当前 RPC 或传感器 endpoint
通过覆盖。

因此当前状态是：ARM64 full Cook 的地图、streaming sublevel 和 sensor
material 覆盖断言已加强；Town01 在 Lavapipe 下的数据面长时 gate 通过但有
明确的 UE ensure；Town10 的可用性仍阻塞于第三方配置引用和运行时类解析。
下一步顺序固定为：用新的 stage 迁移重跑 Town10 三模式；随后针对
`EnhancedInput` runtime module 和 Vulkan layout
ensure 做独立最小复现，不把两个问题混在同一个长时 soak 中。

### 9.43 2026-09-25 Town10 stage 迁移复验：旧引用消失，但 Lavapipe 仍阻塞

已用 `/artifacts/carla/full-cook-20260925T050033Z-z2Sdhk/cooked` 重新生成
client stage `cooked-client-stage-20260925T160310Z-kn9JNf`。stage 过程的
结构化 helper 报告 `runtime config normalized references=2`，并确认
`SM_DebrisContainer.uasset` 存在；stage 内两份配置现在都引用
`SM_DebrisContainer.SM_DebrisContainer`，不再引用
`Sm_ConstructionDebrie`。

随后用预装 Mesa/Lavapipe 镜像
`my-ad/carla-lavapipe:arm64-local` 重跑 `Town10HD_Opt` RPC smoke：

- 默认渲染 profile：`lavapipe-sensor-gate-20260925T160446Z-9qiVbd`。
  地图加载、OpenDRIVE、155 个 spawn points、CarlaServer 端口和
  `New episode` 都出现；旧 CARLA mesh 缺失不再出现。但 `InputSettings.cpp`
  仍有两个 invalid class ensure，之后 50 个 PSO hitch，最终在
  `RenderingThread.cpp:1150` 以 `GameThread timed out waiting for
  RenderThread after 60.00 secs` / SIGSEGV 失败。
- `no-pso` profile：关闭 `r.PSOPrecaching`、Vulkan PSO precaching、异步
  pipeline compile、Vulkan RHI thread，并把正确的
  `[ConsoleVariables]` `g.TimeoutForBlockOnRenderFence` 提到 300 秒。
  日志确认 `Vulkan PSO Precaching = 0` 和 CVar 300000 生效，但进程在
  world bring-up、输入类 ensure 后持续不产生 `New episode`，360 秒 startup
  gate 仍失败；因此关闭 PSO/RHI thread 也不是可接受 workaround。

当前根因边界进一步明确：Town10 的旧 `Sm_ConstructionDebrie` 配置错误已修复，
但 `Actor_29/30 has no SM assigned to the ISM` 不能再作为主因；真实阻塞是
Town10 在 ARM64/Lavapipe 的首帧渲染路径；`EnhancedInput` runtime
class ensure 仍未解决，但不能据此断言它造成了渲染等待。Vulkan layout
ensure 也仍是 Town01 的残余风险。Town10
三模式仍保持 **BLOCKED**，不能把 stage 迁移或 endpoint handshake 记为通过。

### 9.44 2026-09-26 Town10 首帧渲染等待的线程栈

用独立 Lavapipe 容器、已安装 GDB 和未修改的 cooked client，重放 9.43
归档的 35 个 `no-pso` 启动参数（含 300 秒渲染栅栏超时）。运行日志与三次
符号化线程栈保存在
`/artifacts/carla/town10-gdb-20260926-y3xrL4/`：
`server.log`、`threads-first.txt`、`threads-third.txt`。诊断未编辑
CARLA/UE 源码，也没有将 `no-pso` 设为默认。

- GameThread 在 `FEngineLoop::Tick` 的首帧
  `FlushRenderingCommands()` 等 `FRenderCommandFence`，对应
  `LaunchEngineLoop.cpp:5699` 和 `RenderingThread.cpp:1264`。
- 首次抓栈时 RenderThread 在 `FRDGMemcpyCS` 调用链内，
  `VulkanPipeline.cpp:2367` 的 `vkCreateComputePipelines()` 尚未返回；
  调用已进入 Mesa `libvulkan_lvp.so`。再次抓栈仍在同一调用点，
  此时不是 PSO *precache*，因为参数已将 `r.PSOPrecaching` 设为 0。
- 后续抓栈时 RenderThread **已推进**到
  `FVulkanView::InitAsTextureView()` 的 Mesa 调用，
  来源是 `FSlateTexture2DRHIRef::InitRHI()`。这排除了“同一个 compute
  pipeline 调用永久死锁”的断言，但没有证明所有渲染工作最终能完成。
- 进程保持 CPU 活跃，首次启动后超过 400 秒仍未到 `New episode`；
  外层 `timeout` 终止运行。原始 `server.log` 在 handled
  `InputSettings` ensure 中断行处停止输出，不能仅凭日志末行推断
  ensure 是等待原因。

因此当前精确结论是：Town10 的 Lavapipe client 在首帧渲染命令与软件
Vulkan 资源/管线创建路径上超过现有诊断时限；是否为长期性能问题或
驱动内部资源争用仍需单独验证。Town01 的 RPC/传感器 PASS 不迁移到 Town10，
也不能用延长 watchdog 宣称 Town10 已可用。配置迁移 helper 同时改为先解析
两份 JSON，再对结构化值做精确替换；第二份配置损坏时第一份不会被部分改写。

### 9.45 2026-09-26 Town10 渲染/数据面隔离与可执行 NullRHI gate

使用相同的完整 staged ARM64 client 与 `Town10HD_Opt`，将渲染后端作为
单一对照变量：

- NullRHI 对照 `town10-nullrhi-20260926-lVcaxS` 在约 7 秒内完成
  `New episode` 和 UE 初始化。CARLA Python 直接读取到服务端版本
  `0.10.0` 与 `Carla/Maps/Town10HD_Opt`。初次 20 tick evaluator 启动太晚，
  遇到预设 server timeout 后的 `Connection refused`；这不是 endpoint
  失败证据。随后在同一运行中协调服务端与 evaluator 的
  `town10-nullrhi-rpc-coordinated-20260926-Zkeuiq` 完成真实 RPC gate。
- 正式入口 `make carla-town10-nullrhi-rpc` **PASS**，证据
  `/artifacts/carla/town10-nullrhi-rpc-20260926T022255Z-wCrDZv`；
  `endpoint/stage-report.json` 为 handshake、world、sync-settings、ticks、
  cleanup 全部 PASS，errors 为空。其 SHA256 为
  `63142350010bcfa0b161125ba0d61a144120e7af53a3551010a1b4e61eee4c7f`。
  入口在一次性 ARM64 build 容器的独占端口 `20200` 上启动 cooked
  client，等待真实 `get_world()` 和地图名就绪后调用已有 evaluator，
  不依赖 UE stdout 中可能延迟刷出的 `New episode` 字符串。
  调整启动/执行总超时预算后的最终版本同样 **PASS**：
  `/artifacts/carla/town10-nullrhi-rpc-20260926T022619Z-xcvdRD`，
  `endpoint/stage-report.json` SHA256
  `6edee936898d4749bb6f0fa734ce5b3a9bc5b5c5046a8a952603f1f7f9680ff1`。
- gate 的 PASS **仅限**无渲染的 endpoint/20 tick；启动后主动结束
  server 的 stop code 为 143，不证明优雅退出。此前 85 秒 NullRHI
  timeout 的 teardown 还出现 `GPUMessaging.cpp:68` assertion，因此不将
  shutdown 或 RGB camera 记为通过。

Lavapipe 对照 `town10-lavapipe-shader-20260926-9CkctH` 使用相同 stage 与
`no-pso` scope，直接 RPC 取得服务端版本 `0.10.0`，但 `get_world()` 为
`RuntimeError: std::exception`；即使日志出现 `New episode` 也不能代替
客户端世界可用性。利用现有 UE
`-CarlaVulkanShaderDiagnostics=` 捕获的 45 个计算 shader SPIR-V 均通过
`spirv-val --target-env vulkan1.3`，证据为 `spirv-validation.txt`
（SHA256 `015d3217423d3cf9ac21ddb41adad05ffa12fbf2452c08a07ff14d11f8da6755`）。
这只排除了静态校验能识别的 SPIR-V 错误，不能证明 Vulkan pipeline 创建和
运行时正确。`town10-lp4-20260926-G9TWY4` 将 Lavapipe worker 限制为
4 个，并复用生成的 Mesa shader cache；冷/热两次均 handshake 成功、
`get_world()` 失败。不能把 LP=4、磁盘缓存或延长 watchdog 设为默认修复。

本地回归：`make test-local` 561 tests、56 skipped、0 failures；
`git diff --check`、脚本语法与 change-ledger JSON 校验均通过。

目前可用的组合是：`Town10HD_Opt` 仅用于 NullRHI RPC/同步仿真验证，
`Town01_Opt` 继续承载既有 Lavapipe RGB/LiDAR/actors gate。
Town10 的渲染及传感器、GB10/NVIDIA Vulkan、优雅退出仍 **BLOCKED**。
定位方向已缩到 Town10 首帧资源与管线创建/软件 Vulkan 路径；下一步应对
捕获的具体计算 shader 做独立 Vulkan pipeline 最小复现，并与目标 GB10
驱动对照，而不是改写地图、忽略 ensure 或无界延长启动超时。

### 9.46 2026-09-26 独立 Vulkan compute pipeline replay 工具落地

新增 `scripts/carla/vulkan-compute-replay.c` 与
`probe-vulkan-compute-replay.sh`，按捕获 SPIR-V 的 entry point 使用
仓库内 UE fork 的 SPIRV-Reflect 推导 descriptor set、binding、descriptor
type 和 push-constant 基线，然后独立调用 `vkCreateComputePipelines`。
`smoke` 模式额外执行一个固定的 storage-buffer dispatch/readback；报告明确
标记 `ue_exact_replay=false`，因为它还没有复用 UE 的
`FVulkanDescriptorSetsLayoutInfo`、device feature chain、pipeline flags、
shader module patch 和 shader header。

实际验证：

- 固定 `vulkan-compute-smoke.comp` 在 `my-ad/carla-lavapipe:arm64-local`
  上完成 pipeline create、dispatch 和 64-word host readback；
- Town10 捕获的
  `0648BF4DE391D39C0F80B1F5916D360A013038C4.spv` 在同一 Lavapipe 上完成
  SPIRV-Reflect descriptor layout 和 pipeline create；
- 在现有 ARM64 build session 中正式运行
  `probe-vulkan-compute-replay.sh`：fixture smoke 证据目录
  `/artifacts/carla/vulkan-compute-replay-20260926T030018Z-lx4CW1`，
  Town10 捕获 shader create 证据目录
  `/artifacts/carla/vulkan-compute-replay-20260926T030341Z-QsQZnP`；
- 45 个捕获 compute SPIR-V 全部通过
  `spirv-val --target-env vulkan1.3`，但这只证明静态模块有效，不证明
  Lavapipe/GB10 的运行时管线创建或 UE 兼容；
- 45 个捕获 shader 的有界批量独立 pipeline create 全部通过：
  - Lavapipe：`/artifacts/carla/vulkan-compute-replay-20260926T060617Z-zYl5Bq`
  - NVIDIA GB10：`/artifacts/carla/vulkan-compute-replay-20260926T061052Z-xyqPQc`
    （batch-result SHA256
    `43d0bbd3e3c64372026cbb1d06a9091824ccc25d9ba815a7cac4a3e367fc2c3`）
  这排除了“某一条捕获 shader 只在单一后端创建失败”的简单根因，但仍不
  复现 UE 的共享 device、pipeline cache、shader module lifetime 和首帧顺序；
- `make carla-vulkan-compute` 是固定 fixture 的软件 smoke；
  `make carla-vulkan-compute-gpu` 是 GB10 上的 `create` 入口，后者尚未在
  当前会话运行。当前旧的本地 build image 在正式 Make 入口第一次运行时
  缺少新依赖，已在 Dockerfile 补上 `glslang-tools`；直接在已有 ARM64
  session 安装依赖后执行同一 probe，Lavapipe smoke PASS。

随后在带 GPU 能力的 compose 容器中临时安装 `spirv-tools`/
`glslang-tools`，对同一 shader 和 UE layout baseline 运行 GB10 create：
`/artifacts/carla/vulkan-compute-replay-20260926T043248Z-e95Uj0` **PASS**。
设备为 `NVIDIA GB10`，Vulkan API `1.3`，pipeline create 约 `11.6ms`。

因此下一步不是立即修改 UE Vulkan 代码，而是把工具扩展为 UE-exact replay：
从 `FVulkanShaderHeader`/`FVulkanDescriptorSetsLayoutInfo` 归档真实 layout、
device feature/extension 链和 pipeline flags，再对同一 shader 做逐字段对照。
只有独立 replay 能复现失败，才进入 Mesa/NVIDIA 驱动或 SPIR-V workaround；
只有独立 replay 通过而 UE 仍失败，才修改 UE 的资源生命周期或首帧调用链。

### 9.47 2026-09-26 首条 UE compute pipeline 的真实 layout baseline

在同一 Town10 staged client 上使用 ARM64 GDB 断点
`FVulkanPipelineStateCacheManager::CreateComputePipelineFromShader`，断点移到
`ProcessBindingsForStage` 和 `FinalizeBindings` 之后，读取了 UE 实际内存中的
布局对象。证据目录：
`/artifacts/carla/town10-ue-layout-gdb-20260926/`。

首条命中的 compute pipeline baseline：

- `Shader->bUsesBindless=false`
- `DescriptorSetLayoutInfo.SetLayouts.ArrayNum=1`
- set 0 有 2 个 binding：
  - binding 0：`VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC`、
    `descriptorCount=1`、`stageFlags=VK_SHADER_STAGE_COMPUTE_BIT`
  - binding 1：`VK_DESCRIPTOR_TYPE_STORAGE_TEXEL_BUFFER`、
    `descriptorCount=1`、`stageFlags=VK_SHADER_STAGE_COMPUTE_BIT`
- shader header：`PackedGlobalsSize=48`、
  `NumBoundUniformBuffers=1`、`NumBufferInfos=1`、`NumImageInfos=0`、
  `WaveSize=0`、`SpirvCRC=651553000`
- UE finalized set-layout hash：`3539589419`

这证明独立 replay 当前的 `SPIRV-Reflect` layout 还不是 UE exact layout；
独立工具捕获的 Town10 shader `0648...C4` 反射结果包含另一组
uniform/storage-image/sampled-image binding，不能拿来直接替代上面的 UE
shader header。

已将该 baseline 落入
`scripts/carla/vulkan-compute-town10-ue.layout`，replay 支持通过
`CARLA_COMPUTE_LAYOUT_FILE` 覆盖反射布局。用同一条 UE 首条 compute shader
`main_00000628_26d5e8e8` 重跑时，独立 Lavapipe pipeline create **PASS**，
证据 `/artifacts/carla/vulkan-compute-replay-20260926T041740Z-8AIv45`；
报告标记 `layout_source=ue-layout-baseline`，但仍为
`ue_exact_replay=false`，因为 shader header 本身、device feature/extension
链、UE pipeline cache 和 module patch 尚未复用。

因此下一步只剩两种可证伪路径：把 UE shader header 序列化为 replay 输入，
或进一步复用 UE 的 feature/extension chain、pipeline cache 和 module patch。
GB10 独立 create 已经通过，但在这些 UE 运行时细节复用前，不修改 UE Vulkan
主路径，不把 Town10 渲染升级为 PASS。

### 9.48 2026-09-26 UE device feature/extension 差分

在 `FVulkanDevice::CreateDevice` 的 `vkCreateDevice` 前断点读取到：

- UE 实际准备启用 **36 个 device extensions**；
- 核心 1.0 feature 中 `shaderInt64`、`geometryShader`、
  `samplerAnisotropy`、`shaderImageGatherExtended` 均为 enabled；
- UE 查询到的 Vulkan 1.1/1.2/1.3 feature 结构包含 descriptor indexing、
  buffer device address、timeline semaphore、synchronization2、
  dynamic rendering、subgroup size control 等支持项；
- UE 的 extension-specific pNext 规则在 `PreCreateDevice` 中选择性拼接
  feature structs。优化构建下直接用 GDB 自动遍历 pNext 会重复同一节点，
  因此没有把不可靠的完整链列表写入 replay。

这解释了为什么当前独立 replay 的
`feature_policy=all_supported_core_1_0_to_1_3` 不能称作 UE exact：
它启用的是“设备支持的核心 feature 集”，而不是 UE 经过 extension policy、
平台限制和 CVar 筛选后的最终 `VkDeviceCreateInfo`。下一步应在 UE 侧增加
仅诊断用的结构化 `device-create.json`，记录 extension 名称、pNext `sType`
和非零 feature 字段，再让 replay 可选读取该快照；在此之前不修改设备
feature policy，也不把独立 pipeline PASS 升级为完整渲染 PASS。

实际诊断入口为 `make carla-ue-vulkan-device-state`。现有 ARM64 session
已生成快照：
`/artifacts/carla/ue-vulkan-device-state-20260926T101317Z-KwNY2x/device-create.json`。
快照记录 27 个当前 Lavapipe UE device extensions、核心 feature 字段和
14 个可读 pNext `sType` 节点；queue create info 在优化构建的局部变量
生命周期下未可靠读出，因此没有伪造 queue 结论。`gdb` 已加入 ARM64
镜像依赖，后续可在干净 build image 重复采集。

### 9.49 2026-09-26 实际设备配置捕获与双后端重放

9.48 的 schema 1 快照不再作为完整捕获或重放输入：它缺少实际 queue
信息、各 pNext 节点的布尔值，且 `core_1_2/1_3` 是支持查询结果，
不是最终 enable policy。新的捕获在 loader 的 `vkCreateInstance` 和
`vkCreateDevice` API 入口读取 ARM64 参数寄存器，通过 GDB 的 Vulkan
结构体类型解引用；不再依赖优化后的 UE 局部变量或猜测字段偏移。
在 device API 入口停止并退出自有 inferior，不继续启动地图。

新增 `vulkan_device_snapshot.py` 严格校验 schema 2：必须包含全部 55 个
核心 feature、完整的已知类型 pNext 布尔值、队列数量与优先级，以及
instance/device extension 清单。重复 JSON 字段、未知节点、缺失值、
非布尔 feature、非法优先级或未终止链均拒绝，不生成可用重放配置。
schema 中的类型、布尔字段和 sType 已逐项对照 UE Vulkan 1.3.290 头文件。

独立 probe 支持 `CARLA_COMPUTE_DEVICE_STATE` /
`COMPUTE_DEVICE_STATE`，从归档快照生成有类型的 C 配置，保留实际
enable/disable 值、extension 顺序、pNext 顺序与队列配置；目标不支持
任一必要扩展或启用项时明确失败，不静默删减。输入、生成头文件、
编译命令和 shader 在运行前归档；fixture 改为每次运行独立的文件，
避免并发覆盖。报告记录快照 SHA256 与
`feature_policy=captured_vkCreateDevice`。

实际证据（目录均在 `/artifacts/carla/`）：

| 后端 | 完整设备快照 | fixture 派发/64-word 读回 | 匹配 UE shader/layout create |
| --- | --- | --- | --- |
| Mesa 23.2.1 / LLVM 15 | `ue-vulkan-device-state-20260926T112849Z-d4ztqb`，27 扩展、14 节点、1 队列组 | `vulkan-compute-replay-20260926T113244Z-euQTSZ` PASS | `vulkan-compute-replay-20260926T113308Z-opOhZu` PASS |
| Mesa 25.2.8 / LLVM 20 | `ue-vulkan-device-state-20260926T113938Z-nnZEkt`，36 扩展、21 节点、1 队列组 | `vulkan-compute-replay-20260926T114115Z-8LvAT0` PASS | `vulkan-compute-replay-20260926T114115Z-OLYv9j` PASS |
| NVIDIA GB10 / 580.173.02 | `ue-vulkan-device-state-20260926T115742Z-5RoDwN`，39 扩展、26 节点、2 队列组 | `vulkan-compute-replay-20260926T115918Z-unpiIi` PASS | `vulkan-compute-replay-20260926T115918Z-AnQTwV` PASS，约 14.2ms |

匹配 shader 仍为 `main_00000628_26d5e8e8`，set 0 binding 0 为
`UNIFORM_BUFFER_DYNAMIC`，binding 1 为 `STORAGE_TEXEL_BUFFER`。
这是已捕获的首条 compute shader，**尚未证明它就是卡住线程栈中的
`FRDGMemcpyCS`**，不能把二者等同。

拒绝路径也经过实际执行：

- schema 1 输入在生成头文件前退出 2：
  `vulkan-compute-replay-20260926T113910Z-z8kMcJ`。
- Mesa 25 快照用于 GB10 时缺少 `VK_EXT_memory_priority`，明确失败：
  `vulkan-compute-replay-20260926T114753Z-bgjXgQ`。
- GB10 快照用于 Mesa 25 时缺少 fragment shading rate、device fault、
  barycentric、NV compute derivatives 扩展，明确失败：
  `vulkan-compute-replay-20260926T115946Z-9xQP2M`。

因此跨后端不能直接套用另一后端的完整 enable policy。另已修正能力
比较器：空输入拒绝，旧快照和未核验的高级 feature 报
`INCOMPLETE`，不再默认为 PASS；该比较器仍不校验实际 queue 创建，
不是完整 Vulkan replay。

重复入口为 `make carla-ue-vulkan-device-state`（软件）和
`make carla-ue-vulkan-device-state-gpu`（GPU，显式 ptrace 权限）；
重放使用已有两个 compute Make 入口并传入 `COMPUTE_DEVICE_STATE`。
本轮实际运行使用现有 ARM64 session，以及临时 Ubuntu 24.04/Mesa 25
诊断容器，后者临时安装 GDB、编译工具与 GLVND 依赖。容器首次 GPU
对照的失败源于缺失驱动加载依赖，补齐后才得到上述结果；不把临时
安装依赖的运行算作干净镜像验收，镜像重建仍待完成。

本地回归：`make test-local` 583 tests、56 skipped、0 failures；
probe 的 C 编译启用 `-Wall -Wextra -Werror`。所有新运行仍报告
`ue_exact_replay=false`：未复现 UE pipeline cache、shader module patch、
资源绑定与共享设备生命周期，也没有运行 Town10 渲染/传感器流程。
这仅证明上述匹配 shader 在各自实际设备配置下仍可独立创建，不是
对所有 compute 管线或 UE 首帧路径的排除结论。

下一步聚焦真正卡住的后续 compute 管线：捕获其 shader/module、
descriptor layout、pipeline flags 与 cache 上下文，再做对应重放与
共享设备对照。Town10 Vulkan world readiness、RGB/LiDAR 和 GB10
完整 CARLA 运行继续保持 **BLOCKED**；未修改 UE Vulkan 主路径。

### 9.50 2026-09-26 目标 compute 身份、缓存上下文与有界观察

新增 `make carla-ue-vulkan-pipeline`，默认目标 `FRDGMemcpyCS`。
在 `FVulkanDevice::SetupFormats` 后读取已经初始化的
`VulkanDynamicAPI` 函数指针，给实际调用地址设置断点；通过真实
Vulkan API 参数和返回句柄关联 shader module、descriptor set layout、
pipeline layout、pipeline cache 与 compute 创建调用。
SPIR-V 来自 `vkCreateShaderModule.pCode`，不是猜测的 UE 原始缓存文件。
对象数、blob 字节数、调用数、线程抽样数量和总观察时间均有上限。
捕获仅对自有 inferior 生效，退出后终止该进程。

首先纠正 9.47/9.49 的身份边界：`main_00000628_26d5e8e8` 的真实
调用栈是 `ClearUAVShader_T`，不是 `FRDGMemcpyCS`。Mesa 23 的
`ue-vulkan-pipeline-20260926T124857Z-qH3EC8` 记录了该对应关系，但未在
时限内命中默认目标，因此不作为目标捕获 PASS。Mesa 23 的后续运行
`ue-vulkan-pipeline-20260926T131222Z-2baH30` 也未命中指定 TSR 目标，
保留为失败诊断；结束时有多个 compute/graphics 创建调用仍在等待。
不能将 Mesa 23 的调用顺序或观察结果代替 Mesa 25。

在 Mesa 25.2.8 / LLVM 20.1.2 中获得有效目标捕获：
`/artifacts/carla/ue-vulkan-pipeline-20260926T132317Z-iBrJwK`。

- 调用栈明确包含 `PrepareDispatch<FRDGMemcpyCS>`。
- entry：`main_00000654_f5142477`，实际 module 为 1620 字节；
  SHA256：
  `dd368eb6dd97a96c2fe0d4976ee67c1f1697ccee72539a6cd5cd4c49e1fa3e84`。
- set 0 的三个 binding，count 均为 1、stage 均为 compute：
  binding 0 `UNIFORM_BUFFER_DYNAMIC`；
  binding 1/2 `STORAGE_BUFFER`。layout flags、pipeline flags、
  stage flags、push constants 和 required subgroup size 均为 0/空。
- 同一个 pipeline cache 的创建输入为 **0 字节**，flags 为 0；
  到达目标前，该 cache 已收到 **156 次 graphics 创建调用**。
  此次共有 41 条 compute 调用记录，目标前 40 条都观察到返回成功。
  因此初始空 cache **不等于**目标调用时已经变化的 cache 内容。
- 实际 module 字节与 9.45 的
  `CE81D3A4EF1457C0217A606C7F3552BA246F12CA.spv` 完全一致。
  9.44 的历史线程栈没有记录 shader hash，不据此断言其唯一 permutation。

同一目标的独立匹配重放：
`/artifacts/carla/vulkan-compute-matched-20260926T133140Z-tv0w3aom`，
**PASS**，compute create 约 **0.178ms**。使用实际提交的 module、
上述 layout、同次捕获的 schema 2 设备配置以及初始空 cache。
`pipeline_cache_policy=captured_initial_data`，
`device_configuration_reused=true`，仍为 `ue_exact_replay=false`。
重放没有 dispatch FRDG memcpy，也没有重建此前 156 次 graphics 和
40 次 compute 对 cache、资源或共享设备的影响。

另一次 `observe` 运行：
`/artifacts/carla/ue-vulkan-pipeline-20260926T133944Z-Hx5Z8D`。
在 **180 秒总观察窗口**结束时，目标输入已完整捕获，但
`driver_return_observed=false`、`result=null`；RenderThread 仍在
目标 compute 的 Mesa 调用内，GameThread 在 `FlushRenderingCommands`
等待。此运行不构成驱动创建成功，也不能证明永久死锁。
归档最初的 PASS 仅指输入捕获；现已将 `observe` 门禁加强为必须观察到
`VK_SUCCESS`，否则退出 3。同一归档用 `--require-driver-return`
复验确实退出 3，并保留可用的输入。`entry` 模式仍只验证输入捕获。
GDB 断点会影响调度，观测耗时不是纯驱动性能基准。

缓存重放增加 `COMPUTE_CACHE_FILE` / `COMPUTE_CACHE_FLAGS`。
非空 cache 先检查标准 Vulkan header、vendor/device ID 和 UUID，
不让驱动静默忽略不匹配的数据。fixture 的非空 cache-handle/空初始数据
smoke 为 `vulkan-compute-replay-20260926T125715Z-JMvdzD` PASS；
把 SPIR-V 错当 cache 的负例
`vulkan-compute-replay-20260926T130616Z-U2nDQI` 正确失败。
`replay_captured_vulkan_pipeline.py` 校验编译归档与目标的 module、
layout、device snapshot、cache 初始化输入一致后，复制到独立目录执行，
旧失败证据不被覆盖。

本轮编译仍在现有 ARM64 build session 中以 `-Wall -Wextra -Werror`
完成。该 session 的 Mesa 23 正确拒绝 Mesa 25 的扩展配置，
`vulkan-compute-replay-20260926T132826Z-bHpkxm` 保留为失败；
随后仅复用归档的 ARM64 程序在匹配的 Mesa 25 容器运行。临时容器的
包下载超时后，用本机原生 ARM64 GDB 15.1/Python 工具副本及缺失依赖
继续诊断，工具哈希与环境已归档；不作为干净镜像验收。
GDB 明确抑制 UE handled-ensure 的 SIGTRAP 以到达渲染路径，
不会把这种调试条件迁移为产品默认设置。

本地回归：`make test-local` 602 tests、56 skipped、0 failures；
新增测试涵盖句柄关联、目标栈、blob 哈希、路径/类型限制、缓存匹配、
有界返回观察、负例保留和观察成功门禁。Vulkan 运行中的自定义 allocation
callbacks、最终 cache 内容及资源生命周期仍未在独立程序中复现。

当前结论是：匹配的目标 shader/layout/设备配置/初始 cache 可以独立
创建，而完整 UE 上下文内的同族调用仍可超过观察窗口。下一步应检查
共享 cache 的变化、创建并发、allocation callbacks 与资源上下文，
不能直接修改 shader 或删减设备功能，也不能将缓存或并发单独宣称为根因。
Town10 world readiness、RGB/LiDAR、GB10 完整运行继续 **BLOCKED**。

### 9.51 2026-09-26 分配回调记录与目标缓存单变量对照

捕获扩展为读取实际 `VkAllocationCallbacks`：记录 NULL 状态、
`pUserData`、五个函数指针及可解析的符号名，并将 module、layout、
cache 和目标 compute 的创建参数关联起来。UE 源码支持受锁保护的
自定义分配器，并不代表当前二进制实际使用它；本轮不从编译宏猜测。

新增显式、默认关闭的 `UE_PIPELINE_INTERVENTION` /
`CARLA_UE_PIPELINE_INTERVENTION`：

- `none`：不改参数，是基线。
- `allocator-null`：仅在目标 API 入口将 `x4` 置零。
- `cache-null`：仅在目标 API 入口将 `x1` 置零。

干预只能用于 `observe` 模式。创建返回后立刻停止，GDB 退出时终止
自有 inferior，不继续执行 UE 的对象管理或销毁路径，也不写 UE 源码、
配置或默认渲染策略。寄存器赋值后检查读回值；没有实际变化的 allocator
NULL 对照标记为 no-op。归档同时保留 original/submitted 参数。
默认 validator 和 matched replay 拒绝被干预的数据；
只有显式 `--allow-intervention` 才能校验诊断输入。

Mesa 25.2.8 / LLVM 20 的实际对照：

| 对照 | 证据目录（`/artifacts/carla/`） | 结果 |
| --- | --- | --- |
| 基线 `none` | `ue-vulkan-pipeline-20260926T141626Z-nFV3l2` | 输入捕获有效；180 秒总窗口结束时目标未观察到返回，driver-return 门禁退出 3 |
| 仅目标 `cache-null` | `ue-vulkan-pipeline-20260926T142133Z-ecFI5F` | original cache 非零、submitted cache 为 `0x0`，干预已记录；同一窗口内仍未观察到返回，退出 3 |

两个对照的目标均为 `main_00000654_f5142477`，
实际 module SHA256 仍为
`dd368eb6dd97a96c2fe0d4976ee67c1f1697ccee72539a6cd5cd4c49e1fa3e84`，
1620 字节、相同三项 binding、pipeline/stage flags 均为 0。
目标前同一原始 cache 的 graphics 调用计数都是 156。
所记录的目标 compute、module 和 cache 分配器实际都为 **NULL**，
与独立程序一致。因此没有运行无效的 `allocator-null` 实验，也没有
把 UE 自定义分配器的源码实现当作本次等待的证据。

cache-null 对照保留其他目标参数，仍出现 RenderThread 在目标 Mesa
创建调用中等待、GameThread 在渲染栅栏等待。它不支持用“禁用目标
pipeline cache 参数”作为修复，但不能排除共享设备内的缓存状态、
先前 graphics/compute 工作、资源生命周期、调度或其他驱动内部状态。
此前的创建活动并未撤销；GDB 干预也不能作为产品行为或性能基准。
仍不宣称永久死锁或唯一根因。

本轮使用一次性 Mesa 25 容器和临时复制的原生 ARM64 调试工具，
沿用 9.50 的工具/环境归档，不作为干净镜像验收。两份运行时证据不
覆盖旧归档。源码之外的 shader/device policy 未修改。
本地回归：`make test-local` 609 tests、56 skipped、0 failures；
新增测试覆盖回调结构完整性、单寄存器干预、读回失败、no-op、默认关闭、
observe-only 和干预数据不能误作未修改输入等边界。

下一步重点是重建共享 VkDevice 的先前工作和资源状态，并与原生 GPU
路径对照。不能根据本轮负例升级 Town10 world、RGB/LiDAR 或 GB10
完整运行状态；这些验收继续 **BLOCKED**。

### 9.52 2026-09-26 UTC / 2026-09-27 本地 原生 GB10 世界就绪对照

新增 `make carla-town10-gb10-runtime`，默认 `RUNTIME_MODE=rpc`。
该入口在 GPU 容器内强制 NVIDIA ICD，校验 GB10 设备名、staged server 的
ELF64/AArch64 架构、运行进程的 executable 路径与 NVIDIA driver 映射，
并复用 `check_carla_runtime.py` 的同步 tick、sensors 和 actors 门禁。
启动条件由 `wait_carla_world.py` 读取真实版本、目标地图和世界快照，
不再把 `New episode` 日志或版本握手当作世界就绪。RPC retry 有总时限，
进程退出即停止；wrapper 检查三个独占端口，归档命令/输入哈希，
结束时仅终止自有 server process group。服务器退出、清洁镜像、性能、
完整视觉正确性和 ROS/Autoware 集成不在该入口的通过范围。

在原生 ARM64 toolchain GPU 容器、NVIDIA GB10 / 580.173.02 上实际运行：

| Render profile | 证据目录（`/artifacts/carla/`） | 结果 |
| --- | --- | --- |
| `default` | `town10-gb10-rpc-20260926T153908Z-SbsH1y` | 世界就绪失败，server SIGSEGV |
| `no-pso` | `town10-gb10-rpc-20260926T154853Z-KgP5SW` | 关闭 PSO precache、async compile 和 Vulkan RHI thread 后仍失败 |
| `serial-translate` | `town10-gb10-rpc-20260926T161706Z-E2OvVX` | 相对 no-pso 仅加 `r.RHICmd.ParallelTranslate.Enable=0`，仍失败 |

未调试的三次运行都加载 Town10 并在首帧附近退出。日志中的 crash 地址
为 `0x8`，栈顶位于 `libnvidia-glvkspirv.so.580.173.02`，下方有
`libnvidia-eglcore.so` 与 UE compute 创建调用。`vulkaninfo` 标识设备为
NVIDIA GB10，server 日志也确认该设备。serial-translate 的日志确认
对应 CVar 为 0。ready gate 未通过，因此没有运行同步 tick 或 sensors
门禁，不记录 RPC、RGB、LiDAR PASS。InputSettings 的类解析 ensure
仍存在，但不能仅凭它出现在 crash 前就认定因果。

这与 Lavapipe 的长时间等待不同：GB10 当前有可复现的原生运行时
SIGSEGV。独立 45 个 shader 创建通过并不能迁移为完整 UE 上下文通过。
也不能仅凭 crash 位于 NVIDIA 编译库就断言唯一驱动 bug；
提交输入、对象状态与生命周期仍需验证。

捕获增加 `UE_PIPELINE_MODE=crash`：只在 faulting thread 存在尚未返回的
compute 记录时选择其输入，记录 signal/thread，并仍要求 driver-return
成功才能通过。没有对应调用则不填充 target，不拿别的 shader 补缺口。

实际 GDB 对照
`/artifacts/carla/ue-vulkan-pipeline-20260926T160419Z-OUyULF`
没有复现同一 NVIDIA compute 编译失败：其 50 条 compute 记录均观察到
返回，随后在 UE graphics uniform-buffer 绑定路径出现：

- `VulkanCommands.cpp:532`：当前 shader key 与 pending graphics state
  中的 shader key 不一致；
- `VulkanPipelineState.h:121`：descriptor set/binding 越界；
- RenderThread 的最终 SIGSEGV 位于
  `FVulkanCommonPipelineDescriptorState::GetDescriptorType`。

捕获因此保持 `capture_complete=false`、`target_call=null` 并退出 2。
这份诊断保留实际线程栈，不作为某个 compute shader 的失败重放输入。
GDB 会改变调度，不能把调试中的这个失败与三次未调试的 NVIDIA crash
等同，更不能从单次调度差异证明并发是唯一根因。

三个 render profile 都是显式实验选项，默认仍为 default；
不把 no-pso 或 serial-translate 宣称为修复。现有 GPU image 临时复制了
同架构 GDB/SPIR-V 工具以完成诊断，不作为干净镜像验收，未修改 UE
渲染策略或 nested source。后续需在精确故障点检查 shader 身份、
pending state 与 descriptor layout 的一致性，并与未调试 crash 对照。

本地回归：`make test-local` 619 tests、56 skipped、0 failures；
新增测试包括真实世界就绪判定、仅握手不能通过、错误地图/进程退出、
暂态恢复、GPU profile 显式性，以及 crash 只能选中 faulting thread
的 pending compute、非 compute crash 不编造 target。`git diff --check`
及 shell syntax 通过。Town10 world、RGB/LiDAR、完整 GB10 CARLA 验收
继续 **BLOCKED**。

### 9.53 2026-09-27 首个 Vulkan 断言的真实绑定状态

按清单第 2 项，新增 `make carla-ue-vulkan-fault-gpu`、
`probe-ue-vulkan-fault.sh`、独立 GDB state reader 和
`vulkan_fault_snapshot.py`。入口仅在 `FDebug::CheckVerifyFailedImpl2`
过滤首个 VulkanRHI check，以及在致命 signal 处捕获；没有
shader/module/layout/pipeline 创建 API 断点，不修改寄存器、不执行
inferior 函数，也未修改 nested UE 渲染源码或默认策略。
使用显式 no-pso diagnostic flags，与原有 pipeline 捕获条件一致；
不将这些参数提升为产品设置。

同一原生 ARM64 GPU toolchain 容器、NVIDIA GB10 / 580.173.02 上：

| 证据目录（`/artifacts/carla/`） | 首个故障 | 真实字段 |
| --- | --- | --- |
| `ue-vulkan-fault-20260927T010756Z-eINmB6` | `VulkanCommands.cpp:532`，Pixel shader key check | stage 1 / UB index 4；requested key `14181356269016821042`，pending Pixel key 0；Pixel shader 指针 NULL，layout 仅有 Vertex set |
| `ue-vulkan-fault-20260927T011327Z-AypR2k` | 同一首个 check | stage 1 / UB index 1；requested key `4417814085198955516`，pending Pixel key 0；Pixel shader 指针 NULL，layout 同样仅有 Vertex set |

两份记录停在首个 check 函数体之前，没有继续运行到随后 descriptor
索引越界或 SIGSEGV。请求的 shader permutation 和 UB index 不同，
不能称为相同单个 shader 的最小复现；共同的实测契约冲突是 Pixel
shader 参数被提交给了没有 Pixel stage 的当前 graphics PSO。
这次捕获没有 NVIDIA compute compiler 故障，也没有对应 compute
creation caller，不选择此前 FRDG memcpy 或其他模块作为故障输入。

首次捕获还揭示了捕获器自身的两个问题：`gdb.Value.string(length=N)`
返回定长内容，包含 NUL 后相邻 literal；`Breakpoint.stop` 内读取其他
线程时，7 个线程仍显示 running。旧证据原样保留。捕获器现将 C 字符串
在 NUL 处终止，`stop` 仅保存 assertion 信息并请求停止，随后由
GDB stop event 读取有界线程栈；故障线程优先，保留主线程及渲染/工作
线程。第二次报告 `errors=[]`、graphics `read_errors=[]`，字符串完整
且没有相邻 literal。

第二次还关联了实际对象地址：
`PendingGfxState->CurrentState->GfxPipeline` 与 `CurrentPipeline`
相同，descriptor state 的 `DescriptorSetsLayout` 与当前 PSO 的
`Layout->DescriptorSetLayout` 地址相同。两侧 `bUseBindless` 为 false。
validator 独立重算 key mismatch、set/binding bounds 和对象关联，
不是直接相信 report 中布尔结论。因此当前证据支持继续追查
PSO 选择/绑定顺序或命令列表状态，而不是先修 descriptor array；
仍不能单凭一次 stop state 排除更早的生命周期、输入或并发问题。

报告明确区分 `CAPTURED_FAULT` 与运行 PASS，
`runtime_acceptance=false`，不输出世界/tick/sensor 验收结果。
signal 路径若有可读 compute caller，仅记录 `PipelineInfo` 和 shader
stored container；该 container 可能压缩或不同于 layout patch 后的
提交模块，始终标记 `exact_submitted_module=false`、
`exact_api_inputs=false`，不能直接交给已有 matched replay。
超时而没有故障仅为观察结束，validator 失败，不能伪装为捕获成功。

本轮辅助真实世界查询未通过；第二次查询时自有 inferior 已结束。
这些调试运行及查询都不构成未调试的 Town10 RPC 门禁。
调试器会改变调度，两个 graphics 状态捕获不能被等同为前三次原生
NVIDIA 编译器 crash 的根因。仍需分别保留两个故障类别。

GDB 12.1 及同 Ubuntu 22.04 原生 ARM64 库/工具从既有 build session
复制进一次性 GPU 容器，命令、环境、GDB/Python 和代码输入哈希
已归档；它不属于干净镜像验收。没有修改已有持久容器或覆盖旧证据。
工作清单落盘为 `docs/carla-dgx-todo.md`，第 2 项仍未全部完成：
graphics 首故障状态已捕获，NVIDIA 的精确 API/module 输入仍待捕获。

本地定向回归 16 tests、0 failures；`make test-local` 完整回归
635 tests、56 skipped、0 failures，shell syntax 和 `git diff --check`
通过。完整输出归档到第二份证据目录的 `local-regression.log`。
Town10 world、RGB/LiDAR、完整 GB10 CARLA 验收继续 **BLOCKED**。

### 9.63 2026-09-27 graphics 固定状态采集安全化

此前尝试在 history event 中遍历 graphics pipeline 的 vertex binding、
attribute、raster、multisample、depth/stencil、blend 和 dynamic arrays。
该诊断版本在 B075 运行中出现空的 begin 文件和未完成 history，采集器
本身可能放大了故障路径，不能把该次结果当作产品行为证据。

现已将 `GraphicsFixedState` 收缩为安全的顶层摘要：
`pNext/subpass/base pipeline`、stage count、各 Vulkan fixed-state
结构是否存在。它不再遍历嵌套数组、sample mask 或 pNext 内容，也不
宣称 `replay_ready`。ARM64 Development 增量编译成功。

本次仅完成编译与回归，尚未用安全版二进制重新跑 GB10，因此状态是
`diagnostic code compiled, runtime verification pending`。全量本地
回归为 657 tests、56 skipped、0 failures；shell syntax 和 diff check
通过。没有将任何诊断开关提升为默认，也没有实施产品修复。

### 9.62 2026-09-27 render-pass history 收尾完成

修正 target 比较的可见性后，最新同次 GB10 运行
`/artifacts/carla/town10-gb10-rpc-20260927T134801Z-Almx5m/`
成功写出完整 `pipeline-history.txt`。该记录包含：

- 2542 个 pipeline events；
- 1257 个 result events；
- B075 作为最后 compute begin；
- 27 个未完成 graphics create；
- 542 个唯一 graphics shader SPIR-V；
- 15 个 render-pass 摘要。

render-pass 摘要包括 compatible/full hash、attachment 数量和格式、
load/store、samples、depth/resolve/multiview 等字段。未完成 graphics
调用现在可同时关联 VS/PS shader hash、module handle、layout、render pass
和 cache。下一步仍需补齐 vertex input、blend、raster、depth-stencil、
dynamic state、subpass 及 framebuffer/resource contract，才能构造
可执行 graphics replay。当前没有产品行为修改，所有验收继续 BLOCKED。

### 9.56 2026-09-27 原生 NVIDIA compute 故障输入与真实 cache A/B

第 2 项现已完成到“精确故障输入捕获”。在上一轮新增的
`CarlaVulkanShaderDiagnostics` 基础上，先写 compute attempt 的
`.begin.txt`，再读取共享 pipeline cache，保存 `.cache.bin`，然后保存
SPIR-V；driver 返回后才写同前缀 `.result.txt`。因此
`begin exists && result missing` 是未返回调用的明确证据。该记录逻辑
只在显式命令行启用，不改变默认产品运行。

新 ARM64 Development binary、无 GDB、NVIDIA GB10 / 580.173.02 的
实际运行：
`/artifacts/carla/town10-gb10-rpc-20260927T032257Z-KMRgf2/`。
World gate 仍在启动阶段失败，server code 139；日志再次出现
`libnvidia-glvkspirv.so.580.173.02` offset `0x34d610`、地址 `0x8`。
此次不是调试器改变的 crash。

未返回的 exact compute attempt：

- 文件前缀：`shader-diagnostics/compute-2172064413225-B07511BF3F84ED1435DC4ECA18879491E`
- shader hash：`B07511BF3F84ED1435DC4ECA18879491E`
- entry：`main_0000142c_a6b37050`
- SPIR-V：5164 bytes，SHA256 `b5500d7af1d3f07a15118d78fc784d062e343140d8431bee713e3ba1d3710bf3`
- pipeline flags/stage flags：0；stage pNext 为 null；required subgroup size 为 0
- pipeline layout handle：`0xfbe7740015b0`
- shared pipeline cache handle：`0x3de14bb0`
- cache snapshot：21098 bytes，`.cache.bin` 存在
- UE set 0 bindings：`binding 0 type 8 count 1`，
  `binding 1..4 type 3 count 1`，`binding 5 type 2 count 1`，
  `binding 6 type 0 count 1`，均 compute stage
- `.begin.txt` 存在；同前缀 `.result.txt` 不存在；因此崩溃发生在该
  attempt 的 driver call 或其前置诊断阶段，不能把返回值伪造为成功

该 SPIR-V 通过 `spirv-val --target-env vulkan1.3`。同一 SPIR-V、
同一 UE layout 和这次真实 21098-byte cache 的独立 GB10 replay：
`/artifacts/carla/vulkan-compute-replay-20260927T045705Z-GMyhcu/`，
结果 `PASS`，pipeline create 约 `0.016217s`，报告
`pipeline_cache_policy=captured_initial_data`、
`layout_source=ue-layout-baseline`、
`device_configuration_reused=true`，device snapshot SHA256
`dce1145d38abbd3415bfe4c5624dffab30d0ea29f07cf66105187c7fa92d8199`。
这排除了“单个 SPIR-V + UE descriptor layout + cache snapshot +
UE device feature/extension 配置在干净 device 上必然触发”这一假设。

边界仍然重要：独立 replay 没有重建完整 UE VkDevice 的资源生命周期、
此前 graphics/compute 创建顺序、同一 cache 的并发访问、UE shader module
对象、descriptor resource 状态或 command execution history。因此当前
最强结论是：故障发生在完整 UE device context 中，driver library crash
输入已精确落盘，但唯一根因仍未证明。不能把 isolated PASS 当作产品
通过，也不能直接把“pipeline cache 是根因”升级为修复。

第 3 项下一步改为对真实 UE context 做 state/history 对照，优先检查：
同一 VkDevice 上 cache 的并发/合并状态、shader module 生命周期、
创建前 graphics/compute 顺序和 layout/module handle 对应关系。第 4 项
仍未开始，不修改 shader、descriptor type、cache policy 或并发策略。
Town10 tick、RGB/LiDAR、完整 GB10 验收继续 **BLOCKED**。

本轮新增的真实运行和 replay 均使用显式 diagnostic binary/stage，
不是 clean-image acceptance。现有 source diagnostics 默认关闭；direct
runtime 未启用 shader dump 时仍不写文件。

### 9.58 2026-09-27 compute 历史 replay 与串行化对照

为验证“此前 compute pipeline 创建历史”是否是充分触发条件，扩展
`vulkan-compute-replay.c` 支持 `--history`。该模式在**同一个 VkDevice**
和同一个 pipeline cache 上，依次创建：

1. `main_00000628_26d5e8e8`
2. `main_00000638_c4bd1ff5`
3. `main_0000061c_2462ec74`
4. B075 `main_0000142c_a6b37050`

前三个 module/layout/pipeline 对象在 B075 创建时仍保持存活，设备配置、
UE layout 和 21098-byte cache 均复用。证据：
`/artifacts/carla/vulkan-compute-replay-20260927T053512Z-doKJSA/`。
报告 `history_pipeline_count=3`、`status=PASS`，B075 create 约
`0.012512s`。这排除了“仅同一 VkDevice 上前三个 compute pipeline
历史/对象存活就足以触发”的假设。

同时新增默认关闭的
`CarlaVulkanSerializeComputePipelineCreation`，在 UE 内对
`GetOrCreateComputePipeline` 的完整创建过程持有写锁，避免相同 shader
或不同 shader 的 compute pipeline 创建并发。新 binary 的真实 GB10
对照 `/artifacts/carla/town10-gb10-rpc-20260927T053940Z-B0DZTA/`
仍在 B075 进入 NVIDIA compiler 后以地址 `0x8` 崩溃。由此不能把
compute PSO 并发单独认定为根因。

当前差异已进一步收敛为：完整 UE 的 graphics pipeline 创建历史、
graphics shader modules/layout/resource 状态、command submission
上下文或其生命周期，与独立 compute history replay 不同。下一步应
捕获/重建 B075 前的 graphics pipeline inputs 和同一 device 对象状态；
不再继续叠加 compute-only 开关。所有串行化/历史参数均为显式诊断，
不是产品默认。

### 9.59 2026-09-27 graphics driver-call 串行化对照

新增默认关闭的 `CarlaVulkanSerializeGraphicsPipelineCreation`，只在
实际 `vkCreateGraphicsPipelines` 调用外加一个进程内锁；不改变 PSO
选择、shader、layout、cache 或 RHI 命令顺序。ARM64 Development binary
已重新编译，启动命令明确包含该参数。

实际证据：
`/artifacts/carla/town10-gb10-rpc-20260927T111037Z-y4Ip13/`。
日志确认 `CARLA diagnostic: serializing vkCreateGraphicsPipelines driver
calls`，但运行仍在 B075 进入 `libnvidia-glvkspirv.so.580.173.02`
并以地址 `0x8` 崩溃。

该运行的 shader diagnostics 在 B075 上保存了两个不同 attempt：
`compute-2452648440229-B075...begin.txt` 和
`compute-2452648441030-B075...begin.txt`，两个都没有对应
`.result.txt`。这说明在完整 UE context 中同一 B075 shader 可能被
重复提交/重复创建；但不能直接称为根因，因为此前
`/artifacts/carla/town10-gb10-rpc-20260927T053940Z-B0DZTA/` 的
compute-pipeline 全局串行对照只保留一个 B075 begin，仍然在同一
NVIDIA compiler 位置失败。

结论：graphics driver-call 并发不是充分解释；重复 B075 create 也不是
单独充分解释。前一份 `pipeline-history.txt`
`/artifacts/carla/town10-gb10-rpc-20260927T103619Z-XBGVTj/` 仍提供
同次运行的 2594 个事件，其中 B075 开始前存在 34 个未配对 graphics
begin，说明 graphics 创建历史/生命周期确实与独立 replay 不同，但
当前尚未把这些 graphics inputs 完整重建到独立程序。

下一步应从这些未配对 graphics begin 中提取 shader module/layout/render
pass/cache 元数据，优先重建 B075 前的 VkDevice graphics history；不再
继续叠加单一并发开关。所有诊断开关默认关闭，Town10/RGB/LiDAR/full
GB10 验收继续 **BLOCKED**。

### 9.60 2026-09-27 graphics 未完成调用的 shader 关联

新增 `analyze_vulkan_pipeline_history.py`，对同次运行的
`pipeline-history.txt` 做 bounded、fail-closed 配对分析，并解析
`module=... hash=... file=graphics-shaders/...` 映射。分析输出：
`/artifacts/carla/town10-gb10-rpc-20260927T122856Z-9n8I53/pipeline-history-analysis.json`。

实际结果：

- 总事件 1273；
- 已返回 result 628；
- B075 target compute 是最后事件；
- 未完成 graphics create 16 个；
- 未完成调用分布在 14 个线程；
- 主要 cache 为 `0x0`，另有一个未完成调用使用 `0x289e05b0`；
- 每个未完成调用都有 layout、render pass、VS/PS module handle、entry
  和对应 shader hash；
- 同次运行保存 219 个唯一 graphics shader SPIR-V，总目录约 3.7 MB。

典型未完成 graphics 对包括：
`D718BBE293386AAE7A0B2184EDBD69137332965A` +
`7470CDB9F3ECC909B80B08E8332ABA73767F9928`、
`469A20D1F4920206CCE41C3F9E1CF87B917D4679` +
`8826B4301555EC15CBAA2A6B9A230475A0A581D6`、
`CFCDDB61AEC15252407F5BE23BE0B0C7214FC12B` +
`F75968DAAD888604086377A0C642798F382E65C1`。

这比“graphics 仍在并发”更具体：B075 开始时确实存在 16 个未完成
graphics driver calls，且它们涉及一组确定的 graphics shader modules。
但现阶段仍没有完整 render-pass create inputs 和可直接重放的 graphics
PSO 描述，不能直接实施修复。下一步以这 16 个调用为范围补齐
descriptor/pipeline layout/render-pass 元数据，再做最小 graphics history
replay。

新增分析器测试 3 项通过；所有诊断仍默认关闭，产品门禁继续 BLOCKED。

### 9.61 2026-09-27 render-pass 摘要采集边界

在 graphics history 中增加了显式 render-pass 摘要采集，计划记录
compatible/full hash、attachment 数量和格式、samples、depth/resolve/
multiview 等摘要，且只在 `CarlaVulkanPipelineHistory` 开启时执行。
ARM64 Development 编译成功。

中间运行 `/artifacts/carla/town10-gb10-rpc-20260927T130910Z-R7ug74/`
曾保存 graphics shader 但 `pipeline-history.txt` 为空，已作为采集器失败
证据保留。随后修正 target 可见性/写出路径后，最新运行
`/artifacts/carla/town10-gb10-rpc-20260927T134801Z-Almx5m/` 成功保存：

- 2542 个 pipeline history events；
- 1257 个 result events；
- B075 target 为最后 compute begin；
- 27 个未完成 graphics create；
- 542 个唯一 graphics shader SPIR-V；
- 15 个 render-pass 摘要。

render-pass 摘要包括 compatible/full hash、attachment 数量与格式、
load/store、samples、depth/resolve/multiview 等字段。未完成 graphics
调用现在可同时关联 VS/PS shader hash、layout、render pass 和 cache。
这已经形成 graphics history replay 的输入清单，但还不是可执行 replay：
完整 graphics pipeline 还需要 vertex input、blend、rasterizer、
depth-stencil、dynamic state、subpass dependency 和 framebuffer 资源
契约。没有修改产品默认渲染行为。

### 9.57 2026-09-27 no-pso 对照未改变 B075 故障

使用同一新 ARM64 Development binary、同一 GB10、同一 shader
diagnostics，但增加：

- `r.PSOPrecaching=0`
- `r.Vulkan.AllowPSOPrecaching=0`
- `r.AsyncPipelineCompile=0`
- `r.Vulkan.RHIThread=0`

证据目录：`/artifacts/carla/town10-gb10-rpc-20260927T042546Z-Cp5Xwr/`。
运行仍在 `libnvidia-glvkspirv.so.580.173.02`、地址 `0x8` 失败；
未返回 attempt 仍是 `main_0000142c_a6b37050` / B075。该 profile 的
attempt 没有 populated cache snapshot，仍然失败，因此“关闭 PSO
precache/async compile/RHI thread”不能解释或修复该 crash。

这进一步排除了几个低成本 workaround：默认 profile 与 no-pso
profile 都命中同一 compute entry，且此前 exact shader/layout/cache
独立 replay PASS。当前应停止继续堆叠启动开关，转向完整 UE device
上的 shader module 对象、pipeline cache 访问时序、此前 graphics/compute
对象及线程生命周期。没有实施产品默认改动。

### 9.55 2026-09-27 引擎内存记录与无 GDB 低干扰对照

为避免把 GDB 调度变化当成 PSO 根因，新增
`VulkanGraphicsDiagnostic.{h,cpp}`。该代码默认关闭；只有命令行存在
`-CarlaVulkanGraphicsMemoryTrace=<directory>` 时才创建固定 4096 项
环形记录。记录点位于 Vulkan RHI 的现有执行入口：

- `RHISetGraphicsPipelineState` 在 `SetGfxPipeline` 完成后记录 context、
  pending state、submitted/current pipeline、五个 stage keys 和是否变化；
- 图形 `RHISetShaderParameters` 记录 shader 指针、frequency、shader key
  及当时的 current pipeline keys；
- `RHISetShaderUniformBuffer` 首次发现 shader key 与 pending key 不同
  时，记录 mismatch header 和环形历史，然后才执行原有 `check`。

正常没有该命令行参数时，记录函数立即返回，不写文件、不修改 PSO、
shader 或 descriptor 行为。记录实现只在显式诊断期间使用锁和固定上限，
首个 mismatch 才执行文件 I/O。`vulkan_graphics_memory_trace.py` 对
schema、事件数量、key vector、mismatch 不等式和同 context/pipeline
关联进行 fail-closed 校验；结果明确 `runtime_acceptance=false`。

ARM64 Development 增量编译成功，新增 VulkanRHI 文件被 UBT 作为独立
编译单元构建并完成 CarlaUnreal 链接。新二进制没有覆盖旧 cooked
client；使用独立 diagnostic stage，并通过显式 binary/debug 路径和
cooked `Content`/`Engine` 路径验证启动资产关系。第一次直接运行误用
`/workspace/carla` executable path，在 ICU 初始化阶段失败；该证据
保留为路径配置失败，随后已改为独立 stage。

无 GDB direct-run 实际证据：
`/artifacts/carla/ue-vulkan-memory-trace-20260927T024452Z-aZ6Nne/`。
同一 GB10 / NVIDIA 580.173.02、同 no-pso diagnostic renderer flags、
新 Development binary 直接运行约 180 秒：

- Town10HD_Opt 完成加载，记录 `Engine is initialized`；
- 没有 NVIDIA `libnvidia-glvkspirv` SIGSEGV；
- 没有 `graphics-memory-trace.txt`，说明该次没有观察到内存记录点的
  key mismatch；
- 到有界窗口结束时收到 TERM，UE 日志显示正常 `PreExit`、`CleanupWorld`
  和退出，wrapper runtime code 为 124；
- wrapper 最终为 `FAIL` / `validate` / exit 3，因为没有 mismatch
  trace；不是运行 PASS，也不是 Town10 world/tick/sensor acceptance。

这次结果降低了“无 GDB 时必然立即触发该 graphics mismatch”的证据强度，
但不能证明问题不存在：旧 GDB 运行改变调度，运行顺序和 startup state
没有完全隔离；direct-run 也没有建立 RPC readiness、同步 tick 或传感器
门禁。新增内存记录没有得到故障输入，不能据此实施 shader、PSO、
descriptor 或并发修复。NVIDIA compiler SIGSEGV 仍是独立未捕获输入。

新增 direct-run 入口 `make carla-ue-vulkan-memory-trace-gpu`，不申请
`SYS_PTRACE`，不启动 GDB；它只在 trace 文件通过 validator 时报告
`CAPTURED_MISMATCH`，否则保留运行日志并退出 3。相关测试覆盖固定环、
不等式、key 关联、direct/GDB 边界和 Make 入口。

本轮 focused memory-trace tests 5 tests、0 failures；fault-capture tests
30 tests、0 failures；ARM64 Development build PASS；`make test-local`
654 tests、56 skipped、0 failures，shell syntax 和 outer `git diff --check`
通过。所有 Town10/RGB/LiDAR/完整 GB10 产品门禁继续 **BLOCKED**。

### 9.54 2026-09-27 PSO 绑定历史与低干扰命令列表身份

按清单第 3 项，增加默认关闭的 `UE_FAULT_TRACE_GFX` /
`CARLA_UE_FAULT_TRACE_GFX`，只有值 1 才安装额外 graphics 断点。
默认 0 的首故障入口仍没有 Vulkan 创建 API 或正常绑定/参数断点。
wrapper 记录策略和环境哈希，Make 入口明确传递该选项。

最终追踪在 `VulkanPipelineState.cpp:560` 读取 `RHISetGraphicsPipelineState`
的 live context 和 Pipeline：此处 `SetGfxPipeline` 已返回，当前
pipeline/descriptor state 已应用。记录 submitted/current PSO 地址、
五个 stage keys、Pixel shader 指针和执行中的 RHI 命令列表。
shader 参数入口只保存 **Pixel 参数遇到当前 Pixel key 为 0 且 Pixel
shader 指针为 NULL** 的事件；它不是所有 shader 参数的完整日志。
额外断点仍会改变调度，不能称为未调试运行。

最近 256 个事件保存在环形历史中，明确给出 `event_count` 和
`dropped_events`。观察窗口仍为 150 秒，没有为取得 PASS 加长时间。
总事件保护为 1,000,000；在达到保护值时先停止，计数不包含未保存的
失败事件。内存上限不依赖总事件数。validator 检查时间顺序/预算计数，
只关联同一 context、faulting thread、命令列表及 shader，进一步核对
PSO 地址 **和 stage keys**，不只凭可能复用的地址作结论。

本轮同一原生 GB10 / 580.173.02、相同 binary/debug symbols、同
no-pso diagnostic profile 的实际记录（目录均位于 `/artifacts/carla/`）：

| 证据目录 | 结果与范围 |
| --- | --- |
| `ue-vulkan-fault-20260927T014720Z-83F8YB` | 最初在 `SetGfxPipeline` 的返回行 909 读取，`this` 已被优化掉；入口正确失败，没有历史或故障输入冒充 |
| `ue-vulkan-fault-20260927T014927Z-PixK0i` | 上层读取点可用，但保存全部 graphics 参数的初版达到 10,000 事件保护；未捕获原始故障，validator 退出 2 |
| `ue-vulkan-fault-20260927T015348Z-zx8AHX` | 选择性追踪记录 13,994 个事件，保留最近 256 个、截断 13,738 个；150 秒结束为 `SIGINT`/`observation-ended`，无首故障，`capture_complete=false`，validator 退出 2 |
| `ue-vulkan-fault-20260927T015729Z-nO2skz` | 关闭绑定追踪的低干扰基线，再次捕获 `VulkanCommands.cpp:532` 的首个 Pixel shader check；捕获状态 `CAPTURED_FAULT`，不是 runtime PASS |

选择性追踪的 **保留部分** 256 个事件均为已完成绑定，submitted/current
PSO 摘要逐项匹配，并从 `FRHICommandListBase::Execute:this` 读到真实
`list_address`、`list_executing=true`。总数 13,994 是选择性事件数，不是
完整参数调用次数；没有全量类型计数或完整历史，不能证明截断部分
每次绑定都正确，也不能用保留的正常记录解释另一份发生故障的运行。
本轮还发现旧的线程采样优先级把 renderer 和 worker 放在同一优先级，
主线程超时会漏掉 RenderThread；旧 timeout 归档原样保留。现已明确让
RenderThread/RHIThread 排在 worker pool 前，并以定向测试验证。

低干扰基线的实际字段：

- RenderThread 0，stage 1 / UB index 1；
- requested key `4417814085198955516`，pending Pixel key 0；
- 当前 PSO 的 Vertex key `10170975614878636138`，Pixel shader NULL；
- descriptor pipeline/layout 都与当前 PSO 匹配，set 1 不存在；
- command type `FRHICommandSetShaderParameters<FRHIGraphicsShader>::Execute`；
- list address `0xe5ed26987d60`，通过 live `FRHICommandListBase::Execute`
  frame 的 `this` 读取，`bExecuting=true`。

优化掉的 command-frame `this`/`CmdList` 明确保存在
`unavailable_fields`；live Execute frame 提供的是**命令列表身份**，
并未恢复准确命令节点地址。此次 raw `ShaderRHI` 指针也显示 optimized
out，报告保留对应 `read_errors`；shader key 等契约字段可读，不能称为
所有字段完整可读。没有做寄存器猜测、inferior 调用或随意对象替代。
默认关闭/预算截断、不同 context/thread/list/shader 不误关联、
节点身份不可读与列表身份可读的区别均有回归。

当前结论：故障契约仍可在低干扰基线重现，正常追踪条件下没有得到
对应故障历史。缓存暖化、运行顺序和断点调度的影响没有隔离，不能
单凭此对照断言并发、缓存或遗漏某条 PSO 命令是根因，更不能把
trace 下没有 crash 当作修复。清单第 3 项保持未完成；下一步优先采用
默认关闭、不依赖高频断点的内存记录，在实际故障处读取 context/PSO
选择和绑定顺序，再决定最小引擎修复。

本轮未修改 nested UE 渲染源码、产品默认设置、sensor 门禁或
验收超时。NVIDIA 编译器 SIGSEGV 的准确 API/module 输入仍未捕获，
与 graphics 失败分开处理。沿用一次性容器中的 Ubuntu 22.04 原生
ARM64 GDB 12.1 工具副本，工具哈希归档，不作为干净镜像验收。

本地定向回归 30 tests、0 failures；`make test-local` 完整回归
649 tests、56 skipped、0 failures，shell syntax 和 `git diff --check`
通过。完整输出归档到低干扰基线目录的 `local-regression.log`。
Town10 world、RGB/LiDAR、完整 GB10 CARLA 验收继续 **BLOCKED**。

### 9.64 2026-09-28（本地）mixed driver-entry 配对与 graphics 故障边界

旧 shader diagnostics `.begin.txt` 在 `vkCreate*Pipelines` 调用及可选
mixed 序列化锁**之前**写入。多个无 `.result.txt` 的 B075 begin 只能
说明调用尝试开始，不能识别哪一个实际进入驱动。先前对 B075 的
SPIR-V、layout、真实 cache 和 device 配置的独立重放仍有效，但它们
不是已经验证的故障调用完整输入。

在默认关闭的 `CarlaVulkanSerializeMixedPipelineCreation` 与 pipeline
history 同时启用时，分别在 graphics/compute 的同一把驱动锁内、
紧贴 `vkCreate*Pipelines` 写 `driver-*.enter.txt`，驱动返回后写
`.result.txt`。graphics 标记含线程、layout、render pass、cache、
stage module/entry 及从当前 UE module 映射取得的 shader hash；
仅记录句柄和顶层字段，不遍历 Vulkan 嵌套指针。ARM64 Development
增量编译通过，新 binary/debug/sym 只复制到独立
`cooked-client-memorytrace` stage，没有覆盖 `cooked-client-full`。

同一原生 GB10 / NVIDIA 580.173.02 上的无 GDB runtime 证据：

| 证据目录（`artifacts/carla/`） | 驱动锁内的配对结果 | 运行结果 |
| --- | --- | --- |
| `town10-gb10-rpc-20260928T000321Z-YgVbHN` | compute 4 enter / 3 return，唯一未返回 entry `main_00006260_1d3100ee` | startup SIGSEGV，`libnvidia-glvkspirv.so.580.173.02` |
| `town10-gb10-rpc-20260928T000433Z-aRngDT` | compute 3/3；此时尚无 graphics driver-entry 标记 | startup 同类 SIGSEGV；B075 `.begin` 不能指定 faulting API |
| `town10-gb10-rpc-20260928T000626Z-hBG42M` | graphics 626/625、compute 3/3；唯一未返回 graphics VS/PS entry 为 `main_00000340_f609a8f9` / `main_00000418_42e34c54` | startup 同类 SIGSEGV；两份 SPIR-V 归档 |
| `town10-gb10-rpc-20260928T000924Z-FEfsfs` | graphics 631/630、compute 3/3；唯一未返回 graphics VS/PS hash 为 `51930AA2D306F548CD1FA00F690FEFCF5299A012` / `925014F68C7638316C180466FB82977ACC742433` | startup 同类 SIGSEGV；同轮两份 SPIR-V 归档 |

checkpoint history 分别含 1296/1308/1242/1252 个事件，均经
`analyze_vulkan_pipeline_history.py` 检查；checkpoint 发生在后续
故障调用之前，不能借其中的未完成 graphics begin 反推正在运行的
驱动调用。最后两轮 graphics 失败的 shader entry 不同；观察到的
唯一未返回 driver-entry 与故障栈构成有界现场证据，**不能**由此
断定 shader 单独有错、NVIDIA 驱动单独有错或两个现场有同一根因。

`make test-local` 为 662 tests、56 skipped、0 failures；定向 10 tests
与本轮改动的 diff 检查通过。UE 子仓库全量 `git diff --check`
仍报 `DeferredShadingRenderer.h` 两处旧有尾随空格，不属于本轮改动。
下一步须安全捕获故障 graphics 的完整 `VkGraphicsPipelineCreateInfo`、
layout/render-pass/cache，建立独立可重放对照，再依证据实施最小修复。
Town10 世界就绪、同步 tick、RGB/LiDAR、长期运行和 clean rebuild
均未通过，状态继续 **BLOCKED**。

### 9.65 2026-09-28（本地）实际 graphics 模块、固定状态与同轮 cache

进一步核查发现原 `graphics-shaders/<hash>.spv` 来自
`FVulkanShader::GetSpirvCode()`，但 graphics 模块创建时可能通过
`GetPatchedSpirvCode()` 生成不同字节。因此旧 hash 文件不能等同于
`vkCreateShaderModule` 实际输入。本轮在创建 Vulkan shader module
的代码路径上保留最多 2 MiB 实际 SPIR-V，仅显式启用 history 时写入
`graphics-shaders/module-<id>.spv`；driver-entry 记录对应文件、module
handle、entry 和 shader hash。模块句柄复用会刷新文件映射，写入失败
则移除映射，不冒充有效输入。

graphics driver-entry 同轮记录有界的 vertex bindings/attributes、
assembly、viewport 计数、raster、multisample、depth/stencil、
blend、dynamic states 和各级 `pNext`/specialization 是否存在。
未知扩展链和被截断的数组不伪造为已采集字段。附带 UE layout
descriptor bindings、push-constant 路径及 render-target 附件/引用
摘要；**后者不等于实际 `VkRenderPassCreateInfo(2)`**。
`CarlaVulkanGraphicsCacheSnapshot` 显式启用时才在驱动锁内查询和
保存 cache，单次至多 8 MiB、整轮至多 128 MiB；空 handle 写
`cache_data=null`，不调用驱动查询。所有诊断默认关闭。

`analyze_vulkan_driver_entries.py` 校验同轮唯一未返回的锁内调用、
标记完整性、路径边界、模块 SPIR-V 指令和 `OpEntryPoint`、SHA-256、
cache 文件长度。runtime 清理时自动写
`driver-entry-analysis.json`、日志和分析退出码，不能把分析成功当
产品验收。实际原生 GB10 / 580.173.02 证据：

| 目录（`artifacts/carla/`） | 同轮结果 |
| --- | --- |
| `town10-gb10-rpc-20260928T015029Z-uXak7F` | graphics 620/620、compute 4/3；唯一未返回 B075 `main_0000142c_a6b37050`，离线校验 362 个 graphics module 文件 |
| `town10-gb10-rpc-20260928T015541Z-ZSWFnh` | graphics 638/638、compute 4/3；故障 compute entry 改为 `main_000009cc_71a7f7d2`，所有 graphics cache 均为空 |
| `town10-gb10-rpc-20260928T015709Z-pzkhdS` | graphics 633/632、compute 3/3；唯一未返回 VS/PS 实际模块 SHA-256 为 `a29c75735ff247eb0c9a9d1d40901f83be0f4a140cdff932702af3d1ca1e8a68` / `3701561d3728cd6113326da049447716058f30a55007a6ec95130972ac4e644f`；未开启 cache 字节采集 |
| `town10-gb10-rpc-20260928T015955Z-R0V6SI` | graphics 645/644、compute 3/3；同一对实际 SPIR-V SHA-256 再次出现，并保存该故障 graphics 的 21097 字节真实 cache；自动分析退出 0 |
| `town10-gb10-rpc-20260928T020235Z-WCiO9k` | graphics 670/669、compute 3/3；另一对实际 shader module 字节、color/depth attachment references、非 bindless push-constant ranges=0 和 21098 字节 cache 同轮归档；自动分析退出 0 |

上述运行全部在 startup 阶段的
`libnvidia-glvkspirv.so.580.173.02` 内 SIGSEGV，RPC world
readiness FAIL。重复 VS/PS 字节提高了该组作为重放对象的优先级，
但同一故障位置也发生在其他 compute/graphics 调用内，不能把
某一对 shader 或 NVIDIA 驱动独立认定为根因。下一步补采真正的
render-pass 创建 object graph 与 pipeline layout 必需输入，在相同
设备配置上独立重放，再与完整 UE 的资源/并发历史做对照。当前
`replay_ready=false`，Town10、同步 tick、RGB/LiDAR 门禁均 **BLOCKED**。
本轮最终 `make test-local` 为 670 tests、56 skipped、0 failures；
校验器同时在原生容器内和宿主机 bind mount 路径下核验最后一份
graphics 样本，容器路径仅按同一运行目录与预期文件名对应，不依赖
宿主机具有相同的 `/artifacts` 绝对路径。本轮改动的 diff 检查与
shell 语法检查通过。

### 9.66 2026-09-28（本地）实际 render-pass 创建结构与两组独立 graphics 对照

引擎诊断从 UE 的 render-target layout 摘要推进到真正的 Vulkan
render-pass 创建调用。在 `vkCreateRenderPass` 和
`vkCreateRenderPass2KHR` 返回成功、builder 的参数仍有效时，
按实际 handle 保存有界的附件、subpass、依赖、correlated mask、
attachment references 和已识别的 `pNext` 字段。无法识别的扩展链
只记录类型并置 `capture_complete=0`，不冒充完整参数；对应文件
通过 handle 关联到 graphics driver-entry。默认未显式开启
pipeline history 时不保存文件。ARM64 Development 编译重链成功，
只更新独立 `cooked-client-memorytrace` stage。

真实 GB10 / NVIDIA 580.173.02、无 GDB 的两组现场：

| 同轮故障目录（`artifacts/carla/`） | 故障现场 |
| --- | --- |
| `town10-gb10-rpc-20260928T023252Z-ZxrO4t` | graphics 676/675、compute 3/3；唯一未返回 graphics 的两份实际 VS/PS、21098 字节 cache、1 附件/1 subpass/0 依赖的 `vkCreateRenderPass2KHR` 快照均同轮关联，快照 `capture_complete=1`，分析退出 0 |
| `town10-gb10-rpc-20260928T024745Z-7E9KIF` | graphics 625/624、compute 3/3；另一对曾重复出现的故障 VS/PS，同轮实际 render-pass2 和 21098 字节 cache 均完整，分析退出 0 |

两轮 CARLA 启动继续在
`libnvidia-glvkspirv.so.580.173.02` 中 SIGSEGV，世界 RPC 未就绪。
离线生成器严格只接受已验证的单 subpass、无扩展链、纯颜色附件、
非 bindless、无 push constants 等输入，旧运行缺少真实 render-pass
时明确拒绝。构建容器用 `spirv-val` 检查实际 VS/PS，生成同等参数的
独立 Vulkan 程序及输入哈希；GPU 容器再校验哈希并仅执行一次
`vkCreateGraphicsPipelines`，没有绘制或仿真：

| 独立对照目录（`artifacts/carla/`） | 结果 |
| --- | --- |
| `vulkan-graphics-replay-20260928T024620Z-Ir2apU` | 对第一组 VS/PS + 同轮 cache + 实际 render-pass2 结构，GB10 `vkCreateGraphicsPipelines` 返回 `VK_SUCCESS` |
| `vulkan-graphics-replay-20260928T024826Z-ursarG` | 对第二组 VS/PS + 同轮 cache + 实际 render-pass2 结构，GB10 `vkCreateGraphicsPipelines` 返回 `VK_SUCCESS` |

两份独立对照都使用来自 **2026-09-27 另一轮 UE 捕获**的
`vkCreateDevice` 配置；它们不是故障进程同轮 device snapshot，
也未重现完整 UE 的 pipeline/cache、shader module、render-pass、
layout 的存活历史与其他 Vulkan 调用。独立 PASS 排除了这两组输入
在所用隔离设备配置中单独必崩的解释，**不能**把显卡驱动、UE
生命周期或并发提前指定为充分根因。报告明确
`ue_exact_replay=false`；真实产品状态仍是 **BLOCKED**，下一步
需要同轮设备配置与对象/创建顺序对照，再据证据设计最小修复。

`make test-local` 为 678 tests、56 skipped、0 failures；脚本语法、
JSON 格式与本轮改动的 diff 检查通过。UE 子仓库全量 diff 检查仍有
此前 `DeferredShadingRenderer.h` 两处旧有尾随空格，不属于本轮改动。

### 9.67 2026-09-28（本地）当前 GB10 `vkCreateDevice` snapshot 与 device-only 边界

为消除独立 graphics replay 使用旧设备配置的疑问，使用当前
`cooked-client-memorytrace` binary、当前 NVIDIA GB10 / 580.173.02
环境和 GDB 运行捕获 `vkCreateDevice` 的真实入口参数。证据目录：
`artifacts/carla/ue-vulkan-pipeline-20260928T050306Z-oEG3Rk/`。

snapshot 验证结果：

- `capture_point=vkCreateDevice:entry`，`schema_version=2`，
  `capture_complete=true`；
- 39 个 device extensions，26 个已识别 feature-chain 节点；
- queue family 0 使用 16 个 queue，queue family 1 使用 1 个 queue；
- NVIDIA GB10、driver `580.173.02`，snapshot SHA-256：
  `f0a4e32e36d655432897d28dbbefa3287c4cc8f637607388dd643abcd71fa30c`；
- 同一 GDB 进程还观察到 224 个 shader modules、91 个 descriptor-set
  layouts、102 个 pipeline layouts、1 个 pipeline cache 和 48 个
  compute pipeline calls；graphics driver-entry 统计已进入运行时，
 但 GDB capture 的目标逻辑只对 compute call 建立 target correlation。

该 GDB 进程在 90 秒内未到达与无 GDB runtime 相同的 graphics fault，
最终由 SIGINT 结束；因此 `pipeline-capture.json` 的
`capture_complete=false` 是诚实结果，不能把对象历史称为完整 fault
replay。为保留有效 device 证据，GDB probe 新增显式
`CARLA_UE_PIPELINE_ALLOW_DEVICE_ONLY=1`，仅在 crash 模式、pipeline
target 缺失且 device snapshot 完整时输出 `CAPTURED_DEVICE_ONLY`；
默认仍关闭。GPU 容器没有 `spirv-val` 时可用
`CARLA_UE_PIPELINE_DEFER_SPIRV_VALIDATION=1`，再由 build container
执行实际 SPIR-V 校验。

将该**当前环境** snapshot 用于此前完整 render-pass 的 graphics
输入，独立 replay 目录
`artifacts/carla/vulkan-graphics-replay-20260928T050903Z-9Cgo92/`
通过：同一真实 VS/PS、同轮 cache、同轮 render-pass2 结构在 GB10
独立进程的 `vkCreateGraphicsPipelines` 返回 `VK_SUCCESS`。报告中
`device_snapshot_sha256` 与上面的 hash 一致，`ue_exact_replay=false`。

结论进一步收敛：当前设备配置也不是该 graphics 输入的充分崩溃条件；
完整 UE 仍需解释 pipeline/cache 对象生命周期、创建顺序、其他
graphics/compute 调用的并发历史或同一故障进程的设备细节差异。
Town10 world、同步 tick、RGB/LiDAR 和长期运行继续 **BLOCKED**。
本轮完整回归为 680 tests、56 skipped、0 failures。

### 9.68 2026-09-28（本地）同一故障进程 device snapshot、compute fault 与精确输入 replay

高频 GDB 对每个 Vulkan 对象下断点会改变 UE pipeline 创建时序，
因此新增低干扰入口
`scripts/carla/probe-gb10-device-fault.sh`。它只在
`vkCreateInstance` / `vkCreateDevice` 捕获 typed 参数，随后继续运行；
graphics/compute 的实际进入、返回和故障关联由已经编译进
`VulkanPipeline.cpp` 的 driver-entry 标记完成。设备捕获 handler 通过
`CARLA_GDB_CONTINUE_AFTER_DEVICE=1` 显式继续，默认 device-state probe
仍在 `vkCreateDevice` 处停止。该入口强制校验基线 binary 哈希、NVIDIA
GB10 ICD 和归档命令行；只接受唯一未返回的 driver-entry 与 NVIDIA
SIGSEGV 栈，不能把超时或不完整目录称为 fault capture。

最强证据目录：
`artifacts/carla/gb10-device-fault-20260928T054047Z-DbJisL/`。

- 同一个 UE fault process 的 `vkCreateDevice` snapshot 完整，SHA-256
  为 `b719168834c4b669107b90f4663c8e0f6b9b75798a885d4dfd4e7f3d8a5885cf`；
  39 个 device extensions、26 个 feature-chain 节点，queue family
  0/1 分别为 16/1 个 queue；
- 同一目录的 engine-side driver-entry 分析为 graphics 642/642、
  compute 4/3，385 个实际 module 文件和 15 个 render-pass snapshot
  通过校验；
- 唯一未返回调用为 compute
  `main_00000290_c1eca148`，entry marker thread 105，GDB 的同一
  进程线程 `Backgro-rker #4` 收到 SIGSEGV，栈首位为
  `libnvidia-glvkspirv.so.580.173.02`；
- 同轮 shader diagnostics 保存该 compute 的实际 SPIR-V 656 bytes、
  cache 21098 bytes、layout set0/binding0 storage buffer，且 pipeline
  flags/stage flags/pNext/subgroup 均为 0。

在 `compute-replay/` 中以同一 device snapshot 生成 ARM64 独立
replay；GB10 返回：
`vkCreateComputePipelines` `VK_SUCCESS`，entry 相同，
`device_configuration_reused=true`，cache policy 为 captured initial
data，报告 `ue_exact_replay=false`。这与此前两组 graphics 独立 replay
一致：实际 shader/layout/cache/device 配置元组在隔离进程中可创建，
但完整 UE 仍崩溃。

结论边界已经进一步收敛：当前证据排除了“该 compute 输入 tuple 在
同一设备配置下单独必崩”，不能排除 UE 共享 pipeline cache 的并发
访问、对象生命周期/释放后使用、创建顺序、同一进程其它 API 调用
改变的驱动上下文，或 NVIDIA compiler 对完整 UE 状态的敏感路径。
下一步不再继续叠加 compute-only 串行开关，而是保存同一进程中
pipeline cache 创建/合并/查询、shader module/layout/render-pass
创建与销毁的有序事件，寻找独立 replay 缺失的状态。

本轮新增 launcher/validator 单测通过；完整回归更新为 682 tests、
56 skipped、0 failures。Town10、同步 tick、RGB/LiDAR 及长期运行
仍 **BLOCKED**。

### 9.69 2026-09-28（本地）故障 cache 生命周期和快照取数对照

当前 `VULKAN_COMMANDWRAPPERS_ENABLE` 的默认值为 0，原有
`FWrapLayer` 不是当前客户端实际调用路径。本轮仅在
`VulkanPipeline.cpp` 中增加默认关闭的
`CarlaVulkanCacheLifecycle`：该文件的 cache 创建、销毁、合并、
取数以及 mixed 序列锁内的 graphics/compute driver enter/return
事件按顺序追加到 `cache-lifecycle.txt`。记录不增加 Vulkan 资源锁，
分析器要求事件连续、enter/return 配对、唯一故障调用与旧
driver-entry marker 的 thread/cache/kind 匹配；历史不完整时报错。
`VulkanChunkedPipelineCache.cpp` 的 cache create/get-data/destroy 已接入；
bindless descriptor/layout 和其它直接 Vulkan 路径正在单独记录，结论
仍仅适用于当前已观测路径。

同一 GB10 / NVIDIA 580.173.02 上三轮启动仍在编译器中 SIGSEGV：

| 运行目录（`artifacts/carla/`） | cache 观测结果 |
| --- | --- |
| `town10-gb10-rpc-20260928T075319Z-VxXQwN` | 启用 graphics cache snapshot 和 shader diagnostics，1497 个事件；故障 cache 存活、无销毁；此前有 27 个同 cache 重叠（涉及诊断用 `get_data`），**故障 compute 调用期间无重叠** |
| `town10-gb10-rpc-20260928T075611Z-WbnOdX` | 关闭 graphics cache snapshot 和 shader dumps，1285 个事件；故障 cache `0xfb6fea0` 存活、无销毁/合并/已观测重叠；唯一未返回 compute 为 `main_000009cc_71a7f7d2` |
| `gb10-device-fault-20260928T080152Z-wd1TxJ` | 低干扰 GDB 同一故障进程完整 device snapshot，SHA `b719168834c4b669107b90f4663c8e0f6b9b75798a885d4dfd4e7f3d8a5885cf`；1253 个 cache 事件，故障 cache `0x31f27ac0` 存活，故障期间无已记录的重叠，唯一未返回 compute 为 `main_00000290_c1eca148`，栈在 NVIDIA compiler |

第二轮在关闭会改变取数频率的诊断选项后仍崩溃，说明较早看到的
诊断取数重叠不是这类故障的必要条件；单个 cache 的提前销毁也
未在已接入的路径出现。**这不是“所有共享 cache 访问无竞争”的
证明**：其他文件中的 Vulkan 调用、真正的 shader module/layout/
render-pass 延迟销毁和同进程 driver 内部状态仍未完整记录。
下一步优先扩大对象生命周期覆盖，再根据可复现的因果对照实施修复；
Town10、同步 tick、RGB/LiDAR、稳定性门禁继续 **BLOCKED**。

本轮 ARM64 Development 编译重链成功，独立 stage binary 与构建
产物 SHA 均为 `609d49d3aab8006816bb605c7bd1fe656decfad84068a265a83b0d00b3222465`。
`make test-local` 为 693 tests、56 skipped、0 failures；脚本语法、
JSON 格式与本轮改动的 diff 检查通过。

### 9.70 2026-09-28（本地）deferred deletion 与故障对象存活验证

UE 中 `FVulkanShaderModule`、`FVulkanLayout`、`FVulkanRenderPass`
析构时通过 `FDeferredDeletionQueue2::EnqueueResource` 排队，实际
`vkDestroyShaderModule`、`vkDestroyPipelineLayout`、
`vkDestroyRenderPass` 在 `VulkanMemory.cpp::ReleaseResources` 执行。
为了不把“已排队”误当成“已销毁”，本轮在已启用的
`CarlaVulkanCacheLifecycle` 诊断下分别记录实际创建、受队列锁保护的
enqueue、真正 driver destroy 的 enter/return；继续保持默认关闭。
分析器将故障 marker 的 module/layout（graphics 时也包括 render pass
与各 stage module）与已记录的 live/queued 集合配对，缺失或已经销毁
时输出异常，不冒充存活。仅覆盖已接入的创建路径和
`FDeferredDeletionQueue2`，bindless 等直接创建/销毁路径尚未全覆盖。

GB10 / NVIDIA 580.173.02 上的证据：

| 证据目录（`artifacts/carla/`） | 观察 |
| --- | --- |
| `town10-gb10-rpc-20260928T083820Z-lrEvrF` | graphics 632/632、compute 4/3；2493 事件，唯一未返回 B075 compute；cache、module、layout 均 live 且未 queued，故障期间无已记录 cache overlap，启动仍 SIGSEGV |
| `gb10-device-fault-20260928T084351Z-E45puG` | 同一故障进程的完整 device snapshot 与 NVIDIA compiler SIGSEGV，graphics 638/638、compute 4/3；2527 事件，唯一未返回 B075，cache/module/layout live 且未 queued，没有观测到故障期间 cache overlap |
| `town10-gb10-rpc-20260928T085350Z-iP6Een` | 修正 enqueue 记录顺序后的最终 binary（SHA `55e88d97bfaf5486205058f03217a467171c9d9632ff7d8475644cce630822eb`），graphics 639/639、compute 4/3，2539 事件，唯一未返回 `main_00000104_7123e56c`；故障 cache/module/layout live 且未 queued，依然 SIGSEGV |

故障 entry 会变化；三轮记录不能证明 NVIDIA driver 自身有缺陷，
也不能证明所有 Vulkan 对象与全部 API 均无生命周期问题。
三份故障进程的 device snapshot 去除 PID、scope、ICD 路径等
运行元数据后规范化哈希一致：`ce1a7cb23d692a390097c8200f580b0ad74916eca5e77fbefb56e5ad5c9df261`。
当前 device feature/extension 差异、故障 cache 的已观测提前销毁、
故障 module/layout 的已观测提前销毁都不是这些运行的充分解释。

下一步将 bindless 创建路径及 driver 其它内存/调用状态纳入对照，
再设计能正反复现的最小干预；不把
诊断串行锁或单次独立 replay 当产品修复。ARM64 Development
增量编译成功，`make test-local` 691 tests、56 skipped、0 failures，
改动文件 diff、JSON 和 shell 语法检查通过。Town10/RGB/LiDAR
验收仍 **BLOCKED**。

### 9.71 2026-09-28（本地）descriptor/bindless 生命周期覆盖

在生命周期 recorder 上补充 `VulkanDescriptorSets.cpp`、
`VulkanRHI.cpp` 和 `VulkanMemory.cpp` 的 descriptor-set layout
创建、延迟入队和实际销毁；bindless pipeline layout 创建/销毁也
记录到同一事件流。该改动默认关闭，未改变 descriptor flags、bindless
策略或产品路径。

最终 GB10 复验目录：
`artifacts/carla/town10-gb10-rpc-20260928T144007Z-4BrcUZ/`。

- graphics 639/639，compute 4/3，唯一未返回 compute 为
  `main_00001918_0e290505`；
- 生命周期文件共 2758 个事件，分析退出 0；
- 故障 cache、module、pipeline layout 均 live，未 queued，故障期间
  无已记录 cache overlap；
- descriptor lifecycle 事件已出现并被 parser 接受；
- 本次 graphics layout 仍为 `bindless=0`，所以只验证覆盖接线，
  不是 bindless-enabled 行为验收；
- 进程仍在 NVIDIA compiler SIGSEGV。

结论：descriptor/bindless 生命周期记录已接通，但当前 Town10 运行
没有走 bindless pipeline layout，不能用它排除 bindless 专用路径。
下一步处理实际 bindless 开启时的 descriptor-buffer/layout 状态，以及
其它未接入 Vulkan API；产品验收继续 **BLOCKED**。

### 9.72 2026-09-28（本地）null pipeline-cache 必要性对照

新增默认关闭的 `CarlaVulkanSubmitNullPipelineCache`。在 mixed
serialization、pipeline history 和 lifecycle 同时显式开启时，保留
UE 原始 cache handle 和生命周期，但将提交给
`vkCreateGraphicsPipelines` / `vkCreateComputePipelines` 的 cache
改为 `VK_NULL_HANDLE`；driver-entry 同时记录 `cache` 与
`submitted_cache`。干预样本不能被 graphics replay generator 当作
未修改输入。

实际 GB10 对照：
`artifacts/carla/town10-gb10-rpc-20260928T143440Z-6WOYex/`。

- graphics 670/670、compute 4/3；
- `cache_intervention_count=4`，唯一未返回 compute 的 marker 明确为
  `submitted_cache=0x0`；
- 原始 cache 仍 live，module/layout 仍 live，故障期间没有已记录的
  cache overlap；
- 进程仍在 `libnvidia-glvkspirv.so.580.173.02` 中 SIGSEGV。

结论：把提交给 driver 的 pipeline cache 置空仍不能消除崩溃，
因此“非空 pipeline cache 是必要崩溃条件”不成立。该对照修改了
driver 输入，不能作为产品修复或 exact replay；Town10/RGB/LiDAR
验收继续 **BLOCKED**。本轮完整回归为 693 tests、56 skipped、
0 failures。

### 9.73 2026-09-29（本地）allocator 负向对照与当前输入差异收敛

driver-entry 现在记录实际提交给 Vulkan 的
`VkAllocationCallbacks*` 地址和 `pUserData`。最新 GB10 运行：
`artifacts/carla/town10-gb10-rpc-20260929T015350Z-Dw2UCr/`。

故障 compute `main_0000142c_a6b37050` 的 marker 为：

- 原始 cache 与 submitted cache 均为非空；
- `allocator=0x0`；
- `allocator_user_data=0x0`；
- graphics driver-entry 也观察到相同 NULL allocator。

这与独立 graphics/compute replay 的 NULL allocator 一致，因此 allocator
不是当前完整 UE 与独立 replay 的差异。该运行仍在
`libnvidia-glvkspirv.so.580.173.02` SIGSEGV；cache/module/layout
生命周期分析仍显示对象存活、无故障期间 cache 重叠。

结合此前 null-cache 对照，当前已排除的充分条件包括：固定 shader
输入、layout、render-pass、device snapshot、初始 cache、allocator、
提交非空 cache 本身，以及已观测的 cache/module/layout 提前销毁。
剩余差异集中在实际 bindless-enabled 路径、未接入的 Vulkan API、
完整 UE 调用顺序、driver 内部编译上下文和可能的内存/状态污染。
本轮完整回归为 693 tests、56 skipped、0 failures；产品验收继续
**BLOCKED**。

### 9.74 2026-10-01（本地）Vulkan validation layer 对照与 driver 串行化假设

**先结掉 9.71 遗留的 bindless 问题。** 当前 GB10 Town10 运行日志中始终存在：

```text
LogRHI: Warning: Bindless descriptor were requested but NOT enabled
        because of insufficient property support.
```

该分支来自 `VulkanDescriptorSets.cpp` 的 `VerifySupport()`：`VK_EXT_descriptor_buffer`
存在（vulkaninfo 计数 1），但
`maxDescriptorBufferBindings` / `maxResourceDescriptorBufferBindings` /
`maxSamplerDescriptorBufferBindings` / descriptor-buffer memory type 四项要求之一
在 GB10 上不成立。也就是说**运行期根本没有走 bindless/descriptor-buffer 路径**，
`bindless=0` 不是 cook 与 runtime 的配置漂移，而是设备能力判定结果。
9.71 的“覆盖 bindless 实际启用后的专用路径”因此不是本次崩溃的必要项，
该项按“当前配置下不适用”结项，不作为排除证据。

**本轮新增默认关闭的 validation layer 对照。**
`scripts/carla/fetch-vulkan-validation-layer.sh` 把固定版本的 Khronos
validation layer（jammy `1.3.204.1-2`，deb sha256
`dbc3a59a…a316f`，api_version 1.3.204、GLIBC ≤ 2.32，与镜像内
libvulkan1 1.3.204 匹配）解包到 `data/cache/vulkan-validation-layer`；
compose 将该目录只读挂载到 `/opt/vulkan-validation-layer`；probe 仅在显式
`CARLA_RUNTIME_VULKAN_VALIDATION=1` 时导出 `VK_LAYER_PATH`/`LD_LIBRARY_PATH`
并追加 `-vulkanvalidation=1`，同时把层清单与文件哈希写入
`vulkan-validation.json`。默认值为 0，产品启动命令不变。

同一台 GB10、同一 staged client、连续运行的结果：

| 运行目录 | validation | 结果 |
| --- | --- | --- |
| `town10-gb10-rpc-20261001T040959Z-8xFK8j` | 1 | **PASS**：world 就绪、20 tick、endpoint gate PASS，stop code 143 |
| `town10-gb10-rpc-20261001T041158Z-omIeYU` | 1 | **PASS**：同上 |
| `town10-gb10-rpc-20261001T041227Z-hH7BLh` | 0 | FAIL：`libnvidia-glvkspirv.so.580.173.02` SIGSEGV |
| `town10-gb10-rpc-20261001T041045Z-PbzaUn` | 0 | FAIL：同一 SIGSEGV |

即 **2/2 PASS vs 2/2 SIGSEGV**；validation=1 的日志中
`SIGSEGV|Critical error` 计数为 0。

validation layer 自己的报错都是旧层误报，不构成本轮根因证据：未知 pNext
结构体是 `VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_TRACING_POSITION_FETCH_FEATURES_KHR`
（1000481000），另加 `VkPhysicalDeviceProperties2` 以及
`VkMemoryBarrier2`/`VkImageMemoryBarrier2` 的 stage-mask VUID —— 1.3.204 层
不认识 1.4 头文件的结构体。

**解释与边界。** VVL 在其 dispatch 中对每个 entry point 加锁，等价于把整条
driver 调用流串行化。此前只对 `vkCreateGraphicsPipelines` /
`vkCreateComputePipelines` 做过串行化（仍崩溃），因此差异更可能来自**其它被并发
使用的入口**（`vkCreateShaderModule`、layout/descriptor 创建、
`vkGetPipelineCacheData`、`vkMergePipelineCaches`），而不是 pipeline 创建本身。
同时 layer 也改变时序，纯时序效应尚未排除。这是相关性证据，**不是精确根因**；
带诊断层的 PASS **不是产品验收**，该层不进入产品启动命令，Town10/RGB/LiDAR
验收继续 **BLOCKED**。

**同时修正 harness 缺陷。** 四个 probe 的端口预检
（`probe-town10-gb10-runtime.sh`、`probe-town10-nullrhi-rpc.sh`、
`probe-ue-vulkan-fault.sh`、`probe-ue-vulkan-memory-trace.sh`）未设置
`SO_REUSEADDR`，前一次运行的 TIME_WAIT 连接会让紧接其后的运行以
`EADDRINUSE` 失败（exit 1，且不产生 run 目录）。已统一改为带
`SO_REUSEADDR` 的预检，端口被真实监听时仍会失败。

本轮完整回归为 696 tests、56 skipped、0 failures。

### 9.75 2026-10-01（本地）driver 调用串行化对照：9.74 的串行化解释被否

9.74 把 validation layer 的效果归到「VVL 对每个 entry point 加锁」。本轮把该
假设做成可判定的对照：新增默认关闭的 `CarlaVulkanSerializeDriverCalls`
（probe 侧 `CARLA_GB10_SERIALIZE_DRIVER_CALLS`）。它与已有诊断共用**同一把**
锁，并把覆盖范围从 pipeline 创建扩展到：

- `vkCreateShaderModule`（`VulkanShaders.cpp`，1 处）
- `vkCreatePipelineLayout`（`VulkanShaders.cpp`、`VulkanDescriptorSets.cpp`，2 处）
- `vkCreateDescriptorSetLayout`（`VulkanDescriptorSets.cpp` 3 处、`VulkanRHI.cpp` 1 处）
- `vkMergePipelineCaches` / `vkGetPipelineCacheData`（`VulkanPipeline.cpp`，2 处）
- `vkAllocateDescriptorSets`（`VulkanPendingState.cpp` 2 处）
- `vkUpdateDescriptorSets`（`VulkanPendingState.cpp` 1 处、`VulkanPipelineState.cpp` 2 处）
- `vkCreateGraphicsPipelines` / `vkCreateComputePipelines`（进入同一把锁）

诊断接口在 `VulkanResources.h`；Shipping 构建保留调用点但为空实现，
不改变产品行为。

以增量 ARM64 Development 重新编译（`carla-ue-20261001T044114Z-6hyoAk`、
`carla-ue-20261001T044514Z-p9BhJl`），并把新单体 client 覆盖进 staged client
（`Binaries/LinuxArm64/CarlaUnreal` SHA256 由 `6acec2a3…` 变为
`bebd701e…`，随后扩展覆盖后为 `cb734133…`；资产与 shader cache 未变）。

结果：

| 轮次 | 运行 | 配置 | 结果 |
| --- | --- | --- | --- |
| 1 | `town10-gb10-rpc-20261001T044409Z-Ptfd6h` | 基线 | FAIL：`libnvidia-glvkspirv.so.580.173.02` SIGSEGV |
| 1 | `town10-gb10-rpc-20261001T044425Z-47jylw` | 串行化（不含 descriptor alloc/update） | FAIL：同一 SIGSEGV |
| 2 | `town10-gb10-rpc-20261001T044745Z-izx6GL` | 基线 | FAIL：同一 SIGSEGV |
| 2 | `town10-gb10-rpc-20261001T044802Z-EL7gKC` | 串行化（全部上述入口） | FAIL：同一 SIGSEGV |

两次串行化运行都确认生效：`server-command.json` 含
`-CarlaVulkanSerializeDriverCalls`，且 `server.log` 出现一次
`CARLA diagnostic: serializing instrumented Vulkan driver calls`。
即覆盖范围内所有并发入口都被串行化，进程仍在同一地址 SIGSEGV。

**结论与边界。** 9.74 中「validation layer 之所以让 gate 通过，是因为它把
driver 调用流串行化」这一解释**不成立**。`vkCreateShaderModule`、
descriptor/layout 创建、cache merge/get-data、descriptor allocate/update 与
pipeline 创建共处一把锁仍崩溃，说明故障不依赖这些入口之间的并发。
因此 9.74 的 PASS 应归因于 layer 的**校验行为、instance/device 创建链差异、
时序或内存布局扰动**之一，而不是互斥。这是负向对照，不改变产品结论：
Town10/RGB/LiDAR 验收继续 **BLOCKED**，`CarlaVulkanSerializeDriverCalls`
不作为修复方案。下一步应转向「layer 存在但关闭校验」「不装 layer 只加
`-vulkandebugutils`」这类能把「校验」与「时序/内存」分开的对照。

本轮完整回归为 696 tests、56 skipped、0 failures。

### 9.76 2026-10-01（本地）driver 设备创建退化与 client abort 归档缺口

按 9.75 的下一步做 `-vulkandebugutils` 对照（不装 validation layer，只让 UE
激活 `VK_EXT_debug_utils` 并安装 debug messenger）。代码上这确实是
`-vulkanvalidation` 除「装载 layer」之外的**唯一**副作用：全仓库
`GRHIIsDebugLayerEnabled` 除宏定义外无其它使用者，`VulkanLayers.cpp` 里
`bUseVulkanValidation` 只额外走到 `ActivateDebuggingExtension(VK_EXT_debug_utils)`；
`VK_EXT_validation_features` 只在 `-vulkanvalidation=2` / bestpractices /
debugsync 时才激活，本轮不涉及。probe 新增默认关闭的
`CARLA_RUNTIME_VULKAN_DEBUG_UTILS`（传 `-vulkandebugutils`）。

**对照未能取得有效结果，因为环境在 04:48 之后发生了另一件事。**
控制组、`-vulkandebugutils`、validation 三组各跑一次，**全部**在
world 就绪前失败，且 `server-stop-code.txt` 为 `1`（不是 139）：

| 运行 | 配置 | stop code |
| --- | --- | --- |
| `town10-gb10-rpc-20261001T051715Z-ogA1w4` | 基线 | 1 |
| `town10-gb10-rpc-20261001T051719Z-EsNet6` | `-vulkandebugutils` | 1 |
| `town10-gb10-rpc-20261001T051723Z-IF3Lat` | validation | 1 |

连续 4 次基线复现均为 `stop=1`。它不是 9.73 之前那个崩溃：04:47/04:48 的
同一份二进制（staged SHA256 `cb734133…`，现在仍然一致）会出现
`libnvidia-glvkspirv` SIGSEGV，而现在的失败发生在更早的 `vkCreateDevice`。

原因是 probe 一直只归档进程 stdout（`server.log`），而 UE 在
`RequestExit(1)` 前未 flush 的尾部只存在于它自己的日志里。读取
`Saved/Logs/CarlaUnreal.log` 得到明确原因：

```text
LogLinux: Warning: MessageBox: Cannot create a Vulkan device. Try updating
your video driver to a more recent version.
: Vulkan device creation failed:
FUnixPlatformMisc::RequestExit(bForce=true, ReturnCode=1, ...)
```

同一时间同一容器内 `vulkaninfo --summary` 仍可 5/5、3/3 成功创建（最小）设备，
说明这是**客户端的 device 创建请求在当前 GB10 驱动状态下被拒绝**，
而不是 GPU 完全不可用。04:10 与 04:47 的运行使用同一 binary/参数组合时
device 创建是成功的，因此这是**环境/驱动状态变化**，不是本轮代码改动。

**harness 缺口已修。** probe 现在会把
`${client_root}/Saved/Logs/CarlaUnreal.log` 归档为 run 目录下的
`client-ue.log`，把 `Cannot create a Vulkan device` /
`Vulkan device creation failed` 纳入 `server-diagnostics.txt`，并在
`decision.md` 里给出 `- Client abort:`（`none` / `device-creation` /
`shader-compiler-sigsegv`）。在此之前，「设备创建被拒」与「产品崩溃」在证据里
无法区分。

**结论与边界。** 9.75 之后计划的 debug-utils 对照目前**无法得出结论**；
9.74 记录的 validation=1 两次 PASS 也**不能在当前环境下复现**，必须当作
未复核的相关性证据。当前 gate 的任何 FAIL 只要
`Client abort: device-creation`，就不能归因于产品 shader 路径。下一步：等设备
创建恢复（或重置驱动）后重跑基线 / `-vulkandebugutils` / validation 三组，
再看 9.74 是否成立。Town10/RGB/LiDAR 验收继续 **BLOCKED**。

本轮完整回归为 697 tests、56 skipped、0 failures。

### 9.77 2026-10-01（本地）诊断对照入口与结果分类

9.75/9.76 之后需要反复重跑「基线 / `-vulkandebugutils` / validation /
driver-call 串行化」四组，并保证**设备创建被拒的运行不会被当成产品结论**。
新增 `scripts/carla/compare_gb10_vulkan_diagnostics.py` 与
`make carla-gb10-vulkan-comparison`：

- 按固定顺序跑四组，每组复用现有 `carla-town10-gb10-runtime` 目标，
  不复制 compose 契约；
- 读取每轮的 `decision.md`，用 PASS / FAIL / BLOCKED 三态分类：
  `Step: preflight` 失败或 `Client abort: device-creation` 归为 **BLOCKED**，
  只有真正走到产品渲染路径的失败才是 **FAIL**；
- **控制组先跑**：若控制组被 BLOCKED，其余组记为 **NOT-RUN** 并直接给出
  结论，避免在退化环境里白跑三轮；`RUN_ALL=1` 可强制跑完；
- 报告写入 README 约定的 `artifacts/<test-id>/<UTC时间>/`（`artifacts/` 属主为
  用户，而容器创建的 `artifacts/carla` 属主为 root，宿主机进程无法在其中新建
  目录），包含 `comparison.json`（含 client binary SHA256、每组 make 变量、
  run 目录、decision 字段）与 `summary.md` 表格；
- overall 为 `COMPARABLE` 时退出 0，否则退出 3。

当前实测（`artifacts/gb10-vulkan-comparison/20261001T084719Z/`）：

| Configuration | Status | Client abort | Stop code |
| --- | --- | --- | --- |
| control | BLOCKED | device-creation | 1 |
| debug-utils | NOT-RUN | - | - |
| validation | NOT-RUN | - | - |
| serialize-driver-calls | NOT-RUN | - | - |

即 9.76 的设备创建退化**仍在持续**，工具按设计把整轮判为 BLOCKED 并省掉了
三次无意义运行，没有把环境问题写成产品 FAIL。

工具本身有单元测试覆盖：分类规则、`summarize` 的「控制组必须先复现基线崩溃」
约束、容器路径到宿主机路径的映射，以及 make 目标接线。

### 9.78 2026-10-01（本地）四组对照实测：9.74 的 validation PASS 不可复现

设备创建退化在 08:47 之后自行恢复（`CONFIGS=control` 单独运行
`artifacts/gb10-vulkan-comparison/20261001T084759Z/` 已回到
`FAIL / shader-compiler-sigsegv / stop 139`），随即跑完四组完整对照：

| Configuration | Status | Client abort | Stop code | Run |
| --- | --- | --- | --- | --- |
| control | FAIL | shader-compiler-sigsegv | 139 | `town10-gb10-rpc-20261001T084822Z-lFzW5u` |
| debug-utils | FAIL | shader-compiler-sigsegv | 139 | `town10-gb10-rpc-20261001T084837Z-DaTdj2` |
| validation | FAIL | nvidia-driver-sigsegv | 139 | `town10-gb10-rpc-20261001T084853Z-YC9sIh` |
| serialize-driver-calls | FAIL | shader-compiler-sigsegv | 139 | `town10-gb10-rpc-20261001T084916Z-YvKrF2` |

报告：`artifacts/gb10-vulkan-comparison/20261001T084821Z/`，client binary
SHA256 仍为 `cb734133…`（与 04:47/04:48 两轮完全相同）。

三条结论：

1. **9.74 记录的「validation=1 时 gate PASS」不可复现。** 同一 binary、同一参数、
   同一容器路径，本次 validation 组 stop code 139 FAIL。9.74 的两次 PASS 因此
   必须降为**未能复核的历史观测**，不能作为 layer 有保护作用的证据。注意它与
   9.74 并非矛盾到「配置不同」：本轮的 layer 确实被装载
   （`-vulkanvalidation=1` 在 `server-command.json` 内，`vulkaninfo.log` 列出该层，
   `server.log` 有 12 条 Validation Error）。
2. **debug-utils 假设被否。** 只开 `-vulkandebugutils`（不装 layer）与基线完全
   一致：同样 `libnvidia-glvkspirv` SIGSEGV。因此 instance 的 debug-utils 扩展/
   messenger 不是机制。
3. **layer 改变的是崩溃位置，不是崩溃本身。** validation 组的崩溃落在
   `libnvidia-eglcore.so.580.173.02`（地址 `0x160`），而不是
   `libnvidia-glvkspirv`；也就是说它让进程走得更远（多跑若干秒、产生 12 条校验
   输出）但仍失败。这把 9.74 的 PASS 解释为**临界状态下的时序/内存扰动**，
   而不是「校验修好了什么」。

probe 的 `Client abort` 因此细化为
`device-creation` / `shader-compiler-sigsegv` / `nvidia-driver-sigsegv` / `none`，
避免把非编译器路径的驱动崩溃误记为 `none`。

边界：以上仍是环境相关的一次对照（每组各 1 次），足以否掉「validation 稳定地
让 gate 通过」，但不足以确定 eglcore 崩溃与 glvkspirv 崩溃是否同源。
Town10/RGB/LiDAR 验收继续 **BLOCKED**。

本轮完整回归为 712 tests、56 skipped、0 failures。

### 9.79 2026-10-01（本地）方差复核、崩溃符号化与真正的 VUID 违规

**方差。** 追加三轮 `CONFIGS=control,validation`（
`artifacts/gb10-vulkan-comparison/20261001T090723Z/`、`…T090759Z/`、`…T090834Z/`）。
累计：control 在环境正常时 **5/5 FAIL**（`shader-compiler-sigsegv`、stop 139），
另有 1 次 `BLOCKED/device-creation`；validation **4/4 FAIL**（stop 139），
其中 3 次 `nvidia-driver-sigsegv`、1 次因当时 probe 还没有该分类而记为 `none`。
也就是说 9.74 的两次 PASS 在此之后从未再现，**validation 不是修复**。

**崩溃符号化（新能力）。** staged client 用 `-NoDumpSyms` 构建，UE 崩溃处理
只能打印 `CarlaUnreal!UnknownFunction(0x...)`。新增
`scripts/carla/symbolize_client_crash.py` 与
`make carla-symbolize-client-crash RUN_DIR=…`：取 `client-ue.log` 中
`Critical error` 之后的帧，用 ARM64 Development 的 `CarlaUnreal.debug` 与
`llvm-symbolizer-18` 还原。两个关键点：**必须用 UE 打印的第一个（绝对 PC）
地址**，括号里的模块内偏移与 debug 二进制不匹配，会得到完全无关的函数名；
`Critical error` 之前的 `[Callstack]` 属于无关的 ensure，必须排除。

对照组（`town10-gb10-rpc-20261001T090835Z-1DcLyl`，无 validation）内层帧：

```text
libnvidia-glvkspirv.so.580.173.02  (7 帧, 崩溃点)
FVulkanPipelineStateCacheManager::CreateComputePipelineFromShader(...)
FVulkanPipelineStateCacheManager::GetOrCreateComputePipeline(...)
FVulkanPipelineStateCacheManager::RHICreateComputePipelineState(...)
RHICreateComputePipelineState(...)
FCompilePipelineStateTask::CompilePSO(...)
TGraphTask<FCompilePipelineStateTask>::ExecuteTask()
LowLevelTasks::FScheduler::WorkerLoop(...)
```

即 PSO 预编译工作线程上的 `vkCreateComputePipelines`，与 9.64–9.75 的结论一致。

validation 组（`town10-gb10-rpc-20261001T090849Z-rJsZfI`）内层帧：

```text
libnvidia-eglcore.so.580.173.02  (崩溃点, 地址 0x160)
libc.so.6 ×2
VulkanRHI::CheckDeviceFault(FVulkanDevice*)
VulkanRHI::VerifyVulkanResult(VkResult, ...)
VulkanRHI::FFenceManager::WaitForFence(...)
FVulkanCommandBufferManager::WaitForCmdBuffer(...)
FVulkanDynamicRHI::RHIGetRenderQueryResult(...)
MeasureLongGPUTaskExecutionTime(...)
```

且崩溃前一行是 `LogVulkanRHI: Error: Result failed, VkResult= -4`
（`VK_ERROR_DEVICE_LOST`）。对照组的崩溃前**没有** `VkResult` 失败。

因此两者的关系是：**同一个底层驱动故障，被观察到的位置不同**——
无 layer 时直接在工作线程的 shader 编译里 SIGSEGV；有 layer 时编译侧先变成
device lost，随后 UE 的 `CheckDeviceFault` 诊断路径在驱动内崩溃。
这解释了「layer 让进程走得更远」而不是「layer 修好了什么」。

**顺带发现真正（非误报）的 VUID 违规。** 同一次 validation 运行里出现：

- `VUID-vkCmdDrawIndexed-None-02699` / `VUID-vkCmdDraw-None-02699`：
  draw 使用了**从未被 `vkUpdateDescriptorSets()` 写过**的 descriptor；
- `VUID-VkImageMemoryBarrier2-srcStageMask-03934/03935`、
  `VUID-VkMemoryBarrier2-srcStageMask-03934/03935`：stage mask 里用了
  `MESH_SHADER_BIT_NV` / `TASK_SHADER_BIT_NV`，而对应 feature 并未启用。

这些与 9.74 列出的「未知 pNext 结构体」误报不同，是版本无关的真实误用，
且「使用未初始化 descriptor」本身就可能造成 GPU fault。这是目前最值得追的
新线索，但**尚未证明**它与 shader 编译崩溃同源。

本轮完整回归为 721 tests、56 skipped、0 failures。

### 9.80 2026-10-01（本地）draw-time descriptor 违规与厂商证据包

**descriptor 违规的分布。** 把 validation 组全部 4 次失败运行与 9.74 的 2 次
通过运行放在一起比较：

| 运行 | 结果 | VUID 组数 | draw-time descriptor 违规 |
| --- | --- | --- | --- |
| `…T040959Z-8xFK8j` | PASS | 8 | **无** |
| `…T041158Z-omIeYU` | PASS | 8 | **无** |
| `…T084853Z-YC9sIh` | FAIL | 12 | 有（各 3 次） |
| `…T090737Z-JLLRbI` | FAIL | 12 | 有 |
| `…T090813Z-GBKZot` | FAIL | 12 | 有 |
| `…T090849Z-rJsZfI` | FAIL | 12 | 有 |

6/6 分离。两条消息在 4 次失败运行里完全一致：`binding #0 index 0`，
`vkCmdDraw` 与 `vkCmdDrawIndexed` 各一条，只是每次 run 的 descriptor set
handle 不同（说明是固定的引擎路径，不是随机状态）。时间线：
09:09:02 报出违规 → 09:09:07 `VulkanMemory.cpp:4704` 返回
`VK_ERROR_DEVICE_LOST` → 09:09:08 在 `CheckDeviceFault` 中崩溃。

**边界很重要**：这是**相关性**（n=6），且只在装了 validation 的诊断配置里
可见。它不能解释对照组的编译期 SIGSEGV（那是宿主侧驱动编译器崩溃，与 GPU
侧 descriptor 内容无关）。目前应把 GB10 故障当成**两个候选缺陷**：
宿主侧 `vkCreateComputePipelines` 的编译器 SIGSEGV，以及引擎在绑定
「从未写过」的 descriptor 后仍发起 draw。

**厂商证据包生成器。** 新增 `scripts/carla/collect_gb10_driver_report.py` 与
`make carla-gb10-driver-report RUN_DIRS="<run dir> …"`。它**不重新跑任何 gate**，
只从已有 run 目录汇总：环境与驱动版本（取自 `vulkaninfo.log`）、每个 run 的
decision 字段、符号化崩溃栈（driver 崩溃点 + 内层 CarlaUnreal 帧）、validation
发现、已排除项清单，以及**缺失证据清单**（找不到的项显式列出，不静默省略）。
输出 `artifacts/gb10-driver-report/<UTC>/{report.md,report.json}`。

首次生成：`artifacts/gb10-driver-report/20261001T091726Z/`，`missing=none`，
包含 driver `580.173.02`、client binary `cb734133…`、两次 PASS 参照、
两份符号化栈（glvkspirv 与 eglcore）。

本轮完整回归为 729 tests、56 skipped、0 failures。

### 9.81 2026-10-01（本地）descriptor 违规的调用点与规模

为了拿到 VUID 只给对象句柄、不给调用点的问题，在 UE 的 Vulkan debug messenger
里加了默认关闭的 `-CarlaVulkanValidationStackTrace=<子串>`：命中该子串的校验消息
会在**发起调用的线程**上 dump 一次调用栈（放在 `bPrintMessage` 去重判断之后，
因此去重不会吞掉栈）。probe 侧 `CARLA_RUNTIME_VULKAN_VALIDATION_STACK_TRACE`。
`symbolize_client_crash.py` 增加 `--mode validation-call-site`（
`make carla-symbolize-client-crash RUN_DIR=… MODE=validation-call-site`），
处理 UE `StackWalkAndDump` 的 `LogCore: 0x… Dladdr:` 格式。

实测（`town10-gb10-rpc-20261001T121623Z-lCKbre`，validation + `02699` 子串）：

- 捕获 **5723** 次调用栈：`VUID-vkCmdDrawIndexed-None-02699` 5574 次、
  `VUID-vkCmdDraw-None-02699` 149 次；
- 首条调用点符号化为
  `FVulkanCommandListContext::RHIDrawIndexedPrimitive`
  （`VulkanCommands.cpp:736`）→ `FRHICommand<...>::ExecuteAndDestruct`
  → RHI 线程，即**通用 RHI draw 路径**，不是 Carla 专用代码；
- 但日志里去重后只出现 **2 条**消息、**2 个** descriptor set 句柄
  （`0x6373…`、`0x63de…`）——也就是**同一组 2 个 set 被几乎每一帧的每次 draw
  复用**，而 binding #0 从未被写过。

**结论（下调这条线索）。** 「97% 的 draw 都用了未初始化的 descriptor」如果为真，
在任何驱动上都该大面积出错，因此更可能是 **UE 的绑定模式与 VVL 1.3.204 的
追踪语义不一致**（例如 descriptor 由该层未覆盖的路径写入），而不是一个
GB10 特有缺陷。它也无法解释对照组的宿主侧编译期 SIGSEGV。因此在拿到
Lavapipe（Mesa + 同版本层）对照之前，**不应**把它当作根因候选继续投入。

新增能力本身保留：`-CarlaVulkanValidationStackTrace` 可以把任意 VUID
（含 barrier stage mask 等其他条目）的调用点一次性符号化，是通用的排障工具。

本轮完整回归为 732 tests、56 skipped、0 failures。

### 9.82 2026-10-01（本地）InputSettings ensure 根因与修复

清单第 7 项的第一条：每次运行都会出现两次

```text
Ensure condition failed: Class.IsValid()
  [Engine/.../UserInterface/InputSettings.cpp:514 / :507]
```

对应 `UInputSettings::GetDefaultPlayerInputClass()` /
`GetDefaultInputComponentClass()` 中的 `ensureMsgf(Class.IsValid(), ...)`，
随后回落到 `UPlayerInput` / `UInputComponent`。

**根因链（逐段验证，不是推断）：**

1. `DefaultInput.ini` 指向 `/Script/EnhancedInput.EnhancedPlayerInput` 与
   `/Script/EnhancedInput.EnhancedInputComponent`（UE5 默认值）。
2. `CarlaUnreal.uproject` 设置了 `"DisableEnginePluginsByDefault": true`，
   于是 `FPlugin::IsEnabledByDefault()` 只在
   `bAllowEnginePluginsEnabledByDefault` 为真时才启用引擎插件——
   而该标志被项目描述符关掉了，所以**只有 .uproject 里显式列出的引擎插件会被挂载**。
   运行日志正好印证：挂载集合 = `ChaosVehiclesPlugin`、`OnlineBase`、
   `OnlineServices`、`OnlineSubsystem`、`OnlineSubsystemUtils`、
   `ProceduralMeshComponent`、`SunPosition`，与显式列表完全一致。
3. EnhancedInput 不在该列表中 → 不挂载 → `/Script/EnhancedInput.*` 无法解析。
4. 实测把它加进 .uproject 也不行：`LogPluginManager: Error: Unable to load
   plugin 'EnhancedInput' ... The missing dependency is DataValidation`。
   `EnhancedInput.uplugin` 声明依赖 `DataValidation`，而后者位于
   `Engine/Plugins/Editor/DataValidation`，**是编辑器插件**，运行时构建不可能带上。
   因此在这个项目配置下 EnhancedInput 根本不可用。
5. 结论：正确的最小修复是让配置与可用的插件集合一致，而不是扩大插件集合。

**修复**：`Unreal/CarlaUnreal/Config/DefaultInput.ini` 的两行改为

```ini
DefaultPlayerInputClass=/Script/Engine.PlayerInput
DefaultInputComponentClass=/Script/Engine.InputComponent
```

该文件由 cook 生成且属主为 root，宿主机进程无法写入，因此改动通过
`carla-build` 容器落到 fork 源树（`git -C third_party/carla diff` 可见，
纯两行）。已 staged 的 client 同步做了同样替换。

**验证**（NullRHI gate，因为它能跑到输入设置初始化而不被 Vulkan 崩溃打断）：

| 配置 | `Class.IsValid` 次数 | gateway |
| --- | --- | --- |
| 修复前 | 4（2 次 ensure 各 2 行） | PASS |
| 修复后 | **0**，且无 `InputSettings.cpp` 报错 | PASS（`town10-nullrhi-rpc-20261001T144610Z-iv3gBd`） |

**边界**：这只消除 `InputSettings` ensure 并让输入回落到引擎类；Carla 插件源码
不引用 EnhancedInput（已 grep 确认），同文件中的 `+AxisMappings` 也是旧输入系统
的映射，所以行为与配置原本就一致，不构成功能删减。要真正启用 EnhancedInput
必须同时提供编辑器插件，本仓库不做。

清单第 7 项剩余项的裁定见 9.83 与 9.84。

本轮完整回归为 741 tests、56 skipped、0 failures。

### 9.83 2026-10-02（本地）Material::Serialize 诊断探针改为 opt-in

第 7 项的「日志噪声」查下来是自造的。以 `town10-nullrhi-rpc-20261001T144610Z-iv3gBd`
为样本，一次运行共 248 条 `Warning:`，其中 **239 条**来自同一族：

| `Warning:` 家族 | 条数 | 归属 |
| --- | --- | --- |
| `Material::Serialize post-super / pre-shadermaps ... tell=` | 239 | fork 自己的 `Materials/Material.cpp` |
| 其余（`Setting`、`OSS`、`Additional` 等） | 9 | 引擎/内容 |

两条 `UE_LOG` 是 2026-09-21 的 fork 提交 `85a04a40b` 带进来的（当时排查 cooked
material 流偏移），提交后一直以 Warning 级别无条件打印，把 gate 日志淹掉了。

处理方式是**门控而不是删除**——它们在 `tell` 偏移上仍是唯一的同轮证据：

```cpp
int32 GCarlaLogMaterialSerialize = 0;
static FAutoConsoleVariableRef CVarCarlaLogMaterialSerialize(
	TEXT("carla.LogMaterialSerialize"), GCarlaLogMaterialSerialize,
	TEXT("If true, logs material serialization stream offsets while loading materials."),
	ECVF_Default);

static bool ShouldLogCarlaMaterialSerialize()
{
	static const bool bFromCommandLine = FParse::Param(FCommandLine::Get(), TEXT("CarlaLogMaterialSerialize"));
	return bFromCommandLine || GCarlaLogMaterialSerialize != 0;
}
```

两处调用点改为 `if (Ar.IsLoading() && ShouldLogCarlaMaterialSerialize())`。开关
沿用仓库既有惯例：CVar 仿 `carla.AllowEditorContentInServerBuilds`，命令行仿
`-CarlaVulkanValidationStackTrace`。默认 0，stock 行为不变。

**边界**：这是日志门控，不是根因修复。证据分三层：

1. **静态**：`tests/test_carla_engine_log_hygiene.py` 5 例（探针仍在、两处都有
   门控、默认关闭、两个开关可达、三个 include 已声明）。
2. **编译**：`make carla-ue-build` → `PASS`（`artifacts/carla/carla-ue-20261002T015920Z-LEqwrd`，
   增量 119 s）。新二进制中 `carla.LogMaterialSerialize` 与
   `CarlaLogMaterialSerialize` 均以 UTF-16 字面量存在，两条探针字符串仍在（未删除）。
   注意 Linux 下 `TEXT()` 是 UTF-16，用 `strings` 或 ASCII `grep` 都查不到，
   必须按 UTF-16 搜索。
3. **运行时 A/B（实测）**：把新二进制装回 staged client，前后各跑一次 NullRHI gate。

| 运行 | client 二进制 sha256 | `Warning:` | `Material::Serialize` | ensure | Endpoint |
| --- | --- | --- | --- | --- | --- |
| 改前 `…T020244Z-1kFig1` | `d561f54e…` | **248** | 239 | 1 | PASS |
| 改后 `…T020309Z-NIxrGF` | `a53f37d7…` | **8** | **0** | 1 | PASS |

改后剩下的 8 条为 `Setting` 6、`OSS` 1、`Failed` 1，与本项无关；唯一的 ensure 仍是
9.84 那条引擎侧 ensure。预测的「约 9」实测为 8。

**provenance 说明**：本次是把新编的 binary 复制进 staged client
（`cooked-client-full`），**没有**重跑完整 cook/stage；旧二进制保留在
`artifacts/carla/ue-binary-backup-20261002T020304Z/`（sha `d561f54e…`），且
「改前」那次运行的 `inputs.sha256` 记录的正是同一哈希，所以改前基线仍可复核。
完整 cook + stage 的规范化重建仍属清单第 9 项。

### 9.84 2026-10-02（本地）IsInGameThread ensure 归因，裁定不改引擎

清单第 7 项的另一条每次运行都会出现：

```text
Ensure condition failed: IsInGameThread()  [CoreRedirects.cpp] [Line: 1374]
FCoreRedirects can only be initialized on the game thread.
```

先改正计数：这是 **1 次 ensure 事件**（`grep -c '=== Handled ensure'` = 1），
不是 2 次——2 是它的日志行数（即时消息 + 带栈的 dump）。

调用链（逐段在源码中核对）：

| 步骤 | 位置 | 内容 |
| --- | --- | --- |
| ensure | `CoreRedirects.cpp:1374` | `FCoreRedirects::Initialize()` 首句 |
| 调用者 | `Obj.cpp:5409` | `InitUObject()` |
| 注册 | `CoreNative.cpp`（`StartupModule`） | `FCoreDelegates::OnInit.AddStatic(InitUObject)` |
| 触发 | `LaunchEngineLoop.cpp:6818` | `FEngineLoop::AppInit()` 中 `FCoreDelegates::OnInit.Broadcast()` |

关键在于这版引擎的 `IsInGameThread()` **已不再比较线程号**，而是比较任务标签
（`ThreadingBase.cpp:178`）：

```cpp
bool newValue = FTaskTagScope::IsCurrentTag(ETaskTag::EGameThread)
             || FTaskTagScope::IsRunningDuringStaticInit();
```

`ActiveTaskTag` 是 `thread_local`、初值 `EStaticInit`；`GuardedMain`
（`Launch.cpp:100`）在其作用域内装上 `EGameThread`。因此 ensure 触发只说明那一刻
标签既不是 `EGameThread`、也不是静态初始化线程上的 `EStaticInit`：这是**标签
记账**，不是 redirects 表被跨线程使用。`FCoreRedirects::Initialize()` 紧随其后
就有 `bInitialized` 早退与读写锁，调用本身线程安全。

`CoreRedirects.cpp`、`Obj.cpp`、`LaunchEngineLoop.cpp` 在 fork 中均为 pristine
（`git status` 为空），说明这是上游行为，不是我们的回归。

**裁定：接受为已知噪声，不打引擎补丁。** 为一行提示去改上游的标签时序，收益只有
一个日志行，代价是引入一个必须长期维护的引擎行为差异。基线就此固定为「已知
1 次」，任何**新增** ensure 才是信号。

**修正（2026-10-03）**：上面的「不是 redirects 表被跨线程使用」是**错的**。当时栈全是
`UnknownFunction`（staged client 无符号），所以只能按标签推断；用 `.debug` 二进制在 gdb 下
重跑后拿到了完全符号化的栈：

```text
#2 FCoreRedirects::Initialize()            CoreRedirects.cpp:1374   ← ensure
#3 FCoreRedirects::AddKnownMissing()       CoreRedirects.cpp:1718
#4 FLinkerLoad::AddKnownMissingPackage()   LinkerLoad.cpp:6701
#5 FAsyncPackage::CreateLinker()           AsyncLoading.cpp:6293
…
#14 FAsyncLoadingThread::Run()             AsyncLoading.cpp:5225
#15 FRunnableThreadPThread::Run()          PThreadRunnableThread.cpp:25
Thread 35 "FAsyncLoading"
```

即：这**确实是异步加载线程**上的 lazy init（第一次 `AddKnownMissing` 触发
`FCoreRedirects::Initialize()`），不是游戏线程。正确的说法是：**它真的是一次跨线程调用**，
只是 `FCoreRedirects` 本身用 `bInitialized` + 读写锁保证安全，而这条 ensure 是上游用来
标记“不希望在这里发生”的诊断。裁定（不改引擎）不变，但理由要改成「已知的异步线程
lazy init，操作本身安全」，而不是「不是跨线程调用」。

教训：栈没有符号时不要把「看不到调用者」写成结论；有符号后它直接推翻了推断。

### 9.85 2026-10-02（本地）image-layout 警告：无法定位，待澄清

清单第 7 项的第三条「image-layout 警告」在 GB10（`town10-gb10-rpc-*`）与
NullRHI（`town10-nullrhi-rpc-*`）两套日志里都**找不到**任何含 layout 的告警。
本轮已检查的日志族包括 `LogVulkanRHI`、`LogRHI`、`LogStreaming` 与全部
`Warning:` 行。该项不改写成一项工作，需要先确认它指的是哪条告警、来自哪次运行。

### 9.86 2026-10-02（本地）第 9 项起点：fork provenance 的派生与校验

清单第 9 项（干净镜像重建 + provenance 复现）先做可验证的第一步：把「fork 到底
是哪份代码」变成可派生、可校验的事实。

**发现一：探针消费的 provenance 记录是错的。**
`/artifacts/carla/cooked-server-full/runtime-provenance.json` 记录：

| 源 | 记录值 | 实际 |
| --- | --- | --- |
| carla | `aba1bb69…`（2026-09-21） | HEAD `b56ce1a3…`（2026-09-25） |
| ue | `525b5451…` | HEAD `525b5451…` ✓ |

即过去所有 NullRHI / GB10 运行归档的 provenance 里，carla 那一半指向一份**没有
产出过那个二进制**的提交。ue 的 revision 恰好对得上，但 ue 工作树上有 **92 个
tracked 修改**（fork 的 ARM64/editor-only 补丁与诊断插桩），所以「提交号对得上」
同样不是可信标识。

**发现二：两条 pin 与现状全部不符。**`g0-probe.sh` / `ue-setup.sh` 里的断言：

| 断言 | 内容 | 现状 |
| --- | --- | --- |
| g0-probe | `HEAD == CARLA_SOURCE_COMMIT`（`234caf5f…`，09-13） | **FAIL**（HEAD 领先 2 个提交） |
| g0-probe | `HEAD == CARLA_UE_COMMIT`（`791a451d…`，09-13） | **FAIL**（HEAD 领先 8 个提交） |
| ue-setup | `git diff --quiet HEAD`（工作树干净） | **FAIL**（92 个 tracked 修改） |

这两条是**引导用** pin（`ue-setup` 是首次克隆后跑 `Setup.sh` 的步骤），不是每次
运行的判据。但它们说明 pin 语义与本仓库 fork 的实际形态（带未提交修改的工作树）
已不匹配。本项**不改这两条 pin**：动它们需要先决定「fork 改动是提交进分支，还是
保留为 patch 再重新生成树」，属第 9 项后半段。

**修正（同日）：pin 漂移不是「无人管」的缺陷。**`config/carla/source.lock` 已经把它
作为漂移源管起来，`make carla-manifest` 每次捕获都会把 `source_lock_drift` 写进
manifest：

```json
{"lock_present": true, "drifted": ["carla", "unreal_engine"],
 "pins": {"carla": {"pin": "234caf5f…", "actual_head": "b56ce1a3…", "state": "drifted"},
          "unreal_engine": {"pin": "791a451d…", "actual_head": "525b5451…", "state": "drifted"}},
 "uncovered_lock_sources": ["autoware_*", "jetson_*", "nano_ros", "ros_bridge_legacy"]}
```

即「pin ≠ HEAD」是被**记录并复验**的既定状态。真正硬断言相等的是 `g0-probe.sh`
与 `ue-setup.sh` 这两个引导期脚本（`ue-setup` 还额外断言工作树干净），它们假设的
是一棵停在 pin 上的干净树，与当前 fork 的形态不同。要动的是这两个脚本的断言语义，
不是 `source.lock`。

另外，本仓库已有比 `runtime-provenance.json` 强得多的留痕机制：
`make carla-manifest` / `carla-manifest-verify`（`capture_build_manifest.py`，
schema 2）会记录三个仓库的 HEAD、分支、完整 porcelain 状态、相对 HEAD 的 binary
patch 与 SHA256、以及非忽略 untracked 文件的完整快照。9.86 修的是探针吃的那个弱
记录，两者不能互相替代。

**新增能力**：`scripts/carla/report_fork_provenance.py`（配 `make carla-fork-provenance`
与 `make carla-fork-provenance-verify`）。每个 fork 被标识为：

| 字段 | 含义 |
| --- | --- |
| `revision` | HEAD sha |
| `branch` / `subject` | 分支名与 HEAD 提交标题 |
| `tracked_dirty_files` | 已跟踪但被修改的文件数 |
| `untracked_files` | 未跟踪文件数 |
| `tracked_diff_sha256` | `git diff HEAD` 的 sha256（**这才是产出二进制的代码标识**） |

`--verify` 只校验录制文件**自己声明过**的字段，所以旧记录（只有 `location` 与
`revision`）能被校验而不是被拒绝；不一致时逐字段打印 `DRIFT` 并退码 1，一致时
输出 `PASS provenance-verified`。schema 仍为 `schema_version: 1`，
`check_carla_runtime.py` 要求的 `sources.{carla,ue}.{location,revision}` 保持兼容，
因此不破坏既有门禁。

**验证**：

| 步骤 | 结果 |
| --- | --- |
| `--verify` 旧记录 | `DRIFT carla.revision`，exit 1（`artifacts/carla-fork-provenance/20261002T073534Z/verification-before.txt`） |
| 重新派生并安装 | `carla b56ce1a3… tracked_dirty=1`、`ue 525b5451… tracked_dirty=92` |
| `--verify` 新记录 | `PASS provenance-verified` |
| 端到端 | `make carla-town10-nullrhi-rpc` → PASS（`…T073557Z-7bZ4SS`）；运行的 `endpoint/runtime-checks-*/provenance.json` 快照已含全部新字段，`inputs.sha256` 记录的哈希与线上文件一致 |

宿主与容器两处派生得到相同的 `tracked_diff_sha256`（`7493c747…` / `cc17c89b…`），
互为交叉校验。旧记录保留在
`/artifacts/carla/cooked-server-full/runtime-provenance.json.stale-aba1bb69`。

**边界**：这解决了「运行归档的 provenance 说不清是哪份代码」，**没有**解决「干净
重建」——镜像仍是既有镜像（未 `--no-cache` 重建），fork 改动仍在工作树里而不是
提交或 patch 里，现在的 `runtime-provenance.json` 描述的是一棵带 93 个未提交修改
的树：这是**对事实的记录**，不是可复现的输入。要真正复现，仍需先决定 fork 改动的
固化方式，再重建镜像。

### 9.87 2026-10-02（本地）manifest 捕获被临时目录卡住，已清除

清单第 9 项的「移除临时工具副本依赖」在这里第一次有了硬证据：`make carla-manifest`
**当时是失败的**：

```text
# artifacts/carla/build-manifests/20261002T090824Z-1bcba7f4832e/manifest.json
status = "FAIL"
errors = ["not a regular file: /repo/.codex-tmp/cooked-server-test/CarlaUnreal/Config"]
make: *** [Makefile:67: carla-manifest] Error 1
```

原因：manifest 的可重放契约要求「Git 规则之外的全部 untracked 条目都是常规文件」
（常规文件复制并哈希、目录递归、其余一律失败）。仓库根目录的 `.codex-tmp/` 里有
**9 个符号链接**，全部指向共享的 engine/content 树：

```text
.codex-tmp/cooked-server-test/{Engine,Config,CarlaUnreal/Config,Content/Internationalization}
.codex-tmp/cooked-server-test2/{Engine/Binaries,Engine/Platforms,Engine/Content/Internationalization,
                               CarlaUnreal/Config,.Engine-Config.old}
```

规模：3822 个条目、2665 个常规文件、约 2.2 GB；其中 `cooked-server-test2`（935 MB）
与 `cooked-server-test`（740 MB）是 staged cooked-server 副本。它既 untracked、又
未被 `.gitignore` 忽略，所以 manifest 必须处理它——而它无法被处理。

处理步骤：

1. **归档**：把与「构建产物拷贝」无关的部分（audit 9.27 引用的
   `carla-server-probe-final.log` 及同目录日志、两次 GDB 记录、`render-*/` 运行记录、
   `linux-texture-startswith.patch`、`neon-vcvtn-repro.cpp`、`engine-uplugin-list.txt`）
   复制到 `artifacts/codex-tmp-archive/20261002T090908Z/`（80 个文件、6.3 MB，含
   `SHA256SUMS` 与 `README.md`）。
2. **改引用**：audit 9.27 的日志路径改为归档路径。
3. **删除** `.codex-tmp/`（含两个拷贝产物，可由 cook +
   `scripts/carla/stage-arm64-cooked-server.sh` 再生；规范 stage 仍在
   `artifacts/carla/cooked-server-full/`）。宿主用户删不掉 root 所有的残留，最后经
   `carla-dev` 容器以 `-v $PWD:/hostrepo:rw` 挂载清除。

结果：

| 指标 | 前 | 后 |
| --- | --- | --- |
| 非忽略 untracked 条目 | 2690 | **60** |
| 其中的非常规（符号链接）条目 | 9 | **0** |
| `make carla-manifest` | **FAIL**（`not a regular file`） | **PASS**（`…T091006Z-74444f371d46`） |
| `make carla-manifest-verify` | 无 manifest 可验 | **PASS** |

新 manifest 记录：`schema_version: 2`、`status: PASS`、三个仓库（project 60 个
untracked、carla 6、ue 5）各自的 binary patch 与 SHA256、以及 `source_lock_drift`
（carla/ue 均 `drifted`，见 9.86 的修正）。注意 `manifest.json` 里的
`key_files` 已覆盖 `Makefile`、compose、`source.lock`、变更台账与 `scripts/carla`。

新增回归：`tests/test_carla_scratch_hygiene.py`（2 例）直接断言这个前置条件——
非忽略的 untracked 条目里不允许出现符号链接或非常规文件，并附带一条反面检查，
防止「没有 untracked 条目」把断言变成空断言。这条测试本可以在 `.codex-tmp` 还在
时就抓住失败。

**边界**：清理解决的是「捕获能否完成」，不是「重建是否干净」。镜像仍未 `--no-cache`
重建；fork 的 93 个 tracked 修改仍未固化；manifest 只证明**被记录的输入**在复验时
一致——它自己的文档写明：不是 fresh clone，也不代表 Editor、Cook、RPC 或传感器通过。

### 9.88 2026-10-03（本地）干净镜像重建：先得让构建能开始

清单第 9 项的「干净镜像重建」在动手前先撞到一个更基础的问题。

**发现一：在用的镜像比 Dockerfile 落后 11 天。**

| 对象 | 时间 | 身份 |
| --- | --- | --- |
| `my-ad/carla-toolchain:arm64`（在用的） | 2026-09-14 | `sha256:3f9109bb…` |
| `images/carla-arm64/Dockerfile` 最后一次提交 | 2026-09-25（`5362519`） | — |
| 同文件的工作区修改 | 2026-09-26 | 未提交（`M`） |

即：现有全部证据所用的工具链镜像，**既不对应 HEAD 的 Dockerfile，也不对应工作区
的 Dockerfile**。

**发现二：manifest 不记录镜像身份。**`capture_build_manifest.py` 的 `environment`
记的是环境变量（`CARLA_*`、`DOTNET_ROOT`、`PATH` 等），没有镜像 ID 或 digest。所以
一份 manifest 无法回答「这是哪个工具链产出的」。

**修复**：manifest 提升到 schema 3，新增必填字段 `toolchain_image`：

```json
"toolchain_image": {"reference": "my-ad/carla-toolchain:arm64",
                    "id": "sha256:3f9109bb…"}
```

由 `make carla-manifest` / `carla-manifest-verify` 在宿主侧用
`docker image inspect --format '{{.Id}}' $(CARLA_TOOLCHAIN_IMAGE)` 解析后传入（脚本跑在
容器里，问不了 Docker 自己是谁）。verify 会重新解析并比对，**镜像一旦重建就会失败**
——这正是这条字段的用途。未声明时记空字符串（`{"reference": "", "id": ""}`），
记录为空而不是省略。schema 1 / 2 的 manifest 不再被 verify 接受，旧的留作历史 artifact
（既有先例：schema 1 → 2 迁移时同样处理）。新增 3 例测试：声明时记录并 verify 通过、
镜像 ID 变化时 verify 失败、未声明时记空。

**修正（同日）**：最初实现把 `reference` 也纳入严格比对，结果第一次真正用上就撞了
——把默认 tag 指向干净镜像之后，切换前捕获的 manifest 即使拿到旧镜像也无法复验
（标签名变了，报 `toolchain image changed`）。现在**只比 `toolchain_image.id`**：标签
是指针，重建后默认名会被重新指向，旧标签也可能被移到别的 tag 上，这两种情况都不应让
一份诚实的 manifest 失效；`reference` 仍然记录供人读出当时用的是哪个标签。三例实测：

| 验证方式 | 结果 |
| --- | --- |
| 默认 tag（与捕获时相同） | PASS |
| **同一镜像、不同 tag**（原先会误报的场景） | **PASS** |
| 不同镜像 | `FAIL: toolchain image changed` |

端到端实测（`make carla-manifest` → `carla-manifest-verify`，20261003T015104Z-56021d9ceaa4）：

```text
schema: 3   status: PASS
toolchain_image: {"reference": "my-ad/carla-toolchain:arm64",
                  "id": "sha256:3f9109bbe23d19a8944e6118fb32958753535378f6d9a17f219dcb9e44a02ece"}
live image ID  : sha256:3f9109bb…      → verify PASS
换个镜像再验（模拟工具链被重建/替换）  → FAIL: toolchain image changed（make 非零退出）
```

**发现三（根因）：没有 `.dockerignore`，构建上下文是整个工作区。**

```text
$ du -sh --exclude=.git .
966G    .
```

`docker build` 要把上下文交给 daemon。没有忽略文件时上下文就是整棵树（`artifacts/`、
`data/models` 与 `data/engines`、`third_party/` 的引擎检出），966 GB 根本送不进去
——**镜像停在 09-14，是因为之后再也构建不起来**。

**修复**：新增仓库根 `.dockerignore`，默认全拒，只放行 Dockerfile 真正读取的输入。
先扫了全部 Dockerfile 的上下文读取：

```text
images/carla-arm64/Dockerfile       COPY scripts/carla/g0-probe.sh
images/dataset-converter/Dockerfile COPY scripts/dataset-converter/convert.py
```

（`COPY --from=` 的 stage 拷贝不算上下文；`images/ros-tools` 与 `images/navsim` 不读
上下文。）

**过程中差点踩坑**：只放行 carla 那一条会**直接破坏 dataset-converter 镜像的构建**。
所以放行集合按仓库里全部 Dockerfile 的实际读取来定，并新增
`tests/test_carla_image_context.py`（5 例）把这条不变量钉住：第一条规则必须是 `*`；
每个 Dockerfile 的每个上下文输入都必须被放行；输入集合被显式钉成
`{scripts/carla/g0-probe.sh, scripts/dataset-converter/convert.py}`，出现新输入时报错
而不是静默漂移；`artifacts/`、`data/`、`third_party/`、`.git/` 的代表性路径必须被
排除；顶层只有 `scripts` 与 `images` 被放行。

效果：构建日志里 `[internal] load build context` **DONE 0.0s**（此前根本到不了这
一步）。

**结果**：干净构建 `--no-cache` 用时 **24 分 31 秒**完成，exit 0，产出
`my-ad/carla-toolchain:arm64-clean-20261002`（`sha256:6dd61c0c…`，2.87 GB）。

| 项 | 在用镜像（2026-09-14） | 干净镜像（2026-10-03） |
| --- | --- | --- |
| 镜像 ID | `sha256:3f9109bb…` | `sha256:6dd61c0c…` |
| 大小 | 2.78 GB | 2.87 GB |
| cmake | 3.28.3 | 3.28.3 |
| clang-18 | 18.1.8 | 18.1.8 |
| dotnet | 8.0.425 | 8.0.425 |
| python3 | 3.10.12 | 3.10.12 |
| `glslangValidator` | **缺失** | Glslang 11.8.0 |
| `COPY scripts/carla/g0-probe.sh` | — | DONE 0.1s（证明 `.dockerignore` 放行生效） |

即：干净重建**复现了同一个编译器/cmake/dotnet/python**，并补上了 Dockerfile 里已写、
但在 09-14 镜像里缺失的 `glslang-tools`——这正是「镜像落后于 Dockerfile」的具体表现。

**冒烟（真正的证据）**：用干净镜像跑增量引擎构建（先 `touch` `Material.cpp` 只改 mtime、
不改内容，强制真编译）：

```text
CARLA_TOOLCHAIN_IMAGE=my-ad/carla-toolchain:arm64-clean-20261002 make carla-ue-build
→ PASS carla-ue artifacts=/artifacts/carla/carla-ue-20261003T033959Z-fRJ1wl step=complete
→ 20/20 actions（含 [19/20] Link (lld) CarlaUnreal），121 s
```

镜像选择本身也验证过：GNU make 会把命令行变量导出到 recipe 环境（实测
`make probe CARLA_TOOLCHAIN_IMAGE=clean-tag` → recipe 读到 `clean-tag`），而 compose
从环境插值 `${CARLA_TOOLCHAIN_IMAGE}`（`docker compose config` 实测解析为 clean tag）。
冒烟前后 fork 的 `tracked_diff_sha256` 未变（carla `7493c747…`、ue `cc17c89b…`），
manifest verify 仍 PASS——说明 `touch` 只动了 mtime。日志在
`artifacts/carla-image-rebuild/20261003T014235Z/`（`build.log` / `smoke-build.log`）。

**采纳（2026-10-03）**：默认 tag 已指向干净镜像。

```text
my-ad/carla-toolchain:arm64                => 6dd61c0cc7b8  （干净，可由 Dockerfile 重建）
my-ad/carla-toolchain:arm64-clean-20261002 => 6dd61c0cc7b8
my-ad/carla-toolchain:arm64-inuse-20260914 => 3f9109bbe23d  （09-14 旧镜像，保留）
```

依据是三条互相独立的证据：

1. **门禁结果一致**：同一个 NullRHI gate 用干净镜像跑，日志画像与旧镜像**完全一致**
   ——8 条 `Warning:`（`Setting` 6 / `OSS` 1 / `Failed` 1）、0 条 `Material::Serialize`、
   1 次 ensure、0 次 `Class.IsValid`、stop code 143、Endpoint PASS
   （`…T034523Z-39Jfd8` vs `…T020309Z-NIxrGF`）。
2. **工具链版本一致**：cmake 3.28.3 / clang-18 18.1.8 / dotnet 8.0.425 /
   python3 3.10.12 逐项相同。
3. **产出比特级相同**：用干净镜像强制重编 `Material.cpp` 并重新链接，产出的
   `CarlaUnreal` sha256 = `a53f37d7…`，与旧镜像构建的、以及已 staged 的那份
   **逐字节相同**。

即：换成可重建的默认工具链，在可运行的路径上不改变任何结果。旧镜像以
`arm64-inuse-20260914` 保留，既有 manifest 仍可用它复验：

```bash
make carla-manifest-verify MANIFEST=… CARLA_TOOLCHAIN_IMAGE=my-ad/carla-toolchain:arm64-inuse-20260914
```

回退同样一条命令：`docker tag my-ad/carla-toolchain:arm64-inuse-20260914 my-ad/carla-toolchain:arm64`。

**注意**：既有 manifest 记的是 `3f9109bb…`，所以不带 override 直接 verify 会以
`toolchain image changed` 失败——这是该字段按设计工作，不是回归。

**边界**：第 3 条是「增量重编 + 重链」的比特一致，不是「从零全新构建」的比特一致，
后者未验证；另外 GPU 路径（GB10）无法在本次验证，它受宿主机驱动 SIGSEGV 阻塞。

### 9.89 2026-10-03（本地）fork 工作树增量已写进仓库

第 9 项最后一件：fork 的未提交改动此前只存在于工作树里。摆事实：

| fork | HEAD | tracked 修改 | 其中无任何补丁描述的 |
| --- | --- | --- | --- |
| carla | `b56ce1a3` | 1 | 1（`DefaultInput.ini`） |
| ue | `525b5451` | 92 | **83** |

`scripts/carla/patches/` 下 23 个精选补丁中，20 个的内容已经在 fork HEAD 里（对当前树
反向可应用）；另有 3 个（`animation-editoronly`、`legacy-fbx-diagnostic`、
`legacy-fbx-node-metadata`）当前既不适用也未提交。**UE 那 83 个改动没有任何仓库内
artifact 描述它们**——架构上 manifest 能复现，但那份拷贝在 `artifacts/` 下、是时点的、
不被 git 跟踪。

处理：新增 `scripts/carla/freeze_fork_delta.py`（`make carla-fork-delta` /
`carla-fork-delta-verify`），把两个 fork 的 `git diff --binary HEAD` 冻结到
`scripts/carla/patches/fork-working-tree-delta-{carla,ue}.patch`：

```text
carla files=1   bytes=838     sha256=7493c747c48e2a7c
ue    files=92  bytes=197343  sha256=cc17c89b52b60710
```

`verify` 重算并逐字节比对，漂移时打印 `DRIFT` 并退码 1。这两个摘要与 manifest 里的
`tracked_diff.sha256`、以及 `runtime-provenance.json` 里的 `tracked_diff_sha256`
**完全相同**，即三份记录现在确实指同一件事。

顺带修了一处口径不一致：`report_fork_provenance.py` 原用
`diff HEAD --no-color --no-ext-diff`（无 `--binary`），与 manifest 的
`diff --binary HEAD` 不是同一条命令；两者今天碰巧相等（delta 里没有二进制改动），
但那是巧合。现已统一到 manifest 的口径，**实测摘要未变**（`7493c747…` /
`cc17c89b…`），旧记录仍然 verify PASS；并新增跨工具一致性测试：同一棵树上两个工具
必须算出同一个摘要。`scripts/carla/patches/README.md` 说明了两类补丁的区别，并明确
增量补丁**不被任何脚本自动应用**。

**边界**：这是「把事实写进仓库」，不是「提交改动」。HEAD 未动，没有 commit、没有 push；
工作树仍是脏的，`ue-setup` 的「工作树干净」断言仍会失败。是否把 delta 提交进 fork 分支
仍是未决选择，但现在即使不提交，仓库已经能描述这棵树。增量补丁会随工作树变化而失效
——这正是 `verify` 存在的意义。

### 9.90 2026-10-03（本地）「正常退出」：先修停服路径，才能看到真正的 shutdown 崩溃

清单第 7 项最后一条。现状是每次运行的 `decision.md` 都写
`- Excluded: graceful shutdown`，server 永远以 `stop 143` 结束、日志末尾截断在半行。

**第一层根因（停服路径，已修）**：probe 启动 server 时套了
`timeout --signal=INT --kill-after=10`，而 `finish` 里的 `kill -TERM "${server_pid}"`
打的是 **`timeout`** 的 pid（而且还是子 shell 的 pid）。实测 GNU timeout **不把它收到的
SIGTERM 转发给子进程**：

```text
$ timeout 60 sleep 300 & pid=$!
$ kill -TERM $pid; sleep 1
timeout alive? no ; child alive? yes      ← 子进程被孤儿化
timeout exit status: 143（它自己的状态）
```

于是 server 被孤儿化 → 容器退出时被运行时 SIGKILL → 中途截断：**任何信号都没到过 UE**。
而且每次记录的 `Server stop code: 143` 描述的是 **wrapper**，不是 server。

修法（全部在 probe 内，无引擎改动）：`exec` 让 `server_pid` 真的是 server；`finish`
直接 signal server 并**等它真的退出**（`CARLA_NULLRHI_STOP_GRACE`，默认 20 s）后才
SIGKILL；墙钟保护改成显式 watchdog，signal 同一个 pid。

**第二层（真正的问题，已定位未修）**：信号终于能到达 server 后，UE 的退出序列**跑起来了但
会崩**：

```text
LogExit: Preparing to exit …（stock 运行里有 2 行）
EngineExAssertion failed: MessageHandlers.Contains(MessageId)
  [RenderCore/Private/GPUMessaging.cpp] [Line: 68]
Signal 11 caught.
→ Server stop code: 139
```

`GPUMessaging.cpp:68` 是 `FGPUMessageManager::RemoveHandler` 里的
`check(MessageHandlers.Contains(MessageId))`：退出时摘除一个**从未注册（或已摘除）**的
GPU message handler；断言自身又触发一次 exit request，随后 SIGSEGV。

新增字段 `- Shutdown:`（`exited` / `crashed` / `graceful` / `killed`）后实测：

| 运行 | stop code | `Shutdown:` | `LogExit` 行 | GPUMessaging 断言 |
| --- | --- | --- | --- | --- |
| stock `…T043219Z-NkpMCz` | 139 | crashed | 2 | 1 |
| 引擎侧 `-CarlaGracefulTermination` `…T043230Z-LdpKG2` | 139 | crashed | 0 | 1 |

**试过又撤掉的引擎改动**：我一度按源码推断「Unix 上 `SetGracefulTerminationHandler`
只在 commandlet 路径安装」（`LaunchEngineLoop.cpp:4025`，对照 Windows 的 1957），于是加了
一个 opt-in 开关。实测**没有任何可观察差异**：不加开关的 stock 运行同样出现 `LogExit`
（说明信号本来就被处理了），两种模式都是 `crashed`/139。既然证据不支持，**该引擎改动已
完整回退**；用回退后的源码重建得到与原二进制**逐字节相同**的结果（BuildID `377825d7…`、
sha `a53f37d7…`），顺带证明这一点上构建是确定性的。

**边界**：`Shutdown:` 是尽力而为。状态精确但本身有歧义：UE 优雅退出以 128+signal 收尾
（143），与「未处理被杀死」同码，区分只能靠日志；而崩溃会把日志截断在半行，所以日志
判定是竞态的。因此分类**以状态优先**（崩溃信号无歧义），日志只用于升级判定。另：本项
只验证了 NullRHI；GB10 路径无法在本次验证。

**结论**：第 7 项的「正常退出」现在有了明确目标：`GPUMessaging.cpp:68` 的
`RemoveHandler` 断言。这是 NullRHI 与 GB10 都要修的独立缺陷，而且**不需要 GPU 就能复现**。

**机制（同日补充）**：顺着断言两侧查到——

- `RemoveHandler` 在 RenderCore 里的唯一调用者是 `FSocket::Reset()`（`GPUMessaging.cpp:272`），
  而 `FSocket` 是 **move-only**（头文件写明 “Supports only move construction”），所以不是
  「拷贝导致二次 unregister」。
- 注册者是三个 **renderer status-feedback socket**，而它们都是子系统里的**成员变量**：
  `LightGrid.h:29`、`NaniteFeedback.h:13`、`VirtualShadowMapCacheManager.h:466`；注册发生在
  各自的 `InitExtension(FScene&)`（如 `VirtualShadowMapCacheManager.cpp:605`）。
  也就是说：**在场景/渲染扩展析构时，socket 成员析构 → `RemoveHandler` → 断言**。
- 关键一点：日志里紧接断言的是
  `LogCore: Engine exit requested (reason: EngineExAssertion failed: …)`，然后才是
  `Signal 11 caught.`。即 **`check` 失败触发了 UE 的强制退出路径，SIGSEGV 很可能是这条
  强制退出路径的副产物**，而不是 GPU messaging 自身的内存错误。所以这里是两层：
  未满足的不变量（根因，待拿栈）+ 不安全的强制退出（放大器）。
- 而 probe 恰好用 `-ini:Engine:[SystemSettings]` 关掉了 `r.RayTracing`、
  `r.Shadow.Virtual.Enable`、`r.Lumen.DiffuseIndirect.Allow`、`r.VirtualTextures`、
  `r.VolumetricCloud`（`arm64-renderer-scope.sh`）——恰好就是这三个注册者所属的特性。
  「关掉特性但 socket 成员仍参与析构」是**待验证的首选假设**，不是已证结论。

**下一步（明确）**：日志在 `Signal 11 caught.` 处终止，没有调用栈（崩溃处理器没能展开）。
要拿到第一现场需要带 `--cap-add SYS_PTRACE` 的 gdb 运行——仓库已有该模式
（`probe-ue-vulkan-fault.sh` / `make carla-ue-vulkan-fault-gpu`），可照搬到 NullRHI 停服路径。

**同日补充：假设已被否定，而且拿到了最小复现。**

1. **特性开关无关**。手工直启 server（不经 probe、不带任何 client），分别带/不带 probe 那组
   `-ini:Engine:[SystemSettings]:r.*=0` 开关，30 s 后发 SIGTERM：

   | 变体 | exit | `MessageHandlers.Contains` 断言 | `LogExit` |
   | --- | --- | --- | --- |
   | 带开关 | 139 | 1 | 2 |
   | **不带开关** | **139** | **1** | 1 |

   两边都断言、都 SIGSEGV ⇒「probe 关掉特性导致」的假设**否定**，别再往那个方向查。

2. **最小复现（很有用）**：不需要 probe、不需要 client——`启动 server → 等 ~30 s →
   `kill -TERM`」就能得到 `GPUMessaging.cpp:68` 断言 + `Signal 11` + exit 139。干净环境里复盘、
   以及将来递给上游/厂商都可以直接用这个。

3. **core 不可用**：宿主 `core_pattern` 是指向 `apport` 的管道（`|/usr/share/apport/apport …`），
   容器里没有该 helper，所以只能靠 gdb 现场抓，不能靠 core 文件。

4. **gdb 已验证可符号化**（它顺便抓到了上面 9.84 修正里那条完整栈），但临时拼的
   「gdb 后台跑 + 外部发 SIGTERM」编排两次都失败（一次 `pgrep -f` 匹配到了脚本自身
   并误杀 PID 1；一次 `break FSystem::RemoveHandler` 未解析出符号）。**这一步应该写成一个
   真正的脚本（带正确的子进程发现与断点回退），而不是临时命令**，列为下一步的第一件事。

### 9.91 2026-10-03（本地）拿到第一现场：`GSystem` 的 handler 表在 `Reset()` 之前已经被拆了

上面第 4 条照做了。新增 `scripts/carla/probe-ue-shutdown-crash.sh`（编排）+
`dump-ue-shutdown.gdb`（调试器入口）+ `gdb_ue_shutdown_capture.py`（采集）+
`analyze_ue_shutdown_capture.py`（校验/归约），`make carla-ue-shutdown-crash`
（`carla-build` profile，**不需要 GPU**）。运行
`artifacts/carla/ue-shutdown-crash-20261003T054955Z-fydK5V/`（`PASS /
CAPTURED_TARGET_ASSERT`）；补上写监视点后的那次是
`artifacts/carla/ue-shutdown-crash-20261003T061541Z-u8dwTD/`（下文的 `order` 与监视点栈出自
这一次）。

**断点选取不是猜的**：`check()` 展开成 `FDebug::CheckVerifyFailedImpl2(#expr, __FILE__,
__LINE__, TEXT(""))`（`AssertionMacros.h:237`），而它是 `FORCENOINLINE`
（`AssertionMacros.cpp:624`，实测 `info address` = `0x44f797c`）——**任何一次失败的 check
都必然经过它，且不可能被内联掉**。所以它作为必需断点，另外三个（`RemoveHandler` /
`RegisterHandler` / `FSocket::Reset`）只提供上下文，各自按「最小名 → 完整签名」回退，
**哪个 spec 真的绑定了逐条记录**；没绑定就删掉再试下一个，不留一个永远不触发的断点在
那里冒充「没发生」。

**第一现场（实测栈，`Expr`/`File`/`Line` 三个实参都读出来了）**：

```text
#0  FDebug::CheckVerifyFailedImpl2
      Expr="MessageHandlers.Contains(MessageId)"
      File=".../RenderCore/Private/GPUMessaging.cpp"  Line=68
#1  GPUMessage::FSystem::RemoveHandler  (this=0xc13b6c0 <GPUMessage::GSystem>)  GPUMessaging.cpp:68
#2  GPUMessage::FSocket::Reset          (this=0xecb55c94be00)                   GPUMessaging.cpp:272
#3  Nanite::FGlobalResources::ReleaseRHI(this=0xbf9b048 <Nanite::GGlobalResources>)  NaniteShared.cpp:309
#4  FRenderResource::ReleaseResource    (RenderResource.cpp:206)
```

`NaniteShared.cpp:309` 是 `delete FeedbackManager;`（`#if !UE_BUILD_SHIPPING`，同一函数上面
282 行是 `FeedbackManager = new FFeedbackManager();`）。`FFeedbackManager` 的第一个成员就是
`GPUMessage::FSocket StatusFeedbackSocket;`（`NaniteFeedback.h:13`），其构造函数第一句是
`StatusFeedbackSocket = GPUMessage::RegisterHandler(TEXT("Nanite.StatusFeedback"), …)`
（`NaniteFeedback.cpp:26`）。所以：**析构这条 socket → `Reset()` → 注销一个早就注册过的 id，
却失败**。

**为什么失败：表已经不在了。** 断言发生时直接读 `GPUMessage::GSystem.MessageHandlers`
（`TMap` → `Pairs.Elements` 稀疏数组）自身的三项计数：

```json
{"data_slots": 0, "num_free_indices": 0, "allocation_bits": 0, "handlers": 0}
```

**0 个槽位、0 个分配位**——不是「这个 id 不在表里」，而是**整个 handler 表已被销毁**。
而失败 id 是 **0**，正是启动时那次唯一的注册拿到的 id（`RemoveHandler` 的 `abi_x1 = 0`；
同一 socket 对象在 `Reset` 帧里 `MessageId.Index = 0`，两条路径互证）。`FSystem::ReleaseRHI`
并不清表，所以清空只能来自 `~FSystem`（隐式析构销毁 `MessageHandlers`）。也就是说
`GPUMessage::GSystem`（RenderCore）与 `Nanite::GGlobalResources`（Renderer）两个
`TGlobalResource` 的**销毁顺序**让前者先走，后者的 `ReleaseRHI` 于是对着已析构的表调用
`RemoveHandler`，`check` 挂掉，紧接着 `MessageHandlers.Remove(MessageId)` 还在动已释放的
存储——这正是无调试器运行时 SIGSEGV（139）的来源。

**SIGSEGV 是「强制退出路径的副产物」这个假设，这次是做了判决性验证并成立**：同一个
NullRHI 停服路径在 gdb 下**一个信号都没有**（`signals observed: none`）、`LogExit:
Preparing to exit` 正常、exit **143**。原因写在源码里——`CheckVerifyFailedImpl2` 先打日志，
然后 `if (!FPlatformMisc::IsDebuggerPresent())` 才走 `AssertFailedImplV` 致命路径；gdb 挂着
时为真，于是只 `PLATFORM_BREAK()`（SIGTRAP，脚本按已验证的 `nostop noprint nopass` 放行）。
**边界**：这是 gdb 主动改变了失败模式，所以本运行是「不变量 + 调用者」的证据，不是退出码的
证据；退出码 139 仍由 9.90 的 stock/引擎侧两行记录承担。

**这一路修掉的 4 个测量缺陷（都属于「不修就会得出错误结论」那一类）**：

1. `break FSystem::RemoveHandler` 解析不出来——类名带命名空间，真名是
   `GPUMessage::FSystem::RemoveHandler(TRDGHandle<GPUMessage::FSocket, unsigned int>)`。
   9.90 第 4 条记的那次失败，根因就是这个，不是 gdb 的毛病。
2. **gdb 12.1 的 Python API 没有 `Breakpoint.locations`**。第一版按它判「是否绑定」，结果
   四个断点全被判为未绑定，而它们其实都绑上了——`AttributeError` 被 `except` 吞掉，变成
   「静默认定未绑定」。改用 `Breakpoint.pending`（实测：解析成功 `False`，故意写错的名字
   `True`）。
3. **`MessageId` 的 DWARF 读数是错的**。同一个 `RemoveHandler` 实参在三次运行里读出
   3 个不同值（4061044928 / 3447628928 / 1553251840），其中一个恰好等于调用方
   `FSocket*` 截断到 32 位（`0x…f20ea0c0` → `0xf20ea0c0`）——**证明它读的是别的寄存器槽**。
   改为读 ABI 寄存器（`x0`=this、`x1`=首参），三次运行都稳定给出 **0**，并与 `Reset` 帧里
   socket 成员的读数（也走 ABI 指针）一致。**教训：带优化帧里的 DWARF 局部量不能当事实用，
   要有第二个独立来源；这次是一致的才写进结论，不一致的（DWARF 那份）留在 JSON 里当反例。**
4. 探针里写的是 `state.finish()`，而 `finish` 当时是模块级函数——`capture_complete` 一直是
   `false`，最终归约从没跑过（`gdb-messages.log` 里留着 `AttributeError: 'Capture' object
   has no attribute 'finish'`）。之所以还有数据，是因为采集**每次观测后都落盘**。已把
   `finish` 改成 `Capture` 的方法，与仓库另外两个采集模块一致，断点命中数/绑定状态现在
   在结束时被重新读一遍（`resolved=True, observations=1/1/1/3`）。另外 gdb 的
   `hit_count` 实测在这版 gdb 上对**确实触发过的**断点仍报 0，所以不用它，只记自己的计数器。

**顺带发现的仓库缺陷**：当前的 `docker compose`（v5.0.2）**`run` 没有 `--security-opt`**，
而 `carla-ue-vulkan-device-state`、`-gpu`、`carla-ue-vulkan-pipeline`、
`carla-ue-vulkan-fault-gpu` 四个目标都传了 `--security-opt seccomp=unconfined`——它们会在
容器起来之前就以 `unknown flag: --security-opt` 退出 1。实测 `--cap-add SYS_PTRACE` 单独
就够（`CapEff` bit19 置位后 gdb 能断点、能跑），五处已全部去掉该 flag；新目标也从一开始
就只加 `--cap-add SYS_PTRACE`。这条同时说明：**9.90 里那些 gdb 证据不是走这些 target 复现的**。

**结论**：第 7 项「正常退出」的根因从「未满足的不变量（待拿栈）」升级为**已定位的具体缺陷**：
`FSocket` 的析构注销依赖 `GPUMessage::GSystem` 的 handler 表仍然存活，而销毁顺序不保证这一点
（`~FSocket` → `Reset()` → `RemoveHandler`，在表已析构后必然断言，随后是已释放内存上的
`TMap::Remove`）。修法方向属于引擎侧（让 `Reset()` 容忍 manager 已释放、或在
`FSystem::ReleaseRHI` 里显式清表并置状态位让 `Reset()` 直接返回；或让 `GSystem` 的生命周期
覆盖 renderer 资源）。**本轮不擅自给 fork 打补丁**：9.90 那次试改引擎已完整回退，且这类
改动影响面覆盖所有 GPU message 使用者。

**缺口已补上（同日）：用写监视点把「谁清的表」抓成栈。** 不去猜析构符号（`~FSystem` 与
`FSocket::~FSocket` 都被内联，`info address` 取不到），改为对
`GPUMessage::GSystem.MessageHandlers.Pairs.Elements.Data.ArrayNum` 下一个**写监视点**：
硬件监视点绑的是地址，不需要任何可能被内联掉的符号。整轮只触发 **2 次**：

```text
order=1  register    id 分配=0                ← 启动
order=2  map-write   value=1 handlers=1       ← Add（TArray::AddUninitialized ← … ← TMapBase::Emplace）
order=3  reset       id=Null
order=4  reset       id=Null
order=5  map-write   value=0 handlers=0   ★   ← TArray::Empty ← … ← ~TMapBase ← GPUMessage::FSystem::~FSystem
                                                 ← libc 静态析构路径 ← exit() ← __libc_start_main
order=6  reset       id=0                     ← ~FFeedbackManager 的 socket
order=7  remove      id=0                     ← 对着已销毁的表调用 RemoveHandler
order=8  check       MessageHandlers.Contains(MessageId) @ GPUMessaging.cpp:68
```

`order` 是**跨观测类型共用**的单调序号：各列表自己的 `seq` 之间不可比，而「清表与失败谁先」
恰恰就是结论本身。于是两件事从推断变成实测：**清表发生在失败之前**（5 < 8）；**清表者是
`GPUMessage::FSystem::~FSystem` 的成员析构，跑在 libc `exit()` 的静态析构路径里**——不是
`ReleaseRHI`，也不是 RHI 主动拆除。第 2 次触发同时充当本次仪器的正控：它证明了监视的确实是
当初 `Add` 改过的同一个对象（`this=0xc13b708 <GPUMessage::GSystem+72>`），所以「0 次触发」
不会被误读成「表没被清」。

整条链因此闭合：`exit()` → `GSystem` 静态析构（表被销毁）→ `Nanite::GGlobalResources` 的
`ReleaseRHI`（同样在静态析构路径）→ `delete FeedbackManager` → `~FSocket` → `Reset()` →
`RemoveHandler(0)` 在已销毁的表上断言 → 紧随的 `TMap::Remove` 动已释放存储。两个全局都是
`TGlobalResource`，先后完全由静态析构顺序决定。

**同日补充：release 路径也补测了**（顺手关掉了 9.92 报告里的一个未决问题）。给
`GPUMessage::FSystem::ReleaseRHI` 与 `Nanite::FGlobalResources::ReleaseRHI` 各加一个可选断点后，
时间线（每次都带当时表里的 handler 数）：

```text
order= 5  release-gsystem  map_handlers=1   FSystem::ReleaseRHI：跑过，表仍有 1 个 handler
order= 6  map-write        map_handlers=0   表被 ~FSystem 销毁
order= 7  release-nanite   map_handlers=0   Nanite 的 ReleaseRHI：此时表已空
```

两次都从 `FRenderResource::ReleaseResource` 进入，「`FSystem::ReleaseRHI` 是否真的跑过」
有答案了：**跑过、正常通过**（整轮只有那一条 `check` 事件，说明它的
`check(MessageBuffer == nullptr)` 成立）、**且它不清表**——表的死亡不能归给它。

**再加一个 `exit` 断点，把两次 release 归到阶段上**（时间线因此是 11 条）：

```text
order= 5  exit             map_handlers=1   exit() 进入（atexit handler 从这里开始）
order= 6  release-gsystem  map_handlers=1   ~TGlobalResource<FSystem> 的析构函数体 → ReleaseGlobalResource
                                            → FSystem::ReleaseRHI
order= 7  map-write        map_handlers=0   ~FSystem 销毁 MessageHandlers
order= 8  release-nanite   map_handlers=0   ~TGlobalResource<Nanite::FGlobalResources> 的析构函数体
                                            → Nanite::FGlobalResources::ReleaseRHI
```

**两次 release 都在 `exit` 之后**（inside-exit-handlers）⇒ 都不是「RHI 主动关闭时的显式释放」，
而是**静态析构**；缺陷就是这两个 `TGlobalResource` 析构的**相对顺序**。`TGlobalResource`
解释了每次 release 内部那个「先 release、后成员析构」的顺序
（`RenderCore/Public/RenderResource.h:568`）：

```cpp
virtual ~TGlobalResource()
{
    ReleaseGlobalResource();   // 析构函数体：早于 ResourceType 的成员析构
}
```

**一处必须更正**：我在这段更早的版本里写过「`ReleaseRHI` 早于自身成员析构 ⇒ 与由自身基类析构
调用不相容 ⇒ 至少有一次是显式调用」。**这个推断是错的**：release 由 `TGlobalResource`
**自己的析构函数体**调用，而 `TGlobalResource` 是**派生类**，它的函数体本来就先于基类
（`FSystem`）的成员析构 —— 所以「先 release 后成员」正是静态析构路径的**正常**表现。做出这个
更正的依据就是上面那一次 `exit` 断点加那一行源码；结论与修法方向不变（都是顺序问题），但理由
被换掉了。教训与 9.84 那条同类：**推断不要写进结论，除非有第二个独立来源**。

**仪器本身也修了一处**（值得记，因为它会让采集悄悄少一条证据）：断点安装器原先要求「创建时
必须立即解析成功」，否则删掉再试下一个候选。这对二进制自身的符号是对的，但 `exit` 住在 libc
里——创建断点时它还没映射进来，于是这条断点被当作「无法解析」丢掉，第一次跑出来的记录是
`exit=None(resolved=False,observations=0)`（**是显式记录，不是静默丢失**，所以一眼就看出来
了）。现已允许这类角色**保持 pending**，运行结束后再把「绑定成功/命中几次」读回来。若改走
「调整释放顺序」的替代修法，还缺的只是「谁调用 `ReleaseResource`」这个名字：这版构建里 gdb
的 unwind 就在那一帧断开（`Backtrace stopped: previous frame identical to this frame (corrupt
stack?)`），但阶段问题已被 `exit` 断点回答，所以它不再是前提。

**回归**：`make test-local` 全绿；新增 `tests/test_carla_ue_shutdown.py`（**21 项**）专门盯
「不能让坏测量冒充干净结果」：必需断点未绑定必须判 `INVALID_INSTRUMENTATION`、无停服证据时
「没有断言」必须判 `INCONCLUSIVE`、失败 id 必须读 ABI 寄存器、inferior 必须经
`/proc/…/children` + `exe` 核对后发现、Makefile 不得再出现 `--security-opt`；监视点没触发时
「清表先于失败」必须留成**未测量**而不是顺手判真，监视点在失败之后才触发必须被指出。

### 9.92 2026-10-03（本地）把 9.91 的根因做成「可递交但不应用」的修复提案

9.91 只到「根因已确证、修法属引擎侧」。这一节把它做成可给别人看的形态，**同时保证它没有被
偷偷应用**。

**提案本体**：`scripts/carla/patches/ue-gpumessaging-shutdown-guard.patch`。一个文件、
三处改动、不加头文件：给 `FSystem` 加析构函数，在**成员析构之前**（析构函数体先于成员
析构，这正是必须覆盖的窗口）置位 `static bool bIsBeingDestroyed`；`FSocket::Reset()` 在
该位为真时只清自己的 id、不再反向注销。

**为什么不直接改 fork**：guard 落在**共用的注销路径**上，影响所有 GPU message 使用者
（本版还有 `LightGrid.h:29`、`VirtualShadowMapCacheManager.h:466` 两个注册者），属于上游
生命周期契约的决定；而且 9.90 那次「凭源码推断就改引擎」已被实测否定并回退，先例要求这次
先把证据和提案摆出来，由维护者定落点。

**关键取舍**：**不动 `check`**。它抓的是「manager 还活着时重复注销」这类真 bug，全局放宽会
把真 bug 一起藏起来；这里是生命周期问题，不是断言写错了。替代方案（让 `GSystem` 活更久 /
让 Nanite 提前释放 FeedbackManager）都要求每个现存与未来的 socket 持有者都排对顺序——而
「排对顺序」恰恰就是刚刚失败的那个性质。

**边界（必须一起读）**：补丁**未应用、未编译**，所以「断言与 SIGSEGV 会消失」是从实测顺序
出发的论证，不是实测结果；只在 NullRHI 上复现过；`FSystem::ReleaseRHI` 是否真的跑过仍未观测
（不影响本提案，但若改为调整顺序就必须先测）；flag 故意用普通 `bool`（写点在跑 `exit()` 序列
的线程上，本行 socket 的析构也在该线程）。

**防「悄悄应用」**：`tests/test_carla_ue_shutdown_upstream_proposal.py`（8 项）同时钉住两个
方向——补丁对当前 fork **仍可干净应用**（`git apply --check`）、且**仍未在树里**
（`bIsBeingDestroyed` 不在源码中，`git status` 不含该文件）；另外钉住改动面只有一个文件、
以及「置位发生在析构函数体内」这一形状。谁把补丁应用了，测试就会失败——应用与否必须是一个
显式决定。`scripts/carla/patches/README.md` 新增「第 3 类：提案」，与「已应用精选补丁」和
「工作树增量」明确区分（后两类都不是「提案」）。

**递交形态**：`artifacts/ue-gpumessaging-shutdown/20261003T062016Z/`（`report.md` +
`COVER-NOTE.md` + 提案补丁副本 + 该次采集的 `shutdown-capture.json` / `shutdown-analysis.json`
/ `validation.log` / `decision.md`）。它与 GB10 厂商包是**两条独立线索**，封面明确写了不要
互相外推。

### 9.93 2026-10-03（本地）fork 工作树已提交并推送；记录口径从「脏树 + 冻结增量」改为「分支 HEAD」

第 9 项一直挂着的那条：两个 fork 的改动**只存在于本机脏工作树**。本轮把它做成了可复现的资源。

**推送结果**（实测，不是计划）：

| fork | 分支 | remote | 推送 | 结果 |
| --- | --- | --- | --- | --- |
| `third_party/carla` | `dgx-arm64` | `origin` = `gottaBoy/carla` | `234caf5..f6cbc59` | 1 个 tracked 改动 + `.gitignore` 忽略 `Saved_shaderdiag/` |
| `third_party/unreal-engine` | `dgx-arm64` | `gottaBoy` | `693d44c72..5502950e1`（含 5 个从未推送的 commit） | 92 个 tracked + **5 个未跟踪源文件** |

**那 5 个未跟踪文件是真问题，不是洁癖**：`CarlaVulkanObjectLifecycle.h`、`VulkanGraphicsDiagnostic.{h,cpp}`
被**已跟踪的**源文件 include（`VulkanChunkedPipelineCache.cpp`、`VulkanMemory.cpp`、
`VulkanCommands.cpp`、`VulkanPipelineState.cpp`），而 `MovieSceneToolsARM64Stub.cpp`、
`FbxLogCategory.cpp` 按 UE 的模块规则也会被自动编译。也就是说：**在提交之前，
「HEAD + tracked delta」根本描述不出产出二进制的树**——manifest 只能靠快照，而快照在
`artifacts/` 下且不入库。现在它们进了 commit。

**记录随之重生成（顺序不能颠倒）**：提交之后「未提交 delta」变空，所以
`make carla-fork-provenance` 与 `make carla-fork-delta` 必须重跑，否则 `--verify` 报 DRIFT。
新记录：两者都是 `tracked_dirty=0` / `untracked=0`，`tracked_diff_sha256` = 空串 sha256
（`e3b0c44298fc1c14…`）；两个冻结 delta 补丁变成 **0 字节**——**空文件在这里是有意义的断言**：
它说这两棵树是干净的，谁再弄脏，`make carla-fork-delta-verify` 立刻失败。

**顺带修掉一个只能靠手工的环节**：`report_fork_provenance.py` 默认只打印、不落盘，运行时要写入
`artifacts/carla/cooked-server-full/`（root 所有），所以此前那次「重装」是手工做的、后来就陈旧了
（`--verify` 报 10 个字段漂移）。新增 `make carla-fork-provenance-install` 在容器里写这份记录；
它还暴露了第二个坑：容器以 root 跑、fork 属于宿主用户，git 直接拒绝（`detected dubious ownership`），
所以该 target 用 `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_*` 传入 `safe.directory` 而不是放宽全局配置。

**边界**：这解决的是「源码是否可复现」，**没有**解决验收；GB10 侧仍然缺 GPU，`dgx-arm64` 的
上游仍是 `CarlaUnreal/UnrealEngine`（本案只推到了 `gottaBoy` 的 fork 分支）。

**9.91/9.92 里那批「提案」和「厂商包」的共性**：本轮结束时，两条线索都停在「等人做决定」
（发厂商包；定引擎修复落点），再加上第三条待定规则（fork 改动如何固化）。这三件事原来散在
audit 与 todo 里，读者要自己拼。现新增 `docs/carla-handoff.md`：**只放决策队列**——每条写清
卡在谁那里、要发/要决定什么、以及**不依赖这些决定**的下一步；证据仍在 audit、未完成项仍在
todo（三个文件角色不重叠）。`tests/test_carla_handoff.py`（6 项）钉住它「短且不腐」：页里
引用的每个 `artifacts/...` 路径都必须真实存在、两份递交包都必须指到、三条决策都必须具名、
并且**驱动不可更换**这条约束必须在页面上活着（否则厂商包会被读成「换个驱动试试」）。
