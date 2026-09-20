# Native ARM64 Alembic 1.8.6

Integrated entry (explicit prerequisite required):

```sh
make carla-alembic JOBS=4 IMATH_REPORT=/artifacts/carla/usd/imath-<run>/stage-report.json
```

This U1 slice builds the local UE Alembic source in an isolated prefix, using
native LLVM, the existing UE ARM64 sysroot/libc++, and the verified Imath 3.1.9
artifact. It does not build TBB, Imath, UBT or Editor and never writes to UE.

## Run

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -e CARLA_BUILD_JOBS=4 -e CARLA_ALEMBIC_TIMEOUT_SECONDS=1200 \
  -e CARLA_IMATH_REPORT=/artifacts/carla/usd/imath-20260916T021906Z-QilDi2/stage-report.json \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-alembic.sh
```

The Imath report above is an example, not a default; pass the actual verified
report explicitly. Jobs are limited to 1-4 and timeout to
1-1200 seconds, plus a 15 second kill grace. The supervisor retains a FAIL report
if the worker fails or times out. The recipe requires native aarch64 Docker and
uses the existing read-only `carla-dev` mounts, with no socket or downloads.

## Inputs

The default source is
`/workspace/unreal-engine/Engine/Source/ThirdParty/Alembic/alembic-1.8.6`.
`CARLA_ALEMBIC_SOURCE_DIR` can explicitly select another local source copy; the
version must still be 1.8.6. Missing configuration templates or source files
fail; the tool does not generate a replacement SDK or borrow deployed libraries.

Before configuring, and after the smoke, the recipe validates the exact Imath
stage/scope and rehashes its evidence. It requires individually bound source,
library, header and CMake export files, compares both live and retained Imath
source against its manifest, checks the source revision, and pins the report
SHA256. Current compiler/libc++ hashes must match the Imath build inputs.
The final Alembic report includes the original Imath report as a recursively
verified prerequisite. Keep that prerequisite directory alongside the new run.

Alembic `Imath_DIR` is set explicitly to this verified prefix. The configured
selection is checked and IlmBase fallback is disabled. No Imath build is invoked.
UE HEAD and scoped Alembic patch are recorded; actual local sources (including
untracked templates) are retained, without claiming an unmodified upstream tree.

## Scope and Evidence

Release/static/PIC with **HDF5 and Python/PyAlembic OFF**. Binaries, examples and
the upstream test suite are OFF for this bounded slice. This is not full-featured
Alembic, USD integration, geometry-cache import in UE, or an Editor/U1 PASS.

Each run prints `artifacts/carla/usd/alembic-<UTC>-<random>/` containing:

- Original local source copy and source hashes, recipe/smoke/build-rule snapshots.
- Exact configure/build/install/link commands and raw logs, CMake cache and compile commands.
- Installed `libAlembic.a`, headers/CMake exports, source/toolchain/output hashes.
- AArch64 ELF64 checks for every static member, whole-archive shared linking with
  `-z defs -z text`, dynamic-link checks rejecting unresolved symbols or libstdc++.
- Ogawa archive written in one process and read in another: hierarchy/translation,
  four vertices, two triangular faces, two time samples and string metadata.
- Explicit rejection of a truncated archive, missing input and output overwrite.
- `stage-report.json`, Imath dependency pin, exit code and failure decision.

The raw `.abc` control, retained source and installed files are individually
hashed by the report. Live/retained source and prerequisite hashes are rechecked
after execution. No global lock, cleanup, source reset or main-thread UE build
operation is performed.

## Focused Tests

After the real build, use its printed report path:

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  -e CARLA_ALEMBIC_REPORT=/artifacts/carla/usd/alembic-<run>/stage-report.json \
  -e CARLA_IMATH_REPORT=/artifacts/carla/usd/imath-<run>/stage-report.json \
  carla-dev python3 -B -m unittest tests.test_carla_alembic -v
```

The tests exercise limits, source absence, dependency tampering, timeout failure
reports, output isolation, and the real Ogawa smoke/report. Synthetic fixtures
are failure-path checks, not substitutes for the native source build.

## Verified Run

Native ARM64 Docker build on 2026-09-16, four jobs, worker exit 0:

`artifacts/carla/usd/alembic-20260916T024550Z-UKvNhn/stage-report.json`

The run retained 798 local source files and compiled all 103 archive members as
AArch64 ELF64 relocatable objects. Whole-archive PIC linking and dynamic-link
checks passed. Separate writer and reader processes produced:

```text
Alembic 1.8.6 Ogawa write PASS vertices=4 faces=2 samples=2
Alembic 1.8.6 Ogawa read PASS vertices=4 faces=2 samples=2
```

The prerequisite was the requested `imath-20260916T021906Z-QilDi2` report. Before
and after hashes matched, including all 331 Imath source files and the installed
static library. `imath-pin.json` retains the exact report/library SHA256 values.
Independent recursive report validation passed; `focused-tests.log` records
**10 tests passed**, including source/library/header tampering, unrelated stage
identity, a PASS report missing the required library evidence, and timeout.

`build.log`, `ogawa-write.log`, `ogawa-read.log`, `rejection.log`, and
`outputs/mesh.abc` retain the raw execution evidence. HDF5/Python were disabled;
this completes only the named Alembic Ogawa slice, not U1/OpenUSD/UE integration.
