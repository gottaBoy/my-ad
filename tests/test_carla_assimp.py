import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/carla/check-assimp-fbx.py"
spec = importlib.util.spec_from_file_location("check_assimp_fbx", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class AssimpFbxTest(unittest.TestCase):
    def test_metrics_require_real_geometry_and_integer_counts(self):
        checker.check_metrics({"vertices": 8, "faces": 12}, {"vertices": 8, "faces": 12})
        for metrics in ({}, [], {"faces": 0}, {"faces": True}, {"faces": "12"}, {"faces": 12.0}):
            with self.subTest(metrics=metrics):
                with self.assertRaises(ValueError):
                    checker.check_metrics(metrics, {"faces": 12})

    def test_upstream_xml_requires_completed_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "upstream.xml"
            path.write_text("<testsuites tests=\"1\" failures=\"0\"><testsuite>"
                            "<testcase status=\"run\" result=\"completed\"/>"
                            "</testsuite></testsuites>")
            self.assertEqual(1, checker.check_upstream(path))
            for xml in (
                "<testsuites tests=\"0\"/>",
                "<testsuites tests=\"1\"/>",
                "<testsuites tests=\"1\"><testcase status=\"notrun\" result=\"suppressed\"/></testsuites>",
                "<testsuites tests=\"1\" failures=\"1\"><testcase status=\"run\" result=\"completed\"/></testsuites>",
                "<testsuites tests=\"1\"><testcase status=\"run\" result=\"completed\"><failure/></testcase></testsuites>",
            ):
                with self.subTest(xml=xml):
                    path.write_text(xml)
                    with self.assertRaises(ValueError):
                        checker.check_upstream(path)

    def test_build_is_pinned_and_does_not_replace_the_autodesk_sdk(self):
        script = (REPO_ROOT / "scripts/carla/build-arm64-assimp.sh").read_text()
        for value in (
            "version=6.0.5", "commit=392a658f9c271be965271f45e7521a1b80ea4392",
            "rev-parse HEAD", "diff --exit-code HEAD", "sparse-checkout init --cone",
            "ue-arm64-third-party.cmake", "-DASSIMP_BUILD_FBX_IMPORTER=ON",
            "-DASSIMP_BUILD_FBX_EXPORTER=ON", "-DASSIMP_WARNINGS_AS_ERRORS=ON",
            "-DASSIMP_BUILD_ASSBIN_EXPORTER=ON", "-DASSIMP_BUILD_ASSXML_EXPORTER=ON",
            "-u LD_LIBRARY_PATH", "upstream.xml", "installed.sha256",
            "deployed-cli", "listext",
            "not an Autodesk SDK ABI replacement",
        ):
            self.assertIn(value, script)
        self.assertNotIn("libfbxsdk.so", script)
        self.assertNotIn("apt-get", script)

    def test_make_uses_read_only_source_profile(self):
        result = subprocess.run(
            ["make", "-n", "carla-assimp", "JOBS=3"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile g0 run --rm -T", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=3 carla-dev", result.stdout)
        self.assertIn("build-arm64-assimp.sh", result.stdout)

    def test_invalid_jobs_are_rejected_before_network_access(self):
        for value in ("0", "-1", "three", "2;exit 0"):
            with self.subTest(value=value):
                result = subprocess.run(
                    ["bash", str(REPO_ROOT / "scripts/carla/build-arm64-assimp.sh")],
                    env={**os.environ, "CARLA_BUILD_JOBS": value}, capture_output=True, text=True,
                )
                self.assertEqual(64, result.returncode)

    def test_smoke_checks_animation_morph_and_corrupt_input(self):
        script = SCRIPT.read_text()
        for expected in (
            "AnimatedCharacter.fbx", "MorphTargets.fbx", "MultiMatId.fbx",
            "animation_keys", "weights", "morph_targets", "truncated",
            "result.returncode != 1", "subprocess.TimeoutExpired",
        ):
            self.assertIn(expected, script)


if __name__ == "__main__":
    unittest.main()
