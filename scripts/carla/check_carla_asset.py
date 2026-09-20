#!/usr/bin/env python3
"""Evaluate saved uncooked UStaticMesh assets, not Editor reimport or Cook."""

import argparse
import json
from pathlib import Path
import subprocess

from check_ue_interchange import native_elf, read_json, sha256, UFBX_SCOPE
from stage_report import validate_report, write_report


STAGE = "ue-ufbx-asset-roundtrip"
SCOPE = "Saved and reloaded uncooked UStaticMesh assets; not Editor reimport, Cook or GPU rendering"


def validate_graph(data):
    if (data.get("Name") != "CarlaAssetProbe" or data.get("Platform") != "Linux"
            or data.get("Configuration") != "Development"):
        raise ValueError("wrong asset target")
    modules = data.get("Modules", {})
    required = {"Core", "CoreUObject", "Engine", "CarlaUfbxMesh", "CarlaUfbxLegacy"}
    if not required <= modules.keys():
        raise ValueError("missing real Engine/backend modules")
    if {"UnrealEd", "FBX", "InterchangeFbxParser"} & modules.keys():
        raise ValueError("unexpected Editor or SDK dependency")
    if "INTEL_ISPC=0" not in modules.get("IntelISPC", {}).get("PublicDefinitions", []):
        raise ValueError("expected explicit scalar asset profile")


def validate_native(data, fixture):
    if (not isinstance(data, dict) or data.get("stage") != STAGE or data.get("scope") != SCOPE
            or data.get("status") != "PASS" or data.get("error") != ""
            or data.get("source_sha256") != sha256(fixture)):
        raise ValueError("native asset source, scope or result mismatch")
    for name in ("instances", "saved_assets", "reloaded_assets", "rebuilt_objects"):
        if type(data.get(name)) is not int or data[name] != 2:
            raise ValueError(f"incomplete two-asset native check: {name}")


def run(args):
    root = args.run_dir.resolve()
    output = root / "stage-report.json"
    if output.exists() or output.is_symlink():
        raise ValueError("refusing to overwrite asset stage report")
    checks = {name: "FAIL" for name in (
        "graph", "build", "architecture", "linkage", "native", "sources", "assets", "prerequisite")}
    errors, evidence, prerequisites = [], {}, []
    try:
        validate_graph(read_json(root / "target.json"))
        checks["graph"] = "PASS"
        if args.exit_code != 0:
            raise ValueError(f"native process exit={args.exit_code}")
        processes = read_json(root / "processes.json")
        if any(type(processes.get(step)) is not int or processes[step] != 0
               for step in ("graph", "build", "native")):
            raise ValueError("missing successful process statuses")
        if not (root / "build.log").is_file():
            raise ValueError("missing actual UBT build log")
        checks["build"] = "PASS"
        if not native_elf(args.program):
            raise ValueError("not a native AArch64 Program")
        checks["architecture"] = "PASS"
        with (root / "linkage-validation.log").open("w") as stream:
            subprocess.run(["ldd", "-r", str(args.program)], stdout=stream,
                           stderr=subprocess.STDOUT, timeout=30, check=True)
        linkage = (root / "linkage-validation.log").read_text()
        if any(value in linkage for value in ("not found", "undefined symbol", "libfbxsdk")):
            raise ValueError("unresolved or SDK runtime dependency")
        checks["linkage"] = "PASS"
        validate_native(read_json(root / "native.json"), args.input)
        checks["native"] = "PASS"
        with (root / "source-verification.log").open("w") as stream:
            subprocess.run(["sha256sum", "--check", str(root / "source-files.sha256")],
                           stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=120)
        checks["sources"] = "PASS"
        assets = [path for path in (root / "assets").glob("*.uasset") if path.is_file()]
        if len(assets) != 2 or any(path.stat().st_size == 0 for path in assets):
            raise ValueError("expected two nonempty saved asset files")
        checks["assets"] = "PASS"
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    try:
        validate_report(args.ufbx_report, stage_id="ufbx-fbx-static-backend", scope=UFBX_SCOPE)
        checks["prerequisite"] = "PASS"
        prerequisites.append({"path": args.ufbx_report, "stage_id": "ufbx-fbx-static-backend", "scope": UFBX_SCOPE})
    except (OSError, ValueError) as error:
        errors.append(str(error))
    diagnostic = root / "asset-diagnostic.json"
    diagnostic.write_text(json.dumps({"errors": errors, "process_exit_code": args.exit_code}, indent=2) + "\n")
    evidence.update({name: diagnostic for name in checks})
    for name in ("build", "native", "linkage-validation", "source-verification"):
        path = root / (name + ".log")
        if path.is_file():
            evidence[name] = path
    if (root / "processes.json").is_file():
        evidence["processes"] = root / "processes.json"
    for path in root.rglob("*"):
        if path.is_file() and path != output:
            evidence["run." + str(path.relative_to(root))] = path
    for name, path in (("program", args.program), ("fixture", args.input), ("checker", Path(__file__))):
        if path.is_file():
            evidence[name] = path
    commit = root / "ue-commit.txt"
    report = write_report(output, stage_id=STAGE, scope=SCOPE,
        exit_code=1 if errors else 0, required_checks=list(checks), checks=checks, evidence=evidence,
        sources={"ue": {"location": str(args.ue_root), "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
        command=[str(args.program), "-nullrhi", "-input=" + str(args.input)], prerequisites=prerequisites)
    print(f"{report['status']} {STAGE} report={output}")
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("program", "input", "run-dir", "ue-root", "ufbx-report"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--exit-code", required=True, type=int)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
