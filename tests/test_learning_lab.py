from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class LearningLabContractTest(unittest.TestCase):
    def test_module_inspection_entrypoint_is_declared(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("inspect-modules:", makefile)
        self.assertIn("scripts/harness/inspect-modules.sh", makefile)
        self.assertIn('"$(MODULE)"', makefile)
        self.assertIn("learn-module:", makefile)
        self.assertIn('run.sh learning "$(MODULE)" "$(DURATION)"', makefile)
        harness_runner = (
            REPO_ROOT / "scripts/harness/run.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("scripts/harness/learn-module.sh", harness_runner)

        for relative_path in (
            "scripts/harness/inspect-modules.sh",
            "scripts/harness/learn-module.sh",
            "scripts/harness/sample-topic.py",
        ):
            script = REPO_ROOT / relative_path
            self.assertTrue(script.is_file())
            self.assertTrue(script.stat().st_mode & 0o111)

        learner = (
            REPO_ROOT / "scripts/harness/learn-module.sh"
        ).read_text(encoding="utf-8")
        for status in (
            "NO_RUNTIME_INPUT",
            "TOPICS_PRESENT_NO_MESSAGES",
            "MESSAGES_RECEIVED",
        ):
            self.assertIn(status, learner)

    def test_learning_run_writes_observation_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                """#!/usr/bin/env bash
if [[ "$*" == *"ps -q autoware"* ]]; then
  echo fake-container-id
elif [[ "$*" == *"exec -T autoware"* ]]; then
  cat <<'EOF'
module=planning
observation_window_sec=1
effect_chain=map -> trajectory
learning_goal=observe planning output
--- topic=/planning/trajectory ---
status=PRESENT
Publisher count: 1
Subscription count: 1
publisher_count=1
sample_status=RECEIVED type=autoware_planning_msgs/msg/Trajectory
message_count=10
observed_rate_hz=10.000
sample_summary={"points":{"count":4}}
present_topic_count=1
topics_with_messages=1
module_status=MESSAGES_RECEIVED
conclusion=planning messages were captured
warning=topic presence alone is not an algorithm PASS
EOF
else
  exit 1
fi
""",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)

            env_file = temp_root / ".env"
            env_file.write_text("HOST_ROLE=dgx\n", encoding="utf-8")
            artifact_root = temp_root / "artifacts"
            env = os.environ.copy()
            env.update(
                {
                    "ARTIFACT_ROOT": str(artifact_root),
                    "ENV_FILE": str(env_file),
                    "HOST_ROLE": "dgx",
                    "PATH": f"{fake_bin}:{env['PATH']}",
                }
            )
            result = subprocess.run(
                [
                    str(REPO_ROOT / "scripts/harness/run.sh"),
                    "learning",
                    "planning",
                    "1",
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            run_dirs = list((artifact_root / "learning").iterdir())
            self.assertEqual(1, len(run_dirs))
            run_dir = run_dirs[0]
            decision = (run_dir / "decision.md").read_text(encoding="utf-8")
            observation = (
                run_dir / "logs/planning-observation.txt"
            ).read_text(encoding="utf-8")
            summary = (
                run_dir / "metrics/planning-summary.txt"
            ).read_text(encoding="utf-8")
            self.assertIn("Status: OBSERVED", decision)
            self.assertIn("module_status=MESSAGES_RECEIVED", observation)
            self.assertIn('sample_summary={"points":{"count":4}}', summary)

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
            "make learn-module MODULE=sensing",
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

    def test_topic_summary_bounds_large_payloads(self) -> None:
        module_path = REPO_ROOT / "scripts/harness/sample-topic.py"
        spec = importlib.util.spec_from_file_location("sample_topic", module_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        summary = module.summarize_value(
            {
                "data": [1, 2, 3],
                "objects": [{"id": 1}, {"id": 2}],
            }
        )
        self.assertEqual({"length": 3}, summary["data"])
        self.assertEqual(2, summary["objects"]["count"])
        self.assertEqual({"id": 1}, summary["objects"]["first"])


if __name__ == "__main__":
    unittest.main()
