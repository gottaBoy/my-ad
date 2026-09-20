# Native CARLA Delivery Stages

The requested destination is a reproducible native ARM64 Docker workflow from
CARLA source to sensors, ROS/Autoware integration, and regression evidence.
This file tracks delivery, not a completion percentage or a promised date.

## Execution Environment

User clarification, September 14, 2026: **DGX Spark is the only available machine.**
Builds, dependency installation, Editor/Cook and runtime validation must execute
in native ARM64 Docker on that machine. There is no Windows or x86_64 cook host.
Cross-host cooking was discussed as a candidate, but is not an implementation
route or a prerequisite in this plan. QEMU does not count as native validation.

The native ufbx backend, genuine UE static mesh bridge, standalone Interchange
static prototype and independent Vulkan prerequisite have scoped evidence.
The real parser facade, worker/factory consumers, Legacy FBX interfaces and USD
integration remain incomplete.

The remaining work, ownership, dependency order and acceptance criteria are in
`carla-execution-plan.md` and `../config/carla/execution-plan.json`. These are work
plans, not successful stage reports or a completion percentage.

## Delivery

| Stage | Acceptance | Current State |
|---|---|---|
| Foundation | Native toolchain, LibCarla/Python API, shader tools and Game binary | Verified within the scopes in carla-dgx-audit.md |
| ufbx backend | Pinned native shared/PIC static libraries, real FBX/control fixtures and rejection semantics | PASS: nine check groups; see audit section 9.6 |
| UE static mesh bridge | Assimp -> real UE FMeshDescription; transform/attribute/serialization tests | PASS: native build, 25 self-checks and six process cases; see audit section 9.4 |
| ufbx UE static bridge | Native UE conversion, serialization, non-symmetric axes/winding fixtures and Assimp regression | PASS: 32 self-checks and six process cases; not UStaticMesh/Editor/Cook |
| Interchange static boundary | Real UE scene/mesh nodes, standard static payload and graph round trip | PASS: BlenderCube in native ARM64 UBT Program; not translator/worker/Editor/Cook |
| GB10 Vulkan prerequisite | Native graphics queue, render-pass image clear, readback and exact pixel checks | PASS: independent native probe; not UE shaders or CARLA sensors |
| USD dependency inventory | Read-only ARM64 path, architecture and missing-input evidence | BLOCKED: checked-in Linux script and deployment paths are x86_64-oriented |
| Build provenance | Main project, CARLA, UE, dirty patches, untracked source and compatibility ledger | Capture/verify passed; R0 adds retained untracked content rather than hash-only records |
| Editor integration | Native Editor links/runs without unresolved SDK dependencies; required import workflows tested | Blocked on remaining Autodesk FBX consumers and the separate USD integration |
| Cook and RPC | Cook a minimal map, stage the package, launch Server, complete version/world/tick RPC | Not run |
| Rendering and sensors | GB10 Vulkan, nonblank camera frames, timestamps, LiDAR and deterministic stepping | Not run |
| ROS 2 | Clock, sensor types, TF, QoS and rates match contracts without duplicate publishers | Not run |
| ACB | Pinned Rust bridge builds; real server handshake and vehicle/sensor contracts pass | Not run |
| Autoware | Map/sensor configuration, localization, planning and closed-loop control | Not run |
| Regression | Recorder/ground truth, repeatable A/A runs and explicit A/B acceptance thresholds | Not run |

## Harness Rules

- Every stage has an explicit scope and independent prerequisites.
- A backend test, dependency graph, link, process exit or open port alone does
  not establish a later runtime stage.
- Record source versions/diffs, commands, configuration, raw logs, return codes,
  machine-readable checks and evidence hashes in unique run directories.
- Missing evidence, empty test sets, unsupported features, failures and timeouts
  are not successful acceptance results.
- Keep host dependencies unchanged; builds and runtime checks execute in Docker.
- Preserve existing source edits. Never replace a required implementation with
  empty symbols or hide an unsupported feature to obtain a successful link.

## Current Work Split

- Main agent: plan, shared Make/Compose, UE integration and final acceptance.
- FBX agent: parser/worker interfaces and agreed Legacy consumer modules.
- USD agent: pinned dependency recipes in isolated artifact prefixes.
- Harness/provenance agent: retained source inputs and independent rejection tests.

Only one UBT/Editor/Cook build may write a given UE tree at a time. Agent count is
not a build-speed or delivery-date guarantee; disjoint code ownership is required.

The static bridge is intentionally narrower than an Editor importer: it accepts
static geometry only, produces FMeshDescription in memory, and does not create
UStaticMesh assets or cooked packages. Full Editor integration remains a separate
stage even after that bridge passes.

## Reproduce the Completed Substage

```bash
make carla-ue-meshbridge JOBS=8
make carla-ue-ufbxbridge JOBS=4
make carla-interchange JOBS=4
make carla-test
make carla-manifest
```

The Make targets rerun their backend checks, write scoped prerequisites,
then build and exercise the native UE program. The ufbx target also runs the
unchanged Assimp path in the same dual-backend executable. JSON reports must be validated
inside the same Docker mount layout. A source/library/evaluator change may
invalidate an older report; no historical decision.md is automatically promoted.

ufbx reports explicitly do not assert position equivalence with the old Assimp
Front/Coord/Up mapping. The ufbx policy distinguishes front from forward and uses
the library coordinate/geometry-transform APIs; four non-symmetric real FBX cases
check handedness, instance reflection, units and UE triangle-normal agreement.

Next: migrate the public InterchangeFbxParser boundary and its worker/factory
consumers. The existing SDK-dependent Editor consumers remain unchanged and incomplete, as described in
`carla-fbx-migration.md`.

## Native Runtime Harness

`make carla-vulkan` independently checks the GB10 graphics-queue readback
prerequisite. `make carla-test` runs repository tests and shell syntax checks in
ARM64 Docker; fake RPC fixtures use distinct `harness-test.*` stage IDs.

After an actual native cooked package is running on DGX, the endpoint checks are:

```bash
make carla-runtime-check PROVENANCE=/artifacts/carla/ACTUAL_BUILD/provenance.json ALLOW_WORLD_MUTATION=1
make carla-runtime-check RUNTIME_MODE=sensors PROVENANCE=/artifacts/carla/ACTUAL_BUILD/provenance.json ALLOW_WORLD_MUTATION=1
```

`PROVENANCE` must point to real build evidence, not a newly captured checkout HEAD
attributed to an older binary. Its schema is integer `schema_version: 1` with
`sources.carla` and `sources.ue`, each containing nonempty `location` and `revision`
strings. The endpoint runner hashes that caller-supplied evidence; it does not
attest to the remote server build or architecture. Those remain separate gates.

The Make entry connects only to `127.0.0.1` and defaults to host networking. It
installs the existing ARM64 wheel offline inside the disposable container. An
idle, exclusively ticked test world and explicit mutation permission are required.
Settings are restored and owned sensor actors are destroyed; elapsed simulation
time cannot be undone. Sensor validation needs a map with spawn points and visible
geometry; missing fixtures are failures, not substituted images.

The root `stage-report.json` has stage ID `carla-runtime-invocation`; the endpoint
result is under `endpoint/stage-report.json`. Validate the root for a complete
invocation: it includes the outer exit code and verifies that the pinned wheel
and other inputs remained unchanged. A child PASS alone is insufficient.

There is no usable cooked package yet, so real RPC/sensor acceptance is NOT-RUN.
