#!/usr/bin/env python3
"""Summarize observed NVIDIA symbol resolution, without claiming a root cause."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


PREFIX = "loader-bindings."
BINDING = re.compile(
    r"^\s*(?P<pid>[0-9]+):\s+binding file (?P<source>[^\[\]\r\n]+?) \[[0-9]+\] "
    r"to (?P<target>[^\[\]\r\n]+?) \[[0-9]+\]:\s+(?:normal|protected) symbol "
    r"`(?P<symbol>[^'\r\n]+)'(?: \[[^\r\n]+\])?\s*$"
)
DRIVER = re.compile(r"libnvidia-(?:glvkspirv|eglcore)\.so(?:\.[0-9]+)*")
BINDING_START = re.compile(r"[0-9]+:\s+binding file ")
MAX_FILES = 128
MAX_BYTES = 256 * 1024 * 1024
MAX_LINE = 16384


def binding_records(text):
    starts = list(BINDING_START.finditer(text))
    if not starts:
        if "binding file " in text and ("libnvidia-glvkspirv" in text
                                      or "libnvidia-eglcore" in text):
            raise ValueError("unrecognized driver binding")
        return []
    records = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        fragment = text[start.start():end]
        match = BINDING.fullmatch(fragment)
        if not match:
            if "libnvidia-glvkspirv" in fragment or "libnvidia-eglcore" in fragment:
                raise ValueError("unrecognized driver binding")
            continue
        records.append(match)
    return records


def analyze(root):
    paths = sorted(root.glob(PREFIX + "*"))
    if not paths or len(paths) > MAX_FILES:
        raise ValueError("missing or unbounded loader binding logs")
    command = json.loads((root / "server-command.json").read_text())
    if (not isinstance(command, list) or len(command) < 5
        or command[0] != "timeout" or not isinstance(command[4], str)
        or Path(command[4]).name != "CarlaUnreal"):
        raise ValueError("missing expected server command/executable")
    client = command[4]
    total = 0
    records = []
    counts = Counter()
    collisions = Counter()
    driver_pids = set()
    for path in paths:
        suffix = path.name[len(PREFIX):]
        if not suffix.isdecimal() or path.is_symlink() or not path.is_file():
            raise ValueError(f"unsafe loader log: {path.name}")
        size = path.stat().st_size
        total += size
        if total > MAX_BYTES:
            raise ValueError("loader logs exceed byte budget")
        digest = hashlib.sha256()
        bindings = 0
        with path.open("rb") as stream:
            while line := stream.readline(MAX_LINE + 1):
                if len(line) > MAX_LINE or not line.endswith(b"\n"):
                    raise ValueError(f"oversized or partial loader line: {path.name}")
                digest.update(line)
                text = line.decode("utf-8", errors="strict")
                for match in binding_records(text):
                    if match["pid"] != suffix:
                        raise ValueError(f"binding PID differs from log name: {path.name}")
                    source, target = match["source"], match["target"]
                    if not DRIVER.fullmatch(Path(source).name):
                        continue
                    bindings += 1
                    driver_pids.add(match["pid"])
                    counts[(source, target)] += 1
                    if target == client:
                        collisions[(source, match["symbol"])] += 1
        if path.stat().st_size != size:
            raise ValueError(f"loader log changed during analysis: {path.name}")
        records.append({"file": path.name, "bytes": size,
                        "sha256": digest.hexdigest(), "driver_bindings": bindings})
    if not counts:
        raise ValueError("no NVIDIA compiler/eglcore bindings observed")
    return {
        "schema_version": 1,
        "status": "DRIVER_TO_CLIENT_BINDINGS" if collisions else "NO_DRIVER_TO_CLIENT_BINDINGS",
        "client_executable": client,
        "driver_binding_count": sum(counts.values()),
        "driver_pids": sorted(driver_pids, key=int),
        "driver_to_client": [
            {"source": source, "symbol": symbol, "count": count}
            for (source, symbol), count in sorted(collisions.items())
        ],
        "provider_counts": [
            {"source": source, "provider": target, "count": count}
            for (source, target), count in sorted(counts.items())
        ],
        "evidence": records,
        "scope": ("Observed LD_DEBUG=bindings output only; changes timing. "
                  "No bindings is not proof against all symbol interposition or a runtime PASS."),
        "runtime_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = analyze(args.run_dir)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except (OSError, ValueError) as error:
        parser.exit(2, f"loader bindings rejected: {error}\n")
    print(f"{report['status']} driver_bindings={report['driver_binding_count']} "
          f"driver_to_client_symbols={len(report['driver_to_client'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
