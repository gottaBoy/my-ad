# CARLA / DGX 交接页

只放**需要人做决定的事**：谁卡住了、要发什么/要决定什么、以及**不依赖这些决定**的下一步。
证据与推导在 `docs/carla-dgx-audit.md`，未完成清单在 `docs/carla-dgx-todo.md`。
状态时点：**2026-10-07**。

## 恢复进展（2026-10-07）

已恢复排查。本轮完成真实 GB10 GDB 故障捕获与 UE 任务上下文采集，
未应用引擎停服提案，也未宣称主阻塞已修复。

- **主阻塞未解决**：完整 UE 进程在 GB10 / 580.173.02 的 Vulkan pipeline 创建过程中，
  于 NVIDIA shader compiler 内 SIGSEGV。崩溃位置已确认，充分根因及可验收修复尚未确认。
- **新增上下文证据**：`artifacts/carla/gb10-device-fault-20261007T133547Z-fXVgwX/`
  捕获到 `Backgro-rker #2`、CPU 2、允许 CPU `0-19`、约 8 MiB stack；
  `FTaskTagScope` 为 `ERenderingThread | EParallelThread`，active low-level task
  名为 `GraphTask`。这证明已进入真实 UE 任务上下文，但不是根因或修复证据。
- **证据边界**：`UE::Tasks::Private::CurrentTask` 在优化构建中为 optimized out，
  `tpidr_el0` 仍不可读；独立 replay PASS、现有负向对照仍不等于完整排除时序、
  内存破坏或驱动缺陷。详见 audit 9.98。
- **独立停服缺陷**：静态析构顺序根因已确认，但 guard 提案未应用、未验证修复效果，
  不能用于解释或修复启动期编译器崩溃。
- **可行性未定**：仍有调查路线；驱动不可更换的约束下，不能保证能在应用侧修复。
  840 tests / 56 skipped / 0 failures 是当前本地回归结果，不是 GB10 产品验收。

## 决策队列

### 1. GB10 驱动 —— 厂商包发送与上下文差分并行（驱动不可换）

| 项 | 内容 |
| --- | --- |
| 包 | `artifacts/gb10-driver-report/20261003T052318Z/` |
| 文件 | `report.md` / `report.json` / `COVER-NOTE.md` |
| 要做的 | 通过 NVIDIA 对接渠道发出这三个文件 |

> **先说清这一条的实际价值**：发出买的是「信息」而非修复，四种回复里只有两种对你有用——
> ①「你们违反了哪条输入约束」或 ②「有受支持的绕法」，这两条我们自己能改；
> ③「已知缺陷、某版本已修」不能直接用于本机（驱动被 BSP 锁住）；
> ④「无法复现」需要继续补上下文，而不是说明问题已排除。故障非确定性 + 抽出的输入单独能过，
> 对方可能要求稳定的最小复现，当前尚未具备。
> **10 月 5 日 GPU 当前可达，本地实验可以继续，不应再以缺 GPU 挂起。**

- 证据已齐：2026-10-01 全部 **39** 个 GB10 运行、**37 FAIL / 2 PASS**，**缺失证据：none**。
  **注意证据的实际强度**（不要被手写摘要夸大）：带符号化驱动栈的只有 **2** 个记录
  （`…1DcLyl`：13 帧 `libnvidia-glvkspirv` + 9 帧 `libnvidia-eglcore` + 2 帧 `libc`；
  `…rJsZfI`：1 帧 `eglcore` + 2 帧 `libc`），其余只有状态与退出码。这份包的**核心不是栈的
  数量**，而是那条判别实验（同输入独立 replay 0.016 s 通过）。
- 同轮扫描给出了「有没有绕法」的答案：**验证层开着 2/10 通过、关掉 0/29 通过**——所以验证层
  不是可用的绕法（开着仍然 8/10 崩）。
- 封面已把该问的写死：三选一（580.173.02 是否已知缺陷 / 我们违反了 `glvkspirv` 的哪条输入
  约束 / 有无官方绕法）+ **驱动不可更换**的约束（GB10 驱动随 BSP，换驱动是系统级风险）。
- 判别证据是**同一输入在同一台机器上的独立进程通过**，说明尚缺进程上下文；
  它**不能证明应用层没有可验证的修复**。厂商反馈和本地诊断可以并行。
- 10 月 5 日基线再次 SIGSEGV；loader binding 对照未观察到 C++/LLVM/内存函数被 UE 接管；
  独立 compute replay 同日约 0.011 s 通过。证据与下一项见 audit 9.95 和 todo。

### 2. 引擎停服缺陷 —— 修复落点由谁定（提案已备好，未应用）

| 项 | 内容 |
| --- | --- |
| 提案 | `scripts/carla/patches/ue-gpumessaging-shutdown-guard.patch` |
| 递交形态 | `artifacts/ue-gpumessaging-shutdown/20261003T062016Z/` |
| 要决定的 | (a) 这个 guard 是否符合上游对 `GPUMessage::FSocket` 生命周期的设计意图；(b) 落在哪个分支/版本 |

- 缺陷：`GPUMessage::GSystem` 的 handler 表在 `exit()` 静态析构里先被销毁（实测 order 6），
  `Nanite::FGlobalResources::ReleaseRHI`（order 7）随后析构持有 socket 的 `FFeedbackManager`，
  于是 `RemoveHandler` 在已销毁的表上断言，紧随的 `TMap::Remove` 动已释放存储；无调试器时以
  `Signal 11` / exit 139 收尾。
- **不是我们 fork 引入的**：涉及的三个文件都不在 fork delta 里。
- 影响面：guard 在**共用注销路径**上，覆盖所有 GPU message 使用者（本版还有 `LightGrid`、
  `VirtualShadowMapCacheManager`）。
- 有测试钉住两个方向：提案**仍可干净应用**且**仍未在树里**
  （`tests/test_carla_ue_shutdown_upstream_proposal.py`）——应用与否必须是显式决定。

### 3. fork 改动固化方式 —— **正式产物已完成；实验诊断暂未固化**

正式产物仍来自已提交并推送的 `gottaBoy` 分支；本轮另加的
`VulkanPipeline.cpp` 诊断字段属于实验性未提交改动，不应混入正式验收树。

- carla `dgx-arm64` → `origin`（`gottaBoy/carla`）：`234caf5..f6cbc59`
- ue `dgx-arm64` → `gottaBoy`（已设 upstream）：`693d44c72..5502950e1`，含 5 个此前从未推送的 commit
- 记录随之重生成：provenance 与 manifest 现在都是 `tracked_dirty=0` / `untracked=0`，
 `tracked_diff_sha256` = 空串的 sha256（`e3b0c442…`）；两个冻结 delta 补丁变成 0 字节，
 这正是「工作树干净」的断言——有人再弄脏它，`make carla-fork-delta-verify` 会失败。

（原先这里写的是「需要你定规则」；规则已按上面的方式定下并执行。若将来要改回「冻结增量」模型，
只需停止提交 fork 并重新 `make carla-fork-delta`。）

> 当前边界：正式 stage/binary 未改；UE fork 工作树因本轮诊断 marker 临时变脏 1 个文件。
> `make carla-fork-delta-verify` 在这项实验未固化前不应被当作 PASS。

## 当前下一步

1. **补齐调用前状态**：task context 已与故障 compute driver-entry 关联；
   还需把 shader/cache/layout 与调用前有界内存状态做单变量对照。
2. **因果对照与最小修复**：做有判别力的单变量实验，实施有证据支持的修复并验证；
   若落到无应用侧绕法的驱动缺陷，再请求厂商支持或决定验收路线。
3. **逐级验收**：Town10 世界就绪 → 同步 tick → RGB/LiDAR 有效且对齐 → 长时间稳定运行。
   停服修复落点另行决定，应用后须独立验收；GB10 停服采集和 `Shutdown:` 分类仍在待办清单。

（原先列在这里的「补 `ReleaseResource` 的调用者」已完成到不再必要：`exit` 断点已把两次 release
都归到 `inside-exit-handlers`，机制由源码 `RenderCore/Public/RenderResource.h:568` 确认。）

## 现在可信任的状态（凭据）

- `make test-local` → **840 tests / 0 failures / 56 skipped**
- manifest schema 3：capture **PASS** + verify **PASS**
- 正式提交态的 `make carla-fork-delta-verify` 曾 **PASS**；当前实验性
  `VulkanPipeline.cpp` 改动使它按预期报告 drift，尚未固化
- 未完成项的唯一来源：`docs/carla-dgx-todo.md`
