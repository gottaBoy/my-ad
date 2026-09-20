#!/usr/bin/env python3
"""Bind a verified native USD SDK to UE rules; explicit opt-in, no x86 overwrite."""
import argparse
import difflib
import json
from pathlib import Path
import platform
import tempfile

from stage_report import validate_report
from usd.openusd_stage import STAGE, SCOPE, read_dependencies, digest

TP = Path("Engine/Source/ThirdParty")
USD = Path("Engine/Plugins/Runtime/USDCore")
TAG = "CARLA_USD_NATIVE_ROOT"


def once(text, anchor, replacement):
    if replacement in text:
        return text
    if text.count(anchor) != 1:
        raise ValueError("ambiguous native SDK patch anchor: " + anchor[:100])
    return text.replace(anchor, replacement, 1)


def rule_prefix():
    return '''
        // Explicit validated SDK only; preserve all default platform layouts.
        string CarlaNativeRoot = System.Environment.GetEnvironmentVariable("CARLA_USD_NATIVE_ROOT");
        bool CarlaNativeArm64 = !string.IsNullOrEmpty(CarlaNativeRoot)
            && Target.Platform == UnrealTargetPlatform.Linux && Target.Architecture == UnrealArch.Arm64;
        if (CarlaNativeArm64 && (!Path.IsPathFullyQualified(CarlaNativeRoot)
            || !File.Exists(Path.Combine(CarlaNativeRoot, "native-sdk.json"))))
            throw new BuildException("Native USD SDK must be prepared from verified reports");
'''


def patches(ue):
    results = {}
    for path in (TP / "Boost/Boost.Build.cs", TP / "Intel/TBB/IntelTBB.Build.cs", TP / "Python3/Python3.Build.cs",
                 USD / "Source/UnrealUSDWrapper/UnrealUSDWrapper.Build.cs",
                 USD / "Source/UnrealUSDWrapper/Private/UnrealUSDWrapper.cpp"):
        full = ue / path
        if full.is_symlink():
            raise ValueError("engine source symlink: " + str(path))
        raw = full.read_bytes()
        text = raw.decode("utf-8")
        if path.name == "Boost.Build.cs":
            anchor = '\t\tType = ModuleType.External;'
            extra = rule_prefix() + '''        if (CarlaNativeArm64)
        {
            PublicSystemIncludePaths.Add(Path.Combine(CarlaNativeRoot, "boost", "include"));
            string Lib = Path.Combine(CarlaNativeRoot, "boost", "lib");
            PrivateRuntimeLibraryPaths.Add(Lib);
            foreach (string Name in new string[] { "atomic", "chrono", "filesystem", "iostreams", "program_options", "python311", "regex", "system", "thread" })
            {
                string FileName = "libboost_" + Name + "-mt-a64.so.1.82.0";
                string Library = Path.Combine(Lib, FileName);
                if (!File.Exists(Library)) throw new BuildException("Missing native Boost library: " + Library);
                PublicAdditionalLibraries.Add(Library);
                RuntimeDependencies.Add(Library);
            }
            return;
        }
'''
            text = once(text, anchor, anchor + "\n" + extra)
        elif path.name == "IntelTBB.Build.cs":
            anchor = '\t\tType = ModuleType.External;'
            extra = rule_prefix() + '''        if (CarlaNativeArm64)
        {
            if (Target.Configuration == UnrealTargetConfiguration.Debug && Target.bDebugBuildsActuallyUseDebugCRT)
                throw new BuildException("Native USD SDK has no Debug TBB runtime");
            PublicSystemIncludePaths.Add(Path.Combine(CarlaNativeRoot, "tbb-include"));
            PublicDefinitions.Add("TBB_USE_EXCEPTIONS=0");
            bUseRTTI = false;
            bEnableExceptions = false;
            string Lib = Path.Combine(CarlaNativeRoot, "tbb", "lib");
            PrivateRuntimeLibraryPaths.Add(Lib);
            // One shared TBB runtime for the engine and USD in this profile.
            foreach (string Name in new string[] { "libtbb.so.2", "libtbbmalloc.so.2" })
            {
                string Library = Path.Combine(Lib, Name);
                if (!File.Exists(Library)) throw new BuildException("Missing native TBB library: " + Library);
                PublicAdditionalLibraries.Add(Library);
                RuntimeDependencies.Add(Library);
            }
            return;
        }
'''
            text = once(text, anchor, anchor + "\n" + extra)
        elif path.name == "Python3.Build.cs":
            anchor = '\t\tPythonSDKPaths PythonSDK = null;'
            extra = rule_prefix() + '''        if (CarlaNativeArm64)
        {
            string PythonRoot = Path.Combine(CarlaNativeRoot, "python");
            string Include = Path.Combine(PythonRoot, "include", "python3.11");
            string Library = Path.Combine(PythonRoot, "lib", "libpython3.11.so.1.0");
            if (!File.Exists(Path.Combine(Include, "Python.h")) || !File.Exists(Library))
                throw new BuildException("Native Python headers/shared runtime are missing");
            PublicDefinitions.Add("WITH_PYTHON=1");
            PublicDefinitions.Add("UE_PYTHON_DIR=\\\"" + PythonRoot.Replace('\\\\', '/') + "\\\"");
            PublicSystemIncludePaths.Add(Include);
            PublicAdditionalLibraries.Add(Library);
            PrivateRuntimeLibraryPaths.Add(Path.Combine(PythonRoot, "lib"));
            PublicSystemLibraries.Add("util");
            foreach (string FileName in Directory.EnumerateFiles(PythonRoot, "*", SearchOption.AllDirectories))
                if (!FileName.EndsWith(".pyc")) RuntimeDependencies.Add(FileName);
            return;
        }
'''
            text = once(text, anchor, extra + anchor)
        elif path.name.endswith(".Build.cs"):
            anchor = '\t\t\t// Temporarily disabled runtime USD support until Mac and Linux dynamic linking issues are resolved'
            text = once(text, anchor, rule_prefix() + anchor)
            anchor = '\t\t\tif (EnableUsdSdk(Target) && (Target.Type == TargetType.Editor || Target.Platform == UnrealTargetPlatform.Win64))'
            branch = '''            if (CarlaNativeArm64 && EnableUsdSdk(Target) && Target.Type == TargetType.Editor)
            {
                PublicDependencyModuleNames.Add("Python3");
                PublicDefinitions.Add("USE_USD_SDK=1");
                PublicDefinitions.Add("ENABLE_USD_DEBUG_PATH=0");
                PublicDefinitions.Add("USD_USES_SYSTEM_MALLOC=0");
                PublicDefinitions.Add("_LIBCPP_TYPEINFO_COMPARISON_IMPLEMENTATION=2");
                PublicDefinitions.Add("CARLA_USD_NATIVE_ROOT=\\\"" + CarlaNativeRoot.Replace('\\\\', '/') + "\\\"");
                string Prefix = Path.Combine(CarlaNativeRoot, "openusd");
                PublicSystemIncludePaths.Add(Path.Combine(Prefix, "include"));
                string Lib = Path.Combine(Prefix, "lib");
                PrivateRuntimeLibraryPaths.Add(Lib);
                string[] Libraries = Directory.GetFiles(Lib, "libusd_*.so");
                if (Libraries.Length < 20) throw new BuildException("Native USD library set is incomplete");
                foreach (string Library in Libraries)
                {
                    PublicAdditionalLibraries.Add(Library);
                    RuntimeDependencies.Add(Library);
                }
                foreach (string FileName in Directory.EnumerateFiles(Prefix, "*", SearchOption.AllDirectories))
                    if (!FileName.EndsWith(".pyc")) RuntimeDependencies.Add(FileName);
            }
            else ''' + anchor.lstrip()
            text = once(text, anchor, branch)
        else:
            anchor = '\t\t// Have to do this in USDClasses as we need the Json module, which is RTTI == false'
            code = '''#ifdef CARLA_USD_NATIVE_ROOT
        // Preserve the verified prefix layout; do not rewrite its plugInfo.json.
        UsdPluginsPath = FPaths::Combine(TEXT(CARLA_USD_NATIVE_ROOT), TEXT("openusd/lib/usd"));
        MaterialXStdDataLibsPath = FPaths::Combine(TEXT(CARLA_USD_NATIVE_ROOT), TEXT("materialx/libraries"));
        FPlatformMisc::SetEnvironmentVar(TEXT("PXR_MTLX_STDLIB_SEARCH_PATHS"), *MaterialXStdDataLibsPath);
#else
'''
            end = '\t\tIUsdClassesModule::UpdatePlugInfoFiles(UsdPluginsPath, TargetDllFolder);'
            text = once(text, anchor, code + anchor)
            text = once(text, end, end + '\n#endif // CARLA_USD_NATIVE_ROOT')
            anchor = '\t\tPluginDirectories.Add(UsdPluginsPath);'
            text = once(text, anchor, anchor + '''
#ifdef CARLA_USD_NATIVE_ROOT
        PluginDirectories.Add(FPaths::Combine(TEXT(CARLA_USD_NATIVE_ROOT), TEXT("openusd/plugin/usd")));
#endif
''')
        results[path] = (raw, text.encode("utf-8"))
    return results


def prepare(ue, report_path, dependencies, artifact_root):
    ue, report_path, dependencies, artifact_root = (p.resolve() for p in (ue, report_path, dependencies, artifact_root))
    if artifact_root.is_relative_to(ue): raise ValueError("SDK artifacts cannot live in UE source")
    report = validate_report(report_path, stage_id=STAGE, scope=SCOPE)
    deps = read_dependencies(dependencies)
    for name, item in deps.items():
        if not any(p["sha256"] == item["sha256"] for p in report["prerequisites"]):
            raise ValueError("USD report was built with different " + name)
    # Use only the exact prefix whose installed libraries were accepted by USD.
    prefix = report_path.parent / "install"
    for name in ("libusd_usd.so", "libusd_tf.so", "libusd_usdGeom.so"):
        entry = report["evidence"].get("installed.lib/" + name)
        if not entry or digest(prefix / "lib" / name) != entry["sha256"]:
            raise ValueError("USD report does not bind required library " + name)
    edits = patches(ue)
    artifact_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="native-usd-sdk-", dir=artifact_root))
    run.chmod(0o755)
    (run / "openusd").symlink_to(prefix, target_is_directory=True)
    for name, item in deps.items():
        (run / name).symlink_to(item["prefix"], target_is_directory=True)
    tbb_include = Path(deps["tbb"]["path"]).parent / "source/IntelTBB-2019u8/include"
    (run / "tbb-include").symlink_to(tbb_include, target_is_directory=True)
    diffs = []
    for relative, (before, after) in edits.items():
        for label, data in (("before", before), ("after", after)):
            path = run / label / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        diffs.extend(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True),
            fromfile="a/" + str(relative), tofile="b/" + str(relative)))
    (run / "changes.patch").write_text("".join(diffs))
    for relative, (before, after) in edits.items():
        if (ue / relative).read_bytes() != before: raise ValueError("engine changed while preparing SDK")
        if before != after: (ue / relative).write_bytes(after)
    receipt = {"kind": "verified_sdk_binding_not_editor_pass", "openusd_report": str(report_path),
        "openusd_report_sha256": digest(report_path), "dependencies": {k: {x: v[x] for x in ("path", "sha256", "prefix")} for k, v in deps.items()},
        "engine_sources": {str(p): digest(ue / p) for p in edits}, "root": str(run),
        "relocatable": False, "shared_tbb_and_boost": True}
    (run / "native-sdk.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (run / "dependency-map.json").write_text(json.dumps({k: v["path"] for k, v in deps.items()}, indent=2) + "\n")
    return run


def verify(ue, root):
    root, ue = root.resolve(), ue.resolve()
    record = json.loads((root / "native-sdk.json").read_text())
    if record.get("kind") != "verified_sdk_binding_not_editor_pass" or record.get("root") != str(root):
        raise ValueError("SDK binding identity mismatch")
    report = Path(record["openusd_report"])
    if digest(report) != record["openusd_report_sha256"]:
        raise ValueError("OpenUSD report changed")
    validate_report(report, stage_id=STAGE, scope=SCOPE)
    deps = read_dependencies(root / "dependency-map.json")
    if record["dependencies"] != {k: {x: v[x] for x in ("path", "sha256", "prefix")} for k, v in deps.items()}:
        raise ValueError("SDK prerequisites changed")
    expected = {"openusd": report.parent / "install", **{k: Path(v["prefix"]) for k, v in deps.items()},
        "tbb-include": Path(deps["tbb"]["path"]).parent / "source/IntelTBB-2019u8/include"}
    for name, target in expected.items():
        if not (root / name).is_symlink() or (root / name).resolve() != target.resolve():
            raise ValueError("SDK link changed: " + name)
    sources = record["engine_sources"]
    if set(sources) != {str(p) for p in patches(ue)}:
        raise ValueError("SDK engine source set changed")
    for relative, checksum in sources.items():
        if digest(ue / relative) != checksum or digest(root / "after" / relative) != checksum:
            raise ValueError("SDK engine source changed: " + relative)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--ue-root", type=Path, required=True)
    for flag in ("openusd-report", "dependencies", "artifact-root", "sdk-root"):
        parser.add_argument("--" + flag, type=Path)
    args = parser.parse_args()
    if not Path("/.dockerenv").exists() or platform.machine() != "aarch64":
        parser.error("native ARM64 Docker required")
    try:
        if args.action == "prepare":
            if not all((args.openusd_report, args.dependencies, args.artifact_root)):
                parser.error("prepare requires report, dependencies and artifact-root")
            print(prepare(args.ue_root, args.openusd_report, args.dependencies, args.artifact_root))
        else:
            if not args.sdk_root: parser.error("verify requires sdk-root")
            verify(args.ue_root, args.sdk_root)
            print("PASS native SDK binding; not Editor acceptance")
    except (OSError, ValueError) as error:
        parser.exit(1, f"FAIL: {error}\n")
