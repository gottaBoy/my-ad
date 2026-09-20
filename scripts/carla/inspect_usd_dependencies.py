#!/usr/bin/env python3
"""Read-only USD file/rule inventory; never executes UE build scripts or binaries."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import tempfile


TARGET = "aarch64-unknown-linux-gnueabi"
STAGE = "usd-native-dependency-inventory"
SCOPE = "Read-only Linux ARM64 USD dependency availability and architecture; not UBT, ABI, build or runtime validation"
USD = "Engine/Plugins/Runtime/USDCore"
BUILD = f"{USD}/Source/ThirdParty/USD/BuildForLinux.sh"
TPS = f"{USD}/Source/ThirdParty/USD/OpenUSD.tps"
WRAPPER = f"{USD}/Source/UnrealUSDWrapper/UnrealUSDWrapper.Build.cs"
TP = "Engine/Source/ThirdParty"
RULES = {
    "build": BUILD, "tps": TPS, "wrapper": WRAPPER,
    "TBB": f"{TP}/Intel/TBB/IntelTBB.Build.cs",
    "Boost": f"{TP}/Boost/Boost.Build.cs",
    "Python": f"{TP}/Python3/Python3.Build.cs",
    "Imath": f"{TP}/Imath/Imath.Build.cs",
    "OpenSubdiv": f"{TP}/OpenSubdiv/OpenSubdiv.Build.cs",
    "Alembic": f"{TP}/Alembic/AlembicLib.Build.cs",
    "MaterialX": f"{TP}/MaterialX/MaterialX.Build.cs",
}
VERSIONS = {
    "OpenUSD": "24.05", "TBB": "2019u8", "Boost": "1.82.0", "Python": "3.11",
    "Imath": "3.1.9", "OpenSubdiv": "3.6.0", "Alembic": "1.8.6", "MaterialX": "1.38.5",
}
VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z_0-9]*)\}|([A-Za-z_][A-Za-z_0-9]*))")
CS_COMMENTS = re.compile(r'("(?:\\.|[^"\\])*")|(//[^\n]*|/\*[\s\S]*?\*/)')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest_file(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def cs_code(text):
    return CS_COMMENTS.sub(
        lambda match: match.group(1) if match.group(1) is not None
        else "".join("\n" if c == "\n" else " " for c in match.group()), text)


def shell_assignments(text, context):
    """Expand only literal assignments and known variables, never source/eval."""
    values, expressions = dict(context), {}
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^([A-Z][A-Z_0-9]*)=(.*)$", line)
        if not match:
            continue
        name, expression = match.groups()
        expressions[name] = {"line": number, "source": line, "expression": expression}
        if name in context:
            continue
        if "`" in expression or "$(" in expression:
            continue
        parts = shlex.split(expression, comments=True)
        if len(parts) != 1:
            continue
        unresolved = False

        def expand(variable):
            nonlocal unresolved
            key = variable.group(1) or variable.group(2)
            if key not in values:
                unresolved = True
            return values.get(key, variable.group())

        value = VARIABLE.sub(expand, parts[0])
        if not unresolved and "$" not in value:
            values[name] = value
    return values, expressions


def elf_info(data):
    require(len(data) >= 20 and bytes(data[:4]) == b"\x7fELF", "not an ELF header")
    require(data[4] in (1, 2) and data[5] in (1, 2), "invalid ELF encoding")
    require(len(data) >= (64 if data[4] == 2 else 52), "truncated ELF header")
    endian = "little" if data[5] == 1 else "big"
    machine = int.from_bytes(data[18:20], endian)
    return {
        "format": "ELF", "bits": 64 if data[4] == 2 else 32, "endian": endian,
        "machine": machine,
        "architecture": {183: "AArch64", 62: "x86_64", 3: "x86", 40: "ARM"}.get(machine, f"EM_{machine}"),
        "elf_type": int.from_bytes(data[16:18], endian),
    }


def archive_info(data):
    require(data.startswith(b"!<arch>\n"), "thin/unknown archives are not accepted")
    offset, names, members = 8, b"", []
    while offset < len(data):
        header = data[offset:offset+60]
        require(len(header) == 60 and header[58:] == b"`\n", "invalid archive member header")
        length = int(header[48:58].decode("ascii").strip())
        require(length >= 0 and offset + 60 + length <= len(data), "truncated archive member")
        name = header[:16].decode("ascii").strip()
        body = memoryview(data)[offset+60:offset+60+length]
        offset += 60 + length + length % 2
        require(offset <= len(data), "missing archive padding")
        if name == "//":
            names = bytes(body)
            continue
        if name in ("/", "/SYM64/"):
            continue
        if name.startswith("#1/"):
            count = int(name[3:])
            require(0 <= count <= len(body), "invalid BSD member name")
            name, body = bytes(body[:count]).rstrip(b"\0").decode("utf-8"), body[count:]
        elif name.startswith("/") and name[1:].isdigit():
            start = int(name[1:])
            end = names.find(b"/\n", start)
            require(0 <= start < len(names) and end >= start, "invalid archive long name")
            name = names[start:end].decode("utf-8")
        else:
            name = name.rstrip("/")
        if name.startswith("__.SYMDEF"):
            continue
        try:
            info = elf_info(body)
        except ValueError as error:
            info = {"format": "unknown", "architecture": "unknown", "error": str(error)}
        members.append({"name": name, **info})
    require(members, "empty archive")
    return {"format": "AR", "member_count": len(members), "members": members,
            "architectures": dict(sorted(Counter(item["architecture"] for item in members).items()))}


def target_elf(info, types):
    return (info.get("architecture") == "AArch64" and info.get("bits") == 64
            and info.get("endian") == "little" and info.get("elf_type") in types)
 
COMPONENT_ORDER = ("native-toolchain", "TBB", "Boost.Python", "Imath", "Alembic",
                     "OpenSubdiv", "MaterialX", "OpenUSD", "Python", "resources")
COMPONENT_DEPENDENCIES = {
    "native-toolchain": (), "TBB": ("native-toolchain",),
    "Boost.Python": ("native-toolchain",), "Imath": ("native-toolchain",),
    "Alembic": ("native-toolchain", "Imath"), "OpenSubdiv": ("native-toolchain",),
    "MaterialX": ("native-toolchain",), "OpenUSD": ("native-toolchain", "TBB",
                "Boost.Python", "Imath", "Alembic", "OpenSubdiv", "MaterialX", "Python"),
    "Python": ("native-toolchain",), "resources": ("OpenUSD",),
}


class Inspector:
    def __init__(self, root, tool_path=None):
        self.root = Path(root).resolve()
        self.tool_path = os.environ.get("PATH", "") if tool_path is None else tool_path
        self.cache = {}

    def display(self, path):
        path = Path(os.path.abspath(path))
        return path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else str(path)

    def file(self, path, kind, ancestors=()):
        path = Path(os.path.abspath(path))
        key = (str(path), kind)
        if key in self.cache:
            return self.cache[key]
        result = {"path": self.display(path), "resolved_path": None, "sha256": None, "kind": kind,
                  "status": "BLOCKED", "reason": "missing"}
        try:
            resolved = path.resolve()
            require(resolved not in ancestors, "recursive file reference")
            result["resolved_path"] = self.display(resolved)
            if path.is_symlink():
                result["symlink_target"] = os.readlink(path)
            require(path.is_file(), "missing or non-regular file")
            result["size_bytes"] = path.stat().st_size
            require(result["size_bytes"] > 0, "empty file")
            result["sha256"] = digest_file(path)
            with path.open("rb") as stream:
                prefix = stream.read(4096)
            if kind in ("header", "config", "resource", "source", "patch"):
                require(b"\0" not in prefix, "expected a text input")
                result.update(format="text", architecture="not_applicable", status="PASS", reason="present_only")
            elif prefix.startswith(b"!<arch>\n"):
                info = archive_info(path.read_bytes())
                result.update(info)
                require(kind == "static_library", "archive supplied where a shared library/tool is required")
                require(all(target_elf(member, (1,)) for member in info["members"]), "non-AArch64 or unverified archive members")
                result.update(status="PASS", reason="all_members_AArch64_ELF64_REL")
            elif prefix.startswith(b"\x7fELF"):
                info = elf_info(prefix)
                result.update(info)
                types = (2, 3) if kind == "tool" else (3,)
                require(kind != "static_library" and target_elf(info, types), "wrong ELF architecture/class/type")
                if kind == "tool":
                    require(os.access(path, os.X_OK), "tool is not executable")
                result.update(status="PASS", reason="AArch64_ELF64")
            elif kind == "shared_library":
                text = re.sub(r"/\*[\s\S]*?\*/", "", prefix.decode("utf-8")).strip()
                match = re.fullmatch(r"(?:INPUT|GROUP)\s*\(([^()]*)\)\s*;?", text)
                require(match is not None and result["size_bytes"] <= len(prefix), "unverified linker script or unknown binary format")
                references = shlex.split(match.group(1))
                require(references and all(not name.startswith("-") for name in references), "unresolved linker search expression")
                result["format"] = "linker_script"
                result["references"] = [self.file(path.parent / name, "shared_library", (*ancestors, resolved))
                                        for name in references]
                require(all(item["status"] == "PASS" for item in result["references"]), "linker script has blocked dependency")
                result.update(status="PASS", reason="linker_script_targets_verified")
            elif kind == "tool" and prefix.startswith(b"#!"):
                command = shlex.split(prefix.splitlines()[0][2:].decode("utf-8"))
                require(command, "empty tool interpreter")
                executable = command[0]
                if Path(executable).name == "env":
                    require(len(command) == 2 and not command[1].startswith("-"), "unresolved env interpreter")
                    executable = shutil.which(command[1], path=self.tool_path)
                require(executable is not None and os.path.isabs(executable), "unresolved script interpreter")
                result["format"] = "script"
                result["interpreter"] = self.file(executable, "tool", (*ancestors, resolved))
                require(result["interpreter"]["status"] == "PASS" and os.access(path, os.X_OK), "blocked script interpreter")
                result.update(status="PASS", reason="interpreter_architecture_only")
            else:
                raise ValueError("unverified file format")
        except (OSError, ValueError, RuntimeError) as error:
            result.update(status="BLOCKED", reason=str(error))
        self.cache[key] = result
        return result

    def binary_tree(self, directory, component, evidence, profile="linux-arm64-selected"):
        """Inspect every library in a selected directory, including unexpected extras."""
        directory = Path(directory)
        if not directory.is_dir():
            return []
        entries = []
        for path in sorted(directory.rglob("*")):
            if not (path.is_file() or path.is_symlink()):
                continue
            if path.name.endswith(".a"):
                kind = "static_library"
            elif ".so" in path.name:
                kind = "shared_library"
            else:
                continue
            relative = path.relative_to(directory).as_posix()
            entries.append({
                "path": path,
                "kind": kind,
                "id": f"architecture-scan.{component}.{directory.name}.{relative}",
                "evidence": evidence,
                "profile": profile,
            })
        return entries


def csharp_array(code, name, reassignment=False):
    prefix = rf"\b{name}\s*=\s*new\s+string\[\]" if reassignment else rf"\bstring\[\]\s+{name}\s*="
    match = re.search(prefix + r"\s*\{([^}]+)\}", cs_code(code))
    require(match is not None, f"missing array rule: {name}")
    values = re.findall(r'"([^"]+)"', match.group(1))
    require(values and len(values) == len(set(values)), f"empty or ambiguous array rule: {name}")
    return values


def inspect_dependencies(root, tool_path=None):
    scan = Inspector(root, tool_path)
    sources, code, errors, entries, policies = {}, {}, [], [], []
    for name, relative in RULES.items():
        record = scan.file(scan.root / relative, "source")
        sources[name] = record
        if record["status"] == "PASS":
            code[name] = (scan.root / relative).read_text(encoding="utf-8-sig")
        else:
            errors.append(f"required rule unavailable: {relative}")

    def finish():
        entries.sort(key=lambda item: item["id"])
        checks = {item["id"]: item["status"] for item in entries if item["required"]}
        component_versions = {
            "native-toolchain": "UE clang/libc++ ARM64 toolchain",
            "TBB": VERSIONS["TBB"], "Boost.Python": VERSIONS["Boost"],
            "Imath": VERSIONS["Imath"], "Alembic": VERSIONS["Alembic"],
            "OpenSubdiv": VERSIONS["OpenSubdiv"], "MaterialX": VERSIONS["MaterialX"],
            "OpenUSD": VERSIONS["OpenUSD"], "Python": VERSIONS["Python"],
            "resources": "OpenUSD runtime resources",
        }
        def component_id(entry):
            identity = str(entry.get("id", ""))
            component = entry.get("component")
            if identity.startswith(("resources.", "plugin-library.")):
                return "resources"
            if component == "Boost":
                return "Boost.Python"
            if component in ("Clang", "UE-libcxx", "host-tools", "toolchain-runtime"):
                return "native-toolchain"
            return component if component in COMPONENT_ORDER else None
        components = []
        for name in COMPONENT_ORDER:
            required = [entry for entry in entries
                        if entry.get("required") is True and component_id(entry) == name]
            passed = sum(entry.get("status") == "PASS" for entry in required)
            blocked_count = len(required) - passed
            components.append({
                "id": name,
                "version": component_versions[name],
                "depends_on": list(COMPONENT_DEPENDENCIES[name]),
                "status": "PASS" if required and blocked_count == 0 else "BLOCKED",
                "required_entries": len(required),
                "pass": passed,
                "blocked": blocked_count,
            })
        blocked = bool(errors or any(item["status"] == "BLOCKED" for item in policies)
                       or not checks or any(value != "PASS" for value in checks.values()))
        fingerprint = hashlib.sha256(json.dumps(
            {"sources": sources, "entries": entries, "policies": policies, "errors": errors},
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {
            "schema_version": 1, "report_kind": "dependency_inventory_not_stage_report",
            "stage_id": STAGE, "scope": SCOPE, "status": "BLOCKED" if blocked else "PASS",
            "target": {"platform": "Linux", "architecture": TARGET, "configuration": "Development",
                       "native_host": platform.machine()},
            "versions_required": VERSIONS, "source_rules": sources, "requirements": entries,
            "components": components,
            "policies": policies, "errors": errors, "required_checks": sorted(checks), "checks": checks,
            "input_sha256": fingerprint,
            "limits": [
                "Bounded adapters for the inspected UE 5.5 rules, not a shell/C#/CMake interpreter or UBT graph.",
                "build-as-written paths retain x86 choices; arm64-candidate paths are mechanical substitutions, not applied fixes.",
                "Header entries are public/version canaries, not a transitive include closure.",
                "No candidate tools, libraries, build scripts, compiler, linker or package discovery were executed.",
                "File hashes and ELF/AR headers do not establish PIC, C++ ABI, GLIBC versions, link/load or Python import success.",
                "Tool version is the rule requirement or unspecified; executables are not run to query versions.",
                "USD missing-library names use the existing deployment as a diagnostic baseline, not a complete source build graph.",
            ],
        }

    if errors:
        return finish()

    def ref(name, needle, allow_comment=False):
        text = code[name] if name == "build" else cs_code(code[name])
        matches = [(number, line) for number, line in enumerate(text.splitlines(), 1)
                   if needle in line and (allow_comment or not line.lstrip().startswith("#"))]
        require(matches, f"rule changed or missing: {name}: {needle}")
        number = matches[0][0]
        return {"path": RULES[name], "line": number, "source": code[name].splitlines()[number-1]}

    def add(identity, component, kind, path, evidence, profile="editor-wrapper", required=True, note=""):
        observed = scan.file(path, kind)
        entry = {
            "id": identity, "component": component, "version_required": VERSIONS.get(component, "unspecified"),
            "profile": profile, "required": required, "rules": evidence, "note": note,
            "observed": observed, "status": observed["status"],
        }
        entries.append(entry)
        return entry

    def policy(identity, message, evidence):
        policies.append({"id": identity, "status": "BLOCKED", "reason": message, "rules": evidence})

    try:
        context = {"SCRIPT_DIR": str((scan.root / BUILD).parent), "UE_ENGINE_LOCATION": str(scan.root / "Engine")}
        original, declarations = shell_assignments(code["build"], context)
        candidate, _ = shell_assignments(code["build"], {**context, "ARCH_NAME": TARGET})
        require(original.get("OPENUSD_VERSION") == "24.05", "inventory adapter requires OpenUSD 24.05")
        require("v24.05" in code["tps"], "OpenUSD.tps does not pin the v24.05 source")
        require("ARCH_NAME" in original and "C_COMPILER" in original, "unresolved source build paths")
        if original["ARCH_NAME"] != TARGET:
            policy("build-architecture", "BuildForLinux.sh selects a non-ARM64 architecture",
                   [ref("build", "ARCH_NAME=")])
        if '-DBoost_ARCHITECTURE="-x64"' in code["build"]:
            policy("build-boost-architecture", "OpenUSD CMake arguments force Boost x64",
                   [ref("build", "-DBoost_ARCHITECTURE")])
        policy("build-script-safety", "Do not execute the upstream destructive/in-place deployment script; a bounded native rebuild wrapper is required",
               [ref("build", "rm -rf $BUILD_LOCATION"), ref("build", "-j$NUM_CPU"), ref("build", "TOOLCHAIN_NAME=")])
        if "Target.Platform == UnrealTargetPlatform.Linux" in cs_code(code["wrapper"]):
            policy("wrapper-linux-arm64-branch",
                   "UnrealUSDWrapper has a Linux-only USD branch; LinuxArm64 does not select its TBB, Python or USD libraries",
                   [ref("wrapper", "Target.Platform == UnrealTargetPlatform.Linux"),
                    ref("wrapper", "Target.Architecture.LinuxName")])
        if platform.machine() not in ("aarch64", "arm64"):
            policy("native-host", "Inventory is not running on the requested ARM64 host", [])
        policy("python-discovery", "Python3 automatic Unix discovery is X64-only; no ARM64 SDK selection has been established",
               [ref("Python", "Target.Architecture == UnrealArch.X64"), ref("Python", "UE_PYTHON_DIR")])
        policy("boost-platform-suffix", "Linux + ARM64 Editor keeps the x64 filename suffix; LinuxArm64 alone selects a64",
               [ref("Boost", 'BoostLibArchSuffix = "x64"'), ref("Boost", "Target.Platform == UnrealTargetPlatform.LinuxArm64")])

        def build_entry(component, kind, variable, suffix="", extra=(), identity=None):
            require(variable in original and variable in candidate, f"unresolved required assignment: {variable}")
            evidence = [ref("build", variable + "="), *extra]
            label = identity or f"{component}.{variable}.{suffix or kind}"
            for profile, values, required in (
                ("build-as-written", original, True), ("arm64-candidate", candidate, False),
            ):
                add(f"{profile}.{label}", component, kind, Path(values[variable]) / suffix, evidence, profile, required,
                    "ARM64 candidates are not selected by the unmodified build script" if not required else "")

        headers = (
            ("TBB", "TBB_INCLUDE_LOCATION", "tbb/tbb.h"),
            ("Boost", "BOOST_INCLUDE_LOCATION", "boost/version.hpp"),
            ("Imath", "IMATH_LOCATION", "include/Imath/ImathConfig.h"),
            ("OpenSubdiv", "OPENSUBDIV_INCLUDE_DIR", "opensubdiv/version.h"),
            ("Alembic", "ALEMBIC_INCLUDE_LOCATION", "Alembic/Abc/All.h"),
            ("MaterialX", "MATERIALX_LOCATION", "include/MaterialXCore/Generated.h"),
            ("Python", "PYTHON_INCLUDE_LOCATION", "Python.h"),
            ("Python", "PYTHON_INCLUDE_LOCATION", "patchlevel.h"),
            ("Python", "PYTHON_INCLUDE_LOCATION", "pyconfig.h"),
        )
        for component, variable, filename in headers:
            build_entry(component, "header", variable, filename)
        build_entry("OpenUSD", "source", "OPENUSD_SOURCE_LOCATION", "CMakeLists.txt")
        build_entry("Python", "tool", "PYTHON_EXECUTABLE_LOCATION")
        build_entry("Python", "static_library", "PYTHON_LIBRARY_LOCATION", extra=(ref("Python", '"libpython3.11.a"'),))
        for variable in ("C_COMPILER", "CXX_COMPILER"):
            build_entry("Clang", "tool", variable, extra=(ref("build", "TOOLCHAIN_NAME="),))
            for entry in entries[-2:]:
                entry["version_required"] = original["TOOLCHAIN_NAME"]
        for name in ("libc++.a", "libc++abi.a"):
            for profile, triple, required in (("build-as-written", original["ARCH_NAME"], True), ("arm64-candidate", TARGET, False)):
                add(f"{profile}.libcxx.{name}", "UE-libcxx", "static_library",
                    scan.root / TP / "Unix/LibCxx/lib/Unix" / triple / name,
                    [ref("build", "CXX_LINKER=")], profile, required)
        add("build.libcxx.header", "UE-libcxx", "header", scan.root / TP / "Unix/LibCxx/include/c++/v1/__config",
            [ref("build", "CXX_FLAGS=")], "build-as-written")
        for name in ("libtbb.a", "libtbbmalloc.a"):
            build_entry("TBB", "static_library", "TBB_LIB_LOCATION", name, (ref("TBB", '"' + name + '"'),))
        for component, variable, filename in (
            ("Imath", "IMATH_LIB_LOCATION", "lib/libImath-3_1.a"),
            ("OpenSubdiv", "OPENSUBDIV_LIB_LOCATION", "libosdCPU.a"),
            ("Alembic", "ALEMBIC_LIB_LOCATION", "lib/libAlembic.a"),
        ):
            build_entry(component, "static_library", variable, filename,
                        (ref(component, "Target.Architecture.LinuxName"),))
        for name in csharp_array(code["MaterialX"], "MaterialXLibraries", reassignment=True):
            build_entry("MaterialX", "static_library", "MATERIALX_LIB_LOCATION", f"lib{name}.a",
                        (ref("MaterialX", '"' + name + '"'),))
        for component, variable, filename in (
            ("Imath", "IMATH_CMAKE_LOCATION", "ImathConfig.cmake"),
            ("MaterialX", "MATERIALX_CMAKE_LOCATION", "MaterialXConfig.cmake"),
        ):
            build_entry(component, "config", variable, filename)
        for name in csharp_array(code["Boost"], "BoostLibraries"):
            for profile, values, suffix, required in (
                ("build-as-written", original, "x64", True),
                ("arm64-candidate", candidate, "a64", False),
                ("editor-modules", candidate, "x64", True),
            ):
                evidence = [ref("build", "BOOST_LIB_LOCATION="), ref("Boost", "BoostLibraries ="),
                            ref("Boost", 'BoostLibArchSuffix = "x64"')]
                path = Path(values["BOOST_LIB_LOCATION"]) / f"libboost_{name}-mt-{suffix}.a"
                add(f"{profile}.Boost.{name}", "Boost", "static_library", path, evidence, profile, required)
                shared = sorted(path.parent.glob(f"libboost_{name}-mt-{suffix}.so*"))
                if not shared:
                    shared = [path.with_suffix(".so")]
                for index, item in enumerate(shared):
                    add(f"{profile}.Boost.{name}.shared.{index}", "Boost", "shared_library", item,
                        [*evidence, ref("Boost", 'BoostLibName + ".so*"')], profile, required)

        for name in ("libtbb.so", "libtbb.so.2"):
            add(f"wrapper.TBB.{name}", "TBB", "shared_library",
                scan.root / "Engine/Binaries/ThirdParty/Intel/TBB/Linux" / name,
                [ref("wrapper", "string IntelTBBBinaries"), ref("wrapper", '"' + name + '"')])
        for filename, kind, rule in (
            ("bin/python3.11", "tool", "wrapper"),
            ("lib/libpython3.11.so.1.0", "shared_library", "Python"),
        ):
            add(f"wrapper.Python.{filename}", "Python", kind,
                scan.root / "Engine/Binaries/ThirdParty/Python3/Linux" / filename,
                [ref(rule, "python3.11")])
        for header in ("pxr/pxr.h", "pxr/usd/usd/stage.h"):
            add(f"wrapper.OpenUSD.{header}", "OpenUSD", "header",
                scan.root / USD / "Source/ThirdParty/USD/include" / header,
                [ref("wrapper", '"USD", "include"')])

        target_bin = scan.root / USD / "Source/ThirdParty/Linux/bin" / TARGET
        original_bin = scan.root / USD / "Source/ThirdParty/Linux/bin" / original["ARCH_NAME"]
        expected_names = {"libusd_tf.so", "libusd_usd.so", "libusd_sdf.so", "libusd_plug.so"}
        expected_names.update(path.name for path in original_bin.glob("*.so"))
        expected_names.update(path.name for path in target_bin.glob("*.so"))
        for name in sorted(expected_names):
            add(f"wrapper.OpenUSD.library.{name}", "OpenUSD", "shared_library", target_bin / name,
                [ref("wrapper", "var USDBinDir ="), ref("wrapper", 'Directory.EnumerateFiles(USDBinDir, "*.so"')],
                note="Name set includes the existing deployment baseline; not a source-level CMake graph")
            add(f"baseline.OpenUSD.library.{name}", "OpenUSD", "shared_library", original_bin / name,
                [ref("build", "USD_LIBS_LOCATION=")], "deployment-baseline", False)
        selected_binary_trees = (
            ("OpenUSD", target_bin, [ref("wrapper", "var USDBinDir =")]),
            ("TBB", Path(candidate["TBB_LIB_LOCATION"]), [ref("build", "TBB_LIB_LOCATION=")]),
            ("Boost", Path(candidate["BOOST_LIB_LOCATION"]), [ref("build", "BOOST_LIB_LOCATION=")]),
            ("Imath", Path(candidate["IMATH_LIB_LOCATION"]), [ref("build", "IMATH_LIB_LOCATION=")]),
            ("OpenSubdiv", Path(candidate["OPENSUBDIV_LIB_LOCATION"]), [ref("build", "OPENSUBDIV_LIB_LOCATION=")]),
            ("Alembic", Path(candidate["ALEMBIC_LIB_LOCATION"]), [ref("build", "ALEMBIC_LIB_LOCATION=")]),
            ("MaterialX", Path(candidate["MATERIALX_LIB_LOCATION"]), [ref("build", "MATERIALX_LIB_LOCATION=")]),
        )
        for component, directory, evidence in selected_binary_trees:
            for item in scan.binary_tree(directory, component, evidence):
                entry = add(item["id"], component, item["kind"], item["path"],
                            item["evidence"], item["profile"], True,
                            "Every selected LinuxArm64 library must be AArch64 ELF64")
                entry["selection"] = "LinuxArm64"
        python_root = scan.root / USD / "Content/Python/Lib/Linux/site-packages/pxr"
        extensions = sorted(python_root.rglob("*.so"))
        if not extensions:
            extensions = [python_root / "Usd/_usd.so"]
        for path in extensions:
            add("wrapper.OpenUSD.python." + path.relative_to(python_root).as_posix(),
                "OpenUSD", "shared_library", path,
                [ref("build", "USD_PYTHON_MODULE_LOCATION="), ref("build", "mv \"$INSTALL_LOCATION/lib/python/pxr\"")])

        resources = scan.root / USD / "Resources/UsdResources/Linux"
        configs = sorted(resources.rglob("plugInfo.json"))
        require(configs, "no USD plugInfo.json resources: empty resource selector is BLOCKED")
        for path in configs:
            relative = path.relative_to(resources).as_posix()
            is_template = "codegenTemplates" in relative
            resource_entry = add(f"resources.{relative}", "OpenUSD", "resource", path,
                [ref("wrapper", '"Resources", "UsdResources"')])
            if is_template:
                resource_entry["required"] = False
            raw = path.read_text(encoding="utf-8-sig")
            content = json.loads("\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#")))
            if is_template:
                continue
            for index, plugin in enumerate(content.get("Plugins", [])):
                library = plugin.get("LibraryPath")
                if not library:
                    continue
                require(isinstance(library, str) and "$" not in library and "@" not in library, "unresolved plugin LibraryPath")
                base = (path.parent / plugin.get("Root", ".")).resolve()
                evidence = [{"path": scan.display(path), "json_pointer": f"/Plugins/{index}/LibraryPath",
                             "source": library, "root": plugin.get("Root", ".")}]
                add(f"plugin-library.{relative}.{index}", "OpenUSD", "shared_library", base / library, evidence)
        patches = sorted((scan.root / BUILD).parent.glob("OpenUSD_v2405_*.patch"))
        require(patches, "no matching UE OpenUSD 24.05 patches")
        for path in patches:
            add("patch." + path.name, "OpenUSD", "patch", path,
                [ref("build", "git apply", allow_comment=True)], "rebuild-inputs")
        for name, needle in (
            ("bash", "#!/bin/bash"), ("cmake", 'cmake -G "Unix Makefiles"'), ("make", '"Unix Makefiles"'),
            ("python", "python -c"), ("patchelf", "patchelf --set-rpath"),
        ):
            path = shutil.which(name, path=scan.tool_path)
            entry = add(f"host-tool.{name}", "host-tools", "tool", path or scan.root / f"__missing_tool__/{name}",
                        [ref("build", needle, allow_comment=True)], "PATH-tools")
            entry["tool_name"] = name
        for library in ("m", "c", "gcc_s", "gcc", "util"):
            entries.append({
                "id": f"unresolved-system-library.{library}", "component": "toolchain-runtime", "version_required": "unspecified",
                "profile": "build-as-written", "required": True, "status": "BLOCKED",
                "rules": [ref("build", "CXX_LINKER=")],
                "observed": {"path": None, "sha256": None, "status": "BLOCKED", "reason": "implicit linker/sysroot search not executed"},
                "note": f"-l{library}: resolve with the selected native toolchain in a later link probe",
            })
        source_markers = {
            "TBB": ("Intel/TBB/IntelTBB-2019u8/Makefile", "Intel/TBB/IntelTBB-2019u8/src/Makefile"),
            "Imath": ("Imath/Imath-3.1.9/CMakeLists.txt",),
            "OpenSubdiv": ("OpenSubdiv/OpenSubdiv-3.6.0/CMakeLists.txt",),
            "Alembic": ("Alembic/alembic-1.8.6/CMakeLists.txt",),
            "MaterialX": ("MaterialX/MaterialX-1.38.5/CMakeLists.txt",),
            "Boost": ("Boost/boost_1_82_0/bootstrap.sh",),
            "Python": ("Python3/Python-3.11.8/configure",),
        }
        for component, paths in source_markers.items():
            for path in paths:
                add(f"local-source.{path}", component, "source", scan.root / TP / path,
                    [], "source-marker-candidates", False, "Marker existence does not certify complete source or applied patches")
    except (OSError, ValueError, KeyError, UnicodeError) as error:
        errors.append(str(error))
    return finish()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ue-root", type=Path, default=Path(__file__).resolve().parents[2] / "third_party/unreal-engine")
    parser.add_argument("--artifact-dir", type=Path, help="Create a unique output directory here; otherwise JSON goes to stdout")
    args = parser.parse_args(argv)
    report = inspect_dependencies(args.ue_root)
    if args.artifact_dir is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        parent = args.artifact_dir.resolve()
        require(not parent.is_relative_to(args.ue_root.resolve()), "refusing to write inventory into UE")
        parent.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="usd-inventory-", dir=parent))
        output.chmod(0o755)
        (output / "inventory.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        counts = Counter(item["status"] for item in report["requirements"] if item["required"])
        print(f"{report['status']} {STAGE} required={dict(sorted(counts.items()))} report={output / 'inventory.json'}")
        for error in report["errors"]:
            print(f"RULE ERROR: {error}")
    return 1 if report["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
