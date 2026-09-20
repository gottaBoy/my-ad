#!/usr/bin/env python3
"""Validate the real parser static slice, not Worker or Editor acceptance."""

import argparse
from collections import Counter
import json
from pathlib import Path

from check_ue_interchange import native_elf, read_json, sha256, UFBX_SCOPE
from stage_report import validate_report, write_report
from prepare_interchange_parser import MODULE, SDK_UNITS

STAGE = "ue-ufbx-parser-static"
SCOPE = "Real FInterchangeFbxParser with static ufbx session; not worker process, factory assets, full FBX, Editor or Cook"
EXPECTED = Counter({
    "missing provider is a real parser error": 1,
    "real parser loads graph without clearing caller messages": 1,
    "real parser mesh key/transform and consumer readback": 6,
    "repeat query reuses verified bytes": 1,
    "same request in another result directory": 1,
    "query failure reports to owned and external containers": 1,
    "write failure never publishes cached path": 1,
    "missing cached file is rejected": 1,
    "conflicting cached bytes are rejected": 1,
    "graph write failure invalidates loaded state": 1,
    "failed reload invalidates old graph and scene": 1,
    "load succeeds again after prior failure": 1,
    "reset invalidates session": 1,
    "repeated release is safe": 1,
    "unsupported conversion policy is rejected at load": 4,
    "real parser memory graph overload": 1,
    "nonempty caller graph is preserved and rejected": 1,
    "generic non-static payload is rejected": 1,
    "animation query is explicitly unsupported": 1,
    "parser instances do not share session state": 1,
    "ambiguous providers are rejected": 1,
    "real animated/morph FBX rejected": 2,
})


def validate_native(data, root, fixture):
    if (not isinstance(data, dict) or data.get("stage") != STAGE or data.get("scope") != SCOPE
            or data.get("status") != "PASS" or data.get("error") != ""
            or data.get("source_sha256") != sha256(fixture)):
        raise ValueError("native parser identity, result or source mismatch")
    names = data.get("checks")
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or Counter(names) != EXPECTED or type(data.get("self_tests")) is not int
            or data["self_tests"] != sum(EXPECTED.values())):
        raise ValueError("incomplete native parser lifecycle checks")
    paths = data.get("files")
    if not isinstance(paths, list) or len(paths) != 11 or any(not isinstance(p, str) for p in paths):
        raise ValueError("expected eleven graph/payload outputs")
    outputs = []
    for value in paths:
        path = Path(value)
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root / "outputs") or path.stat().st_size <= 0):
            raise ValueError("missing or unsafe parser output")
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError("symlink directory in parser output")
        outputs.append(path)
    if len(set(outputs)) != 11 or Counter(path.suffix for path in outputs) != {".itc": 3, ".payload": 8}:
        raise ValueError("duplicate or incorrect graph/payload outputs")
    return outputs


def run(args):
    root = args.run_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "stage-report.json").exists():
        raise ValueError("refusing to overwrite parser stage report")
    checks = {name: "FAIL" for name in ("build", "architecture", "native", "outputs", "prerequisite", "deployment")}
    evidence, errors = {}, []
    try:
        if args.exit_code != 0:
            raise ValueError(f"native process exit={args.exit_code}")
        checks["build"] = "PASS" if (root / "build.log").is_file() else "FAIL"
        if not native_elf(args.program):
            raise ValueError("not an ARM64 parser Program")
        checks["architecture"] = "PASS"
        files = validate_native(read_json(root / "native.json"), root, args.input)
        checks["native"] = checks["outputs"] = "PASS"
        for index, path in enumerate(files):
            evidence[f"output.{index}"] = path
    except (OSError, ValueError) as error:
        errors.append(str(error))
    try:
        deployed = Path((root / "prepare-dir.txt").read_text().strip())
        deployment = read_json(deployed / "deployment.json")
        if (not isinstance(deployment, dict) or deployment.get("kind") != "source_deployment_not_build_evidence"
                or deployment.get("default_sdk_path_preserved") is not True
                or not isinstance(deployment.get("engine_files"), dict) or not deployment["engine_files"]):
            raise ValueError("invalid engine source deployment")
        expected = {str(MODULE / "Private" / (name + ".cpp")) for name in SDK_UNITS}
        expected.update(str(MODULE / path) for path in ("InterchangeFbxParser.Build.cs",
            "Public/InterchangeFbxStaticBackend.h", "Private/InterchangeFbxStaticParser.cpp"))
        if set(deployment["engine_files"]) != expected:
            raise ValueError("incomplete engine parser deployment file set")
        for relative, digest in deployment["engine_files"].items():
            path = args.ue_root / relative
            if not path.resolve().is_relative_to(args.ue_root.resolve()) or sha256(path) != digest:
                raise ValueError("engine parser source differs from deployment")
            retained = deployed / "after" / relative
            if sha256(retained) != digest:
                raise ValueError("retained engine source differs from deployment")
            evidence["engine." + relative] = retained
        evidence["deployment-record"] = deployed / "deployment.json"
        evidence["deployment-patch"] = deployed / "changes.patch"
        checks["deployment"] = "PASS"
    except (OSError, ValueError, TypeError) as error:
        errors.append(str(error))
    prerequisites = []
    try:
        validate_report(args.ufbx_report, stage_id="ufbx-fbx-static-backend", scope=UFBX_SCOPE)
        checks["prerequisite"] = "PASS"
        prerequisites.append({"path": args.ufbx_report, "stage_id": "ufbx-fbx-static-backend", "scope": UFBX_SCOPE})
    except (OSError, ValueError) as error:
        errors.append(str(error))
    diagnostic = root / "parser-diagnostic.json"
    diagnostic.write_text(json.dumps({"errors": errors, "process_exit_code": args.exit_code}, indent=2) + "\n")
    for name in checks:
        evidence[name] = diagnostic
    for path in root.iterdir():
        if path.is_file() and path.name != "stage-report.json":
            evidence["run." + path.name] = path
    for name, path in (("program", args.program), ("fixture", args.input), ("evaluator", Path(__file__).resolve())):
        if path.is_file():
            evidence[name] = path
    commit = root / "ue-commit.txt"
    report = write_report(root / "stage-report.json", stage_id=STAGE, scope=SCOPE,
        exit_code=0 if not errors and all(value == "PASS" for value in checks.values()) else 1,
        required_checks=list(checks), checks=checks, evidence=evidence,
        sources={"ue": {"location": str(args.ue_root), "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
        command=[str(args.program), "-native-parser-test", "-input=" + str(args.input)],
        prerequisites=prerequisites)
    if report["status"] == "PASS":
        validate_report(root / "stage-report.json", stage_id=STAGE, scope=SCOPE)
    print(f"{report['status']} {STAGE} report={root / 'stage-report.json'}")
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("program", "input", "run-dir", "ue-root", "ufbx-report"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
