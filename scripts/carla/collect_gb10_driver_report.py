#!/usr/bin/env python3
"""Assemble a vendor-escalation evidence report for the GB10 Vulkan failure.

The NVIDIA SPIR-V compiler crash has resisted every UE-side contrast run (see
audit 9.64-9.79), so the next step is a self-contained report that a driver
engineer can act on. This generator collects only evidence that already exists
in the artifact tree, so the report cannot drift from the runs it describes:

* environment and driver version from ``vulkaninfo.log`` and the architecture probe
* per-run verdict, client abort reason and stop code from ``decision.md``
* crash site and innermost UE frames from ``symbolized-crash.txt``
* validation output groups (including the draw-time descriptor violations)
* the documented negative results, so nobody re-tests them

Anything the report cannot find is listed in ``missing`` instead of being
silently omitted.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys

SCHEMA = 1

NEGATIVE_RESULTS = (
    "Identical SPIR-V, descriptor layout, render pass, pipeline cache and device "
    "feature snapshot replay successfully in an isolated GB10 process.",
    "Nulling the submitted pipeline cache does not prevent the crash.",
    "The GPU allocation callbacks are NULL, exactly as in the isolated replay.",
    "Serializing graphics, compute, shader-module, descriptor/layout and cache "
    "entry points behind one lock does not prevent the crash.",
    "Enabling the debug-utils instance extension without the validation layer "
    "does not change the crash.",
    "Driver state is not stable: the same binary that SIGSEGVs can later be "
    "rejected at vkCreateDevice ('Cannot create a Vulkan device') and recover by "
    "itself.",
)

VUID_PATTERN = re.compile(r"VUID-[A-Za-z0-9-]*")
DRIVER_INFO = re.compile(r"driverInfo\s*=\s*(.+)")
DEVICE_NAME = re.compile(r"deviceName\s*=\s*(.+)")


def parse_decision_fields(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            key, separator, value = stripped[2:].partition(":")
            if separator:
                fields[key.strip()] = value.strip()
    return fields


def parse_symbolized_report(text: str) -> dict:
    """Split symbolized-crash.txt into driver frames and UE frames."""
    driver, unreal = [], []
    section = None
    for line in text.splitlines():
        if line.startswith("## Driver frames"):
            section = "driver"
            continue
        if line.startswith("## Symbolized CarlaUnreal frames"):
            section = "unreal"
            continue
        if not line.startswith("- "):
            continue
        value = line[2:].strip()
        if value == "none":
            continue
        if section == "driver":
            driver.append(value)
        elif section == "unreal":
            unreal.append(value)
    return {"driver": driver, "unreal": unreal}


def collect_run(run_dir: pathlib.Path) -> dict:
    decision_path = run_dir / "decision.md"
    decision = parse_decision_fields(decision_path.read_text(encoding="utf-8")) if decision_path.is_file() else {}
    record = {
        "run": run_dir.name,
        "run_dir": str(run_dir),
        "status": decision.get("Status"),
        "client_abort": decision.get("Client abort"),
        "server_stop_code": decision.get("Server stop code"),
        "vulkan_validation": decision.get("Vulkan validation"),
        "vulkan_debug_utils": decision.get("Vulkan debug utils"),
        "crash": None,
        "vuid_groups": [],
    }
    symbolized = run_dir / "symbolized-crash.txt"
    if symbolized.is_file():
        record["crash"] = parse_symbolized_report(symbolized.read_text(encoding="utf-8"))
    log = run_dir / "client-ue.log"
    if log.is_file():
        text = log.read_text(encoding="utf-8", errors="replace")
        record["vuid_groups"] = sorted({match.group(0) for match in VUID_PATTERN.finditer(text)})
        record["device_lost"] = "VK_ERROR_DEVICE_LOST" in text
    return record


def environment_from(run_dirs: list[pathlib.Path]) -> dict:
    for run_dir in run_dirs:
        vulkaninfo = run_dir / "vulkaninfo.log"
        if not vulkaninfo.is_file():
            continue
        text = vulkaninfo.read_text(encoding="utf-8", errors="replace")
        driver = DRIVER_INFO.search(text)
        device = DEVICE_NAME.search(text)
        if driver or device:
            return {
                "device": device.group(1).strip() if device else None,
                "driver_info": driver.group(1).strip() if driver else None,
                "source": str(vulkaninfo),
            }
    return {"device": None, "driver_info": None, "source": None}


def missing_evidence(report: dict) -> list[str]:
    missing = []
    if not report["environment"]["driver_info"]:
        missing.append("driver version was not found in any vulkaninfo.log")
    if not any(run.get("crash") for run in report["runs"]):
        missing.append("no symbolized crash stack; run make carla-symbolize-client-crash")
    if not any(run["status"] == "PASS" for run in report["runs"]):
        missing.append("no passing reference run in the selected set")
    if not any(run.get("device_lost") for run in report["runs"]):
        missing.append("no VK_ERROR_DEVICE_LOST observation in the selected set")
    return missing


def render(report: dict) -> str:
    lines = [
        "# GB10 Vulkan failure - vendor evidence bundle",
        "",
        f"- Generated: {report['generated_utc']}",
        f"- Device: {report['environment']['device']}",
        f"- Driver: {report['environment']['driver_info']}",
        f"- Client binary: {report['client_binary_sha256']}",
        "",
        "## Runs",
        "",
        "| Run | Status | Client abort | Stop code | Crash site | UE frames |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for run in report["runs"]:
        crash = run.get("crash") or {}
        driver = crash.get("driver") or []
        site = driver[0].split(" ", 1)[1] if driver else "-"
        lines.append("| {} | {} | {} | {} | {} | {} |".format(
            run["run"], run["status"] or "-", run["client_abort"] or "-",
            run["server_stop_code"] or "-", site, len(crash.get("unreal") or [])))
    lines += ["", "## Crash stacks", ""]
    for run in report["runs"]:
        crash = run.get("crash")
        if not crash:
            continue
        lines += [f"### {run['run']}", "", "Driver frames (crash site):", ""]
        lines += [f"- {frame}" for frame in crash["driver"]] or ["- none"]
        lines += ["", "Innermost CarlaUnreal frames:", ""]
        lines += [f"- {frame}" for frame in crash["unreal"][:6]] or ["- none"]
        lines.append("")
    lines += ["## Validation findings", ""]
    for run in report["runs"]:
        if run["vuid_groups"]:
            draws = [v for v in run["vuid_groups"] if "CmdDraw" in v]
            lines.append(f"- {run['run']}: {len(run['vuid_groups'])} VUID groups, "
                         f"{len(draws)} draw-time descriptor violations"
                         + (f" (VK_ERROR_DEVICE_LOST observed)" if run.get("device_lost") else ""))
    lines += ["", "## Already excluded", ""]
    lines += [f"- {item}" for item in NEGATIVE_RESULTS]
    lines += ["", "## Missing evidence", ""]
    lines += [f"- {item}" for item in report["missing"]] or ["- none"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=pathlib.Path, action="append", default=[],
                        help="gate run directory; repeatable")
    parser.add_argument("--repo-root", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=pathlib.Path, default=None)
    parser.add_argument("--client-binary", type=pathlib.Path, default=None)
    arguments = parser.parse_args(argv)

    repo_root = arguments.repo_root
    run_dirs = list(arguments.run_dir)
    if not run_dirs:
        parser.error("--run-dir is required (repeat it for each run to include)")

    binary = arguments.client_binary or (
        repo_root / "artifacts/carla/cooked-client-full/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal")
    digest = None
    if binary.is_file():
        import hashlib
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()

    runs = [collect_run(pathlib.Path(run_dir)) for run_dir in run_dirs]
    report = {
        "schema": SCHEMA,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "environment": environment_from([pathlib.Path(r) for r in run_dirs]),
        "client_binary": str(binary),
        "client_binary_sha256": digest,
        "negative_results": list(NEGATIVE_RESULTS),
        "runs": runs,
    }
    report["missing"] = missing_evidence(report)

    output_dir = arguments.output_dir or (repo_root / "artifacts/gb10-driver-report" /
                                          datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    (output_dir / "report.md").write_text(render(report), encoding="utf-8")
    print(render(report))
    print(f"report={output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
