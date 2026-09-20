#!/usr/bin/env python3
"""Pinned OpenUSD build for the UE Linux feature profile, in an isolated prefix."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stage_report import validate_report, write_report

COMMIT = "2864f3d04f396432f22ec5d6928fc37d34bb4c90"
STAGE = "carla-openusd-native-arm64"
SCOPE = "Native ARM64 OpenUSD 24.05 UE-patched Linux feature profile, Python and plugin smoke; not UE deployment, Editor or Cook"
DEPENDENCIES = {
    "tbb": ("carla-tbb-native-arm64", "Native ARM64 TBB 2019u8 dynamic libraries and smoke; not static TBB, OpenUSD, UE Editor or USD SDK"),
    "python": ("carla-python-native-arm64", "Native ARM64 CPython 3.11.8 executable, shared library, static PIC archive, core stdlib, extension and embedding smoke ONLY; not full optional stdlib, pip, OpenUSD or UE Editor/Cook"),
    "imath": ("carla-imath-native-arm64", "Native ARM64 Imath 3.1.9 Release static PIC library and smoke; not PyImath, OpenUSD or UE Editor"),
    "alembic": ("carla-alembic-native-arm64", "Native ARM64 Alembic 1.8.6 Release static PIC library and Ogawa write/read; not HDF5, Python, OpenUSD or UE Editor"),
    "opensubdiv": ("carla-opensubdiv-native-arm64", "Native ARM64 OpenSubdiv 3.6.0 Release osdCPU static PIC and subdivision smoke ONLY; not GPU backends, Python, full OpenSubdiv, OpenUSD or UE Editor/Cook"),
    "materialx": ("carla-materialx-native-arm64", "Native ARM64 MaterialX 1.38.5 six Release PIC libraries and XML material validation; not rendering, Python, OpenUSD or UE Editor"),
    "boost": ("carla-boost-native-arm64", "Native ARM64 Boost 1.82.0 nine static PIC/shared libraries and CPython 3.11.8 extension smoke; not compression filters, NumPy, OpenUSD or UE Editor"),
}
PATCH_NAMES = {
    "OpenUSD_v2405_clang_TfSafeTypeCompare.patch",
    "OpenUSD_v2405_explicit_SdfAssetPath_dtor.patch",
    "OpenUSD_v2405_hdSt_Metal_MaterialX_versioning.patch",
    "OpenUSD_v2405_msvc_preprocessor_version_handling.patch",
    "OpenUSD_v2405_usdMtlx_undef_stdlib_dir.patch",
    "OpenUSD_v2405_weakPtrFacade_cpp20_equality_rewriting.patch",
}
CHECKS = ("preflight", "dependencies", "source", "configure", "build", "install",
          "architecture", "smoke", "retention")


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected regular file: {path}")
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def snapshot(root):
    records = {}
    for folder, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in directories:
            if (Path(folder) / name).is_symlink():
                raise ValueError("source directory symlink")
        for name in sorted(files):
            path = Path(folder) / name
            records[str(path.relative_to(root))] = digest(path)
    if not records:
        raise ValueError(f"empty source tree: {root}")
    return records


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_dependencies(path):
    values = json.loads(path.read_text())
    if not isinstance(values, dict) or set(values) != set(DEPENDENCIES):
        raise ValueError("dependency map must provide exactly: " + ", ".join(DEPENDENCIES))
    result = {}
    for name, (stage, scope) in DEPENDENCIES.items():
        report_path = Path(values[name])
        if not report_path.is_absolute():
            raise ValueError("prerequisite report paths must be absolute container paths")
        pin = digest(report_path)
        report = validate_report(report_path, stage_id=stage, scope=scope)
        if digest(report_path) != pin:
            raise ValueError("prerequisite changed during verification")
        result[name] = {"path": str(report_path), "sha256": pin,
                        "prefix": str(report_path.parent / "install"), "report": report}
    # The headers/configs and libraries actually consumed by CMake must be bound
    # by their own stage evidence, not merely located beside an unrelated PASS.
    required = {
        "tbb": ("lib/libtbb.so.2", "lib/libtbbmalloc.so.2"),
        "python": ("bin/python3.11", "lib/libpython3.11.so.1.0", "include/python3.11/Python.h"),
        "boost": ("lib/libboost_python311-mt-a64.so.1.82.0", "include/boost/version.hpp"),
        "imath": ("lib/libImath-3_1.a", "lib/cmake/Imath/ImathConfig.cmake"),
        "alembic": ("lib/libAlembic.a", "include/Alembic/Abc/All.h"),
        "opensubdiv": ("lib/libosdCPU.a", "include/opensubdiv/version.h"),
        "materialx": ("lib/libMaterialXCore.a", "lib/libMaterialXFormat.a", "lib/cmake/MaterialX/MaterialXConfig.cmake"),
    }
    for name, relative_files in required.items():
        item = result[name]
        base = Path(item["path"]).parent
        bound = {(base / value["path"]).resolve(): value["sha256"]
                 for value in item["report"]["evidence"].values()}
        for relative in relative_files:
            file = (Path(item["prefix"]) / relative).resolve()
            if file not in bound or digest(file) != bound[file]:
                raise ValueError(f"{name} report does not bind the required input: {relative}")
    for child, parent in (("alembic", "imath"), ("boost", "python")):
        if not any(p["sha256"] == result[parent]["sha256"] and p["stage_id"] == DEPENDENCIES[parent][0]
                   for p in result[child]["report"]["prerequisites"]):
            raise ValueError(f"{child} was built against a different {parent} prerequisite")
    return result


class Build:
    def __init__(self, args):
        self.args = args
        self.start = time.monotonic()
        self.run = Path(tempfile.mkdtemp(prefix="openusd-", dir=args.artifact_root))
        self.run.chmod(0o755)
        self.checks = {name: "FAIL" for name in CHECKS}
        self.phase = "preflight"
        self.deps = {}
        self.error = ""
        self.evidence = {}
        self.ue_commit = "unverified"
        print(f"openusd artifacts={self.run}", flush=True)

    def command(self, label, argv, cwd=None, env=None):
        command = list(map(str, argv))
        dump(self.run / (label + ".command.json"), {"argv": command, "cwd": str(cwd or Path.cwd())})
        limit = max(1, int(self.args.timeout - (time.monotonic() - self.start)))
        log = self.run / (label + ".log")
        with log.open("wb") as stream:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = process.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                code = 124
        (self.run / (label + ".exit-code.txt")).write_text(str(code) + "\n")
        print(f"{label}: exit={code} log={log}", flush=True)
        if code != 0:
            with log.open(errors="replace") as stream:
                from collections import deque
                print("".join(deque(stream, 30)), flush=True)
            raise RuntimeError(f"{label} failed with exit {code}")
        return log.read_text(errors="replace").strip() if label.endswith("identity") else None

    def run_build(self):
        a, run = self.args, self.run
        source, work, prefix = run / "source", run / "work", run / "install"
        self.ue_commit = self.command("ue-identity", ["git", "-c", f"safe.directory={a.ue_root}",
            "-C", a.ue_root, "rev-parse", "HEAD"])
        identity = self.command("source-identity", ["git", "-c", f"safe.directory={a.source}",
            "-C", a.source, "rev-parse", "HEAD"])
        if identity != COMMIT:
            raise ValueError("OpenUSD source is not the pinned v24.05 commit")
        state = subprocess.check_output(["git", "-c", f"safe.directory={a.source}", "-C", str(a.source),
            "status", "--porcelain", "--untracked-files=all"], text=True)
        if state:
            raise ValueError("upstream OpenUSD source must be clean; use the copied tree for patches")
        self.checks["preflight"] = "PASS"
        self.phase = "dependencies"
        self.deps = read_dependencies(a.dependencies)
        dump(run / "dependencies.json", {k: {x: v[x] for x in ("path", "sha256", "prefix")} for k, v in self.deps.items()})
        self.dependency_map_hash = digest(a.dependencies)
        shutil.copyfile(a.dependencies, run / "dependency-map.input.json")
        self.checks["dependencies"] = "PASS"
        self.phase = "source"
        self.command("source-copy", ["git", "clone", "--no-hardlinks", "--local", a.source, source])
        for filename in ("openusd_stage.py", "openusd-smoke.py", "build-arm64-openusd.sh"):
            original = Path(__file__).parent / filename
            target = run / filename
            shutil.copyfile(original, target)
            self.evidence["input." + filename] = target
        toolchain = Path(__file__).parents[1] / "ue-arm64-third-party.cmake"
        shutil.copyfile(toolchain, run / toolchain.name)
        self.inputs = {str(p): digest(p) for p in (
            Path(__file__), Path(__file__).with_name("openusd-smoke.py"),
            Path(__file__).with_name("build-arm64-openusd.sh"), toolchain)}
        patch_root = a.ue_root / "Engine/Plugins/Runtime/USDCore/Source/ThirdParty/USD"
        llvm = Path(os.environ.get("CARLA_LLVM_BIN", "/usr/lib/llvm-18/bin"))
        libcxx = a.ue_root / "Engine/Source/ThirdParty/Unix/LibCxx/lib/Unix/aarch64-unknown-linux-gnueabi"
        toolchains = {}
        for path in (llvm / "clang", llvm / "clang++", llvm / "llvm-ar", llvm / "ld.lld",
                     libcxx / "libc++.a", libcxx / "libc++abi.a"):
            toolchains[str(path.resolve())] = digest(path.resolve())
        self.inputs.update(toolchains)
        dump(run / "toolchain.sha256.json", toolchains)
        patches = sorted(patch_root.glob("OpenUSD_v2405_*.patch"))
        if {p.name for p in patches} != PATCH_NAMES:
            raise ValueError("pinned UE USD patch set differs")
        (run / "patches").mkdir()
        for index, patch in enumerate(patches):
            retained = run / "patches" / patch.name
            shutil.copyfile(patch, retained)
            self.inputs[str(patch)] = digest(patch)
            self.command(f"patch-check-{index}", ["git", "apply", "--check", retained], cwd=source)
            self.command(f"patch-apply-{index}", ["git", "apply", retained], cwd=source)
        self.source_before = snapshot(source)
        dump(run / "source.sha256.json", self.source_before)
        self.command("patched-source", ["git", "diff", "--binary", "HEAD"], cwd=source)
        dump(run / "inputs.sha256.json", self.inputs)
        self.checks["source"] = "PASS"
        p = {name: Path(value["prefix"]) for name, value in self.deps.items()}
        tbb_root = Path(self.deps["tbb"]["path"]).parent
        tbb_include = tbb_root / "source/IntelTBB-2019u8/include"
        python = p["python"] / "bin/python3.11"
        # Link extensions to one shared CPython runtime, not a private static
        # interpreter copy per DSO. Static libpython remains separately verified.
        flags = {
            "CMAKE_BUILD_TYPE": "Release", "CMAKE_INSTALL_PREFIX": str(prefix),
            # RTLD_LOCAL extensions do not have unique RTTI addresses. OpenUSD
            # also hashes std::type_index; TfSafeTypeCompare alone is insufficient.
            "CMAKE_CXX_FLAGS_RELEASE": "-O3 -DNDEBUG -D_LIBCPP_TYPEINFO_COMPARISON_IMPLEMENTATION=2",
            "CMAKE_EXPORT_COMPILE_COMMANDS": "ON", "CMAKE_POSITION_INDEPENDENT_CODE": "ON",
            "CMAKE_FIND_ROOT_PATH": ";".join(map(str, p.values())) + ";" + str(tbb_root / "source/IntelTBB-2019u8"),
            "CMAKE_PREFIX_PATH": f"{p['imath']};{p['materialx']}",
            "CMAKE_INSTALL_RPATH_USE_LINK_PATH": "ON", "CMAKE_INSTALL_RPATH": "$ORIGIN",
            # USD libraries exchange C++ streams/locale facets. Localizing each
            # static libc++ copy but exporting inline STL functions mixes their
            # state across DSOs (retained Gf/Usd import-order crash reproduction).
            "CMAKE_SHARED_LINKER_FLAGS": f"-fuse-ld=lld -L{libcxx}",
            "CMAKE_MODULE_LINKER_FLAGS": f"-fuse-ld=lld -L{libcxx}",
            "TBB_INCLUDE_DIR": str(tbb_include), "TBB_INCLUDE_DIRS": str(tbb_include),
            "TBB_LIBRARY": str(p["tbb"] / "lib"),
            "TBB_tbb_LIBRARY_RELEASE": str(p["tbb"] / "lib/libtbb.so.2"), "TBB_USE_DEBUG_BUILD": "OFF",
            "PXR_MALLOC_LIBRARY": str(p["tbb"] / "lib/libtbbmalloc.so.2"),
            "Boost_NO_BOOST_CMAKE": "ON", "Boost_NO_SYSTEM_PATHS": "ON",
            "Boost_ARCHITECTURE": "-a64", "Boost_USE_STATIC_LIBS": "OFF",
            "BOOST_INCLUDEDIR": str(p["boost"] / "include"), "BOOST_LIBRARYDIR": str(p["boost"] / "lib"),
            "Boost_INCLUDE_DIR": str(p["boost"] / "include"),
            "Boost_PYTHON311_LIBRARY_RELEASE": str(p["boost"] / "lib/libboost_python311-mt-a64.so"),
            "Python3_EXECUTABLE": str(python), "Python3_INCLUDE_DIR": str(p["python"] / "include/python3.11"),
            "Python3_LIBRARY": str(p["python"] / "lib/libpython3.11.so"), "Python3_ROOT_DIR": str(p["python"]),
            "PXR_PY_UNDEFINED_DYNAMIC_LOOKUP": "OFF",
            "OPENSUBDIV_INCLUDE_DIR": str(p["opensubdiv"] / "include"),
            "OPENSUBDIV_OSDCPU_LIBRARY": str(p["opensubdiv"] / "lib/libosdCPU.a"),
            "OPENSUBDIV_ROOT_DIR": str(p["opensubdiv"]),
            "ALEMBIC_INCLUDE_DIR": str(p["alembic"] / "include"), "ALEMBIC_DIR": str(p["alembic"]),
            "ALEMBIC_LIBRARY": str(p["alembic"] / "lib/libAlembic.a"),
            "Imath_DIR": str(p["imath"] / "lib/cmake/Imath"), "MaterialX_DIR": str(p["materialx"] / "lib/cmake/MaterialX"),
            "PXR_BUILD_ALEMBIC_PLUGIN": "ON", "PXR_ENABLE_MATERIALX_SUPPORT": "ON",
            "PXR_ENABLE_PYTHON_SUPPORT": "ON", "PXR_BUILD_IMAGING": "ON", "PXR_BUILD_USD_IMAGING": "ON",
            "BUILD_SHARED_LIBS": "ON", "PXR_BUILD_TESTS": "OFF", "PXR_BUILD_EXAMPLES": "OFF",
            "PXR_BUILD_MONOLITHIC": "OFF",
            "PXR_BUILD_TUTORIALS": "OFF", "PXR_BUILD_USD_TOOLS": "OFF", "PXR_BUILD_USDVIEW": "OFF",
            "PXR_ENABLE_GL_SUPPORT": "OFF", "PXR_ENABLE_HDF5_SUPPORT": "OFF",
            "PXR_BUILD_DOCUMENTATION": "OFF",
        }
        dump(run / "cmake-options.json", flags)
        self.phase = "configure"
        self.command("configure", ["cmake", "-S", source, "-B", work, "-G", "Ninja",
            "--toolchain", run / toolchain.name, *[f"-D{k}={v}" for k, v in flags.items()]])
        cache = {}
        for line in (work / "CMakeCache.txt").read_text().splitlines():
            if line and not line.startswith(("#", "//")) and "=" in line:
                name, value = line.split("=", 1)
                cache[name.split(":", 1)[0]] = value
        for name in ("PXR_ENABLE_PYTHON_SUPPORT", "PXR_BUILD_ALEMBIC_PLUGIN", "PXR_ENABLE_MATERIALX_SUPPORT",
                     "PXR_BUILD_IMAGING", "PXR_BUILD_USD_IMAGING", "BUILD_SHARED_LIBS"):
            if cache.get(name) != "ON":
                raise ValueError(f"required UE feature disabled by CMake: {name}")
        for name in ("Boost_INCLUDE_DIR", "Python3_EXECUTABLE", "Python3_INCLUDE_DIR", "Python3_LIBRARY",
                     "TBB_tbb_LIBRARY_RELEASE", "ALEMBIC_LIBRARY", "OPENSUBDIV_OSDCPU_LIBRARY"):
            if Path(cache.get(name, "__missing")).resolve() != Path(flags[name]).resolve():
                raise ValueError(f"dependency selection changed: {name}")
        dump(run / "configuration-verified.json", {k: cache[k] for k in flags if k in cache})
        self.evidence["cmake-cache"] = work / "CMakeCache.txt"
        self.evidence["compile-commands"] = work / "compile_commands.json"
        self.checks["configure"] = "PASS"
        self.phase = "build"
        self.command("build", ["cmake", "--build", work, "--parallel", a.jobs])
        self.checks["build"] = "PASS"
        self.phase = "install"
        self.command("install", ["cmake", "--install", work])
        self.checks["install"] = "PASS"
        self.phase = "architecture"
        elf_files = []
        for file in prefix.rglob("*"):
            if not file.is_file() or file.is_symlink():
                continue
            with file.open("rb") as stream:
                header = stream.read(20)
            if header[:4] == b"\x7fELF":
                if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\xb7\x00":
                    raise ValueError(f"wrong architecture: {file}")
                elf_files.append({"path": str(file), "sha256": digest(file)})
        if len(elf_files) < 30:
            raise ValueError("unexpectedly few OpenUSD libraries/extensions")
        dump(run / "elf.json", elf_files)
        for name in ("libusd_tf.so", "libusd_usd.so", "libusd_usdGeom.so"):
            self.command("link-" + name, ["ldd", "-r", prefix / "lib" / name])
            text = (run / ("link-" + name + ".log")).read_text()
            if any(word in text for word in ("not found", "undefined symbol", "libstdc++")):
                raise ValueError("unresolved or wrong C++ ABI dependency")
        self.checks["architecture"] = "PASS"
        self.phase = "smoke"
        env = dict(os.environ)
        env["PXR_MTLX_STDLIB_SEARCH_PATHS"] = str(p["materialx"] / "libraries")
        self.command("smoke", [python, "-I", "-B", run / "openusd-smoke.py", prefix, run / "outputs",
            Path(self.deps["materialx"]["path"]).parent, Path(self.deps["alembic"]["path"]).parent,
            source], env=env)
        self.checks["smoke"] = "PASS"
        self.phase = "retention"
        if snapshot(source) != self.source_before or any(digest(Path(k)) != v for k, v in self.inputs.items()):
            raise ValueError("source/scripts changed while building OpenUSD")
        if digest(a.dependencies) != self.dependency_map_hash:
            raise ValueError("dependency input map changed")
        final_deps = read_dependencies(a.dependencies)
        if any(v["sha256"] != self.deps[k]["sha256"] for k, v in final_deps.items()):
            raise ValueError("prerequisites changed while building")
        dump(run / "installed.sha256.json", snapshot(prefix))
        self.checks["retention"] = "PASS"

    def finish(self):
        run = self.run
        dump(run / "decision.json", {"phase": self.phase, "error": self.error, "checks": self.checks})
        evidence = dict(self.evidence)
        for name in CHECKS:
            evidence[name] = run / "decision.json"
        for path in run.iterdir():
            if path.is_file() and path.name != "stage-report.json":
                evidence["run." + path.name] = path
        for folder in ("patches", "outputs"):
            for path in (run / folder).rglob("*"):
                if path.is_file(): evidence[str(path.relative_to(run))] = path
        if self.checks["retention"] == "PASS":
            for path in (run / "install").rglob("*"):
                if path.is_file() and not path.is_symlink():
                    evidence["installed." + str(path.relative_to(run / "install"))] = path
        report = write_report(run / "stage-report.json", stage_id=STAGE, scope=SCOPE,
            exit_code=0 if not self.error and all(v == "PASS" for v in self.checks.values()) else 1,
            required_checks=list(CHECKS), checks=self.checks, evidence=evidence,
            sources={"openusd": {"location": str(run / "source"), "revision": COMMIT},
                     "ue": {"location": str(self.args.ue_root), "revision": self.ue_commit}},
            command=["bash", str(Path(__file__).with_name("build-arm64-openusd.sh"))],
            prerequisites=[{"path": v["path"], "stage_id": DEPENDENCIES[k][0], "scope": DEPENDENCIES[k][1]} for k, v in self.deps.items()])
        print(f"{report['status']} {STAGE} report={run / 'stage-report.json'}", flush=True)
        return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "ue-root", "dependencies", "artifact-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if not Path("/.dockerenv").is_file() or platform.machine() != "aarch64":
        parser.error("native ARM64 Docker required")
    if not 1 <= args.timeout <= 14400:
        parser.error("timeout must be 1..14400")
    for name in ("source", "ue_root", "dependencies", "artifact_root"):
        setattr(args, name, getattr(args, name).resolve())
    if args.artifact_root.is_relative_to(args.ue_root) or args.artifact_root.is_relative_to(args.source):
        parser.error("refusing to write artifacts into a source tree")
    args.artifact_root.mkdir(parents=True, exist_ok=True)
    build = Build(args)
    try:
        build.run_build()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        build.error = str(error)
        print(f"FAIL phase={build.phase}: {error}", flush=True)
    return build.finish()


if __name__ == "__main__":
    raise SystemExit(main())
