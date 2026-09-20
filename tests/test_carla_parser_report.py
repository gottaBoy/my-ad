"""Pure parser report validation and failure-only stage fixtures; no UE execution."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/carla"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("native_parser_checker", SCRIPTS / "check_ue_parser.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class NativeParserReportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.outputs = self.root / "outputs"
        self.outputs.mkdir()
        self.fixture = self.root / "control.fbx"
        self.fixture.write_bytes(b"synthetic validator input, not parsed FBX")
        files = []
        for index in range(11):
            path = self.outputs / (str(index) + (".itc" if index < 3 else ".payload"))
            path.write_bytes(b"synthetic output bytes")
            files.append(str(path))
        self.data = {"stage": checker.STAGE, "scope": checker.SCOPE, "status": "PASS", "error": "",
                     "source_sha256": hashlib.sha256(self.fixture.read_bytes()).hexdigest(),
                     "self_tests": sum(checker.EXPECTED.values()),
                     "checks": list(checker.EXPECTED.elements()), "files": files}

    def test_valid_multiset_returns_all_paths_without_writing_a_stage(self):
        paths = checker.validate_native(self.data, self.root, self.fixture)
        self.assertEqual(11, len(paths))
        self.assertEqual(31, self.data["self_tests"])
        self.assertFalse((self.root / "stage-report.json").exists())

    def test_partial_checks_and_same_count_substitution_are_rejected(self):
        for kind in ("remove", "substitute", "count", "boolean", "types"):
            data = copy.deepcopy(self.data)
            if kind == "remove": data["checks"].pop()
            if kind == "substitute": data["checks"][0] = data["checks"][1]
            if kind == "count": data["self_tests"] = 30
            if kind == "boolean": data["self_tests"] = True
            if kind == "types": data["checks"][0] = {}
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                checker.validate_native(data, self.root, self.fixture)

    def test_scope_status_and_input_identity_are_exact(self):
        for field, value in (("stage", "interchange-node-bootstrap"), ("scope", "Editor"),
                             ("status", "FAIL"), ("error", "failed"), ("source_sha256", "0" * 64)):
            data = copy.deepcopy(self.data)
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                checker.validate_native(data, self.root, self.fixture)
        with self.assertRaises(ValueError):
            checker.validate_native([], self.root, self.fixture)

    def test_duplicate_missing_empty_and_escaping_outputs_are_rejected(self):
        for kind in ("duplicate", "missing", "empty", "escape", "relative"):
            data = copy.deepcopy(self.data)
            if kind == "duplicate": data["files"][0] = data["files"][1]
            if kind == "missing": data["files"][0] = str(self.outputs / "missing.itc")
            if kind == "relative": data["files"][0] = "outputs/0.itc"
            if kind == "empty":
                path = self.outputs / "empty.itc"
                path.touch()
                data["files"][0] = str(path)
            if kind == "escape": data["files"][0] = str(self.fixture)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                checker.validate_native(data, self.root, self.fixture)

    def test_symlink_files_and_directories_are_rejected(self):
        link = self.outputs / "alias.itc"
        link.symlink_to(self.outputs / "0.itc")
        data = copy.deepcopy(self.data)
        data["files"][0] = str(link)
        with self.assertRaises(ValueError):
            checker.validate_native(data, self.root, self.fixture)
        directory = self.outputs / "alias"
        directory.symlink_to(self.outputs, target_is_directory=True)
        data["files"][0] = str(directory / "0.itc")
        with self.assertRaises(ValueError):
            checker.validate_native(data, self.root, self.fixture)

    def test_nonzero_process_and_missing_dependencies_write_only_fail(self):
        args = SimpleNamespace(run_dir=self.root, program=self.root / "missing-program", input=self.fixture,
            ue_root=self.root / "ue", exit_code=124, ufbx_report=self.root / "missing-prerequisite")
        self.assertEqual(1, checker.run(args))
        report = json.loads((self.root / "stage-report.json").read_text())
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["native"])
        self.assertEqual("FAIL", report["checks"]["deployment"])
        self.assertEqual("FAIL", report["checks"]["prerequisite"])
        diagnostic = json.loads((self.root / "parser-diagnostic.json").read_text())
        self.assertEqual(124, diagnostic["process_exit_code"])
        before = (self.root / "stage-report.json").read_bytes()
        with self.assertRaises(ValueError):
            checker.run(args)
        self.assertEqual(before, (self.root / "stage-report.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
