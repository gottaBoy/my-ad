# Native Static FBX Parser Slice

This stage builds and directly calls the real UE `FInterchangeFbxParser` class
with a registered ufbx session provider. It is not the recording backend contract
under `scripts/carla/interchange/`, and it does not start InterchangeWorker.

```sh
make carla-interchange-parser JOBS=4
```

## Source Integration

`prepare_interchange_parser.py` installs the two source templates in
`scripts/carla/ue-parser/` into the actual engine parser module. It guards the
nine Autodesk-dependent translation units and the original parser implementation
with `!CARLA_INTERCHANGE_UFBX_STATIC`. Original bytes, modified bytes, generated
patch and file hashes are retained under `artifacts/carla/parser-source/`.
Repeated preparation is idempotent. Conflicting local generated-source changes,
partial guards and changed build-rule markers cause failure, not source reset.

The Build.cs flag defaults to OFF. Opt-in is restricted to Linux ARM64 Program
targets without Engine; Editor and other configurations are rejected. With the
flag unset, the original SDK implementation and FBX build dependency remain.
The original public `InterchangeFbxParser.h`, module and Settings/UHT code are
unchanged. No Autodesk library is renamed or fabricated.

The new engine-owned `IInterchangeFbxStaticBackend` modular feature creates one
session per parser. The Program provides the ufbx implementation. This keeps the
dependency direction from project to engine and does not bypass UBT hierarchy
checks. Registration must outlive all sessions. Missing/ambiguous providers fail.

## Implemented Scope

- File and in-memory graph loading through the real public parser API.
- Exact source-bound mesh keys and transform-specific request identities.
- Genuine Interchange nodes and `MeshDescription + archive bool(false)` payloads.
- Verified file publication before updating the result map; conflicting bytes
  are not overwritten. A new result directory is honored.
- Failed reload, failed graph write, Reset and repeated release invalidate old
  state. A parser reset does not affect another instance.
- This operation owns its messages and forwards errors to an external sink
  without erasing caller-owned diagnostics.

The diagnostic provider accepts only the explicit front-X, centimeter,
namespace-preserving setting combination (all four conversion flags true).
Other settings, generic non-static payloads and animation requests report errors.
Real animated and morph FBX controls are rejected. This is a verified ufbx policy,
not a claim of equivalence to every Autodesk coordinate conversion.

## Evidence And Remaining Work

`artifacts/carla/interchange-parser-20260916T040901Z-RUuhDM/stage-report.json`
records native build/run PASS with 31 lifecycle/query checks and 11 retained
outputs (three graphs, eight payload files, including repeated-directory and
instance-isolation cases). The evaluator requires the exact check multiset,
source digest, ARM64 Program, safe nonempty output paths, deployment source hashes
and valid ufbx prerequisite.

Update: `carla-native-worker.md` now records real static Worker IPC with the
shared provider. The remaining production integration includes:

1. Connect the production WorkerHandler launcher to the explicitly selected
   Worker binary/profile. The shared provider and guarded Worker SDK link are
   now exercised in the native test target; the default SDK path is unchanged.
2. Extend beyond the verified serial static protocol to production progress,
   cancellation, idle/heartbeat policy and required import workloads. The opt-in
   Worker now reports parser failures and serializes the complete transaction.
3. Supply material shader graphs, factory assets, import/reimport identity and
   required skeletal/morph/animation/export support. Material identities are not
   full material import. No collision/LOD/export parity is established here.
4. Migrate enabled UnrealEd/MovieSceneTools/Legacy SDK consumers before treating
   this as a full Editor replacement.

Unique run directories and serial calls are required; no cross-process atomic
publication or concurrent-load safety is claimed. No full Editor/Cook PASS.
