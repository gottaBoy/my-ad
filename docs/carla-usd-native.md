# CARLA USD Native Inventory

This is a read-only dependency inventory for the UE USD plugin on Linux ARM64.
It is an inventory gate, not a USD build, UBT build, ABI check, link check, or runtime test.

## Locked Inputs

The inspector reads these files from the mounted UE tree:

- `Engine/Plugins/Runtime/USDCore/Source/ThirdParty/USD/BuildForLinux.sh`
- `Engine/Plugins/Runtime/USDCore/Source/ThirdParty/USD/OpenUSD.tps`
- `Engine/Plugins/Runtime/USDCore/Source/UnrealUSDWrapper/UnrealUSDWrapper.Build.cs`
- The TBB, Boost, Python3, Imath, OpenSubdiv, Alembic, and MaterialX `Build.cs` rules referenced by the build script.

The source lock is OpenUSD `24.05` (`v24.05` in `OpenUSD.tps`). The UE ARM64 Linux name is `aarch64-unknown-linux-gnueabi`; the checked-in USD build script still defaults to `x86_64-unknown-linux-gnu` and passes Boost `-x64`.

The dependency versions taken from the checked-in rules are TBB `2019u8`, Boost `1.82.0`, Python `3.11`, Imath `3.1.9`, OpenSubdiv `3.6.0`, Alembic `1.8.6`, and MaterialX `1.38.5`.

## Checks

Each required path is emitted as a JSON requirement with source-rule evidence, resolved path, size, SHA-256, status, and architecture data where applicable.

- Headers, source markers, configs, resources, and patches must exist and be non-empty.
- Shared libraries must be AArch64 ELF64 shared objects, or linker scripts whose referenced objects pass the same check.
- Static archives must be normal GNU archives whose every object member is AArch64 ELF64 relocatable code. Thin, empty, malformed, unknown, or mixed-architecture archives are `BLOCKED`.
- Tools are inspected for AArch64 ELF64 executables and are never run.
- Every `.so*` and `.a` found under the selected LinuxArm64 library directories is scanned, including unexpected files, so an x86_64 file cannot be hidden by an unlisted filename.
- Missing paths and unresolved implicit system libraries are `BLOCKED`; the report never converts an absence or an unverified format into `PASS`.

The report also records structural blockers in the real rules: the destructive `rm -rf` build script, its x86 default, the forced Boost x64 setting, the Linux-only wrapper branch, and Python3 auto-discovery limited to X64.

## DGX Spark ARM64 Run

Run the inspector and its focused unit test only through the ARM64 Compose profile. Do not invoke the upstream `BuildForLinux.sh`, and do not start a USD build.


```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  carla-dev python3 /repo/scripts/carla/inspect_usd_dependencies.py \
  --ue-root /workspace/unreal-engine \
  --artifact-dir /artifacts/carla/usd


docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  carla-dev python3 -m unittest tests/test_carla_usd_dependencies.py -v
```

The inspector exits `0` only for a complete, verified inventory. The expected current result is `BLOCKED` with exit code `1`. With `--artifact-dir`, Docker prints the exact report path, normally under `artifacts/carla/usd/usd-inventory-*/inventory.json`; retain that path with the command output as the real report.


A `PASS` here would mean only that the declared files and file headers satisfy this bounded inventory. It would not certify PIC, C++ ABI, GLIBC, link/load behavior, Python imports, plugin loading, or an Unreal Editor launch.

## Source Build Progress, September 16

Separate native recipes now establish TBB shared libraries, Imath PIC static,
Alembic Ogawa PIC static, and OpenSubdiv osdCPU PIC static builds. They remain in
isolated prefixes, not the UE USD deployment paths, so inventory entries do not
automatically become PASS. HDF5/Python Alembic and OpenSubdiv GPU backends are not
claimed. Build reports, prerequisite hashes and smoke logs are retained.

```sh
make carla-imath JOBS=4
make carla-alembic JOBS=4 IMATH_REPORT=/artifacts/carla/usd/imath-<run>/stage-report.json
make carla-opensubdiv JOBS=4
```

Alembic requires an explicit validated Imath report, not a timestamp hard-coded
into the recipe. Missing or tampered prerequisites fail before configuration.
The latest integrator replays are `alembic-20260916T035401Z-MXwTfh` and
`opensubdiv-20260916T035401Z-pfVOVc` under `artifacts/carla/usd/`.
