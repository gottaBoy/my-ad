from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (REPO_ROOT / ".env.example").read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value.strip().strip("'\"")
    return values


class EnvDefaultsTest(unittest.TestCase):
    def test_dgx_core_defaults_are_ready(self) -> None:
        env = load_env_example()
        self.assertEqual(env["HOST_ROLE"], "dgx")
        self.assertEqual(env["PREFLIGHT_SCOPE"], "core")
        self.assertEqual(env["MAPS_DIR"], "./data/maps/sample-map-planning")

        for key in (
            "ROS_DOMAIN_ID",
            "ROS_DISTRO",
            "RMW_IMPLEMENTATION",
            "GPU_SMOKE_IMAGE",
            "AUTOWARE_IMAGE",
            "AUTOWARE_COMMAND",
            "AUTOWARE_HEALTHCHECK_COMMAND",
        ):
            with self.subTest(key=key):
                self.assertTrue(env[key])
                self.assertFalse(env[key].startswith("REPLACE_"))

        self.assertIn("@sha256:", env["AUTOWARE_IMAGE"])
        self.assertIn("@sha256:", env["GPU_SMOKE_IMAGE"])
        self.assertIn("planning_simulator.launch.xml", env["AUTOWARE_COMMAND"])
        self.assertIn("/map/vector_map", env["AUTOWARE_HEALTHCHECK_COMMAND"])

    def test_unverified_profiles_remain_explicitly_optional(self) -> None:
        env = load_env_example()
        for key in (
            "AWSIM_IMAGE",
            "FOXGLOVE_IMAGE",
            "GROUND_TRUTH_IMAGE",
            "ISAAC_IMAGE",
            "TENSORRT_IMAGE",
            "NAVSIM_IMAGE",
        ):
            with self.subTest(key=key):
                self.assertTrue(env[key].startswith("REPLACE_"))

    def test_init_creates_the_default_map_directory(self) -> None:
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("data/maps/sample-map-planning", makefile)

    def test_preflight_requires_the_verified_autoware_map_files(self) -> None:
        preflight = (REPO_ROOT / "scripts/preflight/check.sh").read_text(
            encoding="utf-8"
        )
        runner = (REPO_ROOT / "scripts/ops/run-dgx-mode.sh").read_text(
            encoding="utf-8"
        )
        for filename in ("lanelet2_map.osm", "pointcloud_map.pcd"):
            with self.subTest(filename=filename):
                self.assertIn(filename, preflight)
                self.assertIn(filename, runner)


if __name__ == "__main__":
    unittest.main()
