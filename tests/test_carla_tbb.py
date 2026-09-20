from pathlib import Path
import os
import subprocess
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/carla/usd/build-arm64-tbb.sh"
SMOKE = REPO_ROOT / "scripts/carla/usd/tbb-smoke.cpp"
TBB = REPO_ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/Intel/TBB/IntelTBB-2019u8"
UE = REPO_ROOT / "third_party/unreal-engine"


class CarlaTbbTest(unittest.TestCase):
    def test_script_is_bounded_and_does_not_use_upstream_or_clean(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("BuildForLinux.sh", text)
        self.assertNotIn("make clean", text)
        self.assertNotIn("rm -rf", text)
        self.assertNotIn("compiler=gcc", text)
        self.assertNotIn("CPLUS=g++", text)
        self.assertNotIn("libstdc++", text)
        self.assertIn("timeout --kill-after=30s", text)
        self.assertIn("-j", text)
        self.assertIn("${jobs}", text)
        self.assertIn("=~ ^[1-4]$", text)
        self.assertIn("-le 1200", text)

    def test_script_and_smoke_are_valid_source(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        smoke = SMOKE.read_text(encoding="utf-8")
        self.assertIn("tbb::parallel_for", smoke)
        self.assertIn("tbb::parallel_reduce", smoke)
        self.assertIn("tbb::scalable_allocator", smoke)

    def test_real_tbb_rules_are_release_debug_shared_targets(self):
        makefile = (TBB / "Makefile").read_text(encoding="utf-8")
        tbb_rule = (TBB / "build/Makefile.tbb").read_text(encoding="utf-8")
        malloc_rule = (TBB / "build/Makefile.tbbmalloc").read_text(encoding="utf-8")
        self.assertIn("default: tbb tbbmalloc", makefile)
        self.assertIn("cfg=release", makefile)
        self.assertIn("cfg=debug", makefile)
        self.assertIn("LIB_LINK_CMD", tbb_rule)
        self.assertIn("LIB_LINK_CMD", malloc_rule)
        self.assertIn("DYLIB_KEY = -shared", (TBB / "build/linux.clang.inc").read_text(encoding="utf-8"))

    def test_real_arm64_ue_toolchain_and_libcxx_inputs_exist(self):
        triple = "aarch64-unknown-linux-gnueabi"
        roots = list((UE / "Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64").glob("v*_clang-*/" + triple))
        self.assertEqual(1, len(roots))
        self.assertTrue((roots[0] / "bin/clang++").is_file())
        libcxx = UE / "Engine/Source/ThirdParty/Unix/LibCxx"
        self.assertTrue((libcxx / "include/c++/v1/__config").is_file())
        self.assertTrue((libcxx / "lib/Unix" / triple / "libc++.a").is_file())
        self.assertTrue((libcxx / "lib/Unix" / triple / "libc++abi.a").is_file())

    def test_invalid_bounds_fail_before_container_check(self):
        for variable, value in (("CARLA_BUILD_JOBS", "5"), ("CARLA_TBB_TIMEOUT_SECONDS", "60"), ("CARLA_TBB_TIMEOUT_SECONDS", "1201")):
            environment = os.environ.copy()
            environment[variable] = value
            result = subprocess.run(["bash", str(SCRIPT)], env=environment,
                                        capture_output=True, text=True)
            self.assertEqual(64, result.returncode)
            self.assertIn(variable, result.stderr)


if __name__ == "__main__":
    unittest.main()
