#!/usr/bin/env python3
"""Derive or verify the provenance of the two CARLA forks.

The runtime provenance consumed by the probes records a `revision` per source,
but a 40-hex commit is not a faithful identifier for these trees: the CARLA and
Unreal checkouts are forks that carry tracked working-tree modifications on top
of their HEAD commit (the ARM64/editor-only patches and the diagnostic
instrumentation), and those modifications are what the staged binaries are
actually built from. Recording HEAD alone therefore names code that did not
produce the artifact.

This script derives the full source identity from the live trees - HEAD, branch,
subject, the number of tracked-modified and untracked files, and a sha256 over
the tracked diff - and can compare a previously recorded provenance against the
live trees so drift is reported instead of silently inherited.

The default locations match what the probes record inside the container
(`/workspace/carla`, `/workspace/unreal-engine`) while the default repositories
are the host-side paths, because the probe consumes the recorded file, not the
derivation.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


SCHEMA_VERSION = 1
SOURCES = ("carla", "ue")
DEFAULT_REPOS = {"carla": "third_party/carla", "ue": "third_party/unreal-engine"}
DEFAULT_LOCATIONS = {"carla": "/workspace/carla", "ue": "/workspace/unreal-engine"}


class ProvenanceError(Exception):
    """The provenance could not be derived or is not well formed."""


def _git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ProvenanceError(f"git {' '.join(args)} failed in {repo}: {message}")
    return result.stdout


def _head_subject(repo):
    return _git(repo, "log", "-1", "--format=%s").decode("utf-8", "replace").strip()


def _branch(repo):
    """HEAD's branch, or `detached` when the checkout is not on one."""
    result = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        return "detached"
    return result.stdout.decode("utf-8", "replace").strip() or "detached"


def describe_source(repo, location):
    """Identify one fork by its commit *and* its uncommitted tracked delta."""
    repo = Path(repo)
    if not (repo / ".git").exists():
        raise ProvenanceError(f"not a git checkout: {repo}")
    # Same invocation as capture_build_manifest.py and freeze_fork_delta.py, so a
    # digest means one thing everywhere. Without --binary a binary edit would be
    # reduced to "Binary files differ" and two tools could disagree.
    diff = _git(repo, "diff", "--binary", "HEAD")
    status = _git(repo, "status", "--porcelain")
    tracked, untracked = 0, 0
    for line in status.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        if line.startswith("??"):
            untracked += 1
        else:
            tracked += 1
    return {
        "location": location,
        "revision": _git(repo, "rev-parse", "HEAD").decode("ascii").strip(),
        "branch": _branch(repo),
        "subject": _head_subject(repo),
        "tracked_dirty_files": tracked,
        "untracked_files": untracked,
        "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
    }


def build_provenance(repos, locations):
    return {
        "schema_version": SCHEMA_VERSION,
        "sources": {
            name: describe_source(repos[name], locations[name]) for name in SOURCES
        },
    }


def render(provenance):
    return json.dumps(provenance, indent=2, sort_keys=True) + "\n"


def load_recorded(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProvenanceError(f"cannot read recorded provenance {path}: {error}")
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ProvenanceError(f"{path}: schema_version must be {SCHEMA_VERSION}")
    sources = data.get("sources")
    if not isinstance(sources, dict):
        raise ProvenanceError(f"{path}: sources must be an object")
    for name in SOURCES:
        source = sources.get(name)
        if not isinstance(source, dict):
            raise ProvenanceError(f"{path}: missing source {name}")
        for field in ("location", "revision"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise ProvenanceError(f"{path}: {name}.{field} must be nonempty")
    return data


def compare(recorded, live):
    """Return (source, field, recorded, live, note) rows for every divergence.

    Only fields the recording actually carries are checked, so an older
    provenance that names nothing but `location` and `revision` can still be
    verified without inventing fields it never claimed.
    """
    rows = []
    for name in SOURCES:
        current = live["sources"][name]
        for field, expected in sorted(recorded["sources"][name].items()):
            if field not in current:
                rows.append((name, field, expected, None, "not derivable"))
            elif current[field] != expected:
                rows.append((name, field, expected, current[field], "drift"))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--carla-repo", default=DEFAULT_REPOS["carla"])
    parser.add_argument("--ue-repo", default=DEFAULT_REPOS["ue"])
    parser.add_argument("--carla-location", default=DEFAULT_LOCATIONS["carla"])
    parser.add_argument("--ue-location", default=DEFAULT_LOCATIONS["ue"])
    parser.add_argument("--output", help="write the derived provenance here")
    parser.add_argument("--verify",
                        help="compare this recorded provenance against the live trees")
    args = parser.parse_args(argv)

    repos = {"carla": args.carla_repo, "ue": args.ue_repo}
    locations = {"carla": args.carla_location, "ue": args.ue_location}
    try:
        live = build_provenance(repos, locations)
        recorded = load_recorded(args.verify) if args.verify else None
    except ProvenanceError as error:
        print(f"FAIL provenance {error}")
        return 2

    if recorded is not None:
        rows = compare(recorded, live)
        for name, field, expected, actual, note in rows:
            print(f"DRIFT {name}.{field}: recorded={expected!r} "
                  f"live={actual!r} ({note})")
        if rows:
            print(f"FAIL provenance-drift path={args.verify} fields={len(rows)}")
            return 1
        print(f"PASS provenance-verified path={args.verify}")
        return 0

    text = render(live)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"PASS provenance-recorded path={args.output}")
        for name in SOURCES:
            source = live["sources"][name]
            print(f"{name} revision={source['revision']} branch={source['branch']} "
                  f"tracked_dirty={source['tracked_dirty_files']} "
                  f"untracked={source['untracked_files']} "
                  f"tracked_diff={source['tracked_diff_sha256'][:16]}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
