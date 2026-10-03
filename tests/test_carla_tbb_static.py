"""Static TBB contracts/failure fixtures; real native PASS needs explicit artifacts."""

import ast
import hashlib
import importlib.util
import json
import subprocess
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-tbb-static.sh"
SMOKE = SCRIPT.with_name("tbb-static-smoke.cpp")
STAGE = "carla-tbb-static-native-arm64"
SCOPE = ("Native ARM64 TBB 2019u8 Release libtbb.a/libtbbmalloc.a PIC, no-exceptions, "
         "direct/static and whole-archive smoke ONLY; not Debug, shared coexistence, OpenUSD or UE Editor")
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"


def python_block(function):
    text = SCRIPT.read_text().split(function + "() {", 1)[1]
    return text.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]


class TbbStaticTest(unittest.TestCase):
    def recipe(self, **environment):
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=35,
                              env={**os.environ, "CARLA_BUILD_JOBS": "4",
                                   "CARLA_TBB_STATIC_TIMEOUT_SECONDS": "600", **environment})

    def test_syntax_and_isolated_static_targets(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        for code in re.findall(r"<<'PY'\n(.*?)\nPY", text, re.S):
            ast.parse(code)
        for token in (
            "tbb-static-$(date", '"${run_dir}/work/tbb"', '"${run_dir}/work/tbbmalloc"',
            '"TBB.OBJ"', '"MALLOC.OBJ"', "$(AR) rcsD $@",
            'patch --batch --fuzz=0 -p1 -d "${run_dir}/build-source"',
            "compiler=clang arch=aarch64", "exceptions=0", "-fno-rtti -fno-exceptions",
            "-fPIC", "-nostdinc++", "-stdlib=libc++", "--sysroot=${sysroot}",
            "timeout --kill-after=15s", '-j"${jobs}"',
            "-Wl,--whole-archive", "-Wl,-z,defs", "-Wl,-z,text",
            "source-retention-diff.json", ".exit-code.txt",
        ):
            with self.subTest(token=token):
                self.assertIn(token, text)
        for token in ("build-arm64-tbb.sh", "rm -rf", "apt-get", "git clone",
                      "allow-multiple-definition", "-ltbb ", "-ltbbmalloc "):
            self.assertNotIn(token, text)

    def test_local_ue_requires_static_release_libraries(self):
        rules = (ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/Intel/TBB/IntelTBB.Build.cs").read_text()
        for token in ('"libtbb.a"', '"libtbbmalloc.a"', '"TBB_USE_EXCEPTIONS=0"', "IntelTBB-2019u8"):
            self.assertIn(token, rules)

    def test_ue_rules_append_arm64_rtti_helper_after_tbb_archives(self):
        rules = (ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/Intel/TBB/IntelTBB.Build.cs").read_text()
        self.assertIn("Target.Architecture == UnrealArch.Arm64", rules)
        self.assertIn('"usd", "tbb-rtti-arm64", "libtbb-rtti.a"', rules)
        self.assertIn("Missing ARM64 TBB RTTI helper library", rules)
        tbb = rules.index('"libtbb.a"')
        helper = rules.index('"usd", "tbb-rtti-arm64", "libtbb-rtti.a"')
        self.assertLess(tbb, helper)

    def test_arm64_rtti_helper_abi_contract(self):
        directory = ROOT / "artifacts/carla/usd/tbb-rtti-arm64"
        if not (directory / "task-rtti.cpp").is_file():
            # The helper is generated into artifacts/, which is gitignored: a fresh clone has no
            # copy, and this ABI contract can only be checked where the helper was built.
            self.skipTest("generated ARM64 TBB RTTI helper is not present")
        source = (directory / "task-rtti.cpp").read_text()
        for token in (
            'extern const unsigned char _ZTVN10__cxxabiv117__class_type_infoE[]',
            'extern const char _ZTSN3tbb4taskE[] = "tbb::task"',
            'extern const TbbTaskTypeInfo _ZTIN3tbb4taskE',
            "_ZTVN10__cxxabiv117__class_type_infoE + 16",
        ):
            self.assertIn(token, source)
        self.assertNotIn("typeid", source)

        archive = directory / "libtbb-rtti.a"
        self.assertEqual(b"!<arch>\n", archive.read_bytes()[:8])
        result = subprocess.run(
            ["docker", "exec", "carla-build-session", "/usr/lib/llvm-18/bin/llvm-nm", "-A",
             "/artifacts/carla/usd/tbb-rtti-arm64/libtbb-rtti.a"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn(" D _ZTIN3tbb4taskE", result.stdout)
        self.assertIn(" R _ZTSN3tbb4taskE", result.stdout)
        self.assertIn(" U _ZTVN10__cxxabiv117__class_type_infoE", result.stdout)
        self.assertNotIn("_ZTI3tbb4task", result.stdout.replace("_ZTIN3tbb4taskE", ""))
        self.assertNotIn(" _ZTV3tbb4task", result.stdout)

    def test_smoke_exercises_runtime_and_malloc_not_header_only(self):
        source = SMOKE.read_text()
        for token in ("tbb::TBB_runtime_interface_version()", "TBB_INTERFACE_VERSION == 11008",
                      "_LIBCPP_VERSION", "__aarch64__", "TBB_USE_EXCEPTIONS",
                      "tbb::parallel_for", "tbb::parallel_reduce", "scalable_malloc",
                      "scalable_calloc", "scalable_realloc", "scalable_msize",
                      "scalable_aligned_malloc", "scalable_aligned_free",
                      "sum != 5000050000LL", "observed == 0", '"/proc/self/maps"',
                      "return code;", "TBB 2019u8 whole-archive smoke PASS"):
            self.assertIn(token, source)

    def test_invalid_limits_fail_before_artifacts(self):
        for name, value in (("CARLA_BUILD_JOBS", "0"), ("CARLA_BUILD_JOBS", "5"),
                            ("CARLA_BUILD_JOBS", "04"), ("CARLA_BUILD_JOBS", "bad"),
                            ("CARLA_TBB_STATIC_TIMEOUT_SECONDS", "0"),
                            ("CARLA_TBB_STATIC_TIMEOUT_SECONDS", "1201"),
                            ("CARLA_TBB_STATIC_TIMEOUT_SECONDS", "999999999999999")):
            with self.subTest(name=name, value=value), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "artifacts"
                result = self.recipe(CARLA_ARTIFACT_DIR=str(output), **{name: value})
                self.assertEqual(64, result.returncode, result.stdout + result.stderr)
                self.assertIn(name, result.stderr)
                self.assertFalse(output.exists())

    def test_non_arm_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            uname = root / "uname"
            uname.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
            uname.chmod(0o755)
            result = self.recipe(PATH=directory + os.pathsep + os.environ["PATH"],
                                 CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertEqual(2, result.returncode)
            self.assertFalse((root / "artifacts").exists())

    def test_archive_checker_rejects_renamed_dso_thin_and_empty_inputs(self):
        for data in (b"\x7fELF" + bytes(64), b"!<thin>\n", b""):
            with self.subTest(data=data[:8]), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "install/lib").mkdir(parents=True)
                (root / "work/tbb").mkdir(parents=True)
                (root / "work/tbb/tbb.objects.list").write_text("scheduler.o\n")
                (root / "install/lib/libtbb.a").write_bytes(data)
                result = subprocess.run([sys.executable, "-B", "-c", python_block("check_archives"),
                                         str(root), str(root / "absent-tools")],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("not a regular (non-thin) archive", result.stderr)
                self.assertFalse((root / "archive-objects.json").exists())

    def test_retention_rejects_changes_without_path_exemptions(self):
        code = python_block("retention")
        for mutation in ("edit", "delete", "add"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original, run = root / "original", root / "run"
                original.mkdir()
                run.mkdir()
                records = {}
                for name in ("src/a.cpp", "include/a.h", "build/Makefile.tbb", "Makefile", "LICENSE", "README"):
                    for tree in (original, run / "source", run / "build-source"):
                        path = tree / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("fixture\n")
                    records[name] = hashlib.sha256(b"fixture\n").hexdigest()
                for name in ("source.sha256.json", "patched-source.sha256.json"):
                    (run / name).write_text(json.dumps(records))
                changed = run / "build-source/src/a.cpp"
                if mutation == "edit":
                    changed.write_text("changed\n")
                elif mutation == "delete":
                    changed.unlink()
                else:
                    (run / "build-source/src/extra.o").write_bytes(b"unexpected generated output")
                result = subprocess.run([sys.executable, "-B", "-c", code, str(original), str(run)],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("TBB source changed", result.stderr)
                diff = json.loads((run / "source-retention-diff.json").read_text())["patched"]
                self.assertTrue(any(diff.values()))
                self.assertTrue((run / "patched.after.sha256.json").is_file())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_missing_source_writes_real_failure_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.recipe(CARLA_UE_DIR=str(root / "no-ue"),
                                 CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            paths = list((root / "artifacts/usd").glob("tbb-static-*/stage-report.json"))
            self.assertEqual(1, len(paths), result.stdout + result.stderr)
            report = json.loads(paths[0].read_text())
            self.assertEqual(STAGE, report["stage_id"])
            self.assertEqual(SCOPE, report["scope"])
            self.assertEqual("FAIL", report["status"])
            self.assertEqual(2, report["exit_code"])
            self.assertEqual("FAIL", report["checks"]["preflight"])
            self.assertIn("missing TBB source", (paths[0].parent / "preflight.log").read_text())
            self.assertFalse((paths[0].parent / "build-tbb.log").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_output_cannot_be_in_ue(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.recipe(CARLA_UE_DIR=directory, CARLA_ARTIFACT_DIR=directory + "/out")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("refusing to write", result.stderr)
            self.assertFalse((Path(directory) / "out").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker and local UE/toolchain")
    def test_timeout_is_not_partial_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git = root / "git"
            git.write_text("#!/bin/sh\nexec sleep 20\n")
            git.chmod(0o755)
            result = self.recipe(PATH=directory + os.pathsep + os.environ["PATH"],
                                 CARLA_TBB_STATIC_TIMEOUT_SECONDS="2",
                                 CARLA_ARTIFACT_DIR=str(root / "artifacts"))
            self.assertNotEqual(0, result.returncode)
            path = next((root / "artifacts/usd").glob("tbb-static-*/stage-report.json"))
            report = json.loads(path.read_text())
            self.assertEqual(124, report["exit_code"])
            self.assertEqual("FAIL", report["checks"]["worker-exit"])
            self.assertEqual("FAIL", report["status"])
            self.assertFalse((path.parent / "build-tbb.log").exists())

    @unittest.skipUnless(os.environ.get("CARLA_TBB_STATIC_REPORT"), "supply a real artifact report")
    def test_real_archives_smokes_and_retained_member_identity(self):
        spec = importlib.util.spec_from_file_location("tbb_static_reporter", SCRIPT.parent.parent / "stage_report.py")
        reporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reporter)
        path = Path(os.environ["CARLA_TBB_STATIC_REPORT"])
        report = reporter.validate_report(path, stage_id=STAGE, scope=SCOPE)
        self.assertEqual(0, report["exit_code"])
        records = json.loads((path.parent / "archive-objects.json").read_text())
        self.assertEqual({"tbb", "tbbmalloc"}, set(records))
        for kind, record in records.items():
            archive = path.parent / record["archive"]
            self.assertEqual(b"!<arch>\n", archive.read_bytes()[:8])
            self.assertEqual(record["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertGreater(len(record["members"]), 0)
            for member in record["members"].values():
                data = (path.parent / member["object"]).read_bytes()
                self.assertEqual(b"\x7fELF\x02\x01", data[:6])
                self.assertEqual(b"\x01\x00\xb7\x00", data[16:20])
                self.assertEqual(member["sha256"], hashlib.sha256(data).hexdigest())
        for name in ("smoke-static", "smoke-wholearchive"):
            text = (path.parent / (name + ".log")).read_text()
            metric = json.loads(next(line for line in text.splitlines() if line.startswith("{")))
            self.assertEqual(5000050000, metric["sum"])
            self.assertEqual(100000, metric["parallel_allocations"])
            self.assertGreater(metric["worker_entries"], 0)
            self.assertFalse(metric["dynamic_tbb_loaded"])
            self.assertEqual("0\n", (path.parent / (name + ".exit-code.txt")).read_text())
        for name in ("static-link.map", "wholearchive-link.map"):
            text = (path.parent / name).read_text()
            self.assertIn("libtbb.a(", text)
            self.assertIn("libtbbmalloc.a(", text)
        diffs = json.loads((path.parent / "source-retention-diff.json").read_text())
        self.assertFalse(any(change for diff in diffs.values() for change in diff.values()))
        self.assertIn("retained.static-targets.patch", report["evidence"])


if __name__ == "__main__":
    unittest.main()
