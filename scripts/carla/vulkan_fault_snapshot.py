"""Validate diagnostic evidence without treating a captured fault as runtime PASS."""

import argparse
import json
from pathlib import Path
import sys


def trace_analysis(state, binding):
    trace = state.get("graphics_trace")
    if trace is None:
        return {"status": "not-enabled"}
    enabled = trace.get("enabled")
    if type(enabled) is not bool:
        raise ValueError("invalid graphics trace policy")
    events, count, dropped = trace.get("events"), trace.get("event_count"), trace.get("dropped_events")
    if (not isinstance(events, list) or len(events) > 256 or type(count) is not int
            or type(dropped) is not int or not 0 <= count <= 1000000 or dropped < 0
            or count != len(events) + dropped):
        raise ValueError("invalid graphics trace budget/accounting")
    if not enabled:
        if events or count or trace.get("errors"):
            raise ValueError("disabled graphics trace contains events")
        return {"status": "not-enabled"}
    for offset, event in enumerate(events, dropped + 1):
        if (not isinstance(event, dict) or type(event.get("sequence")) is not int or event["sequence"] != offset
                or event.get("kind") not in ("gfx-bind-applied", "gfx-parameters")):
            raise ValueError("invalid graphics trace chronology")
    result = {"status": "not-correlated", "event_count": count, "retained_events": len(events),
              "dropped_events": dropped, "errors": trace.get("errors", [])}
    command = state.get("graphics_command") or {}
    context, shader = binding.get("context_address"), binding.get("requested_shader_rhi_address")
    address, list_address = command.get("address"), command.get("list_address")
    fault_thread = state.get("fault_thread", {}).get("num")
    if not all(isinstance(value, str) and value != "0x0" for value in (context, shader, list_address)):
        return result
    parameters = [
        event for event in events if event["kind"] == "gfx-parameters"
        and event.get("context_address") == context and event.get("shader_rhi_address") == shader
        and event.get("command", {}).get("list_address") == list_address
        and event.get("thread_num") == fault_thread
        and (not address or not event.get("command", {}).get("address")
             or event["command"]["address"] == address)
    ]
    if not parameters:
        return result
    parameter = parameters[-1]
    binds = [event for event in events if event["kind"] == "gfx-bind-applied"
             and event["sequence"] < parameter["sequence"] and event.get("context_address") == context]
    if not binds:
        return result
    bind = binds[-1]
    stage = binding.get("stage")
    if type(stage) is not int or not 0 <= stage < 5:
        return result
    for event in (bind, parameter):
        pipeline = event.get("pipeline", {})
        keys = pipeline.get("keys")
        if not isinstance(keys, list) or len(keys) != 5 or any(type(key) is not int for key in keys):
            return result
    pipeline = bind["pipeline"]
    bind_list = bind.get("command", {}).get("list_address")
    result.update({
        "status": "correlated", "parameter_sequence": parameter["sequence"], "bind_sequence": bind["sequence"],
        "command_identity_observed": bool(address and parameter.get("command", {}).get("address") == address),
        "correlation_scope": "context-thread-command-list-shader; command node only when explicitly observed",
        "bind_pipeline_address": pipeline["address"], "bind_stage_key": pipeline["keys"][stage],
        "parameter_stage_key": parameter["pipeline"]["keys"][stage],
        "bind_applied_matches_submitted": pipeline == bind.get("submitted_pipeline"),
        "bind_pipeline_matches_fault": (pipeline["address"] == binding.get("pipeline_address")
                                        and pipeline["keys"] == binding.get("pipeline_keys")),
        "parameter_pipeline_matches_fault": (parameter["pipeline"]["address"] == binding.get("pipeline_address")
                                             and parameter["pipeline"]["keys"] == binding.get("pipeline_keys")),
        "bind_command_list": bind_list, "parameter_command_list": list_address,
        "same_command_list": bind_list == list_address if bind_list else None,
        "same_thread": bind.get("thread_num") == parameter.get("thread_num"),
        "read_errors": bind.get("read_errors", []) + bind.get("command", {}).get("read_errors", [])
                       + parameter.get("read_errors", []) + parameter.get("command", {}).get("read_errors", [])
                       + command.get("read_errors", []),
    })
    return result


def analyze(state):
    if state.get("schema_version") != 1 or state.get("capture_complete") is not True:
        raise ValueError("no complete fault capture")
    for key in ("runtime_acceptance", "creation_api_hooks", "inferior_function_calls"):
        if state.get(key) is not False:
            raise ValueError(f"unexpected {key}")
    reason = state.get("stop_reason")
    if reason not in ("vulkan-check", "fatal-signal"):
        raise ValueError("not a Vulkan check or fatal signal")
    thread = state.get("fault_thread")
    threads = state.get("threads")
    if not isinstance(thread, dict) or not isinstance(threads, list):
        raise ValueError("missing fault thread")
    samples = [item for item in threads if item.get("num") == thread.get("num")]
    if len(samples) != 1 or not samples[0].get("frames"):
        raise ValueError("fault thread stack not observed")
    frames = samples[0]["frames"]
    if reason == "vulkan-check":
        assertion = state.get("assertion")
        if not isinstance(assertion, dict) or "/VulkanRHI/" not in assertion.get("file", ""):
            raise ValueError("ungrounded Vulkan check")
        if not assertion.get("expression"):
            raise ValueError("missing failed expression")
    elif state.get("signal") not in ("SIGSEGV", "SIGBUS", "SIGABRT"):
        raise ValueError("not a fatal signal")
    binding = state.get("graphics_binding")
    compute = state.get("compute_creation")
    result = {"fault_bucket": "unclassified", "runtime_acceptance": False,
              "exact_compute_api_inputs": False, "typed_graphics_contract": False}
    if binding:
        stage, requested, keys = binding.get("stage"), binding.get("requested_shader", {}), binding.get("pipeline_keys")
        if type(stage) is int and 0 <= stage <= 4 and isinstance(keys, list) and len(keys) == 5:
            key = requested.get("key")
            if type(key) is int and all(type(item) is int for item in keys):
                matches = key == keys[stage]
                if binding.get("shader_key_matches") is not matches:
                    raise ValueError("inconsistent shader-key conclusion")
                result.update(typed_graphics_contract=True, shader_key_matches=matches,
                              requested_key=key, pending_key=keys[stage])
        result["fault_bucket"] = "ue-graphics-binding"
        sets, index = binding.get("descriptor_sets"), binding.get("buffer_index")
        if type(stage) is int and 0 <= stage <= 4 and type(index) is int and index >= 0 and isinstance(sets, list):
            if not all(isinstance(item, list) for item in sets):
                raise ValueError("invalid descriptor sets")
            in_bounds = stage < len(sets) and index < len(sets[stage])
            if binding.get("descriptor_index_in_bounds") is not in_bounds:
                raise ValueError("inconsistent descriptor-index conclusion")
            result["descriptor_index_in_bounds"] = in_bounds
        for kind, left, right in (
            ("pipeline", "pipeline_address", "descriptor_pipeline_address"),
            ("layout", "pipeline_descriptor_layout_address", "descriptor_layout_address"),
        ):
            if left in binding and right in binding:
                matches = binding[left] == binding[right]
                if binding.get(f"descriptor_{kind}_matches") is not matches:
                    raise ValueError(f"inconsistent descriptor-{kind} conclusion")
                result[f"descriptor_{kind}_matches"] = matches
    elif compute and any("libnvidia-glvkspirv" in (frame.get("library") or "") for frame in frames):
        result["fault_bucket"] = "nvidia-compute-compiler"
    if compute and compute.get("exact_api_inputs") is not False:
        raise ValueError("passive state capture cannot claim exact API module provenance")
    result["graphics_trace"] = trace_analysis(state, binding or {})
    result["read_errors"] = (state.get("errors", []) + (binding or compute or {}).get("read_errors", [])
                             + (state.get("graphics_command") or {}).get("read_errors", []))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = analyze(json.loads((args.run_dir / "fault-capture.json").read_text()))
        (args.run_dir / "fault-analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
