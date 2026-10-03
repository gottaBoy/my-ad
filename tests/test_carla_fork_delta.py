import hashlib
import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "fork_delta", SCRIPTS / "freeze_fork_delta.py")
delta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delta)

GIT = shutil.which("git")

provenance_spec = importlib.util.spec_from_file_location(
    "fork_provenance", SCRIPTS / "report_fork_provenance.py")
provenance = importlib.util.module_from_spec(provenance_spec)
provenance_spec.loader.exec_module(provenance)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipUnless(GIT, "git is required")
class FreezeTest(unittest.TestCase):
    """A frozen delta is only useful if drift is detected, not inherited."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repos = {}
        for name in ("carla", "ue"):
            repo = self.tmp / name
            repo.mkdir()
            _git(repo, "-c", "init.defaultBranch=main", "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test")
            (repo / "source.cpp").write_text("one\n", encoding="utf-8")
            _git(repo, "add", "source.cpp")
            _git(repo, "commit", "-m", "base")
            self.repos[name] = repo
        self.patch_dir = self.tmp / "patches"

    def freeze(self):
        return delta.freeze(self.repos, self.patch_dir)

    def verify(self):
        return delta.verify(self.repos, self.patch_dir)

    def test_clean_forks_freeze_empty_deltas(self):
        records = self.freeze()
        self.assertEqual(["carla", "ue"], sorted(r["source"] for r in records))
        for record in records:
            self.assertEqual(0, record["bytes"])
            self.assertEqual(0, record["files"])
        rows, _ = self.verify()
        self.assertEqual([], rows)

    def test_frozen_delta_names_the_changed_files(self):
        (self.repos["carla"] / "source.cpp").write_text("two\n", encoding="utf-8")
        (self.repos["ue"] / "source.cpp").write_text("three\n", encoding="utf-8")
        records = {r["source"]: r for r in self.freeze()}
        self.assertEqual(1, records["carla"]["files"])
        self.assertEqual(1, records["ue"]["files"])
        self.assertGreater(records["carla"]["bytes"], 0)

    def test_verification_passes_when_the_tree_is_unchanged(self):
        (self.repos["ue"] / "source.cpp").write_text("two\n", encoding="utf-8")
        self.freeze()
        rows, records = self.verify()
        self.assertEqual([], rows)
        self.assertEqual(1, [r for r in records if r["source"] == "ue"][0]["files"])

    def test_a_later_edit_is_reported_as_drift(self):
        (self.repos["carla"] / "source.cpp").write_text("two\n", encoding="utf-8")
        self.freeze()
        (self.repos["carla"] / "source.cpp").write_text("three\n", encoding="utf-8")
        rows, _ = self.verify()
        self.assertEqual([("carla", "bytes"), ("carla", "sha256"), ("carla", "files")],
                         [row[:2] for row in rows])

    def test_the_provenance_tool_reports_the_same_digest(self):
        # Three tools record this digest - capture_build_manifest.py,
        # report_fork_provenance.py and freeze_fork_delta.py - so they must
        # agree, or "the same delta" would mean different things in each record.
        (self.repos["ue"] / "source.cpp").write_text("two\n", encoding="utf-8")
        diff = delta.fork_delta(self.repos["ue"])
        source = provenance.describe_source(self.repos["ue"], "/workspace/ue")
        self.assertEqual(hashlib.sha256(diff).hexdigest(),
                         source["tracked_diff_sha256"])

    def test_extra_untracked_files_are_not_part_of_the_tracked_delta(self):
        # The manifest snapshots untracked files separately; this delta is the
        # tracked patch only, so a new scratch file must not read as drift.
        self.freeze()
        (self.repos["ue"] / "scratch.txt").write_text("noise\n", encoding="utf-8")
        rows, _ = self.verify()
        self.assertEqual([], rows)

    def test_missing_frozen_delta_is_an_error_not_drift(self):
        self.freeze()
        delta.patch_path(self.patch_dir, "ue").unlink()
        with self.assertRaises(delta.DeltaError):
            self.verify()

    def test_missing_checkout_is_refused(self):
        with self.assertRaises(delta.DeltaError):
            delta.fork_delta(self.tmp / "absent")

    def test_binary_content_survives_the_round_trip(self):
        # --binary matches capture_build_manifest.py; without it a binary edit
        # would freeze as an unusable "Binary files differ" marker.
        blob = self.repos["ue"] / "asset.bin"
        blob.write_bytes(bytes(range(256)) * 8)
        _git(self.repos["ue"], "add", "asset.bin")
        _git(self.repos["ue"], "commit", "-m", "add blob")
        payload = bytes(range(256)) * 8 + b"\x00\x01"
        blob.write_bytes(payload)
        self.freeze()
        frozen = delta.patch_path(self.patch_dir, "ue").read_bytes()
        self.assertIn(b"GIT binary patch", frozen)
        self.assertNotIn(b"Binary files differ", frozen)
        rows, _ = self.verify()
        self.assertEqual([], rows)


class CliTest(unittest.TestCase):
    def test_patch_names_are_distinct_and_named_for_the_fork(self):
        self.assertEqual(2, len(set(delta.PATCH_NAMES.values())))
        self.assertIn("carla", delta.PATCH_NAMES["carla"])
        self.assertIn("ue", delta.PATCH_NAMES["ue"])

    def test_frozen_patches_live_beside_the_curated_ones(self):
        self.assertEqual("scripts/carla/patches", delta.DEFAULT_PATCH_DIR)
        self.assertTrue((ROOT / delta.DEFAULT_PATCH_DIR).is_dir())


if __name__ == "__main__":
    unittest.main()
