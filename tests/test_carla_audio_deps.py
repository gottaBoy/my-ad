import os
from pathlib import Path
import subprocess
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/carla/build-arm64-audio-deps.sh"


class CarlaAudioDepsTest(unittest.TestCase):
    def test_build_uses_in_tree_sources_and_ue_sysroot(self):
        script = SCRIPT.read_text()
        for expected in (
            "Ogg/libogg-1.2.2", "libOpus/opus-1.1", "--sysroot=${sysroot}",
            "ue-arm64-third-party.cmake", "CMAKE_POSITION_INDEPENDENT_CODE=ON",
            "make -s -B -f Makefile.unix", "diff --binary HEAD",
        ):
            self.assertIn(expected, script)
        self.assertNotIn("curl", script)
        self.assertNotIn("apt-get", script)
        self.assertNotIn("FIXED_POINT=1", script)

    def test_requires_real_pic_linkage_and_deployed_codec_smoke(self):
        script = SCRIPT.read_text()
        for expected in (
            "llvm-readelf", "AArch64", "-shared -Wl,-z,defs -Wl,--whole-archive",
            "-Wl,--no-whole-archive", "run_step smoke", "run_step deployed-smoke",
            "ldd -r", "installed.sha256",
        ):
            self.assertIn(expected, script)
        self.assertLess(script.index("run_step smoke env"), script.index("run_step ogg-install"))
        self.assertIn("libogg_fPIC.a", script)
        self.assertIn("libopus_fPIC.a", script)

    def test_codec_smoke_validates_data_not_only_return_codes(self):
        smoke = (REPO_ROOT / "scripts/carla/audio-smoke.c").read_text()
        for expected in (
            "ogg_stream_packetin", "ogg_sync_pageout", "ogg_stream_packetout",
            "memcmp(output.packet, payload", "opus_encode", "opus_decode",
            "samples != FRAME_SIZE", "energy < 1000000", "isfinite(energy)",
        ):
            self.assertIn(expected, smoke)

    def test_invalid_jobs_are_rejected_before_running(self):
        for jobs in ("0", "-2", "eight", "2;exit 0"):
            with self.subTest(jobs=jobs):
                result = subprocess.run(
                    ["bash", str(SCRIPT)], env={**os.environ, "CARLA_BUILD_JOBS": jobs},
                    capture_output=True, text=True,
                )
                self.assertEqual(64, result.returncode)
                self.assertIn("CARLA_BUILD_JOBS must be positive", result.stderr)

    def test_make_uses_native_build_container(self):
        result = subprocess.run(
            ["make", "-n", "carla-audio-deps", "JOBS=3"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=3", result.stdout)
        self.assertIn("build-arm64-audio-deps.sh", result.stdout)


if __name__ == "__main__":
    unittest.main()
