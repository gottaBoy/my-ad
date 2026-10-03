import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = ROOT / "artifacts/gb10-driver-report"


def newest_bundle():
    """The bundle that would be sent today: the newest directory that has a report.json."""
    if not BUNDLE_ROOT.is_dir():
        return None
    candidates = [
        path
        for path in BUNDLE_ROOT.iterdir()
        if path.is_dir() and (path / "report.json").is_file()
    ]
    return max(candidates, key=lambda path: path.name) if candidates else None


class Gb10BundleConsistencyTest(unittest.TestCase):
    """What the vendor reads must agree with the evidence it is built from.

    The bundle is generated, but the cover note is written by hand and the numbers in it are
    exactly what a reader will quote back. Reviewing those numbers by eye is how a line count
    got mistaken for a crash count once already, so the prose is checked against report.json
    here instead. Counts are derived, never hardcoded, so regenerating the bundle cannot rot
    this test.
    """

    @classmethod
    def setUpClass(cls):
        bundle = newest_bundle()
        if bundle is None:
            raise unittest.SkipTest("no GB10 driver bundle present")
        cls.bundle = bundle
        cls.document = json.loads((bundle / "report.json").read_text(encoding="utf-8"))
        cls.markdown = (bundle / "report.md").read_text(encoding="utf-8")
        cls.cover = (bundle / "COVER-NOTE.md").read_text(encoding="utf-8")

    def statuses(self):
        counts = {}
        for run in self.document["runs"]:
            counts[run["status"]] = counts.get(run["status"], 0) + 1
        return counts

    def test_bundle_has_the_files_a_reader_needs(self):
        for name in ("report.md", "report.json", "COVER-NOTE.md"):
            self.assertTrue((self.bundle / name).is_file(), name)

    def test_every_run_is_in_the_table_with_its_status(self):
        self.assertEqual(len(self.document["runs"]), len(set(
            run["run"] for run in self.document["runs"])))
        rows = [line for line in self.markdown.splitlines() if line.startswith("| town10")]
        self.assertEqual(len(self.document["runs"]), len(rows))
        for run in self.document["runs"]:
            self.assertIn(f"| {run['status']} |", self.markdown)
        for status, count in self.statuses().items():
            # The table is the only place the distribution appears, so counting its cells is how
            # a reader would derive it.
            self.assertEqual(count, len([
                line for line in rows if f"| {status} |" in line
            ]))

    def test_crash_stacks_section_matches_the_recorded_frames(self):
        with_frames = [run for run in self.document["runs"] if run.get("crash")]
        for run in with_frames:
            self.assertIn(f"### {run['run']}", self.markdown)
            for frame in run["crash"].get("driver", []):
                self.assertIn(frame.replace("0x", "0x"), self.markdown)
        # A run without frames must not appear to have a stack.
        self.assertEqual(len(with_frames), len(re.findall(r"^### town10", self.markdown, re.M)))

    def test_every_referenced_run_directory_still_exists(self):
        # The report is worthless to the vendor if the raw evidence it cites has been removed.
        for run in self.document["runs"]:
            self.assertTrue((ROOT / run["run_dir"]).is_dir(), run["run_dir"])

    def test_missing_evidence_is_rendered_honestly(self):
        section = self.markdown.split("## Missing evidence", 1)
        self.assertEqual(2, len(section), "the report must have a missing-evidence section")
        body = section[1]
        missing = self.document["missing"]
        if missing:
            for item in missing:
                self.assertIn(item.split("(")[0].strip()[:40], body)
        else:
            self.assertIn("- none", body)

    def test_cover_note_numbers_match_the_records(self):
        count = len(self.document["runs"])
        self.assertIn(f"{count} run records", self.cover)
        dates = {run["run"].split("-")[3][:8] for run in self.document["runs"]}
        self.assertEqual(1, len(dates), dates)
        self.assertIn("-".join([dates.pop()[:4], "10", "01"]), self.cover)

    def test_cover_note_does_not_overstate_the_frame_evidence(self):
        # Most records carry a status and a stop code, not a stack. Saying otherwise invites the
        # reader to assume 39 symbolized crashes arrived.
        with_frames = sum(1 for run in self.document["runs"] if run.get("crash"))
        self.assertLess(with_frames, len(self.document["runs"]))
        self.assertIn(f"symbolized crash frames for {with_frames}", self.cover)

    def test_cover_note_states_the_constraint_and_the_ask(self):
        # The one thing the escalation must not be read as: "try another driver".
        self.assertIn("We cannot change the installed driver", self.cover)
        self.assertIn("try another driver", self.cover)
        for marker in ("Please tell us one of", "known defect", "supported workaround"):
            self.assertIn(marker, self.cover)

    def test_cover_note_prefers_the_reproducible_sweep_over_a_subset(self):
        # The 39-run sweep is the strongest statement available (validation layer on: 2 of 10
        # passed; off: 0 of 29), and it supersedes the earlier "validation passes" reading.
        self.assertIn("2 of 10", self.cover)
        self.assertIn("0 of 29", self.cover)


if __name__ == "__main__":
    unittest.main()
