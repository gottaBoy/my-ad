import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/carla/inventory_fbx_dependencies.py"
spec = importlib.util.spec_from_file_location("inventory_fbx_dependencies", SCRIPT)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)

SDK = "Engine/Source/ThirdParty/FBX/2020.2/include"
EDITOR = "Engine/Source/Editor/UnrealEd"
PARSER = "Engine/Plugins/Interchange/Runtime/Source/Parsers/Fbx"


class FbxInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for relative in inventory.CPP_ROOTS:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self.write(f"{SDK}/fbxsdk.h", '#include "fbxsdk/types.h"\n')
        self.write(
            f"{SDK}/fbxsdk/types.h",
            "// class FbxCommentOnly;\n"
            "class FBXSDK_DLL FbxNode {};\n"
            "struct FbxMesh;\n"
            "typedef double FbxDouble;\n"
            "typedef FbxArray<FbxNode*> FbxNodeArray;\n"
            "using FbxAlias = FbxNode;\n"
            "enum class EFbxType { Value };\n"
            "FbxNode* FbxCreateNode();\n",
        )
        self.write(
            f"{EDITOR}/UnrealEd.Build.cs",
            'AddEngineThirdPartyPrivateStaticDependencies(Target,\n    "FBX", "FreeType2");\n',
        )

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def report(self):
        return inventory.inventory(self.root)

    def entry(self, report, relative):
        return next(entry for entry in report["files"] if entry["path"] == relative)

    def test_comments_strings_and_raw_examples_are_not_cpp_references(self):
        path = f"{EDITOR}/Private/CommentCases.cpp"
        self.write(
            path,
            "// FbxMesh *comment;\n"
            "/* #include <fbxsdk.h>\n FbxMesh *block; */\n"
            'const char* a = "FbxMesh // not code";\n'
            'const char* b = R"sample(\n#include <fbxsdk.h>\nFbxMesh /* not code */\n)sample";\n'
            "auto c = '\\'';\n"
            "FbxNode* real; // FbxMesh\n",
        )
        entry = self.entry(self.report(), path)
        self.assertEqual([], entry["sdk_includes"])
        self.assertEqual(
            [{"name": "FbxNode", "evidence": [
                {"line": 10, "columns": [1], "source": "FbxNode* real; // FbxMesh"},
            ]}],
            entry["sdk_type_name_references"],
        )
        self.assertEqual([], entry["other_fbx_identifiers"])

    def test_continued_comment_and_comment_markers_inside_strings(self):
        tokens = inventory.tokenize(
            "// ignored \\\nFbxMesh *still_ignored;\n"
            'auto url = "https://example.invalid/*"; FbxNode* actual;\n'
            "/* comment */ FbxDouble value;\n"
        )
        refs = [token for token in tokens if token.kind == "identifier" and token.text.startswith("Fbx")]
        self.assertEqual([("FbxNode", 3), ("FbxDouble", 4)], [(token.text, token.line) for token in refs])

    def test_sdk_types_come_from_declarations_not_prefixes_or_function_names(self):
        path = f"{EDITOR}/Private/Types.cpp"
        self.write(
            path,
            "FbxNode* n; FbxNode* other;\n"
            "FbxDouble scalar; FbxNodeArray nodes; FbxAlias alias; EFbxType type;\n"
            "int FbxCounter; FbxCreateNode(); FbxCommentOnly();\n",
        )
        report = self.report()
        entry = self.entry(report, path)
        self.assertEqual(
            ["EFbxType", "FbxAlias", "FbxDouble", "FbxNode", "FbxNodeArray"],
            [item["name"] for item in entry["sdk_type_name_references"]],
        )
        node = next(item for item in entry["sdk_type_name_references"] if item["name"] == "FbxNode")
        self.assertEqual([1, 13], node["evidence"][0]["columns"])
        self.assertEqual(
            ["FbxCommentOnly", "FbxCounter", "FbxCreateNode"],
            [item["name"] for item in entry["other_fbx_identifiers"]],
        )
        node_declaration = next(item for item in report["sdk"]["referenced_type_declarations"] if item["name"] == "FbxNode")
        self.assertEqual(f"{SDK}/fbxsdk/types.h", node_declaration["evidence"][0]["path"])
        self.assertEqual(2, node_declaration["evidence"][0]["line"])

    def test_unreal_wrappers_with_fbx_prefix_are_distinct_from_sdk_types(self):
        self.write(
            f"{EDITOR}/Public/FbxImporter.h",
            "#include <fbxsdk.h>\n"
            "class UNREALED_API FFbxImporter;\n"
            "struct FbxSceneInfo {};\n"
            "class UFbxFactory;\n",
        )
        path = f"{EDITOR}/Private/Use.cpp"
        self.write(
            path,
            '#include "FbxImporter.h"\n'
            "UnFbx::FFbxImporter* importer;\n"
            "FbxSceneInfo info; UFbxFactory* factory; FbxNode* node;\n",
        )
        entry = self.entry(self.report(), path)
        self.assertEqual(["FbxNode"], [item["name"] for item in entry["sdk_type_name_references"]])
        self.assertEqual(
            ["FFbxImporter", "FbxSceneInfo", "UFbxFactory"],
            [item["name"] for item in entry["unreal_wrapper_type_name_references"]],
        )
        self.assertEqual([], entry["sdk_includes"])
        self.assertEqual(
            [f"{EDITOR}/Public/FbxImporter.h"],
            entry["unreal_wrapper_includes"][0]["header_candidates"],
        )

    def test_literal_include_evidence_and_missing_headers(self):
        path = f"{PARSER}/Private/FbxInclude.h"
        self.write(
            path,
            '/* #include "fbxsdk.h" */\n'
            '# /* between */ include "fbxsdk.h"\n'
            "#include <fbxsdk/types.h>\n"
            "#include <fbxsdk/missing.h>\n"
            '#include "NoLocalFbxWrapper.h"\n'
            'const char* example = "#include <fbxsdk.h>";\n',
        )
        entry = self.entry(self.report(), path)
        self.assertEqual(
            [(2, "fbxsdk.h"), (3, "fbxsdk/types.h"), (4, "fbxsdk/missing.h")],
            [(item["line"], item["include"]) for item in entry["sdk_includes"]],
        )
        self.assertEqual([], entry["sdk_includes"][2]["header_candidates"])
        self.assertEqual([], entry["sdk_type_name_references"])
        self.assertEqual(["NoLocalFbxWrapper.h"], [item["include"] for item in entry["other_fbx_includes"]])

    def test_build_dependencies_follow_call_structure_not_comments_or_messages(self):
        dependencies, mentions = inventory.scan_build(
            '// PrivateDependencyModuleNames.Add("FBX");\n'
            '/* AddEngineThirdPartyPrivateStaticDependencies(Target, "FBX"); */\n'
            'Log.TraceInformation("FBX");\n'
            'string example = @"PrivateDependencyModuleNames.Add(""FBX"")";\n'
            "PrivateDependencyModuleNames.AddRange(new string[] {\n"
            '    "Core", "InterchangeFbxParser", /* "FBX" */\n'
            '    "FBX"\n'
            "});\n"
            'AddEngineThirdPartyPrivateStaticDependencies(Target, new[] { "FBX" });\n'
            'PrivateDependencyModuleNames.Add("FBX" + "Extra");\n'
            'PrivateDependencyModuleNames.Add(Select("FBX"));\n'
            'PrivateDependencyModuleNames.Add(@"FBX");\n'
            'PublicDefinitions.Add("FBXSDK_SHARED");\n',
        )
        self.assertEqual(
            [
                ("InterchangeFbxParser", "fbx_named_module", 6, "PrivateDependencyModuleNames.AddRange"),
                ("FBX", "autodesk_sdk_module", 7, "PrivateDependencyModuleNames.AddRange"),
                ("FBX", "autodesk_sdk_module", 9, "AddEngineThirdPartyPrivateStaticDependencies"),
                ("FBX", "autodesk_sdk_module", 12, "PrivateDependencyModuleNames.Add"),
            ],
            [(item["dependency"], item["kind"], item["line"], item["operation"]) for item in dependencies],
        )
        self.assertEqual(5, dependencies[0]["call_line"])
        self.assertNotIn(1, [item["line"] for item in mentions])
        self.assertNotIn(2, [item["line"] for item in mentions])
        self.assertIn(13, [item["line"] for item in mentions])

    def test_build_rules_outside_cpp_scope_are_included_with_module_ownership(self):
        self.write(f"{EDITOR}/Public/FbxImporter.h", "FbxNode* n;\n")
        self.write(
            "Engine/Plugins/Animation/ControlRig/Source/ControlRigEditor/ControlRigEditor.Build.cs",
            'AddEngineThirdPartyPrivateStaticDependencies(Target, "FBX");\n',
        )
        self.write(
            "Engine/Source/Programs/InterchangeWorker/InterchangeWorker.Build.cs",
            'PrivateDependencyModuleNames.Add("InterchangeFbxParser");\n'
            'AddEngineThirdPartyPrivateStaticDependencies(Target, "FBX");\n',
        )
        report = self.report()
        self.assertEqual(
            ["ControlRigEditor", "UnrealEd", "InterchangeWorker"],
            [item["module"] for item in report["build_dependencies"] if item["kind"] == "autodesk_sdk_module"],
        )
        self.assertEqual(
            [f"{EDITOR}/UnrealEd.Build.cs"],
            self.entry(report, f"{EDITOR}/Public/FbxImporter.h")["module_build_rules"],
        )
        self.assertEqual("lexical_references_not_a_semantic_call_graph", report["analysis"])

    def test_preprocessor_branches_are_not_silently_evaluated(self):
        path = f"{EDITOR}/Private/Conditional.cpp"
        self.write(path, "#if 0\nFbxNode* node;\n#else\nFbxMesh* mesh;\n#endif\n")
        entry = self.entry(self.report(), path)
        self.assertEqual(["FbxMesh", "FbxNode"], [item["name"] for item in entry["sdk_type_name_references"]])

    def test_same_spelled_variables_and_macro_aliases_are_not_semantic_claims(self):
        self.write(
            f"{SDK}/fbxsdk/aliases.h",
            "class FbxImporter;\n"
            "#define EFbxRotationOrder FbxEuler::EOrder\n",
        )
        path = f"{EDITOR}/Public/Wrapper.h"
        self.write(
            path,
            "class FFbxImporter;\n"
            "void Inspect(FFbxImporter* FbxImporter);\n"
            "EFbxRotationOrder order;\n",
        )
        entry = self.entry(self.report(), path)
        self.assertEqual(["FbxImporter"], [item["name"] for item in entry["sdk_type_name_references"]])
        self.assertEqual(["FFbxImporter"], [item["name"] for item in entry["unreal_wrapper_type_name_references"]])
        self.assertEqual(["EFbxRotationOrder"], [item["name"] for item in entry["other_fbx_identifiers"]])
        self.assertEqual(2, entry["sdk_type_name_references"][0]["evidence"][0]["line"])

    def test_ordering_content_digest_and_generated_directory_exclusion(self):
        self.write(f"{EDITOR}/Private/Z.cpp", "FbxNode* z;\nFbxMesh* a;\n")
        self.write(f"{EDITOR}/Private/A.cpp", "FbxMesh* m; FbxNode* n;\n")
        before = self.report()
        self.write(f"{EDITOR}/Intermediate/Generated.cpp", "FbxNode* generated;\n")
        self.write(f"{EDITOR}/Intermediate/Generated.Build.cs", 'PrivateDependencyModuleNames.Add("FBX");\n')
        after = self.report()
        self.assertEqual(before, after)
        self.assertEqual(sorted(item["path"] for item in after["files"]), [item["path"] for item in after["files"]])
        self.assertEqual(
            ["FbxMesh", "FbxNode"],
            [item["name"] for item in after["files"][0]["sdk_type_name_references"]],
        )
        self.write(f"{EDITOR}/Private/Z.cpp", "FbxNode* renamed;\nFbxMesh* a;\n")
        self.assertNotEqual(before["scope"]["input_sha256"], self.report()["scope"]["input_sha256"])

    def test_output_is_independent_of_root_path_and_file_creation_order(self):
        self.write(f"{EDITOR}/Private/Z.cpp", "FbxNode* z;\n")
        self.write(f"{EDITOR}/Private/A.cpp", "FbxNode* a;\n")
        with tempfile.TemporaryDirectory() as directory:
            other = Path(directory)
            for relative in reversed(inventory.CPP_ROOTS):
                (other / relative).mkdir(parents=True, exist_ok=True)
            for path in sorted(self.root.rglob("*"), reverse=True):
                if path.is_file():
                    target = other / path.relative_to(self.root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
            self.assertEqual(self.report(), inventory.inventory(other))

    def test_cli_is_deterministic_and_does_not_write_into_source_tree(self):
        self.write(f"{EDITOR}/Private/Use.cpp", "FbxNode* n;\n")
        command = [sys.executable, str(SCRIPT), "--ue-root", str(self.root)]
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        first = subprocess.run(command, capture_output=True, check=True)
        second = subprocess.run(command, capture_output=True, check=True)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(b"", first.stderr)
        self.assertEqual(before, after)
        self.assertEqual(self.report(), json.loads(first.stdout))
        self.assertNotIn(str(self.root).encode(), first.stdout)

    def test_missing_roots_or_sdk_are_explicit_errors_not_empty_success(self):
        (self.root / "Engine/Source/Developer").rmdir()
        with self.assertRaisesRegex(ValueError, "Required source directory"):
            self.report()
        (self.root / "Engine/Source/Developer").mkdir()
        (self.root / SDK / "fbxsdk.h").unlink()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--ue-root", str(self.root)], capture_output=True, text=True,
        )
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("Expected one local FBX SDK include tree", result.stderr)

    def test_multiple_sdk_versions_require_explicit_selection(self):
        self.write("Engine/Source/ThirdParty/FBX/other/include/fbxsdk.h", "class FbxOther;\n")
        with self.assertRaisesRegex(ValueError, "select it with --sdk-include"):
            self.report()
        self.assertEqual(SDK, inventory.inventory(self.root, SDK)["sdk"]["include_root"])


if __name__ == "__main__":
    unittest.main()
