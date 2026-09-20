"""Failure fixtures and opt-in real osdCPU evidence; tests never start a build."""

import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-opensubdiv.sh"
SMOKE = SCRIPT.with_name("opensubdiv-smoke.cpp")
REPORTER = ROOT / "scripts/carla/stage_report.py"
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"
STAGE = "carla-opensubdiv-native-arm64"
SCOPE = ("Native ARM64 OpenSubdiv 3.6.0 Release osdCPU static PIC and subdivision smoke ONLY; "
         "not GPU backends, Python, full OpenSubdiv, OpenUSD or UE Editor/Cook")


class OpenSubdivTest(unittest.TestCase):
    def run_recipe(self, **environment):
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env={**os.environ, "CARLA_BUILD_JOBS": "4",
                 "CARLA_OPENSUBDIV_TIMEOUT_SECONDS": "600", **environment},
            capture_output=True, text=True, timeout=35,
        )

    def test_shell_syntax_and_bounded_cpu_pic_contract(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        for token in (
            'opensubdiv-$(date -u', 'cmake -S "${run_dir}/source"',
            '--parallel "${jobs}"', "ue-arm64-third-party.cmake",
            "-DCMAKE_POSITION_INDEPENDENT_CODE=ON", "-DBUILD_SHARED_LIBS=OFF",
            "-DNO_OPENGL=ON", "-DNO_CUDA=ON", "-DNO_OPENCL=ON", "-DNO_TBB=ON",
            "-DNO_OMP=ON", "-Wl,--whole-archive", "-Wl,-z,text", "-Wl,-z,defs",
            "-stdlib=libc++", "-nostdinc++", "timeout --kill-after=15s",
            '"${run_dir}/${step}.exit-code.txt"', "source.sha256.json",
            "stage.validate_report", "worker-exit",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for command in ("rm -rf", "apt-get", "git clone", "build-arm64-tbb.sh", "make clean"):
            self.assertNotIn(command, text)

    def test_smoke_uses_real_cpu_stencils_and_independent_coordinates(self):
        text = SMOKE.read_text()
        for token in (
            "_LIBCPP_VERSION", "OPENSUBDIV_VERSION_NUMBER == 30600",
            "Sdc::SCHEME_CATMARK", "VTX_BOUNDARY_EDGE_ONLY",
            "RefineUniform(uniform)", "Far::StencilTableFactory::Create",
            "Osd::CpuEvaluator::EvalStencils", "refined.GetNumVertices() != 9",
            "refined.GetNumFaces() != 4", "GetVertexChildVertex", "GetEdgeChildVertex",
            "GetFaceChildVertex", "0.25f", "1.75f", "std::isfinite",
            "evaluate(moved)", "expected[value] + offset[value % 3]", "return code;",
        ):
            self.assertIn(token, text)

    def test_invalid_bounds_fail_before_artifacts(self):
        cases = (("CARLA_BUILD_JOBS", "0"), ("CARLA_BUILD_JOBS", "5"),
                 ("CARLA_BUILD_JOBS", "04"), ("CARLA_BUILD_JOBS", "bad"),
                 ("CARLA_OPENSUBDIV_TIMEOUT_SECONDS", "0"),
                 ("CARLA_OPENSUBDIV_TIMEOUT_SECONDS", "1201"),
                 ("CARLA_OPENSUBDIV_TIMEOUT_SECONDS", "999999999999999999"))
        for name, value in cases:
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "artifacts"
                result = self.run_recipe(CARLA_ARTIFACT_DIR=str(output), **{name: value})
                self.assertEqual(64, result.returncode, result.stdout + result.stderr)
                self.assertIn(name, result.stderr)
                self.assertFalse(output.exists())

    def test_non_arm_execution_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "uname"
            fake.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
            fake.chmod(0o755)
            output = Path(directory) / "artifacts"
            result = self.run_recipe(PATH=directory + os.pathsep + os.environ["PATH"],
                                     CARLA_ARTIFACT_DIR=str(output))
            self.assertEqual(2, result.returncode)
            self.assertIn("native ARM64", result.stderr)
            self.assertFalse(output.exists())

    @unittest.skipUnless(NATIVE, "failure report tests require native ARM64 Docker")
    def test_missing_source_produces_fail_without_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_recipe(CARLA_UE_DIR=str(root / "absent-ue"),
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            paths = list((root / "artifacts/usd").glob("opensubdiv-*/stage-report.json"))
            self.assertEqual(1, len(paths), result.stdout + result.stderr)
            report = json.loads(paths[0].read_text())
            self.assertEqual(STAGE, report["stage_id"])
            self.assertEqual(SCOPE, report["scope"])
            self.assertEqual("FAIL", report["status"])
            self.assertEqual("FAIL", report["checks"]["preflight"])
            self.assertEqual(2, report["exit_code"])
            self.assertEqual("2\n", (paths[0].parent / "preflight.exit-code.txt").read_text())
            self.assertIn("missing OpenSubdiv source:", (paths[0].parent / "preflight.log").read_text())
            self.assertFalse((paths[0].parent / "configure.log").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_missing_config_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ue/Engine/Source/ThirdParty/OpenSubdiv/OpenSubdiv-3.6.0"
            source.mkdir(parents=True)
            (source / "CMakeLists.txt").write_text("project(OpenSubdiv)\n")
            result = self.run_recipe(CARLA_UE_DIR=str(root / "ue"),
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            log = next((root / "artifacts/usd").glob("opensubdiv-*/preflight.log")).read_text()
            self.assertIn("opensubdiv-config.cmake.in", log)
            self.assertFalse((source / "opensubdiv-config.cmake.in").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_outputs_cannot_be_written_into_ue(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_recipe(CARLA_UE_DIR=directory, CARLA_ARTIFACT_DIR=directory + "/out")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to write", result.stderr)
            self.assertFalse((Path(directory) / "out").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker with local UE source")
    def test_timeout_is_failed_report_not_partial_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "cmake"
            fake.write_text("#!/bin/sh\nexec sleep 20\n")
            fake.chmod(0o755)
            result = self.run_recipe(PATH=directory + os.pathsep + os.environ["PATH"],
                                     CARLA_OPENSUBDIV_TIMEOUT_SECONDS="3",
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            path = next((root / "artifacts/usd").glob("opensubdiv-*/stage-report.json"))
            report = json.loads(path.read_text())
            self.assertEqual("FAIL", report["status"])
            self.assertEqual(124, report["exit_code"])
            self.assertEqual("FAIL", report["checks"]["worker-exit"])
            self.assertFalse((path.parent / "build.log").exists())

    @unittest.skipUnless(os.environ.get("CARLA_OPENSUBDIV_REPORT"), "set report path for real evidence")
    def test_real_build_report_archive_and_smoke(self):
        spec = importlib.util.spec_from_file_location("opensubdiv_stage_report", REPORTER)
        reporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reporter)
        path = Path(os.environ["CARLA_OPENSUBDIV_REPORT"])
        report = reporter.validate_report(path, stage_id=STAGE, scope=SCOPE)
        self.assertTrue(all(value == "PASS" for value in report["checks"].values()))
        self.assertEqual(0, report["exit_code"])
        self.assertIn("retained.install/lib/libosdCPU.a", report["evidence"])
        self.assertIn("retained.source/opensubdiv-config.cmake.in", report["evidence"])
        self.assertIn("cmake.compile_commands.json", report["evidence"])
        smoke = (path.parent / "smoke.log").read_text()
        metrics = json.loads(next(line for line in smoke.splitlines() if line.startswith("{")))
        self.assertEqual(9, metrics["refined_vertices"])
        self.assertEqual(4, metrics["refined_faces"])
        self.assertEqual(54, metrics["checked_components"])
        self.assertTrue(metrics["translated_input"])
        self.assertIn("OpenSubdiv 3.6.0 osdCPU smoke PASS", smoke)
        self.assertIn("AArch64 ELF64 REL", (path.parent / "archive.log").read_text())
        for step in ("configure", "build", "install", "archive", "smoke-library",
                     "smoke-compile", "architecture", "linkage", "smoke", "retention"):
            self.assertEqual("0\n", (path.parent / (step + ".exit-code.txt")).read_text())
        cache = (path.parent / "work/CMakeCache.txt").read_text()
        for flag in ("NO_CUDA", "NO_OPENCL", "NO_OPENGL", "NO_OMP", "NO_TBB"):
            self.assertIn(flag + ":BOOL=ON", cache)
        self.assertIn("BUILD_SHARED_LIBS:BOOL=OFF", cache)
        self.assertFalse((path.parent / "install/lib/libosdGPU.a").exists())


if __name__ == "__main__":
    unittest.main()
