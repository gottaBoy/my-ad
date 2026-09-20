# DGX CARLA Remaining Work

This is an execution plan, not a completion percentage or a delivery promise.
The matching machine-readable dependency graph is
`config/carla/execution-plan.json`. Planned tasks are not stage evidence.

## Current Assessment

The native toolchain, LibCarla/Python API, Game binary, shader tools, static FBX
backends, bounded UE mesh bridges and Vulkan clear/readback have scoped evidence.
The Interchange tests now call the real parser and run the real Worker launch
module over TCP in an explicit static-only Program profile. This does not prove
the default Editor import workflow. Real Editor/Cook, Server RPC, sensor
rendering, bridge compatibility and Autoware acceptance remain unpassed.

The repository regression counts include harness fixtures. They are not
simulator feature counts and cannot be used as a completion percentage.
Likewise, USD inventory entries include repeated paths and unresolved lookups;
188 blocked entries do not mean 188 independent libraries to port.

Two reproducibility facts must remain explicit:

- The actual UE working tree is ahead of the `source.lock` pin and contains local
  compatibility changes. Every capture now records a structured
  `source_lock_drift` report (`matches` / `drifted` / `unpinned`) alongside the
  repository snapshots, and `verify` fails with `source_lock drift record
  changed` if either side moves after capture. The lock is never rewritten.
- The initial manifest retained hashes, but not content, for untracked files.
  The R0 task adds content snapshots. Even that does not prove a fresh rebuild.

## Steps

| Order | Work Packages | Acceptance |
|---|---|---|
| 0 | R0: retain source changes | HEAD + tracked patches + untracked content and modes, verified in Docker |
| 1, parallel | F1/F2: FBX interfaces; U1: USD SDK | Real implementations and native dependencies; no empty SDK or silent feature removal |
| 2 | E1, then C1 | Native Editor build and project load; minimal LinuxArm64 Cook/package and real RPC |
| 3 | S1 | Actual UE camera/LiDAR output, frame/time alignment and stability |
| 4 | A1 | Compatible bridge, one publisher path, maps and Autoware drive-to-goal |
| 5 | R1 | Clean-checkout replay, full rebuild and one-click product regression |

The longest unresolved path is FBX/Legacy Editor migration and native USD
integration before the first real Editor/Cook. A supported target name or a
passing standalone Program cannot establish that milestone.

`existing_tools` in the JSON lists available helpers, not an executable recipe
that completes the task. In particular, `make carla-editor-check` currently uses
UBT `-SkipBuild`: a successful dependency probe is not an Editor build.

## Multi-Agent Execution

Keep at most four active roles:

- Main/integrator: owns the plan, Make/Compose, UE rule integration and acceptance.
- FBX agent: owns parser contracts and agreed importer/exporter modules.
- USD agent: owns pinned dependency recipes and isolated build prefixes.
- Harness/provenance agent: owns retention and independent failure-path tests.

The runtime/Autoware role replaces an idle role after C1, rather than starting
end-to-end integration before a Server exists. Preparation may run earlier, but
must not be reported as runtime acceptance.

Each task has an explicit write scope in the JSON plan. Cross-owner public API or
build-rule changes go through the main agent. Only one UBT/Editor/Cook build runs
against a given UE working tree at a time. Independent third-party builds use
separate artifact prefixes and initially no more than four compiler jobs.

## First Execution Batch

1. Archive untracked compatibility source alongside the existing patches and
   hashes, and test source/snapshot mutation rejection.
2. Audit the remaining Interchange facade and worker contracts against the actual
   prototype, including multiple payloads and requested transforms; wire the
   existing source-scene self-tests into a separate native Program mode.
3. Deduplicate the USD inventory into components and choose the first native
   dependency recipe using real rule/ABI requirements; prepare and run the
   bounded TBB source-build check in its isolated prefix.
4. Review the outputs, update the ledger, run ARM64 tests, then capture a fresh
   manifest only after source and documentation changes are stable.

This batch does not start an unbounded whole-Editor build, commit unrelated staged
changes, reset source trees, or require a Windows/x86_64 machine.

## Reviewed Next Actions

September 16: the native static prototype exports all meshes through exact
source-bound key queries and three requested transforms. It invokes the 20 scene
self-tests and 27 payload checks, including actual Dispatcher JSON classes.
The real FInterchangeFbxParser now also has an opt-in ARM64 Program static path
with a registered ufbx session and 31 native lifecycle/query checks. The shared
provider now runs inside the real Worker over UE command-queue TCP: five child
processes and 23 IPC checks passed. See `carla-native-worker.md`. Next is the
production WorkerHandler, progress/cancellation policy and consumer/factory integration. Default
Editor/Autodesk code remains unchanged; this is not complete F1/F2 acceptance.
Conversion settings outside the one verified policy are explicitly rejected;
material identities are not material shader graphs.
Keep ufbx as the backend and adapt the UE contracts; do not substitute an
Autodesk library or a Godot engine migration for this work.

The USD graph has component work rather than 188 independent tasks:

```text
native toolchain / libc++
  + TBB 2019u8
  + Python 3.11 -> Boost.Python 1.82.0
  + Imath 3.1.9 -> Alembic 1.8.6
  + OpenSubdiv 3.6.0
  + MaterialX 1.38.5
       -> OpenUSD 24.05 + UE patches
       -> Python/plugin resources + native link/load checks
       -> UE wrapper integration
```

Release TBB/tbbmalloc shared libraries and Imath 3.1.9 Release PIC static library
now have separate native build/link/smoke evidence in isolated prefixes. Alembic
1.8.6 Ogawa/static/PIC and OpenSubdiv 3.6.0 osdCPU/static/PIC have also passed.
MaterialX 1.38.5 and CPython 3.11.8 core/shared/static-PIC now also have native
evidence. Python optional SSL/ctypes/compression modules remain absent. Next are
Boost 1.82.0 nine static/shared libraries and Boost.Python now have native evidence;
Release static/PIC TBB also passes separately from the shared runtime. Required
Python optional dependencies and TBB shared/static coexistence remain. The current
OpenUSD 24.05 plus six UE patches now completes all 2930 native build actions,
installs 81 AArch64 ELF libraries/extensions and passes USDA/USDC/Alembic/standard
MaterialX/pxr smoke. An explicit non-relocatable verified SDK binding selects the
native prefixes in five UE source files while retaining the default x86 paths.
The full Editor dependency graph now reaches `MovieSceneTools -> FBX` rather
than failing at the USD directory. This is not Editor link/runtime validation.
Default-path inventory remains BLOCKED; shared/static TBB coexistence and Python
optional features remain separate requirements. See `carla-openusd-build.md`.

The native Editor target remains `Linux -architecture=arm64`. It already selects
the USD wrapper Linux branch. Do not switch it to `LinuxArm64` to hide the actual
problems: Python discovery is X64-only, Boost selects an x64 filename suffix for
this Editor target, and several library/resource paths need verified ARM64
selection. Architecture inspection alone does not prove C++/Python ABI or PIC.

## Re-Estimation

September 17: F2 preparation is now in progress, without promoting F1 or E1.
`carla-legacy-fbx` compiles four real Editor translation units in a bounded
headers-only diagnostic. The first public data contract, scene node metadata,
now uses UE-owned transforms/pivots with updated producers and consumers.
Native scalar and SDK-free metadata smoke tests pass; factory asset behavior
does not. See `carla-legacy-fbx.md` and `config/carla/fbx-feature-matrix.json`.
The next measured milestone is actual static asset save/reload and reimport,
not another count of standalone meshes or SDK names. Preexisting Core support
libraries are explicitly retained, not described as a fresh Editor rebuild.

The next F2 slice now connects actual ufbx data to a hierarchy helper shared with
the real scene factory. Five Editor translation units, including reimport,
compile; the native adapter checks preserve IDs, parent/instance/material links
and reject unsupported metadata. This is still a factory-data substage, not
asset creation or a replacement of the default Autodesk factory backend.
Use `make carla-legacy-hierarchy JOBS=4`; see the scoped reports in
`carla-legacy-fbx.md`. The required-feature matrix retains NOT_RUN product states.

Do not infer remaining effort from the number of agents, passing tests or lexical
SDK references. Earlier week/month ranges were low-confidence planning budgets.
A tighter estimate needs a frozen required-feature matrix, a measured completed
parser/consumer slice, and the first actual Editor build results. Re-estimate at
E1 and again at the first cooked package and real sensor baseline.

Archive commands, revisions, patches, source snapshots, toolchain identity,
configuration, logs, exit codes and output hashes per gate. Keep private-source
archives private. Missing, stale, rejected or unrun gates are not PASS.
