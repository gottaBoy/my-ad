#!/usr/bin/env python3
"""Fail-closed evidence for real static Worker IPC, not a full Editor stage."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from check_ue_interchange import native_elf, read_json, sha256, UFBX_SCOPE
from prepare_interchange_parser import MODULE, SDK_UNITS
from prepare_interchange_worker import WORKER
from stage_report import write_report, validate_report

STAGE = "ue-ufbx-worker-static"
SCOPE = "Real InterchangeWorker static ufbx over UE command-queue TCP; not production WorkerHandler, full FBX, Editor or Cook"
EXPECTED = Counter({
    "valid Worker handshake": 1, "IPC load and graph readback": 1,
    "IPC mesh consumer readback": 6, "queued requests preserve task and error isolation": 3,
    "malformed task explicitly fails": 1, "unknown translator explicitly fails": 1,
    "unsupported animation task explicitly fails": 1, "unsupported conversion task explicitly fails": 1,
    "failed reload cannot serve old payload": 1, "Worker reload recovers after failure": 1,
    "payload recovers after failed tasks": 1, "normal Terminate exits zero without kill": 1,
    "bad version returns protocol error and nonzero exit": 2,
    "lost peer exits nonzero within deadline": 1, "connection failure exits nonzero": 1,
})


def validate_native(data, root, fixture, worker):
    if (not isinstance(data, dict) or data.get("stage") != STAGE or data.get("scope") != SCOPE
            or data.get("status") != "PASS" or data.get("error") != ""
            or data.get("source_sha256") != sha256(fixture) or data.get("worker") != str(worker)):
        raise ValueError("Worker stage/source/program identity or status mismatch")
    names = data.get("checks")
    if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
            or Counter(names) != EXPECTED or type(data.get("self_tests")) is not int
            or data["self_tests"] != sum(EXPECTED.values())):
        raise ValueError("incomplete Worker protocol checks")
    pids = data.get("worker_pids")
    parent = data.get("parent_pid")
    if (not isinstance(pids, list) or len(pids) != 5 or type(parent) is not int or parent <= 0
            or any(type(pid) is not int or pid <= 0 or pid == parent for pid in pids) or len(set(pids)) != 5):
        raise ValueError("expected five distinct Worker child processes")
    version = data.get("protocol_version")
    if not isinstance(version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+\.[01]", version) is None:
        raise ValueError("invalid observed protocol version")
    files = data.get("files")
    if not isinstance(files, list) or len(files) != 8 or any(not isinstance(p, str) for p in files):
        raise ValueError("expected eight Worker graph/payload outputs")
    paths = []
    for value in files:
        path = Path(value)
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(root / "outputs/valid") or path.stat().st_size <= 0):
            raise ValueError("missing or unsafe Worker output")
        for parent_path in path.parents:
            if parent_path == root: break
            if parent_path.is_symlink(): raise ValueError("symlink output directory")
        paths.append(path)
    if len(set(paths)) != 8 or Counter(p.suffix for p in paths) != {".itc": 2, ".payload": 6}:
        raise ValueError("incorrect or duplicate Worker output types")
    for directory in ("version-0", "version-1", "disconnect", "no-server"):
        if any(p.suffix in (".itc", ".payload") for p in (root / "outputs" / directory).rglob("*")):
            raise ValueError("negative Worker scenario produced assets")
    return paths


def deployment(root, ue, filename, kind, expected, evidence):
    folder = Path((root / filename).read_text().strip())
    report = read_json(folder / "deployment.json")
    if not isinstance(report, dict) or report.get("kind") != kind or set(report.get("engine_files", {})) != expected:
        raise ValueError("invalid Worker/parser source deployment")
    for relative, digest in report["engine_files"].items():
        current, retained = ue / relative, folder / "after" / relative
        if sha256(current) != digest or sha256(retained) != digest:
            raise ValueError("deployed/retained source hash mismatch")
        evidence[filename + "." + relative] = retained
    evidence[filename + ".deployment"] = folder / "deployment.json"
    evidence[filename + ".patch"] = folder / "changes.patch"


def run(args):
    root = args.run_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "stage-report.json"
    if report_path.exists() or report_path.is_symlink(): raise ValueError("refusing to overwrite Worker stage")
    checks = {name: "FAIL" for name in ("build", "architecture", "native", "outputs", "deployment", "prerequisite")}
    evidence, errors, prerequisites = {}, [], []
    try:
        if args.exit_code != 0: raise ValueError(f"Worker probe exit={args.exit_code}")
        if not native_elf(args.program) or not native_elf(args.worker): raise ValueError("Worker and peer must be ARM64 ELF")
        checks["architecture"] = "PASS"
        if not (root / "worker-build.log").is_file() or not (root / "build.log").is_file():
            raise ValueError("missing Worker or peer build log")
        linkage = (root / "worker-linkage.log").read_text()
        if any(word in linkage for word in ("not found", "undefined symbol", "libfbxsdk")):
            raise ValueError("Worker linkage is invalid")
        checks["build"] = "PASS"
        files = validate_native(read_json(root / "native.json"), root, args.input, args.worker)
        for index, path in enumerate(files): evidence[f"output.{index}"] = path
        checks["native"] = checks["outputs"] = "PASS"
    except (OSError, ValueError, TypeError) as error:
        errors.append(str(error))
    try:
        parser_files = {str(MODULE / "Private" / (name + ".cpp")) for name in SDK_UNITS}
        parser_files.update(str(MODULE / p) for p in ("InterchangeFbxParser.Build.cs",
            "Public/InterchangeFbxStaticBackend.h", "Private/InterchangeFbxStaticParser.cpp"))
        deployment(root, args.ue_root, "prepare-dir.txt", "source_deployment_not_build_evidence", parser_files, evidence)
        worker_files = {str(WORKER / p) for p in ("InterchangeWorker.Build.cs", "Private/InterchangeWorker.cpp",
            "Private/InterchangeWorkerImpl.cpp", "Private/InterchangeWorkerStatic.cpp")}
        deployment(root, args.ue_root, "worker-prepare-dir.txt", "worker_source_not_runtime_evidence", worker_files, evidence)
        checks["deployment"] = "PASS"
    except (OSError, ValueError, TypeError) as error:
        errors.append(str(error))
    try:
        validate_report(args.ufbx_report, stage_id="ufbx-fbx-static-backend", scope=UFBX_SCOPE)
        prerequisites.append({"path": args.ufbx_report, "stage_id": "ufbx-fbx-static-backend", "scope": UFBX_SCOPE})
        checks["prerequisite"] = "PASS"
    except (OSError, ValueError) as error:
        errors.append(str(error))
    diagnostic = root / "worker-diagnostic.json"
    diagnostic.write_text(json.dumps({"errors": errors, "probe_exit_code": args.exit_code}, indent=2) + "\n")
    for name in checks: evidence[name] = diagnostic
    for path in root.iterdir():
        if path.is_file() and path != report_path: evidence["run." + path.name] = path
    for path in (root / "outputs").rglob("*.log"):
        evidence["log." + str(path.relative_to(root))] = path
    for name, path in (("peer", args.program), ("worker", args.worker), ("fixture", args.input),
                       ("evaluator", Path(__file__).resolve())):
        if path.is_file(): evidence[name] = path
    commit = root / "ue-commit.txt"
    report = write_report(report_path, stage_id=STAGE, scope=SCOPE,
        exit_code=0 if not errors and all(v == "PASS" for v in checks.values()) else 1,
        required_checks=list(checks), checks=checks, evidence=evidence,
        sources={"ue": {"location": str(args.ue_root), "revision": commit.read_text().strip() if commit.is_file() else "unverified"}},
        command=[str(args.program), "-native-worker-test", "-worker=" + str(args.worker)], prerequisites=prerequisites)
    if report["status"] == "PASS": validate_report(report_path, stage_id=STAGE, scope=SCOPE)
    print(f"{report['status']} {STAGE} report={report_path}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("program", "worker", "input", "run-dir", "ue-root", "ufbx-report"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    raise SystemExit(run(parser.parse_args()))
