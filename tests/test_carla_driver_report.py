import importlib.util
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "driver_report", SCRIPTS / "collect_gb10_driver_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


SYMBOLIZED = """\
# GB10 client crash symbolization

- Run directory: /artifacts/carla/run-1
- CarlaUnreal frames: 2

## Driver frames (crash site)

- 0xfbebeb75d610 libnvidia-glvkspirv.so.580.173.02
- 0xfbebeb75ebf0 libnvidia-glvkspirv.so.580.173.02

## Symbolized CarlaUnreal frames (outermost last)

- FVulkanPipelineStateCacheManager::CreateComputePipelineFromShader(...)
- FCompilePipelineStateTask::CompilePSO(...)
"""

DECISION = """\
# Town10 GB10 Runtime

- Status: FAIL
- Step: startup
- Exit code: 3
- Mode: rpc
- Render profile: default
- Server stop code: 139
- Vulkan validation: 0
- Vulkan debug utils: 0
- Client abort: shader-compiler-sigsegv
"""

VULKANINFO = """\
        deviceName         = NVIDIA GB10
        driverID           = DRIVER_ID_NVIDIA_PROPRIETARY
        driverInfo         = 580.173.02
"""


class ParseTest(unittest.TestCase):
    def test_symbolized_report_splits_driver_and_ue_frames(self):
        parsed = report.parse_symbolized_report(SYMBOLIZED)
        self.assertEqual(2, len(parsed["driver"]))
        self.assertIn("libnvidia-glvkspirv.so.580.173.02", parsed["driver"][0])
        self.assertEqual(2, len(parsed["unreal"]))
        self.assertIn("CreateComputePipelineFromShader", parsed["unreal"][0])

    def test_empty_sections_are_not_reported_as_frames(self):
        parsed = report.parse_symbolized_report("## Driver frames\n\n- none\n")
        self.assertEqual([], parsed["driver"])
        self.assertEqual([], parsed["unreal"])

    def test_decision_fields_are_parsed(self):
        fields = report.parse_decision_fields(DECISION)
        self.assertEqual("FAIL", fields["Status"])
        self.assertEqual("shader-compiler-sigsegv", fields["Client abort"])
        self.assertEqual("139", fields["Server stop code"])

    def test_environment_comes_from_vulkaninfo(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "vulkaninfo.log").write_text(VULKANINFO, encoding="utf-8")
            environment = report.environment_from([run_dir])
        self.assertEqual("NVIDIA GB10", environment["device"])
        self.assertEqual("580.173.02", environment["driver_info"])


class CollectTest(unittest.TestCase):
    def test_collect_run_reads_decision_and_vuid_groups(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "decision.md").write_text(DECISION, encoding="utf-8")
            (run_dir / "symbolized-crash.txt").write_text(SYMBOLIZED, encoding="utf-8")
            (run_dir / "client-ue.log").write_text(
                "VUID-vkCmdDraw-None-02699 x\nwith error VK_ERROR_DEVICE_LOST\n", encoding="utf-8")
            record = report.collect_run(run_dir)
        self.assertEqual("shader-compiler-sigsegv", record["client_abort"])
        self.assertTrue(record["device_lost"])
        self.assertEqual(["VUID-vkCmdDraw-None-02699"], record["vuid_groups"])
        self.assertEqual(2, len(record["crash"]["unreal"]))

    def test_missing_evidence_is_listed_not_silently_dropped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            record = report.collect_run(Path(tmp))
        report_doc = {"environment": {"driver_info": None}, "runs": [record]}
        missing = report.missing_evidence(report_doc)
        self.assertIn("driver version was not found in any vulkaninfo.log", missing)
        self.assertTrue(any("symbolize" in item for item in missing))
        self.assertTrue(any("passing reference run" in item for item in missing))

    def test_render_includes_runs_crash_stacks_and_exclusions(self):
        document = {
            "generated_utc": "2026-10-01T00:00:00Z",
            "environment": {"device": "NVIDIA GB10", "driver_info": "580.173.02"},
            "client_binary_sha256": "abc",
            "runs": [{"run": "run-1", "status": "FAIL", "client_abort": "shader-compiler-sigsegv",
                      "server_stop_code": "139",
                      "crash": {"driver": ["0x1 libnvidia-glvkspirv.so.580.173.02"],
                                "unreal": ["CreateComputePipelineFromShader"]},
                      "vuid_groups": [], "device_lost": False}],
            "negative_results": list(report.NEGATIVE_RESULTS),
            "missing": [],
        }
        text = report.render(document)
        self.assertIn("NVIDIA GB10", text)
        self.assertIn("libnvidia-glvkspirv.so.580.173.02", text)
        self.assertIn("CreateComputePipelineFromShader", text)
        self.assertIn("Already excluded", text)
        self.assertIn("Missing evidence", text)


class WiringTest(unittest.TestCase):
    def test_make_target_uses_the_generator(self):
        result = subprocess.run(["make", "-n", "carla-gb10-driver-report",
                                 "RUN_DIRS=artifacts/carla/a artifacts/carla/b"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("collect_gb10_driver_report.py", result.stdout)
        self.assertIn("--run-dir artifacts/carla/a", result.stdout)
        self.assertIn("--run-dir artifacts/carla/b", result.stdout)


if __name__ == "__main__":
    unittest.main()
