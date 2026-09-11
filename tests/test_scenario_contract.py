from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ScenarioContractTest(unittest.TestCase):
    def test_makefile_exposes_scenario_targets(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        for target in (
            "scenario:",
            "scenario-up:",
            "scenario-prepare:",
            "harness-ros-scenario:",
        ):
            self.assertIn(target, makefile)

    def test_scenario_runtime_scripts_are_executable(self) -> None:
        for relative_path in (
            "scripts/ops/prepare-scenario.sh",
            "scripts/ops/run-dgx-mode.sh",
        ):
            path = REPO_ROOT / relative_path
            self.assertTrue(path.is_file())
            self.assertTrue(path.stat().st_mode & 0o111)

    def test_scenario_defaults_and_topics_are_declared(self) -> None:
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("SCENARIO_IMAGE=ghcr.io/tier4/scenario_simulator_v2:", env_example)
        self.assertIn("scenario_simulation:=true", env_example)
        self.assertIn("launch_autoware:=false", env_example)
        self.assertIn("SCENARIO_HEALTHCHECK_COMMAND=", env_example)

        topics = (
            REPO_ROOT / "config/harness/required-topics-scenario.txt"
        ).read_text(encoding="utf-8")
        for topic in (
            "/clock",
            "/tf",
            "/vehicle/status/velocity_status",
            "/planning/trajectory",
            "/control/command/control_cmd",
        ):
            self.assertIn(topic, topics)

    def test_scenario_up_accepts_configured_required_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker = fake_bin / "docker"
            docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            docker.chmod(0o755)

            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    (
                        "HOST_ROLE=dgx",
                        "SCENARIO_IMAGE=test-scenario-image",
                        "SCENARIO_COMMAND=true",
                        "AUTOWARE_SCENARIO_COMMAND=true",
                        "AUTOWARE_SCENARIO_HEALTHCHECK_COMMAND=true",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            environment = os.environ.copy()
            environment["ENV_FILE"] = str(env_file)
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            result = subprocess.run(
                [
                    str(REPO_ROOT / "scripts/ops/run-dgx-mode.sh"),
                    "scenario-up",
                ],
                cwd=REPO_ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Scenario Simulator stack started", result.stdout)


if __name__ == "__main__":
    unittest.main()
