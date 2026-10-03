# CARLA / DGX 交接页

只放**需要人做决定的事**：谁卡住了、要发什么/要决定什么、以及**不依赖这些决定**的下一步。
证据与推导在 `docs/carla-dgx-audit.md`，未完成清单在 `docs/carla-dgx-todo.md`。
状态时点：**2026-10-03**。

## 决策队列

### 1. GB10 驱动 —— 把厂商包发出去（唯一出路；驱动不可换）

| 项 | 内容 |
| --- | --- |
| 包 | `artifacts/gb10-driver-report/20261003T052318Z/` |
| 文件 | `report.md` / `report.json` / `COVER-NOTE.md` |
| 要做的 | 通过 NVIDIA 对接渠道发出这三个文件 |

> **先说清这一条的实际价值**：发出买的是「信息」而非修复，四种回复里只有两种对你有用——
> ①「你们违反了哪条输入约束」或 ②「有受支持的绕法」，这两条我们自己能改，是**唯一不换驱动
> 就能解锁的路**；③「已知缺陷、某版本已修」对**你没用**（驱动被 BSP 锁住）；④「无法复现」也算
> 有用——它让这条线正式作废，你才好去重定验收。**反方理由**：故障非确定性 + 抽出的输入单独能过
> ⇒ 对方最可能回「请给可复现样本」，而我们没有、也造不出（要有 GPU 时间做上下文刻画）。**所以
> 只有在你有能被答复的渠道时才值得发**；没有渠道的话，这份包是惰性的，真正的决定是「重定 GB10
> 验收范围 vs 申请 GPU 时间」。

- 证据已齐：2026-10-01 全部 **39** 个 GB10 运行、**37 FAIL / 2 PASS**，**缺失证据：none**。
  **注意证据的实际强度**（不要被手写摘要夸大）：带符号化驱动栈的只有 **2** 个记录
  （`…1DcLyl`：13 帧 `libnvidia-glvkspirv` + 9 帧 `libnvidia-eglcore` + 2 帧 `libc`；
  `…rJsZfI`：1 帧 `eglcore` + 2 帧 `libc`），其余只有状态与退出码。这份包的**核心不是栈的
  数量**，而是那条判别实验（同输入独立 replay 0.016 s 通过）。
- 同轮扫描给出了「有没有绕法」的答案：**验证层开着 2/10 通过、关掉 0/29 通过**——所以验证层
  不是可用的绕法（开着仍然 8/10 崩）。
- 封面已把该问的写死：三选一（580.173.02 是否已知缺陷 / 我们违反了 `glvkspirv` 的哪条输入
  约束 / 有无官方绕法）+ **驱动不可更换**的约束（GB10 驱动随 BSP，换驱动是系统级风险）。
- 为什么不能自己绕：判别证据是**同一输入在同一台机器上的独立进程 0.016 s 通过**——故障依赖
  故障进程的运行时上下文，应用层没有可回避的输入。
- **这是唯一可能解开 GB10 主线（Town10 出合法 RGB/LiDAR）的动作。**

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

### 3. fork 改动固化方式 —— **已定并已完成（2026-10-03）**

结论：**提交并在 `gottaBoy` 下推送**，因此「产出二进制的树」不再只存在于某台机器的脏工作树里。

- carla `dgx-arm64` → `origin`（`gottaBoy/carla`）：`234caf5..f6cbc59`
- ue `dgx-arm64` → `gottaBoy`（已设 upstream）：`693d44c72..5502950e1`，含 5 个此前从未推送的 commit
- 记录随之重生成：provenance 与 manifest 现在都是 `tracked_dirty=0` / `untracked=0`，
 `tracked_diff_sha256` = 空串的 sha256（`e3b0c442…`）；两个冻结 delta 补丁变成 0 字节，
 这正是「工作树干净」的断言——有人再弄脏它，`make carla-fork-delta-verify` 会失败。

（原先这里写的是「需要你定规则」；规则已按上面的方式定下并执行。若将来要改回「冻结增量」模型，
只需停止提交 fork 并重新 `make carla-fork-delta`。）

## 不依赖上述决定的下一步（可继续推进）

1. **把停服采集接到 GB10 profile**：同一套字段与断言，但需要 GPU。
2. **`Shutdown:` 分类接进 GB10 运行记录**，让 GB10 的停服行为也有同一口径的状态字段。
3. **扩大 `exit` 这类「符号在动态库里」的断点的使用**（本轮已修好安装器：允许 pending 并在运行
   结束后回读绑定情况），可用于其它只有运行时才存在的符号。

（原先列在这里的「补 `ReleaseResource` 的调用者」已完成到不再必要：`exit` 断点已把两次 release
都归到 `inside-exit-handlers`，机制由源码 `RenderCore/Public/RenderResource.h:568` 确认。）

## 现在可信任的状态（凭据）

- `make test-local` → **815 tests / 0 failures / 56 skipped**
- manifest schema 3：capture **PASS** + verify **PASS**
- `make carla-fork-delta-verify` → **PASS**（提案未应用，delta 未变）
- 未完成项的唯一来源：`docs/carla-dgx-todo.md`
