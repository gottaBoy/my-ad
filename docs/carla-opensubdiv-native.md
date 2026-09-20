# Native ARM64 OpenSubdiv 3.6.0 osdCPU

Integrated entry: `make carla-opensubdiv JOBS=4`.

This U1 slice builds the local UE OpenSubdiv source as a Release PIC static CPU
library. It is not a full OpenSubdiv, GPU, Python, OpenUSD, Editor or Cook PASS.
It never runs UBT, invokes UE's deployment script, or writes to the UE tree.

## Local Dependency Contract

- `Engine/Source/ThirdParty/OpenSubdiv/OpenSubdiv.Build.cs` fixes 3.6.0 and links
  only `libosdCPU.a` on Unix (Debug uses `_d`; this slice is Release only).
- `OpenSubdiv/BuildForUE/Linux/BuildForLinux.sh` selects PIC/static and disables
  CUDA/OpenCL/OpenGL/DX, TBB/OpenMP, PTex, examples and upstream tests.
- `USDCore/Source/ThirdParty/USD/BuildForLinux.sh` points to OpenSubdiv-3.6.0
  headers/libraries and sets `PXR_ENABLE_GL_SUPPORT=OFF`.

The recipe matches that CPU dependency scope. It does not claim to validate the
USD build, all osdCPU APIs, GPU backends or Python bindings. No fake GPU/static
outputs or replacement headers are generated. The existing shared
`ue-arm64-third-party.cmake` is used unchanged: native LLVM tools, the unique UE
ARM64 sysroot, UE libc++ headers and static runtimes.

## Run

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -e CARLA_BUILD_JOBS=4 -e CARLA_OPENSUBDIV_TIMEOUT_SECONDS=600 \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-opensubdiv.sh
```

Use `carla-dev`, whose UE/source mounts are read-only. Jobs must be 1-4. The
worker timeout is 1-1200 seconds (default 600) plus a 15-second kill grace.
Every invocation gets an independent
`artifacts/carla/usd/opensubdiv-<UTC>-<random>/` prefix; no shared build directory
or deployment symlink is updated. Libraries are installed in `install/lib/`
and headers in `install/include/opensubdiv/`, not UE's `Deploy/` directory.

## Real Smoke and Evidence

The smoke constructs a quad using `Far::TopologyDescriptor`, performs one
Catmull-Clark refinement with edge-only boundary interpolation, and builds nine
stencils. It calls the compiled `Osd::CpuEvaluator::EvalStencils` and checks all
nine resulting positions against independent corner, edge-midpoint and center
values, plus four quad faces. It then evaluates the same stencils with translated
input and checks all coordinates again (54 components across both evaluations).
Version 30600 and libc++ headers are compile-time requirements.

The entire `libosdCPU.a` is linked into the smoke shared library using
`--whole-archive -z defs -z text`. All archive members must be ELF64 AArch64 REL;
the shared library and native executable must have no TEXTREL or libstdc++
dependency. `ldd -r` and actual native execution must succeed. This is a bounded
CPU/PIC/ABI check, not an upstream exhaustive regression suite.

Retained evidence includes:

- Local UE HEAD, scoped OpenSubdiv patch, full source copy and per-file SHA256.
  Local/untracked configuration templates are retained as-is; no pristine
  upstream tag is asserted, and missing source/templates fail the build.
- Copies of this recipe, smoke, toolchain, reporter, UE rules and USD build script.
- Toolchain/runtime hashes, compiler version and selected sysroot path.
- Exact step commands, logs and exit codes; worker timeout/failure exit code.
- CMake cache, compile commands, build graph, installed files/hashes, ELF and
  linkage output, numeric smoke output.
- Scoped `stage-report.json` and `decision.json`. Each source/installed file is
  separately hashed. Original/source-copy identity is rechecked after the build.

The supervisor produces FAIL for a failed or timed-out worker; partial build
outputs cannot satisfy missing checks. Platform/argument rejection occurs before
artifact creation. A report is scoped evidence, not signed attestation.

## Focused Verification

```sh
python3 -B -m unittest discover -s tests -p test_carla_opensubdiv.py -v
bash -n scripts/carla/usd/build-arm64-opensubdiv.sh
```

Native failure and real-report tests run in the same read-only Docker service:

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  -e CARLA_OPENSUBDIV_REPORT=/artifacts/carla/usd/opensubdiv-<run>/stage-report.json \
  carla-dev python3 -B -m unittest discover -s tests -p test_carla_opensubdiv.py -v
```

The tests never start a real build. Failure fixtures and missing optional artifact
tests do not establish hardware success. Use the report from an actual run.

## Verified Run

On 2026-09-16 the native ARM64 Docker invocation above completed with exit 0:

`artifacts/carla/usd/opensubdiv-20260916T024540Z-thbjJN/stage-report.json`

- All 47 members of `install/lib/libosdCPU.a` are AArch64 ELF64 REL.
- Whole-archive shared PIC link, no-TEXTREL/no-libstdc++ checks and `ldd -r`
  passed. Original/copied source sets and input/toolchain hashes were unchanged.
- The actual smoke reported 9 refined vertices, 4 quad faces, 9 stencils and 54
  checked coordinate components, including translated-input reevaluation:

```text
OpenSubdiv 3.6.0 osdCPU smoke PASS Catmull-Clark/stencils/libc++
```

All nine focused tests passed in native ARM64 Docker, including verification of
this real report. See `focused-tests.log`, `focused-tests.exit-code.txt` and
`focused-tests-invocation.json` in the same directory. The host-only test run
passed four tests and skipped the five Docker/artifact-dependent tests.

Configure found a Python interpreter as a build tool, not Python bindings.
It also reported an unused `CMAKE_BINDIR_BASE` setting; the archive, install and
smoke checks above passed. Compile logs retain unused `-stdlib=libc++` warnings;
explicit UE libc++ include paths/static runtime and the smoke compile gate
`_LIBCPP_VERSION` were used. None of these results establishes GPU,
Python-binding, OpenUSD integration, Debug-library or full OpenSubdiv support.
