#!/usr/bin/env python3
"""Freeze or verify the working-tree delta of the two CARLA forks.

The forks carry uncommitted tracked modifications, and those modifications are
what the staged binaries are built from: on 2026-10-03 the CARLA checkout had 1
tracked-modified file and the Unreal checkout had 92, of which 83 are named by no
patch under scripts/carla/patches. `make carla-manifest` can replay them, but its
copy lives under artifacts/ and is point-in-time, so nothing tracked in the
repository described the tree a fresh checkout at the recorded HEAD would miss.

`freeze` writes each fork's `git diff --binary HEAD` beside the curated patches;
`verify` recomputes the same diff and compares bytes, so a delta that moved on is
reported instead of silently inherited. The invocation matches
capture_build_manifest.py exactly, so a digest computed here and a digest recorded
there mean the same thing.
"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


SOURCES = ("carla", "ue")
DEFAULT_REPOS = {"carla": "third_party/carla", "ue": "third_party/unreal-engine"}
DEFAULT_PATCH_DIR = "scripts/carla/patches"
PATCH_NAMES = {
    "carla": "fork-working-tree-delta-carla.patch",
    "ue": "fork-working-tree-delta-ue.patch",
}


class DeltaError(Exception):
    """The delta could not be read or the patch directory is unusable."""


def _git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise DeltaError(f"git {' '.join(args)} failed in {repo}: {message}")
    return result.stdout


def fork_delta(repo):
    """The tracked delta relative to HEAD, exactly as the manifest records it."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise DeltaError(f"not a git checkout: {repo}")
    return _git(repo, "diff", "--binary", "HEAD")


def changed_files(diff):
    return sum(1 for line in diff.splitlines() if line.startswith(b"diff --git "))


def patch_path(patch_dir, name):
    return Path(patch_dir) / PATCH_NAMES[name]


def freeze(repos, patch_dir):
    patch_dir = Path(patch_dir)
    if patch_dir.exists() and not patch_dir.is_dir():
        raise DeltaError(f"patch directory is not a directory: {patch_dir}")
    patch_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for name in SOURCES:
        diff = fork_delta(repos[name])
        path = patch_path(patch_dir, name)
        path.write_bytes(diff)
        records.append({
            "source": name,
            "path": str(path),
            "sha256": hashlib.sha256(diff).hexdigest(),
            "bytes": len(diff),
            "files": changed_files(diff),
        })
    return records


def verify(repos, patch_dir):
    """Return (rows, records). A row is (source, field, frozen, live)."""
    rows, records = [], []
    for name in SOURCES:
        path = patch_path(patch_dir, name)
        if not path.is_file():
            raise DeltaError(f"frozen delta is missing: {path}")
        frozen = path.read_bytes()
        live = fork_delta(repos[name])
        records.append({
            "source": name,
            "path": str(path),
            "sha256": hashlib.sha256(frozen).hexdigest(),
            "bytes": len(frozen),
            "files": changed_files(frozen),
        })
        if frozen != live:
            rows.append((name, "bytes", len(frozen), len(live)))
            rows.append((name, "sha256",
                         hashlib.sha256(frozen).hexdigest(),
                         hashlib.sha256(live).hexdigest()))
            rows.append((name, "files", changed_files(frozen), changed_files(live)))
    return rows, records


def _report(records):
    for record in sorted(records, key=lambda item: item["source"]):
        print(f"{record['source']} files={record['files']} "
              f"bytes={record['bytes']} sha256={record['sha256'][:16]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("freeze", "verify"))
    parser.add_argument("--carla-repo", default=DEFAULT_REPOS["carla"])
    parser.add_argument("--ue-repo", default=DEFAULT_REPOS["ue"])
    parser.add_argument("--patch-dir", default=DEFAULT_PATCH_DIR)
    args = parser.parse_args(argv)

    repos = {"carla": args.carla_repo, "ue": args.ue_repo}
    try:
        if args.action == "freeze":
            records = freeze(repos, args.patch_dir)
            _report(records)
            print(f"PASS delta-frozen dir={args.patch_dir}")
            return 0
        rows, records = verify(repos, args.patch_dir)
    except DeltaError as error:
        print(f"FAIL delta {error}")
        return 2

    _report(records)
    if rows:
        for source, field, frozen, live in rows:
            print(f"DRIFT {source}.{field}: frozen={frozen!r} live={live!r}")
        print(f"FAIL delta-drift dir={args.patch_dir} fields={len(rows)}")
        return 1
    print(f"PASS delta-verified dir={args.patch_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
