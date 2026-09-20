# OpenUSD Native Build

This is the full source build for the UE-pinned OpenUSD 24.05 Linux feature
selection, not a full Editor completion claim. The source pin is
`2864f3d04f396432f22ec5d6928fc37d34bb4c90` (official v24.05). The recipe requires
a clean local clone, copies it into a unique run, then applies all six UE
`OpenUSD_v2405_*.patch` files. It does not run the destructive upstream UE script.

```sh
make carla-openusd JOBS=4 DEPENDENCIES=/opt/my-ad/config/carla/usd-native-inputs.json
```

`config/carla/usd-native-inputs.json` records this machine's verified dependency
artifacts, not portable download instructions. On a fresh machine, rebuild the
dependencies and replace the map with those printed report paths. All seven
reports are required: TBB shared, Python, Boost, Imath, Alembic, OpenSubdiv and
MaterialX. The recipe verifies exact stage/scope, evidence hashes, required
consumed headers/libraries, and Alembic->Imath / Boost->Python prerequisite identity.
No `latest` glob or arbitrary SDK prefix is accepted as build evidence.

OpenUSD uses the previously validated shared TBB build. The new Release static
TBB/PIC archives satisfy a separate UE `IntelTBB.Build.cs` need; mixed runtime
coexistence has not been validated. They cannot be substituted for a DSO just
because they exist.

The CMake profile retains Python, Alembic, MaterialX, imaging and USD imaging.
GL, HDF5, usdview, examples/tests/tools follow the UE Linux build script's OFF
choices. CMake cannot silently remove a required ON feature or replace the pinned
library/include/interpreter selections. C++17, native LLVM and UE sysroot/libc++
are used. CPython and Boost.Python link shared, avoiding a separate statically
linked interpreter inside each USD extension. The initial shader-toolchain
`--exclude-libs,ALL` policy is explicitly overridden for OpenUSD shared/module
links: its C++ objects exchange streams/locale facets between DSOs. The first
native run crashed in cross-module stream formatting; retained GDB and copied
relink diagnostics established the problem. OpenUSD additionally uses libc++'s
non-unique RTTI comparison mode for its `std::type_index` cast registry because
Python's local extension loading does not guarantee one type-info address.
These flags are local to this build, not a global UE/toolchain rewrite. Combined
loading into Unreal still requires separate acceptance.

Each run saves actual commands, return codes, logs, CMake cache, compile commands,
source/patch/toolchain/input hashes, dependency report pins and installed-file
hashes. Timeout terminates and reaps the command process group, and failure keeps
a FAIL stage report. The source and live inputs are rehashed before acceptance.

Acceptance requires AArch64 installed ELF libraries/extensions, link checks and
real `pxr` imports under the pinned interpreter. The native smoke writes and reads
USDA and USDC scenes, checks mesh transforms and material binding, loads the
Alembic plugin to read the previous Ogawa control, reads the official
`GraphlessNodes.mtlx` control through the MaterialX file-format plugin, and rejects
a malformed USD layer. The independent MaterialX recipe still tests its own
flattened document; that document contains embedded local nodedefs unsupported by
this USD importer and is not a supported OpenUSD integration input.

The prefix is isolated. No relocation, UE SDK deployment or Editor link/launch is
implied by a source-build PASS. Schema code-generation tools still need Jinja2;
the limited Python dependency lacks several optional stdlib modules. These gaps,
the UE Boost architecture suffix, Python SDK discovery, TBB per-architecture
paths and wrapper runtime plugin-directory hardcoding must be handled before
claiming full Editor support.

## Verified Run And Editor Binding

`artifacts/carla/usd/openusd-peak8y9o/stage-report.json` is the final source-build
PASS. All 2930 build actions, install/link checks, runtime smoke and source/input
retention completed. No Python dlopen flag override is needed. Arrays, lists,
tokens and stream conversion pass with the revised linkage/RTTI settings.

The two earlier runs remain FAIL: `openusd-r2bduadg` records the initial type
conversion failure and retained GDB import-order locale crash;
`openusd-qg1aa95h` records the unsupported embedded MaterialX nodedef input.
`openusd-link-diagnostic-k550e3y8` is a copied-output relink diagnostic, not a fresh
build PASS. None of these old reports were rewritten to claim success.

The explicit SDK binding points to immutable accepted prefixes rather than
overwriting existing x86 deployments:

```sh
make carla-usd-sdk OPENUSD_REPORT=/artifacts/carla/usd/openusd-peak8y9o/stage-report.json \
  DEPENDENCIES=/opt/my-ad/config/carla/usd-native-inputs.json
make carla-editor-check EDITOR_PROFILE=full \
  NATIVE_SDK_ROOT=/artifacts/carla/sdk-bindings/native-usd-sdk-egrs7d3u
```

The binding retains five engine before/after files and a patch. Boost/Python/TBB
select a consistent shared runtime for this ARM64 profile, while default rules
remain intact. USD wrapper runtime paths use the accepted original plugInfo/RPATH
layout without rewriting verified files. The root is a non-relocatable opt-in
SDK, not a packaged installation. It requires its original verified artifacts.

The full Editor UBT graph passed the prior USD missing-directory error and now
stops at `MovieSceneTools -> FBX` with the missing Autodesk ARM64 SDK:
`artifacts/carla/editor-check-20260916T081611Z-Ko6VoM/`.
No `no-usd` flag was used. This is dependency selection evidence only, not full
Editor compile/link/launch, allocator coexistence or runtime SDK acceptance.
