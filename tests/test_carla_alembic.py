"""Run only in ARM64 Docker; negative fixtures never build Alembic or Imath."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-alembic.sh"
IMATH = Path(os.environ["CARLA_IMATH_REPORT"]) if os.environ.get("CARLA_IMATH_REPORT") else None
STAGE = "carla-alembic-native-arm64"
SCOPE = "Native ARM64 Alembic 1.8.6 Release static PIC library and Ogawa write/read; not HDF5, Python, OpenUSD or UE Editor"
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"


@unittest.skipUnless(NATIVE, "run in native ARM64 carla-dev Docker")
class AlembicTest(unittest.TestCase):
    def recipe(self, output, **environment):
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env={**os.environ, "CARLA_ARTIFACT_DIR": str(output), "CARLA_IMATH_REPORT": str(IMATH) if IMATH else "",
                 **environment},
            capture_output=True, text=True, timeout=35,
        )

    def failed_report(self, output):
        reports = list((output / "usd").glob("alembic-*/stage-report.json"))
        self.assertEqual(1, len(reports))
        data = json.loads(reports[0].read_text())
        self.assertEqual("FAIL", data["status"])
        self.assertFalse((reports[0].parent / "build.log").exists())
        return reports[0], data

    def test_syntax_and_scope(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        for expected in ("-DUSE_HDF5=OFF", "-DUSE_PYALEMBIC=OFF", "-DALEMBIC_SHARED_LIBS=OFF",
                         "-DCMAKE_POSITION_INDEPENDENT_CODE=ON", "-Wl,--whole-archive",
                         "timeout --kill-after=15s", 'prerequisites=[{"stage_id": "carla-imath-native-arm64"'):
            self.assertIn(expected, text)
        for forbidden in ("rm -rf", "make clean", "build-arm64-imath.sh", "build-arm64-tbb.sh"):
            self.assertNotIn(forbidden, text)

    def test_invalid_bounds(self):
        for name, value in (("CARLA_BUILD_JOBS", "0"), ("CARLA_BUILD_JOBS", "5"),
                            ("CARLA_ALEMBIC_TIMEOUT_SECONDS", "0"),
                            ("CARLA_ALEMBIC_TIMEOUT_SECONDS", "1201")):
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "out"
                result = self.recipe(output, **{name: value})
                self.assertEqual(64, result.returncode, result.stderr)
                self.assertFalse(output.exists())

    def test_missing_imath_is_a_retained_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            result = self.recipe(output, CARLA_IMATH_REPORT=directory + "/missing/stage-report.json")
            self.assertNotEqual(0, result.returncode)
            path, data = self.failed_report(output)
            self.assertEqual("FAIL", data["checks"]["imath-before"])
            self.assertIn("No such file", (path.parent / "imath-before.log").read_text())

    @unittest.skipUnless(IMATH and IMATH.is_file(), "set CARLA_IMATH_REPORT for prerequisite mutation tests")
    def test_tampered_imath_source_library_and_report_are_rejected(self):
        for relative in ("source/src/Imath/half.cpp", "install/lib/libImath-3_1.a",
                         "install/include/Imath/half.h", "stage-report.json"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                copy = root / "imath"
                shutil.copytree(IMATH.parent, copy)
                target = copy / relative
                if relative == "stage-report.json":
                    data = json.loads(target.read_text())
                    data["stage_id"] = "unrelated-stage"
                    target.write_text(json.dumps(data))
                else:
                    target.write_bytes(target.read_bytes() + b"\ntampered\n")
                output = root / "out"
                result = self.recipe(output, CARLA_IMATH_REPORT=str(copy / "stage-report.json"))
                self.assertNotEqual(0, result.returncode)
                path, report = self.failed_report(output)
                self.assertEqual("FAIL", report["checks"]["imath-before"])
                log = (path.parent / "imath-before.log").read_text()
                self.assertIn("stage_id" if relative == "stage-report.json" else "SHA256 mismatch", log)

    @unittest.skipUnless(IMATH and IMATH.is_file(), "set CARLA_IMATH_REPORT for prerequisite validation")
    def test_pass_report_without_bound_imath_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            copy = root / "imath"
            shutil.copytree(IMATH.parent, copy)
            path = copy / "stage-report.json"
            report = json.loads(path.read_text())
            del report["evidence"]["retained.install/lib/libImath-3_1.a"]
            path.write_text(json.dumps(report))
            result = self.recipe(root / "out", CARLA_IMATH_REPORT=str(path))
            self.assertNotEqual(0, result.returncode)
            output, _ = self.failed_report(root / "out")
            self.assertIn("does not bind required input: install/lib/libImath-3_1.a",
                          (output.parent / "imath-before.log").read_text())

    @unittest.skipUnless(IMATH and IMATH.is_file(), "set CARLA_IMATH_REPORT for the source gate")
    def test_missing_source_is_not_synthesized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.recipe(root / "out", CARLA_ALEMBIC_SOURCE_DIR=str(root / "missing"))
            self.assertNotEqual(0, result.returncode)
            path, data = self.failed_report(root / "out")
            self.assertEqual("PASS", data["checks"]["imath-before"])
            self.assertIn("missing Alembic source:", (path.parent / "preflight.log").read_text())
            self.assertFalse((root / "missing").exists())

    @unittest.skipUnless(IMATH and IMATH.is_file(), "set CARLA_IMATH_REPORT for the timeout gate")
    def test_timeout_has_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "cmake"
            fake.write_text("#!/bin/sh\nsleep 20\n")
            fake.chmod(0o755)
            result = self.recipe(root / "out", PATH=directory + os.pathsep + os.environ["PATH"],
                                 CARLA_ALEMBIC_TIMEOUT_SECONDS="3")
            self.assertNotEqual(0, result.returncode)
            _, data = self.failed_report(root / "out")
            self.assertEqual(124, data["exit_code"])

    def test_output_cannot_be_inside_source(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.recipe(Path(directory) / "out", CARLA_ALEMBIC_SOURCE_DIR=directory,
                                 CARLA_IMATH_REPORT=str(IMATH) if IMATH else directory + "/unused.json")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to write", result.stderr)

    @unittest.skipUnless(os.environ.get("CARLA_ALEMBIC_REPORT") and IMATH, "set CARLA_ALEMBIC_REPORT and CARLA_IMATH_REPORT for real validation")
    def test_real_report_binds_imath_and_ogawa_outputs(self):
        path = Path(os.environ["CARLA_ALEMBIC_REPORT"])
        spec = importlib.util.spec_from_file_location("alembic_stage", ROOT / "scripts/carla/stage_report.py")
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        report = stage.validate_report(path, stage_id=STAGE, scope=SCOPE)
        self.assertEqual("carla-imath-native-arm64", report["prerequisites"][0]["stage_id"])
        self.assertEqual(hashlib.sha256(IMATH.read_bytes()).hexdigest(),
                         report["prerequisites"][0]["sha256"])
        self.assertIn("retained.outputs/mesh.abc", report["evidence"])
        self.assertIn("retained.source/lib/Alembic/Util/Config.h.in", report["evidence"])
        self.assertIn("libAlembic.a", (path.parent / "installed.sha256").read_text())
        self.assertIn("read PASS vertices=4 faces=2 samples=2", (path.parent / "ogawa-read.log").read_text())
        self.assertTrue(all(value == "PASS" for value in report["checks"].values()))

    @unittest.skipUnless(os.environ.get("CARLA_ALEMBIC_REPORT"), "set CARLA_ALEMBIC_REPORT for native smoke")
    def test_real_smoke_rejects_truncation_and_overwrite(self):
        run = Path(os.environ["CARLA_ALEMBIC_REPORT"]).parent
        binary = run / "install/bin/alembic-smoke"
        original = (run / "outputs/mesh.abc").read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mesh.abc"
            for mode, content in (("read", b""), ("read", original[:16]), ("write", original)):
                with self.subTest(mode=mode, size=len(content)):
                    path.write_bytes(content)
                    result = subprocess.run([str(binary), mode, str(path)], capture_output=True, timeout=30)
                    self.assertEqual(2, result.returncode, result.stderr)
                    self.assertEqual(content, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
