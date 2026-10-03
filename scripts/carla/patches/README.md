# `scripts/carla/patches`

这个目录里有两类补丁，**不能混为一谈**。

## 1. 精选功能补丁（其余 `*.patch`）

针对单一关注点的补丁，例如 `usd-arm64-opt-out.patch`、`landscape-editor-boundary.patch`。
它们由 `scripts/carla/probe-arm64-asset.sh` 等脚本**按文件名显式应用**，同时也是 fork
分支上已提交改动的记录：本目录 23 个补丁中有 20 个的内容已经在 fork HEAD 里
（对当前树反向可应用）。

其中 3 个当前既不适用、也未提交——`animation-editoronly.patch`、
`legacy-fbx-diagnostic.patch`、`legacy-fbx-node-metadata.patch`。它们保留为历史记录，
但不是当前树的一部分。

## 2. 工作树增量（`fork-working-tree-delta-*.patch`）

`git diff --binary HEAD` 的原样导出，即 fork 上**尚未提交**的 tracked 改动：

| 文件 | 内容 |
| --- | --- |
| `fork-working-tree-delta-carla.patch` | CARLA fork 的未提交 delta |
| `fork-working-tree-delta-ue.patch` | UE fork 的未提交 delta |

**2026-10-03 更新：两个 delta 现在都是 0 文件 / 0 字节。** 因为两个 fork 的改动已经提交并推送到
`gottaBoy` 下的 `dgx-arm64`（carla `f6cbc59b…`、ue `5502950e…`，两者都是 `tracked_dirty=0`、
`untracked=0`），不再有「未提交的树」可冻结。**空文件在这里是有意义的记录**：它断言两棵 fork
工作树是干净的，并会让 `verify` 在有人重新弄脏工作树时立刻失败。

它们**不被任何脚本自动应用**。作用是把「产出二进制的树」写进仓库，而不是只存在
`artifacts/` 下那份时点 manifest 里。

维护方式：

```bash
make carla-fork-delta         # 重新冻结（改动 fork 工作树之后）
make carla-fork-delta-verify  # 检查冻结的补丁是否仍等于当前工作树
```

`verify` 逐字节比对，漂移时打印 `DRIFT` 并以非零退出。摘要口径与
`capture_build_manifest.py`、`report_fork_provenance.py` 完全一致
（同一条 `git diff --binary HEAD`），所以三份记录里的同一个 sha256 指同一件事。

改动精选补丁**不会**让增量补丁失效；只有 fork 工作树本身变化才会。
见 `docs/carla-dgx-audit.md` 9.89，以及 9.93（提交并推送之后为什么这两个文件是空的）。

## 3. 提案（`ue-gpumessaging-shutdown-guard.patch`）

**这一条既没应用、也没提交**，与上面两类都不同：它是对引擎侧缺陷的修复提案，等上游/维护者
决定。缺陷是 `GPUMessage::GSystem` 的 handler 表在 `exit()` 的静态析构里被 `~FSystem` 销毁
**之后**，`Nanite::FGlobalResources::ReleaseRHI` 仍然析构持有 `GPUMessage::FSocket` 的
`FFeedbackManager`，于是 `FSocket::Reset()` 在已销毁的表上调用 `RemoveHandler`：
`check(MessageHandlers.Contains(MessageId))` 断言，紧随的 `TMap::Remove` 动已释放存储。
无调试器时这条路径以 SIGSEGV（exit 139）收尾。完整证据见 `docs/carla-dgx-audit.md` 9.91 与
`artifacts/carla/ue-shutdown-crash-20261003T061541Z-u8dwTD/`（写监视点栈 + 共用 `order` 时间线）。

提案内容：给 `FSystem` 加一个析构函数，在**成员析构之前**置位 `bIsBeingDestroyed`，
`FSocket::Reset()` 在该位为真时只清自己的 id、不再反向注销。

```bash
# 只检查，不改树
git -C third_party/unreal-engine apply --check \
  "$PWD/scripts/carla/patches/ue-gpumessaging-shutdown-guard.patch"
```

`tests/test_carla_ue_shutdown_upstream_proposal.py` 会持续验证三件事：补丁对当前 fork
**仍可干净应用**、**仍然没有被应用**、且只动 `GPUMessaging.cpp` 一个文件（改动面）。
一旦有人把它应用了，测试会失败——这是有意的，应用与否是一个需要显式做出的决定。
