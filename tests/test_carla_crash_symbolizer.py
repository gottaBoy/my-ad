import importlib.util
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "crash_symbolizer", SCRIPTS / "symbolize_client_crash.py")
symbolizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(symbolizer)


# Mirrors the real log: an unrelated ensure callstack first, then the crash
# banner, then the stack. UE prints the absolute PC first and a
# module-relative offset in parentheses second.
LOG_WITH_ENSURE_AND_CRASH = """\
[2026.10.01-09.08.53:375][  0]LogOutputDevice: Error: [Callstack] 0x0000000004bd0c64 CarlaUnreal!UnknownFunction(0x49d0c63)
[2026.10.01-09.08.53:375][  0]LogOutputDevice: Error: [Callstack] 0x0000000004bd0c1c CarlaUnreal!UnknownFunction(0x49d0c1b)
[2026.10.01-09.09.08:436][  1]LogCore: === Critical error: ===
Unhandled Exception: SIGSEGV: invalid attempt to access memory at address 0x0000000000000160

[2026.10.01-09.09.08:436][  1]LogCore: 0x0000faf7222414a0 libnvidia-eglcore.so.580.173.02!UnknownFunction(0xc514a0)
0x0000000007db3e7c CarlaUnreal!UnknownFunction(0x7bb3e7b)
0x0000faf7669603c8 libc.so.6!UnknownFunction(0x803c7)
"""


class ExtractionTest(unittest.TestCase):
    def test_only_frames_after_the_crash_marker_are_used(self):
        self.assertEqual([0x7DB3E7C], symbolizer.extract_client_frames(LOG_WITH_ENSURE_AND_CRASH))

    def test_the_parenthesised_offset_is_not_mistaken_for_the_pc(self):
        frames = symbolizer.extract_client_frames(LOG_WITH_ENSURE_AND_CRASH)
        self.assertNotIn(0x7BB3E7B, frames)

    def test_driver_frames_carry_the_module_name(self):
        frames = symbolizer.extract_driver_frames(LOG_WITH_ENSURE_AND_CRASH)
        self.assertEqual([(0xFAF7222414A0, "libnvidia-eglcore.so.580.173.02"),
                          (0xFAF7669603C8, "libc.so.6")], frames)

    def test_a_log_without_a_crash_yields_nothing(self):
        self.assertEqual([], symbolizer.extract_client_frames("no crash here\n"))
        self.assertEqual("", symbolizer.crash_tail("no crash here\n"))

    def test_the_last_crash_banner_wins(self):
        text = "Critical error: first\n0x0000000000000001 CarlaUnreal!x\n" \
               "Critical error: second\n0x0000000000000002 CarlaUnreal!y\n"
        self.assertEqual([2], symbolizer.extract_client_frames(text))


class RenderTest(unittest.TestCase):
    def test_report_lists_driver_frames_before_symbols(self):
        report = symbolizer.render(Path("/artifacts/carla/run-1"), [0x7DB3E7C],
                                   [(0xFAF7222414A0, "libnvidia-eglcore.so.580.173.02")],
                                   ["VulkanRHI::CheckDeviceFault(FVulkanDevice*)",
                                    "/workspace/unreal-engine/x.cpp:1:1"])
        self.assertIn("# GB10 client crash symbolization", report)
        self.assertIn("libnvidia-eglcore.so.580.173.02", report)
        self.assertIn("VulkanRHI::CheckDeviceFault(FVulkanDevice*)", report)
        self.assertLess(report.index("Driver frames"), report.index("Symbolized CarlaUnreal frames"))

    def test_empty_stacks_are_reported_as_none(self):
        report = symbolizer.render(Path("/artifacts/carla/run-1"), [], [], [])
        self.assertEqual(2, report.count("- none"))


VALIDATION_LOG = """\
[2026.10.01-12.16.36:663][  0]LogVulkanRHI: Warning: CARLA diagnostic: validation call site for VUID-vkCmdDrawIndexed-None-02699
[2026.10.01-12.16.36:665][  0]LogCore: 0x0000000007e46f4f Dladdr: 0.45ms
[2026.10.01-12.16.36:666][  0]LogCore: 0x0000000006caa3ab Dladdr: 0.46ms
--
[2026.10.01-12.16.36:672][  0]LogVulkanRHI: Warning: CARLA diagnostic: validation call site for VUID-vkCmdDraw-None-02699
[2026.10.01-12.16.36:672][  0]LogCore: 0x0000000007f5e9bb Dladdr: 0.44ms
--
"""


class ValidationCallSiteTest(unittest.TestCase):
    def test_stacks_are_grouped_with_their_message_id(self):
        stacks = symbolizer.extract_validation_stacks(VALIDATION_LOG)
        self.assertEqual(2, len(stacks))
        self.assertEqual("VUID-vkCmdDrawIndexed-None-02699", stacks[0]["vuid"])
        self.assertEqual([0x7E46F4F, 0x6CAA3AB], stacks[0]["addresses"])
        self.assertEqual("VUID-vkCmdDraw-None-02699", stacks[1]["vuid"])

    def test_a_log_without_captures_yields_nothing(self):
        self.assertEqual([], symbolizer.extract_validation_stacks("nothing here\n"))

    def test_validation_report_lists_message_ids_and_first_call_site(self):
        report = symbolizer.render_validation(
            Path("/artifacts/carla/run-1"),
            symbolizer.extract_validation_stacks(VALIDATION_LOG),
            ["FVulkanCommandListContext::RHIDrawIndexedPrimitive(...)"],
            {"VUID-vkCmdDrawIndexed-None-02699": 5574, "VUID-vkCmdDraw-None-02699": 149})
        self.assertIn("VUID-vkCmdDrawIndexed-None-02699: 5574", report)
        self.assertIn("RHIDrawIndexedPrimitive", report)
        self.assertIn("Captured stack dumps: 2", report)


class WiringTest(unittest.TestCase):
    def test_make_target_requires_a_run_directory(self):
        # -n only prints the recipe, so assert the guard itself is present.
        result = subprocess.run(["make", "-n", "carla-symbolize-client-crash"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("RUN_DIR is required", result.stdout)

    def test_make_target_invokes_the_symbolizer(self):
        result = subprocess.run(["make", "-n", "carla-symbolize-client-crash",
                                 "RUN_DIR=/artifacts/carla/run-1",
                                 "MODE=validation-call-site"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("symbolize_client_crash.py", result.stdout)
        self.assertIn('CARLA_CRASH_RUN_DIR="/artifacts/carla/run-1"', result.stdout)
        self.assertIn("--mode validation-call-site", result.stdout)
        self.assertTrue(os.access(SCRIPTS / "symbolize_client_crash.py", os.X_OK))


if __name__ == "__main__":
    unittest.main()
