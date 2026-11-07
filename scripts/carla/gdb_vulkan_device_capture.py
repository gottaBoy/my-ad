"""Read typed Vulkan API entry arguments; stop before device creation."""

import json
import os
from pathlib import Path
import re

import gdb

from vulkan_device_snapshot import CORE_FIELDS, FEATURE_STRUCTS, validate


TASK_TAGS = {
    0x00000001: "EStaticInit",
    0x00000002: "EGameThread",
    0x00000004: "ESlateThread",
    0x00000008: "EAudioThread",
    0x00000010: "ERenderingThread",
    0x00000020: "ERhiThread",
    0x00000040: "EAsyncLoadingThread",
    0x00000080: "EEventThread",
    0x20000000: "EWorkerThread",
    0x40000000: "EParallelThread",
}


def pointed(value, name):
    try:
        typename = gdb.lookup_type(name)
    except gdb.error:
        typename = gdb.lookup_type("struct " + name)
    return value.cast(typename.pointer()).dereference()


def read_task_context():
    """Read UE task/TLS state without invoking any inferior function."""
    context = {
        "scope": "best-effort GDB symbol/memory reads at signal stop; no inferior calls",
        "task_tag": {"symbol": "FTaskTagScope::ActiveTaskTag", "value": None,
                     "names": [], "read_error": None},
        "low_level_active_task": {
            "symbol": "LowLevelTasks::FTask::ActiveTask", "address": None,
            "packed_data": None, "debug_name_address": None, "debug_name": None,
            "packed_data_source": None,
            "state": None, "priority": None, "flags": None, "read_error": None,
        },
        "ue_tasks_current_task": {
            "symbol": "UE::Tasks::Private::CurrentTask", "address": None,
            "read_error": None,
        },
    }

    def expression(name):
        return gdb.parse_and_eval(name)

    try:
        value = int(expression("FTaskTagScope::ActiveTaskTag"))
        context["task_tag"]["value"] = value
        context["task_tag"]["names"] = [
            name for bit, name in TASK_TAGS.items() if value & bit
        ]
    except (gdb.error, ValueError, OverflowError) as error:
        context["task_tag"]["read_error"] = str(error)

    low_level = context["low_level_active_task"]
    try:
        value = expression("LowLevelTasks::FTask::ActiveTask")
        address = int(value)
        low_level["address"] = hex(address) if address else None
        if address:
            # GDB cannot reliably convert libstdc++ atomic wrappers on this
            # target. Use the typed field only to locate PackedData, then read
            # its raw word directly without invoking a method.
            task = value.dereference()
            packed = task["PackedData"]
            packed_address = int(packed.address)
            raw = int.from_bytes(
                gdb.selected_inferior().read_memory(packed_address, 8).tobytes(),
                byteorder="little",
            )
            low_level["packed_data_source"] = (
                f"typed PackedData address {packed_address:#x}, 8-byte raw read"
            )
            low_level["packed_data"] = hex(raw)
            debug_address = (raw >> 6) & ((1 << 53) - 1)
            low_level["debug_name_address"] = (
                hex(debug_address) if debug_address else None
            )
            low_level["state"] = raw & 0x3F
            low_level["priority"] = (raw >> 59) & 0x7
            low_level["flags"] = (raw >> 62) & 0x3
            if debug_address:
                # UE Linux TCHAR is char16_t. Read a bounded string directly
                # from inferior memory; never call a UE or libc helper.
                data = gdb.selected_inferior().read_memory(
                    debug_address, 128 * 2
                ).tobytes()
                low_level["debug_name"] = data.decode(
                    "utf-16-le", errors="replace"
                ).split("\0", 1)[0]
    except (gdb.error, ValueError, TypeError, OverflowError, UnicodeError) as error:
        low_level["read_error"] = str(error)

    current = context["ue_tasks_current_task"]
    try:
        value = expression("UE::Tasks::Private::CurrentTask")
        address = int(value)
        current["address"] = hex(address) if address else None
    except (gdb.error, ValueError, TypeError, OverflowError) as error:
        current["read_error"] = str(error)
    return context


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

    def save_fault_context(self, event):
        signal_name = getattr(event, "stop_signal", None)
        if signal_name not in ("SIGSEGV", "SIGBUS", "SIGABRT"):
            return
        inferior = gdb.selected_inferior()
        selected = gdb.selected_thread()
        pid = inferior.pid
        ptid = list(selected.ptid)
        lwp = ptid[1] if len(ptid) > 1 else ptid[0]
        context = {
            "schema_version": 2,
            "signal": signal_name,
            "inferior_pid": pid,
            "selected_thread_num": selected.num,
            "selected_thread_name": selected.name,
            "selected_ptid": ptid,
            "scope": "Read-only procfs/GDB register snapshot at signal stop; no inferior calls",
            "registers": {},
            "selected_thread_procfs": {},
            "threads": [],
            "ue_task_context": None,
        }
        for register in ("pc", "sp", "x18", "tpidr_el0"):
            try:
                context["registers"][register] = hex(int(gdb.parse_and_eval(f"${register}")))
            except (gdb.error, ValueError):
                context["registers"][register] = None

        def proc_status(tid):
            result = {}
            path = Path(f"/proc/{pid}/task/{tid}/status")
            try:
                for line in path.read_text(errors="replace").splitlines():
                    key, separator, value = line.partition(":")
                    if separator and key in {
                        "Name", "State", "Cpus_allowed_list",
                        "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches",
                    }:
                        result[key] = value.strip()
            except OSError as error:
                result["read_error"] = str(error)
            return result

        def proc_stat(tid):
            result = {}
            path = Path(f"/proc/{pid}/task/{tid}/stat")
            try:
                text = path.read_text(errors="replace")
                closing = text.rfind(")")
                fields = text[closing + 2:].split()
                if len(fields) > 36:
                    result["state"] = fields[0]
                    result["processor"] = int(fields[36])
            except (OSError, ValueError, IndexError) as error:
                result["read_error"] = str(error)
            return result

        def stack_map(tid, sp):
            if not sp:
                return None
            try:
                address = int(sp, 16)
                for line in Path(f"/proc/{pid}/task/{tid}/maps").read_text().splitlines():
                    match = re.match(r"^([0-9a-f]+)-([0-9a-f]+)\s+(.+)$", line)
                    if not match:
                        continue
                    begin, end = int(match[1], 16), int(match[2], 16)
                    if begin <= address < end:
                        return {"range": f"{begin:#x}-{end:#x}", "line": line}
            except (OSError, ValueError):
                return None
            return None

        context["selected_thread_procfs"] = {
            "status": proc_status(lwp),
            "stat": proc_stat(lwp),
            "stack_mapping": stack_map(lwp, context["registers"].get("sp")),
        }
        context["ue_task_context"] = read_task_context()
        try:
            threads = inferior.threads()
        except gdb.error:
            threads = []
        for thread in threads[:128]:
            thread_ptid = list(thread.ptid)
            thread_lwp = thread_ptid[1] if len(thread_ptid) > 1 else thread_ptid[0]
            context["threads"].append({
                "num": thread.num,
                "name": thread.name,
                "ptid": thread_ptid,
                "status": proc_status(thread_lwp),
                "stat": proc_stat(thread_lwp),
            })
        output = Path(os.environ.get(
            "CARLA_UE_VULKAN_FAULT_CONTEXT",
            str(Path(os.environ["CARLA_UE_VULKAN_DEVICE_STATE"]).with_name(
                "fault-thread-context.json"))))
        output.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n")
        print("UE_VULKAN_FAULT_CONTEXT_WRITTEN", output)


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
    gdb.events.stop.connect(capture.save_fault_context)
    return capture
