import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/check-ufbx-fbx.py"
spec = importlib.util.spec_from_file_location("check_ufbx_fbx", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def ar_member(name, body):
    header = (name.ljust(16) + "0".ljust(12) + "0".ljust(6) + "0".ljust(6)
              + "644".ljust(8) + str(len(body)).ljust(10) + "`\n").encode("ascii")
    return header + body + (b"\n" if len(body) % 2 else b"")


def elf_header(machine=183, kind=1):
    data = bytearray(64)
    data[:6] = b"\x7fELF\x02\x01"
    data[16:18] = kind.to_bytes(2, "little")
    data[18:20] = machine.to_bytes(2, "little")
    return bytes(data)


def rejection(status="REJECTED"):
    return {
        "schema_version": 1, "stage": checker.STAGE, "scope": checker.SCOPE,
        "version": checker.VERSION, "commit": checker.COMMIT, "input": "/fixture.fbx",
        "space": "source", "status": status,
        "error_code": "unsupported_features" if status == "REJECTED" else "parse_error",
        "error": "Unsupported animation" if status == "REJECTED" else "Invalid FBX",
        "features": {key: int(key == "animation_curves" and status == "REJECTED") for key in checker.FEATURE_KEYS},
        "nodes": [], "meshes": [], "instances": [], "materials": [],
    }


class UfbxBackendTest(unittest.TestCase):
    def test_build_pins_official_source_and_shared_pic_object(self):
        build = (ROOT / "scripts/carla/build-arm64-ufbx.sh").read_text()
        cmake = (ROOT / "scripts/carla/ufbx-probe/CMakeLists.txt").read_text()
        for token in (
            "tag=v0.23.0", f"commit={checker.COMMIT}", "https://github.com/ufbx/ufbx.git",
            'rev-parse "${tag}^{commit}"', "sparse-checkout init --cone",
            "diff --exit-code HEAD", "--static-library", "llvm-readelf",
        ):
            self.assertIn(token, build)
        self.assertNotIn("apt-get", build)
        self.assertNotIn("libfbxsdk", build)
        self.assertIn("POSITION_INDEPENDENT_CODE ON", cmake)
        self.assertIn("add_library(ufbx SHARED $<TARGET_OBJECTS:ufbx_objects>)", cmake)
        self.assertIn("add_library(ufbx_static STATIC $<TARGET_OBJECTS:ufbx_objects>)", cmake)
        self.assertIn("ARCHIVE DESTINATION lib", cmake)
        self.assertIn('"static-library": args.static_library', SCRIPT.read_text())

    def test_jobs_are_bounded_before_docker_or_network_access(self):
        for value in ("0", "-1", "5", "32", "four", "04", "4;exit 0"):
            with self.subTest(value=value):
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts/carla/build-arm64-ufbx.sh")],
                    env={**os.environ, "CARLA_BUILD_JOBS": value}, capture_output=True, text=True,
                )
                self.assertEqual(64, result.returncode)
                self.assertIn("1 to 4", result.stderr)

    def test_archive_checks_every_object_and_skips_symbol_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "libufbx.a"
            path.write_bytes(b"!<arch>\n" + ar_member("/", b"symbols")
                             + ar_member("first.o/", elf_header()) + ar_member("second.o/", elf_header()))
            self.assertEqual(["first.o", "second.o"], checker.verify_static_archive(path))
            path.write_bytes(b"!<arch>\n" + ar_member("first.o/", elf_header())
                             + ar_member("second.o/", elf_header(machine=62)))
            with self.assertRaisesRegex(ValueError, "non-AArch64"):
                checker.verify_static_archive(path)

    def test_archive_rejects_empty_thin_truncated_and_nonrelocatable_members(self):
        cases = (
            b"!<arch>\n", b"!<thin>\n",
            b"!<arch>\n" + ar_member("bad.o/", b"not an object"),
            b"!<arch>\n" + ar_member("bad.o/", elf_header(kind=3)),
            (b"!<arch>\n" + ar_member("bad.o/", elf_header()))[:-1],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "libufbx.a"
            for data in cases:
                with self.subTest(data=data[:24]):
                    path.write_bytes(data)
                    with self.assertRaises(ValueError):
                        checker.verify_static_archive(path)

    def test_counts_and_vectors_reject_boolean_and_nonfinite_data(self):
        self.assertEqual(3, checker.integer(3))
        for value in (True, False, -1, "1", 1.0, float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    checker.integer(value)
        for value in ([0, 0], [True, 0, 0], [float("inf"), 0, 0], [float("nan"), 0, 0]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    checker.vector(value, 3)

    def test_json_rejects_duplicate_keys_and_nan(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            for content in ('{"status":"PASS","status":"FAIL"}', '{"value":NaN}', '{"value":Infinity}'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    checker.read_json(path)

    def test_rejection_requires_clean_exit_and_detected_unsupported_feature(self):
        report = rejection()
        checker.evaluate(report, 2, "/fixture.fbx", "source", "REJECTED")
        for code in (0, 1, -11, 139, None, True):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    checker.evaluate(report, code, "/fixture.fbx", "source", "REJECTED")
        report["features"]["animation_curves"] = 0
        with self.assertRaisesRegex(ValueError, "no unsupported feature"):
            checker.evaluate(report, 2, "/fixture.fbx", "source", "REJECTED")

    def test_partial_output_or_wrong_scope_cannot_pass_rejection(self):
        for mutation in ({"nodes": [{"id": 0}]}, {"scope": "Editor/Cook"}, {"error": ""}, {"commit": "0"*40}):
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    checker.evaluate({**rejection(), **mutation}, 2, "/fixture.fbx", "source", "REJECTED")

    def test_corrupt_input_requires_parse_failure_not_crash_or_unsupported(self):
        checker.evaluate(rejection("FAIL"), 1, "/fixture.fbx", "source", "FAIL")
        with self.assertRaises(ValueError):
            checker.evaluate(rejection(), 2, "/fixture.fbx", "source", "FAIL")

    def test_independent_geometry_and_normal_matrix_oracle(self):
        parent = [1, 0, 0, 3, 0, 1, 0, 2, 0, 0, 1, 0]
        geometry = [-2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 4]
        world = checker.multiply(parent, geometry)
        self.assertEqual([1, 2, 4], checker.transform(world, [1, 0, 0]))
        self.assertEqual([3, 2, 1], checker.transform(parent, [0, 0, 1]))
        det, normal = checker.normal_matrix(world)
        self.assertEqual(-2, det)
        checker.close(normal, [-0.5, 0, 0, 0, 1, 0, 0, 0, 1], "wrong inverse-transpose")


if __name__ == "__main__":
    unittest.main()
