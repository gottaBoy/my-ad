# CARLA Interchange 回归合约

当前 `carla-interchange` 是一个 DGX 原生 ARM64 的静态 Interchange 子阶段。它由
`carla-assimp`、`carla-ufbx` 前置阶段和 `probe-arm64-interchange-nodes.sh` 组成：

```text
make carla-interchange
  -> carla-assimp
  -> carla-ufbx
  -> carla-build 容器中的 probe-arm64-interchange-nodes.sh
```

## 阶段身份

- `stage_id`: `ue-ufbx-interchange-static`
- `scope`: `ufbx to UE Interchange static nodes and mesh payloads; not translator/worker, material shading, factory assets, Editor or Cook`
- 上游 prerequisite 必须是 `ufbx-fbx-static-backend`，scope 必须精确匹配
  `ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC`
- 阶段 report 使用 `stage_report.py` schema v1。只有 `exit_code == 0`、所有
  required checks 为 `PASS`、证据文件仍可按 SHA256 校验、且上游 report 递归验证为
  `PASS` 时，report 才能是 `PASS`。

## 入口和平台边界

入口固定使用 `compose.carla-arm64.yaml` 的 `build` profile，并在容器内要求：

- Docker 容器存在（`/.dockerenv`）
- `uname -m` 为 `aarch64`
- UE target 为 `CarlaInterchangeProbe Linux Development -architecture=arm64`
- Assimp、ufbx 安装路径分别来自 `assimp-arm64/6.0.5` 和 `ufbx-arm64/0.23.0`

因此本阶段不接受 x86/amd64 容器或二进制。测试只验证门禁和报告逻辑，不伪造
硬件、UE 运行或 ARM64 成功结果。

## 实际验证内容

checker 的 required checks 是：

```text
build, architecture, process, graph, payload, geometry
```

scene probe 必须：

1. 生成 `stage=ue-ufbx-interchange-static`、精确 scope、`status=PASS` 且空
   `error` 的 scene report；
2. 报告正整数的 node、序列化字节数和 payload 几何计数，且 node 总数可由
   scene/mesh/material node 数相加得到；
3. 报告输入 FBX 的 SHA256，并标记 graph/payload round-trip；
4. 在 result directory 生成非空 `nodes.bin`；
5. 对每个 mesh 按 key 查询 identity、translated、mirrored 三种请求，报告
   `payload_contract_version=2` 和 `payload_count == 3 * mesh_nodes`；
6. 生成与 `payload_count` 一致数量的普通 `*.payload` 文件，不能是 symlink；
7. 逐请求记录 mesh UID、source-bound key、request UID、文件、字节数、几何计数、
   Dispatcher JSON 和 round-trip；checker 对全部文件重新计算 SHA256、核对总计；
8. 实际调用 source-scene 自检和 payload 自检，禁止旧单 mesh 报告通过新门禁。

## Payload 协议

这是 UE 内存对象协议，不是文本或自定义顶点格式：

1. `FetchStaticPayload` 使用区分大小写的完整 source-bound key 查找 mesh，复制
   `FMeshDescription`，拒绝未知、重复或旧源文件 key；
2. 应用请求 `FTransform` 一次；UE 处理镜像绕序和 binormal sign，额外采用
   尺度稳健的方向归一化，避免小尺度法线清零，并拒绝 float 精度下坍塌的三角形；
3. 用 `FMeshDescription::Serialize` 写入 payload；
4. 紧接着追加 `bool bSkinned = false`；
5. 文件名使用 source-bound key 与规范化矩阵的 BLAKE3 请求摘要加 `.payload`，
   内容 SHA256 单独记录；相同几何但不同 key 不会因内容相同而覆盖或失败；
6. 读回后反序列化，并要求 reader 完全消费、`bLoadedSkinned == false`，以及
   完整 MeshDescription 再序列化字节一致。变换前后的所有 UV、颜色、材质槽、
   逐面绑定、法线和 tangent 分别检查，另有同计数属性篡改反例。

请求由真实 `FJsonFetchMeshPayloadCmd` 编解码，结果由其 `JsonResultParser`
读写 `ResultFile`；测试调用解码后的 key/transform。此处验证的是进程内协议类，
没有启动 Dispatcher socket、InterchangeWorker 或真实 FInterchangeFbxParser。
该 API 不缓存结果，不宣称并发文件发布安全；程序仍使用唯一 run 目录。

同时，Interchange graph 中的 `UInterchangeMeshNode` 必须使用
`EInterchangeMeshPayLoadType::STATIC`，保留 payload key、顶点/多边形计数、材质
slot 依赖和 scene instance 绑定。node graph 通过 `SaveToFile`/`LoadFromFile`
往返验证。

## 失败和证据门禁

失败也必须留下 `stage-report.json`，但不能被提升为成功：

- build、架构、进程、scene 字段、graph、payload 或 geometry 任一失败都会使
  report 为 `FAIL`；每个 mesh 必须覆盖全部三种请求变换；
- 缺少或失败的 ufbx prerequisite 会使 `build` 失败，且不会改写上游 report；
- `stage_report.py validate` 会重新检查 stage identity、scope、所有证据 SHA256
  和 prerequisite SHA256；
- runner 在 UE build 后、scene 执行前后复核 `source-files.sha256`，其中包含
  fixture、runner、checker、reporter 和 Program/桥接源码。若
  scene 进程原本返回 0 但源码哈希复核失败，进程码被改为 `125`，禁止产生成功
  结果；
- 已存在的 stage report、node graph 或 payload 不会被覆盖。

## 回归测试

### Source-scene selftests

Program 支持独立的 source-scene API 自检模式：

```bash
CarlaInterchangeProbe -source-scene-self-test -output=/path/to/scene-selftests.json
```

该模式实际调用 `CarlaUfbxMesh::RunSceneSelfTests(int32&, FString&)`，并输出：

- `stage`: `ue-ufbx-scene-selftests`
- `scope`: `source-scene API selftests ONLY; not Interchange worker, Editor or Cook`
- `self_tests`: API 返回的实际 passed count
- `status`、`error`、process exit code

API 若返回成功但 passed count 为 0，Program 会将结果改为失败。该模式不生成
Interchange graph、mesh payload 或 Editor/Cook 结果。`scene-test` 也调用该自检，
但另行发布 contract v2 的逐请求报告；旧 stage/scope 边界不变。

2026-09-16 native evidence:
`artifacts/carla/interchange-nodes-20260916T022331Z-QjoaS6/stage-report.json`。
两个 Geometry、六个独立 payload、20 项 source-scene 自检、27 项 payload 检查通过。
最后一组覆盖 1e-8 缩放及镜像、有限大平移坍塌、非法 quaternion、同内容不同 key、
UV1/slot/逐面绑定损坏，以及真实 Dispatcher 命令与结果编解码。

仅运行本阶段独立测试和语法检查：

```bash
python3 -m unittest -v tests/test_carla_interchange.py
python3 -m unittest -v tests/test_carla_ufbx_scene_gate.py
python3 -m py_compile tests/test_carla_interchange.py
python3 -m py_compile tests/test_carla_ufbx_scene_gate.py
```

这些测试使用临时目录、stdlib 和报告 fixture。它们可以验证失败报告、身份、
scope、哈希、入口命令和 C++ payload 协议；它们不会把缺少 UE、Docker、GPU 或
ARM64 硬件的环境伪装成 `PASS`。


## F1 Parser Facade Contract

Historical contract-only layer: the newer **real parser** static integration is
documented in `carla-native-parser.md` and replayed with
`make carla-interchange-parser JOBS=4`. These are distinct stages.

F1 adds a backend-neutral facade under `scripts/carla/interchange/` without changing any UnrealEd public SDK interface or UE Interchange parser header.


The audited UE contract is:


- `FInterchangeFbxParser::LoadFbxFile` loads an FBX source and writes or fills an Interchange node container.
- `FInterchangeFbxParser::FetchMeshPayload` receives the original payload key plus `FTransform MeshGlobalTransform` and returns a result payload identity that includes the transform.
- The worker accepts `LoadSource` and `Payload` JSON commands with `TranslatorID=FBX`; mesh payload commands carry `PayloadKey` and `GlobalMeshTransform`.
- The worker process is started with `-ServerPID`, `-ServerPort`, `-InterchangeDispatcherVersion`, and `-ResultFolder`, then returns a task state, JSON result filename, and JSON messages.

The new facade API is intentionally smaller and backend-neutral:


```cpp
carla::interchange::f1::ParserFacade::LoadScene(
    const LoadSceneRequest&, Scene&, Error&);
carla::interchange::f1::ParserFacade::FetchMeshPayload(
    const MeshPayloadKey&, const Transform&, MeshPayloadResponse&, Error&);
```

`LoadScene` preserves the four real FBX conversion settings and validates nonempty, unique mesh payload keys. `FetchMeshPayload` forwards every requested transform without baking or silently dropping it, and validates the backend result identity and payload file.

F1 currently supports static mesh payloads only. Animation and morph-target requests return `UnsupportedAnimation` and `UnsupportedMorph` with explicit messages before reaching the backend. This is a deliberate contract error, not a claim that the existing UE FBX parser cannot expose those payload types.

The C++ contract test uses a recording backend, compiles and runs the facade, checks two distinct mesh keys and two distinct transforms, and checks both unsupported errors. It does not load an FBX, start `InterchangeWorker`, call `FInterchangeFbxParser`, or produce a parser/worker integration PASS.


Run the focused contract test only in the native ARM64 Docker container:


```bash
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e CXX=/usr/lib/llvm-18/bin/clang++ \
  carla-dev python3 -m unittest -v tests/test_carla_interchange_parser_facade.py
```
