"""Correlate cache lifetime and overlapping API calls with a faulting pipeline."""

import argparse
import json
from pathlib import Path
import re
import sys

from analyze_vulkan_driver_entries import analyze as analyze_entries, fields

EVENT = re.compile(
    r"event=(\d+) operation=(create|destroy|merge|get_data|graphics|compute|"
    r"(?:module|layout|pass|descriptor)_(?:create|enqueue|destroy)) "
    r"phase=(enter|return) thread=(\d+) cache=(0x[0-9a-f]+) "
    r"other=(0x[0-9a-f]+) result=(-?\d+) bytes=(\d+) end=1"
)


def analyze(root):
    driver = analyze_entries(root)
    if driver["status"] != "CAPTURED_DRIVER_ENTRY" or len(driver["unreturned"]) != 1:
        raise ValueError("unique driver-entry fault required")
    fault = driver["unreturned"][0]
    marker = fields(root / fault["marker"])
    has_submitted_cache_field = "submitted_cache" in marker
    path = root / "cache-lifecycle.txt"
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("missing or oversized cache lifecycle file")
    lines = path.read_text().splitlines()
    if not 1 <= len(lines) <= 12000:
        raise ValueError("missing or unbounded cache lifecycle events")
    pending = {}
    live = set()
    live_objects = {name: set() for name in ("module", "layout", "pass", "descriptor")}
    queued_objects = {name: set() for name in ("module", "layout", "pass", "descriptor")}
    intervals = []
    invalid = []
    creation_count = 0
    for sequence, line in enumerate(lines, 1):
        match = EVENT.fullmatch(line)
        if not match or int(match[1]) != sequence:
            raise ValueError(f"invalid cache event at sequence {sequence}")
        _, operation, phase, thread, cache, other, result, amount = match.groups()
        object_kind, _, object_action = operation.partition("_")
        if object_kind in live_objects and object_action == "enqueue":
            if phase != "return" or cache not in live_objects[object_kind]:
                invalid.append({"sequence": sequence, "reason": "unobserved enqueue",
                                "kind": object_kind, "handle": cache})
            queued_objects[object_kind].add(cache)
            continue
        key = (thread, operation)
        if phase == "enter":
            if key in pending:
                raise ValueError(f"nested {operation} on thread {thread}")
            if operation in ("destroy", "merge", "get_data", "graphics", "compute"):
                if cache != "0x0" and cache not in live:
                    invalid.append({"sequence": sequence, "reason": "unobserved cache", "cache": cache})
            if operation == "merge" and other != "0x0" and other not in live:
                invalid.append({"sequence": sequence, "reason": "unobserved source", "cache": other})
            if operation == "destroy" and any(
                event["cache"] == cache for event in pending.values()
                if event["operation"] in ("graphics", "compute", "merge", "get_data")
            ):
                invalid.append({"sequence": sequence, "reason": "destroy during active use", "cache": cache})
            pending[key] = {
                "sequence": sequence, "operation": operation, "thread": thread,
                "cache": cache,
                "submitted_cache": other if operation in ("graphics", "compute")
                and has_submitted_cache_field else cache,
            }
            if operation in ("graphics", "compute", "get_data", "merge"):
                concurrent = [event for item_key, event in pending.items()
                              if item_key != key and event["cache"] == cache
                              and cache != "0x0" and event["operation"] in
                              ("graphics", "compute", "get_data", "merge")]
                if concurrent:
                    intervals.append({
                        "sequence": sequence, "cache": cache, "operation": operation,
                        "concurrent": sorted({entry["operation"] for entry in concurrent}),
                    })
        else:
            start = pending.pop(key, None)
            if not start:
                raise ValueError(f"unpaired cache return at sequence {sequence}")
            if operation != "create" and object_action != "create" and cache != start["cache"]:
                raise ValueError(f"changed cache handle at sequence {sequence}")
            if operation == "create" and int(result) == 0 and cache != "0x0":
                if cache in live:
                    invalid.append({"sequence": sequence, "reason": "duplicate create", "cache": cache})
                live.add(cache)
                creation_count += 1
            if operation == "destroy" and cache != "0x0":
                live.discard(cache)
            if object_kind in live_objects and object_action == "create" and cache != "0x0":
                if cache in live_objects[object_kind]:
                    invalid.append({"sequence": sequence, "reason": "duplicate object create",
                                    "kind": object_kind, "handle": cache})
                live_objects[object_kind].add(cache)
                queued_objects[object_kind].discard(cache)
            if object_kind in live_objects and object_action == "destroy":
                if cache not in live_objects[object_kind]:
                    invalid.append({"sequence": sequence, "reason": "unobserved object destroy",
                                    "kind": object_kind, "handle": cache})
                live_objects[object_kind].discard(cache)
                queued_objects[object_kind].discard(cache)
    remaining = list(pending.values())
    fault_cache = marker.get("cache")
    match = [entry for entry in remaining if entry["operation"] == fault["kind"]
             and entry["cache"] == fault_cache
             and entry["thread"] == marker.get("thread")]
    if len(match) != 1:
        raise ValueError("fault marker has no matching unreturned cache operation")
    submitted = marker.get("submitted_cache", fault_cache)
    if match[0]["submitted_cache"] != submitted and submitted != "0x0":
        raise ValueError("fault marker and cache operation disagree on submitted cache")
    if has_submitted_cache_field and submitted == "0x0":
        entry_line = lines[match[0]["sequence"] - 1]
        if not EVENT.fullmatch(entry_line) or EVENT.fullmatch(entry_line)[6] != "0x0":
            raise ValueError("null-cache intervention lacks a matching lifecycle event")
    fault_start = match[0]["sequence"]
    fault_handles = {}
    for name in ("module", "layout"):
        if marker.get(name):
            fault_handles[name] = marker[name]
    if fault["kind"] == "graphics":
        if marker.get("render_pass"):
            fault_handles["pass"] = marker["render_pass"]
        for key, value in marker.items():
            if re.fullmatch(r"stage[0-9]+", key):
                parts = value.split(",", 2)
                if len(parts) == 3:
                    fault_handles[f"module_{key}"] = parts[1]
    observed_objects = any(live_objects.values()) or any(
        "_create" in line or "_destroy" in line for line in lines
    )
    fault_objects = {
        name: {"handle": handle, "live": handle in live_objects[name.split("_", 1)[0]],
               "queued": handle in queued_objects[name.split("_", 1)[0]]}
        for name, handle in fault_handles.items()
    } if observed_objects else {}
    missing_fault_objects = [
        name for name, status in fault_objects.items() if not status["live"]
    ]
    fault_cache_live = fault_cache == "0x0" or fault_cache in live
    return {
        "status": ("CACHE_LIFETIME_ANOMALY" if invalid or not fault_cache_live or missing_fault_objects
                   else "CAPTURED_CACHE_HISTORY"),
        "event_count": len(lines),
        "created_caches": creation_count,
        "fault_cache": fault_cache,
        "fault_submitted_cache": submitted,
        "fault_cache_live_at_end": fault_cache_live,
        "fault_operation": match[0],
        "fault_objects": fault_objects,
        "object_lifecycle_observed": observed_objects,
        "missing_fault_objects": missing_fault_objects,
        "pending_operations": remaining,
        "invalid_lifetime_events": invalid,
        "overlapping_uses": intervals,
        "overlaps_on_fault_cache": [entry for entry in intervals if entry["cache"] == fault_cache],
        "overlaps_during_fault": [
            entry for entry in intervals
            if entry["cache"] == fault_cache and entry["sequence"] >= fault_start
        ],
        "scope": "Observed VulkanPipeline.cpp cache calls and selected module/layout/render-pass paths only; overlap is not proof of a data race",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = analyze(args.run_dir)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps({key: report[key] for key in (
            "status", "event_count", "created_caches", "fault_cache",
            "fault_cache_live_at_end", "overlaps_on_fault_cache",
        )}, sort_keys=True))
        return 0 if report["status"] == "CAPTURED_CACHE_HISTORY" else 3
    except (OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
