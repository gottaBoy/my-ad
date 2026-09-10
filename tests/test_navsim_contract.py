from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class NavsimContractTest(unittest.TestCase):
    def test_compose_declares_arm64_gpu_navsim_profile(self) -> None:
        compose = (REPO_ROOT / "compose.dgx.yaml").read_text(encoding="utf-8")
        self.assertIn("profiles: [\"navsim\"]", compose)
        self.assertIn("platform: linux/arm64", compose)
        self.assertIn("image: ${NAVSIM_IMAGE:?NAVSIM_IMAGE must be set in .env}", compose)
        self.assertIn("NAVSIM_COMMAND", compose)
        self.assertIn("NAVSIM_HEALTHCHECK_COMMAND", compose)
        self.assertIn("NUPLAN_MAPS_ROOT", compose)
        self.assertIn("/data/navsim/dataset:ro", compose)
        self.assertIn("capabilities: [gpu]", compose)

    def test_navsim_runtime_files_exist(self) -> None:
        dockerfile = REPO_ROOT / "images/navsim/Dockerfile"
        start_script_path = REPO_ROOT / "scripts/ops/start-navsim.sh"
        self.assertTrue(dockerfile.is_file())
        self.assertTrue(start_script_path.is_file())
        self.assertTrue(start_script_path.stat().st_mode & 0o111)
        start_script = start_script_path.read_text(encoding="utf-8")
        self.assertIn("environment.env", start_script)
        self.assertIn("command.txt", start_script)

    def test_makefile_exposes_evaluation_and_harness_targets(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("build-navsim:", makefile)
        self.assertIn("navsim:", makefile)
        self.assertIn("navsim-cache:", makefile)
        self.assertIn("harness-navsim:", makefile)


if __name__ == "__main__":
    unittest.main()
