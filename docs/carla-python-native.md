# Native ARM64 CPython 3.11.8

The patch version comes from the local UE
`Engine/Source/ThirdParty/Python3/Linux/include/patchlevel.h`: 3.11.8 final.
`python_v3.11.x.tps` alone is not sufficient to select a patch. This recipe
rejects a different header version and never selects the latest release.

The local SDK tree inspected for this stage contains headers and prebuilt
libraries, not CPython source (`configure`/`Python/ceval.c`). The recipe accepts
an explicit `CARLA_PYTHON_SOURCE_DIR`, or a clean local
`Python3/Python-3.11.8` directory. Otherwise it fetches the official source:

```text
https://www.python.org/ftp/python/3.11.8/Python-3.11.8.tar.xz
SHA256 9e06008c8901924395bc1da303eac567a729ae012baa182ab39269f650383bb3
CPython release tag v3.11.8
```

The official Sigstore bundle is retained and its declared digest is compared
with the pinned hash. This is **not cryptographic signature verification**.
The tarball is hashed before safe extraction. All required source files and
the source patchlevel must exist; missing/invalid sources are not replaced with
SDK binaries. Download errors remain in the source log and FAIL report.

## Run

Integrated entry: `make carla-python JOBS=4`.

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -e CARLA_BUILD_JOBS=4 -e CARLA_PYTHON_TIMEOUT_SECONDS=1800 \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-python.sh
```

Use native ARM64 `carla-dev` Docker with UE mounted read-only. The unique prefix
is `artifacts/carla/usd/python-<UTC>-<random>/install/`. Jobs are restricted to
1-4 and the worker timeout to 1-3600 seconds (default 1800), plus a 20-second
kill grace. The supervisor retains a FAIL report on timeout. No UBT, system
Python replacement, UE deployment, Make/Compose/ledger edits or package install
are involved.

The native LLVM compiler targets `aarch64-unknown-linux-gnueabi` using the UE
ARM64 sysroot. CPython is C, so no C++ runtime is substituted. Shared and static
libpython are configured explicitly; all core compilation uses `-fPIC`.
`make altinstall` installs only inside the isolated prefix. The actually built
static archive is also copied to `install/lib/libpython3.11.a`, checked byte for
byte, in addition to CPython's normal config-directory installation.

## Source and Build Boundaries

`source/` is the untouched source copy; `build-source/` is a second copy used for
an out-of-tree build in `work/`. A retained `build-constraints.patch` makes only
these build-system changes:

- Cap setup.py's extension compilation at the selected jobs value, instead of
  its native `parallel=True` CPU-count expansion.
- Restrict setup.py header/library discovery to source/build and UE sysroot
  paths, avoiding Debian/Ubuntu host include/library directories. Native module
  execution checks remain enabled; this does not masquerade as cross-compiling.
- Replace install-time `compileall -j0` with the selected jobs bound.
- Add `-B` explicitly to `PYTHON_FOR_BUILD`, `PYTHON_FOR_FREEZE` and
  `PYTHON_FOR_REGEN`. Native build Python uses `-E` and the freezer initializes
  isolated Python, so an environment-only bytecode restriction is insufficient.

pkg-config discovery is disabled. Optional modules without UE-sysroot dependencies
may be absent; `runtime.json` records ssl, ctypes, sqlite, zlib, bz2, lzma,
readline and tkinter availability and import failures explicitly. Their absence
does not become a full-stdlib PASS. ensurepip and upstream test modules are
disabled; pip/network TLS and full CPython regression coverage are not claimed.

The installed executable uses an artifact-specific library rpath; this stage
does not promise relocation. UE Python3.Build.cs still has its separate X64
auto-discovery/deployment policy. Producing ARM64 artifacts does not change that
policy or prove Editor/OpenUSD integration.

## Verification and Retained Evidence

- Version/header/API agreement: CPython 3.11.8 final, AArch64, 64-bit pointers.
- Actual new interpreter runs JSON, math, struct, SHA256, XML, Unicode filesystem
  and subprocess tests with `-I -B`. Required dynamic stdlib modules must load
  from this install, not host/site-packages.
- `python-smoke.c` builds a dynamically loaded C extension and two real embedding
  executables, one linked to stage libpython.so and one to stage libpython.a.
  Both initialize Python, call C APIs and load the new extension.
- Every archive member and installed dynamic extension is checked as AArch64
  ELF64. `ldd -r` must resolve stage libpython for the shared path; the static
  embed must not depend on libpython.so.
- Whole-archive `-shared -z defs -z text` linking demonstrates static PIC
  usability without unresolved symbols or text relocations.
- Pristine/patched sources, exact step commands, build environment, config.log,
  Makefile, pyconfig.h, compiler/source/input hashes, all build/smoke logs and
  per-step/worker return codes are retained. Source copies are rehashed after
  the build; `source.after.sha256.json`, `build-source.after.sha256.json` and
  `source-retention-diff.json` retain full post-build snapshots and exact
  added/removed/changed paths and hashes. No source path, including `__pycache__`,
  is exempted. Installed files are hashed individually as report evidence.

The stage is `carla-python-native-arm64`. PASS requires the executable, shared
library, static PIC archive and all named smoke checks. If the static build or
PIC check fails, its raw logs and partial artifacts remain but the stage is FAIL.

## Focused Tests

```sh
python3 -B -m unittest discover -s tests -p test_carla_python.py -v
bash -n scripts/carla/usd/build-arm64-python.sh
```

For native failure tests and opt-in verification of a real build:

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  -e CARLA_PYTHON_REPORT=/artifacts/carla/usd/python-<run>/stage-report.json \
  carla-dev python3 -B -m unittest discover -s tests -p test_carla_python.py -v
```

Tests do not launch a CPython build or fabricate native PASS reports.

## Verified Run and Retention Fix

On 2026-09-16 the first run, `python-20260916T052323Z-rQzsAe`, built and ran
the executable/shared/static/PIC/embedding checks successfully but correctly
failed retention. There were exactly 172 added bytecode files and no removed or
modified original files. Examples:

```text
Lib/__pycache__/os.cpython-311.pyc
Lib/encodings/__pycache__/utf_8.cpython-311.opt-2.pyc
Tools/scripts/__pycache__/generate_global_objects.cpython-311.pyc
Tools/scripts/__pycache__/umarshal.cpython-311.pyc
```

`python -E` ignored the environment-only bytecode restriction; isolated bootstrap
Python also needed an explicit flag. The recipe now adds `-B` at all three
Makefile interpreter definitions. No path is excluded from the hash gate, no
post-build bytecode is deleted to hide differences, and the old FAIL report
was not rewritten. Its SHA256 remains:

```text
a4c8014ca398403ba4a25fcdca679f2876760d0b82d5d57a600bd9c7fa9d2ecd
```

A fresh source build completed with exit 0 and all checks PASS:

`artifacts/carla/usd/python-20260916T061920Z-6Mp0EP/stage-report.json`

The new artifact also retains `previous-failure-diff.json` and
`previous-build-source.before.sha256.json` / `previous-build-source.after.sha256.json`,
which record every one of the old added paths and hashes without changing the
old run. For the new build, `source-retention-diff.json` has empty added, removed
and changed sets for both source copies. The new pristine and patched manifests
respectively match their post-build snapshots:

```text
source:       07f093a171230336e2e8ae2e594b23366868ffc3899680290448cc69f2b09b75
build-source: c40666fb428fcaad719077d87f0be083c6e2213714a6be43116e9be4a5b57ea3
```

The real library has 146 AArch64 ELF64 relocatable archive members. The executable,
shared library, embedding programs, PIC proof library and dynamic modules total
61 verified AArch64 ELF files. Both shared and static embedding smoke runs passed,
including the actual dynamically loaded C extension. All 13 focused tests passed
in native ARM64 Docker; commands, test-source hash, raw output and exit 0 are in
`focused-tests-invocation.json`, `focused-tests.log` and
`focused-tests.exit-code.txt`. Shell and Python syntax checks also passed.

This sysroot-only build has no `ssl`, `_ctypes`, `sqlite3`, `zlib`, `bz2`, `lzma`,
`readline` or `_tkinter`; the precise import errors are in `runtime.json`.
Static PIC is complete for this stage, but these optional dependencies, pip,
full stdlib regression, relocation and UE/OpenUSD integration remain outside
the demonstrated scope.
