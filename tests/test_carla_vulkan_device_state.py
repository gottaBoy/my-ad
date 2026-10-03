import importlib.util
import unittest
import sys
from unittest.mock import patch

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/compare_vulkan_device_state.py"
spec = importlib.util.spec_from_file_location("device_compare", SCRIPT)
module = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    spec.loader.exec_module(module)


class VulkanDeviceStateTest(unittest.TestCase):
    def test_legacy_capture_is_never_full_capability_pass(self):
        ue = {
            "device_extensions": ["VK_EXT_descriptor_buffer"],
            "core_features": {"core_1_0.shaderInt64": True},
            "pnext_stypes": [1, 2],
        }
        info = {
            "VkPhysicalDeviceProperties": {
                "deviceName": "llvmpipe", "vendorID": 65541, "apiVersion": 4206847,
            },
            "VkPhysicalDeviceFeatures": {"shaderInt64": 1},
            "ArrayOfVkExtensionProperties": [
                {"extensionName": "VK_EXT_descriptor_buffer"},
            ],
        }
        report = module.compare(ue, info)
        self.assertEqual("INCOMPLETE", report["status"])
        self.assertIn("actual_vkCreateDevice_feature_chain", report["unverified_requirements"])
        self.assertEqual(2, report["ue_pnext_s_type_count"])

    def test_missing_requirements_fail_closed(self):
        ue = {
            "device_extensions": ["VK_EXT_descriptor_buffer"],
            "core_features": {"core_1_0.shaderInt64": True},
        }
        info = {
            "VkPhysicalDeviceProperties": {"deviceName": "llvmpipe"},
            "VkPhysicalDeviceFeatures": {"shaderInt64": 0},
            "ArrayOfVkExtensionProperties": [],
        }
        report = module.compare(ue, info)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(["VK_EXT_descriptor_buffer"], report["missing_extensions"])
        self.assertEqual(["core_1_0.shaderInt64"], report["missing_core_features"])

    def test_empty_or_malformed_inputs_rejected(self):
        for state, info in (({}, {}), ({"device_extensions": []}, {}),
                            ({"core_features": {"core_1_0.shaderInt64": True},
                              "device_extensions": ["VK_KHR_surface"]}, {})):
            with self.subTest(state=state), self.assertRaises(ValueError):
                module.compare(state, info)

    def test_unchecked_advanced_features_are_explicit(self):
        ue = {
            "device_extensions": [],
            "core_features": {"core_1_0.shaderInt64": True,
                              "core_1_3.synchronization2": True},
        }
        info = {
            "VkPhysicalDeviceProperties": {"deviceName": "llvmpipe"},
            "VkPhysicalDeviceFeatures": {"shaderInt64": True},
            "ArrayOfVkExtensionProperties": [],
        }
        report = module.compare(ue, info)
        self.assertEqual("INCOMPLETE", report["status"])
        self.assertIn("core_1_3.synchronization2", report["unverified_requirements"])
