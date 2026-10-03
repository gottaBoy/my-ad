#!/usr/bin/env python3
"""Capture and verify a relocatable CARLA/Unreal build manifest.

The command is deliberately stdlib-only and fail-closed.  Capture requires a
native ARM64 host or container.  Inside the build container it does not require
a Docker socket.  Source roots are inspected
read-only; the manifest and binary Git patches are written to a unique
artifact directory outside the CARLA and Unreal roots.

CLI:
  capture --project-root ROOT --carla-root ROOT --ue-root ROOT
          [--artifact-root DIR] [--command ARG ...]
  verify --manifest MANIFEST --project-root ROOT

Capture exits 0 only for a complete PASS manifest.  Verify exits 0 only when
the manifest is PASS and every recorded source identity and SHA256 still
matches.  A failed capture may leave a structured FAIL manifest in the
artifact directory, but it never overwrites an existing path.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid


sys.dont_write_bytecode = True

SCHEMA_VERSION = 3
DEFAULT_ARTIFACT_RELATIVE = Path("artifacts/carla/build-manifests")
ROOT_NAMES = ("project", "carla", "ue")
REPOSITORY_NAMES = {"project": "PROJECT", "carla": "CARLA", "ue": "UE"}
HEX_SHA256 = set("0123456789abcdef")


class ManifestError(ValueError):
    """The requested manifest could not be captured or verified."""


def _require(condition, message):
    if not condition:
        raise ManifestError(message)


def _sha256(path):
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as error:
        raise ManifestError(f"cannot stat {path}: {error}") from error
    _require(stat.S_ISREG(mode), f"not a regular file: {path}")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ManifestError(f"cannot read {path}: {error}") from error
    return digest.hexdigest()

def _file_state(path):
    """Read content and metadata from one regular, non-symlink file."""
    path = Path(path)
    try:
        before = path.lstat()
    except OSError as error:
        raise ManifestError(f"cannot stat {path}: {error}") from error
    _require(stat.S_ISREG(before.st_mode), f"not a regular file: {path}")
    try:
        data = path.read_bytes()
        after = path.lstat()
    except OSError as error:
        raise ManifestError(f"cannot read {path}: {error}") from error
    _require(stat.S_ISREG(after.st_mode), f"file became non-regular: {path}")
    _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
              before.st_ctime_ns, before.st_mode) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
              after.st_ctime_ns, after.st_mode),
             f"file changed during capture: {path}")
    _require(len(data) == after.st_size, f"incomplete file read: {path}")
    return {
        "data": data,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "mode": stat.S_IMODE(after.st_mode),
        "executable": bool(after.st_mode & 0o111),
    }

def _safe_relative_name(name, label):
    _require(isinstance(name, str) and name and not Path(name).is_absolute(),
             f"{label} must be a relative path")
    parts = Path(name).parts
    _require(parts and all(part not in ("", ".", "..") for part in parts),
             f"{label} escapes its source root: {name!r}")
    _require("\x00" not in name, f"{label} contains NUL")
    return Path(name)

def _archive_file(source, destination, label):
    state = _file_state(source)
    destination = Path(destination)
    _require(not destination.exists() and not destination.is_symlink(),
             f"refusing to overwrite snapshot: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(state["data"])
        copied = _file_state(temporary)
        _require(copied["sha256"] == state["sha256"] and copied["size"] == state["size"],
                 f"{label} snapshot content mismatch")
        os.chmod(temporary, state["mode"])
        copied = _file_state(temporary)
        _require(copied["mode"] == state["mode"],
                 f"{label} snapshot mode mismatch")
        latest = _file_state(source)
        _require(latest["sha256"] == state["sha256"] and latest["mode"] == state["mode"],
                 f"{label} source changed during capture")
        # Exclusive publication also protects against a destination created mid-copy.
        os.link(temporary, destination)
        temporary.unlink()
    except (OSError, ManifestError):
        temporary.unlink(missing_ok=True)
        raise
    return {
        "path": destination,
        "sha256": state["sha256"],
        "size": state["size"],
        "mode": state["mode"],
        "executable": state["executable"],
    }


def _json_bytes(value):
    try:
        return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ManifestError(f"cannot encode manifest JSON: {error}") from error


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ManifestError(f"invalid JSON constant: {value}")


def _read_json(path):
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=_invalid_constant)
    except (OSError, UnicodeError, ValueError) as error:
        raise ManifestError(f"cannot parse manifest {path}: {error}") from error
    _require(isinstance(value, dict), "manifest must be a JSON object")
    return value


def _regular_dir(path, label):
    path = Path(path)
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ManifestError(f"missing {label} root: {path}") from error
    _require(stat.S_ISDIR(mode), f"{label} root is missing or non-directory: {path}")
    return path.resolve()


def _regular_file(path):
    path = Path(path)
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise ManifestError(f"missing file: {path}") from error
    _require(stat.S_ISREG(mode), f"not a regular file: {path}")
    return path


def _relative(path, base):
    """Return a stable POSIX path, retaining .. for a source outside the project."""
    return Path(os.path.relpath(Path(path).resolve(), Path(base).resolve())).as_posix()


def _under(path, directory):
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except ValueError:
        return False


def _run_git(root, *args, binary=False):
    try:
        result = subprocess.run(
            ["git", "-c", f"safe.directory={os.fspath(root)}", "-C", os.fspath(root), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise ManifestError(f"cannot execute git for {root}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ManifestError(f"git {' '.join(args)} failed for {root}: {detail}")
    if binary:
        return result.stdout
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError(f"git output is not UTF-8 for {root}: {error}") from error


def _git_snapshot(root, project_root, artifact_dir, repo_name, excluded_untracked):
    head = _run_git(root, "rev-parse", "--verify", "HEAD").strip()
    _require(len(head) == 40 and all(char in HEX_SHA256 for char in head.lower()),
             f"invalid git HEAD for {root}")
    branch = _run_git(root, "branch", "--show-current").strip()
    status_porcelain = _run_git(root, "status", "--porcelain=v1",
                                "--untracked-files=all")
    diff = _run_git(root, "diff", "--binary", "HEAD", binary=True)
    patch_name = f"git/{repo_name}.tracked.patch"
    patch_path = artifact_dir / patch_name
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_bytes(diff)

    untracked_names = _run_git(root, "ls-files", "--others", "--exclude-standard", "-z",
                               "--full-name", binary=True)
    try:
        names = untracked_names.decode("utf-8").split("\0")
    except UnicodeDecodeError as error:
        raise ManifestError(f"untracked path list is not UTF-8 for {root}: {error}") from error
    untracked = []
    for name in sorted(item for item in names if item):
        path = root / Path(name)
        if excluded_untracked and _under(path, excluded_untracked):
            continue
        relative = _safe_relative_name(name, f"{repo_name} untracked path")
        path = root / relative
        snapshot_path = artifact_dir / "untracked" / repo_name / relative
        snapshot = _archive_file(path, snapshot_path, f"{repo_name} untracked {name}")
        untracked.append({
            "path": relative.as_posix(),
            "snapshot": _relative(snapshot["path"], artifact_dir),
            "sha256": snapshot["sha256"],
            "size": snapshot["size"],
            "mode": snapshot["mode"],
            "executable": snapshot["executable"],
        })
    return {
        "root": _relative(root, project_root),
        "identity": f"project-relative:{_relative(root, project_root)}",
        "git": {
            "head": head,
            "branch": branch,
            "status": "clean" if not status_porcelain else "dirty",
            "status_porcelain": status_porcelain,
        },
        "tracked_diff": {
            "path": patch_name,
            "sha256": hashlib.sha256(diff).hexdigest(),
            "bytes": len(diff),
        },
        "untracked": untracked,
    }


_LOCK_SOURCE_REPOS = {"carla": "carla", "unreal_engine": "ue"}
_LOCK_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _source_lock_sources(project_root):
    """Parse source.lock pin commits without external YAML dependencies."""
    path = Path(project_root) / "config/carla/source.lock"
    if not path.exists() and not path.is_symlink():
        return None
    text = _regular_file(path).read_text(encoding="utf-8")
    pins = {}
    in_sources = False
    current = None
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"^[a-z_]+:", line):
            in_sources = line.startswith("sources:")
            current = None
            continue
        source = re.match(r"^  ([a-z_0-9]+):", line)
        if in_sources and source:
            current = source.group(1)
            continue
        commit = re.match(r"^    commit: (.+)$", line)
        if in_sources and current and commit:
            value = commit.group(1).strip().strip("'\"")
            _require(_LOCK_COMMIT_RE.match(value),
                     f"source.lock line {number} has an invalid commit: {value!r}")
            _require(current not in pins, f"source.lock repeats source {current!r}")
            pins[current] = value
    return pins


def _source_lock_drift(project_root, repositories):
    """Compare source.lock pins with the actual captured HEADs."""
    pins = _source_lock_sources(project_root)
    report = {"lock_path": "config/carla/source.lock", "pins": {}, "drifted": []}
    if pins is None:
        report["lock_present"] = False
        return report
    report["lock_present"] = True
    if not pins:
        # A lock without a sources section pins nothing; record it as present
        # but coverage-free instead of rejecting minimal fixture locks.
        text = _regular_file(Path(project_root) / report["lock_path"]).read_text(
            encoding="utf-8")
        _require(not re.search(r"^sources:\s*$", text, re.M),
                 "source.lock is present but records no usable source commits")
        return report
    for lock_name, repo_name in _LOCK_SOURCE_REPOS.items():
        record = repositories.get(repo_name)
        actual_head = None
        if isinstance(record, dict):
            git = record.get("git")
            if isinstance(git, dict):
                actual_head = git.get("head")
        pin = pins.get(lock_name)
        entry = {"lock_source": lock_name, "pin": pin, "actual_head": actual_head}
        if pin is None:
            entry["state"] = "unpinned"
        elif actual_head is None:
            entry["state"] = "untracked_repository"
        elif pin != actual_head:
            entry["state"] = "drifted"
            report["drifted"].append(lock_name)
        else:
            entry["state"] = "matches"
        report["pins"][lock_name] = entry
    unknown = sorted(set(pins) - set(_LOCK_SOURCE_REPOS))
    if unknown:
        report["uncovered_lock_sources"] = unknown
    return report


def _key_files(project_root, excluded_roots=()):
    project_root = Path(project_root).resolve()
    excluded_roots = tuple(Path(root).resolve() for root in excluded_roots)
    aliases = tuple(project_root / root.relative_to(Path("/"))
                    for root in excluded_roots if root.is_absolute())
    excluded_roots = tuple(dict.fromkeys((*excluded_roots, *aliases)))
    def is_excluded(path):
        return any(_under(path, excluded_root) for excluded_root in excluded_roots)
    paths = []
    required = (project_root / "Makefile", project_root / "compose.carla-arm64.yaml",
                project_root / "scripts/carla")
    for path in required:
        if path.name == "carla" and path.is_dir():
            continue
        _regular_file(path)

    for path in (project_root / "Makefile",):
        paths.append(path)
    for pattern in ("Makefile.*", "compose*.yaml", "compose*.yml",
                    "docker-compose*.yaml", "docker-compose*.yml"):
        paths.extend(path for path in project_root.glob(pattern) if not is_excluded(path))

    scripts = project_root / "scripts/carla"
    for path in scripts.rglob("*"):
        if is_excluded(path):
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ManifestError(f"non-regular key script: {path}")
        if path.is_file():
            paths.append(path)

    optional = [project_root / "config/carla/source.lock"]
    optional.extend(project_root.rglob("change-ledger"))
    optional.extend(project_root.rglob("change-ledger.md"))
    optional.extend(project_root.rglob("change-ledger.json"))
    optional.extend(project_root.rglob("change-ledger.yaml"))
    optional.extend(project_root.rglob("change-ledger.yml"))
    for path in optional:
        if is_excluded(path):
            continue
        if path.exists() or path.is_symlink():
            paths.append(path)

    unique = {}
    for path in paths:
        if is_excluded(path):
            continue
        key = path.resolve()
        unique[key] = path
    records = []
    for path in sorted(unique.values(), key=lambda item: _relative(item, project_root)):
        if is_excluded(path):
            continue
        _regular_file(path)
        records.append({"path": _relative(path, project_root), "sha256": _sha256(path)})
    return records


def _validate_change_ledger(project_root):
    ledger = project_root / "config/carla/change-ledger.json"
    if not ledger.exists():
        return
    data = _read_json(ledger)
    _require(type(data.get("schema_version")) is int and data["schema_version"] == 1,
             "change ledger schema_version must be integer 1")
    entries = data.get("entries")
    _require(isinstance(entries, list) and entries, "change ledger entries are required")
    ids = set()
    for entry in entries:
        _require(isinstance(entry, dict), "change ledger entry must be an object")
        identifier = entry.get("id")
        _require(isinstance(identifier, str) and identifier and identifier not in ids,
                 "change ledger IDs must be unique and nonempty")
        ids.add(identifier)
        for field in ("category", "change", "reason", "boundary"):
            _require(isinstance(entry.get(field), str) and entry[field].strip(),
                     f"change ledger field is missing: {field}")
        paths = entry.get("paths")
        _require(isinstance(paths, list) and paths, f"change ledger paths are missing: {identifier}")
        for value in paths:
            _require(isinstance(value, str) and value and not Path(value).is_absolute(),
                     f"change ledger path is invalid: {identifier}")
            _require((project_root / value).exists(), f"change ledger path is missing: {value}")
        validation = entry.get("validation")
        _require(isinstance(validation, list) and validation,
                 f"change ledger validation is missing: {identifier}")


def _machine_arch():
    value = platform.machine().lower()
    return "arm64" if value in {"aarch64", "arm64", "armv8l"} else value


def _docker_arch():
    docker = shutil.which("docker")
    _require(docker is not None, "Docker CLI is not installed")
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.Architecture}}"],
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as error:
        raise ManifestError(f"cannot execute Docker: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise ManifestError(f"Docker daemon is not reachable: {detail}")
    value = result.stdout.strip().lower()
    _require(value in {"arm64", "aarch64"}, f"Docker architecture is not ARM64: {value!r}")
    return "arm64"


def _runtime_gate():
    host_arch = _machine_arch()
    _require(host_arch == "arm64", f"host architecture is not ARM64: {host_arch}")
    if Path("/.dockerenv").is_file():
        return {"host_arch": host_arch, "docker_arch": "arm64", "container": True}
    return {"host_arch": host_arch, "docker_arch": _docker_arch(), "container": False}


def _normalized_command(project_root, argv):
    values = list(argv)
    normalized = []
    for value in values:
        if os.path.isabs(value):
            candidate = Path(value)
            if _under(candidate, project_root):
                normalized.append(_relative(candidate, project_root))
                continue
        normalized.append(value)
    return normalized


def _environment():
    values = {}
    redacted = []
    allowed_prefixes = ("CARLA_", "UE_", "UNREAL_", "NVIDIA_", "DOCKER_", "CMAKE_")
    allowed_names = {"PATH", "LANG", "LC_ALL", "LANGUAGE", "MAKEFLAGS", "CC", "CXX",
                     "DEBIAN_FRONTEND", "DOTNET_ROOT"}
    secret_pattern = re.compile(r"(TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|AUTH)", re.I)
    for key in sorted(os.environ):
        if secret_pattern.search(key):
            redacted.append(key)
        elif key in allowed_names or key.startswith(allowed_prefixes):
            values[key] = os.environ[key]
    return {"values": values, "redacted_keys": redacted}


def _toolchain_image():
    """Which toolchain image the capture ran in, as declared by the caller.

    This script runs inside the container, so it cannot ask Docker what image it
    is. The caller supplies both the reference and the resolved image ID, because
    a tag is mutable: on 2026-10-02 the image in use was eleven days older than
    its Dockerfile and nothing in a manifest could have shown that. Empty values
    mean the caller declared no image, which is recorded rather than inferred.
    """
    return {
        "reference": os.environ.get("CARLA_TOOLCHAIN_IMAGE", "").strip(),
        "id": os.environ.get("CARLA_TOOLCHAIN_IMAGE_ID", "").strip(),
    }


def _artifact_paths(project_root, artifact_root):
    artifact_root = Path(artifact_root).expanduser().resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
        final = artifact_root / name
        if not final.exists() and not final.is_symlink():
            temporary = Path(tempfile.mkdtemp(prefix=".build-manifest-", dir=artifact_root))
            temporary.chmod(0o755)
            return artifact_root, temporary, final
    raise ManifestError(f"could not allocate a unique artifact directory in {artifact_root}")


def _publish(temporary, final):
    try:
        os.rename(temporary, final)
    except FileExistsError as error:
        raise ManifestError(f"refusing to overwrite artifact directory: {final}") from error


def _manifest_template(project_root, carla_root, ue_root, runtime, command):
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "errors": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": {
            "root": ".",
            "identity": "project-root:.",
            "carla_root": _relative(carla_root, project_root),
            "ue_root": _relative(ue_root, project_root),
        },
        "repositories": {},
        "key_files": [],
        "runtime": runtime,
        "toolchain_image": _toolchain_image(),
        "command": {"argv": _normalized_command(project_root, command), "cwd": "."},
        "environment": _environment(),
    }


def capture(project_root, carla_root, ue_root, artifact_root=None, command=None):
    """Capture a manifest and return (manifest path, manifest object)."""
    project_root = _regular_dir(project_root, "project")
    carla_root = _regular_dir(carla_root, "CARLA")
    ue_root = _regular_dir(ue_root, "UE")
    artifact_root = artifact_root or project_root / DEFAULT_ARTIFACT_RELATIVE
    _require(not _under(artifact_root, carla_root) and not _under(artifact_root, ue_root),
             "artifact root must not be inside CARLA or UE source")
    artifact_root, temporary, final = _artifact_paths(project_root, artifact_root)
    manifest = None
    try:
        runtime = _runtime_gate()
        manifest = _manifest_template(project_root, carla_root, ue_root, runtime,
                                      command or [sys.executable, *sys.argv])
        manifest["repositories"]["project"] = _git_snapshot(
            project_root, project_root, temporary, "project", artifact_root)
        manifest["repositories"]["carla"] = _git_snapshot(
            carla_root, project_root, temporary, "carla", temporary)
        manifest["repositories"]["ue"] = _git_snapshot(
            ue_root, project_root, temporary, "ue", temporary)
        manifest["source_lock_drift"] = _source_lock_drift(
            project_root, manifest["repositories"])
        _validate_change_ledger(project_root)
        manifest["key_files"] = _key_files(project_root, [artifact_root])
        manifest["artifact"] = {
            "root": _relative(final, project_root),
            "manifest": "manifest.json",
        }
    except (ManifestError, OSError) as error:
        manifest = manifest or {
            "schema_version": SCHEMA_VERSION,
            "status": "FAIL",
            "errors": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project": {"root": _relative(project_root, project_root),
                        "identity": "project-root:."},
            "command": {"argv": list(command or [sys.executable, *sys.argv]), "cwd": "."},
            "environment": _environment(),
            "toolchain_image": _toolchain_image(),
        }
        manifest["status"] = "FAIL"
        manifest.setdefault("errors", []).append(str(error))
    manifest_path = temporary / "manifest.json"
    manifest_path.write_bytes(_json_bytes(manifest))
    _publish(temporary, final)
    published_manifest = final / "manifest.json"
    return published_manifest, manifest


def _resolve_recorded(project_root, value, label):
    _require(isinstance(value, str) and value and not Path(value).is_absolute(),
             f"{label} must be a relative path")
    path = (Path(project_root) / Path(value)).resolve()
    return path


def _resolve_project_recorded(project_root, value, label):
    path = _resolve_recorded(project_root, value, label)
    _require(_under(path, project_root), f"{label} escapes project root: {value}")
    return path


def _verify_git(root, record, artifact_dir, repo_name, project_root):
    _require(isinstance(record, dict), f"{repo_name} repository record is invalid")
    actual_root = _resolve_recorded(project_root, record.get("root"), f"{repo_name}.root")
    _require(actual_root == root, f"{repo_name} root identity mismatch")
    git = record.get("git")
    _require(isinstance(git, dict), f"{repo_name}.git is invalid")
    actual_head = _run_git(root, "rev-parse", "--verify", "HEAD").strip()
    actual_branch = _run_git(root, "branch", "--show-current").strip()
    actual_status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    _require(git.get("head") == actual_head, f"{repo_name} HEAD changed")
    _require(git.get("branch") == actual_branch, f"{repo_name} branch changed")
    _require(git.get("status_porcelain") == actual_status, f"{repo_name} status changed")
    expected_state = "clean" if not actual_status else "dirty"
    _require(git.get("status") == expected_state, f"{repo_name} status state changed")

    diff = _run_git(root, "diff", "--binary", "HEAD", binary=True)
    diff_record = record.get("tracked_diff")
    _require(isinstance(diff_record, dict), f"{repo_name} tracked diff is invalid")
    patch = _resolve_artifact(artifact_dir, diff_record.get("path"), f"{repo_name} patch")
    _require(_sha256(patch) == diff_record.get("sha256"), f"{repo_name} patch artifact changed")
    _require(patch.read_bytes() == diff, f"{repo_name} tracked diff changed")
    _require(diff_record.get("bytes") == len(diff), f"{repo_name} tracked diff size changed")

    expected = record.get("untracked")
    _require(isinstance(expected, list), f"{repo_name} untracked record is invalid")
    actual = []
    for item in expected:
        _require(isinstance(item, dict), f"{repo_name} untracked entry is invalid")
        _require(type(item.get("size")) is int and item["size"] >= 0
                 and type(item.get("mode")) is int and 0 <= item["mode"] <= 0o7777
                 and type(item.get("executable")) is bool,
                 f"{repo_name} untracked metadata types are invalid")
        relative = _safe_relative_name(item.get("path"), f"{repo_name} untracked path")
        path = root / relative
        source = _file_state(path)
        for field in ("sha256", "size", "mode", "executable"):
            _require(source[field] == item.get(field),
                     f"{repo_name} untracked file metadata changed: {relative}")
        snapshot = _resolve_artifact(artifact_dir, item.get("snapshot"),
                                     f"{repo_name} untracked snapshot")
        archived = _file_state(snapshot)
        for field in ("sha256", "size", "mode", "executable"):
            _require(archived[field] == item.get(field),
                     f"{repo_name} untracked snapshot metadata changed: {relative}")
        _require(archived["data"] == source["data"],
                 f"{repo_name} untracked snapshot content changed: {relative}")
        actual.append({"path": relative.as_posix(), "sha256": source["sha256"],
                        "size": source["size"], "mode": source["mode"],
                        "executable": source["executable"]})
    names = _run_git(root, "ls-files", "--others", "--exclude-standard", "-z",
                     "--full-name", binary=True).decode("utf-8").split("\0")
    current = []
    for name in sorted(item for item in names if item):
        relative = _safe_relative_name(name, f"{repo_name} untracked path")
        path = root / relative
        if _under(path, artifact_dir):
            continue
        state = _file_state(path)
        current.append({"path": relative.as_posix(), "sha256": state["sha256"],
                        "size": state["size"], "mode": state["mode"],
                        "executable": state["executable"]})
    expected_live = []
    for item in expected:
        relative = _safe_relative_name(item.get("path"), f"{repo_name} untracked path")
        expected_live.append({"path": relative.as_posix(), "sha256": item.get("sha256"),
                             "size": item.get("size"), "mode": item.get("mode"),
                             "executable": item.get("executable")})
    _require(current == expected_live, f"{repo_name} untracked file set changed")


def _resolve_artifact(artifact_dir, value, label):
    relative = _safe_relative_name(value, label)
    path = Path(artifact_dir)
    for part in relative.parts:
        path = path / part
        _require(not path.is_symlink(), f"{label} contains a symlink: {path}")
    _require(_under(path, artifact_dir), f"{label} escapes artifact directory")
    return _regular_file(path)


def verify(manifest_path, project_root):
    """Re-hash all recorded inputs and return the verified manifest."""
    manifest_path = _regular_file(manifest_path).resolve()
    project_root = _regular_dir(project_root, "project")
    manifest = _read_json(manifest_path)
    _require(type(manifest.get("schema_version")) is int
             and manifest["schema_version"] == SCHEMA_VERSION, "unsupported manifest schema")
    _require(manifest.get("status") == "PASS" and manifest.get("errors") == [],
             "only a clean PASS manifest can verify")
    runtime = manifest.get("runtime")
    _require(isinstance(runtime, dict) and runtime.get("host_arch") == "arm64"
             and runtime.get("docker_arch") == "arm64", "manifest runtime gate is not ARM64")
    _require(_runtime_gate() == runtime, "current Docker/ARM64 runtime differs")
    toolchain = manifest.get("toolchain_image")
    _require(isinstance(toolchain, dict) and set(toolchain) == {"reference", "id"},
             "toolchain_image record is invalid")
    # Only the resolved ID is compared: a tag is a pointer that legitimately moves,
    # and moving one must not make an honest manifest unverifiable. The reference is
    # recorded so a reader can see which tag was in play at capture time.
    _require(toolchain["id"] == _toolchain_image()["id"], "toolchain image changed")
    project = manifest.get("project")
    _require(isinstance(project, dict) and project.get("root") == "."
             and project.get("identity") == "project-root:.", "project identity is invalid")
    carla = _resolve_recorded(project_root, project.get("carla_root"), "project.carla_root")
    ue = _resolve_recorded(project_root, project.get("ue_root"), "project.ue_root")
    _regular_dir(carla, "CARLA")
    _regular_dir(ue, "UE")
    artifact = manifest.get("artifact")
    _require(isinstance(artifact, dict), "artifact identity is invalid")
    artifact_dir = _resolve_recorded(project_root, artifact.get("root"), "artifact.root")
    _require(artifact_dir == manifest_path.parent, "manifest artifact location changed")
    _require(artifact.get("manifest") == "manifest.json", "manifest artifact name is invalid")
    # Check the drift record before git state so a lock edit fails with an
    # explicit drift reason instead of a generic untracked-set difference.
    drift = manifest.get("source_lock_drift")
    _require(isinstance(drift, dict), "source_lock_drift record is invalid")
    expected_drift = _source_lock_drift(project_root, manifest["repositories"])
    _require(drift == expected_drift, "source_lock drift record changed")
    _verify_git(project_root, manifest["repositories"]["project"], artifact_dir, "project", project_root)
    _verify_git(carla, manifest["repositories"]["carla"], artifact_dir, "carla", project_root)
    _verify_git(ue, manifest["repositories"]["ue"], artifact_dir, "ue", project_root)

    key_files = manifest.get("key_files")
    _require(isinstance(key_files, list), "key_files is invalid")
    for item in key_files:
        _require(isinstance(item, dict), "key file record is invalid")
        path = _resolve_project_recorded(project_root, item.get("path"), "key file")
        _require(_sha256(path) == item.get("sha256"), f"key file changed: {item.get('path')}")
    _require(_key_files(project_root, [artifact_dir.parent]) == key_files,
             "key file set or hash changed")
    return manifest


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    capture_parser = actions.add_parser("capture")
    capture_parser.add_argument("--project-root", type=Path, required=True)
    capture_parser.add_argument("--carla-root", type=Path, required=True)
    capture_parser.add_argument("--ue-root", type=Path, required=True)
    capture_parser.add_argument("--artifact-root", type=Path)
    capture_parser.add_argument("--command", nargs=argparse.REMAINDER)
    verify_parser = actions.add_parser("verify")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "capture":
            command = args.command or [sys.executable, *sys.argv]
            path, manifest = capture(args.project_root, args.carla_root, args.ue_root,
                                     args.artifact_root, command)
            print(path)
            return 0 if manifest.get("status") == "PASS" else 1
        verify(args.manifest, args.project_root)
        print("PASS")
        return 0
    except (ManifestError, OSError, KeyError, TypeError, UnicodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
