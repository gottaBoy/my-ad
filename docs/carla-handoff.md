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

### 3. fork 改动固化方式 —— 需要你定规则

**实测现状（2026-10-03）**：

| fork | 分支 | remote | 未推送 | 未提交 | 未跟踪 |
| --- | --- | --- | --- | --- | --- |
| `third_party/carla` | `dgx-arm64` | `origin` = `gottaBoy/carla` | **ahead 2** | 1 个 tracked（`DefaultInput.ini`） | 6 |
| `third_party/unreal-engine` | `dgx-arm64` | `gottaBoy`（另有 `origin` = `CarlaUnreal/UnrealEngine`） | **分支无 upstream（从未推送）** | **92 个 tracked** | 5 |

**建议：提交并推送到 `gottaBoy` 下的分支**，理由是现在「产出二进制的树」的唯一副本是这台机器上的**脏工作树**：

1. manifest / provenance 记的是 fork HEAD **加上未提交 delta 的 sha256**——只拿 HEAD 复现不出构建；
2. 本轮我已经因为一次误 `git checkout` 丢掉过 46 条 ledger；同样的事故发生在 92 个未提交文件的 UE fork 上，丢的是 ARM64 构建本身；
3. carla fork 的 `origin` 已经就是 `gottaBoy/carla`，只是那 2 个 commit 没推。

**顺序很重要（会连锁失效）**：

```bash
# 1) 先分拣未跟踪项（例如 UE 的 Saved_shaderdiag/ 是诊断输出，应进 .gitignore 而不是提交）
# 2) 两个 fork 各自提交 dgx-arm64
# 3) 推送：carla → origin；ue → gottaBoy（设 upstream）
# 4) 再重生成主仓记录（否则 --verify 报 DRIFT）
m make carla-fork-provenance && make carla-fork-delta
```

第 4 步不能省：提交之后「未提交 delta」变成空，冻结的 `fork-working-tree-delta-*.patch` 与 manifest 的 `tracked_diff_sha256` 都会对不上。

**需要你确认的点**：推送要你的凭据（我这里没有）；`gottaBoy/UnrealEngine` 若是公开仓，请确认 UE 源码的发布方式符合你们对 Epic EULA 的处理（本项目的 `origin` 本来就是公开的 `CarlaUnreal/UnrealEngine`，但这是你的判断）。若决定不推送，现有回退是主仓里冻结的 delta 补丁——但它是 `git diff --binary HEAD` 的文本导出，**不覆盖未跟踪文件**（那部分只在 `artifacts/` 的 manifest 快照里，而它不入库），所以回退比推分支弱。

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
