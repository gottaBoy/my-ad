"""Read typed Vulkan API entry arguments; stop before device creation."""

import json
import os
from pathlib import Path

import gdb

from vulkan_device_snapshot import CORE_FIELDS, FEATURE_STRUCTS, validate


def pointed(value, name):
    try:
        typename = gdb.lookup_type(name)
    except gdb.error:
        typename = gdb.lookup_type("struct " + name)
    return value.cast(typename.pointer()).dereference()


def boolean_fields(value, names):
    result = {}
    for name in names:
        flag = int(value[name])
        if flag not in (0, 1):
            raise ValueError(f"non-VkBool32 value in {name}: {flag}")
        result[name] = bool(flag)
    return result


def extension_names(info):
    count = int(info["enabledExtensionCount"])
    if not 0 <= count <= 128:
        raise ValueError("unbounded extension inventory")
    names = info["ppEnabledExtensionNames"]
    return count, [names[i].string(length=255).split("\0", 1)[0] for i in range(count)]


def instance_state(info):
    count, names = extension_names(info)
    app = info["pApplicationInfo"]
    if not int(app):
        raise ValueError("missing VkApplicationInfo")
    return {
        "api_version": int(app.dereference()["apiVersion"]),
        "flags": int(info["flags"]),
        "pnext_empty": int(info["pNext"]) == 0,
        "layer_count": int(info["enabledLayerCount"]),
        "extension_count": count,
        "extensions": names,
    }


def device_state(info):
    count, names = extension_names(info)
    core = info["pEnabledFeatures"]
    if not int(core):
        raise ValueError("pEnabledFeatures is null; unsupported core feature policy")
    state = {
        "flags": int(info["flags"]),
        "layer_count": int(info["enabledLayerCount"]),
        "device_extension_count": count,
        "device_extensions": names,
        "enabled_core_features": boolean_fields(core.dereference(), CORE_FIELDS),
        "feature_chain": [],
        "queue_create_infos": [],
        "pnext_terminated": False,
    }
    pointer = info["pNext"]
    visited = set()
    while int(pointer):
        address = int(pointer)
        if address in visited or len(visited) >= 64:
            raise ValueError("cyclic or unbounded device pNext")
        visited.add(address)
        base = pointed(pointer, "VkBaseInStructure")
        stype = int(base["sType"])
        if stype not in FEATURE_STRUCTS:
            raise ValueError(f"unsupported device pNext sType={stype}")
        typename, fields = FEATURE_STRUCTS[stype]
        node = pointed(pointer, typename)
        state["feature_chain"].append({
            "sType": stype, "type": typename,
            "features": boolean_fields(node, fields.split()),
        })
        pointer = base["pNext"]
    state["pnext_terminated"] = True
    queue_count = int(info["queueCreateInfoCount"])
    if not 1 <= queue_count <= 16:
        raise ValueError("missing or unbounded queue-create infos")
    queues = info["pQueueCreateInfos"]
    for i in range(queue_count):
        queue = queues[i]
        count = int(queue["queueCount"])
        if not 1 <= count <= 64:
            raise ValueError("missing or unbounded queue priorities")
        state["queue_create_infos"].append({
            "queueFamilyIndex": int(queue["queueFamilyIndex"]),
            "queueCount": count,
            "flags": int(queue["flags"]),
            "pnext_empty": int(queue["pNext"]) == 0,
            "priorities": [float(queue["pQueuePriorities"][j]) for j in range(count)],
        })
    return state


class Capture:
    def __init__(self):
        self.instance = None
        self.errors = []

    def save(self, state):
        state.update({
            "schema_version": 2,
            "capture_point": "vkCreateDevice:entry",
            "scope": "actual API input; no device creation or CARLA runtime acceptance",
            "pid": gdb.selected_inferior().pid,
            "instance": self.instance,
            "capture_complete": not self.errors,
            "capture_errors": list(self.errors),
            "icd": os.environ.get("VK_ICD_FILENAMES"),
        })
        if not self.errors:
            try:
                validate(state)
            except ValueError as error:
                state["capture_complete"] = False
                state["capture_errors"].append(str(error))
        path = Path(os.environ["CARLA_UE_VULKAN_DEVICE_STATE"])
        path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
        print("UE_VULKAN_DEVICE_STATE_WRITTEN", path)
        print("capture_complete =", state["capture_complete"])


class InstanceEntry(gdb.Breakpoint):
    def __init__(self, capture):
        super().__init__("vkCreateInstance", internal=True)
        self.capture = capture

    def stop(self):
        try:
            info = pointed(gdb.parse_and_eval("$x0"), "VkInstanceCreateInfo")
            self.capture.instance = instance_state(info)
        except (gdb.error, ValueError) as error:
            self.capture.errors.append(f"instance: {error}")
        return False


class DeviceEntry(gdb.Breakpoint):
    def __init__(self, capture):
        super().__init__("vkCreateDevice", internal=True)
        self.capture = capture

    def stop(self):
        state = {}
        try:
            info = pointed(gdb.parse_and_eval("$x1"), "VkDeviceCreateInfo")
            state = device_state(info)
        except (gdb.error, ValueError) as error:
            self.capture.errors.append(f"device: {error}")
        self.capture.save(state)
        # Device-state probes stop here; fault probes continue with the same
        # validated device input and let engine-side diagnostics observe the
        # later pipeline failure.
        return os.environ.get("CARLA_GDB_CONTINUE_AFTER_DEVICE") != "1"


def start():
    capture = Capture()
    InstanceEntry(capture)
    DeviceEntry(capture)
    return capture
