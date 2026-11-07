from pathlib import Path
import os
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/probe-gb10-device-fault.sh"


class Gb10DeviceFaultTest(unittest.TestCase):
    def test_invalid_limits_rejected_before_docker(self):
        for key, value in (
            ("CARLA_GB10_FAULT_SECONDS", "301"),
            ("CARLA_GB10_FAULT_SECONDS", "invalid"),
            ("CARLA_GB10_FAULT_PORT", "1023"),
            ("CARLA_GB10_FAULT_PORT", "65533"),
            ("CARLA_GB10_FAULT_CACHE_LIFECYCLE", "invalid"),
        ):
            with self.subTest(key=key, value=value):
                result = subprocess.run(
                    ["bash", str(SCRIPT)],
                    env={**os.environ, key: value},
                    capture_output=True, text=True,
                )
                self.assertEqual(64, result.returncode, result.stderr)

    def test_capture_is_fail_closed_and_default_off(self):
        script = SCRIPT.read_text()
        gdb = (SCRIPT.parent / "dump-ue-vulkan-device-fault.gdb").read_text()
        capture = (SCRIPT.parent / "gdb_vulkan_device_capture.py").read_text()
        self.assertIn("gdb_vulkan_device_capture.start()", gdb)
        self.assertIn("CARLA_GDB_CONTINUE_AFTER_DEVICE", capture)
        self.assertIn("Cpus_allowed_list", capture)
        self.assertIn("tpidr_el0", capture)
        self.assertIn("fault-thread-context.json", capture)
        self.assertNotIn("gdb_vulkan_pipeline_capture", gdb)
        self.assertIn("--signal=INT", script)
        self.assertIn('args[2].startswith("--kill-after=")', script)
        self.assertIn("Baseline binary changed after evidence capture", script)
        self.assertIn("CAPTURED_DRIVER_ENTRY", script)
        self.assertIn("received signal SIGSEGV", script)
        self.assertIn("libnvidia-glvkspirv", script)
        self.assertIn('grep -Fq', script)
        self.assertIn('"-CarlaVulkanCacheLifecycle"', script)
        self.assertIn("CARLA_GB10_FAULT_BINARY", script)
        self.assertIn("CARLA_GB10_FAULT_EXPECTED_BINARY_SHA256", script)
        self.assertIn("binary_override", script)
        self.assertIn('status=CAPTURED_FAULT', script)
        self.assertNotIn("|| true", script)
        self.assertNotIn("status=PASS", script)

    def test_binary_override_requires_explicit_sha(self):
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            env={**os.environ, "CARLA_GB10_FAULT_BINARY": "/tmp/not-present"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode, result.stderr)

    def test_make_target_requires_explicit_inputs(self):
        result = subprocess.run(
            ["make", "carla-gb10-device-fault"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GB10_FAULT_BASELINE is required", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
