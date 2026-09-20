#!/usr/bin/env python3
"""Run the native static MeshDescription probe and write scoped stage evidence.

run_checks(...) takes the same named arguments as the CLI and returns a stage
report. The caller supplies the actual UE git commit and a completed build's
JSON argv file; the build is not executed here. Every probe runs with a timeout
and an inherited zero soft core limit. Expected exit-2 rejections are successful
checks, not successful imports. Only fresh probe outputs are evaluated.
"""

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
    "carla_stage_report", Path(__file__).with_name("stage_report.py"))
stage_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stage_report)

STAGE = "ue-meshdescription-static"
SCOPE = "UE FMeshDescription in-memory bridge; not UStaticMesh, Editor or Cook"
ASSIMP_STAGE = "assimp-fbx-backend"
ASSIMP_SCOPE = "Assimp FBX backend only; not Autodesk SDK ABI or Unreal Editor/Cook"
COORDINATE_POLICY = "FBX Front/Coord/Up to UE X/Y/Z, centimeters, UV V flip"
CASES = (
    ("self-test", None, "self-test"),
    ("blender-cube", "BlenderCube.fbx", "static"),
    ("multi-material", "MultiMatId.fbx", "materials"),
    ("unsupported-animation", "AnimatedCharacter.fbx", "unsupported"),
    ("unsupported-morph", "MorphTargets.fbx", "unsupported"),
    ("truncated-input", None, "truncated"),
)
REQUIRED_CHECKS = ["preflight", *[name for name, _, _ in CASES]]
RUNTIME_FLAGS = ("-unattended", "-stdout", "-notraceserver", "-traceautostart=0")
MAX_TIMEOUT_SECONDS = 300


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def _read_json(path):
    if not path.is_file():
        raise ValueError(f"missing JSON file: {path}")
    # UE SaveStringToFile can emit a BOM; json.loads(bytes) detects its encoding.
    return json.loads(path.read_bytes(), object_pairs_hook=_object, parse_constant=_constant)


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def _verify_runtime_library(path, expected_sha256):
    if not path.is_file():
        raise ValueError(f"runtime Assimp library is missing or not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"runtime Assimp library SHA256 mismatch: {path}; "
                         f"expected {expected_sha256}, got {actual}")
    return actual


def _count(report, name, minimum=0):
    value = report.get(name)
    if (type(value) not in (int, float) or not minimum <= value <= 2**63 - 1
            or int(value) != value):
        raise ValueError(f"{name} must be an integer count >= {minimum}, got {value!r}")
    return value


def evaluate_report(report, *, returncode, kind, input_path=None):
    """Validate a C++ report against the process result and requested case."""
    if kind not in {"self-test", "static", "materials", "unsupported", "truncated"}:
        raise ValueError(f"unknown case kind: {kind}")
    negative = kind in {"unsupported", "truncated"}
    expected_code, expected_status = (2, "FAIL") if negative else (0, "PASS")
    if type(returncode) is not int or returncode != expected_code:
        raise ValueError(f"expected clean exit {expected_code}, got {returncode!r}")
    if not isinstance(report, dict):
        raise ValueError("probe report must be a JSON object")
    for field, expected in (
        ("stage", STAGE), ("scope", SCOPE), ("coordinate_policy", COORDINATE_POLICY),
        ("status", expected_status), ("input", "" if input_path is None else str(input_path)),
    ):
        if report.get(field) != expected:
            raise ValueError(f"{field} must exactly match {expected!r}")
    error = report.get("error")
    if not isinstance(error, str) or (not error.strip() if negative else error != ""):
        raise ValueError("FAIL needs a nonempty error; PASS needs an empty error")
    for name in ("self_tests", "vertices", "triangles", "material_slots", "serialized_bytes"):
        _count(report, name)
    if kind == "self-test":
        _count(report, "self_tests", 25)
    elif negative:
        if kind == "unsupported" and "unsupported" not in error.lower():
            raise ValueError("unsupported fixture must report an unsupported error")
        for name in ("vertices", "triangles", "material_slots", "serialized_bytes"):
            if report[name] != 0:
                raise ValueError(f"rejected input left partial output: {name}")
    else:
        for name in ("vertices", "triangles", "material_slots", "serialized_bytes"):
            _count(report, name, 2 if kind == "materials" and name == "material_slots" else 1)
        bounds = report.get("bounds_cm")
        if (not isinstance(bounds, list) or len(bounds) != 6
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds)
                or any(bounds[index] > bounds[index + 3] for index in range(3))):
            raise ValueError("bounds_cm must contain six finite, ordered coordinates")
    return report


def _invoke(program, work_dir, name, kind, input_path, timeout_seconds):
    case_dir = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=work_dir))
    case_dir.chmod(0o755)
    raw = case_dir / "raw.json"
    argv = [str(program), "-self-test" if input_path is None else f"-input={input_path}",
            f"-output={raw}", *RUNTIME_FLAGS]
    command_path, log_path, result_path = (
        case_dir / "command.json", case_dir / "process.log", case_dir / "result.json")
    _write_json(command_path, argv)
    result = {"name": name, "status": "FAIL", "returncode": None, "timed_out": False,
              "expected_exit_code": 2 if kind in {"unsupported", "truncated"} else 0,
              "errors": []}
    started = time.monotonic()
    try:
        with log_path.open("xb") as log:
            completed = subprocess.run(
                argv, cwd=program.parent, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, timeout=timeout_seconds, check=False,
            )
        result["returncode"] = completed.returncode
        evaluate_report(_read_json(raw), returncode=completed.returncode,
                        kind=kind, input_path=input_path)
    except subprocess.TimeoutExpired:
        result["timed_out"] = True
        result["errors"].append(f"probe timed out after {timeout_seconds} seconds")
    except (OSError, ValueError, OverflowError, RecursionError) as error:
        result["errors"].append(str(error))
    result["duration_seconds"] = time.monotonic() - started
    if not result["errors"]:
        result["status"] = "PASS"
    else:
        with log_path.open("ab") as log:
            log.write(("\nrunner: " + "; ".join(result["errors"]) + "\n").encode("utf-8"))
    _write_json(result_path, result)
    return result, {name: result_path, f"{name}.raw": raw,
                    f"{name}.command": command_path, f"{name}.log": log_path}


def _build_evidence(run_dir, build_command):
    paths = {build_command}
    for directory in {run_dir, build_command.parent}:
        for name in ("build.log", "ubt.log", "architecture.txt", "linkage.log",
                     "unchanged-sources.log", "prerequisite.log", "ue-commit.txt"):
            path = directory / name
            if path.exists():
                paths.add(path)
        for pattern in ("*.sha256", "*.patch", "*.diff"):
            paths.update(directory.glob(pattern))
    # Do not snapshot validation.log: the shell is still writing our output there.
    return {f"build-artifact.{index}.{path.name}": path
            for index, path in enumerate(sorted(paths))}


def run_checks(*, program, fixtures, run_dir, ue_root, ue_commit, assimp_report,
               build_command, timeout_seconds=120):
    """Run six cases, returning a PASS/FAIL report even on preflight failures."""
    if (type(timeout_seconds) not in (int, float)
            or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS):
        raise ValueError(f"timeout_seconds must be in (0, {MAX_TIMEOUT_SECONDS}]")
    program, fixtures, run_dir, ue_root, assimp_report, build_command = (
        Path(value).resolve() for value in
        (program, fixtures, run_dir, ue_root, assimp_report, build_command))
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "stage-report.json"
    if output.exists() or output.is_symlink():
        raise ValueError(f"refusing to overwrite an existing stage report: {output}")
    work_dir = Path(tempfile.mkdtemp(prefix="meshbridge-checks-", dir=run_dir))
    work_dir.chmod(0o755)
    sources = {"ue": {"location": str(ue_root), "revision": ue_commit}}
    prerequisites = [{"stage_id": ASSIMP_STAGE, "scope": ASSIMP_SCOPE, "path": assimp_report}]
    runtime_library = program.parent / "libassimp.so.6"
    expected_library_sha256 = None
    evidence = _build_evidence(run_dir, build_command)
    evidence.update({"program": program, "runtime-library": runtime_library,
                     "evaluator": Path(__file__).resolve(),
                     "stage-report-writer": Path(stage_report.__file__).resolve()})
    preflight = {"status": "FAIL", "errors": [], "core_dumps_disabled": False}
    if not program.is_file() or not os.access(program, os.X_OK):
        preflight["errors"].append(f"program is not an executable file: {program}")
    if not ue_root.is_dir():
        preflight["errors"].append(f"UE root is not a directory: {ue_root}")
    if not isinstance(ue_commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", ue_commit):
        preflight["errors"].append("ue_commit must be the full actual UE git commit")
    try:
        build_argv = _read_json(build_command)
        if (not isinstance(build_argv, list) or not build_argv
                or not isinstance(build_argv[0], str) or not build_argv[0].strip()
                or not all(isinstance(arg, str) for arg in build_argv)):
            raise ValueError("build-command must be a nonempty JSON argv list")
        commit_file = run_dir / "ue-commit.txt"
        if commit_file.exists() and commit_file.read_text(encoding="utf-8").strip() != ue_commit:
            raise ValueError("ue-commit.txt disagrees with --ue-commit")
    except (OSError, ValueError, RecursionError) as error:
        preflight["errors"].append(f"build provenance: {error}")
    try:
        prerequisite = stage_report.validate_report(assimp_report, stage_id=ASSIMP_STAGE,
                                                    scope=ASSIMP_SCOPE)
        if "assimp" not in prerequisite["sources"]:
            raise ValueError("Assimp prerequisite is missing named assimp source provenance")
        sources["assimp"] = prerequisite["sources"]["assimp"]
        if "library" not in prerequisite["evidence"]:
            raise ValueError("Assimp prerequisite is missing named library evidence")
        expected_library_sha256 = prerequisite["evidence"]["library"]["sha256"]
        preflight["runtime_library"] = {
            "path": str(runtime_library),
            "sha256": _verify_runtime_library(runtime_library, expected_library_sha256),
            "prerequisite_sha256": expected_library_sha256,
        }
    except (OSError, ValueError, RecursionError) as error:
        preflight["errors"].append(f"Assimp prerequisite: {error}")
    for _, filename, _ in CASES:
        if filename is not None:
            path = fixtures / filename
            evidence[f"fixture.{filename}"] = path
            if not path.is_file() or path.stat().st_size == 0:
                preflight["errors"].append(f"missing or empty fixture: {path}")
    truncated = work_dir / "truncated.fbx"
    try:
        with (fixtures / "BlenderCube.fbx").open("rb") as stream:
            prefix = stream.read(33)
        if len(prefix) <= 32:
            raise ValueError("BlenderCube must exceed 32 bytes for the truncation case")
        truncated.write_bytes(prefix[:32])
        evidence["fixture.truncated"] = truncated
    except (OSError, ValueError) as error:
        preflight["errors"].append(f"truncated fixture: {error}")

    results = {}
    if not preflight["errors"]:
        saved_core_limit = None
        try:
            saved_core_limit = resource.getrlimit(resource.RLIMIT_CORE)
            resource.setrlimit(resource.RLIMIT_CORE, (0, saved_core_limit[1]))
            preflight["core_dumps_disabled"] = True
            for name, filename, kind in CASES:
                _verify_runtime_library(runtime_library, expected_library_sha256)
                input_path = fixtures / filename if filename else None
                if kind == "truncated":
                    input_path = truncated
                result, files = _invoke(program, work_dir, name, kind, input_path, timeout_seconds)
                results[name] = result
                evidence.update(files)
            _verify_runtime_library(runtime_library, expected_library_sha256)
        except (OSError, ValueError) as error:
            preflight["errors"].append(f"runner setup: {error}")
        finally:
            if saved_core_limit is not None and preflight["core_dumps_disabled"]:
                try:
                    resource.setrlimit(resource.RLIMIT_CORE, saved_core_limit)
                except (OSError, ValueError) as error:
                    preflight["errors"].append(f"restoring core limit: {error}")
    if not preflight["errors"]:
        preflight["status"] = "PASS"
    preflight_path = work_dir / "preflight.json"
    _write_json(preflight_path, preflight)
    evidence["preflight"] = preflight_path
    for name, _, _ in CASES:
        if name not in results:
            results[name] = {"name": name, "status": "MISSING", "returncode": None,
                             "errors": ["not run because preflight/setup failed"]}
            path = work_dir / f"{name}.not-run.json"
            _write_json(path, results[name])
            evidence[name] = path
    checks = {"preflight": preflight["status"],
              **{name: result["status"] for name, result in results.items()}}
    command = [sys.executable, str(Path(__file__).resolve())]
    for name, value in (
        ("program", program), ("fixtures", fixtures), ("run-dir", run_dir),
        ("ue-root", ue_root), ("ue-commit", ue_commit), ("assimp-report", assimp_report),
        ("build-command", build_command), ("timeout-seconds", timeout_seconds),
    ):
        command.extend([f"--{name}", str(value)])
    return stage_report.write_report(
        output, stage_id=STAGE, scope=SCOPE,
        exit_code=0 if all(status == "PASS" for status in checks.values()) else 1,
        required_checks=REQUIRED_CHECKS, checks=checks, evidence=evidence,
        sources=sources, command=command, prerequisites=prerequisites,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("program", "fixtures", "run-dir", "ue-root", "assimp-report", "build-command"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--ue-commit", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        report = run_checks(**vars(args))
    except (OSError, ValueError) as error:
        print(f"FAIL {STAGE}: {error}", file=sys.stderr)
        return 1
    print(f"{report['status']} {STAGE} scope={SCOPE!r} report={args.run_dir / 'stage-report.json'}")
    for error in report["errors"]:
        print(error, file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
