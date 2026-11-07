#!/usr/bin/env python3
"""Bounded local Waymax lab. CPU-only; no downloads or upstream edits."""

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

from lab_support import (
    CONFIG_PATH, REPO, git_snapshot, local_path, read_config,
    require_cpu_environment, sha256, write_file_guide, write_json,
)


def inventory(config, run):
    cpus = require_cpu_environment()
    source = local_path(config["source_root"])
    data = local_path(config["data_file"])
    if not (source / "waymax/__init__.py").is_file():
        raise ValueError(f"Waymax source is missing: {source}")
    if not data.is_file() or data.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("a local regular fixture no larger than 64 MiB is required")
    sys.path.insert(0, str(source))
    import jax
    import tensorflow as tf
    import waymax

    tf.config.set_visible_devices([], "GPU")
    devices = jax.devices()
    if not devices or any(device.platform != "cpu" for device in devices):
        raise ValueError(f"unexpected JAX devices: {devices}")
    if tf.config.get_visible_devices("GPU"):
        raise ValueError("TensorFlow exposes a GPU")
    actual_source = Path(waymax.__file__).resolve().parents[1]
    if actual_source != source:
        raise ValueError(f"Waymax source mismatch: {actual_source} != {source}")
    versions = {
        name: metadata.version(name)
        for name in ("jax", "jaxlib", "tensorflow", "numpy", "optax", "flax", "chex", "matplotlib")
    }
    report = {
        "status": "PASS", "python": sys.executable, "versions": versions,
        "jax_devices": [str(device) for device in devices],
        "jax_backend": jax.default_backend(),
        "tensorflow_visible_gpus": [],
        "cpu_affinity": cpus,
        "waymax_file": str(Path(waymax.__file__).resolve()),
        "source": git_snapshot(source), "project": git_snapshot(REPO),
        "data_file": str(data), "data_bytes": data.stat().st_size,
        "data_sha256": sha256(data),
        "scope": "CPU, local fixture, no network download or simulator rendering",
    }
    write_json(run / "environment.json", report)
    print(f"P1 PASS: backend=cpu, source={source}, fixture={data.stat().st_size} bytes",
          flush=True)
    return tf, data


def inspect_records(tf, data, config, run):
    records = []
    dataset = tf.data.TFRecordDataset([str(data)], num_parallel_reads=1)
    for index, raw in enumerate(dataset.take(config["num_scenarios"]).as_numpy_iterator()):
        example = tf.train.Example.FromString(raw)
        features = example.features.feature
        scenario_id = None
        if "scenario/id" in features and features["scenario/id"].bytes_list.value:
            scenario_id = features["scenario/id"].bytes_list.value[0].decode("utf-8")
        records.append({
            "index": index, "scenario_id": scenario_id,
            "record_sha256": hashlib.sha256(raw).hexdigest(),
            "feature_count": len(features),
            "roadgraph_xyz_values": len(features["roadgraph_samples/xyz"].float_list.value),
            "path_xyz_values": len(features["path_samples/xyz"].float_list.value),
        })
    if len(records) != config["num_scenarios"]:
        raise ValueError(f"fixture has only {len(records)} requested records")
    if len({item["record_sha256"] for item in records}) != len(records):
        raise ValueError("the selected records are duplicates")
    identities = [item["scenario_id"] for item in records]
    if not all(identities) or len(set(identities)) != len(identities):
        raise ValueError("training/evaluation require distinct, nonempty scenario IDs")
    write_json(run / "records.json", records)
    print(f"P1 PASS: {len(records)} distinct local records", flush=True)
    return records


def progress(run, status, phase, value):
    status.setdefault("phases", {})[phase] = value
    status["stage"] = phase
    write_json(run / "status.json", status)


def reload_evaluation(run, output):
    import numpy as np

    config = read_config(run / "config.json")
    output.mkdir(exist_ok=False)
    tf, data = inventory(config, output)
    from lab_runtime import Lab, load_predictor, load_scenarios
    original_environment = json.loads((run / "environment.json").read_text())
    current_environment = json.loads((output / "environment.json").read_text())
    for key in ("data_sha256", "versions", "waymax_file", "source"):
        if original_environment[key] != current_environment[key]:
            raise ValueError(f"reload environment drift: {key}")
    records = inspect_records(tf, data, config, output)
    if records != json.loads((run / "records.json").read_text()):
        raise ValueError("reload records differ from training/evaluation inputs")
    predictor = load_predictor(config, run)
    with np.load(run / "reload-reference.npz", allow_pickle=False) as reference:
        prediction = np.asarray(predictor(reference["features"]))
        if not np.allclose(prediction, reference["prediction"], rtol=0, atol=1e-6):
            raise ValueError("checkpoint predictions differ in the fresh process")
        prediction_difference = float(np.max(np.abs(prediction - reference["prediction"])))
        expected_xy = reference["xy"]
    scenes = load_scenarios(data, config)
    result = Lab(config).rollout(
        scenes[1], "learned", output / "evaluation", predictor,
    )
    trajectory_difference = float(np.max(np.abs(result["xy"] - expected_xy)))
    if not np.allclose(result["xy"], expected_xy, rtol=0, atol=1e-5):
        raise ValueError("checkpoint rollout differs in the fresh process")
    report = {
        "status": "PASS", "fresh_process_pid": os.getpid(),
        "jax_backend": "cpu", "evaluation_scenario_id": records[1]["scenario_id"],
        "prediction_max_abs_difference": prediction_difference,
        "trajectory_max_abs_difference_m": trajectory_difference,
        "checkpoint_sha256": sha256(run / "checkpoint.npz"),
        "scope": "Artifact reload reproducibility, not policy quality or safety",
    }
    write_json(output / "result.json", report)
    write_file_guide(output, "reload", "PASS", include_manifest=False)
    print(f"P5 PASS: checkpoint reloaded in PID {os.getpid()}", flush=True)
    return report


def execute_rollouts(config, run, data, records, stage, status):
    import numpy as np
    from lab_runtime import (
        Lab, check_future_observation_independence, load_scenarios,
        plot_rollouts, train_policy,
    )

    progress(run, status, "P2", "RUNNING")
    scenes = load_scenarios(data, config)
    lab = Lab(config)
    experts = []
    for index, scene in enumerate(scenes):
        experts.append(lab.rollout(scene, "expert", run / f"expert-{index}"))
    write_json(run / "baseline.json", {
        "status": "PASS", "scenario_ids": [item["scenario_id"] for item in records],
        "rollouts": [result["summary"] for result in experts],
        "scope": "Oracle/expert uses next logged frame; not a deployable policy",
    })
    plot_rollouts(scenes[0], [("Expert", experts[0])], run / "baseline.png")
    progress(run, status, "P2", "PASS")
    if stage == "baseline":
        return

    progress(run, status, "P3", "RUNNING")
    brake = lab.rollout(scenes[0], "brake", run / "brake-0")
    differences = np.linalg.norm(experts[0]["xy"] - brake["xy"], axis=-1)
    if not float(differences.max()) > 1e-3:
        raise ValueError("action intervention did not change the simulated trajectory")
    write_json(run / "action-comparison.json", {
        "status": "PASS", "scenario_id": records[0]["scenario_id"],
        "intervention": "constant -2 m/s^2 acceleration and zero steering curvature",
        "max_position_difference_m": float(differences.max()),
        "final_position_difference_m": float(differences[-1]),
        "expert": experts[0]["summary"], "brake": brake["summary"],
        "scope": "Actions change SDC state; NPCs remain logged, not reactive",
    })
    progress(run, status, "P3", "PASS")
    progress(run, status, "P4", "RUNNING")
    write_json(
        run / "observation-check.json",
        check_future_observation_independence(lab, scenes[0]),
    )
    predictor, training = train_policy(config, experts[0], run, records[0], records[1])
    progress(run, status, "P4", "PASS")
    progress(run, status, "P5", "RUNNING")
    learned_train = lab.rollout(scenes[0], "learned", run / "learned-training", predictor)
    learned_eval = lab.rollout(scenes[1], "learned", run / "learned-evaluation", predictor)
    write_json(run / "evaluation.json", {
        "status": "PASS", "engineering_checks_only": True,
        "training_scenario_id": records[0]["scenario_id"],
        "evaluation_scenario_id": records[1]["scenario_id"],
        "training_rollout": learned_train["summary"],
        "heldout_rollout": learned_eval["summary"],
        "heldout_expert": experts[1]["summary"],
        "policy_quality_accepted": False,
    })
    plot_rollouts(
        scenes[0], [("Expert", experts[0]), ("Brake", brake), ("Learned", learned_train)],
        run / "action-comparison.png",
    )
    plot_rollouts(
        scenes[1], [("Expert", experts[1]), ("Learned", learned_eval)],
        run / "heldout-evaluation.png",
    )
    np.savez_compressed(
        run / "reload-reference.npz", features=experts[1]["features"],
        prediction=np.asarray(predictor(experts[1]["features"])), xy=learned_eval["xy"],
    )
    command = [
        sys.executable, str(Path(__file__).resolve()), "--reload-run", str(run),
        "--reload-output", str(run / "reload"),
    ]
    write_json(run / "reload-command.json", command)
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    (run / "reload.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"fresh-process reload failed; see {run / 'reload.log'}")
    reloaded = json.loads((run / "reload/result.json").read_text())
    if reloaded["status"] != "PASS" or reloaded["fresh_process_pid"] == os.getpid():
        raise ValueError("fresh-process reload was not verified")
    progress(run, status, "P5", "PASS")
    write_json(run / "result.json", {
        "status": "PASS", "engineering_checks_only": True,
        "backend": "cpu", "scenario_count": len(scenes),
        "steps_per_rollout": config["rollout_steps"],
        "training_samples": training["training_samples"],
        "initial_training_loss": training["initial_loss"],
        "final_training_loss": training["final_loss"],
        "action_intervention_max_difference_m": float(differences.max()),
        "checkpoint_sha256": sha256(run / "checkpoint.npz"),
        "fresh_process": reloaded,
        "policy_quality_accepted": False,
        "excluded": ["GPU", "raw sensors", "reactive NPCs", "V-Max RL", "Autoware", "safety validation"],
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("inventory", "baseline", "all"), default="all")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--reload-run", type=Path)
    parser.add_argument("--reload-output", type=Path)
    args = parser.parse_args()
    require_cpu_environment()
    if args.reload_run:
        run = args.reload_run.resolve()
        if not run.is_relative_to((REPO / "artifacts/waymax").resolve()):
            raise ValueError("reload must select an existing lab artifact directory")
        if args.reload_output:
            output = args.reload_output.resolve()
            if output.parent != run or not output.name.startswith("reload"):
                raise ValueError("reload output must be a new reload directory under the run")
        else:
            output = run / ("reload-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        reload_evaluation(run, output)
        print(f"RELOAD_DIR={output}")
        return 0
    if args.reload_output:
        parser.error("--reload-output requires --reload-run")
    config = read_config(args.config)
    root = REPO / "artifacts/waymax"
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run = Path(tempfile.mkdtemp(prefix=f"learning-{timestamp}-", dir=root))
    write_json(run / "config.json", config)
    sources = run / "source"
    sources.mkdir()
    for path in sorted(Path(__file__).parent.iterdir()):
        if path.suffix in (".py", ".sh"):
            shutil.copy2(path, sources / path.name)
    write_json(run / "code.json", {
        path.name: sha256(path) for path in sorted(sources.iterdir())
    })
    write_json(run / "command.json", {
        "argv": sys.argv, "executable": sys.executable,
        "environment": {key: os.environ.get(key) for key in (
            "JAX_PLATFORMS", "JAX_PLATFORM_NAME", "CUDA_VISIBLE_DEVICES",
            "TF_NUM_INTEROP_THREADS", "TF_NUM_INTRAOP_THREADS", "OMP_NUM_THREADS",
        )},
    })
    status = {
        "status": "RUNNING", "stage": "inventory", "run_dir": str(run),
        "process_pid": os.getpid(),
    }
    write_json(run / "status.json", status)
    started = time.monotonic()
    try:
        tf, data = inventory(config, run)
        records = inspect_records(tf, data, config, run)
        progress(run, status, "P1", "PASS")
        if args.stage != "inventory":
            execute_rollouts(config, run, data, records, args.stage, status)
        if sha256(data) != json.loads((run / "environment.json").read_text())["data_sha256"]:
            raise ValueError("fixture changed during the experiment")
        status.update(status="PASS", stage=args.stage)
        return 0
    except Exception as error:
        status.update(status="FAIL", error=f"{type(error).__name__}: {error}")
        (run / "error.txt").write_text(traceback.format_exc())
        traceback.print_exc()
        return 1
    finally:
        status["seconds"] = round(time.monotonic() - started, 3)
        status["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)
        write_json(run / "status.json", status)
        write_file_guide(run, args.stage, status["status"])
        write_json(run / "artifact-manifest.json", {
            "schema_version": 1,
            "files": {
                str(path.relative_to(run)): {"bytes": path.stat().st_size, "sha256": sha256(path)}
                for path in sorted(run.rglob("*")) if path.is_file()
                and path != run / "artifact-manifest.json"
            },
            "scope": "Files produced by this invocation; later manual reload directories are separate",
        })
        print(f"RUN_DIR={run}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
