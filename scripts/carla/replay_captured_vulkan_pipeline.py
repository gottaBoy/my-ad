#!/usr/bin/env python3
"""Run an archived native replay binary on the capture's matching Vulkan backend."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from vulkan_device_snapshot import load, require
from vulkan_pipeline_snapshot import layout_text, validate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(capture, compiled, artifacts, backend, timeout):
    call, shader, cache = validate(load(capture / "pipeline-capture.json"), capture)
    require(compiled.is_dir(), "compiled run directory is missing")
    require(digest(compiled / "device-create.json") == digest(capture / "device-create.json"),
            "compiled device snapshot differs from target capture")
    require(digest(compiled / "shader.spv") == call["module"]["sha256"],
            "compiled shader differs from actual target module")
    require((compiled / "ue-layout.txt").read_text() == layout_text(call),
            "compiled layout differs from actual target")
    require(digest(compiled / "pipeline-cache-initial.bin") == digest(cache),
            "compiled initial cache differs from target capture")
    require((compiled / "vulkan-compute-replay").is_file()
            and not (compiled / "vulkan-compute-replay").is_symlink(), "missing regular replay program")
    root = Path(tempfile.mkdtemp(prefix=f"vulkan-compute-matched-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-",
                                 dir=artifacts))
    root.chmod(0o755)
    shutil.copytree(compiled, root / "compiled")
    shutil.copyfile(capture / "pipeline-capture.json", root / "pipeline-capture.json")
    shutil.copyfile(Path(__file__), root / Path(__file__).name)
    program = root / "compiled/vulkan-compute-replay"
    command = [
        str(program), str(root / "compiled/shader.spv"), backend, call["entry"], "create",
        "--ue-layout", str(root / "compiled/ue-layout.txt"),
        "--pipeline-cache", str(root / "compiled/pipeline-cache-initial.bin"),
        str(call["cache_initial"]["flags"]),
    ]
    (root / "command.json").write_text(json.dumps(command) + "\n")
    files = [path for path in root.rglob("*") if path.is_file()]
    (root / "inputs.sha256").write_text("".join(f"{digest(path)}  {path.relative_to(root)}\n" for path in sorted(files)))
    code = 1
    error = None
    try:
        with (root / "result.json").open("w") as output, (root / "run.log").open("w") as log:
            result = subprocess.run(command, stdout=output, stderr=log, timeout=timeout, check=False)
        require(result.returncode == 0, f"replay exited {result.returncode}")
        report = load(root / "result.json")
        require(report.get("status") == "PASS" and report.get("backend") == backend
                and report.get("entry") == call["entry"]
                and report.get("device_snapshot_sha256") == digest(capture / "device-create.json")
                and report.get("device_configuration_reused") is True
                and report.get("pipeline_cache_policy") == "captured_initial_data"
                and report.get("pipeline_cache_initial_bytes") == call["cache_initial"]["bytes"]
                and report.get("pipeline_cache_flags") == call["cache_initial"]["flags"]
                and report.get("layout_source") == "ue-layout-baseline"
                and report.get("pipeline_flags") == call["flags"]
                and report.get("required_subgroup_size") == call["required_subgroup_size"]
                and report.get("ue_exact_replay") is False,
                "replay report differs from target inputs or claims unverified exactness")
        code = 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exception:
        error = str(exception)
    (root / "decision.json").write_text(json.dumps({
        "status": "PASS" if code == 0 else "FAIL", "error": error,
        "capture": str(capture), "compiled_run": str(compiled),
        "graphics_same_cache_before": call.get("graphics_same_cache_before"),
        "scope": "isolated matching compute create with initial cache; not mutated/shared cache or runtime acceptance",
    }, indent=2) + "\n")
    print(f"{'PASS' if code == 0 else 'FAIL'} vulkan-compute-matched artifacts={root}")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--compiled-run", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, default=Path("/artifacts/carla"))
    parser.add_argument("--backend", choices=["lavapipe", "gb10"], required=True)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()
    os.umask(0o022)
    try:
        require(Path("/.dockerenv").is_file() and os.uname().machine == "aarch64",
                "native ARM64 Docker required")
        require(1 <= args.timeout <= 300, "timeout must be 1..300")
        return run(args.capture, args.compiled_run, args.artifacts, args.backend, args.timeout)
    except (OSError, ValueError) as error:
        parser.exit(2, f"matched replay rejected: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
