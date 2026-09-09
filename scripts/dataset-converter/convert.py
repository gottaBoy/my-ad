#!/usr/bin/env python3
"""Create a reproducible dataset manifest from recorded runs.

This is intentionally a manifest-first converter. It does not claim to produce
complete nuScenes annotations until the AWSIM ground-truth message schema and
camera calibration contract are locked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    runs = []
    for run in sorted(path for path in args.input.iterdir() if path.is_dir()):
        files = []
        for file in sorted(path for path in run.rglob("*") if path.is_file()):
            files.append(
                {
                    "path": str(file.relative_to(run)),
                    "size_bytes": file.stat().st_size,
                    "sha256": sha256(file),
                }
            )
        runs.append({"run_id": run.name, "files": files})

    manifest = {
        "format": "my-ad-intermediate-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(args.input),
        "runs": runs,
        "conversion_status": "manifest_only",
        "nuScenes_status": "not_implemented_until_schema_locked",
    }
    output = args.output / "dataset-manifest.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
