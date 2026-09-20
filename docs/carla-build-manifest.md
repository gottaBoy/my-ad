# CARLA 构建留痕

本项目的构建留痕分为两层：

- `config/carla/change-ledger.json` 说明每个 ARM64 兼容改造的原因、文件、验证和边界；
- `capture_build_manifest.py` 记录某一次构建使用的主工程、CARLA、UE 源码状态、
  tracked patch、可重放的 untracked 文件快照，以及 `source.lock` pin 与实际 HEAD 的
  漂移报告。当前格式为 schema 2；旧的仅 hash manifest 不会被自动视为完整源码备份。

阶段报告只证明一个具体阶段。manifest 记录生成该阶段时的输入，二者不能互相替代。

## DGX 一键捕获

所有操作只在 DGX Spark 的 native ARM64 Docker 中执行：

```bash
make carla-manifest
```

Make 会挂载以下路径：

```text
/repo                       主工程，只读
/workspace/carla            CARLA 源码，只读
/workspace/unreal-engine    UE 源码，只读
/artifacts                  构建产物和报告，可写
```

容器内不需要 Docker socket。工具通过 `/.dockerenv` 和 `uname -m` 记录 native ARM64
环境；QEMU 或 `linux/amd64` 不满足此门禁。

每次成功捕获会在 `artifacts/carla/build-manifests/` 生成唯一目录，内容包括：

- 主工程、CARLA、UE 的 HEAD、分支和完整 porcelain 状态；
- 每个仓库相对于 HEAD 的 binary Git patch 及 SHA256；
- Git 规则之外的全部 untracked regular files：每个文件复制到
  `untracked/<repo>/<relative-path>`，并记录原相对路径、SHA256、字节数、Unix mode
  和 executable 标志；
- `Makefile`、Compose、`source.lock`、变更台账和 `scripts/carla` 的哈希；
- 实际命令、容器架构、相关环境变量和被过滤的敏感变量名；
- 相同目录中的 `manifest.json`。

CARLA 和 UE 是独立挂载根，不要求位于主工程目录内。manifest 保存命名根的相对身份，
例如 `../../workspace/carla`；验证必须使用同一 Docker 挂载布局。

## 严格验证

`MANIFEST` 使用容器内路径：

```bash
make carla-manifest-verify \
  MANIFEST=/artifacts/carla/build-manifests/<run>/manifest.json
```

验证会重新检查 native ARM64 环境、三个仓库的 HEAD/分支/状态、binary patch、untracked
集合、关键文件集合和所有 SHA256。任何代码、兼容补丁、脚本、配置、台账或源码版本变化
都会失败。已有 artifact 和 manifest 不会被覆盖。

## source.lock 漂移

`source.lock` 记录的 pin commit 与三个仓库的实际 HEAD 会在每次 capture 时对比，并作为
`source_lock_drift` 写入 manifest：

- `matches`：pin 与 HEAD 一致；
- `drifted`：pin 与 HEAD 不一致，`drifted` 列表记录漂移的 lock source；
- `unpinned`：lock 未覆盖该仓库；
- `untracked_repository`：lock 覆盖了未被捕获的仓库（当前仅 `carla` 和
  `unreal_engine` 参与对比，其余 lock source 记入 `uncovered_lock_sources`）。

漂移只记录、不回写 lock。verify 会先重算漂移报告并与 manifest 比对，因此 capture 之后
对 lock 或 HEAD 的任何修改都会以 `source_lock drift record changed` 失败，而不是等到
git 状态差异才报错。缺少 source.lock 是允许的（`lock_present: false`），但捕获后新增
lock 同样会失败；存在 `sources:` 但没有任何合法 pin 属于格式错误，capture 返回 FAIL。


Manifest 不是签名证明，也不代表 Editor、Cook、RPC 或传感器通过；它只证明记录的输入
在复验时保持一致。构建失败、缺少文件、非 regular file、重复 JSON key、路径逃逸和
解析失败都必须返回非零结果。
 
## 重放边界
 
一次留痕由三部分共同组成：仓库 HEAD 确定基线，tracked binary patch 还原 tracked
改动，untracked snapshot 还原 Git 忽略规则之外的新增 regular files。verify 会同时
核对 artifact 快照和 live 源；工具不会自动恢复、提交、重置或覆盖源码。
 
这不是 fresh clone，也不是完整的 UE 或 ignored 文件归档。它只证明被记录输入可被
重放并在验证时保持一致；依赖下载、构建工具链、Editor、Cook、RPC 和传感器结果仍
需要独立证据。

## 兼容性台账

变更台账至少覆盖：

- ARM64 target triple、UE HostLinux 工具链和 CMake 路径；
- ShaderConductor、DXC、hlslcc、ISPC 和音频 PIC 依赖；
- Assimp/ufbx 的内存边界、坐标单位、法线绕序、静态网格边界；
- Interchange 节点、载荷文件和序列化协议；
- GB10 Vulkan 透传和 runtime capability；
- RPC、传感器、USD 和 Editor 诊断配置。

每一项都必须写明为什么需要、验证命令、适用范围和未解决的后续工作。新的兼容实现
不能删除旧条目；应新增替代条目，并保留旧阶段 artifact 供回归比较。
