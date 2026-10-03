"""Validate same-run, serialized Vulkan driver-entry diagnostics."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys


STAGE = re.compile(
    r"stage(\d+)=(\d+),(0x[0-9a-fA-F]+),([A-Za-z0-9_]+) "
    r"hash=([0-9A-F]{40}|unknown) file=(graphics-shaders/module-[0-9]+\.spv|unavailable)"
)
SHADER_PATH = re.compile(r"graphics-shaders/module-[0-9]+\.spv")
RENDER_PASS_PATH = re.compile(r"renderpasses/renderpass-[0-9]+-0x[0-9a-fA-F]+\.txt")
EXECUTION_MODEL = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}


def fields(path):
    if path.stat().st_size > 256 * 1024:
        raise ValueError(f"oversized marker: {path.name}")
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "schema=1" or lines[-1] != "end=1":
        raise ValueError(f"partial marker: {path.name}")
    values = {}
    for line in lines[1:-1]:
        key, separator, value = line.partition("=")
        if separator:
            if key in values:
                raise ValueError(f"duplicate field {key}: {path.name}")
            values[key] = value
    if values.get("phase") != "enter":
        raise ValueError(f"not a driver-entry: {path.name}")
    return values


def spirv_entries(path):
    data = path.read_bytes()
    if len(data) < 20 or len(data) > 2 * 1024 * 1024 or len(data) % 4:
        raise ValueError(f"invalid SPIR-V length: {path}")
    words = struct.unpack(f"<{len(data) // 4}I", data)
    if words[0] != 0x07230203:
        raise ValueError(f"invalid SPIR-V magic: {path}")
    entries = set()
    offset = 5
    while offset < len(words):
        opcode = words[offset] & 0xffff
        length = words[offset] >> 16
        if length == 0 or offset + length > len(words):
            raise ValueError(f"invalid SPIR-V instruction: {path}")
        if opcode == 15:
            if length < 4:
                raise ValueError(f"short SPIR-V entry point: {path}")
            name = data[(offset + 3) * 4:(offset + length) * 4].split(b"\0", 1)[0]
            entries.add((words[offset + 1], name.decode("ascii")))
        offset += length
    return entries, hashlib.sha256(data).hexdigest()


def render_pass_snapshot(run_dir, path_name, handle):
    if path_name == "unavailable":
        return {"status": "unavailable"}
    if not RENDER_PASS_PATH.fullmatch(path_name):
        raise ValueError(f"invalid render-pass path: {path_name}")
    path = run_dir / path_name
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 128 * 1024:
        raise ValueError(f"missing render-pass snapshot: {path_name}")
    data = path.read_bytes()
    lines = data.decode("utf-8").splitlines()
    if not lines or lines[0] != "schema=1" or lines[-1] != "end=1":
        raise ValueError(f"partial render-pass snapshot: {path_name}")
    fields = {}
    for line in lines[1:-1]:
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise ValueError(f"invalid render-pass field: {path_name}")
        fields[key] = value
    if (fields.get("api") not in ("vkCreateRenderPass", "vkCreateRenderPass2KHR")
        or int(fields.get("handle", "0"), 16) != int(handle, 16)
        or fields.get("capture_complete") not in ("0", "1")):
        raise ValueError(f"render-pass identity mismatch: {path_name}")
    attachments = int(fields.get("attachments", "-1"))
    subpasses = int(fields.get("subpasses", "-1"))
    dependencies = int(fields.get("dependencies", "-1"))
    if not (0 <= attachments <= 64 and 1 <= subpasses <= 8 and 0 <= dependencies <= 64):
        raise ValueError(f"invalid render-pass bounds: {path_name}")
    if fields["capture_complete"] == "1":
        for index in range(attachments):
            if len(fields.get(f"attachment{index}", "").split(",")) != 9:
                raise ValueError(f"missing render-pass attachment: {path_name}")
        for index in range(subpasses):
            prefix = f"subpass{index}"
            if f"{prefix}_flags" not in fields or f"{prefix}_bind_point" not in fields:
                raise ValueError(f"missing render-pass subpass: {path_name}")
            for kind, suffix in (("inputs", "input"), ("colors", "color"),
                                 ("preserves", "preserve")):
                count = int(fields.get(f"{prefix}_{kind}", "-1"))
                if not 0 <= count <= 64 or any(
                    f"{prefix}_{suffix}{item}" not in fields for item in range(count)
                ):
                    raise ValueError(f"missing render-pass references: {path_name}")
            if fields.get(f"{prefix}_resolves") == "1" and any(
                f"{prefix}_resolve{item}" not in fields
                for item in range(int(fields[f"{prefix}_colors"]))
            ):
                raise ValueError(f"missing render-pass resolve: {path_name}")
            if fields.get(f"{prefix}_depth") == "1" and f"{prefix}_depth_ref" not in fields:
                raise ValueError(f"missing render-pass depth: {path_name}")
        if any(f"dependency{index}" not in fields for index in range(dependencies)):
            raise ValueError(f"missing render-pass dependency: {path_name}")
    return {"status": "complete" if fields["capture_complete"] == "1" else "incomplete",
            "file": path_name, "api": fields["api"],
            "attachments": attachments, "subpasses": subpasses,
            "dependencies": dependencies, "sha256": hashlib.sha256(data).hexdigest()}


def analyze(run_dir):
    markers = sorted(run_dir.glob("driver-graphics-*.enter.txt"))
    compute = sorted(run_dir.glob("driver-compute-*.enter.txt"))
    if not markers or not compute or len(markers) + len(compute) > 4096:
        raise ValueError("missing or unbounded mixed driver-entry markers")
    cached_spirv = {}
    cached_render_passes = {}
    unreturned = []
    returned = {"graphics": 0, "compute": 0}
    cache_interventions = 0
    for kind, paths in (("graphics", markers), ("compute", compute)):
        for path in paths:
            details = fields(path)
            original_cache = details.get("cache")
            submitted_cache = details.get("submitted_cache", original_cache)
            if (original_cache is not None and
                (not re.fullmatch(r"0x[0-9a-fA-F]+", original_cache)
                 or submitted_cache not in (original_cache, "0x0"))):
                raise ValueError(f"invalid cache submission: {path.name}")
            cache_interventions += (
                original_cache is not None and submitted_cache != original_cache
            )
            result_path = path.with_name(path.name.replace(".enter.txt", ".result.txt"))
            if result_path.exists():
                if result_path.stat().st_size > 4096:
                    raise ValueError(f"oversized result: {result_path.name}")
                result = result_path.read_text().splitlines()
                if result[:2] != ["schema=1", "phase=return"] or result[-1] != "end=1":
                    raise ValueError(f"partial return: {result_path.name}")
                returned[kind] += 1
            if kind == "compute":
                if not result_path.exists():
                    item = {"kind": kind, "marker": path.name,
                            "entry": details.get("entry")}
                    if original_cache is not None:
                        item.update(cache=original_cache, submitted_cache=submitted_cache)
                    unreturned.append(item)
                continue
            if details.get("replay_ready") != "0":
                raise ValueError(f"unexpected replay status: {path.name}")
            if details.get("layout_state") == "unavailable" or details.get("render_pass_state") == "unavailable":
                raise ValueError(f"missing graphics layout or render pass: {path.name}")
            stages = []
            count = int(details.get("stages", "0"))
            if not 1 <= count <= 5:
                raise ValueError(f"invalid stage count: {path.name}")
            for index in range(count):
                match = STAGE.fullmatch(f"stage{index}={details.get(f'stage{index}', '')}")
                if not match or int(match[1]) != index or not SHADER_PATH.fullmatch(match[6]):
                    raise ValueError(f"missing exact stage: {path.name} stage{index}")
                file = run_dir / match[6]
                if not file.is_file() or file.is_symlink():
                    raise ValueError(f"missing exact module: {file}")
                if file not in cached_spirv:
                    cached_spirv[file] = spirv_entries(file)
                entries, digest = cached_spirv[file]
                model = EXECUTION_MODEL.get(int(match[2]))
                if model is None or (model, match[4]) not in entries:
                    raise ValueError(f"entry differs from exact module: {file}")
                stages.append({"stage": int(match[2]), "module": match[3],
                               "entry": match[4], "shader_hash": match[5],
                               "file": match[6], "sha256": digest})
            cache_data = details.get("cache_data", "not_captured")
            if cache_data not in ("null", "not_captured"):
                expected = path.with_name(path.name.replace(".enter.txt", ".cache.bin"))
                recorded = Path(cache_data)
                if (not recorded.is_absolute()
                    or recorded.name != expected.name
                    or recorded.parent.name != run_dir.name
                    or recorded.parent.parent.name != run_dir.parent.name
                    or not expected.is_file() or expected.is_symlink()):
                    raise ValueError(f"unlinked cache bytes: {path.name}")
                if expected.stat().st_size != int(details.get("cache_bytes", "-1")):
                    raise ValueError(f"cache byte count mismatch: {path.name}")
            render_pass_file = details.get("render_pass_file", "unavailable")
            key = (render_pass_file, details.get("render_pass", "0x0"))
            if key not in cached_render_passes:
                cached_render_passes[key] = render_pass_snapshot(run_dir, *key)
            if not result_path.exists():
                item = {"kind": kind, "marker": path.name,
                        "stages": stages, "cache_data": cache_data,
                        "layout": details.get("layout"),
                        "render_pass": details.get("render_pass"),
                        "render_pass_snapshot": cached_render_passes[key]}
                if original_cache is not None:
                    item.update(cache=original_cache, submitted_cache=submitted_cache)
                unreturned.append(item)
    if len(unreturned) > 1:
        raise ValueError("multiple unreturned entries despite mixed serialization")
    return {
        "status": "CAPTURED_DRIVER_ENTRY" if unreturned else "NO_UNRETURNED_ENTRY",
        "graphics_enters": len(markers),
        "graphics_returns": returned["graphics"],
        "compute_enters": len(compute),
        "compute_returns": returned["compute"],
        "cache_intervention_count": cache_interventions,
        "validated_modules": len(cached_spirv),
        "validated_render_pass_snapshots": sum(
            item["status"] == "complete" for item in cached_render_passes.values()),
        "unreturned": unreturned,
        "replay_ready": False,
        "scope": "Same-run marker and module validation; not a driver root-cause or runtime acceptance",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = analyze(args.run_dir)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({key: report[key] for key in (
            "status", "graphics_enters", "graphics_returns",
            "compute_enters", "compute_returns", "validated_modules",
            "validated_render_pass_snapshots")}, sort_keys=True))
        return 0
    except (OSError, ValueError, UnicodeError, struct.error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
