import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "docs/carla-handoff.md"
TODO = ROOT / "docs/carla-dgx-todo.md"

# Paths the page points a human at. A handoff page whose bundle path has moved sends the reader
# to nothing, and it does so silently, so the referenced artifacts are checked to exist.
PATH_PATTERN = re.compile(r"`(artifacts/[A-Za-z0-9_./-]+)`")


class HandoffPageTest(unittest.TestCase):
    """The decision queue is the one document a human is expected to act on.

    It must stay short, must hand out only paths that exist, and must keep pointing at the audit
    and the todo as the sources of evidence and of open work — those three roles are distinct and
    the page is worthless if it starts duplicating them.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = HANDOFF.read_text(encoding="utf-8")

    def test_referenced_artifacts_exist(self):
        referenced = PATH_PATTERN.findall(self.text)
        self.assertTrue(referenced, "the page must point at the bundles it hands over")
        # The evidence tree is generated and gitignored, so a fresh clone has none of it. Each
        # group is checked only when its top-level directory is present: the page must point at
        # evidence that exists, but its absence is not a defect in the page.
        checked = []
        for group in sorted({"/".join(path.split("/")[:2]) for path in referenced}):
            if not (ROOT / group).is_dir():
                continue
            for path in referenced:
                if path.startswith(group + "/"):
                    self.assertTrue((ROOT / path).exists(), path)
                    checked.append(path)
        if not checked:
            self.skipTest("no generated evidence tree present (artifacts/ is gitignored)")

    def test_hands_over_both_decision_bundles(self):
        self.assertIn("artifacts/gb10-driver-report/20261003T052318Z/", self.text)
        self.assertIn("artifacts/ue-gpumessaging-shutdown/20261003T062016Z/", self.text)

    def test_names_the_three_decisions(self):
        for marker in ("厂商包", "落点", "固化方式"):
            self.assertIn(marker, self.text)

    def test_points_at_the_evidence_and_the_open_list(self):
        self.assertIn("docs/carla-dgx-audit.md", self.text)
        self.assertIn("docs/carla-dgx-todo.md", self.text)

    def test_keeps_the_driver_constraint_visible(self):
        # The user forbade changing the GB10 driver; the handoff page is where that constraint has
        # to survive, otherwise the bundle reads as "try another driver".
        self.assertIn("驱动不可更换", self.text)

    def test_is_reachable_from_the_todo(self):
        self.assertIn("docs/carla-handoff.md", TODO.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
