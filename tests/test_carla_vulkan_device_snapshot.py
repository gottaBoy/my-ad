import copy
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/vulkan_device_snapshot.py"
spec = importlib.util.spec_from_file_location("vulkan_device_snapshot", SCRIPT)
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def valid_state():
    stype = 1000257000
    name, fields = snapshot.FEATURE_STRUCTS[stype]
    return {
        "schema_version": 2,
        "capture_complete": True,
        "capture_errors": [],
        "capture_point": "vkCreateDevice:entry",
        "instance": {
            "api_version": 4206592, "flags": 0, "pnext_empty": True, "layer_count": 0,
            "extension_count": 1, "extensions": ["VK_KHR_surface"],
        },
        "flags": 0, "layer_count": 0,
        "device_extension_count": 1, "device_extensions": ["VK_KHR_buffer_device_address"],
        "enabled_core_features": {field: field == "shaderInt64" for field in snapshot.CORE_FIELDS},
        "pnext_terminated": True,
        "feature_chain": [{
            "sType": stype, "type": name,
            "features": {field: field == "bufferDeviceAddress" for field in fields.split()},
        }],
        "queue_create_infos": [{
            "queueFamilyIndex": 0, "queueCount": 1, "flags": 0,
            "pnext_empty": True, "priorities": [1.0],
        }],
    }


class VulkanDeviceSnapshotTest(unittest.TestCase):
    def test_complete_snapshot_and_empty_chain(self):
        state = valid_state()
        self.assertIs(state, snapshot.validate(state))
        state["feature_chain"] = []
        snapshot.validate(state)

    def test_old_and_incomplete_capture_rejected(self):
        for key, value in (
            ("schema_version", 1), ("capture_complete", False),
            ("capture_errors", ["unreadable"]), ("capture_point", "source-line"),
            ("pnext_terminated", False),
        ):
            with self.subTest(key=key):
                state = valid_state()
                state[key] = value
                with self.assertRaises(ValueError):
                    snapshot.validate(state)

    def test_core_feature_inventory_is_exact_and_boolean(self):
        for mutation in ("missing", "extra", "integer"):
            state = valid_state()
            features = state["enabled_core_features"]
            if mutation == "missing":
                del features["shaderInt64"]
            elif mutation == "extra":
                features["fakeFeature"] = False
            else:
                features["shaderInt64"] = 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                snapshot.validate(state)

    def test_incomplete_or_unknown_pnext_is_rejected(self):
        for mutation in ("duplicate", "unknown", "wrongtype", "missing", "integer"):
            state = valid_state()
            chain = state["feature_chain"]
            if mutation == "duplicate":
                chain.append(copy.deepcopy(chain[0]))
            elif mutation == "unknown":
                chain[0]["sType"] = 1
            elif mutation == "wrongtype":
                chain[0]["type"] = "VkFakeFeatures"
            elif mutation == "missing":
                del chain[0]["features"]["bufferDeviceAddress"]
            else:
                chain[0]["features"]["bufferDeviceAddress"] = 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                snapshot.validate(state)

    def test_extensions_are_unique_and_safe(self):
        for names, count in (
            (["VK_KHR_surface", "VK_KHR_surface"], 2),
            (['VK_KHR_surface";system("x")'], 1),
            (["VK_KHR_surface"], 0),
            (["VK_KHR_surface"], True),
        ):
            state = valid_state()
            state["device_extensions"], state["device_extension_count"] = names, count
            with self.subTest(names=names, count=count), self.assertRaises(ValueError):
                snapshot.validate(state)

    def test_missing_and_invalid_queue_configuration_rejected(self):
        state = valid_state()
        state["queue_create_infos"] = []
        with self.assertRaises(ValueError):
            snapshot.validate(state)
        for key, value in (
            ("queueFamilyIndex", -1), ("queueCount", 0), ("flags", 1),
            ("flags", False), ("pnext_empty", False), ("priorities", []),
            ("priorities", [True]), ("priorities", [float("nan")]),
            ("priorities", [-0.1]), ("priorities", [1.1]),
        ):
            state = valid_state()
            state["queue_create_infos"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                snapshot.validate(state)
        state = valid_state()
        state["queue_create_infos"] *= 2
        with self.assertRaises(ValueError):
            snapshot.validate(state)

    def test_instance_configuration_must_be_replayable(self):
        for key, value in (
            ("api_version", 4194304), ("flags", 1),
            ("pnext_empty", False), ("layer_count", 1),
        ):
            state = valid_state()
            state["instance"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                snapshot.validate(state)

    def test_duplicate_json_and_nonfinite_values_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.json"
            for text in ('{"x":1,"x":2}', '{"priority":NaN}', '{"priority":Infinity}'):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ValueError):
                    snapshot.load(path)
            path.write_text(json.dumps(valid_state()))
            snapshot.validate(snapshot.load(path))

    def test_generated_header_preserves_policy_and_support_checks(self):
        header = snapshot.generate_header(valid_state(), "a" * 64)
        for contract in (
            ".shaderInt64 = VK_TRUE", ".robustBufferAccess = VK_FALSE",
            ".bufferDeviceAddress = VK_TRUE", ".bufferDeviceAddressCaptureReplay = VK_FALSE",
            "info->pEnabledFeatures = &captured_core", "info->pNext = &captured_node_0",
            "vkEnumerateDeviceExtensionProperties", "Captured feature unavailable",
            ".queueFamilyIndex = 0, .queueCount = 1", "captured_priorities_0",
            '#define CARLA_DEVICE_SNAPSHOT_SHA256 "' + "a" * 64 + '"',
        ):
            self.assertIn(contract, header)
        with self.assertRaises(ValueError):
            snapshot.generate_header(valid_state(), '";invalid')

    def test_empty_inventory_header_is_valid_c(self):
        state = valid_state()
        state["feature_chain"] = []
        state["device_extensions"] = []
        state["device_extension_count"] = 0
        header = snapshot.generate_header(state)
        self.assertIn("captured_extensions[] = {NULL}", header)
        self.assertIn("info->pNext = NULL", header)

    def test_schema_matches_repository_vulkan_header(self):
        path = ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/Vulkan/Include/vulkan/vulkan_core.h"
        if not path.exists():
            self.skipTest("UE Vulkan SDK not checked out")
        header = path.read_text()
        for name, fields in [("VkPhysicalDeviceFeatures", " ".join(snapshot.CORE_FIELDS)),
                             *snapshot.FEATURE_STRUCTS.values()]:
            body = re.search(r"typedef struct " + name + r" \{(.*?)\} " + name + ";",
                             header, re.S)
            self.assertIsNotNone(body, name)
            actual = re.findall(r"VkBool32\s+(\w+)\s*;", body.group(1))
            self.assertEqual(fields.split(), actual, name)
        for stype, (name, _) in snapshot.FEATURE_STRUCTS.items():
            constant = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name[2:]).upper()
            constant = re.sub(r"(MAINTENANCE|SYNCHRONIZATION)(\d)", r"\1_\2", constant)
            self.assertIsNotNone(re.search(rf"VK_STRUCTURE_TYPE_{constant}\s*=\s*{stype}\b",
                                          header), f"sType mismatch: {name}")


class Pointer:
    def __init__(self, value=None, address=0):
        self.value, self.address = value, address

    def __int__(self):
        return self.address

    def dereference(self):
        return self.value

    def cast(self, _typename):
        return self


class DummyType:
    def pointer(self):
        return self


class DummyBreakpoint:
    def __init__(self, *args, **kwargs):
        pass


class TypedCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dummy_gdb = types.SimpleNamespace(
            Breakpoint=DummyBreakpoint, error=RuntimeError, lookup_type=lambda _: DummyType(),
        )
        path = ROOT / "scripts/carla/gdb_vulkan_device_capture.py"
        spec = importlib.util.spec_from_file_location("gdb_capture_test", path)
        cls.capture = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"gdb": dummy_gdb, "vulkan_device_snapshot": snapshot}):
            spec.loader.exec_module(cls.capture)

    def info(self):
        return {
            "enabledExtensionCount": 0, "ppEnabledExtensionNames": [],
            "flags": 0, "enabledLayerCount": 0,
            "pEnabledFeatures": Pointer({name: 0 for name in snapshot.CORE_FIELDS}, 10),
            "pNext": Pointer(), "queueCreateInfoCount": 1,
            "pQueueCreateInfos": [{
                "queueFamilyIndex": 2, "queueCount": 2, "flags": 0,
                "pNext": Pointer(), "pQueuePriorities": [0.5, 1.0],
            }],
        }

    def test_typed_queue_fields_and_priorities(self):
        state = self.capture.device_state(self.info())
        queue = state["queue_create_infos"][0]
        self.assertEqual((2, 2, [0.5, 1.0]),
                         (queue["queueFamilyIndex"], queue["queueCount"], queue["priorities"]))
        self.assertTrue(state["pnext_terminated"])
        self.assertEqual(55, len(state["enabled_core_features"]))

    def test_typed_chain_values_and_cycle_guard(self):
        info = self.info()
        node = {"sType": 1000207000, "pNext": Pointer(), "timelineSemaphore": 1}
        pointer = Pointer(node, 100)
        info["pNext"] = pointer
        state = self.capture.device_state(info)
        self.assertEqual({"timelineSemaphore": True}, state["feature_chain"][0]["features"])
        node["pNext"] = pointer
        with self.assertRaisesRegex(ValueError, "cyclic"):
            self.capture.device_state(info)

    def test_unknown_node_and_missing_core_do_not_guess(self):
        info = self.info()
        info["pNext"] = Pointer({"sType": 9999}, 100)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            self.capture.device_state(info)
        info = self.info()
        info["pEnabledFeatures"] = Pointer()
        with self.assertRaisesRegex(ValueError, "null"):
            self.capture.device_state(info)

    def test_non_boolean_vkbool32_rejected(self):
        with self.assertRaises(ValueError):
            self.capture.boolean_fields({"timelineSemaphore": 2}, ["timelineSemaphore"])


class CapabilitySnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "scripts/carla/compare_vulkan_device_state.py"
        spec = importlib.util.spec_from_file_location("device_capability_test", path)
        cls.compare = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"vulkan_device_snapshot": snapshot}):
            spec.loader.exec_module(cls.compare)

    def info(self):
        return {
            "VkPhysicalDeviceProperties": {"deviceName": "test", "apiVersion": 4206592},
            "VkPhysicalDeviceFeatures": {"shaderInt64": True},
            "ArrayOfVkExtensionProperties": [{"extensionName": "VK_KHR_buffer_device_address"}],
            "VkPhysicalDeviceBufferDeviceAddressFeatures": {"bufferDeviceAddress": True},
        }

    def test_complete_enabled_feature_checks_pass(self):
        self.assertEqual("PASS", self.compare.compare(valid_state(), self.info())["status"])

    def test_missing_pnext_values_never_pass(self):
        info = self.info()
        del info["VkPhysicalDeviceBufferDeviceAddressFeatures"]
        report = self.compare.compare(valid_state(), info)
        self.assertEqual("INCOMPLETE", report["status"])
        self.assertEqual(["VkPhysicalDeviceBufferDeviceAddressFeatures.bufferDeviceAddress"],
                         report["unverified_requirements"])

    def test_disabled_target_pnext_feature_fails(self):
        info = self.info()
        info["VkPhysicalDeviceBufferDeviceAddressFeatures"]["bufferDeviceAddress"] = False
        report = self.compare.compare(valid_state(), info)
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(["VkPhysicalDeviceBufferDeviceAddressFeatures.bufferDeviceAddress"],
                         report["missing_core_features"])

    def test_missing_core_value_is_not_guessed(self):
        info = self.info()
        info["VkPhysicalDeviceFeatures"] = {}
        report = self.compare.compare(valid_state(), info)
        self.assertEqual("INCOMPLETE", report["status"])
        self.assertIn("core_1_0.shaderInt64", report["unverified_requirements"])

    def test_older_target_api_fails(self):
        info = self.info()
        info["VkPhysicalDeviceProperties"]["apiVersion"] = 4194304
        self.assertEqual("FAIL", self.compare.compare(valid_state(), info)["status"])
