#!/usr/bin/env python3
"""Capture immutable input identity; finalize the complete runtime invocation.

capture prints only the manifest SHA256. finalize requires the pinned identity,
unchanged inputs, a zero outer exit code and the exact production child scope.
Neither command performs RPC, installs dependencies or establishes server build
identity. Logs must be closed before finalize. Existing outputs are preserved.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile


sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "runtime_endpoint", Path(__file__).with_name("check_carla_runtime.py"))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
stage_report = runtime.stage_report
STAGE_ID = "carla-runtime-invocation"
LABELS = ("wheel", "wrapper", "evaluator", "stage-writer", "provenance", "entry-reporter")
CHECKS = ("outer-exit", "manifest", *[f"input-{label}" for label in LABELS], "endpoint")
LOGS = ("client-install.log", "client-install.command.json", "wheel.sha256",
        "entry-command.json", "client.log")


def root_scope(mode):
    return (f"Complete container CARLA client invocation and exact {mode} endpoint prerequisites; "
            "NOT server architecture/build or Cook")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _digest(path):
    _require(path.is_file(), f"missing or nonregular file: {path}")
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _encode(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def capture(output, files):
    output = Path(output).absolute()
    _require(not output.exists() and not output.is_symlink(), f"refusing to overwrite {output}")
    records = {}
    for label, filename in files:
        _require(label in LABELS, f"unknown input label: {label}")
        _require(label not in records, f"duplicate input label: {label}")
        # Preserve symlink paths so changing their target is detected on finalize.
        path = Path(filename).absolute()
        records[label] = {"path": str(path), "sha256": _digest(path)}
    _require(set(records) == set(LABELS), "all six fixed input labels are required")
    encoded = _encode({"schema_version": 1, "files": records})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(encoded)
    return hashlib.sha256(encoded).hexdigest()


def _input_state(run_dir, input_sha256):
    checks = dict.fromkeys(("manifest", *[f"input-{label}" for label in LABELS]), "FAIL")
    raw = {"checks": checks, "inputs": {}, "errors": []}
    manifest_path = run_dir / "inputs.json"
    evidence = {"inputs-manifest": manifest_path}
    expected_hashes = {}
    try:
        _require(isinstance(input_sha256, str) and re.fullmatch(r"[0-9a-fA-F]{64}", input_sha256),
                 "input-sha256 must be a pinned 64-digit SHA256")
        _require(manifest_path.is_file(), f"missing or nonregular manifest: {manifest_path}")
        encoded = manifest_path.read_bytes()
        _require(hashlib.sha256(encoded).hexdigest() == input_sha256.lower(), "manifest SHA256 mismatch")
        manifest = json.loads(encoded, object_pairs_hook=_object, parse_constant=_constant)
        _require(isinstance(manifest, dict) and type(manifest.get("schema_version")) is int
                 and manifest["schema_version"] == 1, "invalid input manifest schema")
        records = manifest.get("files")
        _require(isinstance(records, dict) and set(records) == set(LABELS), "manifest requires exactly six labels")
        checks["manifest"] = "PASS"
        expected_hashes["inputs-manifest"] = ("manifest", input_sha256.lower())
        for label in LABELS:
            record = records[label]
            name = f"input-{label}"
            try:
                _require(isinstance(record, dict), f"{label}: file record must be an object")
                filename, checksum = record.get("path"), record.get("sha256")
                _require(isinstance(filename, str) and Path(filename).is_absolute(), f"{label}: path must be absolute")
                _require(isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum),
                         f"{label}: invalid SHA256")
                path = Path(filename)
                evidence[f"file-{label}"] = path
                expected_hashes[f"file-{label}"] = (name, checksum)
                observed = {"path": filename, "expected_sha256": checksum, "actual_sha256": None}
                raw["inputs"][label] = observed
                observed["actual_sha256"] = _digest(path)
                _require(observed["actual_sha256"] == checksum, f"{label}: input SHA256 mismatch")
                checks[name] = "PASS"
            except (OSError, ValueError) as error:
                raw["errors"].append(f"{name}: {error}")
    except (OSError, ValueError, RecursionError) as error:
        raw["errors"].append(f"manifest: {error}")
    return raw, evidence, expected_hashes


def verify_inputs(run_dir, input_sha256):
    state, _, _ = _input_state(Path(run_dir).resolve(), input_sha256)
    _require(all(value == "PASS" for value in state["checks"].values()),
             "; ".join(state["errors"]) or "input verification incomplete")


def finalize(run_dir, mode, exit_code, input_sha256):
    _require(mode in runtime.STAGES, "mode must be rpc or sensors")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "stage-report.json"
    _require(not output.exists() and not output.is_symlink(), f"refusing to overwrite {output}")
    work = Path(tempfile.mkdtemp(prefix="entry-finalize-", dir=run_dir))
    work.chmod(0o755)
    checks = dict.fromkeys(CHECKS, "FAIL")
    raw = {"mode": mode, "outer_exit_code": exit_code, "input_sha256": input_sha256,
           "checks": checks, "inputs": {}, "errors": []}
    if type(exit_code) is int and exit_code == 0:
        checks["outer-exit"] = "PASS"
    else:
        raw["errors"].append(f"outer process exit code is not integer zero: {exit_code!r}")
    state, evidence, expected_hashes = _input_state(run_dir, input_sha256)
    checks.update(state["checks"])
    raw["inputs"] = state["inputs"]
    raw["errors"].extend(state["errors"])
    evidence["entry-reporter"] = Path(__file__).resolve()
    child = run_dir / "endpoint/stage-report.json"
    helper = Path(__file__).resolve()
    sources = {"entry-reporter": {"location": str(helper), "revision": f"sha256:{_digest(helper)}"}}
    child_stage, child_scope = runtime.STAGES[mode]
    child_sha256 = None
    try:
        child_sha256 = _digest(child)
        verified = stage_report.validate_report(child, stage_id=child_stage, scope=child_scope)
        _require(_digest(child) == child_sha256, "endpoint report changed during validation")
        sources = verified["sources"]
        checks["endpoint"] = "PASS"
    except (OSError, ValueError, RecursionError) as error:
        raw["errors"].append(f"endpoint: {error}")
    for name in LOGS:
        path = run_dir / name
        if path.exists() or path.is_symlink():
            evidence[f"log-{name}"] = path
    result_path = work / "finalize-result.json"
    evidence.update({name: result_path for name in CHECKS})
    evidence["finalize-result"] = result_path
    command = [sys.executable, str(helper), "finalize", "--run-dir", str(run_dir), "--mode", mode,
               "--exit-code", str(exit_code), "--input-sha256", str(input_sha256)]
    arguments = dict(stage_id=STAGE_ID, scope=root_scope(mode), required_checks=list(CHECKS),
                     checks=checks, evidence=evidence, sources=sources, command=command,
                     prerequisites=[{"path": child, "stage_id": child_stage, "scope": child_scope}])
    outer_failure = exit_code if type(exit_code) is int and exit_code != 0 else 1
    root_exit = 0 if all(value == "PASS" for value in checks.values()) else outer_failure
    raw["root_exit_code"] = root_exit
    result_path.write_bytes(_encode(raw))
    # Keep the temporary report beside the destination so relative evidence paths
    # stay valid, then publish exclusively: a concurrent finalizer cannot clobber it.
    fd, filename = tempfile.mkstemp(prefix=".runtime-entry-", suffix=".json", dir=run_dir)
    os.close(fd)
    pending = Path(filename)
    try:
        report = stage_report.write_report(pending, exit_code=root_exit, **arguments)
        late_errors = []
        for name, (check, expected) in expected_hashes.items():
            if report["evidence"][name]["sha256"] != expected:
                checks[check] = "FAIL"
                late_errors.append(f"{name}: SHA256 changed before final report")
        if child_sha256 is not None and report["prerequisites"][0]["sha256"] != child_sha256:
            checks["endpoint"] = "FAIL"
            late_errors.append("endpoint report changed before final report")
        if late_errors or (report["status"] != "PASS" and root_exit == 0):
            raw["errors"].extend(late_errors + report["errors"])
            raw["root_exit_code"] = outer_failure
            result_path.write_bytes(_encode(raw))
            report = stage_report.write_report(pending, exit_code=outer_failure, **arguments)
        os.link(pending, output)
    finally:
        pending.unlink(missing_ok=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    capture_parser = actions.add_parser("capture")
    capture_parser.add_argument("--output", type=Path, required=True)
    capture_parser.add_argument("--file", nargs=2, action="append", default=[], metavar=("LABEL", "PATH"))
    verify_parser = actions.add_parser("verify-inputs")
    verify_parser.add_argument("--run-dir", type=Path, required=True)
    verify_parser.add_argument("--input-sha256", required=True)
    final_parser = actions.add_parser("finalize")
    final_parser.add_argument("--run-dir", type=Path, required=True)
    final_parser.add_argument("--mode", choices=runtime.STAGES, required=True)
    final_parser.add_argument("--exit-code", type=int, required=True)
    final_parser.add_argument("--input-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "capture":
            print(capture(args.output, args.file))
            return 0
        if args.action == "verify-inputs":
            verify_inputs(args.run_dir, args.input_sha256)
            return 0
        report = finalize(args.run_dir, args.mode, args.exit_code, args.input_sha256)
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"{report['status']} {STAGE_ID} mode={args.mode} outer_exit={args.exit_code} root_exit={report['exit_code']}")
    for error in report["errors"]:
        print(error, file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
