#!/usr/bin/env python3
"""Symbolize the CarlaUnreal frames of a GB10 client crash.

The staged client is built with ``-NoDumpSyms``, so UE's own crash handler
prints ``CarlaUnreal!UnknownFunction(0x...)`` for every frame. The addresses it
prints *first* are the absolute program counters; those are the ones to feed to
``llvm-symbolizer`` together with ``CarlaUnreal.debug``. The value inside the
parentheses is a module-relative offset that does not match the debug binary and
symbolizes to unrelated functions, so it must not be used.

Only the frames after the ``Critical error`` marker are the crash stack; the
earlier ``[Callstack]`` lines belong to unrelated ensures.

Run this in the native ARM64 toolchain container, where the debug binary and
``llvm-symbolizer`` are available.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys

CRASH_MARKER = "Critical error"
CLIENT_FRAME = re.compile(r"(0x[0-9a-f]{16})\s+CarlaUnreal!")
DRIVER_FRAME = re.compile(r"(0x[0-9a-f]{16})\s+(libnvidia-[^\s!]*|libc\.so\.6)!")
# -CarlaVulkanValidationStackTrace makes the Vulkan debug messenger dump the
# caller of a matching validation message; FPlatformStackWalk prints
# "LogCore: 0x<addr> Dladdr: ..." because the staged client has no .sym file.
VALIDATION_MARKER = "CARLA diagnostic: validation call site for "
VALIDATION_FRAME = re.compile(r"LogCore: (0x[0-9a-f]{16}) ")


def crash_tail(text: str) -> str:
    """Return everything after the last crash banner."""
    index = text.rfind(CRASH_MARKER)
    return text[index:] if index >= 0 else ""


def extract_client_frames(text: str) -> list[int]:
    """Absolute program counters of the CarlaUnreal frames in the crash stack."""
    return [int(match.group(1), 16) for match in CLIENT_FRAME.finditer(crash_tail(text))]


def extract_driver_frames(text: str) -> list[tuple[int, str]]:
    """Driver and libc frames of the crash stack, kept as context."""
    return [(int(match.group(1), 16), match.group(2))
            for match in DRIVER_FRAME.finditer(crash_tail(text))]


def extract_validation_stacks(text: str) -> list[dict]:
    """Captured validation call sites: one entry per messenger stack dump."""
    stacks = []
    for block in text.split(VALIDATION_MARKER)[1:]:
        name = block.split("\n", 1)[0].strip()
        body = block.split("\n--", 1)[0]
        stacks.append({
            "vuid": name,
            "addresses": [int(match.group(1), 16)
                          for match in VALIDATION_FRAME.finditer(body)],
        })
    return stacks


def symbolize(addresses: list[int], *, debug_binary: pathlib.Path,
              symbolizer: pathlib.Path) -> list[str]:
    """Symbolize absolute program counters into ``function`` / ``file:line`` pairs."""
    if not addresses:
        return []
    if not debug_binary.is_file():
        raise SystemExit(f"debug binary is missing: {debug_binary}")
    if not symbolizer.is_file():
        raise SystemExit(f"symbolizer is missing: {symbolizer}")
    payload = "\n".join(hex(address) for address in addresses) + "\n"
    result = subprocess.run(
        [str(symbolizer), f"--obj={debug_binary}", "--functions", "--demangle"],
        input=payload, capture_output=True, text=True, check=True)
    return [line for line in result.stdout.splitlines() if line.strip()]


def render(run_dir: pathlib.Path, addresses: list[int], driver_frames: list[tuple[int, str]],
           symbolized: list[str]) -> str:
    lines = [
        "# GB10 client crash symbolization",
        "",
        f"- Run directory: {run_dir}",
        f"- CarlaUnreal frames: {len(addresses)}",
        "",
        "## Driver frames (crash site)",
        "",
    ]
    lines += [f"- {hex(address)} {name}" for address, name in driver_frames] or ["- none"]
    lines += ["", "## Symbolized CarlaUnreal frames (outermost last)", ""]
    lines += [f"- {line}" for line in symbolized] or ["- none"]
    return "\n".join(lines) + "\n"


def render_validation(run_dir: pathlib.Path, stacks: list[dict], symbolized: list[str],
                      vuid_counts: dict) -> str:
    lines = [
        "# GB10 validation call site",
        "",
        f"- Run directory: {run_dir}",
        f"- Captured stack dumps: {len(stacks)}",
        "",
        "## Captured message ids",
        "",
    ]
    lines += [f"- {name}: {count}" for name, count in sorted(vuid_counts.items())] or ["- none"]
    lines += ["", "## First captured call site", ""]
    lines += [f"- {line}" for line in symbolized] or ["- none"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=pathlib.Path,
                        default=os.environ.get("CARLA_CRASH_RUN_DIR") or None,
                        help="gate run directory containing client-ue.log")
    parser.add_argument("--mode", choices=("crash", "validation-call-site"), default="crash",
                        help="crash stack, or the call sites captured by "
                             "-CarlaVulkanValidationStackTrace")
    parser.add_argument("--client-log", type=pathlib.Path, default=None,
                        help="explicit client-ue.log path; defaults to <run-dir>/client-ue.log")
    parser.add_argument("--debug-binary", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get(
                            "CARLA_DEBUG_BINARY",
                            "/workspace/carla/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal.debug")))
    parser.add_argument("--symbolizer", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get("CARLA_SYMBOLIZER",
                                                            "/usr/bin/llvm-symbolizer-18")))
    parser.add_argument("--stdout-only", action="store_true",
                        help="do not write the report into the run directory")
    arguments = parser.parse_args(argv)

    if arguments.run_dir is None:
        parser.error("--run-dir or CARLA_CRASH_RUN_DIR is required")
    run_dir = arguments.run_dir
    client_log = arguments.client_log or (run_dir / "client-ue.log")
    if not client_log.is_file():
        parser.error(f"client log is missing: {client_log}")

    text = client_log.read_text(encoding="utf-8", errors="replace")

    if arguments.mode == "validation-call-site":
        stacks = extract_validation_stacks(text)
        if not stacks:
            print(f"{run_dir.name}: no validation call sites captured; run the gate with "
                  f"RUNTIME_VULKAN_VALIDATION_STACK_TRACE=<vuid substring>")
            return 0
        counts = {}
        for stack in stacks:
            counts[stack["vuid"]] = counts.get(stack["vuid"], 0) + 1
        symbolized = symbolize(stacks[0]["addresses"], debug_binary=arguments.debug_binary,
                               symbolizer=arguments.symbolizer)
        report = render_validation(run_dir, stacks, symbolized, counts)
        if not arguments.stdout_only:
            (run_dir / "symbolized-validation-call-site.txt").write_text(report, encoding="utf-8")
        print(report)
        return 0

    if CRASH_MARKER not in text:
        print(f"{run_dir.name}: no critical error in {client_log.name}; nothing to symbolize")
        return 0

    addresses = extract_client_frames(text)
    driver_frames = extract_driver_frames(text)
    symbolized = symbolize(addresses, debug_binary=arguments.debug_binary,
                           symbolizer=arguments.symbolizer)
    report = render(run_dir, addresses, driver_frames, symbolized)
    if not arguments.stdout_only:
        (run_dir / "symbolized-crash.txt").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
