"""Correlate actual Vulkan module/layout/cache handles with a targeted compute call."""

import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time

import gdb

from gdb_vulkan_device_capture import Capture, device_state, instance_state, pointed


def register(number):
    return gdb.parse_and_eval(f"$x{number}")


def vk_result():
    value = int(register(0)) & 0xffffffff
    return value - 0x100000000 if value & 0x80000000 else value


def bounded(value, maximum, label, minimum=0):
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"unbounded {label}: {number}")
    return number


def no_next(info):
    if int(info["pNext"]):
        raise ValueError("unsupported nonempty pNext")


def allocation_callbacks(pointer):
    address = int(pointer)
    if not address:
        return {"is_null": True, "address": "0x0"}
    info = pointed(pointer, "VkAllocationCallbacks")
    functions = {}
    for name in ("pfnAllocation", "pfnReallocation", "pfnFree",
                 "pfnInternalAllocation", "pfnInternalFree"):
        value = int(info[name])
        symbol = None
        if value:
            try:
                block = gdb.block_for_pc(value)
                symbol = block.function.print_name if block and block.function else None
            except gdb.error:
                pass
        functions[name] = {"address": hex(value), "symbol": symbol}
    return {"is_null": False, "address": hex(address), "user_data": hex(int(info["pUserData"])),
            "functions": functions}


def target_intervention(record, intervention):
    record["intervention"] = intervention
    record["submitted_cache_handle"] = record["cache_handle"]
    record["submitted_allocator"] = record["allocator"]
    record["intervention_applied"] = False
    if intervention == "allocator-null" and not record["allocator"]["is_null"]:
        gdb.parse_and_eval("$x4 = 0")
        if int(register(4)) != 0:
            raise ValueError("allocator register intervention did not apply")
        record["submitted_allocator"] = {"is_null": True, "address": "0x0"}
        record["intervention_applied"] = True
    elif intervention == "cache-null":
        gdb.parse_and_eval("$x1 = 0")
        if int(register(1)) != 0:
            raise ValueError("cache register intervention did not apply")
        record["submitted_cache_handle"] = "0x0"
        record["intervention_applied"] = True


def stack_names():
    frame, names = gdb.newest_frame(), []
    while frame and len(names) < 80:
        names.append(frame.name() or "<unknown>")
        frame = frame.older()
    return names


def descriptor_layout(info):
    no_next(info)
    count = bounded(info["bindingCount"], 64, "descriptor bindings")
    result = {"flags": int(info["flags"]), "bindings": []}
    for i in range(count):
        binding = info["pBindings"][i]
        size = bounded(binding["descriptorCount"], 1024, "descriptor count", 1)
        samplers = binding["pImmutableSamplers"]
        result["bindings"].append({
            "binding": int(binding["binding"]),
            "descriptor_type": int(binding["descriptorType"]),
            "count": size, "stage_flags": int(binding["stageFlags"]),
            "immutable_samplers": [int(samplers[j]) for j in range(size)] if int(samplers) else [],
        })
    return result


def pipeline_layout(info, descriptors):
    no_next(info)
    count = bounded(info["setLayoutCount"], 8, "descriptor sets")
    result = {"flags": int(info["flags"]), "sets": [], "push_constants": []}
    for i in range(count):
        handle = int(info["pSetLayouts"][i])
        if handle not in descriptors:
            raise ValueError(f"unobserved descriptor-set layout: {handle:#x}")
        result["sets"].append(descriptors[handle])
    count = bounded(info["pushConstantRangeCount"], 16, "push ranges")
    for i in range(count):
        item = info["pPushConstantRanges"][i]
        result["push_constants"].append({
            "offset": int(item["offset"]), "size": int(item["size"]),
            "stage_flags": int(item["stageFlags"]),
        })
    return result


class CaptureState:
    def __init__(self):
        self.root = Path(os.environ["CARLA_UE_PIPELINE_CAPTURE_DIR"])
        self.target = os.environ.get("CARLA_UE_PIPELINE_TARGET", "FRDGMemcpyCS")
        self.mode = os.environ.get("CARLA_UE_PIPELINE_MODE", "entry")
        self.intervention = os.environ.get("CARLA_UE_PIPELINE_INTERVENTION", "none")
        self.modules, self.descriptors, self.layouts, self.caches = {}, {}, {}, {}
        self.errors, self.calls = [], []
        self.target_call = None
        self.graphics_calls = 0
        self.graphics_by_cache = {}
        self.total_bytes = 0
        self.object_count = 0
        self.api_bindings = {}
        self.device_capture = Capture()
        self.timer = None
        self.thread_samples = []
        self.observation_end = None
        self.failure_signal = None

    def blob(self, pointer, size, name, limit=16 * 1024 * 1024):
        size = bounded(size, limit, "blob bytes")
        self.total_bytes += size
        if self.total_bytes > 128 * 1024 * 1024:
            raise ValueError("capture exceeded total byte budget")
        data = gdb.selected_inferior().read_memory(int(pointer), size).tobytes() if size else b""
        (self.root / name).write_bytes(data)
        return {"file": name, "bytes": size, "sha256": hashlib.sha256(data).hexdigest()}

    def write(self):
        report = {
            "schema_version": 1, "capture_complete": self.target_call is not None and not self.errors,
            "capture_point": "vkCreateComputePipelines:entry",
            "target": self.target, "target_call": self.target_call,
            "mode": self.mode,
            "intervention": self.intervention,
            "observation_finished_monotonic": self.observation_end,
            "failure_signal": self.failure_signal,
            "errors": self.errors, "calls": self.calls,
            "api_bindings": self.api_bindings,
            "object_counts": {"modules": len(self.modules), "layouts": len(self.layouts),
                              "descriptor_layouts": len(self.descriptors), "caches": len(self.caches)},
            "thread_samples": self.thread_samples,
            "scope": "API inputs only; cache initialization, not final cache contents or runtime acceptance",
        }
        (self.root / "pipeline-capture.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    def finish(self):
        self.observation_end = time.monotonic()
        if self.timer:
            self.timer.cancel()
        selected = gdb.selected_thread()
        threads = gdb.selected_inferior().threads()
        candidates = [thread for thread in threads if thread == selected or thread.ptid[1] == gdb.selected_inferior().pid
                      or (thread.name or "").startswith(("RenderThread", "Foregro", "Backgro"))]
        candidates.sort(key=lambda thread: (thread != selected,
                                             thread.ptid[1] != gdb.selected_inferior().pid,
                                             not (thread.name or "").startswith("RenderThread"),
                                             thread.num))
        for thread in candidates[:8]:
            try:
                thread.switch()
                frame, frames = gdb.newest_frame(), []
                while frame and len(frames) < 24:
                    location = frame.find_sal()
                    frames.append({"name": frame.name(), "pc": hex(frame.pc()),
                                   "file": location.symtab.filename if location.symtab else None,
                                   "line": location.line})
                    frame = frame.older()
                self.thread_samples.append({"name": thread.name, "num": thread.num, "frames": frames})
            except gdb.error as error:
                self.thread_samples.append({"name": thread.name, "error": str(error)})
        if selected and selected.is_valid():
            selected.switch()
        self.write()


class Created(gdb.FinishBreakpoint):
    def __init__(self, state, output, typename, inventory, record):
        super().__init__(gdb.newest_frame(), internal=True)
        self.state, self.output, self.typename = state, output, typename
        self.inventory, self.record = inventory, record

    def stop(self):
        try:
            result = vk_result()
            if result != 0:
                raise ValueError(f"{self.typename} creation failed: {result}")
            handle = int(pointed(self.output, self.typename))
            if not handle:
                raise ValueError("null created handle")
            self.inventory[handle] = self.record
            self.state.write()
        except (gdb.error, ValueError) as error:
            self.state.errors.append(str(error))
            self.state.write()
            return True
        return False

    def out_of_scope(self):
        self.state.errors.append(f"{self.typename} creation did not return")
        self.state.write()


class ComputeReturned(gdb.FinishBreakpoint):
    def __init__(self, state, record, stop_after=False):
        super().__init__(gdb.newest_frame(), internal=True)
        self.state, self.record = state, record
        self.stop_after = stop_after

    def stop(self):
        self.record["driver_return_observed"] = True
        self.record["result"] = vk_result()
        self.record["instrumented_call_seconds"] = time.monotonic() - self.record["created_at_monotonic"]
        self.state.write()
        if self.stop_after and self.state.timer:
            self.state.timer.cancel()
        return self.stop_after


class ApiEntry(gdb.Breakpoint):
    def __init__(self, state, name):
        pointer = int(gdb.parse_and_eval(f"VulkanDynamicAPI::{name}"))
        if not pointer:
            raise ValueError(f"missing initialized API pointer: {name}")
        super().__init__(f"*{pointer:#x}", internal=True)
        self.state, self.name = state, name
        state.api_bindings[name] = hex(pointer)

    def stop(self):
        state = self.state
        try:
            if self.name == "vkCreateGraphicsPipelines":
                cache, count = int(register(1)), int(register(2))
                state.graphics_calls += count
                state.graphics_by_cache[cache] = state.graphics_by_cache.get(cache, 0) + count
                return False
            if self.name == "vkCreateComputePipelines":
                return self.compute()
            state.object_count += 1
            if state.object_count > 512:
                raise ValueError("capture exceeded object budget")
            info_name, handle_name, inventory = {
                "vkCreateShaderModule": ("VkShaderModuleCreateInfo", "VkShaderModule", state.modules),
                "vkCreateDescriptorSetLayout": ("VkDescriptorSetLayoutCreateInfo", "VkDescriptorSetLayout", state.descriptors),
                "vkCreatePipelineLayout": ("VkPipelineLayoutCreateInfo", "VkPipelineLayout", state.layouts),
                "vkCreatePipelineCache": ("VkPipelineCacheCreateInfo", "VkPipelineCache", state.caches),
            }[self.name]
            info = pointed(register(1), info_name)
            no_next(info)
            if self.name == "vkCreateShaderModule":
                size = bounded(info["codeSize"], 16 * 1024 * 1024, "SPIR-V", 20)
                if size % 4:
                    raise ValueError("SPIR-V is not word aligned")
                record = state.blob(info["pCode"], size, f"module-{state.object_count:04d}.spv")
                record["flags"] = int(info["flags"])
            elif self.name == "vkCreateDescriptorSetLayout":
                record = descriptor_layout(info)
            elif self.name == "vkCreatePipelineLayout":
                record = pipeline_layout(info, state.descriptors)
            else:
                record = state.blob(info["pInitialData"], info["initialDataSize"],
                                    f"cache-{state.object_count:04d}.bin", 64 * 1024 * 1024)
                record["flags"] = int(info["flags"])
            record["allocator"] = allocation_callbacks(register(2))
            Created(state, register(3), handle_name, inventory, record)
            return False
        except (gdb.error, ValueError) as error:
            state.errors.append(f"{self.name}: {error}")
            state.write()
            return True

    def compute(self):
        state = self.state
        count = bounded(register(2), 8, "compute pipeline batch", 1)
        if count != 1:
            raise ValueError("only single-pipeline calls are supported")
        info = pointed(register(3), "VkComputePipelineCreateInfo")
        no_next(info)
        stage = info["stage"]
        if int(stage["stage"]) != 32 or int(stage["pSpecializationInfo"]):
            raise ValueError("unsupported compute stage or specialization")
        subgroup = 0
        if int(stage["pNext"]):
            base = pointed(stage["pNext"], "VkBaseInStructure")
            if int(base["sType"]) != 1000225001 or int(base["pNext"]):
                raise ValueError("unsupported compute stage pNext")
            subgroup = int(pointed(stage["pNext"], "VkPipelineShaderStageRequiredSubgroupSizeCreateInfo")["requiredSubgroupSize"])
        module, layout, cache = int(stage["module"]), int(info["layout"]), int(register(1))
        if module not in state.modules or layout not in state.layouts or cache not in state.caches:
            raise ValueError("compute references an unobserved module, layout or cache")
        names = stack_names()
        record = {
            "entry": stage["pName"].string(), "flags": int(info["flags"]),
            "stage_flags": int(stage["flags"]), "required_subgroup_size": subgroup,
            "base_pipeline_handle": int(info["basePipelineHandle"]),
            "base_pipeline_index": int(info["basePipelineIndex"]),
            "module_handle": hex(module), "layout_handle": hex(layout), "cache_handle": hex(cache),
            "module": state.modules[module], "layout": state.layouts[layout],
            "cache_initial": state.caches[cache],
            "graphics_calls_before": state.graphics_calls, "stack": names,
            "graphics_same_cache_before": state.graphics_by_cache.get(cache, 0),
            "driver_return_observed": False, "result": None, "instrumented_call_seconds": None,
            "created_at_monotonic": time.monotonic(),
            "allocator": allocation_callbacks(register(4)),
            "thread_num": gdb.selected_thread().num,
        }
        if len(state.calls) >= 128:
            raise ValueError("capture exceeded compute call budget")
        state.calls.append(record)
        state.write()
        if state.mode != "crash" and any(state.target in name for name in names):
            target_intervention(record, state.intervention)
            state.target_call = record
            state.write()
            if state.mode == "entry" and state.timer:
                state.timer.cancel()
            print("UE_COMPUTE_TARGET_CAPTURED", record["entry"], record["module"]["sha256"])
            if state.mode == "entry":
                return True
            ComputeReturned(state, record, stop_after=True)
            return False
        ComputeReturned(state, record)
        return False


class SetupEntry(gdb.Breakpoint):
    def __init__(self, state):
        super().__init__("FVulkanDevice::SetupFormats()", internal=True)
        self.state = state

    def stop(self):
        try:
            for name in ("vkCreateShaderModule", "vkCreateDescriptorSetLayout", "vkCreatePipelineLayout",
                         "vkCreatePipelineCache", "vkCreateGraphicsPipelines", "vkCreateComputePipelines"):
                ApiEntry(self.state, name)
            self.enabled = False
            self.state.write()
        except (gdb.error, ValueError) as error:
            self.state.errors.append(str(error))
            self.state.write()
            return True
        return False


class InstanceEntry(gdb.Breakpoint):
    def __init__(self, state):
        super().__init__("vkCreateInstance", internal=True)
        self.state = state

    def stop(self):
        try:
            self.state.device_capture.instance = instance_state(pointed(register(0), "VkInstanceCreateInfo"))
        except (gdb.error, ValueError) as error:
            self.state.errors.append(str(error))
            return True
        return False


class DeviceEntry(gdb.Breakpoint):
    def __init__(self, state):
        super().__init__("vkCreateDevice", internal=True)
        self.state = state

    def stop(self):
        try:
            self.state.device_capture.save(device_state(pointed(register(1), "VkDeviceCreateInfo")))
        except (gdb.error, ValueError) as error:
            self.state.errors.append(str(error))
            return True
        return False


state = CaptureState()
InstanceEntry(state)
DeviceEntry(state)
SetupEntry(state)


def signal_stop(event):
    if not isinstance(event, gdb.SignalEvent):
        return
    state.failure_signal = event.stop_signal
    if state.mode == "crash" and event.stop_signal in ("SIGSEGV", "SIGABRT", "SIGBUS"):
        thread = gdb.selected_thread().num
        pending = [call for call in state.calls
                   if call["thread_num"] == thread and not call["driver_return_observed"]]
        if pending:
            state.target_call = pending[-1]
            state.target = "CreateComputePipelineFromShader"
            target_intervention(state.target_call, "none")
    state.write()


gdb.events.stop.connect(signal_stop)
state.timer = threading.Timer(int(os.environ["CARLA_UE_PIPELINE_TIMEOUT"]),
                              lambda: os.kill(os.getpid(), signal.SIGINT))
state.timer.daemon = True
state.timer.start()
