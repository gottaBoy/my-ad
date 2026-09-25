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
spec = importlib.util.spec_from_file_location("asset_checker", SCRIPTS / "check_carla_asset.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
sys.path.pop(0)


class AssetRoundtripTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "fixture.fbx"
        self.fixture.write_text("asset fixture identity only")
        self.native = {"stage": checker.STAGE, "scope": checker.SCOPE, "status": "PASS", "error": "",
                       "source_sha256": checker.sha256(self.fixture), "instances": 2,
                       "saved_assets": 2, "reloaded_assets": 2, "rebuilt_objects": 2}
        self.graph = {"Name": "CarlaAssetProbe", "Platform": "Linux", "Configuration": "Development",
                      "Modules": {name: {} for name in (
                          "Core", "CoreUObject", "Engine", "CarlaUfbxMesh", "CarlaUfbxLegacy")}}
        self.graph["Modules"]["IntelISPC"] = {"PublicDefinitions": ["INTEL_ISPC=0"]}

    def test_fixture_is_scoped_and_does_not_create_stage_evidence(self):
        checker.validate_graph(self.graph)
        checker.validate_native(self.native, self.fixture)
        self.assertFalse((self.root / "stage-report.json").exists())
        self.assertIn("not Editor reimport", checker.SCOPE)

    def test_graph_requires_engine_without_editor_or_sdk(self):
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

    def test_native_requires_exact_identity_and_all_integer_counts(self):
        for field, value in (("status", "FAIL"), ("stage", "Editor"), ("scope", "Cook"),
                             ("source_sha256", "0" * 64), ("error", "failed"),
                             ("instances", True), ("saved_assets", 1),
                             ("reloaded_assets", 2.0), ("rebuilt_objects", "2")):
            data = copy.deepcopy(self.native)
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                checker.validate_native(data, self.fixture)

    def test_failed_process_stays_fail_closed_and_report_is_immutable(self):
        (self.root / "target.json").write_text(json.dumps(self.graph))
        (self.root / "native.json").write_text(json.dumps(self.native))
        (self.root / "native.log").write_text("crash after native report")
        args = SimpleNamespace(run_dir=self.root, input=self.fixture, program=self.root / "absent",
                               ue_root=self.root / "ue", ufbx_report=self.root / "absent-report",
                               exit_code=139)
        self.assertEqual(1, checker.run(args))
        report = json.loads((self.root / "stage-report.json").read_text())
        self.assertEqual("FAIL", report["status"])
        self.assertIn("run.native.log", report["evidence"])
        with self.assertRaisesRegex(ValueError, "overwrite"):
            checker.run(args)

    def test_runner_rejects_invalid_bounds_before_creating_output(self):
        for variable in ("CARLA_BUILD_JOBS", "CARLA_ASSET_BUILD_TIMEOUT"):
            for value in ("0", "-1", "abc", "1;exit 0"):
                env = {**os.environ, variable: value, "CARLA_ARTIFACT_DIR": str(self.root / "out")}
                result = subprocess.run(["bash", str(SCRIPTS / "probe-arm64-asset.sh")],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(64, result.returncode)
                self.assertFalse((self.root / "out").exists())

    def test_program_saves_reloads_and_rebuilds_real_static_meshes(self):
        source = (SCRIPTS / "ue-asset/Source/CarlaAssetProbe/Private/CarlaAssetProbe.cpp").read_text()
        for value in ("NewObject<UStaticMesh>", "BuildFromMeshDescriptions", "UPackage::SavePackage",
                      "LoadPackage(nullptr", "CollectGarbage", "LoadedPackage->GetGuid"):
            self.assertIn(value, source)
        self.assertIn("WITH_ENGINE && !WITH_EDITOR && WITH_EDITORONLY_DATA", source)
        self.assertIn("not Editor reimport, Cook or GPU rendering", source)
        self.assertNotIn("RF_Transient", source)

    def test_instanced_placement_editor_apis_stay_out_of_editoronly_program(self):
        header = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/Engine/Public/Instances/InstancedPlacementClientInfo.h").read_text()
        implementation = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/Engine/Private/Instances/InstancedPlacementClientInfo.cpp").read_text()
        self.assertIn("ENGINE_API void PostSerialize(FArchive& Ar, AInstancedPlacementPartitionActor* InParentPartitionActor);", header)
        self.assertIn("\n#if WITH_EDITOR\nbool FClientPlacementInfo::Initialize", implementation)
        self.assertIn("\n#if WITH_EDITOR\nTArray<FSMInstanceId> FClientPlacementInfo::AddInstances", implementation)
        self.assertIn("\n#endif\n\nvoid FClientPlacementInfo::PostLoad", implementation)
        self.assertIn("\n#endif\n\nFText FClientPlacementInfo::GetISMPartitionInstanceDisplayName", implementation)

    def test_make_and_runner_use_bounded_native_profile(self):
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("-e CARLA_BUILD_JOBS=", makefile)
        self.assertIn("-e CARLA_ASSET_BUILD_TIMEOUT=", makefile)
        self.assertIn("bash /opt/my-ad/scripts/carla/probe-arm64-asset.sh", makefile)
        runner = (SCRIPTS / "probe-arm64-asset.sh").read_text()
        for value in ("CARLA_DISABLE_ISPC=1", "CARLA_ARM64_FBX_HEADERS_ONLY=0",
                      '"-content-root=${run}/assets"', "check_carla_asset.py",
                      "CarlaAssetProbe.target", "-ForceRulesCompile"):
            self.assertIn(value, runner)

    def test_runner_disables_pch_and_tracks_material_boundary_patch(self):
        build_configuration = (SCRIPTS / "ue-asset/BuildConfiguration.xml").read_text()
        runner = (SCRIPTS / "probe-arm64-asset.sh").read_text()
        self.assertIn("<bUsePCHFiles>false</bUsePCHFiles>", build_configuration)
        self.assertIn('material_patch="${scripts}/patches/material-editor-boundary.patch"', runner)
        self.assertIn('apply_patch_idempotent "${material_patch}"', runner)
        self.assertIn('printf "%s already applied: %s\\n" "${label}" "${file}"', runner)
        self.assertIn('"${animation_patch}" "${material_patch}"', runner)


if __name__ == "__main__":
    unittest.main()
