# CARLA Vulkan Bridge：REA 分析

分析日期：2026-11-07，Asia/Singapore。

## 结论

**可以做用户态兼容层，而且本轮出现了比“换语言”更具体的依据；但 bridge
尚未实现或验收，提前加载对照也没有把 CARLA 跑通。**

本轮补齐了直接故障机制：故障线程的 NVIDIA 编译库 TLS 模块槽基址为 0，
解析返回值与线程指针相加后得到 `0x8`，驱动在该地址写入时 SIGSEGV。
线程指针本身不是 0。仍未确认谁、何时、为什么让模块槽为空。

只对 CARLA 子进程增加提前加载同一编译库的对照后，compute 创建从
`4 enter / 3 return` 推进到 `34 / 34`，包括原失败入口；但随后出现
`VK_ERROR_DEVICE_LOST`，UE 主动以 exit 1 退出。因此这是**单次诊断推进**，
不是完整根因、稳定绕法或产品 PASS。

## 本轮实际执行

- 使用固定版本 REA 6.8.0 CLI 的 `capture-process` 和
  `inspect-native-exports`；未安装反编译引擎、注册 MCP 或修改系统 Node。
  上一轮已确认本机 Node 版本不在该 REA 版本的支持范围内，此限制仍在。
- 从固定镜像
  `sha256:6dd61c0cc7b860865e1b2ef14a54af1a097d7f6e5fed4a0bb9407a6cec79a064`
  创建未启动容器，提取加载器和 libc 后移除该容器；静态检查不执行驱动。
- 两次 GPU 诊断均复用现有 Development 二进制，SHA256 为
  `c9defcb3ee951ef3e7da09230f030921f8c1cfc99cea0a073633b9c72d99a3e8`。
  每次 GDB 上限 90 秒，外层 REA 上限 150 秒，端口 20370。
- GDB 只读取寄存器、进程映射、有限内存和回溯，不调用 inferior 函数。
  GPU 运行本身会执行既有 UE、驱动及其初始化逻辑，并更新诊断 stage 的运行日志。
- 不修改引擎源码、宿主驱动、正式 stage 或停服补丁；临时 GPU 容器均已退出移除。
  Compose 提示的其它 orphan 容器没有清理。

产物目录：`artifacts/rea-bridge-20261107/`。

## 直接故障机制

基线运行：2026-11-07 16:57:39 至 16:58:07 本地时间。
`fault-run/fault-tls.json`、`fault-run/backtrace.txt` 与
`fault-driver-entry-analysis.json` 可互相核对：

| 项 | 实测 |
| --- | --- |
| 信号 | SIGSEGV，`si_addr=0x8` |
| 故障指令 | NVIDIA `glvkspirv` 相对偏移 `0x34d610`，`str x5, [x3, x0]` |
| fault thread | `Backgro-rker #3`，LWP 98 |
| 未返回 compute | `main_0000e4c4_ce5f93a6`，同为 LWP 98 / `GraphTask` |
| graphics | 661 enter / 661 return |
| compute | 4 enter / 3 return |
| `x3`，驱动此前读取的线程指针 | `0xe9f9467cde60`，可读取对应 TCB |
| `x0`，resolver 返回的相对偏移 | `0xffff1606b98321a8` |
| 64 位地址计算 | `(x3 + x0) mod 2^64 = 0x8` |
| TLS descriptor resolver | 与 `x6` 相同，位于本轮加载器偏移 `0x142d8` |
| dynamic TLS 参数 | 模块索引 11，模块内偏移 8，generation 23 |
| DTV 模块槽 | `0xe9f9200013e0`，两个 word 均为 0 |

当前捕获入口与 10 月 7 日的 `main_0000142c_a6b37050` 不同，但驱动故障偏移、
TLS 写入机制相同；不能把它们称为同 shader 的重复样本。

加载器静态字节显示该 resolver 会从 DTV 读取模块基址，返回
`module_base + module_offset - thread_pointer`，并保存/恢复 `x3`。
本轮读取的模块基址为 0、模块内偏移为 8，正好解释最终写入地址 8。
`validate_evidence.py` 使用整数运算独立复核了 descriptor、DTV 槽地址与上述算式。

本轮映射路径文件的 SHA256 与静态分析输入一致：

```text
glvkspirv  b63396177492fc33c9081a03445d6bb26ec5cbe6c321c6544f13e812f94d284e
ld-linux   1f4fe51e11324b5bcc01ef1fd3859b5dd344ec16a8674d74ce618ef65016fbaf
libc       79fd4ecae5fb5fab4e17ec4f145d2b79026559dce52f01a478b0a04fef3dff06
```

这些哈希是映射路径对应文件的样本，不是映射内存的完整哈希。
`tpidr_el0` 仍不能直接读取；这次通过驱动保留的 `x3` 和有界 TCB/DTV 读取推进，
不是声称调试器已经支持读取该系统寄存器。

**仍未知**：DTV 的完整更新历史、模块加载/卸载顺序、该线程的所有写入来源，
以及是否存在加载器缺陷、UE 内存破坏或驱动误用。不能直接给问题冠以某个
已知 glibc bug 编号，也不能认为修正空槽就能解决后续 GPU 执行错误。

## 提前加载单变量对照

运行：2026-11-07 17:00:27 至 17:00:59 本地时间。

只在 GDB 的 inferior 环境中加入：

```text
LD_PRELOAD=/usr/lib/aarch64-linux-gnu/libnvidia-glvkspirv.so.580.173.02
```

GDB、`vulkaninfo` 和其它服务不设置此变量。
校验器确认：除归档输出目录和这条 inferior 环境设置外，两轮 GDB 命令相同。
两轮都是诊断 binary、原 renderer 配置和相同驱动，不是新桥接实现。

| 项 | 结果 |
| --- | --- |
| compute | 34 enter / 34 return |
| 本轮基线失败的 `main_0000e4c4_ce5f93a6` | 创建返回 `VK_SUCCESS` |
| 历史失败的 `main_0000142c_a6b37050` | 两次创建均返回 `VK_SUCCESS` |
| graphics | 829 enter / 828 return |
| 后续错误 | `VulkanMemory.cpp:4704`，`VK_ERROR_DEVICE_LOST` |
| UE 退出 | `RequestExit(1, VerifyVulkanResult)`，inferior exit 1 |
| 完整产品状态 | FAIL / 未验收 |

这轮没有目标 SIGSEGV，所以没有信号停点 TLS 快照；不能据此声称已观察到 TLS
槽被修复。它只说明本次提前加载对照跨过了那些 compute 创建调用。
最后一个 graphics 未返回可能只是进程因 device loss 退出时尚未完成，
不能把它直接当作 GPU 故障源头。

这是各一轮的顺序对照，尚未覆盖 cache 冷热、重复启动、时序扰动及无 GDB
运行，不能宣称因果修复。Wrapper/GDB exit 0 只表示采集结束，不是 UE exit 0。

## 宿主影响检查

用户询问对本机的影响后，额外读取宿主内核日志。提前加载对照报
`VK_ERROR_DEVICE_LOST` 的同一时刻，**2026-11-07 17:00:57 +08:00**，
宿主记录：

```text
NVRM: Xid (PCI:000f:01:00): 109, pid=2148355, name=CarlaUnreal,
channel 0x00000013, errorString CTX SWITCH TIMEOUT, Info 0x5c021
```

这证明异常已被宿主 GPU 驱动记录，不能把实验描述为完全隔离在容器内。
它不等于整机已经挂死或硬件损坏；本次后续检查中 `nvidia-smi` 可正常
响应，实验容器均已退出，但未验证所有其它 GPU 任务是否完全未受影响。
原始检查保存在 `host-kernel-gpu-check.log` 和 `host-gpu-status.csv`。

本轮未更换驱动、重置 GPU 或重启机器。发现这条记录后停止继续启动 GPU
实验。原型编写、静态分析和不调用 GPU 的测试可继续；再次真实 GPU 验证
应安排维护窗口或可中断设备，不能承诺零影响，也不应把进程级环境变量的
可撤销性等同于 GPU 故障的即时恢复。

## Bridge 的真实接入边界

### 优先路线：用户态装载适配

本轮提前加载提供了值得复核的实验依据。先重复控制/提前加载/控制对照，
验证正常进程启动及 TLS 状态，再决定是否固化成默认关闭的启动兼容配置。
同时单独定位后续 device loss，不能把“原 SIGSEGV 消失”当作整体解决。

不直接覆写 TLS descriptor、伪造 TCB/DTV、跳过故障写入或返回假管线；
本次尚无证据支持这些修改。

### 次选路线：同进程专用 worker

源码已经有窄的插入点：
`VulkanPipeline.cpp` 的 `CarlaPipelineHistory::CreateComputePipeline`。
`CreateComputePipelineFromShader` 在调用前持有 module 引用、cache scope，
并把 stack 上的 `EntryPoint` 和可能的 subgroup `pNext` 传给调用。

因此可设计原型，但必须满足：

1. 使用同一进程、同一 `VkDevice`、相同真实 module/layout/cache。
2. 原调用者同步等到真实 API 返回；不能提交后立刻返回或超时后任由 worker
   继续引用调用者栈。跨线程发布/完成需要正确的内存同步。
3. cache guard 在原线程的原 scope 内保留；worker 不重复获取调用者已持有的锁，
   不代替原线程释放锁。不能让 RHI 线程等待它自己要执行的队列任务。
4. 复核 allocator、validation/debug callback 的重入。当前故障 marker 的
   allocator 为 NULL，不代表所有产品配置都是 NULL。
5. 返回真实 `VkResult` 与真实 pipeline；设备析构前必须完成/停止 worker。
6. 从 compute 单调用验证开始；早期还存在 graphics 故障和后续 device loss，
   compute bridge 不等于覆盖全部渲染错误。

本轮没有实现或运行 worker bridge，故“换线程能解决”仍是未知。
本轮新线程与提前加载的效果也不能互相替代。

### 两个容易误判的捷径

**只导出同名 Vulkan 函数不保证能拦截。** 此 UE 在
`Linux/VulkanLinuxPlatform.cpp:91` 用 `dlopen` 加载 libvulkan，
在 135 行通过 `vkGetInstanceProcAddr` 填写函数指针；
`VulkanLoader.h:80` 把 compute 创建列在该查询表中。
独立 Layer 必须正确接入 loader 的函数查询/dispatch 链；原型先放在引擎内
更容易确认故障调用确实经过它。REA 导出检查也只发现编译库的 20 个私有
NVVM 动态导出，不能把它当成提供标准 Vulkan 入口的库。

**`r.pso.CreateOnRHIThread=1` 不是覆盖保证。** 当前源码
`RHI/Private/PipelineStateCache.cpp:3112` 只在满足 RHI/命令列表条件时选择
RHI 线程；3118 行仍把 active precache 和 PSO 文件缓存的 compute 路径送往
后台任务。本轮限定范围内的历史 docs/config/scripts/tests 搜索未见该开关
的实验记录，但这不证明它适用于本次故障调用，或此前从未在其它地方尝试。
旧 `no-pso` 和全局串行化失败也不能当成此开关或 worker bridge 的实测。

跨进程“编好后直接传 VkPipeline 句柄回来”不作为本轮原型路线；若要完整
代理资源、命令和同步，会显著扩大问题，不是这里的窄适配。

## REA 证据索引

| 文件 | Evidence ID |
| --- | --- |
| `driver-exports.json` 的 retained record | `ev_fcba79c97fe950b006ff226274ef1dbcee911a218540cbd79ac7cce7e7271b6b` |
| `driver-tls.capture.json` | `ev_56dcc2f119c80214260a08e2acd8577f75258fcb3d135073d8d2cb7faf3a74b3` |
| `loader-tls.capture.json` | `ev_6abd5f60bff91c3f13345731ef4c000556e4b16e6d94f030a42c562fd996d350` |
| `fault-run.capture.json` | `ev_e9186cbb781002a61ecfe35ecc9caf6f0753eb6fa6473fa0ea454214b682f34d` |
| `preload-run.capture.json` | `ev_5e8751f7334d750d84120bfcde998b5be33bdadf32ed00404847f54351bebd8f` |
| `validation.capture.json` | `ev_0ae137c41c197a685be2765e83c7a5e0191d95ead361736e7831c7868c4d5dc5` |

REA 记录 docker/GDB 的编排过程；fault JSON 和完整 GDB 日志是伴随产物。
最后一次 validation capture 对列明的关键伴随产物做前后哈希，并运行独立
算式/命令/返回结果校验，exit 0、无截断、settlement `quiesced`。
其它 capture 部分 settlement 为 `unverifiable`，包括无法读取无关进程
ownership 的情况；未操作这些无关进程，不冒充完整进程环境覆盖。

当前最合理的下一步是**复核装载适配并定位 device loss，再决定是否需要
专用线程 bridge**，而不是直接重写语言或开发完整代理驱动。
