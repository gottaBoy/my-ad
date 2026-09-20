import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("legacy_hierarchy_checker", SCRIPTS / "check_legacy_hierarchy.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
sys.path.pop(0)


class LegacyHierarchyTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "fixture.fbx"
        self.fixture.write_text("fixture identity, not a real native run\n")
        names = list(checker.EXPECTED.elements())
        self.report = {"stage": checker.STAGE, "scope": checker.SCOPE, "status": "PASS", "error": "",
                       "source_sha256": checker.sha256(self.fixture), "checks": names,
                       "self_tests": len(names), "instances": 2, "nodes": []}
        for index, (identity, parent, parent_index, attr, name) in enumerate((
                ("0", "0", -1, "0", ""), ("100", "0", 0, "0", "Root"),
                ("101", "100", 1, "200", "MeshA"), ("102", "100", 1, "201", "MeshB"))):
            self.report["nodes"].append({"id": identity, "parent_id": parent, "attribute_id": attr,
                                         "source_index": index, "parent_index": parent_index,
                                         "name": name, "import": True})

    def test_complete_pure_fixture_is_not_a_product_report(self):
        checker.validate_native(self.report, self.fixture)
        self.assertFalse((self.root / "stage-report.json").exists())
        self.assertIn("not asset import", checker.SCOPE)

    def test_exact_source_stage_scope_and_status_required(self):
        for field, value in (("stage", "ue-ufbx-interchange-static"), ("scope", "Editor PASS"),
                             ("status", "FAIL"), ("error", "failure"), ("source_sha256", "0" * 64)):
            data = copy.deepcopy(self.report)
            data[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                checker.validate_native(data, self.fixture)

    def test_counts_require_all_named_checks_with_exact_multiplicity(self):
        for transform in (lambda names: names[:-1], lambda names: names + [names[0]],
                          lambda names: names[:-1] + [names[0]]):
            data = copy.deepcopy(self.report)
            data["checks"] = transform(data["checks"])
            data["self_tests"] = len(data["checks"])
            with self.assertRaisesRegex(ValueError, "checks"):
                checker.validate_native(data, self.fixture)
        for value in (True, 26.0, "26"):
            data = copy.deepcopy(self.report)
            data["self_tests"] = value
            with self.assertRaises(ValueError):
                checker.validate_native(data, self.fixture)

    def test_same_count_identity_parent_attribute_or_name_mutation_is_rejected(self):
        for field, value in (("id", "999"), ("id", 101), ("parent_id", "0"), ("attribute_id", "201"),
                             ("source_index", 3), ("parent_index", 0), ("parent_index", True),
                             ("name", "MeshB"), ("import", False)):
            data = copy.deepcopy(self.report)
            data["nodes"][2][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                checker.validate_native(data, self.fixture)

    def test_missing_nodes_or_instances_cannot_pass(self):
        for field, value in (("nodes", self.report["nodes"][:-1]), ("instances", True), ("instances", 1)):
            data = copy.deepcopy(self.report)
            data[field] = value
            with self.assertRaises(ValueError):
                checker.validate_native(data, self.fixture)

    def test_cpp_counter_matches_declared_loops(self):
        source = (SCRIPTS / "ue-interchange/Source/CarlaInterchangeProbe/Private/CarlaLegacyHierarchyChecks.cpp").read_text()
        for name in checker.EXPECTED:
            self.assertIn('TEXT("' + name + '")', source)
        self.assertIn("Mode < 6", source)
        self.assertIn("Mode < 11", source)
        self.assertEqual(26, sum(checker.EXPECTED.values()))

    def test_factory_and_native_adapter_call_same_sdk_free_helper(self):
        ue = ROOT / "third_party/unreal-engine/Engine/Source/Editor/UnrealEd"
        helper = (ue / "Public/ImportUtils/SceneImportHierarchy.h").read_text()
        factory = (ue / "Private/Fbx/FbxSceneImportFactory.cpp").read_text()
        adapter = (SCRIPTS / "ue-interchange/Source/CarlaUfbxLegacy/Private/CarlaUfbxLegacyScene.cpp").read_text()
        self.assertNotIn("fbxsdk", helper.lower())
        self.assertNotIn("IsPartOfSkeletonHierarchy(", factory)
        self.assertIn("BuildSceneImportHierarchy(SceneInfo.HierarchyInfo", factory)
        self.assertIn("BuildSceneImportHierarchy(Result.SourceNodes", adapter)
        self.assertIn("Entry.ParentIndex", factory)
        self.assertIn("NodeInfoPtr->bImportNode = Entry.bImportNode", factory)

    def test_both_real_factory_callers_handle_invalid_hierarchy_before_materials(self):
        ue = ROOT / "third_party/unreal-engine/Engine/Source/Editor/UnrealEd/Private/Fbx"
        for name in ("FbxSceneImportFactory.cpp", "ReimportFbxSceneFactory.cpp"):
            text = (ue / name).read_text()
            start = text.index("SceneInfoPtr = ConvertSceneInfo")
            stop = text.index("ExtractMaterialInfo(FbxImporter, SceneInfoPtr)", start)
            boundary = text[start:stop]
            self.assertIn("!SceneInfoPtr.IsValid()", boundary)
            self.assertIn("ReleaseScene()", boundary)
            self.assertIn("EndSlowTask()", boundary)
            self.assertIn("return ", boundary)

    def test_make_profile_does_not_enable_parser_or_editor_sdk_opt_out(self):
        result = subprocess.run(["make", "-n", "carla-legacy-hierarchy"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("CARLA_INTERCHANGE_MODE=legacy", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=4", result.stdout)
        self.assertNotIn("CARLA_ARM64_FBX_HEADERS_ONLY", result.stdout)

    def test_failed_process_is_preserved_and_cannot_publish_pass(self):
        (self.root / "native.json").write_text(json.dumps(self.report))
        (self.root / "native.log").write_text("failed native process")
        result = checker.run(SimpleNamespace(run_dir=self.root, program=self.root / "absent",
            input=self.fixture, ue_root=ROOT / "third_party/unreal-engine",
            ufbx_report=self.root / "absent-report", exit_code=124))
        self.assertEqual(1, result)
        report = json.loads((self.root / "stage-report.json").read_text())
        self.assertEqual("FAIL", report["status"])
        self.assertIn("run.native.log", report["evidence"])
        with self.assertRaisesRegex(ValueError, "overwrite"):
            checker.run(SimpleNamespace(run_dir=self.root))


if __name__ == "__main__":
    unittest.main()
