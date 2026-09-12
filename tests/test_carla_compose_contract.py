from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class CarlaComposeContractTest(unittest.TestCase):
    def test_compose_is_arm64_and_has_only_probe_profile(self) -> None:
        compose = (REPO_ROOT / "compose.carla-arm64.yaml").read_text()
        self.assertIn("platform: linux/arm64", compose)
        self.assertIn('profiles: ["g0"]', compose)
        self.assertIn('profiles: ["build"]', compose)
        self.assertIn("carla-g0-probe", compose)
        self.assertIn("entrypoint: []", compose)
        self.assertIn("CARLA_UNREAL_ENGINE_PATH: /workspace/unreal-engine", compose)
        self.assertIn(":rw", compose)
        self.assertNotIn("/var/run/docker.sock", compose)

    def test_source_lock_pins_all_primary_forks(self) -> None:
        lock = (REPO_ROOT / "config/carla/source.lock").read_text()
        for repository in (
            "gottaBoy/carla.git",
            "gottaBoy/ros-bridge.git",
            "gottaBoy/autoware_carla_bridge.git",
            "gottaBoy/autoware_universe.git",
            "gottaBoy/autoware_launch.git",
            "gottaBoy/nano-ros.git",
        ):
            self.assertIn(repository, lock)
        self.assertIn("primary_carla_route: gottaBoy/carla@dgx-arm64", lock)
        self.assertIn("base_ref: ue5-dev", lock)


if __name__ == "__main__":
    unittest.main()
