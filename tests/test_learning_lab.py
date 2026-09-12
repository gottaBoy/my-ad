from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class LearningLabContractTest(unittest.TestCase):
    def test_module_inspection_entrypoint_is_declared(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("inspect-modules:", makefile)
        self.assertIn("scripts/harness/inspect-modules.sh", makefile)
        self.assertIn('"$(MODULE)"', makefile)

        script = REPO_ROOT / "scripts/harness/inspect-modules.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(script.stat().st_mode & 0o111)

    def test_module_topic_contract_covers_learning_stages(self) -> None:
        topics = (
            REPO_ROOT / "config/harness/module-topics.txt"
        ).read_text(encoding="utf-8")
        for module in (
            "sensing|",
            "localization|",
            "perception|",
            "fusion|",
            "planning|",
            "control|",
        ):
            self.assertIn(module, topics)

        for topic in (
            "/sensing/camera/camera0/image_raw",
            "/sensing/lidar/top/pointcloud_raw",
            "/perception/object_recognition/objects",
            "/planning/trajectory",
            "/control/command/control_cmd",
        ):
            self.assertIn(topic, topics)

    def test_learning_document_sets_the_boundary(self) -> None:
        document = (
            REPO_ROOT / "docs/autoware-learning-lab.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "make up-dgx",
            "make scenario",
            "make inspect-modules",
            "make inspect-modules MODULE=sensing",
            "NAVSIM",
            "BEVFormer",
            "Isaac Lab",
            "CARLA",
            "感知",
            "融合",
            "规划",
            "控制",
            "当前 demo 的边界",
        ):
            self.assertIn(phrase, document)


if __name__ == "__main__":
    unittest.main()
