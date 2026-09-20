"""Harness-only report fixtures; these tests do not execute CARLA or a server."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/carla/runtime_entry_report.py"
spec = importlib.util.spec_from_file_location("runtime_entry_report", SCRIPT)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class RuntimeEntryReportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.files = {}
        for label in entry.LABELS:
            path = self.root / f"{label} fixture"
            path.write_bytes(f"harness-only {label}\n".encode())
            self.files[label] = path
        self.manifest = self.run_dir / "inputs.json"
        self.child = self.run_dir / "endpoint/stage-report.json"
        self.child_evidence = self.root / "endpoint-fixture.json"
        self.child_evidence.write_text('{"test_only":true}\n', encoding="utf-8")

    def capture(self):
        self.identity = entry.capture(self.manifest, list(self.files.items()))
        return self.identity

    def write_child(self, mode="rpc", **overrides):
        stage, scope = entry.runtime.STAGES[mode]
        values = dict(
            stage_id=stage, scope=scope, exit_code=0, required_checks=["fixture"],
            checks={"fixture": "PASS"}, evidence={"fixture": self.child_evidence},
            sources={"carla": {"location": "harness-only/carla", "revision": "fake-fixture"},
                     "ue": {"location": "harness-only/ue", "revision": "fake-fixture"}},
            command=["harness-only-report-fixture"],
        )
        values.update(overrides)
        return entry.stage_report.write_report(self.child, **values)

    def finalize(self, mode="rpc", code=0, identity=None):
        report = entry.finalize(self.run_dir, mode, code, identity or self.identity)
        raw_path = self.run_dir / report["evidence"]["finalize-result"]["path"]
        self.raw = json.loads(raw_path.read_bytes())
        return report

    def validate_root(self, mode="rpc"):
        return entry.stage_report.validate_report(
            self.run_dir / "stage-report.json", stage_id=entry.STAGE_ID, scope=entry.root_scope(mode))

    def cli(self, *args):
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, timeout=20)

    def test_capture_cli_outputs_only_digest_and_absolute_structured_records(self):
        args = ["capture", "--output", self.manifest]
        for label, path in self.files.items():
            args.extend(["--file", label, path])
        result = self.cli(*args)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertRegex(result.stdout, r"^[0-9a-f]{64}\n$")
        self.assertEqual("", result.stderr)
        self.assertEqual(hashlib.sha256(self.manifest.read_bytes()).hexdigest(), result.stdout.strip())
        data = json.loads(self.manifest.read_bytes())
        self.assertEqual(1, data["schema_version"])
        self.assertEqual(set(entry.LABELS), set(data["files"]))
        for label, record in data["files"].items():
            self.assertTrue(Path(record["path"]).is_absolute())
            self.assertEqual(str(self.files[label]), record["path"])
            self.assertEqual(hashlib.sha256(self.files[label].read_bytes()).hexdigest(), record["sha256"])

    def test_capture_rejects_missing_duplicate_unknown_and_nonregular_inputs(self):
        base = list(self.files.items())
        cases = (base[:-1], base + [base[0]], base[:-1] + [("unknown", base[0][1])],
                 base[:-1] + [("entry-reporter", self.root)],
                 base[:-1] + [("entry-reporter", self.root / "missing")])
        for files in cases:
            with self.subTest(files=files), self.assertRaises(ValueError):
                entry.capture(self.manifest, files)
            self.assertFalse(self.manifest.exists())

    def test_capture_and_finalize_never_overwrite_existing_output(self):
        self.capture()
        original = self.manifest.read_bytes()
        with self.assertRaises(ValueError):
            self.capture()
        self.assertEqual(original, self.manifest.read_bytes())
        self.write_child()
        self.finalize()
        root = self.run_dir / "stage-report.json"
        original = root.read_bytes()
        with self.assertRaises(ValueError):
            self.finalize()
        self.assertEqual(original, root.read_bytes())

    def test_verify_inputs_is_silent_read_only_and_does_not_require_child(self):
        self.capture()
        before = self.manifest.read_bytes()
        result = self.cli("verify-inputs", "--run-dir", self.run_dir, "--input-sha256", self.identity)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertEqual("", result.stderr)
        self.assertEqual(before, self.manifest.read_bytes())
        self.assertEqual(["inputs.json"], sorted(path.name for path in self.run_dir.iterdir()))

    def test_verify_inputs_rejects_changed_wheel_without_writing_a_report(self):
        self.capture()
        self.files["wheel"].write_bytes(b"changed before install\n")
        result = self.cli("verify-inputs", "--run-dir", self.run_dir, "--input-sha256", self.identity)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("wheel", result.stderr)
        self.assertFalse((self.run_dir / "stage-report.json").exists())
        self.assertEqual(["inputs.json"], sorted(path.name for path in self.run_dir.iterdir()))

    def test_verify_inputs_rejects_missing_directory_without_creating_it(self):
        missing = self.root / "absent"
        result = self.cli("verify-inputs", "--run-dir", missing, "--input-sha256", "0" * 64)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertFalse(missing.exists())

    def test_verify_cli_without_b_does_not_write_dependency_bytecode(self):
        self.capture()
        scripts = self.root / "isolated-scripts"
        scripts.mkdir()
        for source in (SCRIPT, Path(entry.runtime.__file__), Path(entry.stage_report.__file__)):
            (scripts / source.name).write_bytes(source.read_bytes())
        result = subprocess.run(
            [sys.executable, str(scripts / SCRIPT.name), "verify-inputs",
             "--run-dir", str(self.run_dir), "--input-sha256", self.identity],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)
        self.assertFalse((scripts / "__pycache__").exists())
        self.assertEqual(["inputs.json"], sorted(path.name for path in self.run_dir.iterdir()))

    def test_root_pass_requires_all_inputs_outer_status_and_exact_child(self):
        self.capture()
        child = self.write_child()
        report = self.finalize()
        self.assertEqual("PASS", report["status"])
        self.assertEqual(0, report["exit_code"])
        self.assertEqual(0, self.raw["outer_exit_code"])
        self.assertEqual(child["sources"], report["sources"])
        self.assertEqual(set(entry.CHECKS), set(report["checks"]))
        self.assertTrue(all(value == "PASS" for value in report["checks"].values()))
        self.assertEqual(entry.runtime.STAGES["rpc"][0], report["prerequisites"][0]["stage_id"])
        self.assertEqual(hashlib.sha256(self.child.read_bytes()).hexdigest(), report["prerequisites"][0]["sha256"])
        for check in entry.CHECKS:
            self.assertIsNotNone(report["evidence"][check]["sha256"])
        self.assertEqual(report, self.validate_root())

    def test_child_pass_does_not_override_outer_failure_or_timeout(self):
        self.capture()
        self.write_child()
        for code in (1, 2, 124, 137, -9):
            with self.subTest(code=code):
                run_dir = self.root / f"outer-{code}"
                run_dir.mkdir()
                (run_dir / "inputs.json").write_bytes(self.manifest.read_bytes())
                (run_dir / "endpoint").symlink_to(self.child.parent, target_is_directory=True)
                report = entry.finalize(run_dir, "rpc", code, self.identity)
                self.assertEqual("FAIL", report["status"])
                self.assertEqual(code, report["exit_code"])
                self.assertEqual("PASS", report["checks"]["endpoint"])
                self.assertEqual("FAIL", report["checks"]["outer-exit"])
                raw = json.loads((run_dir / report["evidence"]["outer-exit"]["path"]).read_bytes())
                self.assertEqual(code, raw["outer_exit_code"])

    def test_wheel_change_fails_despite_unchanged_gnu_hash_text(self):
        self.capture()
        self.write_child()
        (self.run_dir / "wheel.sha256").write_text("not parsed as the source of truth\n", encoding="utf-8")
        self.files["wheel"].write_bytes(b"different wheel\n")
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["exit_code"])
        self.assertEqual("FAIL", report["checks"]["input-wheel"])
        self.assertEqual(0, self.raw["outer_exit_code"])
        self.assertNotEqual(self.raw["inputs"]["wheel"]["expected_sha256"],
                            self.raw["inputs"]["wheel"]["actual_sha256"])

    def test_manifest_rewrite_cannot_replace_pinned_input_identity(self):
        self.capture()
        self.write_child()
        changed = json.loads(self.manifest.read_bytes())
        self.files["wheel"].write_bytes(b"replacement\n")
        changed["files"]["wheel"]["sha256"] = hashlib.sha256(b"replacement\n").hexdigest()
        self.manifest.write_text(json.dumps(changed), encoding="utf-8")
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["manifest"])
        self.assertIn("manifest SHA256 mismatch", " ".join(self.raw["errors"]))

    def test_missing_input_is_not_pass(self):
        self.capture()
        self.write_child()
        self.files["wrapper"].unlink()
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["input-wrapper"])

    def test_missing_manifest_and_child_create_failed_root_with_helper_provenance(self):
        report = entry.finalize(self.run_dir, "rpc", 2, "0" * 64)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(2, report["exit_code"])
        self.assertIn("entry-reporter", report["sources"])
        self.assertTrue(report["sources"]["entry-reporter"]["revision"].startswith("sha256:"))
        self.assertEqual("FAIL", report["checks"]["endpoint"])
        self.assertIsNone(report["prerequisites"][0]["sha256"])
        self.assertTrue((self.run_dir / "stage-report.json").is_file())

    def test_missing_child_with_outer_zero_still_fails(self):
        self.capture()
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["exit_code"])
        self.assertEqual("PASS", report["checks"]["outer-exit"])

    def test_harness_test_stage_and_wrong_scope_are_not_production_prerequisites(self):
        self.capture()
        self.write_child(stage_id="harness-test.carla-runtime-rpc")
        report = self.finalize()
        self.assertEqual("FAIL", report["checks"]["endpoint"])
        self.assertEqual("FAIL", report["status"])
        self.assertIn("entry-reporter", report["sources"])

    def test_rpc_child_does_not_satisfy_sensors_mode(self):
        self.capture()
        self.write_child()
        report = self.finalize(mode="sensors")
        self.assertEqual("FAIL", report["status"])
        self.assertNotEqual(entry.root_scope("rpc"), entry.root_scope("sensors"))

    def test_wrong_child_scope_is_rejected(self):
        self.capture()
        self.write_child(scope="HARNESS TEST ONLY")
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["endpoint"])

    def test_failed_child_cannot_be_promoted(self):
        self.capture()
        self.write_child(exit_code=1)
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["endpoint"])
        self.assertEqual(1, report["exit_code"])

    def test_sensors_exact_scope_succeeds_as_fixture_only(self):
        self.capture()
        self.write_child(mode="sensors")
        report = self.finalize(mode="sensors")
        self.assertEqual("PASS", report["status"])
        self.assertEqual(report, self.validate_root("sensors"))

    def test_changed_child_evidence_and_failed_child_are_rejected(self):
        self.capture()
        self.write_child()
        self.child_evidence.write_bytes(b"changed fixture\n")
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["endpoint"])

    def test_closed_logs_are_evidence_and_finalizer_log_is_excluded(self):
        self.capture()
        self.write_child()
        for name in entry.LOGS:
            (self.run_dir / name).write_text("closed harness-only log\n", encoding="utf-8")
        (self.run_dir / "finalize.log").write_text("still open\n", encoding="utf-8")
        report = self.finalize()
        self.assertEqual("PASS", report["status"])
        for name in entry.LOGS:
            self.assertIn(f"log-{name}", report["evidence"])
        self.assertNotIn("log-finalize.log", report["evidence"])
        (self.run_dir / "client.log").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(entry.stage_report.ReportError):
            self.validate_root()

    def test_invalid_optional_evidence_forces_nonzero_root_exit(self):
        self.capture()
        self.write_child()
        (self.run_dir / "client.log").mkdir()
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["exit_code"])
        self.assertEqual(1, self.raw["root_exit_code"])

    def test_input_change_while_writing_cannot_publish_pass(self):
        self.capture()
        self.write_child()
        original_writer = entry.stage_report.write_report
        def change_before_write(*args, **kwargs):
            self.files["wheel"].write_bytes(b"changed after initial validation\n")
            return original_writer(*args, **kwargs)
        with mock.patch.object(entry.stage_report, "write_report", side_effect=change_before_write):
            report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["input-wheel"])
        self.assertNotEqual(0, report["exit_code"])

    def test_symlink_input_retarget_is_detected(self):
        target = self.files["wheel"]
        link = self.root / "wheel-link"
        link.symlink_to(target)
        self.files["wheel"] = link
        self.capture()
        self.write_child()
        other = self.root / "other-wheel"
        other.write_bytes(b"new wheel\n")
        link.unlink()
        link.symlink_to(other)
        report = self.finalize()
        self.assertEqual("FAIL", report["checks"]["input-wheel"])

    def test_finalize_cli_failure_writes_root_and_returns_nonzero(self):
        self.capture()
        self.write_child()
        result = self.cli("finalize", "--run-dir", self.run_dir, "--mode", "rpc",
                          "--exit-code", "124", "--input-sha256", self.identity)
        self.assertNotEqual(0, result.returncode)
        report = json.loads((self.run_dir / "stage-report.json").read_bytes())
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(124, report["exit_code"])

    def test_invalid_manifest_schema_and_identity_fail_closed(self):
        self.write_child()
        self.manifest.write_text('{"schema_version":1,"files":{}}', encoding="utf-8")
        self.identity = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        report = self.finalize()
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["manifest"])


if __name__ == "__main__":
    unittest.main()
