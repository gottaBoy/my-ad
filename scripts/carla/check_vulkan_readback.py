#!/usr/bin/env python3
"""Verify native GB10 image readback, without claiming UE or sensor coverage."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import zlib

from stage_report import write_report


STAGE_ID = "dgx-vulkan-readback"
SCOPE = "Native GB10 Vulkan render-pass clear/readback; not UE shaders or CARLA sensors"
CHECKS = ("native", "device", "frame-0", "frame-1", "changing-frames")
WIDTH, HEIGHT = 64, 48


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def native_elf(path):
    with Path(path).open("rb") as stream:
        header = stream.read(20)
    return (len(header) == 20 and header[:6] == b"\x7fELF\x02\x01"
            and int.from_bytes(header[18:20], "little") == 183)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError(f"invalid JSON number: {value}")


def validate_device(value):
    expected = {
        "schema_version": 1, "device_name": "NVIDIA GB10", "vendor_id": 0x10DE,
        "graphics_queue": True, "width": WIDTH, "height": HEIGHT, "frames": 2,
    }
    if not isinstance(value, dict):
        raise ValueError("device report is not an object")
    for key, item in expected.items():
        if type(value.get(key)) is not type(item) or value[key] != item:
            raise ValueError(f"unexpected {key}: {value.get(key)!r}")
    for key in ("api_version", "driver_version", "device_id", "queue_family"):
        item = value.get(key)
        if type(item) is not int or not 0 <= item <= 0xFFFFFFFF:
            raise ValueError(f"invalid {key}")
    if value["api_version"] < (1 << 22 | 2 << 12) or value["driver_version"] == 0:
        raise ValueError("Vulkan 1.2+ and a nonzero driver version are required")
    for key in ("runtime_descriptor_array", "descriptor_binding_partially_bound",
                "sampled_image_update_after_bind"):
        if type(value.get(key)) is not bool:
            raise ValueError(f"missing feature observation: {key}")


def validate_frame(raw, frame):
    if frame not in (0, 1):
        raise ValueError("unexpected frame index")
    if len(raw) != WIDTH * HEIGHT * 4:
        raise ValueError("readback dimensions/byte count mismatch")
    colors = ((b"\xff\x00\x00\xff", b"\x00\x00\xff\xff"),
              (b"\x00\xff\x00\xff", b"\xff\xff\xff\xff"))
    left, right = colors[frame]
    expected = (left * (WIDTH // 2) + right * (WIDTH // 2)) * HEIGHT
    if raw != expected:
        different = sum(a != b for a, b in zip(raw, expected))
        raise ValueError(f"GPU readback differs from clear rectangles in {different} bytes")
    return {"width": WIDTH, "height": HEIGHT, "pixels_checked": WIDTH * HEIGHT,
            "sha256": hashlib.sha256(raw).hexdigest()}


def write_png(path, rgba):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    rows = b"".join(b"\0" + rgba[y * WIDTH * 4:(y + 1) * WIDTH * 4] for y in range(HEIGHT))
    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )


def run(program, run_dir, timeout):
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "stage-report.json"
    if report_path.exists() or report_path.is_symlink():
        raise ValueError("refusing to overwrite an existing stage report")
    work = Path(tempfile.mkdtemp(prefix="readback-", dir=run_dir))
    work.chmod(0o755)
    program = Path(program).resolve()
    source = Path(__file__).with_name("vulkan-readback.c")
    checks = dict.fromkeys(CHECKS, "FAIL")
    evidence = {name: work / f"{name}.json" for name in CHECKS}
    source_hash = digest(source)
    sources = {"probe": {"location": str(source), "revision": f"sha256:{source_hash}"}}
    command = [str(program), str(work)]
    dump(work / "command.json", command)
    evidence.update({"command": work / "command.json", "source": source,
                     "evaluator": Path(__file__), "stage-writer": Path(__file__).with_name("stage_report.py")})
    code, error = 1, ""
    try:
        snapshot = {"machine": platform.machine(), "docker": Path("/.dockerenv").is_file(),
                    "program": str(program), "aarch64_elf": native_elf(program),
                    "program_sha256": digest(program), "uname": list(platform.uname())}
        dump(evidence["native"], snapshot)
        if (snapshot["machine"] not in ("aarch64", "arm64") or not snapshot["docker"]
                or not snapshot["aarch64_elf"]):
            raise ValueError("native ARM64 Docker and an AArch64 ELF are required")
        checks["native"] = "PASS"
        evidence["program"] = program
        with (work / "device.raw.json").open("wb") as stdout, (work / "process.log").open("wb") as stderr:
            result = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=timeout,
                                    env={k: v for k, v in os.environ.items() if k != "LD_LIBRARY_PATH"})
        evidence.update({"device.raw": work / "device.raw.json", "process.log": work / "process.log"})
        if result.returncode:
            code = result.returncode
            raise ValueError(f"Vulkan process failed with exit code {code}")
        device = json.loads((work / "device.raw.json").read_text(),
                            object_pairs_hook=unique_object, parse_constant=invalid_constant)
        validate_device(device)
        dump(evidence["device"], device)
        checks["device"] = "PASS"
        frames = []
        for frame in range(2):
            path = work / f"frame-{frame}.rgba"
            raw = path.read_bytes()
            evidence[f"frame-{frame}.raw"] = path
            info = validate_frame(raw, frame)
            dump(evidence[f"frame-{frame}"], info)
            png = work / f"frame-{frame}.png"
            write_png(png, raw)
            evidence[f"frame-{frame}.png"] = png
            checks[f"frame-{frame}"] = "PASS"
            frames.append(raw)
        changed = sum(frames[0][i:i + 4] != frames[1][i:i + 4]
                      for i in range(0, len(frames[0]), 4))
        dump(evidence["changing-frames"], {"changed_pixels": changed})
        if changed != WIDTH * HEIGHT:
            raise ValueError("readback frames did not change as specified")
        checks["changing-frames"] = "PASS"
        if digest(program) != snapshot["program_sha256"] or digest(source) != source_hash:
            checks["native"] = "FAIL"
            raise ValueError("probe inputs changed during execution")
        code = 0
    except subprocess.TimeoutExpired:
        code, error = 124, f"Vulkan process exceeded {timeout} seconds"
    except (OSError, ValueError) as exc:
        error = str(exc)
    for name, path in list(evidence.items()):
        if name in CHECKS and not path.exists():
            dump(path, {"error": error or "check did not run"})
    for name in ("process.log", "device.raw.json"):
        path = work / name
        if path.exists():
            evidence[name] = path
    dump(work / "result.json", {"exit_code": code, "error": error, "checks": checks})
    evidence["result"] = work / "result.json"
    for name in ("build.log", "build-command.json", "inputs.sha256"):
        if (run_dir / name).is_file():
            evidence[f"build.{name}"] = run_dir / name
    report = write_report(report_path, stage_id=STAGE_ID, scope=SCOPE, exit_code=code,
                          required_checks=list(CHECKS), checks=checks, evidence=evidence,
                          sources=sources, command=command)
    print(f"{report['status']} {STAGE_ID} artifacts={run_dir} error={error}")
    return 0 if report["status"] == "PASS" else 1


def positive(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=positive, default=45)
    args = parser.parse_args()
    try:
        return run(args.program, args.run_dir, args.timeout)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
