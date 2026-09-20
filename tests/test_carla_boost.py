"""Native ARM64 Boost stage: bounded failure fixtures and optional real evidence."""

import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-boost.sh"
spec = importlib.util.spec_from_file_location("boost_stage", SCRIPT.with_name("boost_stage.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"


@unittest.skipUnless(NATIVE, "native ARM64 carla-dev Docker required")
class BoostTest(unittest.TestCase):
    def recipe(self, root, **env):
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=40,
                              env={**os.environ, "CARLA_ARTIFACT_DIR": str(root), **env})

    def test_syntax_and_contract(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        self.assertEqual(("atomic", "chrono", "filesystem", "iostreams", "program_options",
                          "python311", "regex", "system", "thread"), helper.LIBRARIES)
        text = SCRIPT.read_text()
        for expected in ("./bootstrap.sh", "./b2", "--layout=tagged", "link=static,shared",
                         "-Wl,--whole-archive", "-j\"${jobs}\""):
            self.assertIn(expected, text)

    def test_invalid_bounds(self):
        for name, value in (("CARLA_BUILD_JOBS", "5"), ("CARLA_BUILD_JOBS", "0"),
                            ("CARLA_BOOST_TIMEOUT_SECONDS", "0"), ("CARLA_BOOST_TIMEOUT_SECONDS", "3601")):
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "out"
                result = self.recipe(root, **{name: value})
                self.assertEqual(64, result.returncode)
                self.assertFalse(root.exists())

    def test_wrong_python_hash_fails_before_source_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.recipe(root, CARLA_PYTHON_REPORT_SHA256="0" * 64)
            self.assertNotEqual(0, result.returncode)
            report = next((root / "usd").glob("boost-*/stage-report.json"))
            self.assertEqual("FAIL", json.loads(report.read_text())["status"])
            self.assertIn("Python report SHA256 mismatch", (report.parent / "python-before.log").read_text())
            self.assertFalse((report.parent / "source").exists())

    def test_fake_official_archive_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "source.tar.bz2"
            archive.write_bytes(b"not official source")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                helper.extract(archive, root / "extracted")
            self.assertFalse((root / "extracted").exists())

    def test_output_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.recipe(Path(temp) / "out", CARLA_UE_DIR=temp)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to write", result.stderr)

    def test_native_wrapper_keeps_ue_target_and_handles_bootstrap_language(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prefix = Path(helper.PYTHON_REPORT).parent / "install"
            (root / "python-prefix.txt").write_text(str(prefix))
            with mock.patch.dict(os.environ, {"CARLA_LLVM_BIN": "/usr/lib/llvm-18/bin"}):
                helper.toolchain(root, Path("/workspace/unreal-engine"))
            compiler = root / "compiler/clang++"
            target = subprocess.check_output(
                [str(compiler), "--target=arm64-pc-linux", "-dumpmachine"], text=True)
            self.assertEqual("aarch64-unknown-linux-gnueabi", target.strip())
            result = subprocess.run(
                [str(compiler), "--target=arm64-pc-linux", "-x", "c++", "-std=c++11",
                 "-", "-o", str(root / "bootstrap-probe")],
                input="#include <string>\nint main() { return std::string(\"ok\").size() != 2; }\n",
                text=True, capture_output=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
            subprocess.run([str(root / "bootstrap-probe")], check=True, timeout=10)

    def test_non_arm_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "uname"
            fake.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
            fake.chmod(0o755)
            result = self.recipe(root / "out", PATH=temp + os.pathsep + os.environ["PATH"])
            self.assertEqual(2, result.returncode)
            self.assertFalse((root / "out").exists())

    def test_timeout_keeps_fail_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "python3"
            real = subprocess.check_output(["which", "python3"], text=True).strip()
            fake.write_text(
                "#!/bin/sh\ncase \"$*\" in *python-before*) sleep 20 ;; esac\nexec \"" + real + "\" \"$@\"\n")
            fake.chmod(0o755)
            result = self.recipe(root / "out", PATH=temp + os.pathsep + os.environ["PATH"],
                                 CARLA_BOOST_TIMEOUT_SECONDS="2")
            self.assertNotEqual(0, result.returncode)
            path = next((root / "out/usd").glob("boost-*/stage-report.json"))
            data = json.loads(path.read_text())
            self.assertEqual(124, data["exit_code"])
            self.assertEqual("FAIL", data["status"])
            self.assertFalse((path.parent / "source").exists())

    @unittest.skipUnless(os.environ.get("CARLA_BOOST_REPORT"), "set CARLA_BOOST_REPORT for real artifact")
    def test_real_report_and_prefix(self):
        path = Path(os.environ["CARLA_BOOST_REPORT"])
        report = helper.reporter().validate_report(path, stage_id=helper.STAGE, scope=helper.SCOPE)
        self.assertEqual(helper.PYTHON_SHA256, report["prerequisites"][0]["sha256"])
        consumer = json.loads((path.parent / "consumer.json").read_text())
        self.assertEqual(set(helper.LIBRARIES), set(consumer["libraries"]))
        for name in helper.LIBRARIES:
            self.assertIn(f"-mt-a64.a", consumer["libraries"][name]["static"])
            self.assertIn(f"-mt-a64.so.1.82.0", consumer["libraries"][name]["shared"]["path"])
        self.assertIn("Boost.Python 1.82.0 CPython 3.11.8 import/call/class/exception PASS",
                      (path.parent / "runtime.log").read_text())


if __name__ == "__main__":
    unittest.main()
