"""Focused harness tests for the CARLA build manifest subsystem."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/carla/capture_build_manifest.py"
spec = importlib.util.spec_from_file_location("capture_build_manifest", SCRIPT)
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class BuildManifestTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.carla = self.root / "workspace/carla"
        self.ue = self.root / "workspace/unreal-engine"
        self.artifacts = self.root / "artifacts"
        self._git(self.project, "init", "-q")
        self._git(self.carla, "init", "-q")
        self._git(self.ue, "init", "-q")
        for source in (self.carla, self.ue):
            (source / "tracked.txt").write_text(f"{source.name}\n", encoding="utf-8")
            self._git(source, "add", ".")
            self._git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "-qm", "base")
        (self.project / "Makefile").write_text("carla-build:\n", encoding="utf-8")
        (self.project / "compose.carla-arm64.yaml").write_text("services: {}\n", encoding="utf-8")
        (self.project / "scripts/carla").mkdir(parents=True)
        (self.project / "scripts/carla/build.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.project / "config/carla").mkdir(parents=True)
        (self.project / "config/carla/source.lock").write_text("schema: 1\n", encoding="utf-8")
        self._git(self.project, "add", ".")
        self._git(self.project, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                  "commit", "-qm", "project base")
        self.fake_docker = self.root / "bin/docker"
        self.fake_docker.parent.mkdir()
        self.fake_docker.write_text("#!/bin/sh\nprintf 'arm64\\n'\n", encoding="utf-8")
        self.fake_docker.chmod(0o755)
        self.original_path = os.environ.get("PATH", "")
        self.test_path = os.pathsep.join((str(self.fake_docker.parent), self.original_path))
        self.docker_path = mock.patch.dict(os.environ, {"PATH": self.test_path})
        self.docker_path.start()
        self.addCleanup(self.docker_path.stop)
        self.arch = mock.patch.object(manifest, "_machine_arch", return_value="arm64")
        self.arch.start()
        self.addCleanup(self.arch.stop)

    def _git(self, cwd, *args):
        cwd = Path(cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def capture(self):
        path, data = manifest.capture(self.project, self.carla, self.ue, self.artifacts,
                                      ["capture-build-manifest", "--fixture"])
        self.manifest_path = path
        return data

    def test_capture_records_clean_identity_and_relocatable_inputs(self):
        data = self.capture()
        self.assertEqual("PASS", data["status"])
        self.assertEqual(".", data["project"]["root"])
        self.assertEqual("../workspace/carla", data["project"]["carla_root"])
        self.assertEqual("../workspace/unreal-engine", data["project"]["ue_root"])
        self.assertEqual(["capture-build-manifest", "--fixture"], data["command"]["argv"])
        self.assertEqual("clean", data["repositories"]["carla"]["git"]["status"])
        self.assertEqual("", data["repositories"]["carla"]["git"]["status_porcelain"])
        self.assertEqual([], data["repositories"]["carla"]["untracked"])
        self.assertEqual(hashlib.sha256(b"").hexdigest(),
                         data["repositories"]["carla"]["tracked_diff"]["sha256"])
        self.assertEqual(manifest.verify(self.manifest_path, self.project)["status"], "PASS")

    def test_dirty_staged_and_untracked_are_recorded_and_verify(self):
        (self.carla / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        self._git(self.carla, "add", "tracked.txt")
        (self.carla / "new.bin").write_bytes(b"\x00\xffnew\n")
        (self.carla / "new.bin").chmod(0o644)
        (self.ue / "tracked.txt").write_text("ue dirty\n", encoding="utf-8")
        data = self.capture()
        carla = data["repositories"]["carla"]
        self.assertEqual("dirty", carla["git"]["status"])
        self.assertIn("M  tracked.txt", carla["git"]["status_porcelain"])
        self.assertEqual(1, len(carla["untracked"]))
        untracked = carla["untracked"][0]
        self.assertEqual("new.bin", untracked["path"])
        self.assertEqual("untracked/carla/new.bin", untracked["snapshot"])
        self.assertEqual(hashlib.sha256(b"\x00\xffnew\n").hexdigest(), untracked["sha256"])
        self.assertEqual(6, untracked["size"])
        self.assertEqual(0o644, untracked["mode"])
        self.assertFalse(untracked["executable"])
        snapshot = self.manifest_path.parent / untracked["snapshot"]
        self.assertEqual(b"\x00\xffnew\n", snapshot.read_bytes())
        self.assertNotEqual(carla["tracked_diff"]["sha256"],
                            hashlib.sha256(b"").hexdigest())
        manifest.verify(self.manifest_path, self.project)

    def test_rehash_rejects_rewritten_tracked_untracked_and_key_files(self):
        self.capture()
        (self.carla / "tracked.txt").write_text("rewritten\n", encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)
        (self.carla / "tracked.txt").write_text("carla\n", encoding="utf-8")
        (self.project / "Makefile").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)

    def test_untracked_rewrite_and_new_file_fail_strictly(self):
        (self.carla / "extra").write_bytes(b"one")
        self.capture()
        (self.carla / "extra").write_bytes(b"two")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)
        (self.carla / "extra").write_bytes(b"one")
        (self.carla / "another").write_bytes(b"new")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)
    def test_snapshot_missing_tampered_and_mode_change_fail_verify(self):
        extra = self.carla / "binary.bin"
        extra.write_bytes(b"\x00\xff\x00binary")
        self.capture()
        snapshot = self.manifest_path.parent / "untracked/carla/binary.bin"
        snapshot.unlink()
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)
        extra.write_bytes(b"\x00\xff\x00binary")
        path, _ = manifest.capture(self.project, self.carla, self.ue, self.artifacts)
        snapshot = path.parent / "untracked/carla/binary.bin"
        snapshot.write_bytes(b"tampered")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(path, self.project)
        extra.chmod(0o755)
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(path, self.project)
    def test_capture_failure_during_archive_is_structured_and_not_half_published(self):
        (self.carla / "write-failure.bin").write_bytes(b"payload")
        with mock.patch.object(manifest, "_archive_file", side_effect=OSError("disk full")):
            path, data = manifest.capture(self.project, self.carla, self.ue, self.artifacts)
        self.assertEqual("FAIL", data["status"])
        self.assertIn("disk full", data["errors"])
        self.assertTrue(path.is_file())
        self.assertFalse(any(path.name.endswith(".tmp") for path in path.parent.iterdir()))
    def test_source_change_during_archive_fails_capture(self):
        extra = self.carla / "racy.bin"
        extra.write_bytes(b"before")
        original = manifest._file_state
        changed = False
        def race(path):
            nonlocal changed
            state = original(path)
            if Path(path) == extra and not changed:
                extra.write_bytes(b"after")
                changed = True
            return state
        with mock.patch.object(manifest, "_file_state", side_effect=race):
            path, data = manifest.capture(self.project, self.carla, self.ue, self.artifacts)
        self.assertEqual("FAIL", data["status"])
        self.assertIn("changed during capture", " ".join(data["errors"]))
        self.assertTrue(path.is_file())

    def test_source_mode_change_fails_with_intact_archive_content(self):
        extra = self.carla / "run.sh"
        extra.write_bytes(b"script")
        extra.chmod(0o644)
        data = self.capture()
        record = data["repositories"]["carla"]["untracked"][0]
        snapshot = self.manifest_path.parent / record["snapshot"]
        self.assertEqual(extra.read_bytes(), snapshot.read_bytes())
        manifest.verify(self.manifest_path, self.project)
        extra.chmod(0o755)
        with self.assertRaisesRegex(manifest.ManifestError, "metadata changed"):
            manifest.verify(self.manifest_path, self.project)

    def test_same_content_snapshot_symlink_is_rejected(self):
        extra = self.carla / "source.bin"
        extra.write_bytes(b"source bytes")
        data = self.capture()
        snapshot = self.manifest_path.parent / data["repositories"]["carla"]["untracked"][0]["snapshot"]
        alternative = snapshot.with_name("same-content.bin")
        alternative.write_bytes(snapshot.read_bytes())
        alternative.chmod(snapshot.stat().st_mode & 0o7777)
        snapshot.unlink()
        snapshot.symlink_to(alternative.name)
        with self.assertRaisesRegex(manifest.ManifestError, "symlink"):
            manifest.verify(self.manifest_path, self.project)

    def test_publication_never_overwrites_a_concurrently_created_snapshot(self):
        source = self.root / "source"
        destination = self.root / "destination"
        source.write_bytes(b"source")
        original_link = manifest.os.link

        def race(first, second):
            Path(second).write_bytes(b"preserve-existing")
            return original_link(first, second)

        with mock.patch.object(manifest.os, "link", side_effect=race):
            with self.assertRaises(FileExistsError):
                manifest._archive_file(source, destination, "race")
        self.assertEqual(b"preserve-existing", destination.read_bytes())
        self.assertFalse(list(self.root.glob(".destination.*.tmp")))
    def test_snapshot_path_escape_and_legacy_hash_only_schema_are_rejected(self):
        (self.carla / "escape.bin").write_bytes(b"data")
        self.capture()
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        raw["repositories"]["carla"]["untracked"][0]["snapshot"] = "../outside"
        self.manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)
        raw["schema_version"] = 1
        self.manifest_path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)

    def test_capture_rejects_repeat_missing_root_and_nonregular_root(self):
        self.capture()
        second_path, second_data = manifest.capture(self.project, self.carla, self.ue, self.artifacts)
        self.assertEqual("PASS", second_data["status"])
        self.assertNotEqual(self.manifest_path, second_path)
        with self.assertRaises(manifest.ManifestError):
            manifest.capture(self.project, self.carla / "missing", self.ue, self.artifacts)
        bad = self.root / "not-a-root"
        bad.write_text("file\n", encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            manifest.capture(self.project, bad, self.ue, self.artifacts)

    def test_capture_fails_on_non_arm_or_non_docker(self):
        with mock.patch.object(manifest, "_machine_arch", return_value="x86_64"):
            path, data = manifest.capture(self.project, self.carla, self.ue, self.artifacts)
            self.assertEqual("FAIL", data["status"])
            self.assertIn("not ARM64", data["errors"][0])
            self.assertTrue(path.is_file())
        with mock.patch.object(manifest, "_runtime_gate",
                               side_effect=manifest.ManifestError("Docker daemon is not reachable")):
            path, data = manifest.capture(self.project, self.carla, self.ue, self.artifacts)
            self.assertEqual("FAIL", data["status"])
            self.assertIn("Docker", data["errors"][0])
            self.assertTrue(path.is_file())

    def test_missing_source_lock_is_optional_but_parse_failure_fails_verify(self):
        (self.project / "config/carla/source.lock").unlink()
        data = self.capture()
        self.assertFalse(any(item["path"].endswith("source.lock") for item in data["key_files"]))
        self.assertEqual("PASS", manifest.verify(self.manifest_path, self.project)["status"])
        self.manifest_path.write_text("{not-json\n", encoding="utf-8")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)

    def test_capture_excludes_change_ledger_under_artifact_root_from_key_files(self):
        artifact_root = self.project / "artifacts"
        artifact_ledger = artifact_root / "temporary/change-ledger.json"
        artifact_ledger.parent.mkdir(parents=True)
        artifact_ledger.write_text("artifact-only\\n", encoding="utf-8")
        self.assertTrue(any(item["path"] == "artifacts/temporary/change-ledger.json"
                           for item in manifest._key_files(self.project)))
        self.assertFalse(any(item["path"] == "artifacts/temporary/change-ledger.json"
                            for item in manifest._key_files(self.project, [Path("/artifacts")])))
        path, data = manifest.capture(self.project, self.carla, self.ue, artifact_root,
                                    ["capture-build-manifest", "--artifact-root-fixture"])
        self.assertEqual("PASS", data["status"])
        self.assertFalse(any(item["path"] == "artifacts/temporary/change-ledger.json"
                            for item in data["key_files"]))
        self.assertTrue(path.is_file())

    def test_cli_verify_is_strict_and_capture_does_not_write_source_manifest(self):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "capture",
             "--project-root", str(self.project), "--carla-root", str(self.carla),
             "--ue-root", str(self.ue), "--artifact-root", str(self.artifacts),
             "--command", "fixture", "arg"],
            env={**os.environ, "PATH": self.test_path},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        path = Path(result.stdout.strip())
        self.assertTrue(path.is_file())
        self.assertFalse((self.project / "manifest.json").exists())
        verify = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "verify", "--manifest", str(path),
             "--project-root", str(self.project)],
            env={**os.environ, "PATH": self.test_path},
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(0, verify.returncode, verify.stderr)
        self.assertEqual("PASS\n", verify.stdout)

    def test_project_repo_and_sensitive_environment_are_recorded_safely(self):
        with mock.patch.dict(os.environ, {
            "CARLA_TEST_SETTING": "arm64",
            "MY_ACCESS_TOKEN": "must-not-be-stored",
            "UNRELATED_SETTING": "not-needed",
        }, clear=False):
            data = self.capture()
        self.assertIn("project", data["repositories"])
        self.assertEqual(".", data["repositories"]["project"]["root"])
        self.assertEqual("arm64", data["environment"]["values"]["CARLA_TEST_SETTING"])
        self.assertIn("MY_ACCESS_TOKEN", data["environment"]["redacted_keys"])
        self.assertNotIn("MY_ACCESS_TOKEN", data["environment"]["values"])
        self.assertNotIn("UNRELATED_SETTING", data["environment"]["values"])

    def test_declared_toolchain_image_is_recorded_and_verifies(self):
        declared = {"CARLA_TOOLCHAIN_IMAGE": "my-ad/carla-toolchain:arm64",
                    "CARLA_TOOLCHAIN_IMAGE_ID": "sha256:" + "a" * 64}
        with mock.patch.dict(os.environ, declared, clear=False):
            data = self.capture()
            self.assertEqual("my-ad/carla-toolchain:arm64",
                             data["toolchain_image"]["reference"])
            self.assertEqual("sha256:" + "a" * 64, data["toolchain_image"]["id"])
            self.assertEqual("PASS",
                             manifest.verify(self.manifest_path, self.project)["status"])

    def test_rebuilt_toolchain_image_fails_verify(self):
        # A tag alone cannot pin a toolchain: the image in use was eleven days
        # older than its Dockerfile, so the resolved ID is what has to match.
        with mock.patch.dict(os.environ, {
            "CARLA_TOOLCHAIN_IMAGE": "my-ad/carla-toolchain:arm64",
            "CARLA_TOOLCHAIN_IMAGE_ID": "sha256:" + "a" * 64,
        }, clear=False):
            self.capture()
            with mock.patch.dict(os.environ,
                                 {"CARLA_TOOLCHAIN_IMAGE_ID": "sha256:" + "b" * 64},
                                 clear=False):
                with self.assertRaises(manifest.ManifestError) as caught:
                    manifest.verify(self.manifest_path, self.project)
        self.assertIn("toolchain image changed", str(caught.exception))

    def test_a_retagged_image_still_verifies(self):
        # Rebuilding retags the default name onto a new image, and the default
        # name later moves to another tag; neither may invalidate an honest
        # manifest, because the resolved ID is the identity.
        with mock.patch.dict(os.environ, {
            "CARLA_TOOLCHAIN_IMAGE": "my-ad/carla-toolchain:arm64",
            "CARLA_TOOLCHAIN_IMAGE_ID": "sha256:" + "a" * 64,
        }, clear=False):
            self.capture()
            with mock.patch.dict(os.environ, {
                "CARLA_TOOLCHAIN_IMAGE": "my-ad/carla-toolchain:arm64-inuse",
                "CARLA_TOOLCHAIN_IMAGE_ID": "sha256:" + "a" * 64,
            }, clear=False):
                self.assertEqual("PASS",
                                 manifest.verify(self.manifest_path, self.project)["status"])

    def test_undeclared_toolchain_image_is_recorded_as_empty(self):
        environment = dict(os.environ)
        for key in ("CARLA_TOOLCHAIN_IMAGE", "CARLA_TOOLCHAIN_IMAGE_ID"):
            environment.pop(key, None)
        with mock.patch.dict(os.environ, environment, clear=True):
            data = self.capture()
            self.assertEqual({"reference": "", "id": ""}, data["toolchain_image"])
            self.assertEqual("PASS",
                             manifest.verify(self.manifest_path, self.project)["status"])

    def _write_source_lock(self, carla_commit, ue_commit):
        (self.project / "config/carla/source.lock").write_text(
            "schema: 1\n\nsources:\n"
            f"  carla:\n    commit: {carla_commit}\n"
            f"  unreal_engine:\n    commit: {ue_commit}\n",
            encoding="utf-8")

    def test_source_lock_drift_records_matching_and_drifted_heads(self):
        carla_head = subprocess.run(
            ["git", "-C", str(self.carla), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        ue_head = subprocess.run(
            ["git", "-C", str(self.ue), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        drifted_ue = "0" * 40
        self.assertNotEqual(ue_head, drifted_ue)
        self._write_source_lock(carla_head, drifted_ue)
        data = self.capture()
        drift = data["source_lock_drift"]
        self.assertTrue(drift["lock_present"])
        self.assertEqual("config/carla/source.lock", drift["lock_path"])
        self.assertEqual("matches", drift["pins"]["carla"]["state"])
        self.assertEqual(carla_head, drift["pins"]["carla"]["pin"])
        self.assertEqual("drifted", drift["pins"]["unreal_engine"]["state"])
        self.assertEqual(drifted_ue, drift["pins"]["unreal_engine"]["pin"])
        self.assertEqual(ue_head, drift["pins"]["unreal_engine"]["actual_head"])
        self.assertEqual(["unreal_engine"], drift["drifted"])
        self.assertEqual("PASS", manifest.verify(self.manifest_path, self.project)["status"])

    def test_source_lock_drift_verify_rejects_silent_lock_or_head_changes(self):
        carla_head = subprocess.run(
            ["git", "-C", str(self.carla), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        ue_head = subprocess.run(
            ["git", "-C", str(self.ue), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        self._write_source_lock(carla_head, ue_head)
        data = self.capture()
        self.assertEqual([], data["source_lock_drift"]["drifted"])
        # Editing the lock after capture must fail verify, not silently re-pin.
        self._write_source_lock(carla_head, "0" * 40)
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)
        # Restoring the lock but moving a HEAD must also fail verify.
        self._write_source_lock(carla_head, ue_head)
        self._git(self.ue, "checkout", "-q", "-b", "drift-test")
        (self.ue / "tracked.txt").write_text("advanced\n", encoding="utf-8")
        self._git(self.ue, "add", "tracked.txt")
        self._git(self.ue, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                  "commit", "-qm", "advance ue")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)

    def test_source_lock_missing_is_recorded_and_invalid_commit_fails_capture(self):
        lock = self.project / "config/carla/source.lock"
        lock.unlink()
        data = self.capture()
        self.assertFalse(data["source_lock_drift"]["lock_present"])
        self.assertEqual("PASS", manifest.verify(self.manifest_path, self.project)["status"])
        self._write_source_lock("not-a-commit", "0" * 40)
        _, invalid = manifest.capture(self.project, self.carla, self.ue, self.artifacts)
        self.assertEqual("FAIL", invalid["status"])
        self.assertIn("invalid commit", invalid["errors"][0])
        # A capture that recorded a missing lock must also reject a later lock.
        carla_head = subprocess.run(
            ["git", "-C", str(self.carla), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        ue_head = subprocess.run(
            ["git", "-C", str(self.ue), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        self._write_source_lock(carla_head, ue_head)
        self._git(self.project, "add", "config/carla/source.lock")
        with self.assertRaises(manifest.ManifestError):
            manifest.verify(self.manifest_path, self.project)

    def test_source_lock_missing_pin_and_extra_sources_are_recorded(self):
        carla_head = subprocess.run(
            ["git", "-C", str(self.carla), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True).stdout.strip()
        (self.project / "config/carla/source.lock").write_text(
            "schema: 1\n\nsources:\n"
            f"  carla:\n    commit: {carla_head}\n"
            f"  ros_bridge_legacy:\n    commit: {'1' * 40}\n",
            encoding="utf-8")
        data = self.capture()
        drift = data["source_lock_drift"]
        self.assertEqual("matches", drift["pins"]["carla"]["state"])
        self.assertEqual("unpinned", drift["pins"]["unreal_engine"]["state"])
        self.assertEqual(["ros_bridge_legacy"], drift["uncovered_lock_sources"])
        self.assertEqual([], drift["drifted"])



if __name__ == "__main__":
    unittest.main()
