"""OpenUSD rejection contracts only; fixtures never establish native SDK PASS."""

import ast
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/usd/build-arm64-openusd.sh"
HELPER = SCRIPT.with_name("openusd_stage.py")
spec = importlib.util.spec_from_file_location("native_openusd_stage_tests", HELPER)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
NATIVE = Path("/.dockerenv").is_file() and platform.machine() == "aarch64"
FEATURES = (
    "PXR_ENABLE_PYTHON_SUPPORT", "PXR_BUILD_ALEMBIC_PLUGIN",
    "PXR_ENABLE_MATERIALX_SUPPORT", "PXR_BUILD_IMAGING",
    "PXR_BUILD_USD_IMAGING", "BUILD_SHARED_LIBS",
)
SELECTIONS = (
    "Boost_INCLUDE_DIR", "Python3_EXECUTABLE", "Python3_INCLUDE_DIR",
    "Python3_LIBRARY", "TBB_tbb_LIBRARY_RELEASE", "ALEMBIC_LIBRARY",
    "OPENSUBDIV_OSDCPU_LIBRARY",
)
BOUND_INPUTS = {
    "tbb": ("lib/libtbb.so.2", "lib/libtbbmalloc.so.2"),
    "python": ("bin/python3.11", "lib/libpython3.11.so.1.0", "include/python3.11/Python.h"),
    "boost": ("lib/libboost_python311-mt-a64.so.1.82.0", "include/boost/version.hpp"),
    "imath": ("lib/libImath-3_1.a", "lib/cmake/Imath/ImathConfig.cmake"),
    "alembic": ("lib/libAlembic.a", "include/Alembic/Abc/All.h"),
    "opensubdiv": ("lib/libosdCPU.a", "include/opensubdiv/version.h"),
    "materialx": ("lib/libMaterialXCore.a", "lib/libMaterialXFormat.a",
                  "lib/cmake/MaterialX/MaterialXConfig.cmake"),
}
PATCHES = {
    "OpenUSD_v2405_clang_TfSafeTypeCompare.patch",
    "OpenUSD_v2405_explicit_SdfAssetPath_dtor.patch",
    "OpenUSD_v2405_hdSt_Metal_MaterialX_versioning.patch",
    "OpenUSD_v2405_msvc_preprocessor_version_handling.patch",
    "OpenUSD_v2405_usdMtlx_undef_stdlib_dir.patch",
    "OpenUSD_v2405_weakPtrFacade_cpp20_equality_rewriting.patch",
}


def build_statements():
    tree = ast.parse(HELPER.read_text(), filename=str(HELPER))
    build = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "Build")
    return next(node.body for node in build.body
                if isinstance(node, ast.FunctionDef) and node.name == "run_build")


def assignment_index(body, name):
    matches = [index for index, node in enumerate(body)
               if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == name
                       for target in node.targets)]
    if len(matches) != 1:
        raise AssertionError(f"expected one run_build assignment to {name}")
    return matches[0]


def execute_statements(nodes, **context):
    # run_build has no pure configuration API. Execute its original AST guards
    # in isolation, never bypass prerequisite validation to run a synthetic build.
    code = compile(ast.Module(body=nodes, type_ignores=[]), str(HELPER), "exec")
    namespace = {**vars(stage), **context}
    exec(code, namespace)
    return namespace


class OpenUSDInputsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def dependency_map(self):
        mapping = {}
        for name in stage.DEPENDENCIES:
            path = self.root / name / "stage-report.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"status": "FAIL", "fixture": name}))
            mapping[name] = str(path)
        path = self.root / "dependencies.json"
        stage.dump(path, mapping)
        return path, mapping

    def reader_boundary(self):
        path, mapping = self.dependency_map()
        reports = {}
        for name, inputs in BOUND_INPUTS.items():
            report = {"status": "FAIL", "evidence": {}, "prerequisites": []}
            base = Path(mapping[name]).parent
            for relative in inputs:
                file = base / "install" / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("report reader fixture only, not a native binary\n")
                report["evidence"][relative] = {
                    "path": str(file.relative_to(base)),
                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                }
            reports[name] = report
        for child, parent in (("alembic", "imath"), ("boost", "python")):
            reports[child]["prerequisites"].append({
                "stage_id": stage.DEPENDENCIES[parent][0],
                "scope": stage.DEPENDENCIES[parent][1],
                "sha256": hashlib.sha256(Path(mapping[parent]).read_bytes()).hexdigest(),
            })

        def validate_boundary(report_path, *, stage_id, scope):
            name = next(k for k, value in mapping.items() if value == str(report_path))
            self.assertEqual("carla-" + name + "-native-arm64", stage_id)
            self.assertEqual(stage.DEPENDENCIES[name][1], scope)
            self.assertNotEqual("pending", scope)
            return reports[name]

        return path, mapping, reports, validate_boundary

    def test_map_requires_exact_dependency_names_before_reading_reports(self):
        expected = {"tbb", "python", "imath", "alembic", "opensubdiv", "materialx", "boost"}
        self.assertEqual(expected, set(stage.DEPENDENCIES))
        full = {name: "/absent/stage-report.json" for name in expected}
        cases = [[], None, "map", {}, {**full, "fallback": "/absent"}]
        cases.extend({k: v for k, v in full.items() if k != missing} for missing in expected)
        path = self.root / "dependencies.json"
        for value in cases:
            with self.subTest(value=value):
                stage.dump(path, value)
                with self.assertRaisesRegex(ValueError, "dependency map must provide exactly"):
                    stage.read_dependencies(path)

    def test_malformed_map_json_is_rejected(self):
        path = self.root / "dependencies.json"
        path.write_text('{"tbb":')
        with self.assertRaises(json.JSONDecodeError):
            stage.read_dependencies(path)

    def test_relative_report_path_is_rejected_before_validation(self):
        path, mapping = self.dependency_map()
        mapping["tbb"] = "tbb/stage-report.json"
        stage.dump(path, mapping)
        with mock.patch.object(stage, "validate_report") as validator:
            with self.assertRaisesRegex(ValueError, "absolute container paths"):
                stage.read_dependencies(path)
        validator.assert_not_called()

    def test_missing_directory_and_symlink_reports_are_rejected(self):
        path, mapping = self.dependency_map()
        target = Path(mapping["tbb"])
        link = self.root / "linked-report.json"
        link.symlink_to(target)
        for bad in (self.root / "missing", target.parent, link):
            with self.subTest(path=bad), mock.patch.object(stage, "validate_report") as validator:
                stage.dump(path, {**mapping, "tbb": str(bad)})
                with self.assertRaisesRegex(ValueError, "expected regular file"):
                    stage.read_dependencies(path)
                validator.assert_not_called()

    def test_reader_forwards_exact_ids_scopes_and_hashes_at_validation_boundary(self):
        path, mapping, reports, validate_boundary = self.reader_boundary()
        # This tests dispatch only: no synthetic PASS JSON and no Build invocation.
        with mock.patch.object(stage, "validate_report", side_effect=validate_boundary) as validator:
            records = stage.read_dependencies(path)
        self.assertEqual(len(stage.DEPENDENCIES), validator.call_count)
        for name, record in records.items():
            report_path = Path(mapping[name])
            self.assertEqual(str(report_path), record["path"])
            self.assertEqual(hashlib.sha256(report_path.read_bytes()).hexdigest(), record["sha256"])
            self.assertEqual(str(report_path.parent / "install"), record["prefix"])
            self.assertIs(reports[name], record["report"])
            self.assertEqual("FAIL", record["report"]["status"])
        with self.assertRaisesRegex(ValueError, "report status is not PASS"):
            stage.read_dependencies(path)

    def test_reader_rejects_each_missing_or_wrong_required_input_binding(self):
        path, mapping, reports, validator = self.reader_boundary()
        with mock.patch.object(stage, "validate_report", side_effect=validator):
            for name, inputs in BOUND_INPUTS.items():
                evidence = reports[name]["evidence"]
                for relative in inputs:
                    original = dict(evidence[relative])
                    file = Path(mapping[name]).parent / original["path"]
                    for mutation in ("unbound", "wrong-digest", "wrong-path", "changed-file"):
                        with self.subTest(dependency=name, relative=relative, mutation=mutation):
                            before = file.read_bytes()
                            try:
                                if mutation == "unbound":
                                    evidence.pop(relative)
                                elif mutation == "wrong-digest":
                                    evidence[relative]["sha256"] = "0" * 64
                                elif mutation == "wrong-path":
                                    evidence[relative]["path"] = "install/unrelated-input"
                                else:
                                    file.write_bytes(before + b"changed")
                                with self.assertRaisesRegex(ValueError, name + " report does not bind"):
                                    stage.read_dependencies(path)
                            finally:
                                evidence[relative] = dict(original)
                                file.write_bytes(before)

    def test_reader_rejects_missing_or_different_transitive_prerequisite(self):
        path, _, reports, validator = self.reader_boundary()
        with mock.patch.object(stage, "validate_report", side_effect=validator):
            for child, parent in (("alembic", "imath"), ("boost", "python")):
                original = reports[child]["prerequisites"]
                for mutation in ("missing", "digest", "stage-id"):
                    with self.subTest(child=child, parent=parent, mutation=mutation):
                        changed = [dict(original[0])]
                        if mutation == "missing":
                            changed = []
                        elif mutation == "digest":
                            changed[0]["sha256"] = "0" * 64
                        else:
                            changed[0]["stage_id"] += "-other"
                        reports[child]["prerequisites"] = changed
                        try:
                            with self.assertRaisesRegex(
                                ValueError, child + " was built against a different " + parent
                            ):
                                stage.read_dependencies(path)
                        finally:
                            reports[child]["prerequisites"] = original

    def test_report_mutation_during_validation_is_rejected(self):
        path, mapping = self.dependency_map()

        def mutate(report_path, **unused):
            report_path.write_text('{"status": "FAIL", "changed": true}\n')
            return object()

        with mock.patch.object(stage, "validate_report", side_effect=mutate) as validator:
            with self.assertRaisesRegex(ValueError, "prerequisite changed during verification"):
                stage.read_dependencies(path)
        self.assertEqual(1, validator.call_count)
        self.assertIn("changed", Path(mapping["tbb"]).read_text())

    def test_validator_exception_is_not_swallowed_or_followed_by_more_reports(self):
        path, _ = self.dependency_map()
        error = ValueError("unverified prerequisite evidence")
        with mock.patch.object(stage, "validate_report", side_effect=error) as validator:
            with self.assertRaises(ValueError) as raised:
                stage.read_dependencies(path)
        self.assertIs(error, raised.exception)
        self.assertEqual(1, validator.call_count)

    def failure_report(self):
        path, mapping = self.dependency_map()
        report_path = Path(mapping["tbb"])
        evidence = report_path.parent / "failure.log"
        evidence.write_text("negative-path fixture: no SDK was built\n")
        stage_id, scope = stage.DEPENDENCIES["tbb"]
        report = stage.write_report(
            report_path, stage_id=stage_id, scope=scope, exit_code=1,
            required_checks=["native"], checks={"native": "FAIL"},
            evidence={"native": evidence},
            sources={"fixture": {"location": str(self.root), "revision": "not-a-build"}},
            command=["negative-path-fixture"],
        )
        self.assertEqual("FAIL", report["status"])
        return path, report_path, evidence, report

    def test_real_validator_rejects_fail_report(self):
        path, _, _, _ = self.failure_report()
        with self.assertRaisesRegex(ValueError, "report status is not PASS"):
            stage.read_dependencies(path)

    def test_real_validator_checks_exact_stage_and_scope_even_on_failure(self):
        path, report_path, _, report = self.failure_report()
        for key in ("stage_id", "scope"):
            for suffix in (" ", "-different"):
                with self.subTest(key=key, suffix=suffix):
                    stage.dump(report_path, {**report, key: report[key] + suffix})
                    with self.assertRaisesRegex(ValueError, key + " must exactly match"):
                        stage.read_dependencies(path)

    def test_real_validator_detects_evidence_digest_mutation(self):
        path, _, evidence, _ = self.failure_report()
        evidence.write_text("tampered negative-path evidence\n")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            stage.read_dependencies(path)

    def test_snapshot_tracks_content_and_generated_bytecode_not_git_metadata(self):
        root = self.root / "source"
        root.mkdir()
        source = root / "source.cpp"
        source.write_bytes(b"original")
        (root / ".git").mkdir()
        (root / ".git/config").write_bytes(b"not source")
        before = stage.snapshot(root)
        self.assertEqual({"source.cpp": hashlib.sha256(b"original").hexdigest()}, before)
        (root / ".git/config").write_bytes(b"changed metadata")
        self.assertEqual(before, stage.snapshot(root))
        source.write_bytes(b"changed")
        self.assertNotEqual(before, stage.snapshot(root))
        source.write_bytes(b"original")
        pyc = root / "__pycache__/module.cpython-311.pyc"
        pyc.parent.mkdir()
        pyc.write_bytes(b"generated")
        generated = stage.snapshot(root)
        self.assertEqual({"source.cpp", str(pyc.relative_to(root))}, set(generated))
        self.assertNotEqual(before, generated)
        pyc.write_bytes(b"regenerated")
        self.assertNotEqual(generated, stage.snapshot(root))
        source.unlink()
        self.assertNotIn("source.cpp", stage.snapshot(root))

    def test_snapshot_rejects_file_directory_and_dangling_symlinks(self):
        root = self.root / "source"
        root.mkdir()
        (root / "source.cpp").write_text("source\n")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "external.cpp").write_text("external\n")
        for target in (outside, outside / "external.cpp", outside / "missing"):
            with self.subTest(target=target):
                link = root / "link"
                link.symlink_to(target)
                try:
                    with self.assertRaises(ValueError):
                        stage.snapshot(root)
                finally:
                    link.unlink()

    def test_snapshot_rejects_empty_and_missing_trees(self):
        for root in (self.root, self.root / "absent"):
            with self.subTest(root=root), self.assertRaisesRegex(ValueError, "empty source tree"):
                stage.snapshot(root)


class OpenUSDConfigurationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.body = build_statements()

    def flags(self):
        prefixes = {name: self.root / name / "install" for name in stage.DEPENDENCIES}
        index = assignment_index(self.body, "flags")
        return execute_statements(
            [self.body[index]], p=prefixes, prefix=self.root / "install",
            libcxx=self.root / "ue-libcxx", tbb_root=self.root / "tbb",
            tbb_include=self.root / "tbb/source/IntelTBB-2019u8/include",
            python=prefixes["python"] / "bin/python3.11",
        )["flags"]

    def validate_cache(self, flags, cache):
        work = self.root / "work"
        work.mkdir(exist_ok=True)
        (work / "CMakeCache.txt").write_text(
            "# fixture of CMake output only\n// not a prerequisite report\n"
            + "".join(f"{key}:STRING={value}\n" for key, value in cache.items())
        )
        start = assignment_index(self.body, "cache")
        end = next(index for index in range(start + 1, len(self.body))
                   if isinstance(self.body[index], ast.Expr))
        guards = self.body[start:end]
        self.assertEqual([ast.Assign, ast.For, ast.For, ast.For], list(map(type, guards)))
        execute_statements(guards, work=work, flags=flags)

    def test_pinned_commit_and_exact_six_patch_set(self):
        self.assertEqual("2864f3d04f396432f22ec5d6928fc37d34bb4c90", stage.COMMIT)
        self.assertEqual(PATCHES, stage.PATCH_NAMES)
        index = assignment_index(self.body, "patches")
        guards = self.body[index:index + 2]
        self.assertIsInstance(guards[1], ast.If)
        for name in PATCHES:
            (self.root / name).write_text("filename-selection fixture only\n")
        selected = execute_statements(guards, patch_root=self.root)["patches"]
        self.assertEqual(sorted(PATCHES), [path.name for path in selected])
        for name in sorted(PATCHES):
            with self.subTest(missing=name):
                path = self.root / name
                path.unlink()
                with self.assertRaisesRegex(ValueError, "pinned UE USD patch set differs"):
                    execute_statements(guards, patch_root=self.root)
                path.write_text("filename-selection fixture only\n")
        (self.root / "OpenUSD_v2405_unapproved.patch").write_text("extra\n")
        with self.assertRaisesRegex(ValueError, "pinned UE USD patch set differs"):
            execute_statements(guards, patch_root=self.root)

    def test_requested_feature_profile_keeps_shared_python_and_ue_features(self):
        flags = self.flags()
        self.assertTrue(all(flags[name] == "ON" for name in FEATURES))
        self.assertEqual(str(self.root / "python/install/lib/libpython3.11.so"),
                         flags["Python3_LIBRARY"])
        self.assertEqual("OFF", flags["PXR_PY_UNDEFINED_DYNAMIC_LOOKUP"])
        self.assertEqual("OFF", flags["Boost_USE_STATIC_LIBS"])
        self.assertEqual("ON", flags["CMAKE_POSITION_INDEPENDENT_CODE"])
        # Isolating each libc++ copy caused the retained Usd/Gf import-order
        # locale crash. The OpenUSD DSOs must not inherit shader-library policy.
        self.assertNotIn("--exclude-libs,ALL", flags["CMAKE_MODULE_LINKER_FLAGS"])
        self.assertNotIn("--exclude-libs,ALL", flags["CMAKE_SHARED_LINKER_FLAGS"])
        self.assertIn("-D_LIBCPP_TYPEINFO_COMPARISON_IMPLEMENTATION=2", flags["CMAKE_CXX_FLAGS_RELEASE"])
        self.validate_cache(flags, flags)

    def test_each_required_feature_disabled_or_missing_rejects_configuration(self):
        flags = self.flags()
        for name in FEATURES:
            for value in (None, "OFF", "FALSE", "0", ""):
                with self.subTest(feature=name, value=value):
                    cache = dict(flags)
                    if value is None:
                        cache.pop(name)
                    else:
                        cache[name] = value
                    with self.assertRaisesRegex(ValueError, "required UE feature disabled by CMake: " + name):
                        self.validate_cache(flags, cache)

    def test_selected_dependencies_cannot_fall_back_to_system_or_missing_paths(self):
        flags = self.flags()
        for name in SELECTIONS:
            for value in (None, "/usr/lib/x86_64-linux-gnu/fallback"):
                with self.subTest(dependency=name, value=value):
                    cache = dict(flags)
                    if value is None:
                        cache.pop(name)
                    else:
                        cache[name] = value
                    with self.assertRaisesRegex(ValueError, "dependency selection changed: " + name):
                        self.validate_cache(flags, cache)

    def test_retention_guard_rejects_generated_pyc_and_changed_live_input(self):
        source = self.root / "source"
        source.mkdir()
        (source / "source.cpp").write_text("source\n")
        live = self.root / "recipe"
        live.write_text("original\n")
        context = SimpleNamespace(source_before=stage.snapshot(source),
                                  inputs={str(live): stage.digest(live)})
        guards = [node for node in self.body if isinstance(node, ast.If)
                  and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                          and call.func.id == "snapshot" for call in ast.walk(node.test))]
        self.assertEqual(1, len(guards))
        execute_statements(guards, source=source, self=context)
        pyc = source / "__pycache__/generated.cpython-311.pyc"
        pyc.parent.mkdir()
        pyc.write_bytes(b"generated")
        with self.assertRaisesRegex(ValueError, "source/scripts changed"):
            execute_statements(guards, source=source, self=context)
        pyc.unlink()
        live.write_text("modified\n")
        with self.assertRaisesRegex(ValueError, "source/scripts changed"):
            execute_statements(guards, source=source, self=context)


class OpenUSDEntryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def recipe(self, **environment):
        env = {**os.environ, "CARLA_BUILD_JOBS": "4", "CARLA_OPENUSD_TIMEOUT_SECONDS": "30",
               "CARLA_USD_DEPENDENCIES": "", "CARLA_ARTIFACT_DIR": str(self.root / "artifacts"),
               "CARLA_UE_DIR": str(self.root / "ue"), "CARLA_OPENUSD_SOURCE": str(self.root / "source"),
               **environment}
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                              timeout=30, env=env)

    def helper_argv(self, *extra):
        return [str(HELPER), "--source", str(self.root / "source"),
                "--ue-root", str(self.root / "ue"),
                "--dependencies", str(self.root / "dependencies.json"),
                "--artifact-root", str(self.root / "artifacts"), *extra]

    def assert_helper_rejected(self, message, *extra):
        errors = io.StringIO()
        with mock.patch.object(sys, "argv", self.helper_argv(*extra)), redirect_stderr(errors):
            with mock.patch.object(stage, "Build") as build:
                with self.assertRaises(SystemExit) as raised:
                    stage.main()
        self.assertEqual(2, raised.exception.code)
        self.assertIn(message, errors.getvalue())
        build.assert_not_called()
        self.assertFalse((self.root / "artifacts").exists())

    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_shell_rejects_invalid_jobs_before_artifacts(self):
        for value in ("0", "5", "-1", "04", "1.5", "four", "999999999999999999"):
            with self.subTest(jobs=value):
                result = self.recipe(CARLA_BUILD_JOBS=value)
                self.assertEqual(64, result.returncode, result.stdout + result.stderr)
                self.assertIn("CARLA_BUILD_JOBS must be 1..4", result.stderr)
                self.assertFalse((self.root / "artifacts").exists())

    def test_shell_rejects_invalid_timeout_before_artifacts(self):
        for value in ("0", "-1", "01", "14401", "1.5", "slow", "999999999999999999"):
            with self.subTest(timeout=value):
                result = self.recipe(CARLA_OPENUSD_TIMEOUT_SECONDS=value)
                self.assertEqual(64, result.returncode, result.stdout + result.stderr)
                self.assertIn("CARLA_OPENUSD_TIMEOUT_SECONDS must be 1..14400", result.stderr)
                self.assertFalse((self.root / "artifacts").exists())

    def test_shell_rejects_x86_without_output(self):
        fake = self.root / "uname"
        fake.write_text("#!/bin/sh\nprintf 'x86_64\\n'\n")
        fake.chmod(0o755)
        result = self.recipe(PATH=str(self.root) + os.pathsep + os.environ["PATH"])
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("Run in native ARM64 Docker", result.stderr)
        self.assertFalse((self.root / "artifacts").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_shell_accepts_parameter_edges_but_requires_dependency_map(self):
        for jobs in ("1", "4"):
            for seconds in ("1", "14400"):
                with self.subTest(jobs=jobs, seconds=seconds):
                    result = self.recipe(CARLA_BUILD_JOBS=jobs, CARLA_OPENUSD_TIMEOUT_SECONDS=seconds)
                    self.assertEqual(64, result.returncode, result.stdout + result.stderr)
                    self.assertIn("CARLA_USD_DEPENDENCIES is required", result.stderr)
                    self.assertFalse((self.root / "artifacts").exists())

    def test_helper_rejects_invalid_jobs(self):
        for value in ("0", "5", "-1", "four"):
            with self.subTest(jobs=value):
                self.assert_helper_rejected("--jobs", "--jobs", value)

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_helper_rejects_invalid_timeout(self):
        for value in ("0", "-1", "14401", "999999999999999999"):
            with self.subTest(timeout=value):
                self.assert_helper_rejected("timeout must be 1..14400", "--timeout", value)

    def test_helper_rejects_missing_container_marker(self):
        with mock.patch.object(Path, "is_file", return_value=False):
            self.assert_helper_rejected("native ARM64 Docker required")

    def test_helper_rejects_non_arm_machine(self):
        with mock.patch.object(stage.platform, "machine", return_value="x86_64"):
            self.assert_helper_rejected("native ARM64 Docker required")

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_helper_rejects_artifacts_inside_source_or_ue_including_alias(self):
        for tree in ("source", "ue"):
            source = self.root / tree
            source.mkdir()
            alias = self.root / (tree + "-alias")
            alias.symlink_to(source, target_is_directory=True)
            for base in (source, alias):
                with self.subTest(base=base):
                    self.assert_helper_rejected(
                        "refusing to write artifacts into a source tree",
                        "--artifact-root", str(base / "out"),
                    )
                    self.assertFalse((source / "out").exists())

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_real_wrong_git_commit_leaves_only_failed_preflight_report(self):
        repo = self.root / "tiny-repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Negative Fixture",
                        "-c", "user.email=fixture@invalid", "-c", "commit.gpgsign=false",
                        "commit", "--allow-empty", "--no-verify", "-qm", "not OpenUSD"],
                       check=True)
        result = self.recipe(CARLA_UE_DIR=str(repo), CARLA_OPENUSD_SOURCE=str(repo),
                             CARLA_USD_DEPENDENCIES=str(self.root / "missing-dependencies.json"))
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        reports = list((self.root / "artifacts/usd").glob("openusd-*/stage-report.json"))
        self.assertEqual(1, len(reports), result.stdout + result.stderr)
        report_path = reports[0]
        report = json.loads(report_path.read_text())
        self.assertEqual(stage.STAGE, report["stage_id"])
        self.assertEqual(stage.SCOPE, report["scope"])
        self.assertEqual("FAIL", report["status"])
        self.assertTrue(all(value == "FAIL" for value in report["checks"].values()))
        self.assertEqual([], report["prerequisites"])
        decision = json.loads((report_path.parent / "decision.json").read_text())
        self.assertEqual("preflight", decision["phase"])
        self.assertIn("not the pinned v24.05 commit", decision["error"])
        self.assertFalse((report_path.parent / "source").exists())
        self.assertFalse((report_path.parent / "configure.command.json").exists())
        with self.assertRaises(ValueError):
            stage.validate_report(report_path, stage_id=stage.STAGE, scope=stage.SCOPE)

    def new_build(self, timeout):
        return stage.Build(SimpleNamespace(artifact_root=self.root, timeout=timeout,
                                           ue_root=self.root / "ue"))

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_failed_command_retains_argv_log_and_exit_code(self):
        with redirect_stdout(io.StringIO()):
            build = self.new_build(10)
            argv = [sys.executable, "-B", "-c", "print('intentional failure', flush=True); raise SystemExit(7)"]
            with self.assertRaisesRegex(RuntimeError, "exit 7"):
                build.command("failure-fixture", argv, cwd=self.root)
        self.assertEqual({"argv": argv, "cwd": str(self.root)},
                         json.loads((build.run / "failure-fixture.command.json").read_text()))
        self.assertEqual("7\n", (build.run / "failure-fixture.exit-code.txt").read_text())
        self.assertIn("intentional failure", (build.run / "failure-fixture.log").read_text())
        self.assertTrue(all(value == "FAIL" for value in build.checks.values()))

    @unittest.skipUnless(NATIVE, "requires native ARM64 Docker")
    def test_real_command_timeout_retains_failure_report(self):
        with redirect_stdout(io.StringIO()):
            build = self.new_build(1)
            with self.assertRaisesRegex(RuntimeError, "exit 124") as raised:
                build.command("timeout-fixture", [
                    sys.executable, "-B", "-c", "import time; print('waiting', flush=True); time.sleep(30)",
                ])
            build.error = str(raised.exception)
            self.assertEqual(1, build.finish())
        self.assertEqual("124\n", (build.run / "timeout-fixture.exit-code.txt").read_text())
        self.assertIn("waiting", (build.run / "timeout-fixture.log").read_text())
        report = json.loads((build.run / "stage-report.json").read_text())
        self.assertEqual("FAIL", report["status"])
        self.assertNotEqual(0, report["exit_code"])
        self.assertFalse((build.run / "source").exists())


if __name__ == "__main__":
    unittest.main()
