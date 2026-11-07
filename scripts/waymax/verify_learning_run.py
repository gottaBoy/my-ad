#!/usr/bin/env python3
"""Verify saved lab evidence without importing JAX or using a GPU."""

import argparse
import csv
import json
import math
from pathlib import Path, PurePosixPath

from lab_support import read_config, sha256


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_manifest(run):
    manifest = json.loads((run / "artifact-manifest.json").read_text())
    require(manifest.get("schema_version") == 1, "unknown artifact manifest schema")
    files = manifest.get("files")
    require(isinstance(files, dict) and bool(files), "empty artifact manifest")
    for name, record in files.items():
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "unsafe artifact path")
        path = run / name
        require(path.resolve().is_relative_to(run.resolve()), "artifact escapes run directory")
        require(path.is_file() and not path.is_symlink(), f"missing/unsafe artifact: {name}")
        require(path.stat().st_size == record["bytes"], f"artifact size drift: {name}")
        require(sha256(path) == record["sha256"], f"artifact hash drift: {name}")
    return files


def verify_run(run):
    run = Path(run).resolve()
    files = verify_manifest(run)
    required = {
        "config.json", "environment.json", "status.json", "records.json", "code.json",
        "baseline.json", "action-comparison.json", "observation-check.json",
        "training.json", "evaluation.json", "result.json", "checkpoint.npz",
        "reload/result.json", "reload/evaluation/trajectory.csv",
        "expert-0/trajectory.csv", "brake-0/trajectory.csv",
    }
    require(required <= set(files), "required evidence is not covered by the manifest")
    load = lambda name: json.loads((run / name).read_text())
    config, status, result = read_config(run / "config.json"), load("status.json"), load("result.json")
    require(status["status"] == result["status"] == "PASS", "run is incomplete or failed")
    require(all(status["phases"].get(f"P{i}") == "PASS" for i in range(1, 6)),
            "a required phase did not pass")
    require(result["backend"] == "cpu", "result is not CPU-only")
    require(result["engineering_checks_only"] and not result["policy_quality_accepted"],
            "engineering result was promoted to policy acceptance")
    environment = load("environment.json")
    require(environment["jax_backend"] == "cpu" and not environment["tensorflow_visible_gpus"],
            "recorded environment is not CPU-only")
    require(sha256(environment["data_file"]) == environment["data_sha256"], "fixture data drift")
    for name, expected in load("code.json").items():
        require(Path(name).name == name and f"source/{name}" in files, "unsafe/unretained source name")
        require(sha256(run / "source" / name) == expected, f"source snapshot mismatch: {name}")
    records = load("records.json")
    identities = [item["scenario_id"] for item in records]
    require(len(identities) == config["num_scenarios"] and all(identities)
            and len(set(identities)) == len(identities), "invalid scenario split")
    training = load("training.json")
    require(training["training_record"] == records[0]
            and training["evaluation_record"] == records[1], "training/evaluation split mismatch")
    require(math.isfinite(training["final_loss"])
            and training["final_loss"] < training["initial_loss"], "loss did not decrease")
    require(sha256(run / "checkpoint.npz") == result["checkpoint_sha256"]
            == training["checkpoint_sha256"], "checkpoint identity mismatch")
    for directory in (
        "expert-0", "expert-1", "brake-0", "learned-training",
        "learned-evaluation", "reload/evaluation",
    ):
        summary = load(f"{directory}/summary.json")
        require(summary["steps"] == config["rollout_steps"], "summary rollout length mismatch")
        for name in config["metrics"]:
            metric = summary["metrics"][name]
            require(metric["valid_frames"] == config["rollout_steps"]
                    and math.isfinite(metric["mean"]), f"invalid SDC metric: {directory}/{name}")
    with (run / "expert-0/trajectory.csv").open() as stream:
        expert = list(csv.DictReader(stream))
    with (run / "brake-0/trajectory.csv").open() as stream:
        brake = list(csv.DictReader(stream))
    require(len(expert) == len(brake) == config["rollout_steps"], "rollout length mismatch")
    difference = max(
        math.hypot(float(a["x_m"]) - float(b["x_m"]), float(a["y_m"]) - float(b["y_m"]))
        for a, b in zip(expert, brake)
    )
    require(difference > 1e-3 and math.isclose(
        difference, result["action_intervention_max_difference_m"], abs_tol=1e-5,
    ), "action effect differs from recorded trajectories")
    reloaded = load("reload/result.json")
    require(reloaded["status"] == "PASS" and reloaded["fresh_process_pid"] != status["process_pid"],
            "reload did not use a fresh process")
    require(reloaded["prediction_max_abs_difference"] <= 1e-6
            and reloaded["trajectory_max_abs_difference_m"] <= 1e-5,
            "reload tolerance exceeded")
    return {
        "status": "PASS", "scope": "Saved CPU lab evidence, not driving-policy acceptance",
        "verified_files": len(files), "scenario_ids": identities,
        "recomputed_action_difference_m": difference,
        "checkpoint_sha256": result["checkpoint_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_run(args.run), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
