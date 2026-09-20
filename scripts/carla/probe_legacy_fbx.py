#!/usr/bin/env python3
"""Compile bounded real Editor consumers to inventory unresolved FBX SDK symbols.

This is a headers-only diagnostic, never an Editor link or FBX replacement gate.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time

from stage_report import write_report


SCOPE = "Legacy FBX shared hierarchy and five native objects; no Editor link or FBX parity"
UNITS = {
    "FbxStaticMeshImport.cpp": "Engine/Source/Editor/UnrealEd/Private/Fbx/FbxStaticMeshImport.cpp",
    "MovieSceneToolHelpers.cpp": "Engine/Source/Editor/MovieSceneTools/Private/MovieSceneToolHelpers.cpp",
    "FbxMainImport.cpp": "Engine/Source/Editor/UnrealEd/Private/Fbx/FbxMainImport.cpp",
    "FbxSceneImportFactory.cpp": "Engine/Source/Editor/UnrealEd/Private/Fbx/FbxSceneImportFactory.cpp",
    "ReimportFbxSceneFactory.cpp": "Engine/Source/Editor/UnrealEd/Private/Fbx/ReimportFbxSceneFactory.cpp",
}
REQUIRED = ("sdk", "graph", "actions", "scalar_math", "node_metadata", "compile", "symbols", "inputs_unchanged")
SCALAR_HEADERS = (
    "Engine/Source/Runtime/Core/Public/Math/UnrealMathFPU.h",
    "Engine/Source/Runtime/Experimental/ChaosCore/Public/Chaos/VectorUtility.h",
)
METADATA_HEADERS = (
    "Engine/Source/Editor/UnrealEd/Public/ImportUtils/SceneImportNodeInfo.h",
    "Engine/Source/Editor/UnrealEd/Public/FbxImporter.h",
    "Engine/Source/Editor/UnrealEd/Public/ImportUtils/SceneImportHierarchy.h",
)
SMOKES = (
    ("scalar_math", "scalar-math-smoke.cpp", "PASS scalar math checks=839"),
    ("node_metadata", "scene-node-info-smoke.cpp", "PASS SDK-free scene node metadata"),
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def positive(value):
    if not value.isascii() or not value.isdecimal() or int(value) <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return int(value)


def target_summary(data, project):
    if (data.get("Name") != "CarlaUnrealEditor" or data.get("Configuration") != "Development"
            or data.get("Platform") not in ("Linux", "LinuxArm64")
            or data.get("ProjectFile") != str(project)):
        raise ValueError("unexpected UBT target identity")
    modules = data["Modules"]
    selected = {name for binary in data["Binaries"] for name in binary["Modules"]}
    if not selected or selected - modules.keys():
        raise ValueError("missing selected module records")
    if not {"FBX", "UnrealEd", "MovieSceneTools"} <= selected:
        raise ValueError("required Legacy FBX consumers are not selected")
    fbx = modules["FBX"]
    if fbx["PublicLibraries"] or "FBXSDK_SHARED" not in fbx["PublicDefinitions"]:
        raise ValueError("expected headers-only FBX rules, not an SDK library")
    consumers = []
    for name in sorted(selected):
        module = modules[name]
        edges = [key for key in ("PublicDependencyModules", "PrivateDependencyModules")
                 if "FBX" in module[key]]
        if edges:
            consumers.append({"module": name, "rules": module["Rules"], "edges": edges})
    if not {"UnrealEd", "MovieSceneTools"} <= {item["module"] for item in consumers}:
        raise ValueError("required direct FBX build edges are absent")
    return {
        "kind": "evaluated_ubt_headers_only_target_not_default_editor_acceptance",
        "selected_module_count": len(selected), "direct_fbx_consumers": consumers,
        "editor_acceptance": "NOT_RUN", "scope": SCOPE,
    }


def response_arguments(path, cwd, retained, ancestors=()):
    path = path.resolve()
    if path in ancestors or len(ancestors) >= 16:
        raise ValueError("cyclic or excessive response-file nesting")
    retained.add(path)
    result = []
    for argument in shlex.split(path.read_text()):
        if argument.startswith("@"):
            result.extend(response_arguments(cwd / argument[1:], cwd, retained, (*ancestors, path)))
        else:
            result.append(argument)
    return result


def target_arguments(arguments):
    targets = []
    for index, argument in enumerate(arguments):
        if argument in ("-target", "--target"):
            if index + 1 == len(arguments) or arguments[index + 1].startswith("-"):
                raise ValueError("missing compiler target value")
            targets.append(arguments[index + 1])
        elif argument.startswith(("-target=", "--target=")):
            targets.append(argument.split("=", 1)[1])
    return targets


def compile_plan(data, ue):
    actions = data["Actions"]
    if len(actions) != len(UNITS):
        raise ValueError(f"expected exactly {len(UNITS)} bounded compile actions")
    units, retained = {}, set()
    for action in actions:
        name = action["StatusDescription"]
        if action["Type"] != "Compile" or name not in UNITS or name in units:
            raise ValueError("unexpected or duplicate compile action")
        cwd = Path(action["WorkingDirectory"])
        if cwd != ue / "Engine/Source":
            raise ValueError("unexpected compiler working directory")
        arguments = shlex.split(action["CommandArguments"])
        if len(arguments) != 1 or not arguments[0].startswith("@"):
            raise ValueError("expected a compiler response file")
        args = response_arguments(cwd / arguments[0][1:], cwd, retained)
        source = ue / UNITS[name]
        targets = target_arguments(args)
        if (str(source) not in args or "-c" not in args or "-fsyntax-only" in args
                or args.count("-o") != 1 or not targets
                or set(targets) != {"aarch64-unknown-linux-gnueabi"}):
            raise ValueError("action is not the requested native object compilation")
        if args.index("-o") + 1 == len(args):
            raise ValueError("missing compiler object output")
        output = (cwd / args[args.index("-o") + 1]).resolve()
        if (not output.is_relative_to(ue / "Engine/Intermediate")
                or output.name != name + ".o" or output.parent.name != "SingleFile"):
            raise ValueError("unexpected single-file object output")
        for index, argument in enumerate(args):
            if argument == "-include":
                if index + 1 == len(args):
                    raise ValueError("missing compiler include path")
                retained.add((cwd / args[index + 1]).resolve())
        units[name] = output
    return units, retained


def check_object(path, started_ns):
    if path.stat().st_mtime_ns < started_ns:
        raise ValueError(f"stale object: {path}")
    with path.open("rb") as stream:
        header = stream.read(20)
    if (len(header) != 20 or header[:6] != b"\x7fELF\x02\x01"
            or struct.unpack_from("<HH", header, 16) != (1, 183)):
        raise ValueError(f"not an AArch64 ELF relocatable object: {path}")


def check_shared_library(path):
    with path.open("rb") as stream:
        header = stream.read(20)
    if (len(header) != 20 or header[:6] != b"\x7fELF\x02\x01"
            or struct.unpack_from("<HH", header, 16) != (3, 183)):
        raise ValueError(f"not an AArch64 ELF shared library: {path}")


def smoke_compile_command(action, source, output):
    cwd = Path(action["WorkingDirectory"])
    response = shlex.split(action["CommandArguments"])[0][1:]
    args = response_arguments(cwd / response, cwd, set())
    filtered = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in ("-o", "-MF"):
            index += 2
            continue
        if (argument not in ("-MD", "-MMD") and not argument.startswith("-MF")
                and Path(argument).name not in UNITS):
            filtered.append(argument)
        index += 1
    return [action["CommandPath"], *filtered, str(source), "-o", str(output), "-O2",
            "-fsanitize=undefined", "-fno-sanitize-recover=undefined"]


def fbx_symbols(text):
    symbols = sorted({line[2:] for line in text.splitlines() if line.startswith("U ")
                      and "fbxsdk::" in line[2:]})
    if not symbols:
        raise ValueError("object has no observed unresolved fbxsdk symbols")
    return symbols


def classify_symbols(symbols):
    sdk_prefixes = ("fbxsdk::", "typeinfo for fbxsdk::", "typeinfo name for fbxsdk::", "vtable for fbxsdk::")
    return {
        "sdk": [symbol for symbol in symbols if symbol.startswith(sdk_prefixes)],
        "sdk_typed_ue": [symbol for symbol in symbols if not symbol.startswith(sdk_prefixes)],
    }


def probe(args):
    if not Path("/.dockerenv").exists() or platform.machine() != "aarch64":
        raise ValueError("run in the native ARM64 carla-build container")
    ue, carla = args.ue_root.resolve(), args.carla_root.resolve()
    args.sdk_root = args.sdk_root.resolve()
    project = carla / "Unreal/CarlaUnreal/CarlaUnreal.uproject"
    root = args.artifact_root.resolve()
    if root.is_relative_to(ue) or root.is_relative_to(carla):
        raise ValueError("artifact directory must be outside the source trees")
    root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="legacy-fbx-", dir=root))
    run.chmod(0o755)
    print(f"Legacy diagnostic artifacts={run}", flush=True)
    checks, evidence, sources, commands, inputs = {}, {}, {}, {}, {}
    scripts = Path(__file__).resolve().parent
    env = {**os.environ, "CARLA_ARM64_FBX_HEADERS_ONLY": "1", "CARLA_UE_DISABLE_USD": "0",
           "CARLA_INTERCHANGE_UFBX_STATIC": "0", "CARLA_USD_NATIVE_ROOT": str(args.sdk_root)}
    env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_PRELOAD", None)
    failure = None

    def execute(name, command, cwd=None):
        command = ["timeout", "--kill-after=10", str(args.timeout), *map(str, command)]
        commands[name] = {"argv": command, "cwd": str(cwd or Path.cwd())}
        save(run / "commands.json", commands)
        log = run / (name + ".log")
        evidence[name] = log
        evidence[name + "-log"] = log
        print(f"step={name}", flush=True)
        with log.open("w") as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=cwd)
        if result.returncode:
            raise RuntimeError(f"{name} exited {result.returncode}; see {log}")
        return log

    def retain(path):
        path = Path(path).resolve()
        if str(path) not in inputs:
            token = hashlib.sha256(str(path).encode()).hexdigest()[:20]
            copy = run / "inputs" / token / path.name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
            checksum = digest(copy)
            if checksum != digest(path):
                raise ValueError(f"input changed during capture: {path}")
            inputs[str(path)] = {"sha256": checksum, "snapshot": str(copy.relative_to(run))}
            evidence["input-" + token] = copy

    try:
        for name, path in (("ue", ue), ("carla", carla)):
            git = ["git", "-c", f"safe.directory={path}", "-C", str(path)]
            revision = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
            sources[name] = {"location": str(path), "revision": revision}
            patch = run / (name + "-tracked.patch")
            patch.write_bytes(subprocess.check_output([*git, "diff", "--binary", "HEAD"]))
            evidence[name + "-patch"] = patch
        for path in (Path(__file__), scripts / "stage_report.py",
                     scripts / "prepare_native_usd_sdk.py", args.sdk_root / "native-sdk.json",
                     *(scripts / "legacy-fbx" / name for _, name, _ in SMOKES),
                     scripts / "patches/ue-editor-scalar-math.patch",
                     scripts / "patches/legacy-fbx-node-metadata.patch", project,
                     *(ue / relative for relative in (*UNITS.values(), *SCALAR_HEADERS, *METADATA_HEADERS))):
            retain(path)
        patch = scripts / "patches/legacy-fbx-diagnostic.patch"
        retain(patch)
        git = ["git", "-c", f"safe.directory={ue}", "-C", str(ue)]
        execute("diagnostic-patch", [*git, "apply", "--reverse", "--check", patch])
        execute("scalar-patch", [*git, "apply", "--reverse", "--check",
                                 scripts / "patches/ue-editor-scalar-math.patch"])
        execute("metadata-patch", [*git, "apply", "--reverse", "--check",
                                   scripts / "patches/legacy-fbx-node-metadata.patch"])
        # Do not apply or reset source here. The retained patch is replayed explicitly.
        execute("sdk", [sys.executable, "-B", scripts / "prepare_native_usd_sdk.py", "verify",
                        "--ue-root", ue, "--sdk-root", args.sdk_root])
        checks["sdk"] = "PASS"
        base = ["bash", str(ue / "Engine/Build/BatchFiles/Linux/Build.sh"),
                "CarlaUnrealEditor", "Linux", "Development", "-architecture=arm64",
                f"-project={project}"]
        execute("graph", [*base, "-Mode=JsonExport", f"-OutputFile={run}/target.json"])
        graph = load(run / "target.json")
        summary = target_summary(graph, project)
        save(run / "target-summary.json", summary)
        evidence["target"] = run / "target.json"
        evidence["graph"] = run / "target-summary.json"
        checks["graph"] = "PASS"
        core_paths = [Path(binary["File"]) for binary in graph["Binaries"]
                      if "Core" in binary["Modules"] and binary["Type"] == "DynamicLinkLibrary"]
        if len(core_paths) != 1:
            raise ValueError("expected one selected Core support library")
        core = core_paths[0]
        support = []
        for name in (core.name, "libUnrealEditor-BuildSettings.so", "libUnrealEditor-TraceLog.so"):
            path = core.parent / name
            check_shared_library(path)
            retain(path)
            support.append({"path": str(path), "sha256": digest(path)})
        save(run / "prebuilt-core.json", {
            "scope": "preexisting native Core support, not a source rebuild or Editor acceptance",
            "libraries": support,
        })
        evidence["prebuilt-core"] = run / "prebuilt-core.json"
        log = execute("core-linkage", ["ldd", "-r", core])
        if "not found" in log.read_text() or "undefined symbol" in log.read_text():
            raise ValueError("unresolved Core support dependencies")
        for module in graph["Modules"].values():
            retain(module["Rules"])
        sdk_headers = ue / "Engine/Source/ThirdParty/FBX/2020.2/include"
        for path in sorted(sdk_headers.rglob("*.h")):
            retain(path)
        compile_command = [*base, "-NoUBTMakefiles", "-NoHotReload", "-NoPCH", "-NoSharedPCH",
                           f"-MaxParallelActions={args.jobs}",
                           *(f"-SingleFile={ue / relative}" for relative in UNITS.values())]
        execute("actions", [*compile_command, f"-WriteOutdatedActions={run}/actions.json"])
        actions = load(run / "actions.json")
        units, response_files = compile_plan(actions, ue)
        for path in response_files:
            retain(path)
        evidence["actions"] = run / "actions.json"
        checks["actions"] = "PASS"
        save(run / "inputs.json", inputs)
        action = next(item for item in actions["Actions"] if item["StatusDescription"] == "FbxStaticMeshImport.cpp")
        for name, filename, expected in SMOKES:
            smoke_object, smoke_binary = run / (name + ".o"), run / name
            dependencies = run / (name + ".d")
            started_ns = time.time_ns()
            execute(name + "-compile", [*smoke_compile_command(action,
                    scripts / "legacy-fbx" / filename, smoke_object), "-MD", "-MF", dependencies],
                    cwd=Path(action["WorkingDirectory"]))
            check_object(smoke_object, started_ns)
            if name == "node_metadata" and "fbxsdk" in dependencies.read_text().lower():
                raise ValueError("scene metadata smoke unexpectedly includes the SDK")
            support_link = [str(core), f"-Wl,-rpath,{core.parent}"] if name == "node_metadata" else []
            execute(name + "-link", [action["CommandPath"], smoke_object, *support_link, "-o", smoke_binary,
                                    "-nostdlib++", "/usr/lib/llvm-18/lib/libc++.a",
                                    "/usr/lib/llvm-18/lib/libc++abi.a", "-lm", "-lpthread", "-ldl",
                                    "-fsanitize=undefined"])
            linkage = execute(name + "-linkage", ["ldd", "-r", smoke_binary]).read_text()
            if "not found" in linkage or "undefined symbol" in linkage or "libfbxsdk" in linkage:
                raise ValueError(f"invalid {name} runtime dependencies")
            log = execute(name, [smoke_binary])
            if log.read_text().strip() != expected:
                raise ValueError(f"{name} checks did not complete")
            evidence[name + "-object"] = smoke_object
            evidence[name + "-binary"] = smoke_binary
            evidence[name + "-dependencies"] = dependencies
            checks[name] = "PASS"
        started_ns = time.time_ns()
        execute("compile", compile_command)
        symbols = {}
        for name, path in sorted(units.items()):
            check_object(path, started_ns)
            copy = run / path.name
            shutil.copy2(path, copy)
            evidence[name + "-object"] = copy
            log = execute(name + "-symbols", ["llvm-nm-18", "--undefined-only", "--demangle",
                                              "--format=bsd", copy])
            normalized = "\n".join(line.strip() for line in log.read_text().splitlines())
            symbols[name] = classify_symbols(fbx_symbols(normalized))
        checks["compile"] = "PASS"
        save(run / "symbols.json", {"scope": SCOPE, "units": symbols,
             "unique_sdk_symbol_count": len({symbol for values in symbols.values() for symbol in values["sdk"]}),
             "unique_sdk_typed_ue_symbol_count": len({symbol for values in symbols.values() for symbol in values["sdk_typed_ue"]})})
        evidence["symbols"] = run / "symbols.json"
        checks["symbols"] = "PASS"
        for path, item in inputs.items():
            if digest(path) != item["sha256"] or digest(run / item["snapshot"]) != item["sha256"]:
                raise ValueError(f"input changed during diagnostic: {path}")
        execute("sdk-after", [sys.executable, "-B", scripts / "prepare_native_usd_sdk.py", "verify",
                              "--ue-root", ue, "--sdk-root", args.sdk_root])
        checks["inputs_unchanged"] = "PASS"
        evidence["inputs_unchanged"] = run / "inputs.json"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        failure = str(error)
        print(f"FAIL: {failure}", file=sys.stderr)
    save(run / "inputs.json", inputs)
    save(run / "result.json", {"scope": SCOPE, "editor_acceptance": "NOT_RUN", "failure": failure})
    evidence.update({"result": run / "result.json", "commands": run / "commands.json",
                     "inputs": run / "inputs.json"})
    report = write_report(run / "stage-report.json", stage_id="legacy-fbx-diagnostic", scope=SCOPE,
                          exit_code=1 if failure else 0, required_checks=list(REQUIRED), checks=checks,
                          evidence=evidence, sources=sources, command=[sys.executable, *sys.argv])
    print(f"{report['status']} diagnostic only; Editor NOT_RUN; artifacts={run}", flush=True)
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ue-root", type=Path, default=os.getenv("CARLA_UE_DIR", "/workspace/unreal-engine"))
    parser.add_argument("--carla-root", type=Path, default=os.getenv("CARLA_SOURCE_DIR", "/workspace/carla"))
    parser.add_argument("--artifact-root", type=Path, default=os.getenv("CARLA_ARTIFACT_DIR", "/artifacts/carla"))
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--jobs", type=positive, default="4")
    parser.add_argument("--timeout", type=positive, default="600")
    args = parser.parse_args()
    try:
        return probe(args)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
