"""Worker prepare and evidence contracts; no UBT, child Worker or hardware PASS."""

from collections import Counter
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
PREPARE_SCRIPT = SCRIPTS / "prepare_interchange_worker.py"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location("worker_prepare_contract", PREPARE_SCRIPT)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    spec = importlib.util.spec_from_file_location("worker_evidence_contract", SCRIPTS / "check_ue_worker.py")
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
finally:
    sys.path.pop(0)

STAGE = "ue-ufbx-worker-static"
SCOPE = ("Real InterchangeWorker static ufbx over UE command-queue TCP; not production "
         "WorkerHandler, full FBX, Editor or Cook")


class WorkerReportFixture:
    """Synthetic report/files for validators, never Worker execution evidence."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "run"
        self.outputs = self.run_dir / "outputs/valid"
        self.outputs.mkdir(parents=True)
        self.fixture = self.root / "input.fbx"
        self.fixture.write_bytes(b"synthetic source; not an FBX\n")
        self.worker = self.root / "not-a-worker"
        self.worker.write_bytes(b"not an ELF; never execute this fixture\n")
        files = []
        for index in range(2):
            path = self.outputs / f"SceneDescription-{index + 1}.itc"
            path.write_bytes(b"synthetic graph; not UE serialization\n")
            files.append(str(path))
        for index in range(6):
            path = self.outputs / (f"{index + 1:064x}" + ".payload")
            path.write_bytes(b"synthetic payload; not UE serialization\n")
            files.append(str(path))
        self.data = {
            "stage": STAGE, "scope": SCOPE, "status": "PASS", "error": "",
            "source_sha256": hashlib.sha256(self.fixture.read_bytes()).hexdigest(),
            "worker": str(self.worker), "protocol_version": "20.0.0.0",
            "parent_pid": 1000, "worker_pids": [1001, 1002, 1003, 1004, 1005],
            "self_tests": 23, "checks": list(checker.EXPECTED.elements()), "files": files,
        }

    def validate(self, data=None):
        return checker.validate_native(self.data if data is None else data,
                                       self.run_dir, self.fixture, self.worker)


class WorkerValidatorTest(WorkerReportFixture, unittest.TestCase):
    def test_counter_is_grounded_in_real_worker_check_names_and_loops(self):
        self.assertEqual(STAGE, checker.STAGE)
        self.assertEqual(SCOPE, checker.SCOPE)
        self.assertEqual(23, sum(checker.EXPECTED.values()))
        self.assertEqual(6, checker.EXPECTED["IPC mesh consumer readback"])
        self.assertEqual(3, checker.EXPECTED["queued requests preserve task and error isolation"])
        self.assertEqual(2, checker.EXPECTED["bad version returns protocol error and nonzero exit"])
        source = (SCRIPTS / "ue-interchange/Source/CarlaInterchangeProbe/Private"
                  / "CarlaNativeWorkerChecks.cpp").read_text()
        for name in checker.EXPECTED:
            self.assertEqual(1, source.count("TEXT(" + chr(34) + name + chr(34) + ")"), name)
        for token in ("Source.Meshes.Num() != 2", "for (const auto& Item : Source.Meshes)",
                      "for (const FTransform& Transform : Transforms)", "Index < 3", "Index < 2",
                      "Pids.Add(Peer.Pid)", "Pids.Add(Rejected.Pid)",
                      "Pids.Add(Lost.Pid)", "Pids.Add(Refused.Pid)"):
            self.assertIn(token, source)

    def test_complete_pure_fixture_does_not_publish_stage_report(self):
        paths = self.validate()
        self.assertEqual(8, len(paths))
        self.assertEqual(Counter({".itc": 2, ".payload": 6}), Counter(p.suffix for p in paths))
        self.assertFalse((self.run_dir / "stage-report.json").exists())

    def test_identity_source_scope_status_and_worker_must_match(self):
        for field, wrong in (("stage", "ue-ufbx-parser-static"), ("scope", "WorkerHandler"),
                             ("status", "FAIL"), ("error", "child failed"),
                             ("source_sha256", "0" * 64), ("worker", str(self.worker) + ".other")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.validate({**self.data, field: wrong})
        self.fixture.write_bytes(b"changed input\n")
        with self.assertRaises(ValueError):
            self.validate()
        for value in (None, [], True, "not an object"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                checker.validate_native(value, self.run_dir, self.fixture, self.worker)

    def test_exact_counter_rejects_missing_extra_or_forged_multiplicities(self):
        names = self.data["checks"]
        swapped = list(names)
        swapped[swapped.index("bad version returns protocol error and nonzero exit")] = names[0]
        for values in (names[:-1], names + [names[0]], [names[0]] * 23, swapped,
                       ["unknown", *names[1:]], [None, *names[1:]],
                       [{"check": names[0]}, *names[1:]], dict(checker.EXPECTED)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.validate({**self.data, "checks": values})

    def test_self_test_count_requires_exact_integer(self):
        for count in (True, False, 0, 22, 24, 23.0, "23", None):
            with self.subTest(count=count), self.assertRaises(ValueError):
                self.validate({**self.data, "self_tests": count})

    def test_child_pids_are_positive_distinct_and_different_from_parent(self):
        good = self.data["worker_pids"]
        for values in (good[:-1], good + [1006], [1001] * 5, [1000, *good[1:]],
                       [0, *good[1:]], [-1, *good[1:]], [True, *good[1:]],
                       [1001.0, *good[1:]], ["1001", *good[1:]], "pids", None):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.validate({**self.data, "worker_pids": values})
        for parent in (0, -1, True, 1000.0, "1000", None):
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                self.validate({**self.data, "parent_pid": parent})

    def test_protocol_version_requires_four_numeric_fields_and_binary_lwc(self):
        for value in ("", "20.0.0", "20.bad.0.bad", "20..0.0.0", "20.0.0.2",
                      "-20.0.0.0", "20.0.0.0junk", None, 20):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate({**self.data, "protocol_version": value})

    def test_eight_unique_files_require_two_graphs_and_six_payloads(self):
        files = self.data["files"]
        extra = self.outputs / "SceneDescription-3.itc"
        extra.write_bytes(b"extra graph fixture")
        for values in (files[:-1], files + [files[0]], [files[0], *files[:-1]],
                       [*files[:-1], str(extra)], [None, *files[1:]], "files"):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.validate({**self.data, "files": values})

    def test_output_paths_cannot_escape_valid_directory(self):
        outside = self.root / "outside.itc"
        outside.write_bytes(b"outside")
        sibling = self.run_dir / "outputs/valid-other/SceneDescription-1.itc"
        sibling.parent.mkdir()
        sibling.write_bytes(b"sibling")
        for value in (str(outside), str(sibling), "outputs/valid/SceneDescription-1.itc",
                      str(self.outputs / "../valid-other/SceneDescription-1.itc")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.validate({**self.data, "files": [value, *self.data["files"][1:]]})

    def test_missing_empty_directory_and_symlink_file_are_rejected(self):
        path = Path(self.data["files"][0])
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError): self.validate()
        path.write_bytes(b"")
        with self.assertRaises(ValueError): self.validate()
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError): self.validate()
        path.rmdir()
        target = self.outputs / "same-content.itc"
        target.write_bytes(original)
        path.symlink_to(target)
        with self.assertRaises(ValueError): self.validate()
        path.unlink()
        path.symlink_to(self.root / "missing")
        with self.assertRaises(ValueError): self.validate()

    def test_symlink_parent_is_rejected_even_with_internal_target(self):
        alias = self.run_dir / "outputs/alias"
        alias.symlink_to(self.outputs, target_is_directory=True)
        path = alias / Path(self.data["files"][0]).name
        with self.assertRaises(ValueError):
            self.validate({**self.data, "files": [str(path), *self.data["files"][1:]]})

    def test_negative_scenarios_allow_logs_but_never_graphs_or_payloads(self):
        for name in ("version-0", "version-1", "disconnect", "no-server"):
            directory = self.run_dir / "outputs" / name
            directory.mkdir()
            (directory / "worker.log").write_text("negative case log fixture\n")
        self.assertEqual(8, len(self.validate()))
        for name in ("version-0", "version-1", "disconnect", "no-server"):
            for suffix in (".itc", ".payload"):
                with self.subTest(name=name, suffix=suffix):
                    path = self.run_dir / "outputs" / name / "nested" / ("unexpected" + suffix)
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(b"forbidden negative asset")
                    with self.assertRaisesRegex(ValueError, "negative Worker"):
                        self.validate()
                    path.unlink()


class WorkerFailureEvidenceTest(WorkerReportFixture, unittest.TestCase):
    """All run() fixtures retain a real failure gate; no production stage PASS."""

    def setUp(self):
        super().setUp()
        self.peer = self.root / "not-a-peer"
        self.peer.write_bytes(b"not an executable\n")
        self.ue = self.root / "engine"
        self.ue.mkdir()
        self.upstream = self.root / "absent-upstream.json"
        (self.run_dir / "native.json").write_text(json.dumps(self.data))
        (self.run_dir / "native.log").write_text("fixture only; no Worker executed\n")
        (self.run_dir / "build.log").write_text("synthetic peer build log\n")
        (self.run_dir / "worker-build.log").write_text("synthetic Worker build log\n")
        (self.run_dir / "worker-linkage.log").write_text("synthetic linkage log\n")
        (self.run_dir / "ue-commit.txt").write_text("a" * 40 + "\n")

    def args(self, code=17):
        return SimpleNamespace(program=self.peer, worker=self.worker, input=self.fixture,
                               run_dir=self.run_dir, ue_root=self.ue, ufbx_report=self.upstream,
                               exit_code=code)

    def run_failure(self, code=17):
        with contextlib.redirect_stdout(io.StringIO()):
            result = checker.run(self.args(code))
        self.assertEqual(1, result)
        report = checker.read_json(self.run_dir / "stage-report.json")
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(STAGE, report["stage_id"])
        self.assertEqual(SCOPE, report["scope"])
        self.assertNotEqual(0, report["exit_code"])
        with self.assertRaises(ValueError):
            checker.validate_report(self.run_dir / "stage-report.json", stage_id=STAGE, scope=SCOPE)
        return report

    def diagnostics(self):
        return checker.read_json(self.run_dir / "worker-diagnostic.json")

    def test_failed_process_leaves_report_and_original_exit_code(self):
        report = self.run_failure(124)
        self.assertEqual(124, self.diagnostics()["probe_exit_code"])
        self.assertIn("Worker probe exit=124", " ".join(self.diagnostics()["errors"]))
        self.assertEqual("FAIL", report["checks"]["native"])
        self.assertEqual("FAIL", report["checks"]["prerequisite"])
        self.assertEqual([], report["prerequisites"])

    def test_zero_exit_and_claimed_pass_do_not_bypass_elf_validation(self):
        report = self.run_failure(0)
        self.assertEqual("FAIL", report["checks"]["architecture"])
        self.assertIn("ARM64 ELF", " ".join(self.diagnostics()["errors"]))

    def test_wrong_scope_prerequisite_is_rejected_without_hardware_pass_fixture(self):
        # A PASS report in a HARNESS-ONLY identity, not a synthetic backend PASS.
        evidence = self.root / "harness.txt"
        evidence.write_bytes(b"harness only\n")
        checker.write_report(self.upstream, stage_id="harness-test.ufbx", scope="harness only",
            exit_code=0, required_checks=["fixture"], checks={"fixture": "PASS"},
            evidence={"fixture": evidence}, sources={"fixture": {"location": str(evidence),
            "revision": "harness-only"}}, command=["fixture-not-a-build"])
        report = self.run_failure()
        self.assertEqual("FAIL", report["checks"]["prerequisite"])
        self.assertEqual([], report["prerequisites"])
        errors = " ".join(self.diagnostics()["errors"])
        self.assertIn("ufbx-fbx-static-backend", errors)
        self.assertIn(checker.UFBX_SCOPE, errors)

    def test_existing_report_and_dangling_symlink_are_preserved(self):
        path = self.run_dir / "stage-report.json"
        path.write_bytes(b"existing evidence")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            checker.run(self.args())
        self.assertEqual(b"existing evidence", path.read_bytes())
        path.unlink()
        target = self.root / "must-not-create.json"
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "overwrite"):
            checker.run(self.args())
        self.assertTrue(path.is_symlink())
        self.assertFalse(target.exists())

    def test_failed_report_hashes_native_and_child_raw_logs(self):
        child = self.run_dir / "outputs/version-0/worker.log"
        child.parent.mkdir()
        child.write_text("bad version log fixture\n")
        report = self.run_failure()
        for key, path in (("run.native.log", self.run_dir / "native.log"),
                          ("log.outputs/version-0/worker.log", child)):
            record = report["evidence"][key]
            self.assertEqual(path, (self.run_dir / record["path"]).resolve())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])

    def test_deployment_checks_live_and_retained_sources_and_records_patch_paths(self):
        directory = self.root / "deployment"
        relative = str(prepare.WORKER / "Private/InterchangeWorkerStatic.cpp")
        content = b"synthetic source; not compiled\n"
        for path in (self.ue / relative, directory / "after" / relative):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        report = directory / "deployment.json"
        report.write_text(json.dumps({"kind": "worker_source_not_runtime_evidence",
            "engine_files": {relative: hashlib.sha256(content).hexdigest()}}))
        patch = directory / "changes.patch"
        patch.write_text("synthetic patch\n")
        pointer = self.run_dir / "worker-prepare-dir.txt"
        pointer.write_text(str(directory) + "\n")
        evidence = {}
        checker.deployment(self.run_dir, self.ue, pointer.name,
                           "worker_source_not_runtime_evidence", {relative}, evidence)
        self.assertEqual(report, evidence[pointer.name + ".deployment"])
        self.assertEqual(patch, evidence[pointer.name + ".patch"])
        for path in (self.ue / relative, directory / "after" / relative):
            path.write_bytes(b"changed source")
            with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                checker.deployment(self.run_dir, self.ue, pointer.name,
                                   "worker_source_not_runtime_evidence", {relative}, {})
            path.write_bytes(content)
        with self.assertRaisesRegex(ValueError, "source deployment"):
            checker.deployment(self.run_dir, self.ue, pointer.name,
                               "worker_source_not_runtime_evidence", {relative, "missing"}, {})

    def test_output_hashes_are_retained_even_when_prerequisite_is_missing(self):
        # The actual interpreter ELF only reaches the negative retention branch.
        # Missing deployment and prerequisite forbid a production PASS.
        native = Path(sys.executable).resolve()
        if not checker.native_elf(native):
            self.skipTest("negative retention fixture needs an ARM64 interpreter")
        self.peer = self.worker = native
        self.data["worker"] = str(native)
        (self.run_dir / "native.json").write_text(json.dumps(self.data))
        report = self.run_failure(0)
        records = [record for key, record in report["evidence"].items() if key.startswith("output.")]
        self.assertEqual(8, len(records))
        self.assertEqual("FAIL", report["checks"]["prerequisite"])
        for record in records:
            path = (self.run_dir / record["path"]).resolve()
            self.assertTrue(path.is_relative_to(self.outputs))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])

    def test_cli_requires_worker_and_exit_code(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "check_ue_worker.py")],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(2, result.returncode)
        self.assertIn("--worker", result.stderr)
        self.assertIn("--exit-code", result.stderr)


class WorkerPrepareTest(unittest.TestCase):
    """Run the real replay script on temporary engine fixtures only."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ue = self.root / "engine"
        self.artifacts = self.root / "artifacts"
        module = self.ue / prepare.WORKER
        (module / "Private").mkdir(parents=True)
        self.rule = module / "InterchangeWorker.Build.cs"
        self.rule.write_text(
            '// fixture of the untouched default Worker rules\n'
            'public class InterchangeWorker {\n'
            '\tvoid Configure() {\n'
            '\t\tPrivateDependencyModuleNames.Add("InterchangeFbxParser");\n'
            '\t\tAddEngineThirdPartyPrivateStaticDependencies(Target,\n'
            '\t\t\tnew string[] { "FBX" });\n'
            '\t}\n}\n', encoding="utf-8")
        self.main = module / "Private/InterchangeWorker.cpp"
        self.main.write_text(
            '// Original WorkerMain fixture, not compiled\n'
            'int Main() {\n'
            '\tUE_SET_LOG_VERBOSITY(LogInterchangeWorker, Verbose);\n'
            '\tFString WorkerVersionError;\n'
            '\t{\n\t\tWorker.Run(WorkerVersionError);\n\t}\n'
            '\treturn EXIT_SUCCESS;\n}\n'
            'int Entry() {\n'
            '\tGEngineLoop.PreInit(ArgC, ArgV);\n'
            '\treturn Main();\n}\n', encoding="utf-8")
        self.impl = module / "Private/InterchangeWorkerImpl.cpp"
        self.impl.write_text(
            '// Original asynchronous SDK Worker implementation\n'
            '#include "InterchangeWorkerImpl.h"\n'
            'void OriginalRunTask() {\n'
            '    ActiveThreads.Add(ThreadName, Async(EAsyncExecution::ThreadPool,\n'
            '        [this, Command, ThreadName]() { ProcessCommand(Command, ThreadName); }));\n'
            '}\n', encoding="utf-8")
        self.generated = module / "Private/InterchangeWorkerStatic.cpp"
        self.untouched = (
            prepare.WORKER / "Private/InterchangeWorkerImpl.h",
            prepare.WORKER / "InterchangeWorker.Target.cs",
            Path("Engine/Source/ThirdParty/FBX/2020.2/include/fbxsdk.h"),
            Path("Engine/Binaries/ThirdParty/FBX/2020.2/Linux/libfbxsdk.so"),
        )
        for relative in self.untouched:
            path = self.ue / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preserve original SDK/Worker input\n")
        self.original = self.snapshot()

    def snapshot(self):
        return {path.relative_to(self.ue).as_posix(): path.read_bytes()
                for path in self.ue.rglob("*") if path.is_file()}

    def test_default_sdk_rules_thread_pool_and_unrelated_inputs_are_preserved(self):
        prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(
            prepare.BEGIN.encode() + self.original[self.impl.relative_to(self.ue).as_posix()]
            + prepare.END.encode(), self.impl.read_bytes())
        insert = prepare.RULE_INSERT.replace("\t\t\t", "\t\t")
        self.assertEqual(
            self.original[self.rule.relative_to(self.ue).as_posix()].decode(),
            self.rule.read_text().replace(insert, "", 1))
        main = self.main.read_text()
        self.assertIn("#else\n\t\tWorker.Run(WorkerVersionError);\n#endif", main)
        self.assertIn("#else\n\tGEngineLoop.PreInit(ArgC, ArgV);\n#endif", main)
        for relative in self.untouched:
            self.assertEqual(self.original[str(relative)], (self.ue / relative).read_bytes())
        self.assertIn('LoadModule(TEXT("CarlaUfbxInterchange"))', main)
        self.assertNotIn("CarlaUfbxInterchange", self.rule.read_text())

    def test_prepare_retains_patch_before_after_and_engine_hashes(self):
        run = prepare.prepare(self.ue, self.artifacts)
        report = json.loads((run / "deployment.json").read_text())
        self.assertEqual("worker_source_not_runtime_evidence", report["kind"])
        self.assertIs(True, report["default_path_preserved"])
        self.assertEqual(4, report["changes"])
        expected = {str(prepare.WORKER / name) for name in (
            "InterchangeWorker.Build.cs", "Private/InterchangeWorker.cpp",
            "Private/InterchangeWorkerImpl.cpp", "Private/InterchangeWorkerStatic.cpp")}
        self.assertEqual(expected, set(report["engine_files"]))
        for relative, digest in report["engine_files"].items():
            after = (run / "after" / relative).read_bytes()
            self.assertEqual(after, (self.ue / relative).read_bytes())
            self.assertEqual(hashlib.sha256(after).hexdigest(), digest)
            if relative in self.original:
                self.assertEqual(self.original[relative], (run / "before" / relative).read_bytes())
        patch = (run / "changes.patch").read_text()
        self.assertIn("--- /dev/null", patch)
        self.assertIn("+#if !CARLA_INTERCHANGE_UFBX_STATIC", patch)
        self.assertNotIn("libfbxsdk.so", patch)
        self.assertEqual(
            (SCRIPTS / "ue-parser/InterchangeWorkerStatic.cpp").read_bytes(),
            self.generated.read_bytes())

    def test_prepare_is_idempotent_and_never_overwrites_previous_artifacts(self):
        first = prepare.prepare(self.ue, self.artifacts)
        previous = {name: (first / name).read_bytes()
                    for name in ("deployment.json", "changes.patch")}
        deployed = self.snapshot()
        second = prepare.prepare(self.ue, self.artifacts)
        self.assertNotEqual(first, second)
        self.assertEqual(deployed, self.snapshot())
        self.assertEqual(0, json.loads((second / "deployment.json").read_text())["changes"])
        self.assertEqual(b"", (second / "changes.patch").read_bytes())
        for name, content in previous.items():
            self.assertEqual(content, (first / name).read_bytes())

    def test_missing_or_duplicate_rule_anchor_is_rejected_before_writes(self):
        original = self.rule.read_text()
        marker = "\t\tAddEngineThirdPartyPrivateStaticDependencies(Target,"
        for text in (original.replace(marker, "DifferentRule(Target,"),
                     original + marker + '\n"FBX");\n'):
            with self.subTest(text=text):
                self.rule.write_text(text)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "conflicting Worker source anchor"):
                    prepare.prepare(self.ue, self.artifacts)
                self.assertEqual(before, self.snapshot())

    def test_worker_main_anchor_conflict_is_rejected_before_writes(self):
        self.main.write_text(self.main.read_text().replace(
            "\tGEngineLoop.PreInit(ArgC, ArgV);", "\tCustomPreInit();"))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "conflicting Worker source anchor"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_partial_thread_pool_guard_is_rejected_before_writes(self):
        self.impl.write_bytes(prepare.BEGIN.encode() + self.impl.read_bytes())
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "partial WorkerImpl guard"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_unowned_generated_source_conflict_preserves_engine(self):
        self.generated.write_text("local implementation\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "local Worker source changes"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_receipt_does_not_authorize_overwriting_later_local_changes(self):
        prepare.prepare(self.ue, self.artifacts)
        self.generated.write_bytes(self.generated.read_bytes() + b"\n// user change\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "local Worker source changes"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_sdk_source_and_generated_source_symlinks_are_not_followed_for_writes(self):
        for path in (self.rule, self.main, self.impl, self.generated):
            with self.subTest(path=path.name):
                original = path.read_bytes() if path.exists() else None
                if path.exists():
                    path.unlink()
                external = self.root / (path.name + ".external")
                external.write_bytes(original or b"external source")
                path.symlink_to(external)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "symlink"):
                    prepare.prepare(self.ue, self.artifacts)
                self.assertEqual(before, self.snapshot())
                self.assertTrue(path.is_symlink())
                path.unlink()
                if original is not None:
                    path.write_bytes(original)

    def test_engine_local_artifact_root_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside UE"):
            prepare.prepare(self.ue, self.ue / "artifacts")
        self.assertEqual(self.original, self.snapshot())
        self.assertFalse((self.ue / "artifacts").exists())

    def test_opt_in_rules_validate_flags_and_program_linux_arm64_without_engine(self):
        prepare.prepare(self.ue, self.artifacts)
        text = self.rule.read_text()
        for token in ('StaticBackend != "0" && StaticBackend != "1"',
                      'StaticBackend == "1"', "Target.Type != TargetType.Program",
                      "Target.Platform != UnrealTargetPlatform.Linux",
                      "Target.Architecture != UnrealArch.Arm64", "Target.bCompileAgainstEngine"):
            self.assertIn(token, text)
        self.assertLess(text.index("return;"), text.index("AddEngineThirdPartyPrivateStaticDependencies"))
        self.assertIn('"FBX"', text[text.index("AddEngineThirdPartyPrivateStaticDependencies"):])

    def test_cli_requires_parameters_and_reports_missing_source_or_forbidden_root(self):
        cases = [
            ([], 2),
            (["--ue-root", str(self.ue)], 2),
            (["--ue-root", str(self.root / "missing"), "--artifact-root", str(self.artifacts)], 1),
            (["--ue-root", str(self.ue), "--artifact-root", str(self.ue / "artifacts")], 1),
        ]
        for arguments, code in cases:
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, "-B", str(PREPARE_SCRIPT), *arguments],
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(code, result.returncode, result.stderr)
                self.assertTrue(result.stderr)
                self.assertEqual(self.original, self.snapshot())

    def test_cli_success_prints_real_deployment_artifact_not_runtime_pass(self):
        result = subprocess.run(
            [sys.executable, "-B", str(PREPARE_SCRIPT), "--ue-root", str(self.ue),
             "--artifact-root", str(self.artifacts)], capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        directory = Path(result.stdout.strip())
        self.assertTrue(directory.is_relative_to(self.artifacts))
        report = json.loads((directory / "deployment.json").read_text())
        self.assertEqual("worker_source_not_runtime_evidence", report["kind"])
        self.assertNotIn("status", report)


if __name__ == "__main__":
    unittest.main()
