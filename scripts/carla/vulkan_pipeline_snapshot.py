#!/usr/bin/env python3
"""Validate a correlated compute capture and materialize bounded replay inputs."""

import argparse
import hashlib
from pathlib import Path
import re
import shutil

from vulkan_device_snapshot import load, require, validate as validate_device


DESCRIPTORS = {
    0: "sampler", 1: "combined_image_sampler", 2: "sampled_image", 3: "storage_image",
    4: "uniform_texel_buffer", 5: "storage_texel_buffer", 6: "uniform_buffer",
    7: "storage_buffer", 8: "uniform_buffer_dynamic", 9: "storage_buffer_dynamic",
    10: "input_attachment",
}
ALLOCATOR_FIELDS = ("pfnAllocation", "pfnReallocation", "pfnFree",
                    "pfnInternalAllocation", "pfnInternalFree")


def integer(value, minimum, maximum, label):
    require(type(value) is int and minimum <= value <= maximum, f"invalid {label}")


def address(value):
    require(isinstance(value, str) and re.fullmatch(r"0x[0-9a-f]+", value), "invalid pointer address")
    return int(value, 16)


def validate_allocator(value):
    require(isinstance(value, dict) and type(value.get("is_null")) is bool,
            "incomplete allocation callbacks")
    pointer = address(value.get("address"))
    if value["is_null"]:
        require(pointer == 0 and set(value) == {"is_null", "address"},
                "nonempty null allocation callbacks")
        return
    require(pointer > 0, "null allocation callback structure")
    address(value.get("user_data"))
    functions = value.get("functions")
    require(isinstance(functions, dict) and set(functions) == set(ALLOCATOR_FIELDS),
            "incomplete allocation callback functions")
    pointers = []
    for name in ALLOCATOR_FIELDS:
        function = functions[name]
        require(isinstance(function, dict) and
                (function.get("symbol") is None or isinstance(function.get("symbol"), str)),
                "invalid allocation callback symbol")
        pointers.append(address(function.get("address")))
    require(all(pointers[:3]) and bool(pointers[3]) == bool(pointers[4]),
            "missing required allocation callbacks or mismatched notification pair")


def validate_intervention(state, call, allow):
    intervention = state.get("intervention", "none")
    require(intervention in ("none", "allocator-null", "cache-null"), "unknown target intervention")
    require(intervention == "none" or allow, "intervened captures are not unmodified replay inputs")
    if "allocator" not in call:
        require(intervention == "none", "intervention lacks original allocator capture")
        return
    validate_allocator(call["allocator"])
    require(call.get("intervention") == intervention, "target intervention differs from run")
    validate_allocator(call.get("submitted_allocator"))
    require(type(call.get("intervention_applied")) is bool, "missing applied intervention state")
    if intervention == "allocator-null":
        require(state.get("mode") == "observe" and call["submitted_allocator"]["is_null"]
                and call.get("submitted_cache_handle") == call["cache_handle"]
                and call["intervention_applied"] == (not call["allocator"]["is_null"]),
                "allocator intervention changed unexpected inputs")
    elif intervention == "cache-null":
        require(state.get("mode") == "observe" and call.get("submitted_cache_handle") == "0x0"
                and call["submitted_allocator"] == call["allocator"] and call["intervention_applied"],
                "cache intervention changed unexpected inputs")
    else:
        require(call.get("submitted_cache_handle") == call["cache_handle"]
                and call["submitted_allocator"] == call["allocator"] and not call["intervention_applied"],
                "unmodified capture contains changed inputs")


def checked_blob(root, blob, maximum, label):
    require(isinstance(blob, dict), f"missing {label}")
    name = blob.get("file")
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name),
            f"unsafe {label} filename")
    integer(blob.get("bytes"), 0, maximum, f"{label} byte count")
    path = root / name
    require(path.is_file() and not path.is_symlink(), f"missing regular {label} file")
    require(path.stat().st_size == blob["bytes"], f"{label} size/hash mismatch")
    data = path.read_bytes()
    require(len(data) == blob["bytes"] and hashlib.sha256(data).hexdigest() == blob.get("sha256"),
            f"{label} size/hash mismatch")
    return path


def validate(state, root, allow_intervention=False):
    require(isinstance(state, dict) and type(state.get("schema_version")) is int
            and state["schema_version"] == 1, "pipeline capture schema 1 required")
    require(state.get("capture_complete") is True and state.get("errors") == [],
            "pipeline capture is incomplete")
    require(state.get("capture_point") == "vkCreateComputePipelines:entry", "wrong capture point")
    target, call = state.get("target"), state.get("target_call")
    require(isinstance(target, str) and target and isinstance(call, dict), "missing target call")
    require(isinstance(call.get("stack"), list)
            and all(isinstance(name, str) for name in call["stack"])
            and any(target in name for name in call["stack"]), "target not grounded in captured stack")
    require(isinstance(state.get("calls"), list) and call in state["calls"],
            "target is not a recorded compute call")
    require(isinstance(call.get("entry"), str)
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", call["entry"]), "unsafe entry name")
    integer(call.get("flags"), 0, 0xffffffff, "pipeline flags")
    require(not call["flags"] & 4, "derivative pipeline replay is unsupported")
    require(type(call.get("stage_flags")) is int and call["stage_flags"] == 0,
            "unsupported stage flags")
    integer(call.get("required_subgroup_size"), 0, 128, "required subgroup size")
    require(type(call.get("base_pipeline_handle")) is int and call["base_pipeline_handle"] == 0
            and type(call.get("base_pipeline_index")) is int and call["base_pipeline_index"] in (0, -1),
            "unsupported base pipeline")
    integer(call.get("graphics_calls_before"), 0, 100000, "prior graphics call count")
    for name in ("module_handle", "layout_handle", "cache_handle"):
        require(isinstance(call.get(name), str) and re.fullmatch(r"0x[0-9a-f]+", call[name])
                and int(call[name], 16) > 0, f"unobserved {name}")
    validate_intervention(state, call, allow_intervention)
    shader = checked_blob(root, call.get("module"), 16 * 1024 * 1024, "shader")
    require(type(call["module"].get("flags")) is int and call["module"]["flags"] == 0
            and call["module"]["bytes"] >= 20
            and call["module"]["bytes"] % 4 == 0, "unsupported shader module")
    cache = checked_blob(root, call.get("cache_initial"), 64 * 1024 * 1024, "cache initial data")
    integer(call["cache_initial"].get("flags"), 0, 1, "cache flags")
    layout = call.get("layout")
    require(isinstance(layout, dict) and type(layout.get("flags")) is int and layout["flags"] == 0
            and layout.get("push_constants") == [], "unsupported layout flags/push constants")
    sets = layout.get("sets")
    require(isinstance(sets, list) and 1 <= len(sets) <= 8, "invalid descriptor set count")
    for item in sets:
        require(isinstance(item, dict) and type(item.get("flags")) is int and item["flags"] == 0,
                "unsupported descriptor layout flags")
        bindings = item.get("bindings")
        require(isinstance(bindings, list) and 1 <= len(bindings) <= 64, "invalid binding count")
        seen = set()
        for binding in bindings:
            require(isinstance(binding, dict), "invalid binding")
            number = binding.get("binding")
            integer(number, 0, 63, "binding number")
            require(number not in seen, "duplicate binding")
            seen.add(number)
            integer(binding.get("descriptor_type"), 0, 10, "descriptor type")
            integer(binding.get("count"), 1, 1024, "descriptor count")
            require(binding.get("stage_flags") == 32 and binding.get("immutable_samplers") == [],
                    "unsupported stage visibility or immutable sampler")
    validate_device(load(root / "device-create.json"))
    return call, shader, cache


def layout_text(call):
    sets = call["layout"]["sets"]
    lines = [
        "# Captured at the actual vkCreateComputePipelines call.",
        f"set_count={len(sets)}", "push_constants=0",
        f"pipeline_flags={call['flags']}", f"required_subgroup_size={call['required_subgroup_size']}",
    ]
    for index, item in enumerate(sets):
        for binding in item["bindings"]:
            lines.append(f"binding {index} {binding['binding']} "
                         f"{DESCRIPTORS[binding['descriptor_type']]} {binding['count']} compute")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--require-driver-return", action="store_true")
    parser.add_argument("--allow-intervention", action="store_true")
    args = parser.parse_args()
    try:
        call, shader, cache = validate(load(args.run_dir / "pipeline-capture.json"), args.run_dir,
                                       allow_intervention=args.allow_intervention)
        shutil.copyfile(shader, args.run_dir / "shader.spv")
        shutil.copyfile(cache, args.run_dir / "pipeline-cache-initial.bin")
        (args.run_dir / "ue-layout.txt").write_text(layout_text(call))
    except (OSError, ValueError) as error:
        parser.exit(2, f"pipeline capture rejected: {error}\n")
    print(f"validated target entry={call['entry']} shader_sha256={call['module']['sha256']} "
          f"cache_initial_bytes={call['cache_initial']['bytes']} "
          f"graphics_calls_before={call['graphics_calls_before']}")
    if args.require_driver_return and (call.get("driver_return_observed") is not True
                                       or type(call.get("result")) is not int or call["result"] != 0):
        parser.exit(3, "driver observation did not return VK_SUCCESS; input capture remains valid\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
