#!/usr/bin/env python3
"""Replay the opt-in Program-only parser port; retain every engine source edit."""

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import tempfile


MODULE = Path("Engine/Plugins/Interchange/Runtime/Source/Parsers/Fbx")
SDK_UNITS = ("FbxAPI", "FbxAnimation", "FbxCamera", "FbxConvert", "FbxHelper",
             "FbxLight", "FbxMaterial", "FbxMesh", "FbxScene", "InterchangeFbxParser")
BEGIN = "#if !CARLA_INTERCHANGE_UFBX_STATIC\n"
END = "\n#endif // !CARLA_INTERCHANGE_UFBX_STATIC\n"
RULE_MARKER = "\t\t\tAddEngineThirdPartyPrivateStaticDependencies(Target,"
RULE_INSERT = '''\t\t\t// CARLA opt-in static parser: the default Editor/SDK path is unchanged.
\t\t\tstring StaticBackend = System.Environment.GetEnvironmentVariable("CARLA_INTERCHANGE_UFBX_STATIC");
\t\t\tif (!string.IsNullOrEmpty(StaticBackend) && StaticBackend != "0" && StaticBackend != "1")
\t\t\t\tthrow new BuildException("CARLA_INTERCHANGE_UFBX_STATIC must be 0 or 1");
\t\t\tbool bStaticBackend = StaticBackend == "1";
\t\t\tPublicDefinitions.Add("CARLA_INTERCHANGE_UFBX_STATIC=" + (bStaticBackend ? "1" : "0"));
\t\t\tif (bStaticBackend)
\t\t\t{
\t\t\t\tif (Target.Type != TargetType.Program || Target.Platform != UnrealTargetPlatform.Linux
\t\t\t\t\t|| Target.Architecture != UnrealArch.Arm64 || Target.bCompileAgainstEngine)
\t\t\t\t\tthrow new BuildException("Static ufbx diagnostic requires a Linux ARM64 Program without Engine; not Editor");
\t\t\t\treturn;
\t\t\t}

'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prepare(ue, artifact_root):
    ue, artifact_root = ue.resolve(), artifact_root.resolve()
    if artifact_root.is_relative_to(ue):
        raise ValueError("engine tree cannot contain deployment artifacts")
    templates = Path(__file__).resolve().parent / "ue-parser"
    changes = {}
    for name in SDK_UNITS:
        path = MODULE / "Private" / (name + ".cpp")
        raw = (ue / path).read_bytes()
        text = raw.decode("utf-8")
        if text.startswith(BEGIN) and text.endswith(END):
            changes[path] = (raw, raw)
        elif "CARLA_INTERCHANGE_UFBX_STATIC" in text:
            raise ValueError(f"partial or conflicting source guard: {path}")
        else:
            changes[path] = (raw, (BEGIN + text + END).encode("utf-8"))
    rule = MODULE / "InterchangeFbxParser.Build.cs"
    raw = (ue / rule).read_bytes()
    text = raw.decode("utf-8")
    if RULE_INSERT in text:
        changes[rule] = (raw, raw)
    elif "CARLA_INTERCHANGE_UFBX_STATIC" in text or text.count(RULE_MARKER) != 1:
        raise ValueError("conflicting parser build rules")
    else:
        changes[rule] = (raw, text.replace(RULE_MARKER, RULE_INSERT + RULE_MARKER).encode("utf-8"))

    artifact_root.mkdir(parents=True, exist_ok=True)
    receipt_path = artifact_root / "parser-deployment.json"
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    generated = {}
    for folder, name in (("Public", "InterchangeFbxStaticBackend.h"),
                         ("Private", "InterchangeFbxStaticParser.cpp")):
        path = MODULE / folder / name
        after = (templates / name).read_bytes()
        destination = ue / path
        if destination.is_symlink():
            raise ValueError(f"refusing to replace engine source symlink: {path}")
        before = destination.read_bytes() if destination.exists() else None
        if before is not None and before != after and sha(before) != receipt.get(str(path)):
            raise ValueError(f"local generated-source edits would be overwritten: {path}")
        changes[path] = (before, after)
        generated[str(path)] = sha(after)
    run = Path(tempfile.mkdtemp(prefix="parser-prepare-", dir=artifact_root))
    run.chmod(0o755)
    patches = []
    for path, (before, after) in changes.items():
        destination = ue / path
        if destination.is_symlink():
            raise ValueError(f"refusing to edit source symlink: {path}")
        if before is not None:
            saved = run / "before" / path
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(before)
        saved = run / "after" / path
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(after)
        patches.extend(difflib.unified_diff((before or b"").decode("utf-8").splitlines(True),
            after.decode("utf-8").splitlines(True),
            fromfile="a/" + str(path) if before is not None else "/dev/null", tofile="b/" + str(path)))
    (run / "changes.patch").write_text("".join(patches), encoding="utf-8")
    # All conflicts are checked before writes. Recheck each input before changing it.
    for path, (before, after) in changes.items():
        destination = ue / path
        current = destination.read_bytes() if destination.exists() else None
        if current != before:
            raise ValueError(f"engine source changed during preparation: {path}")
        if before != after:
            destination.write_bytes(after)
    receipt_path.write_text(json.dumps(generated, sort_keys=True, indent=2) + "\n")
    report = {"kind": "source_deployment_not_build_evidence",
              "engine_files": {str(path): sha(after) for path, (_, after) in changes.items()},
              "changes": sum(before != after for before, after in changes.values()),
              "default_sdk_path_preserved": True}
    (run / "deployment.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ue-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(prepare(args.ue_root, args.artifact_root))
    except (OSError, ValueError) as error:
        parser.exit(1, f"FAIL: {error}\n")


if __name__ == "__main__":
    main()
