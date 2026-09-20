#!/usr/bin/env python3
"""Boost stage-local provenance, toolchain, ELF and native smoke helper."""

import argparse
import difflib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile


sys.dont_write_bytecode = True
VERSION = "1.82.0"
SOURCE_SHA256 = "a6e1ab9b0860e6a2881dd7b21fe9f737a095e5f33a3a874afc6a345228597ee6"
SOURCE_URL = "https://archives.boost.io/release/1.82.0/source/boost_1_82_0.tar.bz2"
PYTHON_REPORT = "/artifacts/carla/usd/python-20260916T061920Z-6Mp0EP/stage-report.json"
PYTHON_SHA256 = "2c73ab400124041f5af994c234edb11518b8ead136cbdc380d3318a5da78d7cc"
PYTHON_STAGE = "carla-python-native-arm64"
PYTHON_SCOPE = ("Native ARM64 CPython 3.11.8 executable, shared library, static PIC archive, "
                "core stdlib, extension and embedding smoke ONLY; not full optional stdlib, "
                "pip, OpenUSD or UE Editor/Cook")
STAGE = "carla-boost-native-arm64"
SCOPE = ("Native ARM64 Boost 1.82.0 nine static PIC/shared libraries and CPython 3.11.8 "
         "extension smoke; not compression filters, NumPy, OpenUSD or UE Editor")
LIBRARIES = ("atomic", "chrono", "filesystem", "iostreams", "program_options",
             "python311", "regex", "system", "thread")
CHECKS = ("python-before", "preflight", "source", "bootstrap", "configure", "build",
          "elf", "pic-link", "smoke-static-build", "smoke-shared-build",
          "extension-static-build", "extension-shared-build", "runtime",
          "python-after", "retention", "worker-exit")


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def reporter():
    spec = importlib.util.spec_from_file_location("boost_reporter", Path(__file__).resolve().parents[1] / "stage_report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_command(command, **kwargs):
    print(shlex.join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def snapshot(root):
    records = {}
    for path in sorted(Path(root).rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        require(stat.S_ISREG(mode), f"nonregular source: {path}")
        records[path.relative_to(root).as_posix()] = digest(path)
    require(records, f"empty source: {root}")
    return records


def validate_python(path, expected_sha256):
    path = Path(path).resolve()
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256), "invalid pinned Python report hash")
    require(digest(path) == expected_sha256, "Python report SHA256 mismatch")
    report = reporter().validate_report(path, stage_id=PYTHON_STAGE, scope=PYTHON_SCOPE)
    base = path.parent
    def bound(relative):
        record = report["evidence"].get("retained." + relative)
        require(isinstance(record, dict) and record["path"] == relative
                and record["sha256"] == digest(base / relative), f"unbound Python input: {relative}")
    for name in ("bin/python3.11", "include/python3.11/Python.h", "include/python3.11/pyconfig.h",
                 "include/python3.11/patchlevel.h", "lib/libpython3.11.so.1.0"):
        bound("install/" + name)
    for folder, manifest in (("source", "pristine-source.sha256.json"),
                             ("build-source", "build-source.sha256.json")):
        bound(manifest)
        expected = json.loads((base / manifest).read_text())
        require(snapshot(base / folder) == expected, f"Python {folder} set/hash mismatch")
    require(digest(path) == expected_sha256, "Python report changed during validation")
    return base / "install"


def python_gate(run, phase):
    path = Path(os.environ.get("CARLA_PYTHON_REPORT", PYTHON_REPORT)).resolve()
    expected = os.environ.get("CARLA_PYTHON_REPORT_SHA256", PYTHON_SHA256)
    prefix = validate_python(path, expected)
    pin = {"report": str(path), "sha256": expected, "prefix": str(prefix),
           "library_sha256": digest(prefix / "lib/libpython3.11.so.1.0")}
    if phase == "before":
        write_json(run / "python-pin.json", pin)
        shutil.copyfile(path, run / "inputs/python-stage-report.json")
        (run / "python-prefix.txt").write_text(str(prefix) + "\n")
    else:
        require(json.loads((run / "python-pin.json").read_text()) == pin, "Python dependency changed")
    print(json.dumps(pin, indent=2))


def extract(archive, target):
    require(digest(archive) == SOURCE_SHA256, "official Boost archive SHA256 mismatch")
    require(not target.exists(), "refusing to overwrite Boost source")
    with tarfile.open(archive, "r:bz2") as stream:
        members = stream.getmembers()
        for member in members:
            path = Path(member.name)
            require(not path.is_absolute() and ".." not in path.parts and path.parts
                    and path.parts[0] == "boost_1_82_0", f"unsafe source path: {path}")
            require(member.isdir() or member.isfile(), f"nonregular archive member: {path}")
        stream.extractall(target, members=members)


def prepare(run):
    cached = os.environ.get("CARLA_BOOST_ARCHIVE")
    archive = run / "boost_1_82_0.tar.bz2"
    if cached:
        require(digest(cached) == SOURCE_SHA256, "cached official Boost archive SHA256 mismatch")
        shutil.copyfile(cached, archive)
    else:
        run_command(["curl", "--fail", "--location", "--retry", "2", "--connect-timeout", "20",
                     "--max-time", "240", "--dump-header", run / "download.headers",
                     "--output", archive, SOURCE_URL])
    extract(archive, run / "download")
    (run / "download/boost_1_82_0").rename(run / "source")
    version = (run / "source/boost/version.hpp").read_text()
    require(re.search(r"^#define BOOST_VERSION 108200\s*$", version, re.M), "wrong Boost source version")
    for name in ("bootstrap.sh", "tools/build/src/engine/build.sh", "libs/python/build/Jamfile",
                 "libs/system/src/error_code.cpp"):
        require((run / "source" / name).is_file(), f"missing upstream source: {name}")
    original = snapshot(run / "source")
    write_json(run / "pristine-source.sha256.json", original)
    shutil.copytree(run / "source", run / "build-source")
    # Keep the authoritative source immutable; bootstrap-generated files live in build-source.
    (run / "source.patch").write_text("")
    write_json(run / "build-source.sha256.json", original)
    write_json(run / "source-identity.json", {"version": VERSION, "url": SOURCE_URL,
               "sha256": SOURCE_SHA256, "source_files": len(original),
               "manifest_sha256": digest(run / "pristine-source.sha256.json")})
    print(f"Verified official Boost {VERSION}, retained {len(original)} pristine source files")


def toolchain(run, ue):
    llvm = Path(os.environ["CARLA_LLVM_BIN"])
    triple = "aarch64-unknown-linux-gnueabi"
    roots = list((ue / "Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64").glob("v*_clang-*/" + triple))
    require(len(roots) == 1, "expected one UE ARM64 sysroot")
    sysroot = roots[0]
    libcxx = ue / "Engine/Source/ThirdParty/Unix/LibCxx"
    lib = libcxx / "lib/Unix" / triple
    for path in (llvm / "clang++", llvm / "llvm-ar", llvm / "llvm-ranlib", llvm / "llvm-readelf",
                 llvm / "ld.lld", lib / "libc++.a", lib / "libc++abi.a"):
        require(path.is_file(), f"missing toolchain input: {path}")
    rules = (ue / "Engine/Source/ThirdParty/Boost/Boost.Build.cs").read_text()
    match = re.search(r"BoostLibraries\s*=\s*\{([^}]+)\}", rules)
    require(match and tuple(re.findall(r'"([^"]+)"', match[1])) == LIBRARIES, "UE nine-library contract changed")
    require('BoostVersion = "1_82_0"' in rules, "UE Boost version changed")
    prefix = Path((run / "python-prefix.txt").read_text().strip())
    compiler = run / "compiler/clang++"
    compiler.parent.mkdir()
    # Both bootstrap and b2 use this native compiler wrapper. Link-only arguments
    # are appended after object/archive inputs to preserve static runtime ordering.
    common = [str(llvm / "clang++"), "--target=" + triple, "--sysroot=" + str(sysroot),
              "-fPIC", "-stdlib=libc++", "-nostdinc++",
              "-isystem", str(libcxx / "include"), "-isystem", str(libcxx / "include/c++/v1")]
    link = ["--target=" + triple, "-x", "none", "-fuse-ld=lld", "-nodefaultlibs", str(lib / "libc++.a"), str(lib / "libc++abi.a"),
            "-lm", "-lc", "-lgcc_s", "-lgcc", "-lpthread", "-ldl", "-lrt",
            "-Wl,--exclude-libs,ALL"]
    compiler.write_text("#!/usr/bin/env bash\nset -euo pipefail\nlink=1\n"
                        "for arg in \"$@\"; do\n"
                        "  case \"$arg\" in -c|-E|-S|-M|-MM|-fsyntax-only|--version|-dumpmachine) link=0 ;; esac\n"
                        "done\n"
                        "if [[ \"$link\" == 1 ]]; then\n  exec " + shlex.join(common) +
                        ' "$@" ' + shlex.join(link) + "\nfi\nexec " + shlex.join(common) +
                        ' "$@" --target=' + triple + "\n")
    compiler.chmod(0o755)
    config = (f'using clang : 18 : "{compiler}" : <archiver>"{llvm}/llvm-ar" '
              f'<ranlib>"{llvm}/llvm-ranlib" ;\n'
              f'using python : 3.11 : "{prefix}/bin/python3.11" : "{prefix}/include/python3.11" '
              f': "{prefix}/lib" ;\n')
    (run / "user-config.jam").write_text(config)
    records = {str(path): digest(path) for path in (
        llvm / "clang++", llvm / "llvm-ar", llvm / "llvm-ranlib", llvm / "ld.lld",
        lib / "libc++.a", lib / "libc++abi.a")}
    write_json(run / "toolchain.sha256.json", records)
    write_json(run / "toolchain.json", {"sysroot": str(sysroot), "triple": triple, "libcxx": str(lib)})
    run_command([compiler, "--version"])
    run_command([compiler, "-dumpmachine"])
    print(config)


def configure(run):
    # bootstrap.sh writes a default project-config that would select another clang.
    path = run / "build-source/project-config.jam"
    require(path.is_file(), "bootstrap did not create project-config.jam")
    before = path.read_text()
    after = "# Configuration is exclusively provided by the retained user-config.jam.\n"
    (run / "bootstrap-project-config.jam").write_text(before)
    path.write_text(after)
    (run / "bootstrap-config.patch").write_text("".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True), fromfile="a/project-config.jam", tofile="b/project-config.jam")))
    write_json(run / "build-source.before-build.json", snapshot(run / "build-source"))


def elf(path, types=(3,)):
    with path.open("rb") as stream:
        header = stream.read(20)
    require(header[:6] == b"\x7fELF\x02\x01" and int.from_bytes(header[18:20], "little") == 183
            and int.from_bytes(header[16:18], "little") in types, f"not AArch64 ELF64 {types}: {path}")
    return {"path": str(path), "sha256": digest(path)}


def check_libraries(run):
    llvm = Path(os.environ["CARLA_LLVM_BIN"])
    prefix = run / "install"
    records = {}
    for name in LIBRARIES:
        stem = "libboost_" + name + "-mt-a64"
        archive = prefix / "lib" / (stem + ".a")
        shared = prefix / "lib" / (stem + ".so.1.82.0")
        require(archive.is_file() and shared.is_file(), f"missing actual b2 output: {stem}")
        members = subprocess.check_output([str(llvm / "llvm-ar"), "t", str(archive)], text=True)
        text = subprocess.check_output([str(llvm / "llvm-readelf"), "-h", str(archive)], text=True)
        (run / (stem + ".readelf.txt")).write_text(text)
        (run / (stem + ".members.txt")).write_text(members)
        count = len(members.splitlines())
        require(count > 0, f"empty archive: {archive}")
        for field, wanted in (("Class", "ELF64"), ("Machine", "AArch64"), ("Type", "REL")):
            values = re.findall(r"^\s*" + field + r":\s+(\S+)", text, re.M)
            require(len(values) == count and all(v == wanted for v in values), f"archive {field}: {archive}")
        dynamic = subprocess.check_output([str(llvm / "llvm-readelf"), "-d", str(shared)], text=True)
        (run / (stem + ".dynamic.txt")).write_text(dynamic)
        require("TEXTREL" not in dynamic and "libstdc++" not in dynamic, f"invalid runtime: {shared}")
        require(stem + ".so.1.82.0" in dynamic, f"unexpected SONAME: {shared}")
        records[name] = {"static": str(archive), "static_sha256": digest(archive),
                         "members": count, "shared": elf(shared)}
        print(f"PASS {stem}: {count} AArch64 static PIC candidates + shared SONAME")
    elf(run / "build-source/b2", (2, 3))
    write_json(run / "libraries.json", records)


def runtime(run):
    prefix = run / "install"
    python = Path((run / "python-prefix.txt").read_text().strip())
    llvm = Path(os.environ["CARLA_LLVM_BIN"])
    paths = [prefix / "bin/boost-smoke-static", prefix / "bin/boost-smoke-shared",
             prefix / "lib/libboost-pic-proof.so",
             prefix / "lib/boost-python-static/_boost_stage.so",
             prefix / "lib/boost-python-shared/_boost_stage.so"]
    for path in paths:
        elf(path)
        header = subprocess.check_output([str(llvm / "llvm-readelf"), "-h", "-d", str(path)], text=True)
        log_name = path.parent.name + "-" + path.name
        (run / (log_name + ".readelf.txt")).write_text(header)
        require("TEXTREL" not in header and "libstdc++" not in header, f"unexpected linkage: {path}")
        result = subprocess.run(["ldd", "-r", str(path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (run / (log_name + ".ldd.txt")).write_text(result.stdout)
        require(result.returncode == 0 and not any(s in result.stdout for s in
                ("not found", "undefined symbol", "libstdc++")), f"ldd -r failed: {path}")
        if path.name == "_boost_stage.so":
            require(str(python / "lib/libpython3.11.so") in result.stdout, "extension loaded wrong libpython")
            if path.parent.name == "boost-python-shared":
                require(str(prefix / "lib/libboost_python311") in result.stdout, "wrong Boost.Python loaded")
    for kind in ("static", "shared"):
        run_command([prefix / ("bin/boost-smoke-" + kind), run / ("smoke-" + kind + ".txt")])
        command = [python / "bin/python3.11", "-I", "-B", "-c", """
import importlib.util, pathlib, sys
assert sys.version_info[:3] == (3,11,8)
spec = importlib.util.spec_from_file_location("_boost_stage", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.version() == 108200
assert m.add(19,23) == 42
assert m.greet("ARM64") == "hello ARM64"
assert m.Counter(4).increment(3) == 7
try:
    m.reject()
except ValueError as e:
    assert str(e) == "boost rejection"
else:
    raise AssertionError("C++ exception not translated")
try:
    m.add("bad", 2)
except TypeError:
    pass
else:
    raise AssertionError("wrong argument accepted")
print("Boost.Python 1.82.0 CPython 3.11.8 import/call/class/exception PASS", sys.argv[1])
""", prefix / ("lib/boost-python-" + kind) / "_boost_stage.so"]
        run_command(command)
    write_json(run / "consumer.json", {
        "boost_version": VERSION, "prefix": str(prefix), "include": str(prefix / "include"),
        "lib": str(prefix / "lib"), "layout": "tagged", "Boost_ARCHITECTURE": "-a64",
        "python_prefix": str(python), "Python3_EXECUTABLE": str(python / "bin/python3.11"),
        "Python3_INCLUDE_DIR": str(python / "include/python3.11"),
        "Python3_LIBRARY": str(python / "lib/libpython3.11.so.1.0"),
        "libraries": json.loads((run / "libraries.json").read_text()),
        "excluded": ["iostreams zlib/bzip2/lzma/zstd filters", "NumPy", "OpenUSD", "UE Editor"],
    })


def retention(run):
    require(snapshot(run / "source") == json.loads((run / "pristine-source.sha256.json").read_text()),
            "pristine source changed")
    before = json.loads((run / "build-source.before-build.json").read_text())
    after = snapshot(run / "build-source")
    write_json(run / "build-source.after.json", after)
    diff = {"added": sorted(after.keys() - before.keys()), "removed": sorted(before.keys() - after.keys()),
            "changed": [p for p in sorted(before.keys() & after.keys()) if before[p] != after[p]]}
    write_json(run / "build-source-diff.json", diff)
    require(not diff["added"] and not diff["removed"] and not diff["changed"], "build source modified by b2")
    for manifest in ("toolchain.sha256.json", "inputs.sha256.json"):
        for path, expected in json.loads((run / manifest).read_text()).items():
            require(digest(path) == expected, f"input changed during build: {path}")
    write_json(run / "installed.sha256.json", {p.relative_to(run / "install").as_posix(): digest(p)
               for p in sorted((run / "install").rglob("*")) if p.is_file()})
    print("PASS: pristine/build-source and toolchain/recipe hashes unchanged")


def finalize(run, code, ue):
    stage = reporter()
    checks = {name: "PASS" if (run / "passed" / name).is_file() else "FAIL" for name in CHECKS}
    checks["worker-exit"] = "PASS" if code == 0 else "FAIL"
    pinfile = run / "python-pin.json"
    prerequisite = []
    if pinfile.is_file():
        pin = json.loads(pinfile.read_text())
        if digest(pin["report"]) != pin["sha256"]:
            checks["python-after"] = "FAIL"
        prerequisite = [{"stage_id": PYTHON_STAGE, "scope": PYTHON_SCOPE, "path": pin["report"]}]
    else:
        checks["python-before"] = "FAIL"
    evidence = {name: run / (name + ".log") if (run / (name + ".log")).is_file()
                else run / "worker.log" for name in CHECKS}
    evidence["worker-exit"] = run / "exit-code.txt"
    # The immutable source tar and manifests bind original contents; installed
    # headers/binaries/configs and recipe/logs are individually rehashed.
    for path in sorted(run.rglob("*")):
        relative = path.relative_to(run)
        if path.is_file() and relative.parts[0] not in ("passed", "work", "source", "build-source", "download"):
            evidence["retained." + relative.as_posix()] = path
    if (run / "build-source/b2").is_file():
        evidence["b2"] = run / "build-source/b2"
        if (run / "build-source/bootstrap.log").is_file():
            evidence["bootstrap-raw"] = run / "build-source/bootstrap.log"
    identity = run / "source-identity.json"
    commit = run / "ue-commit.txt"
    report = stage.write_report(run / "stage-report.json", stage_id=STAGE, scope=SCOPE, exit_code=code,
        required_checks=list(CHECKS), checks=checks, evidence=evidence, prerequisites=prerequisite,
        sources={"boost": {"location": str(run / "source"), "revision": digest(identity) if identity.is_file() else "unverified"},
                 "ue": {"location": str(ue), "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
        command=["bash", str(Path(__file__).with_name("build-arm64-boost.sh"))])
    write_json(run / "decision.json", {"status": report["status"], "checks": checks, "scope": SCOPE, "exit_code": code})
    if report["status"] == "PASS":
        stage.validate_report(run / "stage-report.json", stage_id=STAGE, scope=SCOPE)
    print(f"{report['status']} {STAGE} report={run / 'stage-report.json'}")
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("python-before", "python-after", "toolchain", "prepare", "configure",
                                         "elf", "runtime", "retention", "finalize"))
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--ue", type=Path, default=Path(os.environ.get("CARLA_UE_DIR", "/workspace/unreal-engine")))
    parser.add_argument("--exit-code", type=int, default=1)
    args = parser.parse_args()
    if args.action.startswith("python-"):
        python_gate(args.run, args.action.split("-")[1])
    elif args.action == "toolchain":
        toolchain(args.run, args.ue)
    elif args.action == "finalize":
        return finalize(args.run, args.exit_code, args.ue)
    else:
        {"prepare": prepare, "configure": configure, "elf": check_libraries,
         "runtime": runtime, "retention": retention}[args.action](args.run)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
