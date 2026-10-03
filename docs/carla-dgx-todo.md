# CARLA / DGX GB10 修复清单

更新：2026-09-28（本地）。最终目标为原生 GB10 上 Town10 世界就绪、同步仿真、
RGB/LiDAR 有效数据、持续稳定运行及可复现部署。当前未达到产品验收。

**需要人做决定的三件事汇总在 `docs/carla-handoff.md`**（厂商包发送、引擎提案落点、fork 改动
固化方式）；本文件是未完成项与逐项证据的唯一清单。

## 现在就可用的（已完成，可直接使用）

| 能力 | 入口 | 状态 |
| --- | --- | --- |
| NullRHI 端到端复现 Town10 + 同步 tick | `make carla-town10-nullrhi-rpc` | PASS；停服信号真的到达 UE，`decision.md` 带 `Shutdown:` 分类 |
| **停服崩溃根因可复现**（第一现场栈 + 有序时间线 + 写监视点，不需 GPU） | `make carla-ue-shutdown-crash` | PASS / `CAPTURED_TARGET_ASSERT`；`map_cleared_before_check` 由数据判定 |
| 引擎修复**提案**（未应用） | `scripts/carla/patches/ue-gpumessaging-shutdown-guard.patch` | `git apply --check` 通过；测试钉住「仍可应用且仍未应用」 |
| 可复现性链：manifest（schema 3，记 toolchain image id）+ verify | `make carla-manifest` / `-verify` | PASS |
| fork provenance（revision/branch/dirty/diff sha256）+ verify | `make carla-fork-provenance` / `-verify` | PASS |
| fork 工作树增量冻结 + verify | `make carla-fork-delta` / `-verify` | PASS |
| 干净镜像重建（`.dockerignore`，24m31s，逐字节相同二进制） | `make carla-ue-build` | 已采用 |
| 自造日志噪声门控（248 → 8 条 Warning） | CVar `carla.LogMaterialSerialize` / `-CarlaLogMaterialSerialize` | 已实测 |
| GB10 厂商证据包（39 运行 + 封面）与**数字自校验** | `artifacts/gb10-driver-report/20261003T052318Z/` + `tests/test_carla_gb10_bundle.py` | 封面每个数字从 `report.json` 反推比对 |
| 决策队列一页 | `docs/carla-handoff.md` | 引用的 `artifacts/` 路径有测试保证存在 |

回归：`make test-local` → **826 tests / 0 failures / 56 skipped**。

## 做不了、已挂起的（阻塞原因 + 谁能解开）

| 挂起的事 | 为什么现在做不了 | 谁能解开 |
| --- | --- | --- |
| 清单第 2/3/5/6/8 项（GB10 世界、RGB/LiDAR、长跑） | 故障在 NVIDIA 的 SPIR-V 编译器里，且**我们的输入单独能过**（独立 GB10 进程 0.016 s）⇒ 不是我们能在应用层绕开的 | 厂商回复，或重定验收范围 |
| 把上下文依赖变成**确定性复现**（唯一能让厂商可行动的形态） | 需要 GPU 时间做实验 | 你/组织批 GPU 时间 |
| 停服采集接 GB10 profile、`Shutdown:` 字段接 GB10 记录 | 代码可写，但**跑不了**（要 GPU 才产生新证据） | GPU 可达 |
| 第 3 项下一步：串行化扩展到 `vkCreateShaderModule`/layout/descriptor/cache 入口 | 同上，需 GPU 才有证据；且 39 运行已否掉「验证层」这条 | GPU 可达 |
| 应用引擎修复提案 | 属上游生命周期契约，影响所有 GPU message 使用者 | 上游/维护者定落点 |
| 第 9 项：fork 改动固化方式 | **已定并已完成（2026-10-03）** | 两 fork 已提交并推送（carla `f6cbc59b`、ue `5502950e`），provenance 与 manifest 均为 `tracked_dirty=0`/`untracked=0`，冻结 delta 为空——见 `docs/carla-handoff.md` 与 audit 9.93 |
| 发送 GB10 厂商包 | **已从"建议"降级为"备选"**：证据核心只有 2 条符号栈 + 1 条判别实验，故障非确定性 ⇒ 对方大概率回「请给可复现样本」 | 你的渠道；若无渠道则此路作废 |
| 重定 GB10 验收范围 | 产品决策 | 你/组织 |

除上述之外，仓库里没有卡住的工作。

- [x] 1. 保留失败基线、日志、栈、命令及输入哈希。
- [ ] 2. 捕获完整的同轮故障输入：故障 graphics 的实际 shader module、固定状态、layout、cache、`vkCreateRenderPass2KHR` 和故障 compute 的 device snapshot 已有同轮证据；独立 GB10 创建均通过。现已记录部分 cache/module/layout/render-pass 的实际创建、延迟删除入队和 Vulkan 销毁，但还缺其余调用点及完整 UE 驱动状态，不能标为 UE exact replay。
- [ ] 3. 定位源头：同进程 compute 与 graphics 故障输入在独立 GB10 replay 均可创建。关闭 graphics 快照、置空提交 cache 后 GB10 仍崩溃；cache、故障 module/layout 均存活，未入延迟删除队列，故障期间没有已记录 cache 重叠；最新 allocator 实测为 NULL，与独立 replay 一致。运行期 bindless/descriptor-buffer 已确认未启用（设备能力判定失败），故不在本次故障路径内。2026-10-01 新证据：开启 Khronos validation layer 后同一 GB10 连续 2/2 通过（world + 20 tick + endpoint gate），关闭后 2/2 SIGSEGV（**此条后来没有站住**：完整扫描 39 个运行时，验证层开着是 **2/10 通过**、关掉 **0/29 通过**——它只与仅有的 2 次通过同时出现，开着仍有 8/10 崩，故**不能当作绕法**），说明故障依赖 driver 调用的并发/时序；下一步把串行化从 pipeline 创建扩展到其它并发入口（`vkCreateShaderModule`、layout/descriptor 创建、`vkGetPipelineCacheData`、`vkMergePipelineCaches`），区分“串行化”与“纯时序”两种解释。不可把“未观察到”写成完整排除。
- [ ] 4. 实施有证据支持的最小修复，增加回归；不得通过压制断言或静默删减功能验收。
- [ ] 5. 未调试的 GB10 Town10 世界、地图、快照及 20 次同步 tick 门禁通过。
- [ ] 6. RGB/LiDAR 数据有效、帧号/时间对齐、相机图像随世界变化。
- [ ] 7. 分别解决 InputSettings 类解析 ensure、image-layout 警告和正常退出。（InputSettings 已修；自造日志噪声已门控；`IsInGameThread` ensure 已裁定不改；「image-layout 警告」已穷举搜索确认日志中不存在、结项；停服路径已修；正常退出的根因已定位到 `GPUMessaging.cpp:68`：`GSystem` 的 handler 表被先销毁，`~FSocket` 仍在其上注销——修法属引擎侧，未动 fork）
- [ ] 8. 完成传感器、车辆、行人长时间运行及内存/丢帧检查。
- [ ] 9. 干净镜像重建与 provenance 复现，移除临时工具副本依赖。（已开始：provenance 现已可派生、可校验，见下；镜像重建与 fork 改动固化方式未定）

## 本轮已完成

- [x] 独立首故障捕获入口，不挂 Vulkan 创建 API 断点，不调用 inferior 函数。
- [x] 两次真实 GB10 捕获确认：请求 Pixel shader 的 key 非零，当前 PSO 的 Pixel key 为 0、指针为空、descriptor set 1 不存在。
- [x] 第二次捕获确认 descriptor state 的 pipeline 和 layout 与当前 PSO 匹配，无字段/线程读取错误。
- [x] 字符串有界并在 NUL 处终止；多线程读取延后至 stop event；validator 独立重算 key、索引及关联结论。
- [x] 增加 `UE_FAULT_TRACE_GFX=1` 显式追踪；最近 256 个事件有界保存，保留截断计数；context/thread/list/shader 关联不冒充命令节点身份。
- [x] 修复优化掉的读取点、事件预算计数、超时线程优先级和 PSO 地址复用误关联，失败归档不覆盖。
- [x] 低干扰基线再次捕获首个 graphics check，并从 `FRHICommandListBase::Execute` 读到 `bExecuting=true` 的命令列表；命令节点地址和原始 ShaderRHI 指针仍不可读。
- [x] `make test-local`：693 tests、56 skipped、0 failures；shell syntax 和本轮改动的 diff 检查通过。
- [x] 在 UE VulkanRHI 中增加默认关闭的固定环形内存记录：PSO 绑定、graphics parameters、首个 uniform-buffer shader-key mismatch；只在显式 `CarlaVulkanGraphicsMemoryTrace=...` 时启用。
- [x] ARM64 Development 增量编译成功；独立 diagnostic stage 保留新 binary/debug 与 cooked asset 路径关系。
- [x] 无 GDB direct-run 运行 180 秒：Town10 加载完成、正常 PreExit、无 NVIDIA SIGSEGV、无 memory trace；结果仍为 diagnostic FAIL/未复现。
- [x] 新二进制无 GDB GB10 runtime gate 复现 NVIDIA SIGSEGV；直接故障输入为 `main_0000142c_a6b37050`、shader hash `B07511BF3F84ED1435DC4ECA18879491A24EE19E`、SPIR-V 5164 bytes、SHA256 `b5500d7af1d3f07a15118d78fc784d062e343140d8431bee713e3ba1d3710bf3`。
- [x] 记录真实 UE layout：set 0 bindings `8,3,3,3,3,2,0`，pipeline flags/stage flags/pNext/subgroup 均为 0，cache bytes 21098；该 attempt 有 `.begin.txt` 无 `.result.txt`。
- [x] 同一 SPIR-V + 同一 UE layout + 真实 cache，并复用 UE `vkCreateDevice` snapshot 的独立 GB10 replay PASS（约 0.016s），因此 shader/layout/cache/device feature 配置不是充分根因。
- [x] 同一 VkDevice、同一 cache、保留对象的 3 个故障前 compute pipeline 历史 replay PASS（`history_pipeline_count=3`）；compute 历史本身不是充分根因。
- [x] 完整 UE compute pipeline 创建串行化 diagnostic 仍在 B075 崩溃；单纯 compute 并发不是充分根因。
- [x] 完整 UE graphics `vkCreateGraphicsPipelines` driver-call 串行化 diagnostic 仍在 B075 崩溃；该运行出现两个 B075 begin、均无 result，重复 compute create 不是单独充分根因。
- [x] 同次 graphics history 已保存 219 个唯一 graphics SPIR-V；分析得到 16 个未完成 graphics create，分布在 14 个线程并映射到 VS/PS shader hash。
- [x] graphics render-pass 摘要采集器已修正；最新同次运行保存 542 个唯一 graphics shader、15 个 render-pass 摘要，分析得到 27 个未完成 graphics create。
- [x] 固定状态摘要收缩为安全的顶层字段；checkpoint 在 GB10 运行中完整保存并经 analyzer 检查，但不是故障调用身份。
- [x] 增加默认关闭的 compute/graphics driver-entry/return 标记；ARM64 Development 编译并以独立 stage 复验。无 shader diagnostics 时 4 次 compute enter/3 次 return，未返回 `main_00006260_1d3100ee`；开启 diagnostics 后的两次运行分别为 626/625 和 631/630 次 graphics enter/return，compute 皆为 3/3。最后一次 graphics 标记包含 VS/PS shader hash 和同轮 SPIR-V；仍为 SIGSEGV。
- [x] 补齐锁内 graphics 固定状态的有界快照；实际 shader module 创建字节按模块归档，校验器核对 SPIR-V entry 与 SHA-256。`20260928T015955Z-R0V6SI` 的 graphics 645/644、compute 3/3，唯一未返回 graphics 同轮保存 21097 字节非空 cache；最终 `20260928T020235Z-WCiO9k` 的 graphics 670/669、compute 3/3，另一个未返回 graphics 同轮保存 21098 字节 cache、颜色/深度附件引用与 layout 信息，自动分析退出 0。所有运行仍因 NVIDIA compiler SIGSEGV 无 RPC world。
- [x] 默认关闭地捕获实际 `vkCreateRenderPass(2)` 参数（附件/subpass/依赖和已知 pNext）；用 handle 将同轮故障 graphics、实际 VS/PS、cache 和 render-pass 文件相连。`20260928T023252Z-ZxrO4t` 的 graphics 676/675、compute 3/3，render-pass `capture_complete=1`，自动分析退出 0；`20260928T024745Z-7E9KIF` 的 graphics 625/624、compute 3/3，第二个简单 render-pass 样本也完整关联。复杂 pNext 或不支持的子通道不强行标成完整。
- [x] 只接受单 subpass、无扩展链、非 bindless 等已验证输入的独立 graphics 创建器，复用归档的不同轮 `vkCreateDevice` 配置；两份 UE 崩溃 graphics 的 GB10 独立创建均 PASS。见 `vulkan-graphics-replay-20260928T024620Z-Ir2apU` 与 `vulkan-graphics-replay-20260928T024826Z-ursarG`。这不是 UE 生命周期或传感器验收。
- [x] GDB pipeline capture 支持显式 `CARLA_UE_PIPELINE_ALLOW_DEVICE_ONLY=1`：当 crash 模式的 pipeline target 未捕获但同一进程 `vkCreateDevice` snapshot 完整时，归档为 `CAPTURED_DEVICE_ONLY`，不伪装成 PASS；延后 `spirv-val` 也有显式开关。
- [x] 低干扰 GB10 fault capture `gb10-device-fault-20260928T054047Z-DbJisL` 完成：同一故障进程 device snapshot 完整，642 graphics / 4 compute driver entries，唯一未返回 compute `main_00000290_c1eca148`，385 个 module、15 个 render-pass snapshot 通过校验，GDB 栈落在 `libnvidia-glvkspirv.so.580.173.02`。
- [x] 同一故障进程 compute replay：实际 shader 656 bytes、cache 21098 bytes、layout set0/binding0 storage buffer、device snapshot SHA `b719168834c4b669107b90f4663c8e0f6b9b75798a885d4dfd4e7f3d8a5885cf`；独立 GB10 `vkCreateComputePipelines` PASS，证据在该目录 `compute-replay/result.json`。
- [x] 默认关闭的 cache 生命周期事件：`VulkanPipeline.cpp` 的 cache 创建/销毁/合并/取数及 graphics/compute driver enter/return 按序落盘；无 graphics 快照的 GB10 runtime 记录 1285 事件，故障 cache 存活、无已观测重叠；低干扰 GDB 同一故障进程记录 1253 事件，device snapshot 与前次相同，故障 compute cache 存活、无重叠。已修复基线自带 cache 标志时未自动分析的问题。
- [x] 实际 module/layout/render-pass 创建、deferred enqueue、`ReleaseResources` 中真正的 `vkDestroy*` 已加入默认关闭的有序事件；最终 GB10 runtime `town10-gb10-rpc-20260928T085350Z-iP6Een` 的 2539 条事件显示故障 cache/module/layout 存活且未排队销毁、无故障期间重叠，仍在 NVIDIA compiler SIGSEGV；低干扰 GDB `gb10-device-fault-20260928T084351Z-E45puG` 的同进程 device snapshot + 故障栈亦与此相符。
- [x] `VulkanChunkedPipelineCache.cpp` 的 cache create/get-data/destroy 已接入同一生命周期接口；最终运行 `town10-gb10-rpc-20260928T101159Z-vaBhus` 记录 2602 事件、故障 cache/module/layout 存活且故障期间无重叠。该运行没有启用 chunk-specific cache object，因此 eviction 本身仍未覆盖。
- [x] bindless/descriptor-set layout 的创建、延迟入队和实际销毁已接入生命周期记录；最终运行 `town10-gb10-rpc-20260928T144007Z-4BrcUZ` 记录 descriptor 事件，分析器校验通过，故障 compute 的 cache/module/layout 仍存活且无故障期间重叠。该运行 graphics layout 仍为 `bindless=0`，只验证覆盖接线，不证明 bindless 实际启用行为。
- [ ] 覆盖 bindless 实际启用时的专用 descriptor-buffer/layout 状态和其它未走当前路径的 Vulkan API；核对 driver 调用与内存状态，再形成可验证的最小修复。
- [x] 结项 9.71 遗留项：运行期 bindless/descriptor-buffer 从未启用（`LogRHI: Bindless descriptor were requested but NOT enabled because of insufficient property support.`），`bindless=0` 是设备能力判定而非 cook/runtime 漂移，按“当前配置不适用”结项，不作为排除证据。
- [x] 默认关闭的 validation layer 对照：固定 jammy `1.3.204.1-2`（deb sha256 固定、只读挂载、`vulkan-validation.json` 记录层清单哈希）；同一 GB10 连续运行 validation=1 **2/2 PASS**（world + 20 tick + endpoint gate），validation=0 **2/2 SIGSEGV**（`libnvidia-glvkspirv.so.580.173.02`）。这是相关性证据，不是精确根因，也不是产品验收。
- [x] 修正四个 probe 的端口预检 TIME_WAIT 误判（补 `SO_REUSEADDR`）。
- [x] 把 9.74 的串行化解释做成可判定对照：`CarlaVulkanSerializeDriverCalls` 与 pipeline 创建共用一把锁，覆盖 `vkCreateShaderModule`、descriptor/layout 创建、cache merge/get-data、descriptor allocate/update。两轮（含扩展覆盖）均为基线 FAIL / 串行化 FAIL，且诊断行与启动参数已确认生效。**结论：故障不依赖这些入口之间的并发**，9.74 的 PASS 只能归因于 layer 的校验行为、instance/device 创建链差异、时序或内存布局扰动。
- [x] 新增 `CARLA_RUNTIME_VULKAN_DEBUG_UTILS`（`-vulkandebugutils`，不装 layer）以分开「debug-utils/instance 链」与「layer 校验」。
- [x] 修 harness 缺口：probe 曾只归档 stdout，无法区分「产品崩溃」与「设备创建被拒」。现在归档 `Saved/Logs/CarlaUnreal.log` 为 `client-ue.log`，诊断加入 `Cannot create a Vulkan device`，`decision.md` 新增 `- Client abort:`。
- [x] **环境阻塞（已关闭）**：04:48 之后同一份 binary（`cb734133…`）在基线 / `-vulkandebugutils` / validation 三组上全部以 `stop=1` 失败，原因是 `Cannot create a Vulkan device`（`vkCreateDevice` 被拒），而同时 `vulkaninfo` 仍能成功创建最小设备。debug-utils 对照与 9.74 的 validation PASS 均**未取得有效复核**，需等设备创建恢复后重跑三组。→ 设备创建已自行恢复，9.78 四组对照已重跑完毕（control / debug-utils / serialize-driver-calls 均 `shader-compiler-sigsegv`，validation 亦 stop 139），本阻塞项关闭。
- [x] 新增可复用对照入口 `make carla-gb10-vulkan-comparison`（`scripts/carla/compare_gb10_vulkan_diagnostics.py`）：四组固定顺序、控制组先跑、`device-creation`/`preflight` 归 BLOCKED、其余记 NOT-RUN，报告写入 `artifacts/gb10-vulkan-comparison/<UTC>/`（含 client binary SHA256）。环境恢复后一条命令即可复核 9.74。
- [x] 四组实测完成（`artifacts/gb10-vulkan-comparison/20261001T084821Z/`）：control / debug-utils / serialize-driver-calls 均为 `shader-compiler-sigsegv` stop 139，**validation 也是 stop 139**（崩溃改落在 `libnvidia-eglcore`）。结论：9.74 的 validation PASS 不可复现，降为未复核历史观测；debug-utils 假设被否；layer 只是把崩溃位置往后推。
- [x] 方差复核（追加 3 轮 control+validation）：control 5/5 FAIL，validation 4/4 FAIL（3 次 `nvidia-driver-sigsegv`）。9.74 的 PASS 未再现。
- [x] 新增崩溃符号化：`make carla-symbolize-client-crash RUN_DIR=…`（`scripts/carla/symbolize_client_crash.py`）。必须用 UE 打印的**第一个（绝对 PC）**地址，括号内偏移与 debug 二进制不匹配；只取 `Critical error` 之后的帧。control 内层帧 = `CreateComputePipelineFromShader`；validation 内层帧 = `VK_ERROR_DEVICE_LOST` → `VulkanRHI::CheckDeviceFault`。
- [x] **新线索（已降级）**：validation 运行暴露真实 VUID 违规——`VUID-vkCmdDraw(Indexed)-None-02699`（draw 使用从未被 `vkUpdateDescriptorSets()` 写过的 descriptor）与 stage mask 使用未启用的 `MESH/TASK_SHADER_BIT_NV`。需确认它是否为 ARM64 特有、以及是否与 shader 编译崩溃同源。→ 已取得调用点（9.81）：通用 RHI 路径 `RHIDrawIndexedPrimitive`，但去重后仅 **2 个** descriptor set 被复用，且无法解释对照组的编译期 SIGSEGV，故降级为「UE 绑定模式与 VVL 1.3.204 语义不一致」的候选之一。
- [x] descriptor 违规的调用点已拿到：新增 `-CarlaVulkanValidationStackTrace=<子串>`（默认关闭）+ `MODE=validation-call-site` 符号化。实测 5723 次捕获（5574 draw-indexed / 149 draw），调用点是通用 RHI 路径 `FVulkanCommandListContext::RHIDrawIndexedPrimitive`；但去重后只有 **2 个** descriptor set 被复用。
- [ ] **线索下调**：「97% 的 draw 用未初始化 descriptor」在任何驱动上都应大面积出错，更可能是 UE 绑定模式与 VVL 1.3.204 语义不一致，而非 GB10 特有；也无法解释对照组的编译期 SIGSEGV。拿到 Lavapipe（Mesa + 同版本层）对照前不再投入。
- [x] descriptor 违规分布已量化（n=6）：4 次失败运行均含 `binding #0 index 0` 的 draw 违规（`vkCmdDraw` + `vkCmdDrawIndexed`），2 次通过运行均不含。时间线：违规 → 5 s 后 `VK_ERROR_DEVICE_LOST` → `CheckDeviceFault` 崩溃。这是**相关性**，且无法解释对照组的编译期 SIGSEGV；当前按「两个候选缺陷」处理。
- [x] 新增厂商证据包：`make carla-gb10-driver-report RUN_DIRS="…"`（`scripts/carla/collect_gb10_driver_report.py`），不重跑 gate，只汇总环境/驱动、decision 字段、符号化栈、validation 发现、已排除项与**缺失证据清单**；首次输出 `artifacts/gb10-driver-report/20261001T091726Z/`（`missing=none`）。
- [x] **厂商包已刷新为可发送形态**：`artifacts/gb10-driver-report/20261003T052318Z/`——纳入 2026-10-01 全部 **39** 个 GB10 运行（含四组对照、方差复核与 device-creation 失败样本），`missing=none`；另加 `COVER-NOTE.md`（数据转储里没有的那部分）：**明确的三选一要什么**（是否为 580.173.02 已知缺陷 / 我们违反了 glvkspirv 的哪条输入约束 / 有没有官方绕法）、**最强判别证据**（同输入独立 replay 0.016 s 通过）、**驱动不可更换的约束**（GB10 绑 BSP，换驱动是系统级风险，不被允许）、最小环境与文件清单。旧的 2026-10-01 包保留。
- [x] **第 7 项·InputSettings ensure 已修**：根因是 `CarlaUnreal.uproject` 的 `DisableEnginePluginsByDefault: true` → 只挂载显式列出的引擎插件 → EnhancedInput 不挂载；即使加进 .uproject 也因依赖**编辑器插件** `DataValidation` 而无法加载。修复 = `DefaultInput.ini` 改指 `/Script/Engine.PlayerInput` 与 `/Script/Engine.InputComponent`（经容器写入 fork 源树）。NullRHI gate 上 `Class.IsValid` 由 4 → **0**。
- [x] **第 7 项·日志噪声（自造）已门控**：一次 NullRHI 运行 248 条 `Warning:` 里 **239 条**来自 fork 自带的 `Material::Serialize … tell=` 探针（fork 提交 `85a04a40b`，2026-09-21 排查 cooked material 流偏移时带入）。改为 opt-in（CVar `carla.LogMaterialSerialize` / 命令行 `-CarlaLogMaterialSerialize`，默认关），探针本身保留。证据：静态测试 `tests/test_carla_engine_log_hygiene.py` + `make carla-ue-build` PASS + **运行时 A/B 实测** `Warning:` **248 → 8**、`Material::Serialize` **239 → 0**（`…T020244Z-1kFig1` sha `d561f54e` vs `…T020309Z-NIxrGF` sha `a53f37d7`，两次 Endpoint 均 PASS）。旧二进制保留在 `artifacts/carla/ue-binary-backup-20261002T020304Z/`。
- [x] **第 7 项·`IsInGameThread` ensure 已裁定不改引擎**：计数更正为 **1 次事件**（不是 2 次，2 是日志行数）。这版引擎的 `IsInGameThread()` 比的是任务标签而非线程号，`FCoreDelegates::OnInit.Broadcast()`（`LaunchEngineLoop.cpp:6818` → `InitUObject()` → `CoreRedirects.cpp:1374`）落在标签未装上的窗口内，属上游标签记账；`CoreRedirects.cpp`/`Obj.cpp`/`LaunchEngineLoop.cpp` 在 fork 中均 pristine。基线固定为「已知 1 次」，**新增** ensure 才是信号。见 audit 9.84。
- [x] **第 7 项·`IsInGameThread` ensure 的结论已修正**：9.84 当时栈无符号，我写成「不是跨线程调用」——用 `.debug` 在 gdb 下重跑拿到完整符号栈，**实际就是异步加载线程**（`FAsyncLoadingThread::Run` → `AddKnownMissing` → `CoreRedirects::Initialize`）。裁定（不改引擎）不变，理由改为「已知的异步线程 lazy init，操作本身安全」。教训：栈没符号时不要把「看不到调用者」写成结论。
- [x] **第 7 项·「image-layout 警告」穷举搜索：日志里不存在这条告警**（13135 个 run 日志、有界搜索）。命中“layout”的 Warning/Error 行只有两族，都不是图像布局告警：
  (1) `VUID-VkDeviceCreateInfo-pNext-pNext`（28 次）——它枚举 pNext 链的合法结构体，里面几个**结构体名含 Layout**（`VkPhysicalDeviceUniformBufferStandardLayoutFeatures`、`VkPhysicalDeviceWorkgroupMemoryExplicitLayoutFeaturesKHR`），所以 `grep -i layout` 会误命中；本质是 pNext 链问题；
  (2) `LogLinker: Error: [AssetLog] …/layout/Engine/…/WorldGridMaterial.uasset: Serialization error - FName …`——“layout”只是路径里的**目录名**，错误是 FName 序列化。
  其余命中均是**非告警**：`LogVulkanRHI: Display:` 的设备特性列表（`VK_EXT_scalar_block_layout` / `VK_KHR_separate_depth_stencil_layouts` 等）与**名为 `T01_Layout`/`T10HD_Layout` 的关卡**。全仓库里唯一的“image layout”字样在**我们自己的代码**：`vulkan-readback.c`（正确使用 `VK_IMAGE_LAYOUT_*`）与 `run-carla-lavapipe-sensors.sh:95` 的一句**断言**（未触发）。见 audit 9.85。
- [x] **约束（用户决定，2026-10-03）：不换驱动**。理由：会导致系统出问题，除非零风险——不满足。因此 GB10 主线**只剩投厂商**这一条路，该条不再作为可选方案反复提议。
- [x] **第 7 项·停服路径已修（信号从未到达 server）**：probe 原先把 server 套在 `timeout` 下，`finish` kill 的是 wrapper（还是子 shell）的 pid；实测 GNU timeout **不转发**收到的 SIGTERM，server 被孤儿化后被容器 SIGKILL——所以**任何信号都没到过 UE**，`Server stop code` 描述的也是 wrapper。已改为 `exec` 启动 + 直接 signal server + 等它退出（`CARLA_NULLRHI_STOP_GRACE`，默认 20 s），墙钟保护改为显式 watchdog；新增 `- Shutdown:` 分类字段。见 audit 9.90。
- [ ] **第 7 项·正常退出的真正阻塞点（已定位未修）**：信号到达后 UE 退出序列会崩——`RenderCore/GPUMessaging.cpp:68` 的 `check(MessageHandlers.Contains(MessageId))`（`RemoveHandler` 摘除未注册的 handler）→ `Signal 11` → `stop 139`。**最小复现**：直启 server + 30 s 后 `kill -TERM`，不需要 probe、不需要 client。**已否定**「probe 的 `-ini:…r.*=0` 特性开关导致」（带/不带开关都断言）。宿主 `core_pattern` 指向 apport 管道，容器内无 core 文件，只能 gdb 现场抓；gdb 已验证可符号化，但「后台 gdb + 外部发信号」的编排需写成正式脚本（已完成两处失败教训：`pgrep -f` 会匹配脚本自身、`FSystem::RemoveHandler` 断点未解析）。
- [x] **第 7 项·已拿到第一现场，根因落到具体缺陷**：新增 `make carla-ue-shutdown-crash`（`scripts/carla/probe-ue-shutdown-crash.sh` + `dump-ue-shutdown.gdb` + `gdb_ue_shutdown_capture.py` + `analyze_ue_shutdown_capture.py`，`carla-build`、**不需 GPU**），运行 `artifacts/carla/ue-shutdown-crash-20261003T054955Z-fydK5V/`（`PASS / CAPTURED_TARGET_ASSERT`）。栈：`Nanite::FGlobalResources::ReleaseRHI`（`NaniteShared.cpp:309` 的 `delete FeedbackManager`）→ `~FFeedbackManager` → `~FSocket` → `FSocket::Reset`（`GPUMessaging.cpp:272`）→ `RemoveHandler` → `check` 挂。**关键测量**：断言发生时 `GPUMessage::GSystem.MessageHandlers` **0 个槽位、0 个分配位（表已被销毁）**，而失败 id = **0**，正是启动时唯一那次注册（`Nanite.StatusFeedback`）拿到的 id；`FSystem::ReleaseRHI` 不清表 ⇒ 清空来自 `~FSystem`，即 `GSystem` 与 `Nanite::GGlobalResources` 两个 `TGlobalResource` 的**销毁顺序**问题（`~FSocket` 之后还在已释放的表上 `TMap::Remove`，这就是 139 的来源）。**SIGSEGV 是强制退出路径副产物**这个假设也做了判决性验证：同一路径在 gdb 下**零信号**、`LogExit` 正常、exit **143**（因为 `IsDebuggerPresent()` 为真时 `CheckVerifyFailedImpl2` 只 `PLATFORM_BREAK()`）。**修法方向属引擎侧，本轮不擅自改 fork**。仍缺一次测量：哪条析构路径先清表。见 audit 9.91。
- [x] **第 7 项·清表者已抓成栈（缺口补上）**：改用**写监视点**盯 `GSystem.MessageHandlers.Pairs.Elements.Data.ArrayNum`（硬件监视点绑地址，不需要可能被内联掉的析构符号），整轮只触发 2 次：`order=2 value 0→1`（启动时 `TMapBase::Emplace` 的 `Add`，同时是正控）与 `order=5 value 1→0`（`TArray::Empty ← … ← ~TMapBase ← GPUMessage::FSystem::~FSystem ← libc exit() 静态析构路径`）。共用单调 `order` 让「谁先」成为数据：**清表在失败之前（5 < 8）**，且清表者是 `~FSystem`、跑在 `exit()` 的静态析构里（不是 `ReleaseRHI`）。完整链：`exit()` → `~GSystem` 销毁表 → `Nanite::FGlobalResources::ReleaseRHI`（同样在静态析构）→ `delete FeedbackManager` → `~FSocket` → `Reset()` → 已销毁表上 `RemoveHandler(0)` 断言 → `TMap::Remove` 动已释放存储 = 139。分析器新增 `map_cleared_before_check`，监视点没触发时留成 `null`（未测量）而不是顺手判真。见 audit 9.91。
- [x] **第 7 项·修复提案已做成「可递交但不应用」**：`scripts/carla/patches/ue-gpumessaging-shutdown-guard.patch`（单文件、三处改动、不加头文件：`FSystem` 析构函数体在**成员析构之前**置位 `bIsBeingDestroyed`，`FSocket::Reset()` 据此跳过反向注销）。**不动 `check`**（它抓的是真 bug），**不改 fork**（guard 在共用注销路径上，影响所有 GPU message 使用者，属上游契约决定）。递交给上游的形态：`artifacts/ue-gpumessaging-shutdown/20261003T062016Z/`（报告 + 封面 + 提案 + 该次采集原始证据）。防「悄悄应用」：`tests/test_carla_ue_shutdown_upstream_proposal.py`（8 项）同时要求补丁**仍可干净应用**且**仍未在树里**。见 audit 9.92。
- [x] **第 7 项·顺带修掉一个一直被藏着的仓库缺陷**：当前 `docker compose`（v5.0.2）的 `run` **没有 `--security-opt`**，而 `carla-ue-vulkan-device-state`、`-gpu`、`carla-ue-vulkan-pipeline`、`carla-ue-vulkan-fault-gpu` 四个目标都传了它 → 容器还没起来就以 `unknown flag: --security-opt` 退出 1（也就是说 9.90 里那些 gdb 证据不是走这些 target 得到的）。实测 `--cap-add SYS_PTRACE` 单独足够（`CapEff` bit19 置位后 gdb 能断点能跑），五处已移除该 flag，并加测试防止回归。
- [x] **第 7 项·撤掉一个无证据支持的引擎改动**：曾按源码推断 Unix 缺 `SetGracefulTerminationHandler` 而加 opt-in 开关，实测无任何可观察差异（stock 同样出现 `LogExit`），已完整回退；回退后重建得到与原二进制逐字节相同的结果。见 audit 9.90。
- [x] **第 9 项·provenance 派生与校验（第一步）**：新增 `scripts/carla/report_fork_provenance.py` + `make carla-fork-provenance` / `carla-fork-provenance-verify`。发现探针消费的 `/artifacts/carla/cooked-server-full/runtime-provenance.json` **记错了 carla revision**（`aba1bb69…` vs 实际 `b56ce1a3…`），且 ue 虽 revision 正确但工作树有 **92 个 tracked 修改**，单靠提交号不可信。现已改为记录 HEAD + 分支 + tracked 修改数 + `git diff HEAD` 的 sha256，并已重装（旧记录保留为 `.stale-aba1bb69`）；`--verify` 现在 PASS，NullRHI gate 端到端仍 PASS。另发现 `g0-probe`/`ue-setup` 的 pin 与现状全部不符（**未改**，需先定 fork 改动固化方式）。见 audit 9.86。
- [x] **第 9 项·fork 工作树增量已固化**：新增 `scripts/carla/freeze_fork_delta.py` + `make carla-fork-delta` / `carla-fork-delta-verify`，把两个 fork 的未提交 delta 冻结成 `scripts/carla/patches/fork-working-tree-delta-{carla,ue}.patch`（carla 1 文件/838 B、ue **92 文件/197343 B**）。摘要与 manifest、`runtime-provenance.json` **完全一致**（`7493c747…` / `cc17c89b…`）；`report_fork_provenance.py` 的 diff 口径已对齐到 `--binary`（实测摘要未变）。新增 `scripts/carla/patches/README.md` 区分两类补丁，并说明增量补丁不被任何脚本自动应用。见 audit 9.89。
- [ ] **第 9 项·未提交**：HEAD 未动，fork 改动仍未提交进分支（「工作树干净」断言仍失败），且 UE 那 83 个改动仍无精选补丁覆盖——但仓库现在已能描述这棵树。
- [x] **第 9 项·临时工具副本已清除（manifest 解锁）**：`make carla-manifest` 之前一直 FAIL，原因是根目录 `.codex-tmp/`（2.2 GB、3822 条目）里有 **9 个符号链接**，而 manifest 要求非忽略的 untracked 条目必须是常规文件。已把 audit 9.27 引用的日志与 GDB/render 记录等 80 个文件（6.3 MB）归档到 `artifacts/codex-tmp-archive/20261002T090908Z/`（含 SHA256SUMS + README），改掉 audit 引用，删除 `.codex-tmp/`。结果：untracked 条目 2690 → **60**，非常规条目 9 → **0**，`make carla-manifest` **PASS**（`…T091006Z-74444f371d46`）、`carla-manifest-verify` **PASS**。新增回归 `tests/test_carla_scratch_hygiene.py`。见 audit 9.87。
- [x] **第 9 项·构建上下文与工具链身份**：发现仓库根**没有 `.dockerignore`**，构建上下文就是整个工作区（**966 GB**），`docker build` 根本开始不了——在用的工具链镜像因此停在 **2026-09-14**，而 Dockerfile 已到 09-25（且工作区还有未提交修改）。新增根 `.dockerignore`（默认全拒 + 只放行 Dockerfile 真正读的输入；先扫了全部 4 个 Dockerfile，差点因为只放行 carla 那条而破坏 dataset-converter 镜像），上下文加载变为 **DONE 0.0s**；新增 `tests/test_carla_image_context.py`（5 例）钉住「每个 Dockerfile 的每个上下文输入都必须被放行」。同时把 manifest 提升到 **schema 3**，新增必填 `toolchain_image`（引用名 + 解析后的镜像 ID），verify 会在镜像重建后失败——此前 manifest 根本回答不了「这是哪个工具链产出的」。旧镜像已保留为 `my-ad/carla-toolchain:arm64-inuse-20260914`（sha `3f9109bb…`）。见 audit 9.88。
- [x] **第 9 项·干净镜像构建已完成**：`--no-cache` 构建 **24 分 31 秒**、exit 0，产出 `my-ad/carla-toolchain:arm64-clean-20261002`（`sha256:6dd61c0c…`，2.87 GB）。与在用镜像对比：cmake 3.28.3 / clang-18 18.1.8 / dotnet 8.0.425 / python 3.10.12 **完全一致**，并补上了 09-14 镜像里缺失的 `glslang-tools`（Dockerfile 里早就写了）。冒烟：`CARLA_TOOLCHAIN_IMAGE=<clean> make carla-ue-build` → **PASS**（20/20 actions 含 Link，121 s）；冒烟前后 fork diff 哈希未变、manifest verify 仍 PASS。日志在 `artifacts/carla-image-rebuild/20261003T014235Z/`。
- [x] **第 9 项·默认工具链已换成可重建镜像**：默认 tag `my-ad/carla-toolchain:arm64` 现指向干净构建（`sha256:6dd61c0c`）；旧图像以 `arm64-inuse-20260914`（`3f9109bb`）保留，回退一条命令。依据：同一 NullRHI gate 用干净镜像跑出的日志画像**完全一致**、四个工具版本逐项相同、且强制重编后产出的 `CarlaUnreal` **sha256 逐字节相同**（`a53f37d7…`）。注意既有 manifest 记的是旧 ID，不带 override 直接 verify 会以 `toolchain image changed` 失败（字段按设计工作）。

证据：`artifacts/carla/ue-vulkan-fault-20260927T015729Z-nO2skz/`；
追踪未复现故障的证据：`artifacts/carla/ue-vulkan-fault-20260927T015348Z-zx8AHX/`。
direct-run 未复现证据：`artifacts/carla/ue-vulkan-memory-trace-20260927T024452Z-aZ6Nne/`；
NVIDIA 精确输入证据：`artifacts/carla/town10-gb10-rpc-20260927T032257Z-KMRgf2/`；
真实 cache replay：`artifacts/carla/vulkan-compute-replay-20260927T045705Z-GMyhcu/`；
compute history replay：`artifacts/carla/vulkan-compute-replay-20260927T053512Z-doKJSA/`；
graphics history：`artifacts/carla/town10-gb10-rpc-20260927T122856Z-9n8I53/`；
graphics render-pass history：`artifacts/carla/town10-gb10-rpc-20260927T134801Z-Almx5m/`；
driver-entry compute：`artifacts/carla/town10-gb10-rpc-20260928T000321Z-YgVbHN/`；
driver-entry graphics/hash：`artifacts/carla/town10-gb10-rpc-20260928T000924Z-FEfsfs/`。
driver-entry 完整 graphics/module/cache 样本：
`artifacts/carla/town10-gb10-rpc-20260928T015955Z-R0V6SI/` 和
`artifacts/carla/town10-gb10-rpc-20260928T020235Z-WCiO9k/`。
render-pass 同轮故障及对应独立重放：
`artifacts/carla/town10-gb10-rpc-20260928T023252Z-ZxrO4t/`、
`artifacts/carla/vulkan-graphics-replay-20260928T024620Z-Ir2apU/`、
`artifacts/carla/town10-gb10-rpc-20260928T024745Z-7E9KIF/`、
`artifacts/carla/vulkan-graphics-replay-20260928T024826Z-ursarG/`。
当前 GB10 GDB device snapshot 与同环境重放：
`artifacts/carla/ue-vulkan-pipeline-20260928T050306Z-oEG3Rk/`、
`artifacts/carla/vulkan-graphics-replay-20260928T050903Z-9Cgo92/`。
同一故障进程 device/fault 与 compute replay：
`artifacts/carla/gb10-device-fault-20260928T054047Z-DbJisL/`。
cache 生命周期无快照与同进程 GDB 证据：
`artifacts/carla/town10-gb10-rpc-20260928T075611Z-WbnOdX/`、
`artifacts/carla/gb10-device-fault-20260928T080152Z-wd1TxJ/`。
对象生命周期及最终复验：
`artifacts/carla/gb10-device-fault-20260928T084351Z-E45puG/`、
`artifacts/carla/town10-gb10-rpc-20260928T085350Z-iP6Een/`。
bindless/descriptor 生命周期覆盖复验：
`artifacts/carla/town10-gb10-rpc-20260928T144007Z-4BrcUZ/`。
null pipeline-cache 对照：
`artifacts/carla/town10-gb10-rpc-20260928T143440Z-6WOYex/`。
allocator 记录对照：
`artifacts/carla/town10-gb10-rpc-20260929T015350Z-Dw2UCr/`。
validation layer 对照（通过 / 崩溃配对）：
`artifacts/carla/town10-gb10-rpc-20261001T040959Z-8xFK8j/`、
`artifacts/carla/town10-gb10-rpc-20261001T041158Z-omIeYU/`、
`artifacts/carla/town10-gb10-rpc-20261001T041227Z-hH7BLh/`、
`artifacts/carla/town10-gb10-rpc-20261001T041045Z-PbzaUn/`。
driver 调用串行化负向对照：
`artifacts/carla/town10-gb10-rpc-20261001T044409Z-Ptfd6h/`、
`artifacts/carla/town10-gb10-rpc-20261001T044425Z-47jylw/`、
`artifacts/carla/town10-gb10-rpc-20261001T044745Z-izx6GL/`、
`artifacts/carla/town10-gb10-rpc-20261001T044802Z-EL7gKC/`。
设备创建退化（`Client abort: device-creation`）：
`artifacts/carla/town10-gb10-rpc-20261001T051715Z-ogA1w4/`、
`artifacts/carla/town10-gb10-rpc-20261001T051719Z-EsNet6/`、
`artifacts/carla/town10-gb10-rpc-20261001T051723Z-IF3Lat/`、
`artifacts/carla/town10-gb10-rpc-20261001T052015Z-jmBnTr/`。
对照入口首次运行：`artifacts/gb10-vulkan-comparison/20261001T084719Z/`。
四组完整对照（含 validation 不可复现）：`artifacts/gb10-vulkan-comparison/20261001T084821Z/`。
方差复核（3 轮 control+validation）：`artifacts/gb10-vulkan-comparison/20261001T090723Z/`、`…T090759Z/`、`…T090834Z/`。
崩溃符号化结果：`artifacts/carla/town10-gb10-rpc-20261001T090835Z-1DcLyl/symbolized-crash.txt`、
`artifacts/carla/town10-gb10-rpc-20261001T090849Z-rJsZfI/symbolized-crash.txt`。
厂商证据包：`artifacts/gb10-driver-report/20261001T091726Z/`。
validation 调用点：`artifacts/carla/town10-gb10-rpc-20261001T121623Z-lCKbre/symbolized-validation-call-site.txt`。
InputSettings 修复验证：`artifacts/carla/town10-nullrhi-rpc-20261001T144610Z-iv3gBd/`。
日志噪声基线（改前）：同上运行，`Warning:` 248 条中 239 条为 `Material::Serialize`。
详细边界见 `docs/carla-dgx-audit.md` 9.64–9.85。
捕获状态为 `CAPTURED_FAULT`、`runtime_acceptance=false`，
不是运行成功；同轮唯一未返回的 compute 或 graphics driver-entry 与
NVIDIA 编译器崩溃具有时间关联，尚未证明 shader、UE 状态或驱动自身的根因。
