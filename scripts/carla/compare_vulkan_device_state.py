#!/usr/bin/env python3
"""Compare UE's captured Vulkan device requirements with vulkaninfo JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from vulkan_device_snapshot import load, require, validate


def compare(ue_state: dict, vulkan_info: dict) -> dict:
    require(isinstance(ue_state, dict) and isinstance(vulkan_info, dict),
            "both inputs must be JSON objects")
    actual_capture = ue_state.get("schema_version") == 2
    if actual_capture:
        validate(ue_state)
        expected_features = {
            "core_1_0." + name: value
            for name, value in ue_state["enabled_core_features"].items()
        }
        nodes = ue_state["feature_chain"]
    else:
        expected_features = ue_state.get("core_features")
        nodes = []
        require(isinstance(expected_features, dict) and expected_features
                and all(isinstance(name, str) and type(value) is bool
                        for name, value in expected_features.items()),
                "legacy snapshot has no valid core feature inventory")
    names = ue_state.get("device_extensions")
    require(isinstance(names, list) and all(isinstance(name, str) for name in names)
            and len(names) == len(set(names)), "invalid device_extensions")
    properties = vulkan_info.get("VkPhysicalDeviceProperties", {})
    features = vulkan_info.get("VkPhysicalDeviceFeatures", {})
    inventory = vulkan_info.get("ArrayOfVkExtensionProperties")
    require(isinstance(properties, dict) and isinstance(properties.get("deviceName"), str)
            and properties["deviceName"] and isinstance(features, dict),
            "missing Vulkan device properties/features")
    require(isinstance(inventory, list)
            and all(isinstance(item, dict) and isinstance(item.get("extensionName"), str)
                    for item in inventory), "missing or invalid target extension inventory")
    available_extensions = {item["extensionName"] for item in inventory}
    require(len(available_extensions) == len(inventory), "duplicate target extension names")
    ue_extensions = set(names)
    missing_extensions = sorted(ue_extensions - available_extensions)

    missing_features = []
    unverified = [] if actual_capture else ["actual_vkCreateDevice_feature_chain"]

    def check_flag(values, key, label):
        flag = values.get(key)
        if type(flag) not in (int, bool) or flag not in (0, 1):
            unverified.append(label)
        elif not flag:
            missing_features.append(label)

    for name, expected in expected_features.items():
        if not expected:
            continue
        if not name.startswith("core_1_0."):
            unverified.append(name)
            continue
        feature = name.split(".", 1)[1]
        check_flag(features, feature, name)
    for node in nodes:
        typename = node["type"]
        values = vulkan_info.get(typename)
        if not isinstance(values, dict):
            values = vulkan_info.get(typename + "KHR", vulkan_info.get(typename + "EXT", {}))
        if not isinstance(values, dict):
            values = {}
        for name, enabled in node["features"].items():
            if enabled:
                check_flag(values, name, typename + "." + name)
    api_version = properties.get("apiVersion")
    if actual_capture:
        if type(api_version) is not int:
            unverified.append("api_version")
        elif api_version < ue_state["instance"]["api_version"]:
            missing_features.append("api_version")

    report = {
        "schema_version": 1,
        "status": ("FAIL" if missing_extensions or missing_features else
                   "INCOMPLETE" if unverified else "PASS"),
        "ue_device_extension_count": len(ue_extensions),
        "available_device_extension_count": len(available_extensions),
        "missing_extensions": missing_extensions,
        "missing_core_features": sorted(missing_features),
        "unverified_requirements": sorted(unverified),
        "ue_pnext_s_type_count": len(nodes) if actual_capture else len(ue_state.get("pnext_stypes", [])),
        "device_name": properties.get("deviceName"),
        "vendor_id": properties.get("vendorID"),
        "api_version": properties.get("apiVersion"),
        "scope": "capability comparison only; not UE VkDeviceCreateInfo replay",
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ue-state", required=True, type=Path)
    parser.add_argument("--vulkaninfo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = compare(load(args.ue_state), load(args.vulkaninfo))
    except (OSError, ValueError) as error:
        parser.exit(2, f"capability inputs rejected: {error}\n")
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"{report['status']} vulkan-device-capability device={report['device_name']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
