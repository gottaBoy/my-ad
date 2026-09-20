"""CPython stage contracts/failure fixtures; real success requires explicit artifacts."""

import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-python.sh"
HELPER = SCRIPT.with_name("python_stage.py")
spec = importlib.util.spec_from_file_location("native_cpython_stage", HELPER)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"
HEADER = ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/Python3/Linux/include/patchlevel.h"


class PythonStageTest(unittest.TestCase):
    def run_recipe(self, **environment):
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=35,
                              env={**os.environ, "CARLA_BUILD_JOBS": "4",
                                   "CARLA_PYTHON_TIMEOUT_SECONDS": "1800", **environment})

    def test_exact_local_patchlevel_is_pinned_not_latest(self):
        self.assertEqual("3.11.8", stage.read_version(HEADER))
        self.assertEqual("https://www.python.org/ftp/python/3.11.8/Python-3.11.8.tar.xz", stage.URL)
        self.assertEqual("9e06008c8901924395bc1da303eac567a729ae012baa182ab39269f650383bb3",
                         stage.ARCHIVE_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            header = Path(directory) / "patchlevel.h"
            original = HEADER.read_text()
            for old, new in (('"3.11.8"', '"3.11.9"'), ("PY_MICRO_VERSION        8", "PY_MICRO_VERSION        9"),
                             ("PY_RELEASE_SERIAL       0", "PY_RELEASE_SERIAL       1"),
                             ("PY_MAJOR_VERSION        3", "PY_MAJOR_VERSION        4")):
                with self.subTest(new=new):
                    header.write_text(original.replace(old, new))
                    with self.assertRaises(ValueError):
                        stage.read_version(header)

    def test_recipe_syntax_pic_and_isolation(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        for token in ('python-$(date -u', "timeout --kill-after=20s",
                      '--sysroot=${CARLA_PYTHON_SYSROOT}', '-O2 -fPIC',
                      "--enable-shared", "--with-static-libpython", "--without-ensurepip",
                      '--with-pkg-config=no', '-j"${jobs}"', "altinstall",
                      "-Wl,--whole-archive", "-Wl,-z,defs", "-Wl,-z,text",
                      "embed-shared", "embed-static", '"${prefix}/bin/python3.11" -I -B'):
            self.assertIn(token, text)
        for token in ("apt-get", "rm -rf", "update-alternatives", "UBT", "Build.sh"):
            self.assertNotIn(token, text)
        helper = HELPER.read_text()
        self.assertIn('f"self.parallel = {jobs}"', helper)
        self.assertIn('before.replace("-j0", f"-j{jobs}")', helper)
        self.assertIn('self.inc_dirs = list(self.compiler.include_dirs)', helper)

    def test_invalid_jobs_and_timeout_fail_before_artifacts(self):
        cases = (("CARLA_BUILD_JOBS", "0"), ("CARLA_BUILD_JOBS", "5"),
                 ("CARLA_BUILD_JOBS", "04"), ("CARLA_BUILD_JOBS", "four"),
                 ("CARLA_PYTHON_TIMEOUT_SECONDS", "0"), ("CARLA_PYTHON_TIMEOUT_SECONDS", "3601"),
                 ("CARLA_PYTHON_TIMEOUT_SECONDS", "999999999999999999"))
        for name, value in cases:
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "artifacts"
                result = self.run_recipe(CARLA_ARTIFACT_DIR=str(output), **{name: value})
                self.assertEqual(64, result.returncode, result.stdout + result.stderr)
                self.assertIn(name, result.stderr)
                self.assertFalse(output.exists())

    def test_non_arm_is_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "uname"
            fake.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
            fake.chmod(0o755)
            result = self.run_recipe(PATH=directory + os.pathsep + os.environ["PATH"],
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertEqual(2, result.returncode)
            self.assertFalse((root / "artifacts").exists())

    def test_archive_hash_failure_happens_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.tar.xz"
            archive.write_bytes(b"not official source")
            with self.assertRaisesRegex(ValueError, "SHA256"):
                stage.extract(archive, root / "out")
            self.assertFalse((root / "out").exists())

    def test_archive_path_and_symlink_are_rejected_even_with_matching_hash(self):
        for name, link in (("../escape", False), ("/absolute", False),
                           ("Python-3.11.8/unsafe", True)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "source.tar.xz"
                with tarfile.open(archive, "w:xz") as stream:
                    member = tarfile.TarInfo(name)
                    if link:
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/etc"
                        stream.addfile(member)
                    else:
                        member.size = 1
                        stream.addfile(member, io.BytesIO(b"x"))
                with mock.patch.object(stage, "ARCHIVE_SHA256", stage.digest(archive)):
                    with self.assertRaises(ValueError):
                        stage.extract(archive, root / "out")
                self.assertFalse((root / "out").exists())

    def test_snapshot_detects_changes_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "source.c"
            path.write_text("original\n")
            before = stage.snapshot(root)
            path.write_text("changed\n")
            self.assertNotEqual(before, stage.snapshot(root))
            (root / "link").symlink_to(path)
            with self.assertRaisesRegex(ValueError, "symlink"):
                stage.snapshot(root)

    def test_build_interpreters_disable_bytecode_even_with_ignore_environment(self):
        before = ("PYTHON_FOR_BUILD=@PYTHON_FOR_BUILD@\n"
                  "PYTHON_FOR_FREEZE=@PYTHON_FOR_FREEZE@\n"
                  "PYTHON_FOR_REGEN?=@PYTHON_FOR_REGEN@\ncompileall -j0\n")
        after = stage.constrain_makefile(before, 4)
        for name in ("PYTHON_FOR_BUILD", "PYTHON_FOR_FREEZE", "PYTHON_FOR_REGEN"):
            self.assertIn("@" + name + "@ -B", after)
        self.assertIn("compileall -j4", after)
        self.assertNotIn("-j0", after)
        with self.assertRaises(ValueError):
            stage.constrain_makefile(before.replace("@PYTHON_FOR_BUILD@", "unexpected"), 4)

    def test_retention_keeps_exact_diff_and_rejects_even_generated_bytecode(self):
        for mutation in ("bytecode", "edit", "delete", "unchanged"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                run = Path(directory)
                for folder, manifest in (("source", "pristine-source.sha256.json"),
                                         ("build-source", "build-source.sha256.json")):
                    root = run / folder
                    root.mkdir()
                    (root / "source.c").write_text("original\n")
                    (root / "LICENSE").write_text("retained\n")
                    stage.write_json(run / manifest, stage.snapshot(root))
                root = run / "build-source"
                if mutation == "bytecode":
                    (root / "Lib/__pycache__").mkdir(parents=True)
                    (root / "Lib/__pycache__/os.cpython-311.pyc").write_bytes(b"generated")
                elif mutation == "edit":
                    (root / "source.c").write_text("modified\n")
                elif mutation == "delete":
                    (root / "source.c").unlink()
                if mutation == "unchanged":
                    stage.check_retention(run)
                else:
                    with self.assertRaisesRegex(ValueError, "build-source changed"):
                        stage.check_retention(run)
                diff = json.loads((run / "source-retention-diff.json").read_text())["build-source"]
                self.assertTrue((run / diff["after_manifest"]).is_file())
                if mutation == "bytecode":
                    self.assertEqual(["Lib/__pycache__/os.cpython-311.pyc"], list(diff["added"]))
                elif mutation == "edit":
                    self.assertEqual(["source.c"], list(diff["changed"]))
                elif mutation == "delete":
                    self.assertEqual(["source.c"], list(diff["removed"]))
                else:
                    self.assertFalse(any(diff[k] for k in ("added", "removed", "changed")))

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_missing_ue_version_produces_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_recipe(CARLA_UE_DIR=str(root / "absent"),
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            reports = list((root / "artifacts/usd").glob("python-*/stage-report.json"))
            self.assertEqual(1, len(reports), result.stdout + result.stderr)
            report = json.loads(reports[0].read_text())
            self.assertEqual("FAIL", report["status"])
            self.assertEqual(stage.SCOPE, report["scope"])
            self.assertEqual("FAIL", report["checks"]["preflight"])
            self.assertFalse((reports[0].parent / "source.log").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_output_inside_ue_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_recipe(CARLA_UE_DIR=directory, CARLA_ARTIFACT_DIR=directory + "/out")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to write", result.stderr)
            self.assertFalse((Path(directory) / "out").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker and local UE headers/tools")
    def test_timeout_writes_failed_report_before_source_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "git"
            fake.write_text("#!/bin/sh\nexec sleep 20\n")
            fake.chmod(0o755)
            result = self.run_recipe(PATH=directory + os.pathsep + os.environ["PATH"],
                                     CARLA_PYTHON_TIMEOUT_SECONDS="2",
                                     CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            path = next((root / "artifacts/usd").glob("python-*/stage-report.json"))
            report = json.loads(path.read_text())
            self.assertEqual(124, report["exit_code"])
            self.assertEqual("FAIL", report["checks"]["worker-exit"])
            self.assertEqual("FAIL", report["status"])
            self.assertFalse((path.parent / "source.log").exists())

    @unittest.skipUnless(os.environ.get("CARLA_PYTHON_REPORT"), "requires explicit real stage artifact")
    def test_real_source_build_and_native_api_evidence(self):
        spec = importlib.util.spec_from_file_location("python_reporter", SCRIPT.parent.parent / "stage_report.py")
        reporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reporter)
        path = Path(os.environ["CARLA_PYTHON_REPORT"])
        report = reporter.validate_report(path, stage_id=stage.STAGE, scope=stage.SCOPE)
        self.assertEqual(set(stage.CHECKS), set(report["checks"]))
        self.assertEqual(0, report["exit_code"])
        self.assertIn("retained.install/lib/libpython3.11.a", report["evidence"])
        self.assertIn("retained.install/lib/libpython3.11.so.1.0", report["evidence"])
        runtime = json.loads((path.parent / "runtime.json").read_text())
        self.assertEqual("3.11.8", runtime["version"])
        self.assertEqual("aarch64", runtime["machine"])
        self.assertEqual(8, runtime["pointer_bytes"])
        self.assertIn("_decimal", runtime["extensions"])
        self.assertIn("optional", runtime)
        identity = json.loads((path.parent / "source-identity.json").read_text())
        self.assertEqual("3.11.8", identity["version"])
        if identity["origin"] == "python.org-source-release":
            self.assertEqual(stage.ARCHIVE_SHA256, identity["sha256"])
        for mode in ("shared", "static"):
            self.assertIn("embedding C API + dynamic extension PASS",
                          (path.parent / f"embed-{mode}.log").read_text())
        elf = json.loads((path.parent / "elf.json").read_text())
        self.assertGreater(elf["archive_members"], 100)
        self.assertTrue(all(item["machine"] == "AArch64" for item in elf["files"]))
        for step in stage.CHECKS[:-1]:
            self.assertEqual("0\n", (path.parent / (step + ".exit-code.txt")).read_text())
        differences = json.loads((path.parent / "source-retention-diff.json").read_text())
        for diff in differences.values():
            self.assertFalse(any(diff[k] for k in ("added", "removed", "changed")))


if __name__ == "__main__":
    unittest.main()
