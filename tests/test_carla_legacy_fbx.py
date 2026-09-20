import copy
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/probe_legacy_fbx.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("legacy_fbx_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
sys.path.pop(0)


class LegacyFbxProbeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "CarlaUnreal.uproject"
        self.graph = {
            "Name": "CarlaUnrealEditor", "Configuration": "Development", "Platform": "Linux",
            "ProjectFile": str(self.project),
            "Binaries": [{"Modules": ["FBX", "UnrealEd", "MovieSceneTools", "IncludeOnly"]}],
            "Modules": {},
        }
        for name in ("FBX", "UnrealEd", "MovieSceneTools", "IncludeOnly", "Unselected"):
            self.graph["Modules"][name] = {
                "Rules": f"/{name}.Build.cs", "PublicDependencyModules": [],
                "PrivateDependencyModules": [], "PublicIncludePathModules": [],
                "PublicLibraries": [], "PublicDefinitions": [],
            }
        self.graph["Modules"]["FBX"]["PublicDefinitions"] = ["FBXSDK_SHARED"]
        self.graph["Modules"]["UnrealEd"]["PublicDependencyModules"] = ["FBX"]
        self.graph["Modules"]["MovieSceneTools"]["PrivateDependencyModules"] = ["FBX"]
        self.graph["Modules"]["Unselected"]["PrivateDependencyModules"] = ["FBX"]
        self.graph["Modules"]["IncludeOnly"]["PublicIncludePathModules"] = ["FBX"]

    def actions(self):
        cwd = self.root / "Engine/Source"
        cwd.mkdir(parents=True, exist_ok=True)
        actions = []
        for name, source in probe.UNITS.items():
            output = self.root / "Engine/Intermediate/SingleFile" / (name + ".o")
            output.parent.mkdir(parents=True, exist_ok=True)
            response = output.with_suffix(".o.rsp")
            response.write_text(f'-c -target aarch64-unknown-linux-gnueabi "{self.root / source}" -o "{output}"')
            actions.append({"Type": "Compile", "StatusDescription": name,
                            "WorkingDirectory": str(cwd), "CommandArguments": f'@"{response}"'})
        return {"Actions": actions}

    def test_graph_uses_evaluated_selected_dependencies_not_lexical_or_include_edges(self):
        result = probe.target_summary(self.graph, self.project)
        self.assertEqual(["MovieSceneTools", "UnrealEd"],
                         [entry["module"] for entry in result["direct_fbx_consumers"]])
        self.assertEqual("NOT_RUN", result["editor_acceptance"])
        self.assertEqual(4, result["selected_module_count"])
        self.assertIn("no Editor link", result["scope"])

    def test_graph_rejects_unexpected_target_or_missing_selected_modules(self):
        for field, value in (("Name", "CarlaUnreal"), ("Platform", "Win64"),
                             ("Configuration", "Debug"), ("ProjectFile", "other.uproject")):
            data = copy.deepcopy(self.graph)
            data[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "identity"):
                probe.target_summary(data, self.project)
        self.graph["Binaries"][0]["Modules"].remove("MovieSceneTools")
        with self.assertRaisesRegex(ValueError, "not selected"):
            probe.target_summary(self.graph, self.project)

    def test_missing_module_or_sdk_library_is_not_headers_only_evidence(self):
        self.graph["Modules"]["FBX"]["PublicLibraries"] = ["/x86/libfbxsdk.so"]
        with self.assertRaisesRegex(ValueError, "headers-only"):
            probe.target_summary(self.graph, self.project)
        del self.graph["Modules"]["FBX"]
        with self.assertRaisesRegex(ValueError, "missing selected"):
            probe.target_summary(self.graph, self.project)

    def test_action_plan_requires_exact_requested_native_objects(self):
        units, retained = probe.compile_plan(self.actions(), self.root)
        self.assertEqual(set(probe.UNITS), set(units))
        self.assertEqual(len(probe.UNITS), len(retained))
        self.assertTrue(all(path.suffix == ".o" for path in units.values()))

    def test_real_ubt_repeated_target_in_shared_response_is_allowed_only_if_identical(self):
        data = self.actions()
        response = self.root / "Engine/Intermediate/SingleFile/FbxStaticMeshImport.cpp.o.rsp"
        shared = self.root / "Engine/Source/shared.rsp"
        response.write_text(response.read_text() + " @shared.rsp")
        shared.write_text("-c -target aarch64-unknown-linux-gnueabi")
        units, retained = probe.compile_plan(data, self.root)
        self.assertEqual(set(probe.UNITS), set(units))
        self.assertIn(shared, retained)
        for target in ("-target x86_64-unknown-linux-gnu", "--target=x86_64-unknown-linux-gnu",
                       "-target", "--target=", "-target -c"):
            shared.write_text(target)
            with self.subTest(target=target), self.assertRaises(ValueError):
                probe.compile_plan(data, self.root)

    def test_action_plan_rejects_missing_extra_duplicate_and_link_actions(self):
        original = self.actions()
        for actions in ([], original["Actions"][:1], original["Actions"] * 2,
                        [original["Actions"][0]] * 2):
            with self.subTest(actions=len(actions)), self.assertRaises(ValueError):
                probe.compile_plan({"Actions": actions}, self.root)
        original["Actions"][0]["Type"] = "Link"
        with self.assertRaisesRegex(ValueError, "unexpected"):
            probe.compile_plan(original, self.root)

    def test_action_plan_rejects_wrong_architecture_source_syntax_only_and_output(self):
        for old, new in (("aarch64-unknown-linux-gnueabi", "x86_64-unknown-linux-gnu"),
                         ("-c ", "-c -fsyntax-only "),
                         (probe.UNITS["FbxStaticMeshImport.cpp"], "other.cpp"),
                         ("Engine/Intermediate/SingleFile", "Engine/Source/SingleFile")):
            data = self.actions()
            response = next(self.root.glob("Engine/Intermediate/SingleFile/FbxStaticMeshImport.cpp.o.rsp"))
            response.write_text(response.read_text().replace(old, new))
            with self.subTest(change=new), self.assertRaises(ValueError):
                probe.compile_plan(data, self.root)

    def test_response_files_are_recursively_retained_and_cycles_rejected(self):
        first, second = self.root / "first.rsp", self.root / "second.rsp"
        first.write_text('@"second.rsp" -c')
        second.write_text("-target aarch64-unknown-linux-gnueabi")
        retained = set()
        self.assertEqual(["-target", "aarch64-unknown-linux-gnueabi", "-c"],
                         probe.response_arguments(first, self.root, retained))
        self.assertEqual({first, second}, retained)
        second.write_text("@first.rsp")
        with self.assertRaisesRegex(ValueError, "cyclic"):
            probe.response_arguments(first, self.root, set())

    def test_objects_must_be_fresh_arm64_relocatables(self):
        path = self.root / "unit.o"
        header = bytearray(20)
        header[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<HH", header, 16, 1, 183)
        started = time.time_ns()
        path.write_bytes(header)
        probe.check_object(path, started)
        with self.assertRaisesRegex(ValueError, "stale"):
            probe.check_object(path, path.stat().st_mtime_ns + 1)
        for kind, machine in ((1, 62), (3, 183), (2, 183)):
            struct.pack_into("<HH", header, 16, kind, machine)
            path.write_bytes(header)
            with self.assertRaisesRegex(ValueError, "relocatable"):
                probe.check_object(path, 0)

    def test_scalar_smoke_uses_actual_ubt_headers_without_rewriting_editor_outputs(self):
        action = self.actions()["Actions"][0]
        action["CommandPath"] = "/usr/bin/clang++-18"
        command = probe.smoke_compile_command(action, self.root / "smoke.cpp", self.root / "smoke.o")
        self.assertIn("-fsanitize=undefined", command)
        self.assertIn("-fno-sanitize-recover=undefined", command)
        self.assertEqual(1, command.count("-o"))
        self.assertIn(str(self.root / "smoke.cpp"), command)
        self.assertNotIn(str(self.root / probe.UNITS["FbxStaticMeshImport.cpp"]), command)
        self.assertEqual(str(self.root / "smoke.o"), command[command.index("-o") + 1])
        self.assertEqual(["aarch64-unknown-linux-gnueabi"], probe.target_arguments(command))

    def test_prebuilt_core_must_be_a_real_native_shared_library(self):
        path = self.root / "core.so"
        header = bytearray(20)
        header[:6] = b"\x7fELF\x02\x01"
        for kind, machine in ((3, 183), (1, 183), (3, 62)):
            struct.pack_into("<HH", header, 16, kind, machine)
            path.write_bytes(header)
            if (kind, machine) == (3, 183):
                probe.check_shared_library(path)
            else:
                with self.assertRaisesRegex(ValueError, "shared library"):
                    probe.check_shared_library(path)

    def test_staticmesh_diagnostic_uses_opt_in_scalar_target_without_changing_default(self):
        target = (ROOT / "scripts/carla/ue-staticmesh/Source/CarlaStaticMeshProbe.Target.cs").read_text()
        runner = (ROOT / "scripts/carla/probe-arm64-staticmesh.sh").read_text()
        linux = (ROOT / "third_party/unreal-engine/Engine/Source/Programs/UnrealBuildTool/Platform/Linux/UEBuildLinux.cs").read_text()
        self.assertIn("bCompileISPC = false", target)
        self.assertIn("export CARLA_DISABLE_ISPC=1", runner)
        self.assertIn('Target.bCompileISPC = DisableIspc != "1"', linux)
        self.assertIn('CARLA_STATICMESH_PROBE=1', target)
        self.assertIn('CARLA_DISABLE_ISPC must be 0 or 1', linux)
        self.assertIn('Type = TargetType.Program', target)
        build = (ROOT / "scripts/carla/ue-staticmesh/Source/CarlaStaticMeshProbe/CarlaStaticMeshProbe.Build.cs").read_text()
        self.assertIn('"AutomationController"', build)
        self.assertIn('"AutomationWorker"', build)

    def test_symbol_inventory_counts_only_observed_undefined_sdk_names(self):
        text = "U fbxsdk::FbxNode::Create(char const*)\nU UnrealFunction()\nT fbxsdk::Inline()\n"
        self.assertEqual(["fbxsdk::FbxNode::Create(char const*)"], probe.fbx_symbols(text + text))
        with self.assertRaisesRegex(ValueError, "no observed"):
            probe.fbx_symbols("T fbxsdk::Inline()\nU UnrealFunction()")

    def test_sdk_implementations_and_sdk_typed_ue_functions_are_not_conflated(self):
        values = ["fbxsdk::FbxNode::Create()", "UnFbx::FFbxImporter::Import(fbxsdk::FbxNode*)",
                  "typeinfo for fbxsdk::FbxObject"]
        result = probe.classify_symbols(values)
        self.assertEqual([values[0], values[2]], result["sdk"])
        self.assertEqual([values[1]], result["sdk_typed_ue"])

    def test_scene_node_migration_keeps_existing_conversion_and_no_sdk_data_members(self):
        ue = ROOT / "third_party/unreal-engine"
        header = (ue / probe.METADATA_HEADERS[0]).read_text()
        self.assertNotIn("fbxsdk", header.lower())
        for value in ("FTransform Transform", "FVector RotationPivot", "FVector ScalePivot"):
            self.assertIn(value, header)
        importer = (ue / probe.METADATA_HEADERS[1]).read_text()
        self.assertIn("using FbxNodeInfo = UE::Import::FSceneNodeInfo", importer)
        producer = (ue / probe.UNITS["FbxMainImport.cpp"]).read_text()
        consumer = (ue / probe.UNITS["FbxSceneImportFactory.cpp"]).read_text()
        self.assertIn("FFbxDataConverter::ConvertTransform(RootNode->EvaluateGlobalTransform())", producer)
        self.assertIn("FFbxDataConverter::ConvertPos(ChildNode->RotationPivot.Get())", producer)
        self.assertIn("FFbxDataConverter::ConvertTransform(RealFbxNode->EvaluateLocalTransform())", consumer)
        self.assertIn("NodeInfoPtr->Transform = NodeInfo.Transform", consumer)
        self.assertNotIn("NodeInfo.Transform.GetT()", consumer)
        self.assertNotIn("ConvertPos(NodeInfo.RotationPivot)", consumer)

    def test_cli_rejects_invalid_limits_before_running(self):
        for option in ("--jobs", "--timeout"):
            for value in ("0", "-1", "abc", "1s", "1;exit 0"):
                result = subprocess.run([sys.executable, str(SCRIPT), "--sdk-root", "/absent",
                                         option, value], capture_output=True, text=True)
                self.assertEqual(2, result.returncode)
                self.assertIn("positive integer", result.stderr)

    def test_make_entrypoint_requires_sdk_and_keeps_bounded_defaults(self):
        result = subprocess.run(["make", "-n", "carla-legacy-fbx", "NATIVE_SDK_ROOT=/native/sdk"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn('--jobs "4" --timeout "600"', result.stdout)
        self.assertIn("probe_legacy_fbx.py", result.stdout)
        result = subprocess.run(["make", "carla-legacy-fbx", "NATIVE_SDK_ROOT="], cwd=ROOT,
                                capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("NATIVE_SDK_ROOT is required", result.stdout)


if __name__ == "__main__":
    unittest.main()
