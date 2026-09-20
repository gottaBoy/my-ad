import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/carla/check_ue_meshbridge.py"
spec = importlib.util.spec_from_file_location("check_ue_meshbridge", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def probe_report(kind="static", input_path=None):
    negative = kind in {"unsupported", "truncated"}
    report = {
        "stage": checker.STAGE, "scope": checker.SCOPE,
        "coordinate_policy": checker.COORDINATE_POLICY,
        "status": "FAIL" if negative else "PASS",
        "input": "" if input_path is None else str(input_path),
        "error": ("Animation is unsupported by the static bridge" if kind == "unsupported"
                  else "Truncated FBX data") if negative else "",
        "self_tests": 25 if kind == "self-test" else 0,
        "vertices": 0 if negative or kind == "self-test" else 8,
        "triangles": 0 if negative or kind == "self-test" else 12,
        "material_slots": 0 if negative or kind == "self-test" else 2,
        "serialized_bytes": 0 if negative or kind == "self-test" else 1024,
    }
    if kind in {"static", "materials"}:
        report["bounds_cm"] = [-100, -100, 0, 100, 100, 200]
    return report


class MeshBridgeEvaluatorTest(unittest.TestCase):
    def test_self_test_does_not_require_outer_mesh_geometry(self):
        report = probe_report("self-test")
        for count in (25, 25.0, 26):
            with self.subTest(count=count):
                checker.evaluate_report({**report, "self_tests": count},
                                        returncode=0, kind="self-test")
        for count in (0, 6, 7, 19, 24, True, "25", 25.5):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, "self_tests"):
                    checker.evaluate_report({**report, "self_tests": count},
                                            returncode=0, kind="self-test")

    def test_static_metrics_require_nonzero_counts_and_serialization(self):
        report = probe_report()
        checker.evaluate_report(report, returncode=0, kind="static")
        checker.evaluate_report({**report, "vertices": 8.0}, returncode=0, kind="static")
        for field in ("vertices", "triangles", "material_slots", "serialized_bytes"):
            for value in (0, -1, True, "1", None, 1.5, float("nan"), float("inf")):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        checker.evaluate_report({**report, field: value}, returncode=0, kind="static")
        with self.assertRaisesRegex(ValueError, "material_slots"):
            checker.evaluate_report({**report, "material_slots": 1}, returncode=0, kind="materials")

    def test_reports_must_match_scope_schema_input_and_process_status(self):
        report = probe_report(input_path="/fixtures/cube.fbx")
        for mutation in (
            {"stage": "assimp-fbx-backend"}, {"scope": "Editor/Cook"},
            {"coordinate_policy": "unknown"}, {"input": "/another.fbx"}, {"status": "FAIL"},
            {"error": "still failed"}, {"error": None}, {"bounds_cm": None},
            {"bounds_cm": [0, 0, 0, -1, 1, 1]}, {"bounds_cm": [0, 0, 0, 1, 1, float("nan")]},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    checker.evaluate_report({**report, **mutation}, returncode=0,
                                            kind="static", input_path="/fixtures/cube.fbx")
        for code in (-11, -6, 1, 2, 139, 0.0, False, None):
            with self.subTest(code=code):
                with self.assertRaisesRegex(ValueError, "clean exit"):
                    checker.evaluate_report(report, returncode=code,
                                            kind="static", input_path="/fixtures/cube.fbx")
        with self.assertRaises(ValueError):
            checker.evaluate_report([], returncode=0, kind="static")

    def test_rejections_need_exit_two_fail_and_specific_unsupported_error(self):
        for kind in ("unsupported", "truncated"):
            report = probe_report(kind)
            checker.evaluate_report(report, returncode=2, kind=kind)
            for mutation in ({"status": "PASS"}, {"error": ""}, {"error": "   "}, {"vertices": 1}):
                with self.subTest(kind=kind, mutation=mutation):
                    with self.assertRaises(ValueError):
                        checker.evaluate_report({**report, **mutation}, returncode=2, kind=kind)
            for code in (0, 1, -11, 139):
                with self.subTest(kind=kind, code=code):
                    with self.assertRaisesRegex(ValueError, "clean exit"):
                        checker.evaluate_report(report, returncode=code, kind=kind)
        with self.assertRaisesRegex(ValueError, "unsupported error"):
            checker.evaluate_report({**probe_report("unsupported"), "error": "file not found"},
                                    returncode=2, kind="unsupported")


class MeshBridgeRunnerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.program = self.root / "binary with spaces" / "CarlaMeshBridge"
        self.program.parent.mkdir()
        self.program.write_bytes(b"mock program; never executed\n")
        self.program.chmod(0o755)
        self.ue_root = self.root / "ue"
        self.fixtures = self.ue_root / "fixtures"
        self.fixtures.mkdir(parents=True)
        for _, filename, _ in checker.CASES:
            if filename is not None:
                (self.fixtures / filename).write_bytes(b"mock FBX fixture\n" + bytes(range(64)))
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.build_command = self.run_dir / "build-command.json"
        self.build_argv = ["timeout", "300", "bash", "/ue/Build.sh", "CarlaMeshBridge"]
        self.build_command.write_text(json.dumps(self.build_argv), encoding="utf-8")
        (self.run_dir / "build.log").write_text("mock build log\n", encoding="utf-8")
        (self.run_dir / "ue-tracked.patch").write_bytes(b"")
        (self.run_dir / "source-files.sha256").write_text("mock source manifest\n", encoding="utf-8")
        (self.run_dir / "ue-commit.txt").write_text("a" * 40 + "\n", encoding="utf-8")
        (self.run_dir / "validation.log").write_text("still open\n", encoding="utf-8")
        self.assimp_evidence = self.root / "assimp-tests.log"
        self.assimp_evidence.write_text("mock Assimp results\n", encoding="utf-8")
        self.assimp_report = self.root / "assimp" / "stage-report.json"
        self.installed_library = self.root / "assimp" / "install" / "lib" / "libassimp.so"
        self.installed_library.parent.mkdir(parents=True)
        self.installed_library.write_bytes(b"mock Assimp shared library\n")
        self.runtime_library = self.program.parent / "libassimp.so.6"
        self.runtime_library.write_bytes(self.installed_library.read_bytes())
        self.write_prerequisite()
        self.args = dict(program=self.program, fixtures=self.fixtures, run_dir=self.run_dir,
                         ue_root=self.ue_root, ue_commit="a" * 40, assimp_report=self.assimp_report,
                         build_command=self.build_command, timeout_seconds=30)
        self.run_mock = self.patch(checker.subprocess, "run", side_effect=self.fake_process)
        self.get_core = self.patch(checker.resource, "getrlimit", return_value=(4096, 8192))
        self.set_core = self.patch(checker.resource, "setrlimit")

    def patch(self, owner, name, **kwargs):
        patcher = mock.patch.object(owner, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def write_prerequisite(self, **overrides):
        values = dict(
            stage_id=checker.ASSIMP_STAGE, scope=checker.ASSIMP_SCOPE, exit_code=0,
            required_checks=["fbx"], checks={"fbx": "PASS"},
            evidence={"fbx": self.assimp_evidence, "library": self.installed_library},
            sources={"assimp": {"location": "/sources/assimp", "revision": "b" * 40}},
            command=["mock-assimp-tests"],
        )
        values.update(overrides)
        return checker.stage_report.write_report(self.assimp_report, **values)

    def decode_invocation(self, argv):
        output = Path(next(value.removeprefix("-output=") for value in argv if value.startswith("-output=")))
        if "-self-test" in argv:
            return output, "self-test", None
        input_path = Path(next(value.removeprefix("-input=") for value in argv if value.startswith("-input=")))
        kind = {
            "BlenderCube.fbx": "static", "MultiMatId.fbx": "materials",
            "AnimatedCharacter.fbx": "unsupported", "MorphTargets.fbx": "unsupported",
            "truncated.fbx": "truncated",
        }[input_path.name]
        return output, kind, input_path

    def fake_process(self, argv, **kwargs):
        self.assertEqual(str(self.program), argv[0])
        self.assertTrue(all(flag in argv for flag in checker.RUNTIME_FLAGS))
        self.assertEqual(self.program.parent, kwargs["cwd"])
        self.assertEqual(30, kwargs["timeout"])
        self.assertEqual(subprocess.STDOUT, kwargs["stderr"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.set_core.assert_called_once_with(checker.resource.RLIMIT_CORE, (0, 8192))
        output, kind, input_path = self.decode_invocation(argv)
        self.assertFalse(output.exists())
        output.write_text(json.dumps(probe_report(kind, input_path)), encoding="utf-8")
        kwargs["stdout"].write(b"mock native process output\n")
        return subprocess.CompletedProcess(argv, 2 if kind in {"unsupported", "truncated"} else 0)

    def run_checks(self, **overrides):
        return checker.run_checks(**{**self.args, **overrides})

    def artifact(self, report, name):
        return (self.run_dir / report["evidence"][name]["path"]).resolve()

    def validate(self):
        return checker.stage_report.validate_report(
            self.run_dir / "stage-report.json", stage_id=checker.STAGE, scope=checker.SCOPE)

    def test_six_mocked_invocations_derive_scoped_pass_and_complete_evidence(self):
        report = self.run_checks()
        self.assertEqual("PASS", report["status"])
        self.assertEqual(checker.REQUIRED_CHECKS, report["required_checks"])
        self.assertEqual({name: "PASS" for name in checker.REQUIRED_CHECKS}, report["checks"])
        self.assertEqual(6, self.run_mock.call_count)
        outputs = set()
        for name, _, kind in checker.CASES:
            result = json.loads(self.artifact(report, name).read_text())
            self.assertEqual(2 if kind in {"unsupported", "truncated"} else 0, result["returncode"])
            raw_path = self.artifact(report, f"{name}.raw")
            outputs.add(raw_path)
            raw = json.loads(raw_path.read_text())
            self.assertEqual("FAIL" if kind in {"unsupported", "truncated"} else "PASS", raw["status"])
            argv = json.loads(self.artifact(report, f"{name}.command").read_text())
            self.assertIn(f"-output={raw_path}", argv)
            self.assertIn("mock native process output", self.artifact(report, f"{name}.log").read_text())
        self.assertEqual(6, len(outputs))
        self.assertEqual((self.fixtures / "BlenderCube.fbx").read_bytes()[:32],
                         self.artifact(report, "fixture.truncated").read_bytes())
        self.assertEqual("a" * 40, report["sources"]["ue"]["revision"])
        self.assertEqual("b" * 40, report["sources"]["assimp"]["revision"])
        self.assertEqual(checker.ASSIMP_STAGE, report["prerequisites"][0]["stage_id"])
        self.assertEqual(checker.ASSIMP_SCOPE, report["prerequisites"][0]["scope"])
        self.set_core.assert_has_calls([
            mock.call(checker.resource.RLIMIT_CORE, (0, 8192)),
            mock.call(checker.resource.RLIMIT_CORE, (4096, 8192)),
        ])
        self.assertEqual(report, self.validate())

    def test_case_and_work_directories_are_host_readable(self):
        report = self.run_checks()
        for name, _, _ in checker.CASES:
            with self.subTest(case=name):
                case_dir = self.artifact(report, f"{name}.raw").parent
                self.assertEqual(0o755, case_dir.stat().st_mode & 0o777)
                self.assertEqual(0o755, case_dir.parent.stat().st_mode & 0o777)

    def test_runtime_copy_is_hashed_separately_and_revalidated_after_report(self):
        report = self.run_checks()
        self.assertEqual("PASS", report["status"])
        self.assertEqual(self.runtime_library, self.artifact(report, "runtime-library"))
        self.assertNotEqual(self.installed_library, self.artifact(report, "runtime-library"))
        expected = hashlib.sha256(self.installed_library.read_bytes()).hexdigest()
        self.assertEqual(expected, report["evidence"]["runtime-library"]["sha256"])
        preflight = json.loads(self.artifact(report, "preflight").read_text())
        self.assertEqual(expected, preflight["runtime_library"]["prerequisite_sha256"])
        self.assertEqual(expected, preflight["runtime_library"]["sha256"])
        self.runtime_library.write_bytes(b"runtime copy changed after report\n")
        checker.stage_report.validate_report(
            self.assimp_report, stage_id=checker.ASSIMP_STAGE, scope=checker.ASSIMP_SCOPE)
        with self.assertRaisesRegex(checker.stage_report.ReportError, "SHA256 mismatch"):
            self.validate()

    def test_missing_runtime_copy_blocks_all_invocations(self):
        self.runtime_library.unlink()
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["preflight"])
        self.assertIsNone(report["evidence"]["runtime-library"]["sha256"])
        self.assertIn("runtime Assimp library is missing", self.artifact(report, "preflight").read_text())
        self.run_mock.assert_not_called()
        self.set_core.assert_not_called()

    def test_different_runtime_copy_fails_even_with_matching_manifest_text(self):
        expected = hashlib.sha256(self.installed_library.read_bytes()).hexdigest()
        (self.run_dir / "binaries.sha256").write_text(
            f"{expected}  {self.runtime_library}\n", encoding="utf-8")
        self.runtime_library.write_bytes(b"different runtime Assimp build\n")
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("runtime Assimp library SHA256 mismatch", self.artifact(report, "preflight").read_text())
        self.assertNotEqual(expected, report["evidence"]["runtime-library"]["sha256"])
        self.run_mock.assert_not_called()

    def test_prerequisite_library_evidence_is_mandatory(self):
        self.write_prerequisite(evidence={"fbx": self.assimp_evidence})
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("missing named library evidence", self.artifact(report, "preflight").read_text())
        self.run_mock.assert_not_called()

    def test_runtime_copy_matching_tampered_install_does_not_bypass_prerequisite(self):
        self.installed_library.write_bytes(b"unverified library build\n")
        self.runtime_library.write_bytes(self.installed_library.read_bytes())
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("SHA256 mismatch", self.artifact(report, "preflight").read_text())
        self.run_mock.assert_not_called()

    def test_runtime_change_blocks_the_next_invocation(self):
        def replace_library(argv, **kwargs):
            completed = self.fake_process(argv, **kwargs)
            self.runtime_library.write_bytes(b"replaced during first case\n")
            return completed
        self.run_mock.side_effect = replace_library
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, self.run_mock.call_count)
        self.assertEqual("MISSING", report["checks"]["blender-cube"])
        self.assertIn("runtime Assimp library SHA256 mismatch", self.artifact(report, "preflight").read_text())

    def test_runtime_change_during_last_case_cannot_produce_pass(self):
        def replace_library(argv, **kwargs):
            completed = self.fake_process(argv, **kwargs)
            if self.decode_invocation(argv)[1] == "truncated":
                self.runtime_library.write_bytes(b"replaced during last case\n")
            return completed
        self.run_mock.side_effect = replace_library
        report = self.run_checks()
        self.assertEqual(6, self.run_mock.call_count)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["preflight"])
        self.assertIn("runtime Assimp library SHA256 mismatch", self.artifact(report, "preflight").read_text())

    def test_completed_build_artifacts_are_hashed_but_active_validation_log_is_not(self):
        report = self.run_checks()
        paths = {self.artifact(report, name) for name in report["evidence"]}
        for name in ("build-command.json", "build.log", "source-files.sha256", "ue-tracked.patch"):
            self.assertIn(self.run_dir / name, paths)
        self.assertNotIn(self.run_dir / "validation.log", paths)
        (self.run_dir / "validation.log").write_text("appended later\n", encoding="utf-8")
        self.validate()
        (self.run_dir / "source-files.sha256").write_text("changed manifest\n", encoding="utf-8")
        with self.assertRaisesRegex(checker.stage_report.ReportError, "SHA256 mismatch"):
            self.validate()

    def test_missing_or_legacy_prerequisite_blocks_invocations_without_migration(self):
        self.assimp_report.write_text('{"status": "PASS"}\n', encoding="utf-8")
        original = self.assimp_report.read_bytes()
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.run_mock.assert_not_called()
        self.assertEqual("MISSING", report["checks"]["self-test"])
        self.assertIn("Assimp prerequisite", self.artifact(report, "preflight").read_text())
        self.assertEqual(original, self.assimp_report.read_bytes())

    def test_wrong_prerequisite_scope_and_missing_assimp_source_are_rejected(self):
        self.write_prerequisite(scope="Editor/Cook")
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.run_mock.assert_not_called()
        self.write_prerequisite(sources={"other": {"location": "/source", "revision": "b" * 40}})
        report = self.run_checks(run_dir=self.root / "second-run")
        self.assertEqual("FAIL", report["status"])
        self.run_mock.assert_not_called()

    def test_prerequisite_evidence_is_revalidated(self):
        self.run_checks()
        self.assimp_evidence.write_text("changed after evaluation\n", encoding="utf-8")
        with self.assertRaisesRegex(checker.stage_report.ReportError, "SHA256 mismatch"):
            self.validate()

    def test_crash_is_not_accepted_even_when_program_writes_pass(self):
        def crash(argv, **kwargs):
            self.fake_process(argv, **kwargs)
            return subprocess.CompletedProcess(argv, -11)
        self.run_mock.side_effect = crash
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["exit_code"])
        self.assertIn("expected clean exit", self.artifact(report, "self-test").read_text())
        self.assertIn("-11", self.artifact(report, "self-test").read_text())
        self.set_core.assert_called_with(checker.resource.RLIMIT_CORE, (4096, 8192))

    def test_timeout_is_failure_even_with_valid_pass_json(self):
        def timeout(argv, **kwargs):
            self.fake_process(argv, **kwargs)
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        self.run_mock.side_effect = timeout
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        result = json.loads(self.artifact(report, "self-test").read_text())
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["returncode"])
        self.assertIn("timed out", self.artifact(report, "self-test.log").read_text())
        self.set_core.assert_called_with(checker.resource.RLIMIT_CORE, (4096, 8192))

    def test_spawn_error_saves_diagnostics_and_cannot_pass(self):
        self.run_mock.side_effect = OSError("loader failed")
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("loader failed", self.artifact(report, "self-test.log").read_text())
        self.assertIsNone(report["evidence"]["self-test.raw"]["sha256"])

    def test_missing_output_cannot_reuse_stale_json(self):
        stale = self.run_dir / "self-test.json"
        stale.write_text(json.dumps(probe_report("self-test")), encoding="utf-8")
        self.run_mock.side_effect = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0)
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertNotEqual(stale, self.artifact(report, "self-test.raw"))
        self.assertIsNone(report["evidence"]["self-test.raw"]["sha256"])
        self.assertEqual("PASS", json.loads(stale.read_text())["status"])

    def test_empty_and_malformed_reports_are_failures(self):
        payloads = iter((b"", b"{}", b'{"stage": "wrong"}', b'{"status":"FAIL","status":"PASS"}',
                         b'{"vertices": NaN}', b"\xff"))
        def malformed(argv, **kwargs):
            output, kind, _ = self.decode_invocation(argv)
            output.write_bytes(next(payloads))
            return subprocess.CompletedProcess(argv, 2 if kind in {"unsupported", "truncated"} else 0)
        self.run_mock.side_effect = malformed
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertTrue(all(report["checks"][name] == "FAIL" for name, _, _ in checker.CASES))

    def test_ue_bom_encoded_report_is_supported_without_rewriting_raw(self):
        def encoded(argv, **kwargs):
            completed = self.fake_process(argv, **kwargs)
            output, kind, input_path = self.decode_invocation(argv)
            output.write_bytes(json.dumps(probe_report(kind, input_path)).encode("utf-16"))
            return completed
        self.run_mock.side_effect = encoded
        report = self.run_checks()
        self.assertEqual("PASS", report["status"])
        self.assertTrue(self.artifact(report, "self-test.raw").read_bytes().startswith(b"\xff\xfe"))
        self.validate()

    def test_missing_fixture_or_unexecutable_program_blocks_all_cases(self):
        (self.fixtures / "MorphTargets.fbx").unlink()
        self.program.chmod(0o644)
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        preflight = self.artifact(report, "preflight").read_text()
        self.assertIn("not an executable", preflight)
        self.assertIn("missing or empty fixture", preflight)
        self.run_mock.assert_not_called()

    def test_build_argv_and_actual_commit_metadata_are_required(self):
        self.build_command.write_text('"bash Build.sh"', encoding="utf-8")
        report = self.run_checks(ue_commit="unverified")
        self.assertEqual("FAIL", report["status"])
        preflight = self.artifact(report, "preflight").read_text()
        self.assertIn("JSON argv", preflight)
        self.assertIn("full actual UE git commit", preflight)
        self.run_mock.assert_not_called()

    def test_supplied_commit_must_agree_with_build_commit_file(self):
        report = self.run_checks(ue_commit="c" * 40)
        self.assertEqual("FAIL", report["status"])
        self.assertIn("disagrees", self.artifact(report, "preflight").read_text())
        self.run_mock.assert_not_called()

    def test_core_limit_failure_blocks_native_invocations(self):
        self.set_core.side_effect = OSError("core limit denied")
        report = self.run_checks()
        self.assertEqual("FAIL", report["status"])
        self.assertIn("core limit denied", self.artifact(report, "preflight").read_text())
        self.run_mock.assert_not_called()

    def test_core_limit_restore_failure_invalidates_otherwise_passing_cases(self):
        self.set_core.side_effect = [None, OSError("restore denied")]
        report = self.run_checks()
        self.assertEqual(6, self.run_mock.call_count)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["exit_code"])
        self.assertEqual("FAIL", report["checks"]["preflight"])
        self.assertIn("restoring core limit", self.artifact(report, "preflight").read_text())

    def test_existing_stage_report_is_preserved_and_timeout_is_bounded(self):
        path = self.run_dir / "stage-report.json"
        path.write_bytes(b"existing user evidence\n")
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            self.run_checks()
        self.assertEqual(b"existing user evidence\n", path.read_bytes())
        for timeout in (0, -1, True, float("nan"), float("inf"), 301):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                    self.run_checks(timeout_seconds=timeout)
        self.run_mock.assert_not_called()

    def test_cli_maps_requested_arguments_and_returns_gate_status(self):
        argv = []
        for name, value in self.args.items():
            argv.extend([f"--{name.replace('_', '-')}", str(value)])
        with mock.patch.object(checker, "run_checks", return_value={"status": "PASS", "errors": []}) as run:
            with mock.patch("sys.stdout"):
                self.assertEqual(0, checker.main(argv))
            run.assert_called_once_with(**self.args)
        with mock.patch.object(checker, "run_checks", return_value={"status": "FAIL", "errors": ["bad check"]}):
            with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
                self.assertEqual(1, checker.main(argv))
        self.run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
