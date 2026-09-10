#!/usr/bin/env python3
"""Create a deterministic integrity manifest from recorded runs.

This is intentionally a manifest-first converter. It does not claim to produce
complete nuScenes annotations until the AWSIM ground-truth message schema and
camera calibration contract are locked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(input_root: Path, source_id: str) -> dict[str, object]:
    if not input_root.is_dir():
        raise ValueError(f"input directory not found: {input_root}")

    runs = []
    for run in sorted(path for path in input_root.iterdir() if path.is_dir()):
        files = []
        for file in sorted(path for path in run.rglob("*") if path.is_file()):
            if file.is_symlink():
                continue
            files.append(
                {
                    "path": file.relative_to(run).as_posix(),
                    "size_bytes": file.stat().st_size,
                    "sha256": sha256(file),
                }
            )
        runs.append({"run_id": run.name, "files": files})

    return {
        "format": "my-ad-intermediate-v1",
        "source_id": source_id,
        "runs": runs,
        "conversion_status": "manifest_only",
        "nuScenes_status": "not_implemented_until_schema_locked",
    }


def write_atomic_json(output: Path, payload: dict[str, object]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temp_path = Path(stream.name)
        json.dump(payload, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp_path.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-id", default="awsim")
    args = parser.parse_args()

    try:
        manifest = build_manifest(args.input, args.source_id)
    except ValueError as error:
        parser.error(str(error))

    output = args.output / "dataset-manifest.json"
    write_atomic_json(output, manifest)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
