"""Analyze the bounded same-run Vulkan graphics/compute creation history."""

import argparse
import json
from pathlib import Path
import sys


def parse_line(line):
    return {
        key: value
        for token in line.strip().split()
        for key, separator, value in [token.partition("=")]
        if separator
    }


def stage_values(event):
    stages = []
    for key, value in event.items():
        if not key.startswith("stage") or not key[5:].isdigit():
            continue
        parts = value.split(",", 2)
        if len(parts) != 3:
            raise ValueError("invalid stage record")
        stages.append({
            "index": int(key[5:]),
            "stage": int(parts[0]),
            "module": parts[1],
            "entry": parts[2],
        })
    return sorted(stages, key=lambda item: item["index"])


def analyze(path, target):
    lines = path.read_text().splitlines()
    if len(lines) < 4 or lines[0] != "schema=1" or lines[-1] != "end=1":
        raise ValueError("invalid pipeline history schema")
    header = {}
    for line in lines[1:]:
        if line.startswith("event="):
            break
        header.update(parse_line(line))
    if header.get("phase") != "before_compute_create":
        raise ValueError("history is not a pre-compute snapshot")
    if header.get("target") != target:
        raise ValueError("snapshot target differs from requested shader")
    trigger = header.get("trigger", "selected_target")
    if trigger not in ("selected_target", "checkpoint"):
        raise ValueError("invalid snapshot trigger")
    first = int(header["first_sequence"])
    last = int(header["last_sequence"])
    dropped = int(header["dropped"])
    if first < 1 or last < first or dropped != first - 1:
        raise ValueError("invalid sequence bounds")
    events = []
    modules = {}
    graphics_states = {}
    for line in lines:
        if line.startswith("event="):
            event = parse_line(line)
            event["stages"] = stage_values(event)
            events.append(event)
        elif line.startswith("graphics_state "):
            state = parse_line(line)
            call = state.get("call")
            if not call or call in graphics_states or state.get("replay_ready") != "0":
                raise ValueError("invalid or duplicated graphics fixed-state record")
            graphics_states[call] = state
        elif line.startswith("module="):
            module = parse_line(line)
            if not module.get("module") or not module.get("hash"):
                raise ValueError("invalid module mapping")
            modules[module["module"]] = module["hash"]
    if len(events) > 4096:
        raise ValueError("history exceeds bounded event capacity")
    if len(events) != last - first + 1 or any(
        int(event["event"]) != sequence
        for sequence, event in enumerate(events, first)
    ):
        raise ValueError("history sequence is incomplete")
    begins = {}
    results = []
    for event in events:
        if event.get("phase") == "begin":
            begins[event["call"]] = event
        elif event.get("phase") == "result":
            results.append(event)
        else:
            raise ValueError("unknown pipeline event phase")
    for result in results:
        begins.pop(result["call"], None)
    graphics_begins = {
        event["call"] for event in events
        if event.get("phase") == "begin" and event.get("kind") == "graphics"
    }
    if not set(graphics_states).issubset(graphics_begins):
        raise ValueError("graphics fixed-state lacks a graphics begin")
    target_events = [
        event for event in events if event.get("kind") == "compute"
        and any(stage["entry"] == target for stage in event["stages"])
    ]
    if trigger == "checkpoint" and not any(
        event["phase"] == "begin" for event in target_events
    ):
        raise ValueError("checkpoint target has no compute begin")
    unmatched_graphics = []
    for event in begins.values():
        if event.get("kind") != "graphics":
            continue
        stages = []
        for stage in event["stages"]:
            item = dict(stage)
            item["shader_hash"] = modules.get(stage["module"])
            stages.append(item)
        unmatched_graphics.append({
            "event": int(event["event"]),
            "call": int(event["call"]),
            "thread": int(event["thread"]),
            "cache": event["cache"],
            "layout": event["layout"],
            "render_pass": event["render_pass"],
            "flags": int(event["flags"]),
            "stages": stages,
            "fixed_state": graphics_states.get(event["call"]),
        })
    unmatched_graphics.sort(key=lambda item: item["event"])
    result = {
        "schema_version": 1,
        "status": "CAPTURED_CHECKPOINT" if trigger == "checkpoint" else "CAPTURED_HISTORY",
        "event_count": len(events),
        "result_count": len(results),
        "module_count": len(modules),
        "fixed_state_count": len(graphics_states),
        "unmatched_graphics_count": len(unmatched_graphics),
        "target": target,
        "trigger": trigger,
        "replay_ready": False,
        "target_events": target_events,
        "unmatched_graphics": unmatched_graphics,
        "thread_event_counts": {
            thread: sum(1 for event in events if event.get("thread") == thread)
            for thread in sorted({event.get("thread") for event in events})
        },
        "cache_event_counts": {
            cache: sum(1 for event in events if event.get("cache") == cache)
            for cache in sorted({event.get("cache") for event in events})
        },
        "scope": "same-run Vulkan create history only; not a runtime or sensor acceptance",
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--target", default="main_0000142c_a6b37050")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = analyze(args.history, args.target)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({
            key: result[key]
            for key in ("status", "event_count", "result_count", "unmatched_graphics_count")
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
