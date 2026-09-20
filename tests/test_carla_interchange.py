import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts/carla"
RUNNER = SCRIPT_DIR / "probe-arm64-interchange-nodes.sh"
CHECKER_SCRIPT = SCRIPT_DIR / "check_ue_interchange.py"
STAGE_REPORT_SCRIPT = SCRIPT_DIR / "stage_report.py"
PROBE_SOURCE = SCRIPT_DIR / "ue-interchange/Source/CarlaInterchangeProbe/Private/CarlaInterchangeProbe.cpp"
PAYLOAD_CHECKS = PROBE_SOURCE.with_name("CarlaInterchangePayloadChecks.cpp")
PAYLOAD_API = SCRIPT_DIR / "ue-meshbridge/Source/CarlaUfbxMesh/Private/CarlaUfbxPayload.cpp"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stage_report = load_module("carla_interchange_stage_report", STAGE_REPORT_SCRIPT)
sys.modules["stage_report"] = stage_report
checker = load_module("carla_interchange_checker", CHECKER_SCRIPT)

STAGE_ID = "ue-ufbx-interchange-static"
SCOPE = ("ufbx to UE Interchange static nodes and mesh payloads; not translator/worker, "
         "material shading, factory assets, Editor or Cook")
UFBX_STAGE_ID = "ufbx-fbx-static-backend"
UFBX_SCOPE = "ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC"


class InterchangeFixtureTest(unittest.TestCase):
    """Pure report/checker fixtures; no UE, Docker, GPU, or fake hardware PASS."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run_dir = self.root / "interchange-run"
        self.run_dir.mkdir()
        self.input_file = self.root / "fixture.fbx"
        self.input_file.write_bytes(b"project-owned fixture bytes\n")
        self.program = self.root / "CarlaInterchangeProbe"
        self.program.write_bytes(b"not an executable; failure fixture only\n")
        self.ufbx_evidence = self.root / "ufbx-evidence.log"
        self.ufbx_evidence.write_text("fixture prerequisite evidence\n", encoding="utf-8")
        self.ufbx_report = self.root / "ufbx-stage-report.json"
        self.write_ufbx_report()

    def write_ufbx_report(self, **overrides):
        values = {
            "stage_id": UFBX_STAGE_ID,
            "scope": UFBX_SCOPE,
            "exit_code": 0,
            "required_checks": ["backend"],
            "checks": {"backend": "PASS"},
            "evidence": {"backend": self.ufbx_evidence},
            "sources": {"ufbx": {"location": "/artifacts/carla/sources/ufbx-v0.23.0",
                                  "revision": "f" * 40}},
            "command": ["build-arm64-ufbx.sh"],
        }
        values.update(overrides)
        return stage_report.write_report(self.ufbx_report, **values)

    def run_checker(self, exit_code=1, **overrides):
        values = {
            "program": self.program,
            "input": self.input_file,
            "run_dir": self.run_dir,
            "ue_root": "/workspace/unreal-engine",
            "ufbx_report": self.ufbx_report,
            "runner": RUNNER,
            "timeout": 1,
            "exit_code": exit_code,
        }
        values.update(overrides)
        return checker.run(SimpleNamespace(**values))

    def report(self):
        return json.loads((self.run_dir / "stage-report.json").read_text(encoding="utf-8"))

    def test_checker_failure_writes_fail_report_and_keeps_prerequisite_hashed(self):
        result = self.run_checker(exit_code=137)
        report = self.report()
        self.assertEqual(1, result)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(STAGE_ID, report["stage_id"])
        self.assertEqual(SCOPE, report["scope"])
        self.assertEqual(1, report["exit_code"])
        self.assertEqual(list(checker.CHECKS), report["required_checks"])
        self.assertEqual("FAIL", report["checks"]["process"])
        self.assertEqual("FAIL", report["checks"]["architecture"])
        diagnostic = self.run_dir / report["evidence"]["process"]["path"]
        diagnostic_data = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertIn("scene process exit code: 137", " ".join(diagnostic_data["errors"]))
        self.assertEqual(UFBX_STAGE_ID, report["prerequisites"][0]["stage_id"])
        self.assertEqual(hashlib.sha256(self.ufbx_report.read_bytes()).hexdigest(),
                         report["prerequisites"][0]["sha256"])
        with self.assertRaises(stage_report.ReportError):
            stage_report.validate_report(self.run_dir / "stage-report.json",
                                         stage_id=STAGE_ID, scope=SCOPE)

    def test_failed_upstream_report_is_a_gate_and_is_not_promoted(self):
        self.write_ufbx_report(exit_code=23, checks={"backend": "FAIL"})
        result = self.run_checker(exit_code=0)
        report = self.report()
        self.assertEqual(1, result)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["build"])
        self.assertEqual([], report["prerequisites"])
        self.assertEqual("FAIL", json.loads(self.ufbx_report.read_text(encoding="utf-8"))["status"])
        with self.assertRaises(stage_report.ReportError):
            stage_report.validate_report(self.ufbx_report, stage_id=UFBX_STAGE_ID, scope=UFBX_SCOPE)

    def test_stage_identity_scope_and_evidence_hash_are_exact(self):
        report = self.write_ufbx_report()
        self.assertEqual(UFBX_STAGE_ID, report["stage_id"])
        self.assertEqual(UFBX_SCOPE, report["scope"])
        self.assertEqual(report, stage_report.validate_report(self.ufbx_report,
                         stage_id=UFBX_STAGE_ID, scope=UFBX_SCOPE))
        with self.assertRaisesRegex(stage_report.ReportError, "stage_id"):
            stage_report.validate_report(self.ufbx_report, stage_id="ue-editor-cook", scope=UFBX_SCOPE)
        with self.assertRaisesRegex(stage_report.ReportError, "scope"):
            stage_report.validate_report(self.ufbx_report, stage_id=UFBX_STAGE_ID, scope="Editor/Cook")
        self.ufbx_evidence.write_text("changed after report\n", encoding="utf-8")
        with self.assertRaisesRegex(stage_report.ReportError, "SHA256 mismatch"):
            stage_report.validate_report(self.ufbx_report, stage_id=UFBX_STAGE_ID, scope=UFBX_SCOPE)

    def test_checker_rejects_non_native_program_without_pass(self):
        self.run_dir.joinpath("build.log").write_text("fixture build log\n", encoding="utf-8")
        result = self.run_checker(exit_code=0)
        report = self.report()
        self.assertEqual(1, result)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual("FAIL", report["checks"]["architecture"])
        self.assertNotIn("scene-report", report["evidence"])
        self.assertFalse((self.run_dir / "outputs").exists())

    def test_native_elf_helper_is_strict_but_does_not_create_hardware_pass(self):
        arm64 = bytearray(20)
        arm64[:6] = b"\x7fELF\x02\x01"
        arm64[18:20] = b"\xb7\x00"
        x86 = bytearray(arm64)
        x86[18:20] = b"\x3e\x00"
        arm64_path, x86_path, short_path = (
            self.root / name for name in ("arm64-header", "x86-header", "short")
        )
        arm64_path.write_bytes(arm64)
        x86_path.write_bytes(x86)
        short_path.write_bytes(arm64[:19])
        self.assertTrue(checker.native_elf(arm64_path))
        self.assertFalse(checker.native_elf(x86_path))
        self.assertFalse(checker.native_elf(short_path))
        self.run_checker(exit_code=1)
        self.assertEqual("FAIL", self.report()["status"])


class InterchangeContractTest(unittest.TestCase):
    def test_make_entry_orders_prerequisites_and_uses_build_profile(self):
        result = subprocess.run(["make", "-n", "carla-interchange", "JOBS=4"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        output = result.stdout
        assimp = output.index("build-arm64-assimp.sh")
        ufbx = output.index("build-arm64-ufbx.sh")
        interchange = output.index("probe-arm64-interchange-nodes.sh")
        self.assertLess(assimp, ufbx)
        self.assertLess(ufbx, interchange)
        self.assertIn("--profile build run --rm -T", output)
        self.assertIn("-e CARLA_BUILD_JOBS=4", output)
        self.assertNotIn("carla-vulkan", output)

    def test_compose_and_runner_are_dgx_native_arm64_only(self):
        compose = (ROOT / "compose.carla-arm64.yaml").read_text(encoding="utf-8")
        runner = RUNNER.read_text(encoding="utf-8")
        for text in (compose, runner):
            self.assertNotIn("linux/amd64", text)
        self.assertIn("platform: linux/arm64", compose)
        self.assertIn('profiles: ["build"]', compose)
        self.assertIn('if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]', runner)
        self.assertIn("CarlaInterchangeProbe Linux Development -architecture=arm64", runner)
        self.assertIn('export CARLA_UFBX_INSTALL="${artifact_dir}/ufbx-arm64/0.23.0/install"', runner)
        self.assertIn('export CARLA_ASSIMP_INSTALL="${artifact_dir}/assimp-arm64/6.0.5/install"', runner)

    def test_runner_uses_real_multi_mesh_fixture_and_records_fixture_hash(self):
        runner = RUNNER.read_text(encoding="utf-8")
        fixture = 'scene_fixture="${script_dir}/ufbx-probe/fixtures/multi-mesh.fbx"'
        fixture_path = ROOT / "scripts/carla/ufbx-probe/fixtures/multi-mesh.fbx"
        content = fixture_path.read_text(encoding="utf-8")
        self.assertIn(fixture, runner)
        self.assertTrue(fixture_path.is_file())
        self.assertEqual(2, content.count("\n    Geometry: "))
        self.assertEqual(3, content.count("\n    Model: "))
        self.assertIn('C: "OO", 200, 101', content)
        self.assertIn('C: "OO", 201, 102', content)
        self.assertIn('sha256sum "${scene_fixture}" > "${run_dir}/scene-fixture.sha256"', runner)
        self.assertIn('"-input=${scene_fixture}"', runner)
        self.assertIn('--input "${scene_fixture}"', runner)
        self.assertNotIn("BlenderCube.fbx", runner)
        static_process = runner[runner.index('"-output=${run_dir}/scene.json"'):]
        self.assertNotIn("hierarchy-geometry.fbx", static_process)
        legacy_hash = runner.index('"${script_dir}/ufbx-probe/fixtures/hierarchy-geometry.fbx"')
        legacy_start = runner.rfind('if [[ "${mode}" == legacy ]]; then', 0, legacy_hash)
        self.assertGreaterEqual(legacy_start, 0)
        self.assertLess(legacy_hash, runner.index("\nfi", legacy_start))
        fixture_declaration = runner.index(fixture)
        fixture_hash = runner.index('scene-fixture.sha256')
        scene_process = runner.index('"-output=${run_dir}/scene.json"')
        checker_input = runner.index('--input "${scene_fixture}"', scene_process)
        self.assertLess(fixture_declaration, fixture_hash)
        self.assertLess(fixture_hash, scene_process)
        self.assertLess(scene_process, checker_input)
        self.assertEqual(64, hashlib.sha256(fixture_path.read_bytes()).hexdigest().__len__())

    def test_runner_has_source_hash_gate_before_and_after_scene_process(self):
        runner = RUNNER.read_text(encoding="utf-8")
        first = runner.index("sha256sum --check")
        scene = runner.index('"-output=${run_dir}/scene.json"')
        second = runner.index("sha256sum --check", scene)
        checker_call = runner.index('python3 "${script_dir}/check_ue_interchange.py"')
        self.assertLess(first, scene)
        self.assertLess(scene, second)
        self.assertLess(second, checker_call)
        self.assertIn('if [[ "${process_code}" == 0 ]]; then', runner)
        self.assertIn("process_code=125", runner)
        self.assertIn("source-files.sha256", runner)

    def test_stage_id_scope_and_boundary_are_not_editor_or_cook_claims(self):
        self.assertEqual(STAGE_ID, checker.STAGE_ID)
        self.assertEqual(SCOPE, checker.SCOPE)
        for excluded in ("translator/worker", "material shading", "factory assets", "Editor", "Cook"):
            self.assertIn(excluded, checker.SCOPE)
        self.assertEqual(UFBX_SCOPE, checker.UFBX_SCOPE)
        runner = RUNNER.read_text(encoding="utf-8")
        self.assertIn("not FBX translation or Cook", runner)
        self.assertIn("factory assets, Editor or Cook", runner)

    def test_checker_required_checks_and_scene_contract_are_explicit(self):
        checker_text = CHECKER_SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(("build", "architecture", "process", "graph", "payload", "geometry"), checker.CHECKS)
        for field in ('"nodes", "scene_nodes", "mesh_nodes", "material_nodes"',
                      '"serialized_bytes", "payload_vertices", "payload_triangles",',
                      '"payload_materials"', "validate_payloads(data, output_dir)",
                      'data.get("source_sha256") != sha256(fixture)',
                      'data.get("graph_roundtrip")', 'data.get("payload_roundtrip")',
                      "count != 3 * mesh_count", "path.is_symlink()"):
            self.assertIn(field, checker_text)

    def test_cpp_uses_real_interchange_graph_and_static_mesh_payload_protocol(self):
        source = PROBE_SOURCE.read_text(encoding="utf-8")
        source += (SCRIPT_DIR / "ue-interchange/Source/CarlaUfbxInterchange/Private/CarlaUfbxInterchangeGraph.cpp").read_text(encoding="utf-8")
        for token in ("UInterchangeBaseNodeContainer", "UInterchangeSceneNode", "UInterchangeMeshNode",
                      "SerializeNodeContainerData", "Container->SaveToFile(GraphPath)",
                      "Loaded->LoadFromFile(GraphPath)", "EInterchangeMeshPayLoadType::STATIC"):
            self.assertIn(token, source)
        source = PAYLOAD_CHECKS.read_text(encoding="utf-8")
        for token in ("CarlaUfbxMesh::FetchStaticPayload", "Mesh.Serialize(Writer)", "bool bSkinned = false", "Writer << bSkinned",
                      "Loaded.Serialize(Reader)", "bool bLoadedSkinned = true", "Reader << bLoadedSkinned",
                      "Reader.Tell() != Data.Num()", "bLoadedSkinned", 'TEXT(".payload")',
                      "Payload.RequestUid", "MeshBytes(Loaded) != MeshBytes(Payload.Mesh)"):
            self.assertIn(token, source)
        self.assertLess(source.index("Mesh.Serialize(Writer)"), source.index("Writer << bSkinned"))
        self.assertLess(source.index("Writer << bSkinned"), source.index('TEXT(".payload")'))
        self.assertLess(source.index("Loaded.Serialize(Reader)"), source.index("Reader << bLoadedSkinned"))
        self.assertLess(source.index("Reader << bLoadedSkinned"), source.index("|| bLoadedSkinned"))


    def test_scene_test_runs_payload_roundtrip_for_every_mesh(self):
        source = PROBE_SOURCE.read_text(encoding="utf-8")
        scene_test = PAYLOAD_CHECKS.read_text(encoding="utf-8")
        self.assertIn(
            "for (const CarlaUfbxMesh::FSceneMesh& SourceMesh : Scene.Meshes)",
            scene_test,
        )
        self.assertIn("WriteAndReadPayload(Payload, ResultDir, PayloadBytes, Error)", scene_test)
        self.assertIn('TEXT("payload_count")', source)
        self.assertIn("PayloadCount = PayloadRecords.Num()", source)
        self.assertIn("RunSceneSelfTests(SceneTests, Error)", source)
        self.assertIn("MeshBytes(SourceMesh.Mesh) != Original", scene_test)

    def test_native_payload_api_binds_keys_transforms_and_atomic_output(self):
        source = PAYLOAD_API.read_text(encoding="utf-8")
        for token in ("Ambiguous payload key", "Unknown payload key", "Scene.SourceSha256",
                      "IsNormalized", "IsFinite", "ContainsNaN", "Matrix.M[Row][Column]",
                      "FStaticMeshOperations::ApplyTransform(Result.Mesh, Matrix, true)",
                      "Output = MoveTemp(Result)"):
            self.assertIn(token, source)

    def test_cpp_scene_report_identity_is_distinct_from_bootstrap_identity(self):
        source = PROBE_SOURCE.read_text(encoding="utf-8")
        self.assertIn('TEXT("ue-ufbx-interchange-static")', source)
        self.assertIn('TEXT("interchange-node-bootstrap")', source)
        self.assertIn("const bool bScene = FParse::Param", source)
        self.assertIn('FParse::Value(FCommandLine::Get(), TEXT("input="), Input)', source)
        self.assertIn('FParse::Value(FCommandLine::Get(), TEXT("result-dir="), ResultDir)', source)
        self.assertIn("return Success ? 0 : 2;", source)


if __name__ == "__main__":
    unittest.main()
