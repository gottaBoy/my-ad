# UE FBX Migration Boundaries

Read-only review, September 14, 2026. UE base commit:
`693d44c72ab03d7959e882fab7c8296308194a18`. Evidence describes the local working
tree, not an assumed clean checkout.

The main implementation has initial external UBT Program and reusable module
sources for Assimp static scenes -> genuine UE `FMeshDescription`, using
CoreUObject/MeshDescription/StaticMeshDescription without UnrealEd. **The bridge
has not been compiled at this snapshot.** Even successful bridge tests would
not replace `FFbxImporter`, `FFbxExporter`, the SDK-dependent Interchange backend,
or establish Editor/Cook success.

Update after the review snapshot: the native static bridge now builds and passes
25 self-checks and six process cases. See `carla-dgx-audit.md` section 9.4 for the
actual run evidence, allocator/copy fixes and data validation changes. This does
not change the SDK dependencies and remaining migration boundaries below.

September 15 update: the ufbx static bridge also has native evidence, and a
standalone Interchange Program has exercised BlenderCube nodes and a static
payload. That Program is not the actual `InterchangeFbxParser`/worker/factory
integration. Current remaining work and multi-agent ownership are tracked in
`carla-execution-plan.md`; the lexical inventory below remains a review snapshot.

## Inventory Contract

September 17 update: the evaluated headers-only CARLA target selects seven direct
FBX consumers. Four actual translation units now compile natively, and the
`FbxNodeInfo` data members described in the historical table below have been
migrated to an SDK-free UE value type. The importer and scene factory use the
existing converter at production boundaries. This is a scoped compile/data
contract result, not importer runtime parity. See `carla-legacy-fbx.md` for
reports, replay patches, scalar fixes and the required-feature matrix.

```bash
python3 scripts/carla/inventory_fbx_dependencies.py --ue-root third_party/unreal-engine
python3 -m unittest discover -s tests -p test_carla_fbx_inventory.py -v
```

JSON goes to stdout; the tool does not write into UE. Paths are UE-root-relative,
with one-based physical line/column evidence, per-matched-file hashes and a digest
of all inspected inputs. Sorted output excludes timestamps and absolute paths.

- C++ scope: `Engine/Source/Editor`, `Engine/Source/Developer`, and
  `Engine/Plugins/Interchange`. Other plugins' C++ and CARLA project sources are
  outside this inventory, not declared dependency-free.
- Build scope: `.Build.cs` under `Engine/Source` and `Engine/Plugins`.
  Generated/cache directories and directory symlinks are not traversed.
- SDK names are checked against declarations in local SDK headers. UE wrappers
  such as `FFbxImporter`, `UFbxFactory`, `FbxSceneInfo`, `FbxNodeInfo` are separate.
- Comments/quoted examples do not create C++ type/include uses. Recognized build
  calls distinguish dependency literals from diagnostics, paths and definitions.
- **Lexical references, not a semantic call graph:** no preprocessing, macro
  expansion, target/module reachability, or UBT execution. All branches remain
  visible. A same-spelled variable can match an SDK type name: `FbxImporter` in
  `Engine/Source/Editor/UnrealEd/Classes/Factories/FbxFactory.h:66` is actually a
  parameter of type `UnFbx::FFbxImporter*`. Macro alias `EFbxRotationOrder`, defined
  in SDK `fbxsdk/core/math/fbxmath.h:94`, remains explicitly unclassified.

The scan inspected 10,710 C++ files, 2,033 build rules and 235 SDK headers. It found
3 direct SDK include sites, 12 modules with explicit `"FBX"` dependency literals,
and 44 files with SDK-type-name lexical matches. **44 is not a semantic dependency
count or a full migration workload estimate.** Inspect the source evidence.

## Build and Backend Boundaries

Abbreviations for evidence, relative to the UE root:

```text
U = Engine/Source/Editor/UnrealEd
M = Engine/Source/Editor/MovieSceneTools
I = Engine/Plugins/Interchange/Runtime/Source/Parsers/Fbx
```

| Evidence | Consequence |
|---|---|
| `U/UnrealEd.Build.cs:318`, FBX literal at `:319` | Direct third-party dependency outside the preceding Win64 branch. Disabling a plugin does not remove UnrealEd's FBX requirement. |
| `M/MovieSceneTools.Build.cs:98` | Independent direct FBX dependency. |
| `I/InterchangeFbxParser.Build.cs:65` | The parser module itself requires FBX. |
| `Engine/Source/Programs/InterchangeWorker/InterchangeWorker.Build.cs:38` | Worker also declares FBX, not only its parser-module dependency. |
| `Engine/Source/ThirdParty/FBX/FBX.Build.cs:62`, `:73`, `:74` | Unix architecture-specific library path, linking and staging of `libfbxsdk.so`; deleting a directory check supplies no implementation. |
| `I/Public/InterchangeFbxParser.h:25`, `:66`, `:119` | Public facade uses UE containers/payloads and owns a private `FFbxParser`: a plausible backend replacement boundary. |
| `I/Private/FbxMesh.h:93`, `:98`, `:103`, `:108` | Implementation still accepts SDK scenes/converters/meshes/shapes for static, skinned and morph payloads. |
| `U/Private/Fbx/FbxStaticMeshImport.cpp:378`, `:390` | Legacy static import fills MeshDescription while operating on SDK nodes/meshes, UStaticMesh and material/reimport state. Matching the output type does not replace this workflow. |

Other modules with direct FBX literals: ControlRigEditor, LevelSequenceEditor,
SequencerScriptingEditor, HairStrandsEditor, ApexDestructionEditor, and Datasmith
FBX/DeltaGen/VRED translators. The JSON records exact rules/lines/operations.
Presence does not prove they are enabled for CARLA; each enabled consumer needs
its own migration or an explicitly agreed reduced-feature disposition.

## Public SDK Leakage

Unqualified line numbers in the first six rows refer to `U/Public/FbxImporter.h`.

| Interface and evidence | Exposed SDK dependency |
|---|---|
| `:76` | Public header includes `fbxsdk.h`. |
| `FFbxAnimCurveHandle`, `FFbxAnimPropertyHandle`, `FFbxAnimNodeHandle` at `:287`, `:309`, `:339` | SDK curve pointer, `EFbxType`, and nested `FbxNodeAttribute::EType`. |
| `FFbxCurvesAPI` at `:395` | Public `FbxScene*` state. |
| UE `FbxNodeInfo` at `:424`, `:425`, `:426`; `FbxSceneInfo` at `:450` | By-value SDK matrix/pivots inside UE hierarchy wrappers. |
| `FFbxDataConverter` at `:480`, `:487`, `:496`, `:499`, `:514` | SDK matrices/vectors/properties/strings, including by-value returns and inline operations. |
| `FFbxImporter` at `:753`, `:768`, `:1316`, `:1335` | Node arguments, scene state and geometry converter cross public boundaries. |
| `U/Public/CinematicExporter.h:26`, `:27` | `INodeNameAdapter` virtual methods expose `fbxsdk::FbxNode*`. |
| `U/Public/FbxAnimUtils.h:41`, `:49`, `:57` | Exported helpers/callbacks expose SDK nodes, curves and properties. |
| `M/Public/MovieSceneToolHelpers.h:441`, `:539`, `:544` | SDK camera/node arguments and results. |
| `U/Private/FbxExporter.h:252`, `:276`, `:283`, `:329`, `:501` | Despite its private header path, exported methods/shared state remain SDK-shaped; MovieSceneTools includes this header. |

Forward declarations do not neutralize these contracts. By-value SDK members and
inline operations cannot be fixed by forward declarations. Removing SDK types
requires changing APIs and callers, or retaining a real SDK path during migration;
these interfaces cannot stay unchanged and simply receive Assimp `aiScene`.

## Smallest Honest Phases

1. **Compile/test the independent bridge.** Verify native UBT linking and actual
   UE mesh creation, units/axes, node transforms, indices, normals/UVs and material
   slots. Reject unsupported inputs explicitly. This passes only a narrow bridge
   substage, not a replacement or Cook gate.
2. **Integrate a backend-neutral parser path.** Interchange's public facade is a
   smaller boundary than UnrealEd. Validate node IDs, payload keys/serialization,
   transforms, materials, diagnostics and worker behavior. Static geometry alone
   cannot remove existing skeletal/morph/animation/camera/light SDK code.
3. **Migrate legacy importer interfaces and consumers.** Replace public leaks with
   owned UE data or neutral handles; preserve scene/material/static/skeletal/morph/
   animation import, reimport identity/settings, LODs, sockets and collision.
   Developer consumers also matter: NaniteUtilities `Public/NaniteLayout.h:27`
   includes `FbxMeshUtils.h` and invokes reimport at `:528` and `:593`.
4. **Migrate exporter/cinematic consumers.** Test `FFbxExporter`, node adapters,
   curves, cameras/lights, skins/bind poses and scene export separately. Assimp
   backend round trips do not prove UE asset/export/reimport equivalence.
5. **Remove build edges only after code migration.** Establish target reachability
   using UBT, compile callers, inspect linked/staged dependencies. Unported
   functionality remains incomplete. No stub SDK, renamed Assimp library or silent
   feature removal. A reduced-feature profile is not full parity.
6. **Validate Editor/Cook/runtime independently.** Minimal-map cook/package,
   server/RPC, GPU sensor output and Autoware remain separate gates.

Parser/importer/exporter owners can work in parallel; public API changes need
coordinated integration. This sidecar does not edit UE, the stage harness or bridge
and does not block independent Program compilation.

## Feasibility and Time

Narrow static-scene bridges have now been demonstrated within their recorded
scope. Whole-SDK replacement is a substantial importer/exporter and consumer
API port, **not a small missing-library build fix**. Local source reveals no
intrinsic impossibility, but does not establish Assimp parity for every required
contract or prove complete achievability on a schedule.

Planning scale, not a promise: narrow usable static assets need weeks-or-longer
assessment; complete importer/exporter migration is months-scale or longer. A
reliable ETA requires the first real bridge build, a frozen required-feature and
fixture matrix, and actual Editor dependency/build results. Multiple agents help
independent work, not shared API closure or serial Cook/runtime gates. Lexical
counts do not justify a completion percentage.

Unverified here: Editor linking, enabled plugin consumers,
asset/reimport and material/coordinate fidelity, skin/morph/animation/export
equivalence, Cook/package, RPC, GPU sensors and Autoware. Neither a full-parity
guarantee nor a delivery date follows from this inventory.
