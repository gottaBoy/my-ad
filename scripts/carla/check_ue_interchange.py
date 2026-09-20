#!/usr/bin/env python3
"""Fail-closed evaluator for the native ufbx/Interchange static substage."""

import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile

from stage_report import write_report, validate_report


STAGE_ID = "ue-ufbx-interchange-static"
SCOPE = ("ufbx to UE Interchange static nodes and mesh payloads; not translator/worker, "
         "material shading, factory assets, Editor or Cook")
UFBX_SCOPE = "ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC"
CHECKS = ("build", "architecture", "process", "graph", "payload", "geometry")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_json,
                      parse_constant=reject_constant)


def native_elf(path):
    data = Path(path).read_bytes()[:20]
    return len(data) == 20 and data[:6] == b"\x7fELF\x02\x01" and data[18:20] == b"\xb7\x00"


def validate_payloads(data, output_dir):
    source = data.get("source_sha256")
    if not isinstance(source, str) or re.fullmatch(r"[0-9a-f]{64}", source) is None or output_dir.is_symlink():
        raise ValueError("invalid source digest or output directory")
    if type(data.get("payload_contract_version")) is not int or data["payload_contract_version"] != 2:
        raise ValueError("missing transform-aware payload contract version 2")
    mesh_count = data.get("mesh_nodes")
    count = data.get("payload_count")
    records = data.get("payloads")
    if (type(mesh_count) is not int or mesh_count <= 0 or type(count) is not int
            or count != 3 * mesh_count or not isinstance(records, list) or len(records) != count):
        raise ValueError("payload count must cover all meshes and three transforms")
    for field, minimum in (("source_scene_self_tests", 20), ("payload_self_tests", 4 * mesh_count + 19)):
        if type(data.get(field)) is not int or data[field] < minimum:
            raise ValueError(f"missing native self-tests: {field}")
    groups, names, keys, totals, result = {}, set(), set(), {}, []
    for entry in records:
        if not isinstance(entry, dict):
            raise ValueError("invalid payload record")
        uid, key, request = (entry.get(field) for field in ("mesh_uid", "payload_key", "request_uid"))
        if (not isinstance(uid, str) or not uid or not isinstance(key, str)
                or key != "ufbx-static-ue-cm-v1/" + data["source_sha256"] + "/" + uid
                or not isinstance(request, str) or re.fullmatch(r"[0-9a-f]{64}", request) is None):
            raise ValueError("invalid source/key/request identity")
        name = entry.get("filename")
        if name != request + ".payload" or name in names:
            raise ValueError("duplicate or unsafe payload filename")
        names.add(name)
        keys.add(key)
        modes = groups.setdefault(uid, set())
        mode = entry.get("transform")
        if mode not in ("identity", "translated", "mirrored") or mode in modes:
            raise ValueError("duplicate or missing transform query")
        modes.add(mode)
        path = output_dir / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError("payload must be a nonempty regular file")
        for metric in ("bytes", "vertices", "triangles", "materials"):
            if type(entry.get(metric)) is not int or entry[metric] <= 0:
                raise ValueError(f"invalid per-payload metric: {metric}")
            totals[metric] = totals.get(metric, 0) + entry[metric]
        if entry["bytes"] != path.stat().st_size or entry.get("roundtrip") is not True:
            raise ValueError("payload size or round-trip mismatch")
        if entry.get("dispatcher_roundtrip") is not True:
            raise ValueError("missing native Dispatcher round-trip")
        transport = []
        for field in ("request_json", "result_json"):
            if not isinstance(entry.get(field), str):
                raise ValueError("missing Dispatcher JSON")
            value = json.loads(entry[field], object_pairs_hook=unique_json, parse_constant=reject_constant)
            if not isinstance(value, dict):
                raise ValueError("invalid Dispatcher object")
            transport.append(value)
        request_data, response_data = transport
        command = request_data.get("CmdData")
        if (request_data.get("CmdID") != "Payload" or request_data.get("TranslatorID") != "FBX"
                or not isinstance(command, dict) or command.get("PayloadKey") != key
                or not isinstance(command.get("GlobalMeshTransform"), str) or not command["GlobalMeshTransform"]
                or response_data.get("ResultFile") != str(path)):
            raise ValueError("Dispatcher request/result does not match the payload record")
        result.append({**entry, "sha256": sha256(path)})
    if len(groups) != mesh_count or len(keys) != mesh_count or any(len(modes) != 3 for modes in groups.values()):
        raise ValueError("payloads do not cover every mesh and transform")
    if {path.name for path in output_dir.glob("*.payload")} != names:
        raise ValueError("payload files do not match the request records")
    for metric, field in (("bytes", "serialized_bytes"), ("vertices", "payload_vertices"),
                          ("triangles", "payload_triangles"), ("materials", "payload_materials")):
        if type(data.get(field)) is not int or data[field] != totals[metric]:
            raise ValueError(f"aggregate payload metric mismatch: {field}")
    return result


def run(args):
    root = Path(args.run_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    report_path = root / "stage-report.json"
    if report_path.exists() or report_path.is_symlink():
        raise ValueError(f"refusing to overwrite {report_path}")
    work = Path(tempfile.mkdtemp(prefix="interchange-checks-", dir=root))
    work.chmod(0o755)
    result_path = work / "result.json"
    checks = {name: "FAIL" for name in CHECKS}
    errors = []
    evidence = {}
    program = Path(args.program).resolve()
    fixture = Path(args.input).resolve()
    output_dir = root / "outputs"
    scene_report = root / "scene.json"
    process_log = root / "bootstrap.log"
    command = [str(program), "-scene-test", f"-input={fixture}",
               f"-output={scene_report}", f"-result-dir={output_dir}"]
    if (root / "build.log").is_file():
        evidence["build-log"] = root / "build.log"
    evidence.update({
        "architecture": root / "architecture.txt",
        "process": process_log,
        "graph": output_dir / "nodes.bin",
        "payload": work / "payload.bin",
        "geometry": work / "geometry.json",
        "input": fixture,
        "program": program,
        "source-files": root / "source-files.sha256",
        "build-command": root / "build-command.json",
        "evaluator": Path(__file__).resolve(),
        "stage-writer": Path(__file__).with_name("stage_report.py"),
        "runner": args.runner,
        "ue-commit": root / "ue-commit.txt",
        "ue-tracked-patch": root / "ue-tracked.patch",
        "result": result_path,
    })
    data = {}
    process_code = args.exit_code
    if process_code is None:
        process_code = 1
        try:
            with process_log.open("wb") as log:
                process = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                         timeout=args.timeout)
            process_code = process.returncode
        except subprocess.TimeoutExpired:
            process_code = 124
            errors.append(f"process exceeded {args.timeout} seconds")
        except OSError as error:
            errors.append(f"process could not start: {error}")
    checks["build"] = "PASS" if (root / "build.log").is_file() else "FAIL"
    arch = root / "architecture.txt"
    try:
        is_native = native_elf(program)
    except OSError:
        is_native = False
    checks["architecture"] = "PASS" if arch.is_file() and is_native and "ARM aarch64" in arch.read_text() else "FAIL"
    if process_code == 0:
        checks["process"] = "PASS"
        try:
            data = read_json(scene_report)
            expected_scope = SCOPE
            if (data.get("stage") != STAGE_ID or data.get("scope") != expected_scope
                    or data.get("status") != "PASS" or data.get("error") != ""):
                raise ValueError("scene report identity or status is invalid")
            for key in ("nodes", "scene_nodes", "mesh_nodes", "material_nodes",
                        "serialized_bytes", "payload_vertices", "payload_triangles",
                        "payload_materials"):
                if type(data.get(key)) is not int or data[key] <= 0:
                    raise ValueError(f"invalid scene metric: {key}")
            expected_meshes = getattr(args, "expected_meshes", None)
            if expected_meshes is not None and data["mesh_nodes"] != expected_meshes:
                raise ValueError("scene does not contain the required independent mesh count")
            if data["nodes"] != data["scene_nodes"] + data["mesh_nodes"] + data["material_nodes"]:
                raise ValueError("node counts do not add up")
            if (data.get("source_sha256") != sha256(fixture) or data.get("graph_roundtrip") is not True
                    or data.get("payload_roundtrip") is not True):
                raise ValueError("source digest or round-trip markers are invalid")
            graph = output_dir / "nodes.bin"
            checks["graph"] = "PASS" if graph.is_file() and not graph.is_symlink() and graph.stat().st_size > 0 else "FAIL"
            records = validate_payloads(data, output_dir)
            payloads = [output_dir / entry["filename"] for entry in records]
            evidence["payload"] = payloads[0]
            for index, payload in enumerate(payloads):
                evidence[f"payload-{index}"] = payload
            evidence["payload-directory"] = output_dir / "nodes.bin"
            checks["payload"] = "PASS"
            geometry = {
                key: data[key] for key in ("payload_vertices", "payload_triangles", "payload_materials")
            }
            geometry["source_sha256"] = data["source_sha256"]
            geometry["payload_sha256"] = sha256(payloads[0])
            geometry["payload_sha256s"] = [sha256(payload) for payload in payloads]
            geometry["payloads"] = records
            geometry_path = work / "geometry.json"
            geometry_path.write_text(json.dumps(geometry, sort_keys=True) + "\n")
            checks["geometry"] = "PASS"
        except (OSError, ValueError, json.JSONDecodeError) as error:
            errors.append(str(error))
    else:
        errors.append(f"scene process exit code: {process_code}")
    if process_log.is_file():
        evidence["process-log"] = process_log
    if scene_report.is_file():
        evidence["scene-report"] = scene_report
    diagnostic = work / "diagnostic.json"
    diagnostic.write_text(json.dumps({
        "process_exit_code": process_code, "errors": errors, "checks": checks
    }, sort_keys=True) + "\n")
    for name in CHECKS:
        evidence.setdefault(name, diagnostic)
    for name, path in list(evidence.items()):
        if not Path(path).is_file():
            evidence[name] = diagnostic
    result_path.write_text(json.dumps({
        "process_exit_code": process_code, "errors": errors, "checks": checks
    }, sort_keys=True) + "\n")
    evidence["result"] = result_path
    ue_commit = (root / "ue-commit.txt").read_text().strip() if (root / "ue-commit.txt").is_file() else "unverified"
    sources = {"ue": {"location": args.ue_root, "revision": ue_commit}}
    prerequisite = None
    try:
        prerequisite = validate_report(Path(args.ufbx_report), stage_id="ufbx-fbx-static-backend",
                                       scope=UFBX_SCOPE)
        source = prerequisite["sources"].get("ufbx")
        if not isinstance(source, dict):
            raise ValueError("ufbx prerequisite does not contain ufbx source identity")
        sources["ufbx"] = source
    except (OSError, ValueError) as error:
        errors.append(f"ufbx prerequisite: {error}")
        checks["build"] = "FAIL"
    report = write_report(
        report_path, stage_id=STAGE_ID, scope=SCOPE,
        exit_code=0 if all(value == "PASS" for value in checks.values()) and not errors else 1,
        required_checks=list(CHECKS), checks=checks, evidence=evidence, sources=sources,
        command=command, prerequisites=([{"path": args.ufbx_report,
                                           "stage_id": "ufbx-fbx-static-backend",
                                           "scope": UFBX_SCOPE}] if prerequisite else []))
    print(f"{report['status']} {STAGE_ID} report={report_path}")
    return 0 if report["status"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--ue-root", required=True)
    parser.add_argument("--ufbx-report", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--expected-meshes", type=int)
    args = parser.parse_args()
    try:
        return run(args)
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
