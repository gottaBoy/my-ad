import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("staticmesh_checker", SCRIPTS / "check_staticmesh.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
sys.path.pop(0)


class StaticMeshTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "fixture.fbx"
        self.fixture.write_text("evaluator fixture only")
        self.native = {"stage": checker.STAGE, "scope": checker.SCOPE, "status": "PASS", "error": "",
                       "source_sha256": checker.sha256(self.fixture), "instances": 2,
                       "built_objects": 2, "rejected_queries": 2}
        self.graph = {"Name": "CarlaStaticMeshProbe", "Platform": "Linux", "Configuration": "Development",
                      "Modules": {name: {} for name in ("Core", "CoreUObject", "Engine", "CarlaUfbxMesh", "CarlaUfbxLegacy")}}
        self.graph["Modules"]["IntelISPC"] = {"PublicDefinitions": ["INTEL_ISPC=0"]}

    def test_pure_fixture_is_scoped_and_does_not_write_stage_evidence(self):
        checker.validate_graph(self.graph)
        checker.validate_native(self.native, self.fixture)
        self.assertFalse((self.root / "stage-report.json").exists())
        self.assertIn("not saved assets", checker.SCOPE)

    def test_graph_requires_engine_and_no_editor_or_sdk(self):
        for name in ("Engine", "CarlaUfbxMesh", "CarlaUfbxLegacy"):
            data = copy.deepcopy(self.graph)
            del data["Modules"][name]
            with self.assertRaises(ValueError):
                checker.validate_graph(data)
        for name in ("FBX", "UnrealEd", "InterchangeFbxParser"):
            data = copy.deepcopy(self.graph)
            data["Modules"][name] = {}
            with self.assertRaises(ValueError):
                checker.validate_graph(data)

    def test_graph_requires_correct_target_and_explicit_scalar_profile(self):
        for field, value in (("Name", "CarlaUnreal"), ("Platform", "Win64"), ("Configuration", "Shipping")):
            data = copy.deepcopy(self.graph)
            data[field] = value
            with self.assertRaises(ValueError):
                checker.validate_graph(data)
        self.graph["Modules"]["IntelISPC"]["PublicDefinitions"] = ["INTEL_ISPC=1"]
        with self.assertRaises(ValueError):
            checker.validate_graph(self.graph)

    def test_native_identity_scope_source_and_integer_counts_are_required(self):
        for field, value in (("status", "FAIL"), ("stage", "Editor"), ("scope", "saved asset"),
                             ("source_sha256", "0" * 64), ("error", "bad"),
                             ("built_objects", 0), ("instances", True), ("rejected_queries", 2.0)):
            data = copy.deepcopy(self.native)
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                checker.validate_native(data, self.fixture)

    def test_failed_process_keeps_report_and_is_never_pass(self):
        (self.root / "target.json").write_text(json.dumps(self.graph))
        (self.root / "native.json").write_text(json.dumps(self.native))
        (self.root / "native.log").write_text("crash after writing a native report")
        args = SimpleNamespace(run_dir=self.root, input=self.fixture, program=self.root / "absent",
                               ue_root=self.root / "ue", ufbx_report=self.root / "absent-report", exit_code=139)
        self.assertEqual(1, checker.run(args))
        data = json.loads((self.root / "stage-report.json").read_text())
        self.assertEqual("FAIL", data["status"])
        self.assertIn("run.native.log", data["evidence"])
        with self.assertRaisesRegex(ValueError, "overwrite"):
            checker.run(args)

    def test_runner_rejects_invalid_bounds_before_creating_output(self):
        for variable in ("CARLA_BUILD_JOBS", "CARLA_STATICMESH_BUILD_TIMEOUT"):
            for value in ("0", "-1", "abc", "1;exit 0"):
                env = {**os.environ, variable: value, "CARLA_ARTIFACT_DIR": str(self.root / "out")}
                result = subprocess.run(["bash", str(SCRIPTS / "probe-arm64-staticmesh.sh")],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(64, result.returncode)
                self.assertFalse((self.root / "out").exists())

    def test_program_uses_real_engine_objects_not_package_acceptance(self):
        source = (SCRIPTS / "ue-staticmesh/Source/CarlaStaticMeshProbe/Private/CarlaStaticMeshProbe.cpp").read_text()
        self.assertIn("NewObject<UStaticMesh>", source)
        self.assertIn("BuildFromMeshDescriptions", source)
        self.assertIn("bCommitMeshDescription = false", source)
        self.assertIn("WITH_ENGINE && !WITH_EDITOR && WITH_EDITORONLY_DATA", source)
        self.assertIn("RF_Transient", source)
        self.assertIn("CheckBuffers", source)
        self.assertNotIn("SavePackage(", source)

    def test_runner_stages_engine_layout_and_evaluates_native_process_result(self):
        runner = (SCRIPTS / "probe-arm64-staticmesh.sh").read_text()
        for value in ('layout="${run}/layout"', 'staged_project="${layout}/CarlaStaticMesh"',
                      'cp --reflink=auto', 'staged_binary="${staged_bin}/CarlaStaticMeshProbe"',
                      '"-abslog=${run}/unreal.log" "-project=${staged_project}/CarlaStaticMeshProbe.uproject"',
                      'processes.json',
                      'check_staticmesh.py', 'step=validation', '-ForceRulesCompile'):
            self.assertIn(value, runner)
        self.assertIn('"${layout}/Engine/${directory}"', runner)
        self.assertIn('"${ue}/Engine/Binaries/Linux/CarlaStaticMeshProbe.target"', runner)

    def test_checker_records_runtime_layout_and_requires_process_statuses(self):
        source = (SCRIPTS / "check_staticmesh.py").read_text()
        self.assertIn('read_json(root / "processes.json")', source)
        self.assertIn('"graph", "build", "native"', source)
        self.assertIn('"layout.txt"', source)

    def test_scalar_patch_preserves_unsigned_shift_and_const_unaligned_load_semantics(self):
        header = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/Core/Public/Math/UnrealMathFPU.h").read_text()
        for name in ("VectorShiftLeftImmTemplate", "VectorShiftRightImmLogicalTemplate"):
            block = header[header.index("FORCEINLINE VectorRegister4Int " + name):]
            block = block[:block.index("#define")]
            self.assertIn("if constexpr (ImmAmt >= 32)", block)
            self.assertIn("MakeVectorRegisterInt(0, 0, 0, 0)", block)
            self.assertEqual(4, block.count("static_cast<uint32>"))
        for name in ("VectorLoadURGBA16N", "VectorLoadSRGBA16N"):
            block = header[header.index("FORCEINLINE VectorRegister4Float " + name):]
            block = block[:block.index("\n}")]
            self.assertIn("const void* Ptr", block)
            self.assertIn("FMemory::Memcpy(E, Ptr, sizeof(E))", block)

    def test_ubt_patch_has_one_default_preserving_opt_in_assignment(self):
        source = (ROOT / "third_party/unreal-engine/Engine/Source/Programs/UnrealBuildTool/Platform/Linux/UEBuildLinux.cs").read_text()
        self.assertEqual(1, source.count('GetEnvironmentVariable("CARLA_DISABLE_ISPC")'))
        self.assertEqual(1, source.count('Target.bCompileISPC = DisableIspc != "1"'))
        self.assertIn('DisableIspc != "0" && DisableIspc != "1"', source)


if __name__ == "__main__":
    unittest.main()
