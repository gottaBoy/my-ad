#!/usr/bin/env python3
"""Verify only the dual-backend ufbx -> UE in-memory MeshDescription substage."""

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
import time


_spec = importlib.util.spec_from_file_location(
    "carla_assimp_bridge_checker", Path(__file__).with_name("check_ue_meshbridge.py"))
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)
stage_report = base.stage_report

STAGE = "ue-ufbx-meshdescription-static"
SCOPE = ("ufbx to UE FMeshDescription in-memory bridge; dual backend, "
         "not SDK replacement, UStaticMesh, Editor or Cook")
UFBX_STAGE = "ufbx-fbx-static-backend"
UFBX_SCOPE = "ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC"
UFBX_COMMIT = "fcc5d6ba444cfd3eb80677dba5e37e493941abe5"
COORDINATE_POLICY = "ufbx right +Y/up +Z/front -X, centimeters, UV V flip"
CASES = base.CASES
REQUIRED_CHECKS = ["preflight", *[name for name, _, _ in CASES]]
RUNTIME_FLAGS = base.RUNTIME_FLAGS
MIN_SELF_TESTS = 32


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _match_evidence(report, report_path, path):
    path = Path(path).resolve()
    for record in report["evidence"].values():
        if (report_path.parent / record["path"]).resolve() == path:
            if record["sha256"] != digest(path):
                raise ValueError(f"prerequisite hash mismatch: {path}")
            return
    raise ValueError(f"prerequisite has no evidence for current file: {path}")


def _match_source_evidence(report, report_path, path):
    try:
        _match_evidence(report, report_path, path)
        return
    except ValueError:
        record = report["evidence"].get("build.source-files.sha256")
        if not record:
            raise
    # The backend emits canonical GNU sha256sum lines with absolute paths.
    # Check the individual source record, not only the hash of its manifest.
    manifest = (report_path.parent / record["path"]).resolve()
    expected = f"{digest(path)}  {path}".encode("utf-8")
    if expected not in manifest.read_bytes().splitlines():
        raise ValueError(f"backend source manifest lacks current SHA256: {path}")


def backend_inputs(ufbx_report, install):
    report_path, install = Path(ufbx_report).resolve(), Path(install).resolve()
    report = stage_report.validate_report(report_path, stage_id=UFBX_STAGE, scope=UFBX_SCOPE)
    source = report["sources"].get("ufbx", {})
    if source.get("revision") != UFBX_COMMIT:
        raise ValueError("ufbx prerequisite must match the pinned v0.23.0 source commit")
    source_root = Path(source.get("location", ""))
    if not source_root.is_absolute() or not source_root.is_dir():
        raise ValueError("ufbx prerequisite needs an absolute existing source directory")
    paths = [install / "lib/libufbx.a", install / "include/ufbx.h",
             source_root / "ufbx.c", source_root / "ufbx.h"]
    static_record = report["evidence"].get("static-library", {})
    if (report_path.parent / static_record.get("path", "")).resolve() != paths[0].resolve():
        raise ValueError("ufbx prerequisite must name the current static-library evidence")
    _match_evidence(report, report_path, paths[0])
    for path in paths[2:]:
        _match_source_evidence(report, report_path, path)
    if digest(paths[1]) != digest(paths[3]):
        raise ValueError("installed ufbx.h differs from pinned source")
    return report, [report_path, *paths]


def source_files(root):
    root = Path(root).resolve()
    if not (root / "Source/CarlaUfbxMesh/Private/CarlaUfbxMesh.cpp").is_file():
        raise ValueError("bridge source root is missing CarlaUfbxMesh")
    paths = sorted(path.resolve() for path in root.rglob("*") if path.is_file())
    if not paths:
        raise ValueError("bridge source root is empty")
    return paths


def capture_inputs(*, output, program, source_root, ufbx_install, ufbx_report, assimp_library, ue_commit):
    output = Path(output).resolve()
    if output.exists() or output.is_symlink():
        raise ValueError(f"refusing to overwrite build inputs: {output}")
    _, backend_paths = backend_inputs(ufbx_report, ufbx_install)
    paths = source_files(source_root) + backend_paths + [
        Path(assimp_library).resolve(), Path(__file__).resolve(), Path(base.__file__).resolve(),
        Path(stage_report.__file__).resolve(), Path(__file__).with_name("build-arm64-ue-ufbxbridge.sh"),
    ]
    manifest = {
        "schema_version": 1, "complete": False, "program": str(Path(program).resolve()),
        "source_root": str(Path(source_root).resolve()),
        "ufbx_install": str(Path(ufbx_install).resolve()),
        "ufbx_report": str(Path(ufbx_report).resolve()), "ue_commit": ue_commit,
        "source_files": [str(path) for path in source_files(source_root)],
        "inputs": {str(path): digest(path) for path in paths},
    }
    base._write_json(output, manifest)
    return manifest


def verify_inputs(manifest):
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("invalid build input manifest")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError("missing build input hashes")
    if manifest.get("source_files") != [str(path) for path in source_files(manifest["source_root"])]:
        raise ValueError("bridge source file set changed after capture")
    _, backend_paths = backend_inputs(manifest["ufbx_report"], manifest["ufbx_install"])
    for path in backend_paths + source_files(manifest["source_root"]):
        if str(path) not in inputs:
            raise ValueError(f"missing captured build input: {path}")
    for path, expected in inputs.items():
        if not Path(path).is_absolute() or digest(path) != expected:
            raise ValueError(f"build input SHA256 mismatch: {path}")


def seal_build(*, inputs, output):
    output = Path(output).resolve()
    if output.exists() or output.is_symlink():
        raise ValueError(f"refusing to overwrite build manifest: {output}")
    manifest = base._read_json(Path(inputs))
    if manifest.get("complete") is not False:
        raise ValueError("expected unsealed build inputs")
    verify_inputs(manifest)
    manifest.update(complete=True, program_sha256=digest(manifest["program"]))
    base._write_json(output, manifest)
    return manifest


def verify_build(manifest, *, program, ufbx_report, ufbx_install, ue_commit):
    verify_inputs(manifest)
    if manifest.get("complete") is not True:
        raise ValueError("build manifest is not sealed")
    for name, expected in (("program", program), ("ufbx_report", ufbx_report), ("ufbx_install", ufbx_install)):
        if manifest.get(name) != str(Path(expected).resolve()):
            raise ValueError(f"build manifest disagrees with current {name}")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", ue_commit) or manifest.get("ue_commit") != ue_commit:
        raise ValueError("build manifest disagrees with actual UE commit")
    if digest(program) != manifest.get("program_sha256"):
        raise ValueError("current program SHA256 differs from completed build")
    with Path(program).open("rb") as stream:
        header = stream.read(20)
    if len(header) != 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\xb7\x00":
        raise ValueError("program is not a little-endian ARM64 ELF")


def evaluate_report(report, *, returncode, kind, input_path=None):
    if not isinstance(report, dict):
        raise ValueError("probe report must be a JSON object")
    for name, expected in (("stage", STAGE), ("scope", SCOPE), ("backend", "ufbx"),
                           ("coordinate_policy", COORDINATE_POLICY),
                           ("equivalence_to_assimp", "not asserted; front-vs-forward policies differ")):
        if report.get(name) != expected:
            raise ValueError(f"{name} must exactly match {expected!r}")
    # Reuse count/bounds/error validation, never its stage identity or runner.
    base.evaluate_report({**report, "stage": base.STAGE, "scope": base.SCOPE,
                          "coordinate_policy": base.COORDINATE_POLICY},
                         returncode=returncode, kind=kind, input_path=input_path)
    if kind == "self-test":
        base._count(report, "self_tests", MIN_SELF_TESTS)
        details = report.get("coordinate_checks")
        if not isinstance(details, list) or len(details) != 4:
            raise ValueError("four real FBX coordinate/handedness checks are required")
        seen = set()
        for item in details:
            if not isinstance(item, dict):
                raise ValueError("coordinate check must be an object")
            sign, mirrored = item.get("source_front_sign"), item.get("mirrored_instance")
            if type(sign) not in (int, float) or sign not in (-1, 1) or type(mirrored) is not bool:
                raise ValueError("invalid source axis or mirrored instance case")
            if (sign, mirrored) in seen:
                raise ValueError("duplicate coordinate check")
            seen.add((sign, mirrored))
            if item.get("ufbx_reversed_winding") is not (sign == 1) or item.get("ue_facing_matches_normals") is not True:
                raise ValueError("ufbx/UE winding verification failed")
            x = -sign * (700 if mirrored else 500)
            for name, expected in (
                ("bounds_cm", [x, 100 if mirrored else 200, 300, x, 200 if mirrored else 300,
                               500 if mirrored else 400]),
                ("normal_world", [-sign, 0, 0]),
            ):
                values = item.get(name)
                if (not isinstance(values, list) or len(values) != len(expected)
                        or any(type(v) not in (int, float) or not math.isfinite(v)
                               or not math.isclose(v, e, abs_tol=1e-3)
                               for v, e in zip(values, expected))):
                    raise ValueError(f"coordinate fixture {name} differs from UE policy")
    elif kind in {"static", "materials"}:
        expected_triangles, expected_slots = (12, 6) if kind == "static" else (960, 4)
        if report["triangles"] != expected_triangles or report["material_slots"] != expected_slots:
            raise ValueError("fixture triangle/material-slot counts differ from expected geometry")
    return report


def invoke(program, work_dir, name, kind, input_path, timeout):
    case_dir = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=work_dir))
    case_dir.chmod(0o755)
    raw, log = case_dir / "raw.json", case_dir / "process.log"
    argv = [str(program), "-backend=ufbx",
            "-self-test" if input_path is None else f"-input={input_path}",
            f"-output={raw}", *RUNTIME_FLAGS]
    base._write_json(case_dir / "command.json", argv)
    result = {"status": "FAIL", "returncode": None, "timed_out": False, "errors": [],
              "expected_exit_code": 2 if kind in {"unsupported", "truncated"} else 0}
    start = time.monotonic()
    try:
        with log.open("xb") as stream:
            process = subprocess.run(argv, cwd=program.parent, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        result["returncode"] = process.returncode
        evaluate_report(base._read_json(raw), returncode=process.returncode, kind=kind, input_path=input_path)
        result["status"] = "PASS"
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["errors"].append(f"probe timed out after {timeout} seconds")
    except (OSError, ValueError, OverflowError, RecursionError) as error:
        result["errors"].append(str(error))
    result["duration_seconds"] = time.monotonic() - start
    base._write_json(case_dir / "result.json", result)
    return result, {name: case_dir / "result.json", f"{name}.raw": raw,
                    f"{name}.command": case_dir / "command.json", f"{name}.log": log}


def run_checks(*, program, fixtures, run_dir, ue_root, ue_commit, ufbx_report, ufbx_install,
               assimp_report, assimp_bridge_report, build_manifest, build_command, timeout_seconds=120):
    if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 300:
        raise ValueError("timeout_seconds must be in (0, 300]")
    program, fixtures, run_dir, ue_root, ufbx_report, ufbx_install, assimp_report, assimp_bridge_report, build_manifest, build_command = (
        Path(path).resolve() for path in (program, fixtures, run_dir, ue_root, ufbx_report, ufbx_install,
                                         assimp_report, assimp_bridge_report, build_manifest, build_command))
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "stage-report.json"
    if output.exists() or output.is_symlink():
        raise ValueError(f"refusing to overwrite stage report: {output}")
    work = Path(tempfile.mkdtemp(prefix="ufbxbridge-checks-", dir=run_dir))
    work.chmod(0o755)
    sources = {"ue": {"location": str(ue_root), "revision": ue_commit}}
    prerequisites = [
        {"stage_id": UFBX_STAGE, "scope": UFBX_SCOPE, "path": ufbx_report},
        {"stage_id": base.ASSIMP_STAGE, "scope": base.ASSIMP_SCOPE, "path": assimp_report},
        {"stage_id": base.STAGE, "scope": base.SCOPE, "path": assimp_bridge_report},
    ]
    evidence = base._build_evidence(run_dir, build_command)
    evidence.update({"program": program, "build-manifest": build_manifest,
                     "evaluator": Path(__file__).resolve()})
    preflight = {"status": "FAIL", "errors": [], "core_dumps_disabled": False}
    manifest = None
    verification = dict(program=program, ufbx_report=ufbx_report, ufbx_install=ufbx_install, ue_commit=ue_commit)
    try:
        if not program.is_file() or not os.access(program, os.X_OK) or not ue_root.is_dir():
            raise ValueError("missing executable or UE root")
        argv = base._read_json(build_command)
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            raise ValueError("build command must be a nonempty argv list")
        manifest = base._read_json(build_manifest)
        verify_build(manifest, **verification)
        backend, paths = backend_inputs(ufbx_report, ufbx_install)
        sources["ufbx"] = backend["sources"]["ufbx"]
        evidence.update({f"build-input.{index}": Path(path) for index, path in enumerate(manifest["inputs"])})
        for item in prerequisites[1:]:
            stage_report.validate_report(item["path"], stage_id=item["stage_id"], scope=item["scope"])
        regression = base._read_json(assimp_bridge_report)
        if regression["evidence"]["program"]["sha256"] != manifest["program_sha256"]:
            raise ValueError("Assimp regression did not test the current dual-backend binary")
        assimp = base._read_json(assimp_report)
        runtime = program.parent / "libassimp.so.6"
        base._verify_runtime_library(runtime, assimp["evidence"]["library"]["sha256"])
        evidence["assimp-runtime"] = runtime
        for _, filename, _ in CASES:
            if filename:
                path = fixtures / filename
                if not path.is_file() or not path.stat().st_size:
                    raise ValueError(f"missing fixture: {path}")
                _match_evidence(backend, ufbx_report, path)
                evidence[f"fixture.{filename}"] = path
        truncated = work / "truncated.fbx"
        with (fixtures / "BlenderCube.fbx").open("rb") as stream:
            prefix = stream.read(33)
        if len(prefix) != 33:
            raise ValueError("cube fixture is too short for truncation test")
        truncated.write_bytes(prefix[:32])
        evidence["fixture.truncated"] = truncated
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        preflight["errors"].append(str(error))

    results = {}
    if not preflight["errors"]:
        saved_limit = None
        try:
            saved_limit = resource.getrlimit(resource.RLIMIT_CORE)
            resource.setrlimit(resource.RLIMIT_CORE, (0, saved_limit[1]))
            preflight["core_dumps_disabled"] = True
            for name, filename, kind in CASES:
                verify_build(manifest, **verification)
                base._verify_runtime_library(runtime, assimp["evidence"]["library"]["sha256"])
                input_path = fixtures / filename if filename else None
                if kind == "truncated":
                    input_path = truncated
                result, files = invoke(program, work, name, kind, input_path, timeout_seconds)
                results[name] = result
                evidence.update(files)
            verify_build(manifest, **verification)
            base._verify_runtime_library(runtime, assimp["evidence"]["library"]["sha256"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            preflight["errors"].append(str(error))
        finally:
            if saved_limit is not None and preflight["core_dumps_disabled"]:
                try:
                    resource.setrlimit(resource.RLIMIT_CORE, saved_limit)
                except (OSError, ValueError) as error:
                    preflight["errors"].append(str(error))
    if not preflight["errors"]:
        preflight["status"] = "PASS"
    base._write_json(work / "preflight.json", preflight)
    evidence["preflight"] = work / "preflight.json"
    for name, _, _ in CASES:
        if name not in results:
            results[name] = {"status": "MISSING", "errors": ["preflight/setup failed"]}
            path = work / f"{name}.not-run.json"
            base._write_json(path, results[name])
            evidence[name] = path
    checks = {"preflight": preflight["status"], **{name: value["status"] for name, value in results.items()}}
    return stage_report.write_report(output, stage_id=STAGE, scope=SCOPE,
                                     exit_code=0 if all(value == "PASS" for value in checks.values()) else 1,
                                     required_checks=REQUIRED_CHECKS, checks=checks, evidence=evidence,
                                     sources=sources, command=[sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                                     prerequisites=prerequisites)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    capture = commands.add_parser("capture-inputs")
    for name in ("output", "program", "source-root", "ufbx-install", "ufbx-report", "assimp-library"):
        capture.add_argument(f"--{name}", type=Path, required=True)
    capture.add_argument("--ue-commit", required=True)
    seal = commands.add_parser("seal-build")
    for name in ("inputs", "output"):
        seal.add_argument(f"--{name}", type=Path, required=True)
    run = commands.add_parser("run")
    for name in ("program", "fixtures", "run-dir", "ue-root", "ufbx-report", "ufbx-install",
                 "assimp-report", "assimp-bridge-report", "build-manifest", "build-command"):
        run.add_argument(f"--{name}", type=Path, required=True)
    run.add_argument("--ue-commit", required=True)
    run.add_argument("--timeout-seconds", type=float, default=120)
    args = vars(parser.parse_args(argv))
    action = args.pop("action")
    try:
        if action == "capture-inputs":
            capture_inputs(**args)
        elif action == "seal-build":
            seal_build(**args)
        else:
            report = run_checks(**args)
            print(f"{report['status']} {STAGE}: {args['run_dir'] / 'stage-report.json'}")
            for error in report["errors"]:
                print(error, file=sys.stderr)
            return 0 if report["status"] == "PASS" else 1
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        print(f"FAIL {STAGE}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
