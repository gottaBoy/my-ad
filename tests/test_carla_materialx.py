"""Bounded failure fixtures and real MaterialX artifact checks in ARM64 Docker."""

import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-materialx.sh"
STAGE = "carla-materialx-native-arm64"
SCOPE = "Native ARM64 MaterialX 1.38.5 six Release PIC libraries and XML material validation; not rendering, Python, OpenUSD or UE Editor"
LIBRARIES = ("MaterialXCore", "MaterialXFormat", "MaterialXGenGlsl",
             "MaterialXGenMdl", "MaterialXGenOsl", "MaterialXGenShader")
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"


@unittest.skipUnless(NATIVE, "run in native ARM64 carla-dev Docker")
class MaterialXTest(unittest.TestCase):
    def recipe(self, output, **environment):
        return subprocess.run(
            ["bash", str(SCRIPT)], env={**os.environ, "CARLA_ARTIFACT_DIR": str(output), **environment},
            capture_output=True, text=True, timeout=40,
        )

    def failed_report(self, output):
        paths = list((output / "usd").glob("materialx-*/stage-report.json"))
        self.assertEqual(1, len(paths))
        report = json.loads(paths[0].read_text())
        self.assertEqual("FAIL", report["status"])
        self.assertFalse((paths[0].parent / "build.log").exists())
        return paths[0], report

    def test_shell_syntax_and_scope(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        for expected in ("--parallel \"${jobs}\"", "timeout --kill-after=15s", "-Wl,--whole-archive",
                         "-Wl,-z,text", "-DMATERIALX_BUILD_RENDER=OFF", "-DMATERIALX_BUILD_PYTHON=OFF"):
            self.assertIn(expected, text)
        for forbidden in ("rm -rf", "make clean", "build-arm64-tbb.sh", "build-arm64-imath.sh"):
            self.assertNotIn(forbidden, text)

    def test_invalid_bounds_do_not_create_artifacts(self):
        for name, value in (("CARLA_BUILD_JOBS", "0"), ("CARLA_BUILD_JOBS", "5"),
                            ("CARLA_MATERIALX_TIMEOUT_SECONDS", "0"),
                            ("CARLA_MATERIALX_TIMEOUT_SECONDS", "1201")):
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "out"
                result = self.recipe(output, **{name: value})
                self.assertEqual(64, result.returncode)
                self.assertFalse(output.exists())

    def test_missing_source_has_specific_log_and_fail_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.recipe(root / "out", CARLA_MATERIALX_SOURCE_DIR=str(root / "absent"))
            self.assertNotEqual(0, result.returncode)
            path, _ = self.failed_report(root / "out")
            self.assertIn("missing MaterialX source:", (path.parent / "preflight.log").read_text())
            self.assertFalse((root / "absent").exists())

    def test_13810_source_is_rejected_even_in_1385_named_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "MaterialX-1.38.5"
            source.mkdir()
            (source / "CMakeLists.txt").write_text(
                "set(MATERIALX_MAJOR_VERSION 1)\nset(MATERIALX_MINOR_VERSION 38)\nset(MATERIALX_BUILD_VERSION 10)\n")
            result = self.recipe(root / "out", CARLA_MATERIALX_SOURCE_DIR=str(source))
            self.assertNotEqual(0, result.returncode)
            path, _ = self.failed_report(root / "out")
            self.assertIn("source version must be 1.38.5", (path.parent / "preflight.log").read_text())
            self.assertFalse((path.parent / "configure.log").exists())

    def test_output_inside_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.recipe(Path(directory) / "out", CARLA_MATERIALX_SOURCE_DIR=directory)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to write", result.stderr)

    def test_non_arm_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "uname"
            fake.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
            fake.chmod(0o755)
            result = self.recipe(Path(directory) / "out",
                                 PATH=directory + os.pathsep + os.environ["PATH"])
            self.assertEqual(2, result.returncode)
            self.assertIn("native ARM64", result.stderr)

    def test_timeout_has_fail_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "cmake"
            fake.write_text("#!/bin/sh\nsleep 25\n")
            fake.chmod(0o755)
            result = self.recipe(root / "out", PATH=directory + os.pathsep + os.environ["PATH"],
                                 CARLA_MATERIALX_TIMEOUT_SECONDS="5")
            self.assertNotEqual(0, result.returncode)
            _, report = self.failed_report(root / "out")
            self.assertEqual(124, report["exit_code"])

    @unittest.skipUnless(os.environ.get("CARLA_MATERIALX_REPORT"), "set CARLA_MATERIALX_REPORT for real validation")
    def test_real_report_binds_all_six_libraries_and_xml(self):
        path = Path(os.environ["CARLA_MATERIALX_REPORT"])
        spec = importlib.util.spec_from_file_location("materialx_stage", ROOT / "scripts/carla/stage_report.py")
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        report = stage.validate_report(path, stage_id=STAGE, scope=SCOPE)
        for name in LIBRARIES:
            self.assertIn(f"retained.install/lib/lib{name}.a", report["evidence"])
        self.assertIn("retained.outputs/material.mtlx", report["evidence"])
        self.assertIn("retained.inputs/USD-BuildForLinux.sh", report["evidence"])
        self.assertTrue(all(value == "PASS" for value in report["checks"].values()))
        self.assertIn("XML read/validate PASS", (path.parent / "xml-read.log").read_text())
        self.assertTrue((path.parent / "install/libraries/bxdf/standard_surface.mtlx").is_file())

    @unittest.skipUnless(os.environ.get("CARLA_MATERIALX_REPORT"), "set CARLA_MATERIALX_REPORT for native smoke")
    def test_real_xml_rejects_malformed_invalid_type_and_overwrite(self):
        run = Path(os.environ["CARLA_MATERIALX_REPORT"]).parent
        smoke, libraries = run / "install/bin/materialx-smoke", run / "install/libraries"
        original = (run / "outputs/material.mtlx").read_bytes()
        tree = ET.fromstring(original)
        value = tree.find("./standard_surface[@name='surface']/input[@name='specular_roughness']")
        self.assertIsNotNone(value)
        value.set("type", "color3")
        value.set("value", "0.1, 0.2, 0.3")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "material.mtlx"
            for mode, content in (("read", b"<materialx><"), ("read", ET.tostring(tree)),
                                  ("write", original)):
                with self.subTest(mode=mode, size=len(content)):
                    path.write_bytes(content)
                    result = subprocess.run([str(smoke), mode, str(path), str(libraries)],
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(2, result.returncode, result.stderr)
                    self.assertEqual(content, path.read_bytes())
            result = subprocess.run([str(smoke), "read", str(run / "outputs/material.mtlx"),
                                     str(Path(directory) / "missing-libraries")],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(2, result.returncode)
            self.assertIn("no installed MaterialX libraries", result.stderr)


if __name__ == "__main__":
    unittest.main()
