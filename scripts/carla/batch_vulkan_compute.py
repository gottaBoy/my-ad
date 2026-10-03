#!/usr/bin/env python3
"""Replay captured compute shaders in bounded, isolated Vulkan processes."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


REQUIRED_METADATA = {"shader", "entry", "bytes", "bindless", "wave_size", "flags"}
HASH = re.compile(r"[0-9A-F]{40}\Z")
ENTRY = re.compile(r"main_[0-9a-f]{8}_[0-9a-f]{8}\Z")


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def metadata(path, spv):
    fields = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields or key not in REQUIRED_METADATA:
            raise ValueError(f"unexpected compute metadata: {path}")
        fields[key] = value
    if set(fields) != REQUIRED_METADATA or not ENTRY.fullmatch(fields["entry"]):
        raise ValueError(f"incomplete compute metadata: {path}")
    if fields["bindless"] not in ("0", "1"):
        raise ValueError(f"invalid bindless flag: {path}")
    if any(not fields[key].isdecimal() for key in ("bytes", "wave_size", "flags")):
        raise ValueError(f"invalid numeric metadata: {path}")
    if int(fields["bytes"]) != spv.stat().st_size:
        raise ValueError(f"compute shader byte count differs: {path}")
    return fields


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def invalid_number(value):
    raise ValueError(f"invalid JSON number: {value}")


def run_one(program, validator, source, output, backend, timeout):
    output.mkdir(mode=0o755)
    shader = output / "shader.spv"
    meta = output / "metadata.txt"
    shutil.copyfile(source, shader)
    shutil.copyfile(source.with_suffix(".txt"), meta)
    if digest(source) != digest(shader) or digest(source.with_suffix(".txt")) != digest(meta):
        raise ValueError(f"source changed during capture: {source}")
    fields = metadata(meta, shader)
    command = [str(program), str(shader), backend, fields["entry"], "create"]
    (output / "command.json").write_text(json.dumps(command) + "\n")
    record = {
        "shader": source.stem, "entry": fields["entry"],
        "shader_sha256": digest(shader), "metadata_sha256": digest(meta),
        "status": "FAIL", "returncode": None, "seconds": None, "error": "",
    }
    start = time.monotonic()
    for phase, argv in (
        ("validation", [str(validator), "--target-env", "vulkan1.3", str(shader)]),
        ("create", command),
    ):
        try:
            result = subprocess.run(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                stdin=subprocess.DEVNULL, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as error:
            record["error"] = f"{phase} timed out after {timeout}s"
            (output / f"{phase}.log").write_text(
                error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes)
                else error.stderr or ""
            )
            break
        (output / f"{phase}.log").write_text(result.stderr)
        if phase == "create":
            (output / "result.json").write_text(result.stdout)
            record["returncode"] = result.returncode
        if result.returncode:
            record["error"] = f"{phase} exited {result.returncode}"
            break
        if phase == "create":
            try:
                parsed = json.loads(
                    result.stdout, object_pairs_hook=strict_object, parse_constant=invalid_number
                )
                if not (isinstance(parsed, dict) and parsed.get("status") == "PASS"
                        and parsed.get("backend") == backend
                        and parsed.get("entry") == fields["entry"]
                        and parsed.get("mode") == "create"
                        and parsed.get("ue_exact_replay") is False
                        and parsed.get("layout_source") == "spirv-reflect"
                        and parsed.get("readback_words") == 0):
                    raise ValueError("replay result does not match requested shader and scope")
            except (ValueError, TypeError) as error:
                record["error"] = f"invalid replay result: {error}"
                break
            record["status"] = "PASS"
    record["seconds"] = round(time.monotonic() - start, 6)
    return record


def run(program, validator, shader_dir, run_dir, backend, timeout):
    if backend not in ("lavapipe", "gb10") or not 0 < timeout <= 120:
        raise ValueError("invalid backend or per-shader timeout")
    if not program.is_file() or not validator.is_file():
        raise ValueError("missing replay program or SPIR-V validator")
    if not shader_dir.is_dir() or not run_dir.is_dir():
        raise ValueError("missing captured shader directory or run directory")
    if (run_dir / "batch-result.json").exists():
        raise ValueError("refusing to overwrite an existing batch result")
    shaders = sorted(shader_dir.glob("*.spv"))
    if not 1 <= len(shaders) <= 64 or any(not HASH.fullmatch(path.stem) for path in shaders):
        raise ValueError("expected 1..64 hashed captured shaders")
    if {path.stem for path in shaders} != {path.stem for path in shader_dir.glob("*.txt")}:
        raise ValueError("shader and metadata inventories differ")
    # Validate the entire inventory before running any pipeline.
    for shader in shaders:
        if not shader.is_file() or not shader.with_suffix(".txt").is_file():
            raise ValueError(f"missing regular shader or metadata: {shader}")
        metadata(shader.with_suffix(".txt"), shader)
    results = []
    for shader in shaders:
        results.append(run_one(
            program, validator, shader, run_dir / shader.stem, backend, timeout
        ))
    summary = {
        "schema_version": 1, "backend": backend, "mode": "create",
        "scope": "one new Vulkan device and pipeline per shader; not UE-exact or shared-device replay",
        "shader_count": len(results), "passed": sum(r["status"] == "PASS" for r in results),
        "status": "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL",
        "results": results,
    }
    (run_dir / "batch-result.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--shader-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=("lavapipe", "gb10"), required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    try:
        summary = run(**vars(args))
    except (OSError, ValueError) as error:
        parser.exit(2, f"batch preflight failed: {error}\n")
    print(
        f"{summary['status']} vulkan-compute-batch "
        f"backend={summary['backend']} passed={summary['passed']}/{summary['shader_count']}"
    )
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
