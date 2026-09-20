# Native ARM64 Boost 1.82.0

This bounded stage bootstraps b2 from the official 1.82.0 source archive and
builds the nine `Boost.Build.cs` libraries: atomic, chrono, filesystem, iostreams,
program_options, python311, regex, system and thread. Both static PIC and shared
outputs are required. No UE deploy paths, Make/Compose or other recipes change.

## Run

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -e CARLA_BUILD_JOBS=4 -e CARLA_BOOST_TIMEOUT_SECONDS=1800 \
  -e CARLA_PYTHON_REPORT=/artifacts/carla/usd/python-20260916T061920Z-6Mp0EP/stage-report.json \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-boost.sh
```

`CARLA_BOOST_ARCHIVE` optionally selects a cached official tar.bz2. Otherwise the
recipe downloads `boost_1_82_0.tar.bz2` from `archives.boost.io/release/1.82.0/source/`.
The mandatory SHA256 is
`a6e1ab9b0860e6a2881dd7b21fe9f737a095e5f33a3a874afc6a345228597ee6`.
No other version, prebuilt binary or headers-only install is accepted.

The default Python report SHA256 is
`2c73ab400124041f5af994c234edb11518b8ead136cbdc380d3318a5da78d7cc`.
Changing Python requires an explicit `CARLA_PYTHON_REPORT_SHA256` as well as its
report path, with the same exact stage/scope. Source manifests, interpreter,
headers and library evidence are rechecked before and after Boost execution.

Only native aarch64 Docker is supported. Jobs are restricted to 1-4 and worker
timeout to 1-3600 seconds. A supervisor publishes a FAIL report on timeout or
any bootstrap, build, dependency, architecture, linkage or smoke failure.

## Consumer Contract

The output prints a unique `artifacts/carla/usd/boost-<run>/` directory.
`install/include` contains Boost headers; `install/lib` contains b2's actual
`--layout=tagged` outputs `libboost_<component>-mt-a64.a` and
`libboost_<component>-mt-a64.so.1.82.0` plus the unversioned shared link.
No x64 library is renamed. Use `BOOST_ROOT=<prefix>`, `Boost_ARCHITECTURE=-a64`,
`Boost_NO_SYSTEM_PATHS=ON`, `Boost_USE_MULTITHREADED=ON`, component `python311`.
`consumer.json` lists exact output paths and hashes after runtime validation.

UE's current Linux/ARM64 Editor rule still chooses an x64 filename suffix;
main-thread integration must correct that selection rather than aliasing x64.
The recipe uses native LLVM with the UE ARM64 sysroot and libc++, including
bootstrap; no libstdc++ fallback. Both linked forms run a real C++ smoke.
Whole-archive shared linking verifies PIC for every required static archive.

On Unix, upstream Boost.Python does not directly link libpython. Our static and
shared Boost.Python extension variants explicitly link the supplied
`install/lib/libpython3.11.so.1.0` and run under its `bin/python3.11 -I -B`.
Import, functions, strings, wrapped classes, C++ exception translation and Python
argument errors are tested. RUNPATH and `ldd -r` must resolve the intended prefix.

The UE sysroot has no compressor development inputs: Boost.Iostreams is built
without zlib/bzip2/lzma/zstd filters, and NumPy is not enabled. These exclusions
must not be reported as full compression/NumPy or full Editor support. This is
not an OpenUSD/U1 completion claim.

## Retention and Tests

The run retains the official raw tar, pristine source, bootstrap working copy,
hash manifests, configuration patch, UE rules, compiler wrapper/user-config,
commands, bootstrap/b2 logs, installed outputs, and the Python report pin.
`stage-report.json` binds the exact Python report as a recursive prerequisite.
Build products live outside pristine source. No UBT or other dependency build
is invoked.

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  -e CARLA_BOOST_REPORT=/artifacts/carla/usd/boost-<run>/stage-report.json \
  carla-dev python3 -B -m unittest tests.test_carla_boost -v
```

## Verified Prefix

Real native ARM64 Docker run on 2026-09-16, four b2 jobs:

```text
report=/artifacts/carla/usd/boost-20260916T070841Z-re7OmX/stage-report.json
report_sha256=6d50a962609b4d2d035c3bfc7349e4c9c4cd0cf91a9ba715d716f569e31c4222
prefix=/artifacts/carla/usd/boost-20260916T070841Z-re7OmX/install
include=${prefix}/include
lib=${prefix}/lib
```

Stage ID: `carla-boost-native-arm64`. Exact scope:

```text
Native ARM64 Boost 1.82.0 nine static PIC/shared libraries and CPython 3.11.8 extension smoke; not compression filters, NumPy, OpenUSD or UE Editor
```

All nine named libraries were built as static and shared; all 62 archive members
are AArch64 ELF64. Whole-archive PIC linking, static/shared C++ smoke and both
Boost.Python extension variants passed. Python import, integer/string calls,
wrapped classes, translated C++ exceptions and bad-argument rejection ran under
the exact pinned CPython executable. Independent recursive report validation and
**9 focused tests** passed. No source changes were made to Boost; only the
bootstrap-generated project configuration was replaced by explicit user config,
with the configuration diff retained.

Actual Python library names are `libboost_python311-mt-a64.a` and
`libboost_python311-mt-a64.so.1.82.0` with an unversioned `.so` link. The latter is
also the shared SONAME. Extension RUNPATH resolves the requested Python prefix;
the Python library is `lib/libpython3.11.so.1.0` under
`/artifacts/carla/usd/python-20260916T061920Z-6Mp0EP/install`.

Raw evidence: `bootstrap.log`, `build.log`, `elf.log`, `runtime.log`,
`focused-tests.log`, `libraries.json`, `consumer.json`, and the individual
readelf/ldd outputs. The pristine source archive, 75460-file source manifest,
original and bootstrap working trees are retained. Earlier runs
`boost-20260916T070404Z-6kKwKN` and `boost-20260916T070615Z-ANUyey` remain FAIL:
they exposed wrapper handling of bootstrap language flags and b2 target
overrides, both now covered by the native compiler regression test.

Boost.System upstream 1.82 retains a compatibility binary while its principal
implementation is header-only; the recipe builds the real upstream target and
does not fabricate an archive. Compression filters, ICU-dependent regex
extensions and NumPy remain outside this slice. Full Editor/OpenUSD acceptance
is still the downstream integrators responsibility.
