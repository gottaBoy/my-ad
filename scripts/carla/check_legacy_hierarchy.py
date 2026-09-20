#!/usr/bin/env python3
"""Evaluate the native shared factory hierarchy slice, not asset acceptance."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from check_ue_interchange import native_elf, read_json, sha256, UFBX_SCOPE
from stage_report import validate_report, write_report

STAGE = "ue-ufbx-legacy-hierarchy"
SCOPE = "ufbx to shared Legacy factory hierarchy; not asset import, save/reimport, Editor or Cook"
EXPECTED = Counter({
    "shared factory skeleton and LOD policy": 1,
    "malformed hierarchy rejected atomically": 6,
    "synthetic root and full width IDs": 1,
    "deep hierarchy without recursive traversal": 1,
    "real ufbx IDs and shared factory hierarchy": 1,
    "instance transform material and native payload": 2,
    "corrupt adapter input rejected atomically": 11,
    "real nonzero pivot metadata rejected": 1,
    "real camera metadata rejected": 1,
    "real shared mesh mirrored instance and geometry transforms": 1,
})


def validate_native(data, fixture):
    if (not isinstance(data, dict) or data.get("stage") != STAGE or data.get("scope") != SCOPE
            or data.get("status") != "PASS" or data.get("error") != ""
            or data.get("source_sha256") != sha256(fixture)):
        raise ValueError("native hierarchy identity, source or result mismatch")
    names = data.get("checks")
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or Counter(names) != EXPECTED or type(data.get("self_tests")) is not int
            or data["self_tests"] != sum(EXPECTED.values())):
        raise ValueError("incomplete native hierarchy checks")
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or len(nodes) != 4 or type(data.get("instances")) is not int or data["instances"] != 2:
        raise ValueError("incomplete native hierarchy or instances")
    for index, (identity, parent, parent_index) in enumerate((("0", "0", -1), ("100", "0", 0),
                                                            ("101", "100", 1), ("102", "100", 1))):
        node = nodes[index]
        if (not isinstance(node, dict) or node.get("id") != identity or node.get("parent_id") != parent
                or type(node.get("source_index")) is not int or node["source_index"] != index
                or type(node.get("parent_index")) is not int or node["parent_index"] != parent_index
                or node.get("import") is not True):
            raise ValueError("changed source identity, order or parent index")
    for node, attribute, name in ((nodes[2], "200", "MeshA"), (nodes[3], "201", "MeshB")):
        if node.get("attribute_id") != attribute or node.get("name") != name:
            raise ValueError("changed mesh instance association")


def run(args):
    root = args.run_dir.resolve()
    report_path = root / "stage-report.json"
    if report_path.exists() or report_path.is_symlink():
        raise ValueError("refusing to overwrite hierarchy stage report")
    checks = {name: "FAIL" for name in ("build", "architecture", "native", "sources", "prerequisite")}
    errors, evidence, prerequisites = [], {}, []
    try:
        if args.exit_code != 0:
            raise ValueError(f"native process exit={args.exit_code}")
        if not (root / "build.log").is_file():
            raise ValueError("missing UBT build log")
        checks["build"] = "PASS"
        if not native_elf(args.program):
            raise ValueError("not an ARM64 Program")
        checks["architecture"] = "PASS"
        validate_native(read_json(root / "native.json"), args.input)
        checks["native"] = "PASS"
        with (root / "source-verification.log").open("w") as stream:
            subprocess.run(["sha256sum", "--check", str(root / "source-files.sha256")],
                           stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=60)
        checks["sources"] = "PASS"
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    try:
        validate_report(args.ufbx_report, stage_id="ufbx-fbx-static-backend", scope=UFBX_SCOPE)
        checks["prerequisite"] = "PASS"
        prerequisites.append({"path": args.ufbx_report, "stage_id": "ufbx-fbx-static-backend", "scope": UFBX_SCOPE})
    except (OSError, ValueError) as error:
        errors.append(str(error))
    diagnostic = root / "hierarchy-diagnostic.json"
    diagnostic.write_text(json.dumps({"errors": errors, "process_exit_code": args.exit_code}, indent=2) + "\n")
    evidence.update({name: diagnostic for name in checks})
    for path in root.rglob("*"):
        if path.is_file() and path != report_path:
            evidence["run." + str(path.relative_to(root))] = path
    for name, path in (("program", args.program), ("fixture", args.input), ("evaluator", Path(__file__))):
        if path.is_file():
            evidence[name] = path
    commit = root / "ue-commit.txt"
    report = write_report(report_path, stage_id=STAGE, scope=SCOPE,
        exit_code=1 if errors else 0, required_checks=list(checks), checks=checks, evidence=evidence,
        sources={"ue": {"location": str(args.ue_root), "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
        command=[str(args.program), "-legacy-hierarchy-test", "-input=" + str(args.input)],
        prerequisites=prerequisites)
    print(f"{report['status']} {STAGE} report={report_path}")
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("program", "input", "run-dir", "ue-root", "ufbx-report"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
