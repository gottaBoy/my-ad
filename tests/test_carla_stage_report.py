import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/carla/stage_report.py"
spec = importlib.util.spec_from_file_location("carla_stage_report", SCRIPT)
stage_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage_report)

STAGE = "G4.1.meshdescription"
SCOPE = "Native MeshDescription bridge only; not Editor/Cook/RPC"


class StageReportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.evidence = self.root / "native mesh.json"
        self.evidence.write_text('{"vertices": 8, "triangles": 12}\n', encoding="utf-8")
        self.output = self.root / "run" / "stage.json"

    def write(self, output=None, **overrides):
        values = {
            "stage_id": STAGE,
            "scope": SCOPE,
            "exit_code": 0,
            "required_checks": ["mesh"],
            "checks": {"mesh": "PASS"},
            "evidence": {"mesh": self.evidence},
            "sources": {"ue": {"location": "/workspace/unreal-engine", "revision": "a" * 40}},
            "command": ["mesh-probe", "--fixture", "mesh with spaces.fbx", ""],
        }
        values.update(overrides)
        return stage_report.write_report(output or self.output, **values)

    def validate(self, path=None, **overrides):
        values = {"stage_id": STAGE, "scope": SCOPE}
        values.update(overrides)
        return stage_report.validate_report(path or self.output, **values)

    def assert_fail(self, needle, **values):
        report = self.write(**values)
        self.assertEqual("FAIL", report["status"])
        self.assertIn(needle, "; ".join(report["errors"]))
        self.assertEqual(report, json.loads(self.output.read_text(encoding="utf-8")))
        with self.assertRaises(stage_report.ReportError):
            self.validate()
        return report

    def rewrite(self, report, path=None):
        (path or self.output).write_text(json.dumps(report) + "\n", encoding="utf-8")

    def prerequisite(self, path, stage_id=STAGE, scope=SCOPE):
        return {"stage_id": stage_id, "scope": scope, "path": path}

    def cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                              cwd=cwd or self.root, capture_output=True, text=True, timeout=20)

    def cli_write_args(self, exit_code=0):
        return [
            "write", "--output", self.output, "--stage-id", STAGE, "--scope", SCOPE,
            "--exit-code", str(exit_code), "--required-check", "mesh",
            "--check", "mesh", "PASS", "--evidence", "mesh", self.evidence,
            "--source", "ue", "/workspace/unreal-engine", "a" * 40,
        ]

    def test_pass_records_identity_provenance_argv_and_sha256(self):
        report = self.write()
        self.assertEqual("PASS", report["status"])
        self.assertEqual([], report["errors"])
        self.assertEqual(1, report["schema_version"])
        self.assertEqual([], report["prerequisites"])
        self.assertEqual(STAGE, report["stage_id"])
        self.assertEqual(SCOPE, report["scope"])
        self.assertEqual("a" * 40, report["sources"]["ue"]["revision"])
        self.assertEqual(["mesh-probe", "--fixture", "mesh with spaces.fbx", ""], report["command"])
        self.assertEqual("../native mesh.json", report["evidence"]["mesh"]["path"])
        self.assertEqual(hashlib.sha256(self.evidence.read_bytes()).hexdigest(),
                         report["evidence"]["mesh"]["sha256"])
        self.assertEqual(report, self.validate())
        self.assertEqual(["stage.json"], sorted(path.name for path in self.output.parent.iterdir()))

    def test_exit_status_must_be_exact_integer_zero(self):
        for code in (1, 127, -9, True, False, "0", None, 0.0):
            with self.subTest(code=code):
                self.assert_fail("exit_code", exit_code=code)

    def test_required_checks_are_explicit_and_nonempty(self):
        for required in ([], ["mesh", "mesh"], [""], "mesh"):
            with self.subTest(required=required):
                self.assert_fail("required_checks", required_checks=required)
        self.assert_fail("missing required check", required_checks=["mesh", "rpc"])
        self.assert_fail("at least one named check", checks={})
        self.assert_fail("undeclared check", checks={"mesh": "PASS", "rpc": "PASS"})

    def test_only_explicit_pass_checks_are_accepted(self):
        for status in ("FAIL", "SKIP", "MISSING", "pass", "", None, True, 0):
            with self.subTest(status=status):
                self.assert_fail("is not PASS", checks={"mesh": status})

    def test_each_check_needs_its_own_named_evidence(self):
        self.assert_fail("missing evidence for check", evidence={})
        self.assert_fail("missing evidence for check", evidence={"unrelated-log": self.evidence})
        self.assert_fail("missing evidence for check", required_checks=["mesh", "rpc"],
                         checks={"mesh": "PASS", "rpc": "PASS"})

    def test_missing_nonregular_and_extra_missing_evidence_fail(self):
        for evidence in (self.root / "missing.json", self.root):
            with self.subTest(evidence=evidence):
                self.assert_fail("SHA256", evidence={"mesh": evidence})
        self.assert_fail("SHA256", evidence={"mesh": self.evidence, "log": self.root / "absent.log"})
        self.assert_fail("path", evidence={"mesh": ""})

    def test_empty_patch_is_valid_additional_evidence(self):
        patch = self.root / "clean-source.patch"
        patch.write_bytes(b"")
        report = self.write(evidence={"mesh": self.evidence, "source-patch": patch})
        self.assertEqual("PASS", report["status"])
        self.assertEqual(hashlib.sha256(b"").hexdigest(), report["evidence"]["source-patch"]["sha256"])
        self.validate()

    def test_changed_or_deleted_evidence_invalidates_stored_pass(self):
        self.write()
        self.evidence.write_text('{"vertices": 0, "triangles": 12}\n', encoding="utf-8")
        with self.assertRaisesRegex(stage_report.ReportError, "SHA256 mismatch"):
            self.validate()
        self.evidence.unlink()
        with self.assertRaisesRegex(stage_report.ReportError, "not a regular file"):
            self.validate()
        self.assertEqual("PASS", json.loads(self.output.read_text())["status"])

    def test_source_provenance_and_real_argv_are_required(self):
        for sources in ({}, {"ue": {}}, {"ue": {"location": "/ue", "revision": ""}},
                        {"ue": {"location": "", "revision": "a" * 40}}):
            with self.subTest(sources=sources):
                self.assert_fail("source", sources=sources)
        for command in ([], [""], ["  "], [False], ["probe", 1], "probe"):
            with self.subTest(command=command):
                self.assert_fail("argv", command=command)

    def test_identity_and_scope_are_mandatory_and_exact(self):
        self.write()
        with self.assertRaisesRegex(stage_report.ReportError, "stage_id"):
            self.validate(stage_id="G4.Editor")
        with self.assertRaisesRegex(stage_report.ReportError, "scope"):
            self.validate(scope="Editor/Cook/RPC")
        for values in ({"stage_id": ""}, {"scope": ""}, {"stage_id": None}, {"scope": None}):
            with self.subTest(values=values):
                self.assert_fail(next(iter(values)), **values)

    def test_validation_uses_report_directory_not_working_directory(self):
        self.write()
        result = self.cli("validate", self.output, "--stage-id", STAGE, "--scope", SCOPE, cwd="/tmp")
        self.assertEqual(0, result.returncode, result.stderr)
        moved = self.root / "moved"
        moved.mkdir()
        self.evidence.rename(moved / self.evidence.name)
        self.output.parent.rename(moved / "run")
        self.validate(moved / "run" / self.output.name)

    def test_prerequisite_needs_own_exact_stage_and_scope(self):
        prior = self.root / "backend.json"
        self.write(prior, stage_id="G4.1.assimp", scope="Assimp backend only")
        prerequisite = self.prerequisite(prior, "G4.1.assimp", "Assimp backend only")
        report = self.write(prerequisites=[prerequisite])
        self.assertEqual("PASS", report["status"])
        self.assertEqual(hashlib.sha256(prior.read_bytes()).hexdigest(),
                         report["prerequisites"][0]["sha256"])
        self.validate()
        self.assert_fail("stage_id", prerequisites=[{**prerequisite, "stage_id": "G4.Editor"}])
        self.assert_fail("scope", prerequisites=[{**prerequisite, "scope": "Editor/Cook/RPC"}])
        self.assert_fail("duplicate prerequisite", prerequisites=[prerequisite, prerequisite])
        self.assert_fail("named check", prerequisites=[prerequisite], checks={})

    def test_missing_failed_or_legacy_prerequisites_do_not_pass(self):
        prior = self.root / "backend.json"
        self.assert_fail("prerequisite", prerequisites=[self.prerequisite(prior)])
        self.write(prior, exit_code=1)
        self.assert_fail("report status is not PASS", prerequisites=[self.prerequisite(prior)])
        prior.write_text('{"status": "PASS", "scope": "Assimp FBX backend only"}\n', encoding="utf-8")
        original = prior.read_bytes()
        self.assert_fail("schema_version", prerequisites=[self.prerequisite(prior)])
        self.assertEqual(original, prior.read_bytes())

    def test_prerequisites_recursively_recheck_leaf_evidence(self):
        leaf_evidence = self.root / "backend.log"
        leaf_evidence.write_text("backend tests passed\n", encoding="utf-8")
        leaf = self.root / "backend.json"
        middle = self.root / "bridge.json"
        self.write(leaf, stage_id="backend", scope="backend only", evidence={"mesh": leaf_evidence})
        self.write(middle, stage_id="bridge", scope="bridge only",
                   prerequisites=[self.prerequisite(leaf, "backend", "backend only")])
        self.write(prerequisites=[self.prerequisite(middle, "bridge", "bridge only")])
        self.validate()
        leaf_evidence.write_text("backend tests failed\n", encoding="utf-8")
        with self.assertRaisesRegex(stage_report.ReportError, "SHA256 mismatch"):
            self.validate()

    def test_changed_prerequisite_report_is_rejected_even_when_still_pass(self):
        prior = self.root / "backend.json"
        original = self.write(prior)
        self.write(prerequisites=[self.prerequisite(prior)])
        original["command"] = ["different-probe"]
        self.rewrite(original, prior)
        self.validate(prior)
        with self.assertRaisesRegex(stage_report.ReportError, "prerequisite SHA256 mismatch"):
            self.validate()

    def test_self_evidence_and_prerequisite_cycles_are_rejected(self):
        self.write()
        self.assert_fail("own evidence", evidence={"mesh": self.output})
        report = self.write()
        report["prerequisites"] = [{
            "stage_id": STAGE, "scope": SCOPE, "path": self.output.name, "sha256": "0" * 64,
        }]
        self.rewrite(report)
        with self.assertRaisesRegex(stage_report.ReportError, "prerequisite cycle"):
            self.validate()

    def test_forged_pass_is_recomputed_instead_of_trusted(self):
        mutations = (
            {"checks": {}}, {"checks": {"mesh": "FAIL"}}, {"exit_code": 9},
            {"required_checks": ["rpc"]}, {"evidence": {}}, {"command": []}, {"sources": {}},
            {"schema_version": 0}, {"schema_version": True}, {"errors": ["ignored failure"]},
            {"status": "FAIL"}, {"created_at": "yesterday"}, {"created_at": "2026-09-14T12:00:00"},
            {"prerequisites": None}, {"prerequisites": [{}]},
            {"evidence": {"mesh": {"path": "../native mesh.json", "sha256": "0" * 64}}},
        )
        valid = self.write()
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.rewrite({**valid, **mutation})
                with self.assertRaises(stage_report.ReportError):
                    self.validate()

    def test_missing_required_fields_and_invalid_shapes_fail_closed(self):
        valid = self.write()
        for name in valid:
            with self.subTest(missing=name):
                malformed = dict(valid)
                del malformed[name]
                self.rewrite(malformed)
                with self.assertRaises(stage_report.ReportError):
                    self.validate()
        for mutation in (
            {"required_checks": [None]}, {"required_checks": [["mesh"]]},
            {"checks": []}, {"evidence": ["log"]}, {"evidence": {"mesh": None}},
            {"evidence": {"mesh": {"path": None, "sha256": "0" * 64}}},
            {"sources": {"ue": None}}, {"prerequisites": [None]}, {"created_at": []},
        ):
            with self.subTest(mutation=mutation):
                self.rewrite({**valid, **mutation})
                with self.assertRaises(stage_report.ReportError):
                    self.validate()

    def test_malformed_ambiguous_and_legacy_json_is_not_migrated(self):
        self.output.parent.mkdir()
        for raw in (
            b'{"status": "PASS"}', b'{"status":"FAIL","status":"PASS"}', b"[]",
            b'{"value": NaN}', b'{"value": Infinity}', b'{"schema_version":', b"\xff",
        ):
            with self.subTest(raw=raw):
                self.output.write_bytes(raw)
                with self.assertRaises(stage_report.ReportError):
                    self.validate()
                self.assertEqual(raw, self.output.read_bytes())

    def test_cli_writes_and_validates_from_unrelated_cwd(self):
        command = ["mesh-probe", "--fixture", "mesh with spaces.fbx", ""]
        result = self.cli(*self.cli_write_args(), "--command", *command)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"PASS {STAGE}", result.stdout)
        self.assertEqual(command, self.validate()["command"])
        result = self.cli("validate", self.output, "--stage-id", STAGE, "--scope", SCOPE, cwd="/tmp")
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.cli("validate", self.output, "--stage-id", "G4.Editor", "--scope", SCOPE)
        self.assertEqual(1, result.returncode)
        self.assertIn("stage_id", result.stderr)

    def test_cli_writes_fail_report_on_failed_process_or_absent_checks(self):
        result = self.cli(*self.cli_write_args(exit_code=23), "--command", "mesh-probe")
        self.assertEqual(1, result.returncode, result.stderr)
        report = json.loads(self.output.read_text())
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(23, report["exit_code"])
        result = self.cli("write", "--output", self.output, "--stage-id", STAGE, "--scope", SCOPE,
                          "--exit-code", "0", "--command", "mesh-probe")
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual("FAIL", json.loads(self.output.read_text())["status"])
        self.assertIn("named check", result.stderr)

    def test_cli_rejects_duplicate_names_and_missing_identity(self):
        for duplicate in (
            ["--check", "mesh", "FAIL"],
            ["--evidence", "mesh", self.evidence],
            ["--source", "ue", "/different/source", "b" * 40],
        ):
            with self.subTest(duplicate=duplicate):
                result = self.cli(*self.cli_write_args(), *duplicate, "--command", "mesh-probe")
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("duplicate name", result.stderr)
                self.assertFalse(self.output.exists())
        self.write()
        result = self.cli("validate", self.output)
        self.assertEqual(2, result.returncode, result.stderr)

    def test_cli_prerequisite_requires_exact_identity_and_rejects_missing_file(self):
        prior = self.root / "backend.json"
        self.write(prior, stage_id="backend", scope="backend only")
        args = [*self.cli_write_args(), "--prerequisite", "backend", "backend only", prior,
                "--command", "mesh-probe"]
        result = self.cli(*args)
        self.assertEqual(0, result.returncode, result.stderr)
        self.validate()
        prior.unlink()
        result = self.cli(*args)
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertIn("prerequisite", result.stderr)
        self.assertEqual("FAIL", json.loads(self.output.read_text())["status"])


if __name__ == "__main__":
    unittest.main()
