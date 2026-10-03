#!/usr/bin/env python3
"""Compare the GB10 Town10 gate across the Vulkan diagnostic configurations.

Audit 9.74 recorded that the Khronos validation layer turned the Town10 gate
from FAIL into PASS, and audit 9.75 refuted the first explanation (serialized
driver entry). The remaining candidates are the layer's checks, the
instance/debug-utils creation chain, timing and memory-layout perturbation, so
the next comparison has to be able to tell those apart.

This runner executes the existing gate once per configuration and classifies
each result. It deliberately refuses to treat an environment-level
``vkCreateDevice`` rejection as a product result: such a run is reported as
BLOCKED, the remaining configurations are left NOT-RUN, and the whole
comparison is marked inconclusive. Without that rule a driver-state regression
silently looks like "the diagnostic stopped working" (audit 9.76).
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys

SCHEMA = 1

# Ordered so the control run decides first whether the environment can produce
# an interpretable result at all.
CONFIGS = (
    {
        "name": "control",
        "make_env": {},
        "purpose": "Baseline: expected to reach the NVIDIA SPIR-V compiler crash.",
    },
    {
        "name": "debug-utils",
        "make_env": {"RUNTIME_VULKAN_DEBUG_UTILS": "1"},
        "purpose": "Instance debug-utils extension and messenger only, no layer.",
    },
    {
        "name": "validation",
        "make_env": {"RUNTIME_VULKAN_VALIDATION": "1"},
        "purpose": "Khronos validation layer, the configuration audit 9.74 recorded as PASS.",
    },
    {
        "name": "serialize-driver-calls",
        "make_env": {"GB10_SERIALIZE_DRIVER_CALLS": "1"},
        "purpose": "Diagnostic serialization of the instrumented driver entry points.",
    },
)

DECISION_FIELDS = (
    "Status",
    "Step",
    "Exit code",
    "Mode",
    "Render profile",
    "Server stop code",
    "Vulkan validation",
    "Vulkan debug utils",
    "Client abort",
)


def parse_decision(text: str) -> dict:
    """Parse the ``- Key: value`` lines of a run's decision.md."""
    fields = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        key, separator, value = stripped[2:].partition(":")
        if separator:
            fields[key.strip()] = value.strip()
    return fields


def classify(decision_text: str) -> str:
    """Map a run to PASS / FAIL / BLOCKED.

    Only PASS and FAIL say something about the product. An abort before the
    product rendering path is reachable is BLOCKED: it is an environment or
    precondition problem, not evidence about the shader path.
    """
    fields = parse_decision(decision_text)
    status = fields.get("Status")
    if status == "PASS":
        return "PASS"
    if fields.get("Step") == "preflight":
        return "BLOCKED"
    if fields.get("Client abort") == "device-creation":
        return "BLOCKED"
    return "FAIL"


def blocked_reason(decision_text: str) -> str:
    fields = parse_decision(decision_text)
    if fields.get("Step") == "preflight":
        return "gate preflight failed before the client started"
    if fields.get("Client abort") == "device-creation":
        return "vkCreateDevice was rejected; the product shader path was never reached"
    return ""


def parse_artifact_path(stdout: str) -> str | None:
    """Pick the run directory out of the gate's ``artifacts=...`` line."""
    for word in stdout.split():
        if word.startswith("artifacts="):
            return word.split("=", 1)[1]
    return None


def host_run_dir(container_path: str, artifacts_root: pathlib.Path) -> pathlib.Path | None:
    """Map /artifacts/<...> from the container onto the host artifacts root."""
    parts = pathlib.PurePosixPath(container_path).parts
    if len(parts) < 3 or parts[1] != "artifacts":
        return None
    candidate = artifacts_root.joinpath(*parts[2:])
    return candidate if candidate.is_dir() else None


def sha256_file(path: pathlib.Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_config(config: dict, *, repo_root: pathlib.Path, artifacts_root: pathlib.Path,
               extra_make_args: list[str], timeout: int) -> dict:
    command = ["make", "carla-town10-gb10-runtime", *extra_make_args]
    make_env = dict(config["make_env"])
    for key, value in make_env.items():
        command.append(f"{key}={value}")
    try:
        result = subprocess.run(command, cwd=repo_root, capture_output=True, text=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"config": config["name"], "make_env": make_env, "status": "BLOCKED",
                "run_dir": None, "reason": f"gate did not finish within {timeout}s"}

    stdout = result.stdout or ""
    run_dir = None
    container_path = parse_artifact_path(stdout)
    if container_path:
        run_dir = host_run_dir(container_path, artifacts_root)
    if run_dir is None:
        return {"config": config["name"], "make_env": make_env, "status": "BLOCKED",
                "run_dir": None,
                "reason": "gate did not report a run directory"}

    decision_path = run_dir / "decision.md"
    decision_text = decision_path.read_text(encoding="utf-8") if decision_path.is_file() else ""
    record = {
        "config": config["name"],
        "purpose": config["purpose"],
        "make_env": make_env,
        "run_dir": str(run_dir),
        "decision": parse_decision(decision_text),
        "status": classify(decision_text),
    }
    reason = blocked_reason(decision_text)
    if reason:
        record["reason"] = reason
    return record


def summarize(runs: list[dict]) -> dict:
    blocked = [run for run in runs if run["status"] == "BLOCKED"]
    if blocked:
        return {
            "status": "BLOCKED",
            "reason": blocked[0].get("reason", "first configuration was blocked"),
        }
    statuses = {run["config"]: run["status"] for run in runs}
    if statuses.get("control") != "FAIL":
        return {
            "status": "BLOCKED",
            "reason": f"control did not reproduce the baseline crash (control={statuses.get('control')})",
        }
    return {
        "status": "COMPARABLE",
        "reason": "control reproduced the baseline crash and every configuration produced a verdict",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[2])
    parser.add_argument("--artifact-dir", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[2] / "artifacts/carla",
                        help="CARLA artifacts root used to resolve gate run directories")
    parser.add_argument("--report-dir", type=pathlib.Path, default=None,
                        help="host-writable evidence root; defaults to <repo>/artifacts")
    parser.add_argument("--configs", default="",
                        help="comma separated subset of configuration names")
    parser.add_argument("--make-arg", action="append", default=[],
                        help="extra VAR=value passed to the gate target")
    parser.add_argument("--client-binary", type=pathlib.Path, default=None)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--run-all", action="store_true",
                        help="keep going even when the control run is blocked")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the configuration plan without running the gate")
    arguments = parser.parse_args(argv)

    wanted = [name.strip() for name in arguments.configs.split(",") if name.strip()]
    selected = [config for config in CONFIGS if not wanted or config["name"] in wanted]
    unknown = sorted(set(wanted) - {config["name"] for config in CONFIGS})
    if unknown:
        parser.error(f"unknown configuration(s): {', '.join(unknown)}")

    if arguments.dry_run:
        for config in selected:
            rendered = " ".join(f"{key}={value}" for key, value in config["make_env"].items())
            print(f"{config['name']}: make carla-town10-gb10-runtime {rendered}".rstrip())
        return 0

    binary = arguments.client_binary or (
        arguments.artifact_dir / "cooked-client-full/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal")
    # Gate runs create their directories inside the container, which leaves
    # artifacts/carla owned by root; the report therefore goes to the
    # host-writable artifacts/<test-id>/<utc>/ layout from the README.
    report_root = arguments.report_dir or (arguments.repo_root / "artifacts")
    run_root = report_root / "gb10-vulkan-comparison" / (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    run_root.mkdir(parents=True, exist_ok=True)

    runs: list[dict] = []
    for index, config in enumerate(selected):
        record = run_config(config, repo_root=arguments.repo_root,
                            artifacts_root=arguments.repo_root / "artifacts",
                            extra_make_args=arguments.make_arg, timeout=arguments.timeout)
        runs.append(record)
        print(f"{record['config']}: {record['status']}"
              + (f" ({record.get('reason')})" if record.get("reason") else ""))
        if record["status"] == "BLOCKED" and not arguments.run_all:
            for remainder in selected[index + 1:]:
                runs.append({
                    "config": remainder["name"],
                    "purpose": remainder["purpose"],
                    "make_env": dict(remainder["make_env"]),
                    "run_dir": None,
                    "decision": {},
                    "status": "NOT-RUN",
                    "reason": "skipped because an earlier configuration was blocked",
                })
            break

    report = {
        "schema": SCHEMA,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "client_binary": str(binary),
        "client_binary_sha256": sha256_file(binary),
        "extra_make_args": arguments.make_arg,
        "overall": summarize(runs),
        "runs": runs,
        "scope": "Diagnostic comparison of the GB10 Town10 gate; a run that aborts before the "
                 "product rendering path is not a product result.",
    }
    (run_root / "comparison.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# GB10 Vulkan diagnostic comparison",
        "",
        f"- Overall: {report['overall']['status']}",
        f"- Reason: {report['overall']['reason']}",
        f"- Client binary: {report['client_binary']} ({report['client_binary_sha256']})",
        "",
        "| Configuration | Status | Client abort | Stop code | Run |",
        "| --- | --- | --- | --- | --- |",
    ]
    for run in runs:
        decision = run["decision"]
        run_dir = pathlib.Path(run["run_dir"]).name if run["run_dir"] else "-"
        lines.append("| {} | {} | {} | {} | {} |".format(
            run["config"], run["status"], decision.get("Client abort", "-"),
            decision.get("Server stop code", "-"), run_dir))
    (run_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"comparison={run_root}")
    return 0 if report["overall"]["status"] == "COMPARABLE" else 3


if __name__ == "__main__":
    sys.exit(main())
