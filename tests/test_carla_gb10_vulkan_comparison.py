import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "gb10_vulkan_comparison", SCRIPTS / "compare_gb10_vulkan_diagnostics.py")
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def decision(**fields):
    lines = [f"- {key}: {value}" for key, value in fields.items()]
    return "# Town10 GB10 Runtime\n\n" + "\n".join(lines) + "\n"


class ClassifyTest(unittest.TestCase):
    def test_pass_is_a_product_result(self):
        text = decision(Status="PASS", Step="complete", **{"Client abort": "none"})
        self.assertEqual("PASS", comparison.classify(text))

    def test_device_creation_abort_is_blocked_not_a_product_failure(self):
        text = decision(Status="FAIL", Step="startup", **{
            "Client abort": "device-creation", "Server stop code": "1"})
        self.assertEqual("BLOCKED", comparison.classify(text))
        self.assertIn("vkCreateDevice", comparison.blocked_reason(text))

    def test_shader_compiler_crash_is_fail(self):
        text = decision(Status="FAIL", Step="startup", **{
            "Client abort": "shader-compiler-sigsegv", "Server stop code": "139"})
        self.assertEqual("FAIL", comparison.classify(text))
        self.assertEqual("", comparison.blocked_reason(text))

    def test_other_nvidia_driver_crash_is_fail(self):
        text = decision(Status="FAIL", Step="startup", **{
            "Client abort": "nvidia-driver-sigsegv", "Server stop code": "139"})
        self.assertEqual("FAIL", comparison.classify(text))
        self.assertEqual("", comparison.blocked_reason(text))

    def test_preflight_failure_is_blocked(self):
        text = decision(Status="FAIL", Step="preflight", **{"Client abort": "none"})
        self.assertEqual("BLOCKED", comparison.classify(text))
        self.assertIn("preflight", comparison.blocked_reason(text))

    def test_unknown_decision_text_is_not_a_pass(self):
        self.assertEqual("FAIL", comparison.classify(""))

    def test_decision_fields_are_parsed_independently_of_order(self):
        text = decision(**{"Client abort": "device-creation", "Status": "FAIL", "Step": "startup"})
        fields = comparison.parse_decision(text)
        self.assertEqual("device-creation", fields["Client abort"])
        self.assertEqual("startup", fields["Step"])


class SummarizeTest(unittest.TestCase):
    def test_blocked_control_makes_the_comparison_inconclusive(self):
        runs = [
            {"config": "control", "status": "BLOCKED",
             "reason": "vkCreateDevice was rejected; the product shader path was never reached"},
            {"config": "validation", "status": "NOT-RUN"},
        ]
        summary = comparison.summarize(runs)
        self.assertEqual("BLOCKED", summary["status"])
        self.assertIn("vkCreateDevice", summary["reason"])

    def test_control_must_reproduce_the_baseline_crash(self):
        summary = comparison.summarize([{"config": "control", "status": "PASS"}])
        self.assertEqual("BLOCKED", summary["status"])
        self.assertIn("control", summary["reason"])

    def test_control_failure_with_other_results_is_comparable(self):
        runs = [{"config": "control", "status": "FAIL"}, {"config": "validation", "status": "PASS"}]
        self.assertEqual("COMPARABLE", comparison.summarize(runs)["status"])


class WiringTest(unittest.TestCase):
    def test_artifact_path_is_read_from_the_gate_output(self):
        stdout = "PASS town10-gb10-rpc artifacts=/artifacts/carla/run-1 step=complete exit=0\n"
        self.assertEqual("/artifacts/carla/run-1", comparison.parse_artifact_path(stdout))
        self.assertIsNone(comparison.parse_artifact_path("no artifact line"))

    def test_container_paths_map_onto_the_host_artifacts_root(self):
        # Its own tree on purpose: the real artifacts/ root is generated and gitignored, so a
        # fresh clone has none of it and the mapping contract is what matters here.
        with tempfile.TemporaryDirectory() as tmp:
            artifacts_root = Path(tmp)
            (artifacts_root / "carla" / "run-1").mkdir(parents=True)
            self.assertEqual(artifacts_root / "carla",
                             comparison.host_run_dir("/artifacts/carla", artifacts_root))
            self.assertEqual(artifacts_root / "carla" / "run-1",
                             comparison.host_run_dir("/artifacts/carla/run-1", artifacts_root))
            self.assertIsNone(
                comparison.host_run_dir("/opt/other/run-1", artifacts_root))
            self.assertIsNone(
                comparison.host_run_dir("/artifacts/carla/does-not-exist", artifacts_root))

    def test_control_runs_first_so_a_blocked_environment_costs_one_run(self):
        names = [config["name"] for config in comparison.CONFIGS]
        self.assertEqual("control", names[0])
        self.assertTrue({"debug-utils", "validation", "serialize-driver-calls"} <= set(names))

    def test_make_target_and_script_are_declared(self):
        result = subprocess.run(["make", "-n", "carla-gb10-vulkan-comparison"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("compare_gb10_vulkan_diagnostics.py", result.stdout)
        self.assertTrue(os.access(SCRIPTS / "compare_gb10_vulkan_diagnostics.py", os.X_OK))


if __name__ == "__main__":
    unittest.main()
