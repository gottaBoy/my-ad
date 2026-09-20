from pathlib import Path
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class CarlaIspcContractTest(unittest.TestCase):
    def test_build_script_is_native_arm64_and_pinned(self):
        script = (REPO_ROOT / "scripts/carla/build-arm64-ispc.sh").read_text()
        self.assertIn('version="${CARLA_ISPC_VERSION:-1.24.0}"', script)
        self.assertIn('"$(uname -m)" != aarch64', script)
        self.assertIn("-DX86_ENABLED=OFF", script)
        self.assertIn("-DARM_ENABLED=ON", script)
        self.assertIn("-DclangASTMatchersPath", script)
        self.assertIn("libclang-cpp.so.18.1", script)
        self.assertIn("llvmorg-${llvm_source_version}.tar.gz", script)
        self.assertIn("clang/Basic/CharInfo.h", script)
        self.assertIn("DiagnosticCommonKinds.inc", script)
        self.assertIn("--target clangBasic", script)
        self.assertIn("/usr/include/aarch64-linux-gnu", script)
        self.assertIn("--target=neon-i32x4", script)
        self.assertIn("backup=\"${destination}.x86_64\"", script)

    def test_dockerfile_contains_ispc_parser_build_dependencies(self):
        dockerfile = (REPO_ROOT / "images/carla-arm64/Dockerfile").read_text()
        for package in ("bison", "flex", "m4"):
            self.assertIn(f"        {package} \\", dockerfile)

    def test_make_entrypoint_is_declared(self):
        result = subprocess.run(
            ["make", "-n", "carla-ispc", "JOBS=3"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=3", result.stdout)
        self.assertIn("build-arm64-ispc.sh", result.stdout)

        result = subprocess.run(
            ["make", "-n", "carla-ue-build", "JOBS=3"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("build-arm64-carla-ue.sh", result.stdout)

    def test_carla_ue_build_script_requires_arm64_ispc(self):
        script = (REPO_ROOT / "scripts/carla/build-arm64-carla-ue.sh").read_text()
        self.assertIn("CarlaUnreal LinuxArm64 Development", script)
        self.assertIn("-buildscw", script)
        self.assertIn("ARM aarch64", script)
        self.assertIn("CarlaUnreal executable was not produced", script)
        self.assertIn("--target carla-server", script)
        self.assertIn("libc++abi.a", script)


if __name__ == "__main__":
    unittest.main()
