import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "fork_provenance", SCRIPTS / "report_fork_provenance.py")
provenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provenance)

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@unittest.skipUnless(GIT, "git is required")
class LiveTreeTest(unittest.TestCase):
    """describe_source must capture the delta, not just the commit."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "fork"
        self.repo.mkdir()
        _git(self.repo, "-c", "init.defaultBranch=main", "init")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        (self.repo / "file.txt").write_text("one\n", encoding="utf-8")
        _git(self.repo, "add", "file.txt")
        _git(self.repo, "commit", "-m", "first")

    def test_clean_checkout_has_no_delta(self):
        source = provenance.describe_source(self.repo, "/workspace/fork")
        self.assertEqual("main", source["branch"])
        self.assertEqual("first", source["subject"])
        self.assertEqual(0, source["tracked_dirty_files"])
        self.assertEqual(0, source["untracked_files"])
        self.assertEqual(64, len(source["tracked_diff_sha256"]))

    def test_tracked_edit_changes_the_diff_digest(self):
        clean = provenance.describe_source(self.repo, "/workspace/fork")
        (self.repo / "file.txt").write_text("two\n", encoding="utf-8")
        dirty = provenance.describe_source(self.repo, "/workspace/fork")
        self.assertEqual(clean["revision"], dirty["revision"])
        self.assertEqual(1, dirty["tracked_dirty_files"])
        self.assertNotEqual(clean["tracked_diff_sha256"], dirty["tracked_diff_sha256"])

    def test_second_tracked_edit_is_a_second_delta(self):
        (self.repo / "file.txt").write_text("two\n", encoding="utf-8")
        first = provenance.describe_source(self.repo, "/workspace/fork")
        (self.repo / "file.txt").write_text("three\n", encoding="utf-8")
        second = provenance.describe_source(self.repo, "/workspace/fork")
        self.assertEqual(1, second["tracked_dirty_files"])
        self.assertNotEqual(first["tracked_diff_sha256"], second["tracked_diff_sha256"])

    def test_untracked_files_are_counted_separately(self):
        (self.repo / "scratch.txt").write_text("x\n", encoding="utf-8")
        source = provenance.describe_source(self.repo, "/workspace/fork")
        self.assertEqual(0, source["tracked_dirty_files"])
        self.assertEqual(1, source["untracked_files"])

    def test_detached_head_is_reported_as_detached(self):
        _git(self.repo, "checkout", "--detach", "HEAD")
        self.assertEqual("detached",
                         provenance.describe_source(self.repo, "/workspace/fork")["branch"])

    def test_missing_checkout_is_refused(self):
        with self.assertRaises(provenance.ProvenanceError):
            provenance.describe_source(self.tmp / "absent", "/workspace/fork")


class CompareTest(unittest.TestCase):
    """Verification must only judge what the recording actually claims."""

    def live(self, revision="a" * 40, digest="d" * 64, tracked=1):
        return {"schema_version": 1, "sources": {
            "carla": {"location": "/workspace/carla", "revision": revision,
                      "branch": "main", "tracked_dirty_files": tracked,
                      "tracked_diff_sha256": digest},
            "ue": {"location": "/workspace/unreal-engine", "revision": "b" * 40,
                   "branch": "main", "tracked_dirty_files": 0,
                   "tracked_diff_sha256": "e" * 64},
        }}

    def recorded(self, **carla):
        source = {"location": "/workspace/carla", "revision": "a" * 40}
        source.update(carla)
        return {"schema_version": 1, "sources": {"carla": source,
                                                "ue": {"location": "/workspace/unreal-engine",
                                                       "revision": "b" * 40}}}

    def test_matching_revision_and_digest_has_no_rows(self):
        live = self.live()
        recorded = self.recorded(tracked_dirty_files=1, tracked_diff_sha256="d" * 64)
        self.assertEqual([], provenance.compare(recorded, live))

    def test_a_legacy_recording_with_only_revision_still_verifies(self):
        # The file the probes shipped with names nothing but location and
        # revision, so it must be checkable rather than rejected.
        self.assertEqual([], provenance.compare(self.recorded(), self.live()))

    def test_wrong_revision_is_reported(self):
        rows = provenance.compare(self.recorded(revision="c" * 40), self.live())
        self.assertEqual(1, len(rows))
        self.assertEqual(("carla", "revision"), rows[0][:2])

    def test_changed_diff_digest_is_reported(self):
        recorded = self.recorded(tracked_diff_sha256="f" * 64)
        rows = provenance.compare(recorded, self.live())
        self.assertEqual([("carla", "tracked_diff_sha256")], [row[:2] for row in rows])

    def test_source_absent_from_the_recording_is_not_reported(self):
        # `ue` is compared too, but only for the fields the recording carries.
        recorded = self.recorded()
        recorded["sources"]["ue"]["revision"] = "b" * 40
        self.assertEqual([], provenance.compare(recorded, self.live()))

    def test_extra_derivable_fields_are_compared_when_claimed(self):
        recorded = self.recorded(tracked_dirty_files=7)
        rows = provenance.compare(recorded, self.live(tracked=1))
        self.assertEqual([("carla", "tracked_dirty_files")], [row[:2] for row in rows])

    def test_field_that_can_no_longer_be_derived_is_reported(self):
        rows = provenance.compare(self.recorded(removed_field="x"), self.live())
        self.assertEqual([("carla", "removed_field", "x", None, "not derivable")], rows)


class RenderAndLoadTest(unittest.TestCase):
    def test_render_is_deterministic_and_sorted(self):
        data = {"schema_version": 1, "sources": {
            "ue": {"revision": "b"}, "carla": {"revision": "a"}}}
        text = provenance.render(data)
        self.assertEqual(text, provenance.render(data))
        self.assertLess(text.index('"carla"'), text.index('"ue"'))
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(data, json.loads(text))

    def test_load_rejects_a_wrong_schema_version(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "provenance.json"
        path.write_text('{"schema_version": 2, "sources": {}}', encoding="utf-8")
        with self.assertRaises(provenance.ProvenanceError):
            provenance.load_recorded(path)

    def test_load_rejects_a_source_without_a_revision(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "provenance.json"
        path.write_text(json.dumps({"schema_version": 1, "sources": {
            "carla": {"location": "/workspace/carla", "revision": ""},
            "ue": {"location": "/workspace/unreal-engine", "revision": "b"}}}),
            encoding="utf-8")
        with self.assertRaises(provenance.ProvenanceError):
            provenance.load_recorded(path)

    def test_schema_version_matches_the_runtime_checker(self):
        # check_carla_runtime.read_provenance accepts exactly this version.
        self.assertEqual(1, provenance.SCHEMA_VERSION)
        source = Path(SCRIPTS / "check_carla_runtime.py").read_text(encoding="utf-8")
        self.assertIn('data["schema_version"] == 1', source)


if __name__ == "__main__":
    unittest.main()
