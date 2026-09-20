import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/carla/ue-meshbridge/Source"


class MeshBridgeBuildContractTest(unittest.TestCase):
    def test_make_runs_verified_backend_before_native_ue_build(self):
        result = subprocess.run(
            ["make", "-n", "carla-ue-meshbridge", "JOBS=3"], cwd=ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertLess(result.stdout.index("build-arm64-assimp.sh"),
                        result.stdout.index("build-arm64-ue-meshbridge.sh"))
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=3", result.stdout)

    def test_invalid_build_limits_fail_before_source_or_container_changes(self):
        script = ROOT / "scripts/carla/build-arm64-ue-meshbridge.sh"
        for variable in ("CARLA_BUILD_JOBS", "CARLA_MESHBRIDGE_BUILD_TIMEOUT"):
            for value in ("0", "-1", "invalid", "1;exit 0"):
                with self.subTest(variable=variable, value=value):
                    result = subprocess.run(["bash", str(script)],
                                            env={**os.environ, variable: value},
                                            capture_output=True, text=True)
                    self.assertEqual(64, result.returncode)

    def test_library_link_name_and_actual_copy_dependency_are_separate(self):
        rule = (SOURCE / "CarlaAssimpMesh/CarlaAssimpMesh.Build.cs").read_text()
        self.assertIn("File.ResolveLinkTarget(Library, true)", rule)
        self.assertIn("ExternalDependencies.Add(LibraryFile)", rule)
        self.assertIn("PublicAdditionalLibraries.Add(Path.Combine(Root, \"lib\", \"libassimp.so\"))", rule)
        self.assertIn("RuntimeDependencies.Add(\"$(TargetOutputDir)/libassimp.so.6\", LibraryFile)", rule)
        script = (ROOT / "scripts/carla/build-arm64-ue-meshbridge.sh").read_text()
        self.assertIn("run_step library-copy cmp", script)
        self.assertIn("--check \"${run_dir}/source-files.sha256\"", script)

    def test_bridge_keeps_vendor_allocation_and_ue_stage_boundary_explicit(self):
        cpp = (SOURCE / "CarlaAssimpMesh/Private/CarlaAssimpMesh.cpp").read_text()
        for api in ("aiCreatePropertyStore", "aiReleasePropertyStore", "aiImportFileExWithProperties", "aiReleaseImport"):
            self.assertIn(api, cpp)
        self.assertNotIn("Assimp::Importer Importer", cpp)
        self.assertIn("FStaticMeshOperations::ComputeTriangleTangentsAndNormals", cpp)
        self.assertIn("TriangleUVChannels", cpp)
        cmake = (ROOT / "scripts/carla/assimp-probe/CMakeLists.txt").read_text()
        self.assertIn("target_link_options(assimp PRIVATE \"-Wl,-Bsymbolic\")", cmake)
        target = (SOURCE / "CarlaMeshBridge.Target.cs").read_text()
        self.assertIn("Type = TargetType.Program", target)
        self.assertIn("bCompileAgainstEngine = false", target)
        self.assertIn("bCompileAgainstCoreUObject = true", target)
        self.assertNotIn("FORCE_ANSI_ALLOCATOR", target)


if __name__ == "__main__":
    unittest.main()
