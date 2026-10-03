"""Validate the diagnostic in-memory graphics trace without claiming runtime success."""

import argparse
import json
from pathlib import Path
import sys


def parse_line(line):
    values = {}
    for item in line.strip().split():
        key, separator, value = item.partition("=")
        if separator:
            values[key] = value
    return values


def integer(values, key):
    try:
        return int(values[key], 0)
    except (KeyError, ValueError) as error:
        raise ValueError(f"invalid integer field: {key}") from error


def validate(path):
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "schema=1":
        raise ValueError("invalid trace schema")
    header = parse_line(lines[1]) if len(lines) > 1 else {}
    if header.get("kind") != "uniform_buffer_mismatch":
        raise ValueError("missing mismatch header")
    if not {"context", "pending_state", "pipeline", "shader", "frequency",
            "buffer_index", "shader_key", "pending_key"} <= header.keys():
        raise ValueError("incomplete mismatch header")
    event_count = integer(parse_line(lines[2]), "event_count") if len(lines) > 2 else -1
    if not 0 <= event_count <= 4096:
        raise ValueError("event count outside fixed ring bound")
    event_lines = [line for line in lines if line.startswith("event=")]
    if len(event_lines) != event_count:
        raise ValueError("event count does not match trace")
    events = [parse_line(line) for line in event_lines]
    for index, event in enumerate(events):
        if integer(event, "event") != index:
            raise ValueError("event order is not monotonic")
        if event.get("kind") not in ("pipeline", "graphics_parameters", "uniform_buffer_mismatch"):
            raise ValueError("unknown event kind")
        keys = event.get("keys", "").split(",")
        if len(keys) != 5 or any(not key.isdigit() for key in keys):
            raise ValueError("pipeline key vector is incomplete")
    mismatch_key = integer(header, "shader_key")
    pending_key = integer(header, "pending_key")
    if mismatch_key == pending_key:
        raise ValueError("trace does not contain a mismatch")
    matching_pipeline = [
        event for event in events
        if event.get("kind") == "pipeline"
        and event.get("context") == header["context"]
        and event.get("pipeline") == header["pipeline"]
        and event.get("keys", "").split(",")[1] == str(pending_key)
    ]
    matching_parameters = [
        event for event in events
        if event.get("kind") == "graphics_parameters"
        and event.get("context") == header["context"]
        and event.get("shader") == header["shader"]
        and integer(event, "shader_key") == mismatch_key
    ]
    result = {
        "status": "PASS",
        "runtime_acceptance": False,
        "trace_events": event_count,
        "mismatch_shader_key": mismatch_key,
        "mismatch_pending_key": pending_key,
        "matching_pipeline_events": len(matching_pipeline),
        "matching_parameter_events": len(matching_parameters),
        "last_events": events[-8:],
        "scope": "direct diagnostic memory trace only; no Town10/tick/sensor acceptance",
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate(args.trace)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
