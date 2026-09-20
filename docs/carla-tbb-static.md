# Native ARM64 TBB 2019u8 Static PIC

This slice builds real Release `libtbb.a` and `libtbbmalloc.a` from the local
UE `Engine/Source/ThirdParty/Intel/TBB/IntelTBB-2019u8` sources. It supplements,
not replaces or rewrites, the existing shared-only stage:
`artifacts/carla/usd/tbb-20260915T144340Z-p7WNcW`.

## Two Different Dependency Contracts

- UE `IntelTBB.Build.cs` selects `libtbb.a` and `libtbbmalloc.a` for Linux Release,
  and defines `TBB_USE_EXCEPTIONS=0`. Debug CRT selects `_debug.a`; this slice
  does not build or claim those Debug libraries.
- UE's USD `BuildForLinux.sh` points to the TBB library directory. The local
  OpenUSD v24.05 `cmake/modules/FindTBB.cmake` creates a `TBB::tbb SHARED IMPORTED`
  target. `UnrealUSDWrapper` also retains dynamic TBB runtime dependencies.

The static archive is not a replacement for a shared DSO in OpenUSD's current
configuration. Loading static TBB in UE while USD loads shared TBB can introduce
two runtimes/allocators and symbol-interposition or lifetime issues. This stage
does not establish mixed static/shared coexistence, Editor or OpenUSD success.
Deployment and combined-process tests remain with the main integration thread.

## Run

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -e CARLA_BUILD_JOBS=4 -e CARLA_TBB_STATIC_TIMEOUT_SECONDS=600 \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-tbb-static.sh
```

Native `aarch64` Docker is mandatory; use the read-only UE mounts in `carla-dev`.
Jobs are restricted to 1-4, worker timeout to 1-1200 seconds (default 600), with
a 15-second kill grace. Each run creates
`artifacts/carla/usd/tbb-static-<UTC>-<random>/` with its own source copies,
work directories and `install/` prefix. No UE tree, old shared recipe/artifact,
system prefix, Make, Compose or ledger changes are made.

## Real Objects and Source Patch

The original 2019u8 Linux Makefiles link shared libraries. This recipe retains
`static-targets.patch` inside its run directory and applies it only to
`build-source/`. The two new targets use the unchanged original `TBB.OBJ` and
`MALLOC.OBJ` compilation rules followed by `llvm-ar rcsD`. No `.so` is renamed,
no thin/empty archive is accepted, and no old object or library is reused.

Source input selection is explicit: `src/`, `include/`, `build/`, `Makefile`,
`LICENSE`, `README`. UE's prebuilt `lib/` and unrelated examples are not copied.
Original selected files, pristine copies and patched copies have manifests and
strict before/after verification with exact diffs; there is no generated-file
exemption. C++ sources are not patched. Generated dependency/version/object files
are written to `work/tbb/` and `work/tbbmalloc/`.

Native LLVM uses the UE ARM64 sysroot and UE libc++ headers/static runtimes,
`-fPIC -fno-rtti -fno-exceptions -DTBB_USE_EXCEPTIONS=0`. This bounded static
configuration sets `__TBB_DYNAMIC_LOAD_ENABLED=0` to prevent optional TBB/RML/Cilk
DSO loading. It does not claim those optional integrations or allocator proxy
support. These compile choices and the original UE rules are retained.

Every archive member must match the original Makefile object list, be ELF64
AArch64 `ET_REL`, and match the full bytes of a freshly compiled `.o` in this
run. `archive-objects.json` records each member's object path, SHA256 and size;
both archives, original objects, compile logs and link maps remain available.

## Smoke and PIC Proof

Two actual execution paths are required:

1. Direct executable linked to both archive paths (no `-ltbb` shared lookup).
2. Both archives linked in full using `--whole-archive -shared -z defs -z text`
   into `libtbb-static-smoke.so`, called by a separately linked executable.

Both use libc++, require interface 11008, disable exceptions, bound the TBB
scheduler to four threads, observe a worker, perform 100000 parallel allocations,
check the parallel reduction result, and exercise scalable calloc/realloc,
usable-size and aligned allocation/free. Each checks `/proc/self/maps` before
and after execution to reject an accidental dynamic TBB/allocator/RML load.
Architecture, no-TEXTREL/no-libstdc++ checks and `ldd -r` must also pass.

The report stage is `carla-tbb-static-native-arm64`. Build/smoke failures and
timeouts retain a FAIL report, exact commands, logs and return codes. All
retained source, object and installed files are individually hashed as evidence.
A scoped PASS does not prove the complete upstream suite, Debug, unloading under
all conditions, mixed shared/static usage, OpenUSD, or UE Editor.

## Focused Tests

```sh
python3 -B -m unittest discover -s tests -p test_carla_tbb_static.py -v
bash -n scripts/carla/usd/build-arm64-tbb-static.sh
```

For all native failure tests and real artifact validation:

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  -e CARLA_TBB_STATIC_REPORT=/artifacts/carla/usd/tbb-static-<run>/stage-report.json \
  carla-dev python3 -B -m unittest discover -s tests -p test_carla_tbb_static.py -v
```

Tests do not start a TBB build and do not generate hardware PASS fixtures.

## Verified Run

On 2026-09-16, this fresh native ARM64 Docker run completed with exit 0 and all
16 stage checks PASS:

`artifacts/carla/usd/tbb-static-20260916T070632Z-hhVyTx/stage-report.json`

The libraries for main-thread integration are:

```text
/artifacts/carla/usd/tbb-static-20260916T070632Z-hhVyTx/install/lib/libtbb.a
/artifacts/carla/usd/tbb-static-20260916T070632Z-hhVyTx/install/lib/libtbbmalloc.a
/artifacts/carla/usd/tbb-static-20260916T070632Z-hhVyTx/install/include/
```

`libtbb.a` contains 37 freshly compiled AArch64 ELF64 relocatable members;
`libtbbmalloc.a` contains 6. Every member matched its original work-directory
object byte for byte. Both direct static and full-archive shared linking passed,
with `-z defs -z text` on the shared path and no undefined symbols, TEXTREL,
dynamic TBB dependency or libstdc++ dependency.

Both smoke runs observed 3 worker entries, completed 100000 parallel allocations,
produced the expected sum 5000050000, passed calloc/realloc and 64-byte alignment
checks, and reported `dynamic_tbb_loaded=false`. Original, pristine and patched
source retention diffs are all empty. The 11 focused tests passed in Docker,
including the real report validation; see `focused-tests.log`,
`focused-tests.exit-code.txt` and `focused-tests-invocation.json` for command,
test source hash and exit 0. Shell and embedded Python syntax checks passed.

The first run, `tbb-static-20260916T070153Z-ybnErf`, remains FAIL. Its archives
passed validation, but compiling the new smoke failed because the version-query
function call needed the `tbb::` namespace qualifier:

```text
use of undeclared identifier TBB_runtime_interface_version;
did you mean tbb::TBB_runtime_interface_version?
```

No missing allocator proxy was stubbed, no duplicate symbol was suppressed, and
no PIC or exception check was relaxed. The corrected smoke and both libraries
use `TBB_USE_EXCEPTIONS=0` / `-fno-exceptions`. Existing Clang warnings from the
legacy sources (deprecated implicit copy and unused `-stdlib=libc++` during
compile-only invocations) are preserved in build logs.

This establishes the Release static-PIC dependency slice, not a completed Editor
build, Debug CRT libraries, or coexistence with the OpenUSD shared TBB runtime.
