"""Small, dependency-free contracts for the CPU learning lab."""

import hashlib
import json
import os
from pathlib import Path
import subprocess


REPO = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO / "config/waymax/learning.json"
CPU_ENVIRONMENT = {
    "CUDA_VISIBLE_DEVICES": "-1",
    "JAX_PLATFORMS": "cpu",
    "JAX_PLATFORM_NAME": "cpu",
    "MPLBACKEND": "Agg",
}


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require_cpu_environment():
    for key, expected in CPU_ENVIRONMENT.items():
        if os.environ.get(key) != expected:
            raise ValueError(f"{key} must be {expected!r}; use run-cpu.sh")
    if os.environ.get("LD_PRELOAD"):
        raise ValueError("LD_PRELOAD must be unset for this CPU-only experiment")
    cpus = sorted(os.sched_getaffinity(0))
    if not 1 <= len(cpus) <= 4:
        raise ValueError("CPU affinity must contain 1..4 processors")
    return cpus


def validate_config(config):
    allowed = {
        "schema_version", "source_root", "data_file", "num_scenarios",
        "max_num_objects", "max_num_rg_points", "num_paths",
        "num_points_per_path", "init_steps", "rollout_steps", "seed",
        "training_updates", "hidden_units", "learning_rate", "metrics",
    }
    if not isinstance(config, dict) or set(config) != allowed:
        raise ValueError("configuration has missing or unknown fields")
    bounds = {
        "schema_version": (1, 1), "num_scenarios": (2, 4),
        "max_num_objects": (5, 64), "max_num_rg_points": (16, 30000),
        "num_paths": (1, 30), "num_points_per_path": (1, 200),
        "init_steps": (1, 11), "rollout_steps": (2, 80),
        "seed": (0, 2**31 - 1), "training_updates": (1, 1000),
        "hidden_units": (2, 64),
    }
    for name, (low, high) in bounds.items():
        value = config[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{name} must be an integer in {low}..{high}")
    if config["init_steps"] + config["rollout_steps"] > 91:
        raise ValueError("requested rollout exceeds the 91-frame fixture")
    rate = config["learning_rate"]
    if type(rate) not in (int, float) or not 0 < rate <= 0.1:
        raise ValueError("learning_rate must be in (0, 0.1]")
    expected_metrics = {
        "log_divergence", "overlap", "offroad", "kinematic_infeasibility",
    }
    if (not isinstance(config["metrics"], list)
            or set(config["metrics"]) != expected_metrics
            or len(config["metrics"]) != len(expected_metrics)):
        raise ValueError("the four bounded learning metrics must be present exactly once")
    for name in ("source_root", "data_file"):
        if not isinstance(config[name], str) or not config[name]:
            raise ValueError(f"{name} must be a local path")
        if "://" in config[name] or any(c in config[name] for c in "*?[]@"):
            raise ValueError(f"remote paths and globs are not allowed: {name}")
    return config


def read_config(path=CONFIG_PATH):
    return validate_config(json.loads(Path(path).read_text(encoding="utf-8")))


def local_path(value):
    path = Path(value).expanduser()
    return (REPO / path).resolve() if not path.is_absolute() else path.resolve()


def git_snapshot(path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(path), *args], text=True, stderr=subprocess.PIPE,
        ).strip()
    return {
        "root": str(Path(path).resolve()),
        "revision": git("rev-parse", "HEAD"),
        "status": git("status", "--short", "--untracked-files=no").splitlines(),
    }
