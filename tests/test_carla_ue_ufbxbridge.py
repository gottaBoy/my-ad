import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/check_ue_ufbxbridge.py"
spec = importlib.util.spec_from_file_location("check_ue_ufbxbridge", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def raw_report(kind, path=None):
    negative = kind in {"unsupported", "truncated"}
    geometry = kind in {"static", "materials"}
    report = {
        "stage": checker.STAGE, "scope": checker.SCOPE, "backend": "ufbx",
        "coordinate_policy": checker.COORDINATE_POLICY,
        "equivalence_to_assimp": "not asserted; front-vs-forward policies differ",
        "status": "FAIL" if negative else "PASS",
        "error": "unsupported animation" if kind == "unsupported" else ("ufbx load failed" if negative else ""),
        "input": "" if path is None else str(path),
        "self_tests": 32 if kind == "self-test" else 0,
        "vertices": 8 if geometry else 0, "triangles": (12 if kind == "static" else 960) if geometry else 0,
        "material_slots": (6 if kind == "static" else 4) if geometry else 0,
        "serialized_bytes": 1024 if geometry else 0,
    }
    if geometry:
        report["bounds_cm"] = [-100, -100, -100, 100, 100, 100]
    if kind == "self-test":
        report["coordinate_checks"] = [
            {"source_front_sign": sign, "mirrored_instance": mirrored,
             "ufbx_reversed_winding": sign == 1, "ue_facing_matches_normals": True,
             "bounds_cm": [-sign * (700 if mirrored else 500), 100 if mirrored else 200, 300,
                           -sign * (700 if mirrored else 500), 200 if mirrored else 300, 500 if mirrored else 400],
             "normal_world": [-sign, 0, 0]}
            for sign in (-1, 1) for mirrored in (False, True)
        ]
    return report


class UfbxEvaluatorTest(unittest.TestCase):
    def test_explicit_ufbx_identity_does_not_accept_assimp_stage(self):
        report = raw_report("static")
        checker.evaluate_report(report, returncode=0, kind="static")
        for name, value in (("stage", checker.base.STAGE), ("scope", checker.base.SCOPE),
                            ("backend", "assimp"), ("coordinate_policy", checker.base.COORDINATE_POLICY)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checker.evaluate_report({**report, name: value}, returncode=0, kind="static")

    def test_native_self_checks_cannot_be_replaced_by_old_assimp_count(self):
        for count in (0, 25, 31, True, "32", 32.5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                checker.evaluate_report({**raw_report("self-test"), "self_tests": count},
                                        returncode=0, kind="self-test")
        checker.evaluate_report(raw_report("self-test"), returncode=0, kind="self-test")

    def test_fixture_geometry_counts_are_exact(self):
        for kind in ("static", "materials"):
            for field in ("triangles", "material_slots"):
                report = raw_report(kind)
                report[field] += 1
                with self.subTest(kind=kind, field=field), self.assertRaises(ValueError):
                    checker.evaluate_report(report, returncode=0, kind=kind)

    def test_asymmetric_real_fbx_winding_and_unit_evidence_required(self):
        for change in ({"ufbx_reversed_winding": True}, {"ue_facing_matches_normals": False},
                       {"normal_world": [-1, 0, 0]}, {"bounds_cm": [-500, 200, 300, -500, 300, 400]}):
            report = raw_report("self-test")
            report["coordinate_checks"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                checker.evaluate_report(report, returncode=0, kind="self-test")

    def test_rejected_fixture_is_exit_two_and_empty_not_import_support(self):
        for kind in ("unsupported", "truncated"):
            report = raw_report(kind)
            checker.evaluate_report(report, returncode=2, kind=kind)
            for change in ({"vertices": 1}, {"serialized_bytes": 1}, {"status": "PASS"}, {"error": ""}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    checker.evaluate_report({**report, **change}, returncode=2, kind=kind)
            for code in (-11, -6, 0, 139):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    checker.evaluate_report(report, returncode=code, kind=kind)

    def test_finite_bounds_and_current_input_required(self):
        for change in ({"bounds_cm": [0, 0, 0, 1, 1, float("inf")]}, {"input": "/old/file.fbx"},
                       {"vertices": True}, {"serialized_bytes": 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                checker.evaluate_report({**raw_report("static"), **change}, returncode=0, kind="static")


class UfbxRunnerTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.program = self.root / "bin/CarlaMeshBridge"
        self.program.parent.mkdir()
        self.program.write_bytes(b"\x7fELF\x02\x01" + b"\x00" * 12 + b"\xb7\x00" + b"mock program")
        self.program.chmod(0o755)
        self.source_root = self.root / "bridge"
        cpp = self.source_root / "Source/CarlaUfbxMesh/Private/CarlaUfbxMesh.cpp"
        cpp.parent.mkdir(parents=True)
        cpp.write_text("mock bridge code\n")
        self.ufbx_source = self.root / "ufbx-source"
        self.ufbx_source.mkdir()
        (self.ufbx_source / "ufbx.c").write_text("mock C source\n")
        (self.ufbx_source / "ufbx.h").write_text("mock C header\n")
        self.install = self.root / "ufbx-install"
        (self.install / "lib").mkdir(parents=True)
        (self.install / "include").mkdir()
        self.library = self.install / "lib/libufbx.a"
        self.library.write_bytes(b"mock static archive")
        (self.install / "include/ufbx.h").write_text("mock C header\n")
        self.log = self.root / "backend.log"
        self.log.write_text("mock native checks")
        self.ufbx_report = self.root / "ufbx-stage.json"
        self.write_backend()
        self.assimp_library = self.root / "libassimp.so.6"
        self.assimp_library.write_bytes(b"mock Assimp library")
        self.runtime = self.program.parent / "libassimp.so.6"
        self.runtime.write_bytes(self.assimp_library.read_bytes())
        self.assimp_report = self.root / "assimp-stage.json"
        self.write_stage(self.assimp_report, checker.base.ASSIMP_STAGE, checker.base.ASSIMP_SCOPE,
                         {"library": self.assimp_library}, name="assimp")
        self.regression_report = self.root / "regression-stage.json"
        self.write_stage(self.regression_report, checker.base.STAGE, checker.base.SCOPE,
                         {"program": self.program}, name="ue")
        self.inputs = self.root / "build-inputs.json"
        self.manifest = self.root / "build-manifest.json"
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.build_command = self.run_dir / "build-command.json"
        self.build_command.write_text(json.dumps(["Build.sh", "CarlaMeshBridge", "Linux", "-architecture=arm64"]))
        self.ue_root = self.root / "ue"
        self.fixtures = self.ue_root / "fixtures"
        self.fixtures.mkdir(parents=True)
        for _, filename, _ in checker.CASES:
            if filename:
                (self.fixtures / filename).write_bytes(b"mock fixture" + bytes(range(64)))
        self.write_backend()
        checker.capture_inputs(output=self.inputs, program=self.program, source_root=self.source_root,
                               ufbx_install=self.install, ufbx_report=self.ufbx_report,
                               assimp_library=self.assimp_library, ue_commit="a" * 40)
        checker.seal_build(inputs=self.inputs, output=self.manifest)
        self.args = dict(program=self.program, fixtures=self.fixtures, run_dir=self.run_dir, ue_root=self.ue_root,
                         ue_commit="a" * 40, ufbx_report=self.ufbx_report, ufbx_install=self.install,
                         assimp_report=self.assimp_report, assimp_bridge_report=self.regression_report,
                         build_manifest=self.manifest, build_command=self.build_command, timeout_seconds=30)
        self.run_mock = self.patch(checker.subprocess, "run", side_effect=self.fake_process)
        self.patch(checker.resource, "getrlimit", return_value=(4096, 8192))
        self.set_limit = self.patch(checker.resource, "setrlimit")

    def patch(self, owner, name, **kwargs):
        patcher = mock.patch.object(owner, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def write_stage(self, path, stage, scope, evidence, *, name):
        return checker.stage_report.write_report(
            path, stage_id=stage, scope=scope, exit_code=0, required_checks=["native"],
            checks={"native": "PASS"}, evidence={"native": self.log, **evidence},
            sources={name: {"location": str(self.ufbx_source),
                            "revision": checker.UFBX_COMMIT if name == "ufbx" else "b" * 40}},
            command=["mock-backend-check"])

    def write_backend(self, omit=None, scope=checker.UFBX_SCOPE):
        evidence = {"static-library": self.library, "header": self.install / "include/ufbx.h",
                    "source-c": self.ufbx_source / "ufbx.c", "source-h": self.ufbx_source / "ufbx.h"}
        if hasattr(self, "fixtures"):
            evidence.update({f"fixture.{filename}": self.fixtures / filename
                             for _, filename, _ in checker.CASES if filename})
        if omit:
            evidence.pop(omit)
        return self.write_stage(self.ufbx_report, checker.UFBX_STAGE, scope, evidence, name="ufbx")

    def fake_process(self, argv, **kwargs):
        self.assertIn("-backend=ufbx", argv)
        self.assertTrue(all(flag in argv for flag in checker.RUNTIME_FLAGS))
        self.assertEqual(30, kwargs["timeout"])
        self.assertEqual(self.program.parent, kwargs["cwd"])
        self.assertEqual(subprocess.DEVNULL, kwargs["stdin"])
        self.set_limit.assert_called_once_with(checker.resource.RLIMIT_CORE, (0, 8192))
        raw = Path(next(arg[len("-output="):] for arg in argv if arg.startswith("-output=")))
        self.assertFalse(raw.exists())
        path = next((Path(arg[len("-input="):]) for arg in argv if arg.startswith("-input=")), None)
        kind = {"BlenderCube.fbx": "static", "MultiMatId.fbx": "materials",
                "AnimatedCharacter.fbx": "unsupported", "MorphTargets.fbx": "unsupported",
                "truncated.fbx": "truncated"}[path.name] if path else "self-test"
        raw.write_text(json.dumps(raw_report(kind, path)))
        return subprocess.CompletedProcess(argv, 2 if kind in {"unsupported", "truncated"} else 0)

    def run_checks(self, **overrides):
        return checker.run_checks(**{**self.args, **overrides})

    def test_scoped_pass_six_fresh_processes_with_provenance(self):
        report = self.run_checks()
        self.assertEqual("PASS", report["status"], report["errors"])
        self.assertEqual(6, self.run_mock.call_count)
        self.assertEqual(checker.REQUIRED_CHECKS, report["required_checks"])
        self.assertEqual(checker.UFBX_STAGE, report["prerequisites"][0]["stage_id"])
        self.assertEqual(report, checker.stage_report.validate_report(
            self.run_dir / "stage-report.json", stage_id=checker.STAGE, scope=checker.SCOPE))
        self.set_limit.assert_has_calls([mock.call(resource, value) for resource, value in (
            (checker.resource.RLIMIT_CORE, (0, 8192)), (checker.resource.RLIMIT_CORE, (4096, 8192)))])

    def test_changed_static_library_blocks_before_probe(self):
        self.library.write_bytes(b"changed static archive")
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.run_mock.assert_not_called()

    def test_missing_source_hash_in_backend_report_is_rejected(self):
        self.write_backend(omit="source-c")
        with self.assertRaisesRegex(ValueError, "no evidence"):
            checker.backend_inputs(self.ufbx_report, self.install)

    def test_backend_source_manifest_checks_actual_file_hash_not_just_manifest_hash(self):
        manifest = self.root / "source-files.sha256"
        paths = [self.ufbx_source / "ufbx.c", self.ufbx_source / "ufbx.h"]
        manifest.write_text("".join(f"{checker.digest(path)}  {path}\n" for path in paths))
        evidence = {"static-library": self.library, "build.source-files.sha256": manifest}
        self.write_stage(self.ufbx_report, checker.UFBX_STAGE, checker.UFBX_SCOPE, evidence, name="ufbx")
        checker.backend_inputs(self.ufbx_report, self.install)
        paths[0].write_text("source changed after manifest generation")
        with self.assertRaisesRegex(ValueError, "current SHA256"):
            checker.backend_inputs(self.ufbx_report, self.install)

    def test_installed_header_must_match_source_header(self):
        (self.install / "include/ufbx.h").write_text("different API\n")
        self.write_backend()
        with self.assertRaisesRegex(ValueError, "differs"):
            checker.backend_inputs(self.ufbx_report, self.install)

    def test_wrong_backend_scope_fails_closed(self):
        self.write_backend(scope="Unreal Editor/Cook")
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.run_mock.assert_not_called()

    def test_changed_actual_program_is_not_accepted_as_prior_build(self):
        self.program.write_bytes(self.program.read_bytes() + b"changed")
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.run_mock.assert_not_called()

    def test_new_bridge_source_invalidates_captured_file_set(self):
        (self.source_root / "added.h").write_text("unbuilt code")
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.run_mock.assert_not_called()

    def test_source_change_prevents_build_sealing(self):
        cpp = self.source_root / "Source/CarlaUfbxMesh/Private/CarlaUfbxMesh.cpp"
        cpp.write_text("changed during build")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            checker.seal_build(inputs=self.inputs, output=self.root / "another-manifest.json")

    def test_ufbx_source_change_during_cases_prevents_pass(self):
        def mutate(argv, **kwargs):
            result = self.fake_process(argv, **kwargs)
            (self.ufbx_source / "ufbx.c").write_text("changed")
            return result
        self.run_mock.side_effect = mutate
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.assertEqual(1, self.run_mock.call_count)

    def test_binary_change_after_final_case_prevents_pass(self):
        def mutate(argv, **kwargs):
            result = self.fake_process(argv, **kwargs)
            if self.run_mock.call_count == 6:
                self.program.write_bytes(self.program.read_bytes() + b"changed")
            return result
        self.run_mock.side_effect = mutate
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.assertEqual(6, self.run_mock.call_count)

    def test_stale_assimp_runtime_is_rejected(self):
        self.runtime.write_bytes(b"stale")
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.run_mock.assert_not_called()

    def test_different_binary_assimp_regression_is_rejected(self):
        other = self.root / "other-program"
        other.write_bytes(b"another old program")
        self.write_stage(self.regression_report, checker.base.STAGE, checker.base.SCOPE,
                         {"program": other}, name="ue")
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.run_mock.assert_not_called()

    def test_timeout_never_becomes_expected_rejection(self):
        self.run_mock.side_effect = subprocess.TimeoutExpired(["mock"], 30)
        self.assertEqual("FAIL", self.run_checks()["status"])
        self.assertEqual(6, self.run_mock.call_count)

    def test_signal_is_not_clean_rejection(self):
        def crash(argv, **kwargs):
            self.fake_process(argv, **kwargs)
            return subprocess.CompletedProcess(argv, -11)
        self.run_mock.side_effect = crash
        self.assertEqual("FAIL", self.run_checks()["status"])

    def test_existing_report_and_timeout_limits_are_not_overwritten(self):
        for timeout in (0, -1, 301, True, float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.run_checks(timeout_seconds=timeout)
        report = self.run_checks()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            self.run_checks()
        self.assertEqual("PASS", report["status"])

    def test_unsealed_manifest_fails_closed(self):
        self.assertEqual("FAIL", self.run_checks(build_manifest=self.inputs)["status"])
        self.run_mock.assert_not_called()


class UfbxBuildContractTest(unittest.TestCase):
    def test_default_program_only_links_optional_module_when_environment_is_set(self):
        source = ROOT / "scripts/carla/ue-meshbridge/Source"
        build = (source / "CarlaMeshBridge/CarlaMeshBridge.Build.cs").read_text()
        self.assertIn('Environment.GetEnvironmentVariable("CARLA_UFBX_INSTALL") != null', build)
        self.assertIn('if (WithUfbx) PrivateDependencyModuleNames.Add("CarlaUfbxMesh")', build)
        module = (source / "CarlaUfbxMesh/CarlaUfbxMesh.Build.cs").read_text()
        for text in ("String.IsNullOrWhiteSpace", "Path.IsPathFullyQualified", "libufbx.a",
                     "ufbx.h", "ExternalDependencies.Add"):
            self.assertIn(text, module)

    def test_no_assimp_import_or_sdk_removal_in_new_backend(self):
        cpp = (ROOT / "scripts/carla/ue-meshbridge/Source/CarlaUfbxMesh/Private/CarlaUfbxMesh.cpp").read_text()
        for api in ("ufbx_load_file", "ufbx_load_memory", "ufbx_free_scene", "ufbx_matrix_for_normals",
                    "ufbx_transform_position", "ufbx_catch_triangulate_face", "geometry_to_world",
                    "target_axes", "target_unit_meters", "CarlaAssimpMesh::CheckMemoryRoundTrip"):
            self.assertIn(api, cpp)
        self.assertNotIn("CarlaAssimpMesh::ImportStatic", cpp)
        self.assertNotIn("aiImport", cpp)

    def test_job_limit_is_four_and_invalid_values_exit_before_build(self):
        script = ROOT / "scripts/carla/build-arm64-ue-ufbxbridge.sh"
        for value in ("0", "5", "-1", "invalid", "99999999999999999999", "1;exit 0"):
            with self.subTest(value=value):
                result = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                                        env={**os.environ, "CARLA_BUILD_JOBS": value})
                self.assertEqual(64, result.returncode)


if __name__ == "__main__":
    unittest.main()
