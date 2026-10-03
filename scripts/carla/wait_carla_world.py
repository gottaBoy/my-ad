#!/usr/bin/env python3
"""Wait for a real CARLA world within a bounded deadline, not a log marker."""

import argparse
import importlib
import json
import os
from pathlib import Path
import time

from check_carla_runtime import validate_snapshot, validate_versions


def wait(*, api, host, port, map_suffix, seconds, server_pid, clock=time.monotonic,
         pause=time.sleep, alive=None):
    if alive is None:
        def alive():
            try:
                os.kill(server_pid, 0)
                return True
            except ProcessLookupError:
                return False
    deadline = clock() + seconds
    attempts, error = 0, None
    while clock() < deadline and alive():
        attempts += 1
        try:
            client = api.Client(host, port)
            client.set_timeout(min(2.0, max(0.1, deadline - clock())))
            versions = validate_versions(client.get_client_version(), client.get_server_version())
            world = client.get_world()
            name = world.get_map().name
            if not isinstance(name, str) or name.rsplit("/", 1)[-1] != map_suffix:
                raise ValueError(f"unexpected world map: {name!r}")
            snapshot = validate_snapshot(world.get_snapshot())
            return {"status": "PASS", "attempts": attempts, "versions": versions,
                    "map": name, "snapshot": snapshot,
                    "scope": "RPC world readiness only; not rendering, sensors or shutdown"}
        except (RuntimeError, ValueError) as exception:
            error = str(exception)
            pause(min(1.0, max(0, deadline - clock())))
    return {"status": "FAIL", "attempts": attempts, "error": error,
            "scope": "RPC world readiness only; not rendering, sensors or shutdown"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--server-pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not (1024 <= args.port <= 65532 and 1 <= args.seconds <= 300 and args.server_pid > 0):
        parser.error("invalid readiness limits or server PID")
    result = wait(api=importlib.import_module("carla"), host=args.host, port=args.port,
                  map_suffix=args.map, seconds=args.seconds, server_pid=args.server_pid)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return 0 if result["status"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
