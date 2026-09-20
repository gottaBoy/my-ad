#!/usr/bin/env python3
"""Write and verify scoped, fail-closed CARLA stage evidence (stdlib only).

API: write_report(output, *, stage_id, scope, exit_code, required_checks,
                  checks, evidence, sources, command, prerequisites=())
returns the written report, including status and errors. validate_report(path,
*, stage_id, scope) returns a verified PASS report or raises ReportError.

checks maps names to PASS/FAIL/SKIP. Every required check must be reported and
have an evidence file with the same name. Extra evidence is also verified.
sources maps names to {"location": ..., "revision": ...}; record dirty patches
and build inputs as additional evidence. prerequisites is a sequence of
{"stage_id": ..., "scope": ..., "path": ...} with exact expected identity.
Input paths are relative to the caller; stored paths are relative to the report.
command is an argv list, recorded but never executed.

There is no status override, inferred parent-stage PASS, or historical import.
Callers define the required checks and prerequisites and evaluate actual test
results. Hashes detect changed files; this is not signed attestation and does
not interpret logs, verify source revisions, or establish test coverage.

CLI: write --output REPORT --stage-id ID --scope TEXT --exit-code CODE
           --required-check NAME --check NAME PASS --evidence NAME FILE
           --source NAME LOCATION REVISION
           [--prerequisite EXPECTED_ID EXPECTED_SCOPE REPORT]
           --command PROGRAM [ARG ...]
     validate REPORT --stage-id EXPECTED_ID --scope EXPECTED_SCOPE
Repeat check/evidence/source/prerequisite options as needed; --command goes last.
Exit codes: 0 = verified PASS, 1 = FAIL or invalid report, 2 = CLI usage error.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


SCHEMA_VERSION = 1
MAX_PREREQUISITE_DEPTH = 32


class ReportError(ValueError):
    """The requested scoped PASS could not be established."""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _resolve(value, base):
    if not _text(value):
        raise ReportError("path must be a nonempty string")
    try:
        return (base / value).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise ReportError(f"invalid path {value!r}: {error}") from error


def _digest(path):
    if not path.is_file():
        raise ReportError(f"not a regular file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(value, base):
    record = {"path": os.fspath(value), "sha256": None}
    try:
        path = _resolve(record["path"], Path.cwd())
        record["path"] = os.path.relpath(path, base)
        record["sha256"] = _digest(path)
    except (OSError, ValueError):
        pass  # Missing/unreadable files remain in the FAIL report.
    return record


def _file_identity(record, base):
    if not isinstance(record, dict):
        raise ReportError("file evidence must be an object")
    path = _resolve(record.get("path"), base)
    checksum = record.get("sha256")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ReportError(f"missing or invalid SHA256 for {path}")
    return path, checksum


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReportError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ReportError(f"invalid JSON constant: {value}")


def _content_errors(report, path, *, stage_id, scope, ancestors):
    errors = []
    if type(report.get("schema_version")) is not int or report["schema_version"] != SCHEMA_VERSION:
        errors.append("unsupported or missing schema_version")
    if not _text(stage_id) or report.get("stage_id") != stage_id:
        errors.append(f"stage_id must exactly match {stage_id!r}")
    if not _text(scope) or report.get("scope") != scope:
        errors.append(f"scope must exactly match {scope!r}")
    try:
        if datetime.fromisoformat(report["created_at"]).utcoffset() is None:
            raise ValueError("timezone missing")
    except (KeyError, TypeError, ValueError):
        errors.append("created_at must be an ISO timestamp with timezone")
    if type(report.get("exit_code")) is not int or report["exit_code"] != 0:
        errors.append(f"process exit_code is not integer zero: {report.get('exit_code')!r}")

    command = report.get("command")
    if (not isinstance(command, list) or not command or not _text(command[0])
            or not all(isinstance(arg, str) for arg in command)):
        errors.append("command must be a nonempty argv list")
    sources = report.get("sources")
    if not isinstance(sources, dict) or not sources:
        errors.append("source provenance is required")
    else:
        for name, source in sources.items():
            if (not _text(name) or not isinstance(source, dict)
                    or not _text(source.get("location")) or not _text(source.get("revision"))):
                errors.append(f"source {name!r} requires location and revision")

    required = report.get("required_checks")
    if (not isinstance(required, list) or not required
            or not all(_text(name) for name in required)):
        errors.append("required_checks must be a nonempty list of names")
        required = []
    elif len(set(required)) != len(required):
        errors.append("required_checks contains duplicate names")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks:
        errors.append("at least one named check is required")
        checks = {}
    for name in required:
        if name not in checks:
            errors.append(f"missing required check: {name}")
    for name, status in checks.items():
        if not _text(name) or name not in required:
            errors.append(f"undeclared check: {name!r}")
        if status != "PASS":
            errors.append(f"check {name!r} is not PASS: {status!r}")

    evidence = report.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        errors.append("at least one evidence file is required")
        evidence = {}
    for name in set(required) | checks.keys():
        if name not in evidence:
            errors.append(f"missing evidence for check: {name}")
    for name, record in evidence.items():
        try:
            if not _text(name):
                raise ReportError("evidence name must be nonempty")
            evidence_path, checksum = _file_identity(record, path.parent)
            if evidence_path == path:
                raise ReportError("a report cannot be its own evidence")
            if _digest(evidence_path) != checksum:
                raise ReportError(f"SHA256 mismatch: {evidence_path}")
        except (OSError, ValueError) as error:
            errors.append(f"evidence {name!r}: {error}")

    prerequisites = report.get("prerequisites")
    if not isinstance(prerequisites, list):
        errors.append("prerequisites must be an explicit list")
        prerequisites = []
    seen_stages = set()
    for index, prerequisite in enumerate(prerequisites):
        try:
            if (not isinstance(prerequisite, dict)
                    or not _text(prerequisite.get("stage_id"))
                    or not _text(prerequisite.get("scope"))):
                raise ReportError("expected stage_id and scope are required")
            expected_id = prerequisite["stage_id"]
            if expected_id in seen_stages:
                raise ReportError(f"duplicate prerequisite stage_id: {expected_id}")
            seen_stages.add(expected_id)
            prerequisite_path, checksum = _file_identity(prerequisite, path.parent)
            _validate_file(
                prerequisite_path, stage_id=expected_id, scope=prerequisite["scope"],
                ancestors=ancestors, checksum=checksum,
            )
        except (OSError, ValueError) as error:
            errors.append(f"prerequisite {index}: {error}")
    return errors


def _validate_file(path, *, stage_id, scope, ancestors=(), checksum=None):
    if path in ancestors:
        raise ReportError(f"prerequisite cycle: {path}")
    if len(ancestors) >= MAX_PREREQUISITE_DEPTH:
        raise ReportError("prerequisite depth limit exceeded")
    if not path.is_file():
        raise ReportError(f"report is not a regular file: {path}")
    raw = path.read_bytes()
    if checksum is not None and hashlib.sha256(raw).hexdigest() != checksum:
        raise ReportError(f"prerequisite SHA256 mismatch: {path}")
    try:
        report = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
    except (UnicodeError, ValueError) as error:
        raise ReportError(f"invalid report JSON: {path}: {error}") from error
    if not isinstance(report, dict):
        raise ReportError(f"report must be a JSON object: {path}")
    errors = _content_errors(report, path, stage_id=stage_id, scope=scope,
                             ancestors=(*ancestors, path))
    if report.get("status") != "PASS":
        errors.append("report status is not PASS")
    if report.get("errors") != []:
        errors.append("report errors must be an empty list")
    if errors:
        raise ReportError(f"{path}: " + "; ".join(errors))
    return report


def validate_report(path, *, stage_id, scope):
    """Recheck exact identity, checks, evidence hashes and all prerequisites."""
    try:
        return _validate_file(_resolve(os.fspath(path), Path.cwd()),
                              stage_id=stage_id, scope=scope)
    except OSError as error:
        raise ReportError(str(error)) from error


def write_report(output, *, stage_id, scope, exit_code, required_checks, checks,
                 evidence, sources, command, prerequisites=()):
    """Atomically write PASS or FAIL; gate failures still produce a FAIL report."""
    path = _resolve(os.fspath(output), Path.cwd())
    report = {
        "schema_version": SCHEMA_VERSION,
        "stage_id": stage_id,
        "scope": scope,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": exit_code,
        "required_checks": required_checks,
        "checks": checks,
        "evidence": {name: _snapshot(value, path.parent) for name, value in evidence.items()},
        "sources": sources,
        "command": command,
        "prerequisites": [
            {"stage_id": item["stage_id"], "scope": item["scope"],
             **_snapshot(item["path"], path.parent)}
            for item in prerequisites
        ],
        "status": "FAIL",
        "errors": [],
    }
    report["errors"] = _content_errors(report, path, stage_id=stage_id, scope=scope,
                                       ancestors=(path,))
    if not report["errors"]:
        report["status"] = "PASS"
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{path.name}.", dir=path.parent) as temporary:
        pending = Path(temporary) / "report.json"
        pending.write_text(encoded, encoding="utf-8")
        pending.replace(path)
    return report


def _named(parser, entries, option):
    result = {}
    for name, *values in entries:
        if name in result:
            parser.error(f"{option}: duplicate name {name!r}")
        result[name] = values
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    writer = commands.add_parser("write", help="write one explicitly scoped stage report")
    writer.add_argument("--output", type=Path, required=True)
    writer.add_argument("--stage-id", required=True)
    writer.add_argument("--scope", required=True)
    writer.add_argument("--exit-code", type=int, required=True)
    writer.add_argument("--required-check", action="append", default=[], metavar="NAME")
    writer.add_argument("--check", action="append", nargs=2, default=[], metavar=("NAME", "STATUS"))
    writer.add_argument("--evidence", action="append", nargs=2, default=[], metavar=("NAME", "FILE"))
    writer.add_argument("--source", action="append", nargs=3, default=[],
                        metavar=("NAME", "LOCATION", "REVISION"))
    writer.add_argument("--prerequisite", action="append", nargs=3, default=[],
                        metavar=("STAGE_ID", "SCOPE", "REPORT"))
    writer.add_argument("--command", nargs=argparse.REMAINDER, required=True)
    validator = commands.add_parser("validate", help="verify a PASS for exact stage and scope")
    validator.add_argument("report", type=Path)
    validator.add_argument("--stage-id", required=True)
    validator.add_argument("--scope", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "write":
            checks = _named(parser, args.check, "--check")
            evidence = _named(parser, args.evidence, "--evidence")
            sources = _named(parser, args.source, "--source")
            report = write_report(
                args.output, stage_id=args.stage_id, scope=args.scope, exit_code=args.exit_code,
                required_checks=args.required_check,
                checks={name: values[0] for name, values in checks.items()},
                evidence={name: values[0] for name, values in evidence.items()},
                sources={name: {"location": values[0], "revision": values[1]}
                         for name, values in sources.items()},
                command=args.command,
                prerequisites=[{"stage_id": stage, "scope": scope, "path": path}
                               for stage, scope, path in args.prerequisite],
            )
            output = args.output
        else:
            report = validate_report(args.report, stage_id=args.stage_id, scope=args.scope)
            output = args.report
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"{report['status']} {report['stage_id']} scope={report['scope']!r} report={output}")
    for error in report["errors"]:
        print(error, file=sys.stderr)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
