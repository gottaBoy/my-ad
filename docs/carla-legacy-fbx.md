# Native Legacy FBX Migration

September 17, 2026: a bounded real Editor compile is now available. This does not
link an Editor, provide an Autodesk SDK, import a UStaticMesh or pass F2/E1.

## Replay

```bash
make carla-legacy-fbx JOBS=4 \
  NATIVE_SDK_ROOT=/artifacts/carla/sdk-bindings/native-usd-sdk-egrs7d3u
```

The native ARM64 Docker command verifies the USD binding, exports the evaluated
`CarlaUnrealEditor Linux Development -architecture=arm64` target, verifies the
bounded UBT action list, runs native smoke tests, and compiles these five real
translation units without linking the Editor:

- `FbxStaticMeshImport.cpp`
- `MovieSceneToolHelpers.cpp`
- `FbxMainImport.cpp`
- `FbxSceneImportFactory.cpp`
- `ReimportFbxSceneFactory.cpp`

`TIMEOUT` is a positive per-command limit (default 600 seconds). The diagnostic
forces its explicit headers-only profile and keeps USD enabled. The ordinary
`carla-editor-check EDITOR_PROFILE=full` rejects an inherited headers-only switch.
No source reset, automatic patch application or feature removal is performed.

The effective engine changes are retained as three replay patches under
`scripts/carla/patches/`: `legacy-fbx-diagnostic.patch`,
`ue-editor-scalar-math.patch`, and `legacy-fbx-node-metadata.patch`. On a separate
checkout, inspect and apply each with `git apply --check` followed by `git apply`
inside the UE root; an already modified tree is not reset. The diagnostic checks
that the patches are present. The full manifest also retains the new UE header.

## First Slice Evidence

`artifacts/carla/legacy-fbx-3hcme51t/stage-report.json` passes only this scope:

```text
Legacy FBX node metadata and four native objects; no Editor link or FBX parity
```

The evaluated diagnostic target selects seven direct FBX consumers:
ControlRigEditor, HairStrandsEditor, InterchangeFbxParser, LevelSequenceEditor,
MovieSceneTools, SequencerScriptingEditor and UnrealEd. This is target selection,
not a C++ call graph or evidence that the default Editor can link.

Four freshly generated AArch64 relocatable objects contain 222 unique strong
undefined SDK symbols and 33 distinct UE symbols whose signatures contain SDK
types. These categories are separate: a UE importer method taking `FbxNode*` is
not an Autodesk library implementation. Inline, optimized-away, weak and other
translation-unit dependencies are outside these counts. They are not workload
percentages or a complete SDK replacement list.

The ARM64 Docker repository regression passes 491 tests with no skips. Its log is
`artifacts/carla/regression-legacy-20260917T055923Z.log`. The existing static
Interchange path also passes its 20 scene and 27 payload checks in
`artifacts/carla/interchange-nodes-20260917T055444Z-JARQnH/stage-report.json`.
The ordinary full Editor dependency probe remains blocked at the missing native
FBX SDK in `artifacts/carla/editor-check-20260917T055941Z-TeqxRP/`; the diagnostic
has not changed that acceptance result.

The run retains source/rule/header/response-file snapshots, binary patches,
commands, logs, fresh objects, symbol lists and hashes. Failed runs remain intact.
In particular, the earlier response-file check rejected UBT's duplicated but
identical target flags; conflicting architectures are still rejected.

## Implemented Changes

The real Linux Editor scalar path exposed an unimplemented arithmetic right
shift, two wrong variable names in Chaos bit casts, and a double unpack returning
a float vector. These are fixed without changing platform or enabling NEON.
The existing static mesh importer also now includes its required platform-file
header explicitly, instead of depending on a PCH/unity neighbor.

The native scalar smoke uses the actual UBT compile arguments and UBSan. Its 279
checks cover shifts 0 through 33 and 255, negative values, signed limits, float
bit patterns and double precision. Other unimplemented scalar intrinsics remain
outside this fix.

`UE::Import::FSceneNodeInfo` is an SDK-free public value type. The existing
`UnFbx::FbxNodeInfo` name aliases it. Producers perform the existing
`FFbxDataConverter::ConvertTransform`/`ConvertPos` operations; the scene factory
copies evaluated UE transforms and pivots instead of decomposing SDK matrices.
Front-axis refresh updates both root and child records through the same converter.
This preserves the factory's decomposed-transform contract, not a raw affine
matrix or arbitrary-shear representation.

The SDK-free smoke verifies default initialization, 64-bit IDs, string ownership,
copy independence, mirrored/small scales and double-valued positions. Its compiler
dependency list contains no SDK header; its loaded libraries contain no FBX SDK.
It uses existing native Core/BuildSettings/TraceLog shared libraries, recorded in
`prebuilt-core.json` and retained by content. Those libraries are **not rebuilt by
this command**, and this test does not establish clean-checkout reconstruction.
The real SDK producer/consumer paths are compiled, not executed against Autodesk;
full asset and coordinate equivalence still needs the Editor tests.

## Next Acceptance

### Shared Factory Hierarchy

The next native slice is available as:

```bash
make carla-legacy-hierarchy JOBS=4
```

`UE::Import::BuildSceneImportHierarchy` now implements the actual scene factory's
parent indexing, skeleton-subtree exclusion and direct LOD-child import policy.
`UFbxSceneImportFactory::ConvertSceneInfo` calls this SDK-free helper. The helper
rejects empty, duplicate-ID, disconnected, cyclic and non-parent-first input
without changing its output; deep hierarchy filtering no longer recurses through
every ancestor. Both real import and reimport callers check failures before
material extraction, release the scene and close the slow task. These call sites
are included in the five-object native compile, not merely source-string tests.

The new `CarlaUfbxLegacy` module converts genuine ufbx scene data into the same
node records and invokes that same factory helper. It retains source numeric IDs
directly from ufbx DOM, source hashes, local/global geometry transforms, shared
mesh instances and per-instance material identities. It does not reconstruct
numeric IDs from display names or rounded JSON numbers. Geometry transforms
remain separate and are applied once when fetching a mesh payload.

Only null, mesh and LOD-group metadata are accepted in this static profile.
Nonzero source pivots, camera/light attributes, multiple attributes and other
unsupported node kinds are rejected, not silently stripped. The existing static
Interchange behavior remains separate; the new metadata restriction does not
disable its geometry path. Skeletal/morph/animation loading is still outside the
static backend. LOD selection policy is tested, not full LOD asset construction.

The native hierarchy report is
`artifacts/carla/legacy-hierarchy-20260917T084437Z-1BMYlY/stage-report.json`.
It contains 26 checks, including real multi-mesh and shared-mesh fixtures,
mirrored geometry, instance material overrides, real FBX pivot/camera rejection,
64-bit identities, a 10,000-node helper test and failed-output preservation.
The native Program does not load `libfbxsdk`. Its report identity is explicitly:

```text
ufbx to shared Legacy factory hierarchy; not asset import, save/reimport, Editor or Cook
```

The five real Editor translation units pass in
`artifacts/carla/legacy-fbx-sopqt03v/stage-report.json`, with scope
`Legacy FBX shared hierarchy and five native objects; no Editor link or FBX parity`.
Their observed undefined symbols are 222 SDK and 40 SDK-typed UE symbols. The
additional translation unit changes the comparison set; these are not a count
of newly introduced product dependencies or migration-completion percentages.

The original FBX factory still obtains its source scene through Autodesk. Sharing
and exercising its hierarchy code does **not** switch the default factory to
ufbx, create a UStaticMesh, save a package or execute reimport. The native
Engine/UnrealEd shared libraries and complete Editor executable are still absent.
Those are explicit remaining requirements before asset-level acceptance.

Final ARM64 regression: 501 tests pass without skips, logged in
`artifacts/carla/regression-hierarchy-final-20260917T084436Z.log`. Native replay
also passes static scene/payload (20/27), real Parser (31) and Worker IPC (23)
checks in `interchange-nodes-20260917T084441Z-8hhHoH`,
`interchange-parser-20260917T084443Z-P23pKk`, and
`interchange-worker-20260917T084632Z-WIz8V2`, respectively, under
`artifacts/carla/`. All four native reports were revalidated after the builds.

`config/carla/fbx-feature-matrix.json` records the required workflows, source
paths, available fixtures and missing fixtures. None is marked product PASS.
CARLA's own `Util/Tools/Import.py` explicitly requests `FbxFactory`, material and
texture import, replacement and tiled-map handling, so a standalone static mesh
payload is not a substitute for that workflow.

Next: feed the native backend through the actual static factory lifecycle and
verify save/reload plus replace/reimport before extending collision, sockets,
LODs, skeletal/animation/morph and exporter consumers. Existing functionality is
not disabled while those implementations are incomplete. The diagnostic also
exposes missing VHACD, KissFFT, libxml2 and SpeedTree native library paths, which
remain independent Editor link blockers.
