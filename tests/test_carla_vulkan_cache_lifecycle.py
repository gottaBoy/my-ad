import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "cache_lifecycle_test", SCRIPTS / "analyze_vulkan_cache_lifecycle.py")
lifecycle = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    spec.loader.exec_module(lifecycle)


def event(number, operation, phase, thread, cache, other="0x0", result=0, size=0):
    return (f"event={number} operation={operation} phase={phase} thread={thread} "
            f"cache={cache} other={other} result={result} bytes={size} end=1\n")


class CacheLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.marker = self.root / "driver-compute-42-7-main.entry.txt"
        self.marker.touch()
        self.driver = {
            "status": "CAPTURED_DRIVER_ENTRY",
            "unreturned": [{"kind": "compute", "marker": self.marker.name}],
        }
        self.details = {"cache": "0x42", "thread": "7"}

    def run_report(self, lines):
        (self.root / "cache-lifecycle.txt").write_text("".join(lines))
        with patch.object(lifecycle, "analyze_entries", return_value=self.driver), \
             patch.object(lifecycle, "fields", return_value=self.details):
            return lifecycle.analyze(self.root)

    def test_live_fault_cache_and_overlapping_query_are_reported(self):
        report = self.run_report([
            event(1, "create", "enter", 7, "0x0"),
            event(2, "create", "return", 7, "0x42"),
            event(3, "compute", "enter", 7, "0x42"),
            event(4, "get_data", "enter", 8, "0x42"),
            event(5, "get_data", "return", 8, "0x42"),
        ])
        self.assertTrue(report["fault_cache_live_at_end"])
        self.assertEqual("0x42", report["fault_cache"])
        self.assertEqual([], report["invalid_lifetime_events"])
        self.assertEqual(["compute"], report["overlaps_on_fault_cache"][0]["concurrent"])
        self.assertEqual(1, len(report["overlaps_during_fault"]))

    def test_null_submitted_cache_is_preserved_as_intervention(self):
        self.details["cache"] = "0x42"
        self.details["submitted_cache"] = "0x0"
        report = self.run_report([
            event(1, "create", "enter", 7, "0x0"),
            event(2, "create", "return", 7, "0x42"),
            event(3, "compute", "enter", 7, "0x42", other="0x0"),
        ])
        self.assertEqual("0x0", report["fault_submitted_cache"])

    def test_prior_diagnostic_overlap_is_not_fault_time_overlap(self):
        report = self.run_report([
            event(1, "create", "enter", 7, "0x0"),
            event(2, "create", "return", 7, "0x42"),
            event(3, "get_data", "enter", 8, "0x42"),
            event(4, "get_data", "enter", 9, "0x42"),
            event(5, "get_data", "return", 9, "0x42"),
            event(6, "get_data", "return", 8, "0x42"),
            event(7, "compute", "enter", 7, "0x42"),
        ])
        self.assertEqual(1, len(report["overlaps_on_fault_cache"]))
        self.assertEqual([], report["overlaps_during_fault"])

    def test_deferred_deletion_does_not_imply_destroyed_handle(self):
        self.details.update({"module": "0x10", "layout": "0x20"})
        report = self.run_report([
            event(1, "create", "enter", 7, "0x0"),
            event(2, "create", "return", 7, "0x42"),
            event(3, "module_create", "enter", 7, "0x0"),
            event(4, "module_create", "return", 7, "0x10"),
            event(5, "layout_create", "enter", 7, "0x0"),
            event(6, "layout_create", "return", 7, "0x20"),
            event(7, "module_enqueue", "return", 7, "0x10"),
            event(8, "compute", "enter", 7, "0x42"),
        ])
        self.assertEqual({"handle": "0x10", "live": True, "queued": True},
                         report["fault_objects"]["module"])
        self.assertTrue(report["fault_objects"]["layout"]["live"])
        self.assertEqual("CAPTURED_CACHE_HISTORY", report["status"])

    def test_actual_deferred_destroy_is_distinct_from_enqueue(self):
        self.details["module"] = "0x10"
        report = self.run_report([
            event(1, "create", "enter", 7, "0x0"),
            event(2, "create", "return", 7, "0x42"),
            event(3, "module_create", "enter", 7, "0x0"),
            event(4, "module_create", "return", 7, "0x10"),
            event(5, "module_enqueue", "return", 7, "0x10"),
            event(6, "module_destroy", "enter", 8, "0x10"),
            event(7, "module_destroy", "return", 8, "0x10"),
            event(8, "compute", "enter", 7, "0x42"),
        ])
        self.assertFalse(report["fault_objects"]["module"]["live"])
        self.assertEqual(["module"], report["missing_fault_objects"])
        self.assertEqual("CACHE_LIFETIME_ANOMALY", report["status"])

    def test_destroy_while_compute_is_pending_is_not_claimed_safe(self):
        report = self.run_report([
            event(1, "create", "enter", 7, "0x0"),
            event(2, "create", "return", 7, "0x42"),
            event(3, "compute", "enter", 7, "0x42"),
            event(4, "destroy", "enter", 8, "0x42"),
            event(5, "destroy", "return", 8, "0x42"),
        ])
        self.assertFalse(report["fault_cache_live_at_end"])
        self.assertEqual("CACHE_LIFETIME_ANOMALY", report["status"])
        self.assertEqual("destroy during active use",
                         report["invalid_lifetime_events"][0]["reason"])

    def test_partial_or_unpaired_history_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "invalid cache event"):
            self.run_report([event(2, "create", "enter", 7, "0x0")])
        with self.assertRaisesRegex(ValueError, "unpaired cache return"):
            self.run_report([event(1, "compute", "return", 7, "0x42")])
        with self.assertRaisesRegex(ValueError, "fault marker"):
            self.run_report([event(1, "create", "enter", 7, "0x0"),
                             event(2, "create", "return", 7, "0x42")])

    def test_instrumentation_is_off_by_default(self):
        source = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime"
                  / "VulkanRHI/Private/VulkanPipeline.cpp").read_text()
        block = source.split("namespace CarlaCacheLifecycle\n{", 2)[-1].split(
            "TAutoConsoleVariable", 1)[0]
        self.assertIn("CarlaVulkanCacheLifecycle", block)
        for kind in ("Create", "Destroy", "Merge", "GetData"):
            self.assertIn(f"CarlaCacheLifecycle::{kind}(", source)
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('cache_lifecycle="${CARLA_RUNTIME_CACHE_LIFECYCLE:-0}"', script)
        self.assertIn("requires mixed serialization and pipeline history", script)

    def test_object_history_distinguishes_deferred_enqueue_from_actual_destroy(self):
        base = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime"
                / "VulkanRHI/Private")
        memory = (base / "VulkanMemory.cpp").read_text()
        shaders = (base / "VulkanShaders.cpp").read_text()
        render = (base / "VulkanRenderpass.h").read_text()
        self.assertIn("module_enqueue", memory)
        self.assertIn("layout_enqueue", memory)
        self.assertIn("pass_enqueue", memory)
        self.assertIn("module_destroy", memory)
        self.assertIn("layout_destroy", memory)
        self.assertIn("pass_destroy", memory)
        self.assertIn("descriptor_enqueue", memory)
        self.assertIn("descriptor_destroy", memory)
        self.assertIn("vkDestroyShaderModule(DeviceHandle", memory)
        self.assertIn("vkDestroyPipelineLayout(DeviceHandle", memory)
        self.assertIn("vkDestroyRenderPass(DeviceHandle", memory)
        self.assertIn("module_create", shaders)
        self.assertIn("layout_create", shaders)
        self.assertIn("pass_create", render)
        descriptor = (base / "VulkanDescriptorSets.cpp").read_text()
        rhi = (base / "VulkanRHI.cpp").read_text()
        self.assertIn("descriptor_create", descriptor)
        self.assertIn("descriptor_create", rhi)


if __name__ == "__main__":
    unittest.main()
