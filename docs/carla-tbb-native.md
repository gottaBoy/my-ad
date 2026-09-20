# Native ARM64 TBB 2019u8

This stage builds only the UE-pinned Intel TBB 2019u8 `tbb` and `tbbmalloc` release shared libraries in the native DGX Spark ARM64 Docker container.
It does not build OpenUSD, UBT, the Editor, or the complete USD SDK.

## Inputs and Boundaries

- Source: `Engine/Source/ThirdParty/Intel/TBB/IntelTBB-2019u8`.
- Source is copied into the run directory under `artifacts/carla/usd/tbb-*/source`; the UE source tree is read-only and is never cleaned or overwritten.
- The actual TBB 2019u8 Linux Make rules select `tbb` and `tbbmalloc`, use `-shared`, and produce release and debug shared targets. This stage requests release outputs only.
- The local rules do not provide a static archive target for this Linux path. Static mode is recorded as `NOT_IMPLEMENTED`; no empty or renamed `.a` is created.
- Compiler and linker inputs are the existing UE ARM64 clang target toolchain plus UE libc++ headers, `libc++.a`, and `libc++abi.a`. The script rejects host execution and never falls back to g++ or libstdc++.
- Jobs are limited to 1 through 4 and the outer timeout is limited to 1200 seconds.

The smoke program exercises `tbb::parallel_for`, `tbb::parallel_reduce`, and `tbb::scalable_allocator` allocation/free through the staged shared libraries. Architecture checks and `ldd -r` are prerequisites for the smoke run.

## DGX Spark Command

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-tbb.sh
```

The command writes a unique `artifacts/carla/usd/tbb-*/` run directory containing:

- `source-original.sha256`, `source-copy.sha256`, and `source-diff.txt`
- exact build and smoke compile command files
- build, architecture, linkage, and smoke logs
- staged `libtbb.so*`, `libtbbmalloc.so*`, and `installed.sha256`
- `static-mode.txt` explaining why static mode is not implemented
- `stage-report.json` and `decision.md`

The exact stage scope is `Native ARM64 TBB 2019u8 dynamic libraries and smoke; not static TBB, OpenUSD, UE Editor or USD SDK`, reported as `carla-tbb-native-arm64` through `stage_report.py`.

## Acceptance and Stop Conditions

- The staged libraries must be AArch64 ELF64 shared objects.
- `ldd -r` on the smoke executable must contain no `not found` or `undefined symbol` result.
- The smoke executable must run natively and print `TBB smoke PASS`.
- The stage report is `PASS` only when source retention, toolchain identity, build, architecture, linkage, smoke, and retention checks all pass.
- Any old-TBB compiler error, unknown architecture, x86_64 output, unresolved dependency, libc++ mismatch, missing output, or smoke failure stops the stage and leaves a fail-closed report with the smallest failing log.
- A successful TBB stage does not establish OpenUSD, USD plugin loading, Python import, UBT, Editor, ABI, or runtime compatibility.
