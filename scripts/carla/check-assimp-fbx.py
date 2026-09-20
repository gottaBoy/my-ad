#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


def check_metrics(metrics, minimums):
    if not isinstance(metrics, dict):
        raise ValueError("Probe metrics must be a JSON object")
    for key, minimum in minimums.items():
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{key}: expected integer >= {minimum}, got {value!r}")


def check_upstream(path):
    root = ET.parse(path).getroot()
    count = int(root.attrib.get("tests", "0"))
    failures = int(root.attrib.get("failures", "0")) + int(root.attrib.get("errors", "0"))
    cases = root.findall(".//testcase")
    completed = [case for case in cases if case.attrib.get("status") == "run"
                 and case.attrib.get("result") == "completed"]
    if count < 1 or failures or not completed or any(
        case.find("failure") is not None or case.find("error") is not None for case in cases
    ):
        raise ValueError(f"Upstream FBX suite: tests={count}, failures={failures}")
    return len(completed)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", type=Path)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--upstream", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"scope": "Assimp FBX backend only; not Autodesk SDK ABI or Unreal Editor/Cook",
              "status": "FAIL", "cases": []}

    def invoke(name, path, *extra):
        result = subprocess.run([str(args.probe), str(path), *map(str, extra)],
                                capture_output=True, text=True, timeout=60)
        (args.output / f"{name}.stdout.txt").write_text(result.stdout)
        (args.output / f"{name}.stderr.txt").write_text(result.stderr)
        return result

    try:
        report["upstream_tests"] = check_upstream(args.upstream)
        fixtures = (
            ("static", "BlenderCube.fbx", {"meshes": 1, "vertices": 8, "faces": 12}),
            ("materials", "MultiMatId.fbx", {"meshes": 1, "materials": 2}),
            ("animation", "AnimatedCharacter.fbx", {"bones": 1, "weights": 1, "animations": 1, "animation_keys": 1}),
            ("morph", "MorphTargets.fbx", {"morph_targets": 1}),
        )
        for name, filename, minimums in fixtures:
            path = args.fixtures / filename
            result = invoke(name, path)
            if result.returncode != 0:
                raise ValueError(f"{name} import failed: {result.stderr.strip()}")
            metrics = json.loads(result.stdout)
            check_metrics(metrics, {"meshes": 1, "vertices": 1, "faces": 1, **minimums})
            report["cases"].append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **metrics})
        cube = args.fixtures / "BlenderCube.fbx"
        for format_name in ("fbx", "fbxa"):
            exported = args.output / f"roundtrip-{format_name}.fbx"
            result = invoke(f"export-{format_name}", cube, format_name, exported)
            if result.returncode != 0 or not exported.is_file() or exported.stat().st_size == 0:
                raise ValueError(f"{format_name} export failed: {result.stderr.strip()}")
            original = json.loads(result.stdout)
            result = invoke(f"roundtrip-{format_name}", exported)
            if result.returncode != 0:
                raise ValueError(f"{format_name} reimport failed: {result.stderr.strip()}")
            metrics = json.loads(result.stdout)
            check_metrics(metrics, {"meshes": 1, "vertices": 8, "faces": 12})
            if metrics["faces"] != original["faces"]:
                raise ValueError(f"{format_name} changed face count during round trip")
            report["cases"].append({"name": f"roundtrip-{format_name}", **metrics})
        for name, data in (("invalid", b"not an FBX file\n"), ("truncated", cube.read_bytes()[:32])):
            path = args.output / f"{name}.fbx"
            path.write_bytes(data)
            result = invoke(name, path)
            if result.returncode != 1:
                raise ValueError(f"{name} must fail cleanly with code 1, got {result.returncode}")
            report["cases"].append({"name": name, "rejected": True})
        report["status"] = "PASS"
    except (OSError, ValueError, ET.ParseError, subprocess.TimeoutExpired) as error:
        report["error"] = str(error)
    (args.output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
