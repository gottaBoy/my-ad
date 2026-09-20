# Native ARM64 Imath 3.1.9

This bounded U1 stage builds the local UE Imath 3.1.9 source into a new isolated
artifact prefix. It does not rebuild TBB or write to the UE tree, Make, Compose,
or the compatibility ledger. It is not an OpenUSD, PyImath, or Editor PASS.

## Run

Integrated entry: `make carla-imath JOBS=4`.

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -e CARLA_BUILD_JOBS=4 -e CARLA_IMATH_TIMEOUT_SECONDS=600 \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-imath.sh
```

The script requires native `aarch64` Docker. Jobs are limited to 1-4; the worker
has a 1-1200 second timeout (default 600), with a 15 second kill grace. The outer
supervisor writes a FAIL report on worker timeout or any missing input. No socket,
network download, upstream in-place deployment script, or source repair is used.

Source: `Engine/Source/ThirdParty/Imath/Imath-3.1.9`. `Imath.Build.cs` requires
`libImath-3_1.a`. The existing `ue-arm64-third-party.cmake` selects native LLVM,
the UE `aarch64-unknown-linux-gnueabi` sysroot, UE libc++ headers/static runtime,
and PIC. Release/static is explicit; Python bindings, docs, and upstream tests
are OFF for this slice.

## Evidence

Each invocation prints a new `artifacts/carla/usd/imath-<UTC>-<random>/` directory:

- `source/` and `source.sha256.json`: the entire local Imath source, including
  untracked configuration templates. This records the actual checkout, not a
  claimed clean upstream release. Missing templates cause failure, not synthesis.
- `inputs/`: copies of the recipe, smoke, toolchain, reporter, and UE build rules.
- UE HEAD, scoped tracked patch, compiler/runtime hashes, and exact step commands.
- Configure/build/install logs and retained CMake cache/compile commands.
- `install/lib/libImath-3_1.a`, headers, CMake exports, and installed SHA256 list.
- Whole-archive shared-link test with `-z defs -z text`, per-member AArch64/ELF64
  checks, `ldd -r`, and native half/vector/matrix/color/iostream smoke output.
- `stage-report.json`, `decision.json`, worker exit code and raw logs. Every
  retained source/installed file is individually hashed as report evidence.

The source set and hashes are compared before/after build for both the original
tree and artifact copy. The smoke uses compiled Imath functions, including
libc++ iostream linkage; header-only arithmetic is not enough to pass. This is a
bounded ABI/PIC check, not full Imath correctness or downstream USD integration.
Run directories are never reused. Reports are not signed attestations.

## Focused Tests

Use the report path printed by the real build:

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  -e CARLA_IMATH_REPORT=/artifacts/carla/usd/imath-<run>/stage-report.json \
  carla-dev python3 -B -m unittest tests.test_carla_imath -v
```

Tests cover bounds, architecture gate, source/template failure, output isolation,
timeout failure reports and (when supplied) the actual build report. Synthetic
failure fixtures do not establish a native library build.

## Verified Run

On 2026-09-16, the real native ARM64 `carla-dev` run completed with exit 0:

`artifacts/carla/usd/imath-20260916T020047Z-PpOPbp/stage-report.json`

All five `libImath-3_1.a` members were AArch64 ELF64 relocatable objects. The
whole-archive shared PIC link and both dynamic-link checks passed, followed by:

```text
Imath 3.1.9 smoke PASS half/vector/matrix/color/libc++
```

Eight focused tests passed in the same ARM64 Docker service, including validation
of this real report; the raw test output is in `focused-tests.log` beside it.
Original source, configuration inputs, and toolchain hashes were unchanged after
the build. This establishes only the named Imath stage, not U1 as a whole.
