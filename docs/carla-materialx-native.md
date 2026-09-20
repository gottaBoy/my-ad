# Native ARM64 MaterialX 1.38.5

This isolated U1 slice builds **1.38.5**, as required by USD `BuildForLinux.sh`
and the Linux branch of `MaterialX.Build.cs`. It does not use the generic
MaterialX script's 1.38.10 source, deployed libraries, or a placeholder SDK.

## Build

Integrated entry: `make carla-materialx JOBS=4`.

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -e CARLA_BUILD_JOBS=4 -e CARLA_MATERIALX_TIMEOUT_SECONDS=1200 \
  carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-materialx.sh
```

The recipe requires native aarch64 Docker, limits compiler jobs to 1-4, and
bounds the worker to 1-1200 seconds with a 15 second kill grace. The supervisor
retains a FAIL stage report if the worker fails or times out.

Default source:
`/workspace/unreal-engine/Engine/Source/ThirdParty/MaterialX/MaterialX-1.38.5`.
`CARLA_MATERIALX_SOURCE_DIR` can select another explicit local copy, but its
CMake version must be exactly 1.38.5. Preflight checks the USD version selection
and Linux six-library requirement against the actual rules. Generated headers
and the compiled runtime version are also checked. A directory name alone is
not sufficient. Local 1.38.5 sources exist, so this recipe does not download or
synthesize missing inputs; missing files are reported and stop the build.

It reuses the existing UE native LLVM/sysroot/libc++ toolchain, Release/static
and PIC, with a new source copy, work directory and install prefix for each run.
It does not run UBT or write to UE, Make, Compose, the ledger or shared scripts.

## Libraries and Scope

The six real static libraries are Core, Format, GenShader, GenGlsl, GenMdl and
GenOsl (`libMaterialX*.a`). Their installed headers, CMake exports and standard
material resources are retained. All six archives are whole-archive linked
into a shared smoke library with `-z defs -z text`, then loaded by a native
executable using UE libc++.

Render, Viewer, Python, JS, OIIO, docs and upstream tests are explicitly OFF.
Generator objects are instantiated to exercise their link dependencies; shader
generation correctness or GPU rendering is not claimed. This is not an OpenUSD,
complete U1 or Editor PASS.

The smoke loads installed stdlib/pbrlib/bxdf resources and validates them. It
creates a `standard_surface` shader connected to a `surfacematerial`, checks
typed color/roughness values, validates the document and writes XML with library
definitions embedded. A separate process parses that XML and repeats validation
and semantic checks. Malformed XML, invalid parameter types, missing documents
and overwrite attempts must fail with exit 2.

## Evidence and Tests

Each run prints `artifacts/carla/usd/materialx-<UTC>-<random>/` with:

- Actual local source copy and per-file hashes, including untracked templates.
- Recipe/smoke/toolchain/build-rule snapshots, UE HEAD and scoped tracked patch.
- Exact commands, compiler/runtime hashes, configure/build/install/link logs.
- Per-member AArch64 ELF64 inspection for each archive, PIC/dynamic-link checks.
- Installed archives, headers, resources, XML controls and per-file SHA256.
- `stage-report.json`, worker exit code, failure decision and validation log.

Original and retained source sets and hashes are checked before/after execution.
The report individually hashes retained source and installed files. This records
the actual local source state, not a claimed pristine upstream release.

```sh
docker compose --project-name my-ad-carla --env-file .env \
  -f compose.carla-arm64.yaml --profile g0 run --rm -T --no-deps \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
  -e CARLA_MATERIALX_REPORT=/artifacts/carla/usd/materialx-<run>/stage-report.json \
  carla-dev python3 -B -m unittest tests.test_carla_materialx -v
```

Focused tests cover version substitution, bounds, missing source, architecture,
output isolation, timeout failure evidence and the real XML smoke/report.
Harness fixtures are not substitutes for the real source build.

## Verified Run

On 2026-09-16, the four-job native ARM64 Docker build completed with exit 0:

`artifacts/carla/usd/materialx-20260916T052606Z-jXEd5r/stage-report.json`

The six static archives contain 99 AArch64 ELF64 relocatable members: Core 15,
Format 5, GenGlsl 30, GenMdl 12, GenOsl 5 and GenShader 32. Whole-archive PIC
linking, dynamic-link checks, three generator constructors and both XML processes
passed. The read log records:

```text
MaterialX 1.38.5 XML read/validate PASS material=material_control shader=standard_surface generators=3
```

All 1239 original/retained source file hashes matched after the build. The
independent stage-report rehash passed, and `focused-tests.log` records **9 tests
passed** inside ARM64 Docker. `build.log`, `archives.log`, `xml-write.log`,
`xml-read.log`, `rejection.log` and `outputs/material.mtlx` retain raw evidence.
The installed prefix is the run directory plus `/install`, with CMake exports
under `lib/cmake/MaterialX` and resources under `libraries`.

The earlier `materialx-20260916T052340Z-SARb7v` run remains FAIL: the smoke used a
material name that collided with a standard-library element. Only the smoke name
was corrected; MaterialX source and validation were not modified or weakened.
This scoped PASS does not establish rendering, OpenUSD integration or U1 completion.
