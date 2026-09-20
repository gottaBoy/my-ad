"""Harness failures and optional real artifact verification; no UE/TBB builds."""

import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-imath.sh"
REPORTER = ROOT / "scripts/carla/stage_report.py"
STAGE = "carla-imath-native-arm64"
SCOPE = "Native ARM64 Imath 3.1.9 Release static PIC library and smoke; not PyImath, OpenUSD or UE Editor"
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"


@unittest.skipUnless(NATIVE, "run in native ARM64 carla-dev Docker")
class ImathTest(unittest.TestCase):
    def run_recipe(self, **environment):
        return subprocess.run(
            ["bash", str(SCRIPT)], env={**os.environ, **environment},
            capture_output=True, text=True, timeout=35,
        )

    def test_shell_syntax_and_isolation(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        self.assertIn("--parallel \"${jobs}\"", text)
        self.assertIn("timeout --kill-after=15s", text)
        self.assertIn("-DCMAKE_POSITION_INDEPENDENT_CODE=ON", text)
        self.assertIn("-Wl,--whole-archive", text)
        self.assertIn("-Wl,-z,text", text)
        self.assertIn("ue-arm64-third-party.cmake", text)
        self.assertNotIn("make clean", text)
        self.assertNotIn("rm -rf", text)
        self.assertNotIn("build-arm64-tbb.sh", text)

    def test_invalid_bounds_fail_before_creating_artifacts(self):
        for name, value in (("CARLA_BUILD_JOBS", "0"), ("CARLA_BUILD_JOBS", "5"),
                            ("CARLA_BUILD_JOBS", "bad"), ("CARLA_IMATH_TIMEOUT_SECONDS", "0"),
                            ("CARLA_IMATH_TIMEOUT_SECONDS", "1201"),
                            ("CARLA_IMATH_TIMEOUT_SECONDS", "999999999999999999")):
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "artifacts"
                result = self.run_recipe(CARLA_ARTIFACT_DIR=str(output), **{name: value})
                self.assertEqual(64, result.returncode, result.stdout + result.stderr)
                self.assertIn(name, result.stderr)
                self.assertFalse(output.exists())

    def test_missing_source_retains_specific_failure_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_recipe(CARLA_UE_DIR=str(root / "no-ue"),
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            reports = list((root / "artifacts/usd").glob("imath-*/stage-report.json"))
            self.assertEqual(1, len(reports))
            data = json.loads(reports[0].read_text())
            self.assertEqual("FAIL", data["status"])
            self.assertEqual("FAIL", data["checks"]["preflight"])
            log = (reports[0].parent / "preflight.log").read_text()
            self.assertIn("missing Imath source:", log)
            self.assertIn("Imath-3.1.9/CMakeLists.txt", log)
            self.assertFalse((reports[0].parent / "configure.log").exists())

    def test_missing_configuration_template_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ue/Engine/Source/ThirdParty/Imath/Imath-3.1.9"
            source.mkdir(parents=True)
            (source / "CMakeLists.txt").write_text("project(Imath VERSION 3.1.9 LANGUAGES C CXX)\n")
            result = self.run_recipe(CARLA_UE_DIR=str(root / "ue"),
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            log = next((root / "artifacts/usd").glob("imath-*/preflight.log")).read_text()
            self.assertIn("config/ImathConfig.h.in", log)
            self.assertFalse((source / "config").exists())

    def test_non_arm_execution_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "uname"
            fake.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
            fake.chmod(0o755)
            result = self.run_recipe(PATH=directory + os.pathsep + os.environ["PATH"])
            self.assertEqual(2, result.returncode)
            self.assertIn("native ARM64", result.stderr)

    def test_output_inside_ue_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_recipe(CARLA_UE_DIR=directory, CARLA_ARTIFACT_DIR=directory + "/out")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to write", result.stderr)
            self.assertFalse((Path(directory) / "out").exists())

    def test_timeout_produces_fail_report_without_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cmake = root / "cmake"
            cmake.write_text("#!/bin/sh\nsleep 20\n")
            cmake.chmod(0o755)
            result = self.run_recipe(PATH=directory + os.pathsep + os.environ["PATH"],
                                     CARLA_IMATH_TIMEOUT_SECONDS="3",
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            path = next((root / "artifacts/usd").glob("imath-*/stage-report.json"))
            report = json.loads(path.read_text())
            self.assertEqual(124, report["exit_code"])
            self.assertEqual("FAIL", report["status"])
            self.assertEqual("FAIL", report["checks"]["worker-exit"])
            self.assertFalse((path.parent / "build.log").exists())

    @unittest.skipUnless(os.environ.get("CARLA_IMATH_REPORT"), "set CARLA_IMATH_REPORT for real artifact validation")
    def test_real_build_report_and_retained_inputs(self):
        spec = importlib.util.spec_from_file_location("imath_stage_report", REPORTER)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        path = Path(os.environ["CARLA_IMATH_REPORT"])
        data = stage.validate_report(path, stage_id=STAGE, scope=SCOPE)
        self.assertTrue(all(value == "PASS" for value in data["checks"].values()))
        self.assertIn("Imath 3.1.9 smoke PASS", (path.parent / "smoke.log").read_text())
        self.assertIn("libImath-3_1.a", (path.parent / "installed.sha256").read_text())
        self.assertIn("retained.source/config/ImathConfig.h.in", data["evidence"])
        self.assertIn("cmake.compile_commands.json", data["evidence"])


if __name__ == "__main__":
    unittest.main()
