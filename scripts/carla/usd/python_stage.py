#!/usr/bin/env python3
"""Private CPython stage helper: source identity, bounded build, runtime evidence."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys


VERSION = "3.11.8"
ARCHIVE_SHA256 = "9e06008c8901924395bc1da303eac567a729ae012baa182ab39269f650383bb3"
URL = f"https://www.python.org/ftp/python/{VERSION}/Python-{VERSION}.tar.xz"
STAGE = "carla-python-native-arm64"
SCOPE = ("Native ARM64 CPython 3.11.8 executable, shared library, static PIC archive, "
         "core stdlib, extension and embedding smoke ONLY; not full optional stdlib, "
         "pip, OpenUSD or UE Editor/Cook")
CHECKS = ("preflight", "source", "configure", "build", "install", "extension-compile",
          "embed-shared-compile", "embed-static-compile", "pic-link", "elf",
          "runtime", "embed-shared", "embed-static", "retention", "worker-exit")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_version(path):
    text = Path(path).read_text()
    values = []
    for name in ("MAJOR", "MINOR", "MICRO"):
        match = re.search(r"^#define\s+PY_" + name + r"_VERSION\s+(\d+)\s*$", text, re.M)
        require(match is not None, f"missing PY_{name}_VERSION: {path}")
        values.append(int(match[1]))
    version = ".".join(map(str, values))
    require(re.search(r'^#define\s+PY_VERSION\s+"' + re.escape(version) + r'"\s*$', text, re.M),
            "version string does not match numeric version")
    require(re.search(r"^#define\s+PY_RELEASE_LEVEL\s+PY_RELEASE_LEVEL_FINAL\s*$", text, re.M)
            and re.search(r"^#define\s+PY_RELEASE_SERIAL\s+0\s*$", text, re.M),
            "only final CPython release sources are supported")
    require(version == VERSION, f"expected pinned {VERSION}, observed {version}; no latest fallback")
    return version


def snapshot(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), f"source symlink is not allowed: {path}")
        if path.is_dir():
            continue
        require(path.is_file(), f"nonregular source: {path}")
        result[path.relative_to(root).as_posix()] = digest(path)
    require(result, f"empty source: {root}")
    return result


def constrain_makefile(before, jobs):
    require(1 <= jobs <= 4, "jobs must be 1..4")
    require("-j0" in before, "unexpected CPython compileall parallel rule")
    after = before.replace("-j0", f"-j{jobs}")
    for name in ("PYTHON_FOR_BUILD", "PYTHON_FOR_FREEZE", "PYTHON_FOR_REGEN"):
        token = f"@{name}@"
        require(after.count(token) == 1, f"unexpected CPython {name} rule")
        after = after.replace(token, token + " -B", 1)
    return after


def check_retention(run):
    differences = {}
    for folder, manifest in (("source", "pristine-source.sha256.json"),
                             ("build-source", "build-source.sha256.json")):
        before = json.loads((run / manifest).read_text())
        after = snapshot(run / folder)
        after_path = run / (folder + ".after.sha256.json")
        write_json(after_path, after)
        differences[folder] = {
            "before_manifest": manifest, "before_manifest_sha256": digest(run / manifest),
            "after_manifest": after_path.name, "after_manifest_sha256": digest(after_path),
            "added": {p: after[p] for p in sorted(after.keys() - before.keys())},
            "removed": {p: before[p] for p in sorted(before.keys() - after.keys())},
            "changed": {p: {"before": before[p], "after": after[p]}
                        for p in sorted(before.keys() & after.keys()) if before[p] != after[p]},
        }
    write_json(run / "source-retention-diff.json", differences)
    for folder, diff in differences.items():
        require(not any(diff[k] for k in ("added", "removed", "changed")),
                f"{folder} changed during build: " +
                "; ".join(f"{k}={list(diff[k])}" for k in ("added", "removed", "changed")))
    print("PASS pristine and patched source hashes unchanged; no generated-output exemptions")


def extract(archive, destination):
    import tarfile
    require(digest(archive) == ARCHIVE_SHA256, "official CPython archive SHA256 mismatch")
    require(not destination.exists(), "refusing to replace source")
    with tarfile.open(archive, "r:xz") as stream:
        members = stream.getmembers()
        for member in members:
            path = Path(member.name)
            require(not path.is_absolute() and ".." not in path.parts
                    and path.parts[0] == f"Python-{VERSION}",
                    f"unsafe archive path: {member.name}")
            require(member.isdir() or member.isfile(), f"nonregular archive entry: {member.name}")
        stream.extractall(destination, members=members)


def prepare(run, ue, jobs, local_source):
    import base64
    import difflib
    import shutil
    import subprocess
    header = ue / "Engine/Source/ThirdParty/Python3/Linux/include/patchlevel.h"
    read_version(header)
    identity = {"version": VERSION, "ue_patchlevel_sha256": digest(header)}
    if local_source:
        original = Path(local_source).resolve()
        require(original.is_dir(), f"missing requested CPython source: {original}")
        identity.update(origin="local-source", location=str(original))
    else:
        candidate = ue / f"Engine/Source/ThirdParty/Python3/Python-{VERSION}"
        if (candidate / "configure").is_file():
            original = candidate
            identity.update(origin="local-ue-source", location=str(original))
        else:
            archive = run / f"Python-{VERSION}.tar.xz"
            for url, output in ((URL, archive), (URL + ".sigstore", run / "source.sigstore.json")):
                command = ["curl", "--fail", "--location", "--show-error", "--retry", "2",
                           "--connect-timeout", "20", "--max-time", "180",
                           "--dump-header", str(output) + ".headers", "--output", str(output), url]
                print(json.dumps(command), flush=True)
                subprocess.run(command, check=True)
            bundle = json.loads((run / "source.sigstore.json").read_text())
            message = bundle["messageSignature"]["messageDigest"]
            require(message["algorithm"] == "SHA2_256"
                    and base64.b64decode(message["digest"]).hex() == ARCHIVE_SHA256,
                    "official Sigstore digest differs from pinned archive hash")
            extract(archive, run / "download")
            original = run / "download" / f"Python-{VERSION}"
            identity.update(origin="python.org-source-release", url=URL,
                            sha256=ARCHIVE_SHA256, tag=f"v{VERSION}",
                            sigstore="digest compared only; signature verification not performed")
    read_version(original / "Include/patchlevel.h")
    for name in ("configure", "Python/ceval.c", "Objects/longobject.c", "Lib/os.py", "LICENSE"):
        require((original / name).is_file(), f"missing CPython source: {name}")
    for name in ("python", "libpython3.11.a", "libpython3.11.so", "Programs/python.o"):
        require(not (original / name).exists(), f"not a clean source directory: {name}")
    shutil.copytree(original, run / "source")
    pristine = snapshot(run / "source")
    write_json(run / "pristine-source.sha256.json", pristine)
    identity["source_manifest_sha256"] = digest(run / "pristine-source.sha256.json")
    write_json(run / "source-identity.json", identity)
    shutil.copytree(run / "source", run / "build-source")
    edits = []
    setup = run / "build-source/setup.py"
    before = setup.read_text()
    require(before.count("self.parallel = True") == 1, "unexpected CPython build_ext parallel rule")
    after = before.replace("self.parallel = True", f"self.parallel = {jobs}", 1)
    anchor = "        self.init_inc_lib_dirs()\n"
    require(after.count(anchor) == 1, "unexpected CPython extension discovery")
    # Keep native execution/import checks, but never search host distro include/lib directories.
    after = after.replace(anchor, anchor + '''
        root = os.environ["CARLA_PYTHON_SYSROOT"]
        self.compiler.include_dirs = [
            os.getcwd(), os.path.join(self.srcdir, "Include"),
            os.path.join(root, "usr/include")]
        self.compiler.library_dirs = [
            os.getcwd(), *[os.path.join(root, p) for p in
                          ("lib64", "usr/lib64", "lib", "usr/lib")]]
        self.inc_dirs = list(self.compiler.include_dirs)
        self.lib_dirs = list(self.compiler.library_dirs)
''', 1)
    edits.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                    fromfile="a/setup.py", tofile="b/setup.py"))
    setup.write_text(after)
    makefile = run / "build-source/Makefile.pre.in"
    before = makefile.read_text()
    after = constrain_makefile(before, jobs)
    edits.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                    fromfile="a/Makefile.pre.in", tofile="b/Makefile.pre.in"))
    makefile.write_text(after)
    (run / "build-constraints.patch").write_text("".join(edits))
    write_json(run / "build-source.sha256.json", snapshot(run / "build-source"))
    print(json.dumps(identity, indent=2))


def elf(path):
    with Path(path).open("rb") as stream:
        data = stream.read(20)
    require(len(data) == 20 and data[:6] == b"\x7fELF\x02\x01"
            and int.from_bytes(data[18:20], "little") == 183, f"not AArch64 ELF64: {path}")
    return {"path": str(path), "sha256": digest(path), "machine": "AArch64",
            "elf_type": int.from_bytes(data[16:18], "little")}


def check_elf(run):
    import subprocess
    prefix = run / "install"
    library = prefix / "lib/libpython3.11.a"
    tool = Path(os.environ["CARLA_LLVM_BIN"])
    members = subprocess.check_output([str(tool / "llvm-ar"), "t", str(library)], text=True)
    headers = subprocess.check_output([str(tool / "llvm-readelf"), "-h", str(library)], text=True)
    (run / "archive-members.txt").write_text(members)
    (run / "archive-readelf.txt").write_text(headers)
    count = len(members.splitlines())
    require(count > 100, "unexpectedly small libpython archive")
    for field, wanted in (("Class", "ELF64"), ("Type", "REL"), ("Machine", "AArch64")):
        values = re.findall(r"^\s*" + field + r":\s+(\S+)", headers, re.M)
        require(len(values) == count and all(v == wanted for v in values), f"archive {field} mismatch")
    paths = [prefix / "bin/python3.11", prefix / "lib/libpython3.11.so.1.0",
             prefix / "bin/python-embed-shared", prefix / "bin/python-embed-static",
             prefix / "lib/libpython-pic-proof.so", prefix / "lib/python-smoke/_ue_native.so"]
    paths.extend(sorted((prefix / "lib/python3.11/lib-dynload").glob("*.so")))
    records = []
    for index, path in enumerate(paths):
        records.append(elf(path))
        result = subprocess.check_output([str(tool / "llvm-readelf"), "-h", "-d", str(path)], text=True)
        (run / f"elf-{index}.txt").write_text(result)
        require("TEXTREL" not in result and "libstdc++" not in result, f"unexpected linkage: {path}")
    for name in ("python3.11", "python-embed-shared", "python-embed-static"):
        result = subprocess.run(["ldd", "-r", str(prefix / "bin" / name)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        (run / f"{name}.ldd.txt").write_text(result.stdout)
        require(result.returncode == 0 and "not found" not in result.stdout
                and "undefined symbol" not in result.stdout, f"ldd failed: {name}")
        if name == "python-embed-static":
            require("libpython3.11.so" not in result.stdout, "static embed silently uses shared Python")
        else:
            require(str(prefix / "lib/libpython3.11.so") in result.stdout,
                    f"not using stage libpython: {name}")
    write_json(run / "elf.json", {"archive_members": count, "files": records})
    print(f"PASS {count} static PIC candidate members; {len(records)} AArch64 ELF64 binaries/extensions")


def runtime_smoke(run):
    import importlib
    import importlib.util
    import math
    import platform
    import subprocess
    import xml.etree.ElementTree as ET
    prefix = (run / "install").resolve()
    require(sys.version_info[:3] == (3, 11, 8), "wrong runtime version")
    require(platform.machine() == "aarch64" and struct.calcsize("P") == 8, "wrong runtime ABI")
    require(Path(sys.executable).resolve() == prefix / "bin/python3.11"
            and Path(sys.prefix).resolve() == prefix, "wrong interpreter/prefix")
    require(json.loads(json.dumps({"answer": 42}))["answer"] == 42, "json")
    require(math.isclose(math.sqrt(81), 9), "math")
    require(struct.unpack("<Q", struct.pack("<Q", 0x123456789))[0] == 0x123456789, "struct")
    require(hashlib.sha256(b"abc").hexdigest() ==
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "hashlib")
    require(ET.fromstring("<root><n>7</n></root>").find("n").text == "7", "XML")
    text = "native CPython \u4e2d\u6587"
    (run / "unicode.txt").write_text(text)
    require((run / "unicode.txt").read_text() == text, "unicode filesystem")
    modules = {}
    for name in ("math", "_struct", "_socket", "_decimal", "_json", "pyexpat", "_posixsubprocess"):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        require(path.is_relative_to(prefix), f"host extension leaked: {name}: {path}")
        modules[name] = elf(path)
    extension = prefix / "lib/python-smoke/_ue_native.so"
    spec = importlib.util.spec_from_file_location("_ue_native", extension)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.answer() == (42, 8, 31108), "native C extension API result")
    child = subprocess.check_output([sys.executable, "-I", "-B", "-c", "print(6*7)"], text=True)
    require(child.strip() == "42", "subprocess stdlib")
    optional = {}
    for name in ("ssl", "_ctypes", "sqlite3", "zlib", "bz2", "lzma", "readline", "_tkinter"):
        try:
            module = importlib.import_module(name)
            optional[name] = {"available": True, "path": getattr(module, "__file__", None)}
        except ImportError as error:
            optional[name] = {"available": False, "reason": str(error)}
    data = {"version": platform.python_version(), "executable": sys.executable,
            "prefix": sys.prefix, "machine": platform.machine(), "pointer_bytes": 8,
            "stdlib": ["json", "math", "struct", "hashlib", "XML", "unicode", "subprocess"],
            "extensions": modules, "c_api_extension": elf(extension), "optional": optional}
    write_json(run / "runtime.json", data)
    print(json.dumps(data, indent=2))
    print("CPython 3.11.8 native stdlib/dynamic-extension smoke PASS")


def finalize(run, code, reporter, ue, recipe):
    import importlib.util
    spec = importlib.util.spec_from_file_location("python_stage_report", reporter)
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    checks = {n: "PASS" if (run / "passed" / n).is_file() else "FAIL" for n in CHECKS}
    checks["worker-exit"] = "PASS" if code == 0 else "FAIL"
    evidence = {n: run / (n + ".log") if (run / (n + ".log")).is_file()
                else run / "worker.log" for n in CHECKS}
    evidence["worker-exit"] = run / "exit-code.txt"
    for path in sorted(run.rglob("*")):
        relative = path.relative_to(run)
        if path.is_file() and relative.parts[0] not in ("passed", "work", "download"):
            evidence["retained." + relative.as_posix()] = path
    for name in ("Makefile", "config.log", "config.status", "pyconfig.h"):
        path = run / "work" / name
        if path.is_file():
            evidence["configure." + name] = path
    identity = run / "source-identity.json"
    report = stage.write_report(
        run / "stage-report.json", stage_id=STAGE, scope=SCOPE, exit_code=code,
        required_checks=list(CHECKS), checks=checks, evidence=evidence,
        sources={"cpython": {"location": str(run / "source"),
                             "revision": digest(identity) if identity.is_file() else "unverified"},
                 "ue": {"location": str(ue), "revision": (run / "ue-commit.txt").read_text().strip()
                        if (run / "ue-commit.txt").is_file() else "unverified"}},
        command=["bash", str(recipe)])
    write_json(run / "decision.json", {"status": report["status"], "exit_code": code,
                                     "scope": SCOPE, "checks": checks})
    if report["status"] == "PASS":
        stage.validate_report(run / "stage-report.json", stage_id=STAGE, scope=SCOPE)
    print(f"{report['status']} {STAGE} report={run / 'stage-report.json'} exit={code}")
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("version", "prepare", "elf", "runtime", "retention", "finalize"))
    parser.add_argument("--run", type=Path)
    parser.add_argument("--ue", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--local-source", default="")
    parser.add_argument("--exit-code", type=int, default=1)
    parser.add_argument("--reporter", type=Path)
    parser.add_argument("--recipe", type=Path)
    args = parser.parse_args()
    if args.action == "version":
        print(read_version(args.ue / "Engine/Source/ThirdParty/Python3/Linux/include/patchlevel.h"))
    elif args.action == "prepare":
        require(1 <= args.jobs <= 4, "jobs must be 1..4")
        prepare(args.run, args.ue, args.jobs, args.local_source)
    elif args.action == "elf":
        check_elf(args.run)
    elif args.action == "runtime":
        runtime_smoke(args.run)
    elif args.action == "retention":
        check_retention(args.run)
    else:
        return finalize(args.run, args.exit_code, args.reporter, args.ue, args.recipe)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
