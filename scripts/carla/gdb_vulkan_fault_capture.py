"""Read the first Vulkan check/fatal signal without instrumenting creation calls."""

import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time

import gdb


def bounded(value, maximum, label):
    number = int(value)
    if not 0 <= number <= maximum:
        raise ValueError(f"unbounded {label}: {number}")
    return number


def array_data(value, maximum=128):
    count = bounded(value["ArrayNum"], maximum, "array size")
    capacity = bounded(value["ArrayMax"], 16 * 1024 * 1024, "array capacity")
    if count > capacity:
        raise ValueError("array size exceeds capacity")
    pointer = value["AllocatorInstance"]["Data"]
    if count and not int(pointer):
        raise ValueError("nonempty array has null data")
    element = value.type.strip_typedefs().template_argument(0)
    return pointer.cast(element.pointer()), count


def array_items(value, maximum=128):
    pointer, count = array_data(value, maximum)
    return [pointer[index] for index in range(count)]


def ue_string(value):
    pointer, count = array_data(value["Data"], 512)
    width = pointer.type.target().sizeof
    if width not in (2, 4):
        raise ValueError(f"unsupported TCHAR size: {width}")
    data = gdb.selected_inferior().read_memory(int(pointer), count * width).tobytes() if count else b""
    return data.decode(f"utf-{width * 8}-le").rstrip("\0")


def c_string(value, maximum):
    return value.string(length=maximum).split("\0", 1)[0]


def frames_from(frame):
    frames = []
    while frame and len(frames) < 64:
        frames.append(frame)
        frame = frame.older()
    return frames


def frame_record(frame):
    location = frame.find_sal()
    return {
        "name": frame.name(), "pc": hex(frame.pc()), "library": gdb.solib_name(frame.pc()),
        "file": location.symtab.filename if location.symtab else None, "line": location.line,
    }


def thread_priority(thread, selected, pid):
    name = thread.name or ""
    role = 0 if name.startswith(("RenderThread", "RHIThread")) else 1 if name.startswith(("Foregro", "Backgro")) else 2
    return thread != selected, thread.ptid[1] != pid, role, thread.num


def field(record, errors, name, reader):
    try:
        record[name] = reader()
    except (gdb.error, ValueError, KeyError, OverflowError) as error:
        errors.append(f"{name}: {error}")


def shader_record(pointer, errors):
    record = {"address": hex(int(pointer))}
    if not int(pointer):
        return record
    shader = pointer.dereference()
    for name, reader in (
        ("key", lambda: int(shader["ShaderKey"])),
        ("frequency", lambda: int(shader["Frequency"])),
        ("bindless", lambda: bool(shader["bUsesBindless"])),
        ("debug_name", lambda: ue_string(shader["CodeHeader"]["DebugName"])),
        ("spirv_crc", lambda: int(shader["CodeHeader"]["SpirvCRC"])),
        ("bound_uniform_buffers", lambda: int(shader["CodeHeader"]["NumBoundUniformBuffers"])),
        ("uniform_buffers", lambda: [
            {"layout_hash": int(info["LayoutHash"]), "has_resources": int(info["bHasResources"]),
             "bindless_cb_index": int(info["BindlessCBIndex"])}
            for info in array_items(shader["CodeHeader"]["UniformBufferInfos"])
        ]),
    ):
        field(record, errors, name, reader)
    return record


def layout_record(pointer):
    sets = array_items(pointer.dereference()["SetLayouts"], 8)
    return [
        [{"binding": int(binding["binding"]), "descriptor_type": int(binding["descriptorType"]),
          "count": int(binding["descriptorCount"]), "stage_flags": int(binding["stageFlags"])}
         for binding in array_items(layout["LayoutBindings"])]
        for layout in sets
    ]


def command_record(frames):
    record, errors, unavailable = {}, [], []
    for frame in frames:
        name = frame.name() or ""
        if (name.startswith("FRHICommandSetGraphicsPipelineState::Execute")
                or name.startswith("FRHICommandSetShaderParameters<FRHIGraphicsShader>::Execute")):
            record["type"] = name
            field(record, unavailable, "address", lambda: hex(int(frame.read_var("this"))))
            field(record, unavailable, "list_address", lambda: hex(int(frame.read_var("CmdList").address)))
            if "list_address" in record:
                record["list_address_source"] = "command:CmdList"
                field(record, errors, "list_executing", lambda: bool(frame.read_var("CmdList")["bExecuting"]))
            break
    if "list_address" not in record:
        for frame in frames:
            if frame.name() == "FRHICommandListBase::Execute":
                field(record, errors, "list_address", lambda: hex(int(frame.read_var("this"))))
                field(record, errors, "list_executing", lambda: bool(frame.read_var("this")["bExecuting"]))
                record["list_address_source"] = "FRHICommandListBase::Execute:this"
                break
    record["unavailable_fields"] = unavailable
    record["read_errors"] = errors
    return record


def brief_pipeline(pointer):
    record = {"address": hex(int(pointer))}
    if int(pointer):
        record["keys"] = [int(pointer["ShaderKeys"][index]) for index in range(5)]
        record["pixel_shader_address"] = hex(int(pointer["VulkanShaders"][1]))
    return record


def trace_bind_record(frame):
    context = frame.read_var("this")
    pending = context["PendingGfxState"]
    record, errors = {
        "kind": "gfx-bind-applied", "pending_address": hex(int(pending)),
        "context_address": hex(int(context)),
        "pipeline": brief_pipeline(pending["CurrentPipeline"]),
        "submitted_pipeline": brief_pipeline(frame.read_var("Pipeline")),
        "command": command_record(frames_from(frame)),
        "location": frame_record(frame),
    }, []
    field(record, errors, "descriptor_state_address", lambda: hex(int(pending["CurrentState"])))
    record["read_errors"] = errors
    return record


def trace_parameters_record(frame):
    context, shader = frame.read_var("this"), frame.read_var("Shader")
    if "FRHIGraphicsShader" not in str(shader.type):
        return None
    frequency = int(shader["Frequency"])
    if frequency != 3:
        return None
    pipeline = brief_pipeline(context["PendingGfxState"]["CurrentPipeline"])
    if pipeline.get("keys", [None, None])[1] != 0 or pipeline.get("pixel_shader_address") != "0x0":
        return None
    record, errors = {
        "kind": "gfx-parameters", "context_address": hex(int(context)),
        "shader_rhi_address": hex(int(shader)),
        "pipeline": pipeline, "shader_frequency": frequency,
        "selection": "pixel-parameters-on-missing-pixel-stage",
        "command": command_record(frames_from(frame)),
    }, []
    record["read_errors"] = errors
    return record


def graphics_record(frame):
    record, errors = {}, []
    frame.select()
    field(record, errors, "stage", lambda: bounded(frame.read_var("Stage"), 4, "graphics stage"))
    field(record, errors, "buffer_index", lambda: bounded(frame.read_var("BufferIndex"), 127, "UB index"))
    field(record, errors, "requested_shader", lambda: shader_record(frame.read_var("Shader"), errors))
    field(record, errors, "requested_shader_rhi_address", lambda: hex(int(frame.read_var("ShaderRHI"))))
    context = frame.read_var("this")
    record["context_address"] = hex(int(context))
    pending = context.dereference()["PendingGfxState"]
    record["pending_address"] = hex(int(pending))
    pipeline, descriptor = pending["CurrentPipeline"], pending["CurrentState"]
    record["pipeline_address"], record["descriptor_state_address"] = hex(int(pipeline)), hex(int(descriptor))
    if int(pipeline):
        record["pipeline_keys"] = [int(pipeline["ShaderKeys"][index]) for index in range(5)]
        record["pipeline_shaders"] = [shader_record(pipeline["VulkanShaders"][index], errors)
                                      for index in range(5)]
        field(record, errors, "pipeline_layout_address", lambda: hex(int(pipeline["Layout"])))
        field(record, errors, "pipeline_descriptor_layout_address",
              lambda: hex(int(pipeline["Layout"]["DescriptorSetLayout"].address)))
        field(record, errors, "pipeline_bindless", lambda: bool(pipeline["bUsesBindless"]))
    if int(descriptor):
        field(record, errors, "descriptor_layout_address", lambda: hex(int(descriptor["DescriptorSetsLayout"])))
        field(record, errors, "descriptor_sets", lambda: layout_record(descriptor["DescriptorSetsLayout"]))
        field(record, errors, "descriptor_bindless", lambda: bool(descriptor["bUseBindless"]))
        field(record, errors, "descriptor_pipeline_address", lambda: hex(int(descriptor["GfxPipeline"])))
    stage, index = record.get("stage"), record.get("buffer_index")
    keys, sets = record.get("pipeline_keys"), record.get("descriptor_sets")
    requested = record.get("requested_shader", {})
    if stage is not None and keys is not None and "key" in requested:
        record["shader_key_matches"] = requested["key"] == keys[stage]
    if stage is not None and index is not None and sets is not None:
        record["descriptor_index_in_bounds"] = stage < len(sets) and index < len(sets[stage])
    if "descriptor_pipeline_address" in record:
        record["descriptor_pipeline_matches"] = record["pipeline_address"] == record["descriptor_pipeline_address"]
    if "descriptor_layout_address" in record and "pipeline_descriptor_layout_address" in record:
        record["descriptor_layout_matches"] = record["descriptor_layout_address"] == record["pipeline_descriptor_layout_address"]
    record["read_errors"] = errors
    return record


def compute_record(frame, root):
    record, errors = {}, []
    frame.select()
    field(record, errors, "shader", lambda: shader_record(frame.read_var("Shader"), errors))
    try:
        info = frame.read_var("PipelineInfo")
        record["entry"] = c_string(info["stage"]["pName"], 128)
        for name, value in (("flags", info["flags"]), ("stage_flags", info["stage"]["flags"]),
                            ("module_handle", info["stage"]["module"]), ("layout_handle", info["layout"]),
                            ("base_pipeline_index", info["basePipelineIndex"])):
            record[name] = int(value)
        record["stage_pnext"] = hex(int(info["stage"]["pNext"]))
    except (gdb.error, ValueError) as error:
        errors.append(f"PipelineInfo: {error}")
    # The shader's stored container is not necessarily the patched module submitted to Vulkan.
    try:
        container = frame.read_var("Shader")["SpirvContainer"]
        pointer, count = array_data(container["SpirvCode"], 16 * 1024 * 1024)
        data = gdb.selected_inferior().read_memory(int(pointer), count).tobytes() if count else b""
        name = "shader-container.bin"
        (root / name).write_bytes(data)
        record["stored_container"] = {
            "file": name, "bytes": count, "sha256": hashlib.sha256(data).hexdigest(),
            "uncompressed_size": int(container["UncompressedSizeBytes"]),
            "exact_submitted_module": False,
        }
    except (gdb.error, ValueError) as error:
        errors.append(f"stored_container: {error}")
    record["exact_api_inputs"] = False
    record["read_errors"] = errors
    return record


class CaptureState:
    def __init__(self):
        self.root = Path(os.environ["CARLA_UE_FAULT_CAPTURE_DIR"])
        self.timer = None
        self.pending_assertion = None
        self.trace_enabled = os.environ.get("CARLA_UE_FAULT_TRACE_GFX", "0") == "1"
        self.report = {
            "schema_version": 1, "capture_complete": False, "runtime_acceptance": False,
            "creation_api_hooks": False, "inferior_function_calls": False,
            "stop_reason": None, "assertion": None, "signal": None,
            "graphics_binding": None, "compute_creation": None, "errors": [], "threads": [],
            "graphics_command": None,
            "graphics_trace": {"enabled": self.trace_enabled, "event_count": 0, "dropped_events": 0,
                               "events": [], "errors": [],
                               "selection": "all-applied-gfx-binds-and-missing-pixel-stage-parameters"},
            "scope": "first Vulkan check or fatal signal; debugger scheduling differs from uninstrumented execution",
        }

    def write(self):
        (self.root / "fault-capture.json").write_text(json.dumps(self.report, indent=2, sort_keys=True) + "\n")

    def capture(self, reason, assertion=None, fault_signal=None):
        if self.report["stop_reason"]:
            return
        if self.timer:
            self.timer.cancel()
        self.report.update(stop_reason=reason, assertion=assertion, signal=fault_signal)
        selected = gdb.selected_thread()
        self.report["fault_thread"] = {"num": selected.num, "name": selected.name, "ptid": list(selected.ptid)}
        frames = frames_from(gdb.newest_frame())
        for frame in frames:
            name = frame.name() or ""
            if "FVulkanCommandListContext::RHISetShaderUniformBuffer" in name:
                try:
                    parameter = frame.read_var("ShaderRHI")
                    if "FRHIGraphicsShader" not in str(parameter.type):
                        continue
                except gdb.error as error:
                    self.report["errors"].append(f"graphics overload identity: {error}")
                    continue
                field(self.report, self.report["errors"], "graphics_binding", lambda: graphics_record(frame))
                self.report["graphics_command"] = command_record(frames)
                break
        for frame in frames:
            if "CreateComputePipelineFromShader" in (frame.name() or ""):
                field(self.report, self.report["errors"], "compute_creation",
                      lambda: compute_record(frame, self.root))
                break
        threads = sorted(gdb.selected_inferior().threads(),
                         key=lambda thread: thread_priority(thread, selected, gdb.selected_inferior().pid))
        for thread in threads[:8]:
            try:
                thread.switch()
                self.report["threads"].append({
                    "num": thread.num, "name": thread.name, "ptid": list(thread.ptid),
                    "frames": [frame_record(frame) for frame in frames_from(gdb.newest_frame())],
                })
            except gdb.error as error:
                self.report["errors"].append(f"thread {thread.num}: {error}")
        if selected.is_valid():
            selected.switch()
        self.report["capture_complete"] = bool(self.report["threads"]) and reason in ("vulkan-check", "fatal-signal")
        self.write()

    def finish(self):
        if not self.report["stop_reason"] and gdb.selected_inferior().threads():
            self.capture("observation-ended")
        if self.timer:
            self.timer.cancel()
        self.write()


class FirstVulkanCheck(gdb.Breakpoint):
    def __init__(self, state):
        super().__init__("FDebug::CheckVerifyFailedImpl2", internal=True)
        self.state = state

    def stop(self):
        try:
            frame = gdb.newest_frame()
            file = c_string(frame.read_var("File"), 1024)
            if "/VulkanRHI/" not in file:
                return False
            assertion = {"file": file, "line": int(frame.read_var("Line")),
                         "expression": c_string(frame.read_var("Expr"), 2048)}
            self.state.pending_assertion = assertion
            if self.state.timer:
                self.state.timer.cancel()
        except (gdb.error, ValueError) as error:
            self.state.report["errors"].append(f"check capture: {error}")
            self.state.write()
        return True


class GraphicsTrace(gdb.Breakpoint):
    def __init__(self, state, kind):
        location = ("VulkanPipelineState.cpp:560" if kind == "bind"
                    else "FVulkanCommandListContext::RHISetShaderParameters")
        super().__init__(location, internal=True)
        self.state, self.kind = state, kind

    def stop(self):
        trace = self.state.report["graphics_trace"]
        try:
            frame = gdb.newest_frame()
            record = trace_bind_record(frame) if self.kind == "bind" else trace_parameters_record(frame)
            if record is None:
                return False
            if trace["event_count"] >= 1000000:
                raise ValueError("graphics trace exceeded event budget")
            trace["event_count"] += 1
            record.update(sequence=trace["event_count"], thread_num=gdb.selected_thread().num,
                          observed_monotonic=time.monotonic())
            if len(trace["events"]) == 256:
                trace["events"].pop(0)
                trace["dropped_events"] += 1
            trace["events"].append(record)
        except (gdb.error, ValueError, KeyError) as error:
            trace["errors"].append(f"{self.kind}: {error}")
            self.state.write()
            return True
        return False


def stopped(event):
    # Breakpoint.stop runs before all other threads have necessarily been suspended.
    if state.pending_assertion:
        state.capture("vulkan-check", assertion=state.pending_assertion)
    elif isinstance(event, gdb.SignalEvent):
        if event.stop_signal in ("SIGSEGV", "SIGABRT", "SIGBUS"):
            state.capture("fatal-signal", fault_signal=event.stop_signal)
        elif event.stop_signal == "SIGINT":
            state.capture("observation-ended", fault_signal=event.stop_signal)


def start():
    global state
    state = CaptureState()
    FirstVulkanCheck(state)
    if state.trace_enabled:
        GraphicsTrace(state, "bind")
        GraphicsTrace(state, "parameters")

    gdb.events.stop.connect(stopped)
    state.timer = threading.Timer(int(os.environ["CARLA_UE_FAULT_TIMEOUT"]),
                                  lambda: os.kill(os.getpid(), signal.SIGINT))
    state.timer.daemon = True
    state.timer.start()
    state.write()
