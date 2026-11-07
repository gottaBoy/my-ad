import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "scripts/waymax/lab_support.py"
SPEC = importlib.util.spec_from_file_location("waymax_lab_support", SUPPORT)
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "waymax_lab_verify", ROOT / "scripts/waymax/verify_learning_run.py",
)
verifier = importlib.util.module_from_spec(VERIFY_SPEC)
with mock.patch.dict(sys.modules, {"lab_support": support}):
    VERIFY_SPEC.loader.exec_module(verifier)


class WaymaxLabTest(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config/waymax/learning.json").read_text())

    def test_default_is_a_small_local_experiment(self):
        support.validate_config(self.config)
        self.assertEqual(2, self.config["num_scenarios"])
        self.assertEqual(80, self.config["rollout_steps"])
        self.assertEqual(32, self.config["max_num_objects"])
        self.assertNotIn("://", self.config["data_file"])

    def test_rejects_missing_and_unknown_configuration(self):
        for key in ("source_root", "training_updates", "seed"):
            invalid = copy.deepcopy(self.config)
            del invalid[key]
            with self.assertRaises(ValueError):
                support.validate_config(invalid)
        invalid = dict(self.config, use_gpu=True)
        with self.assertRaises(ValueError):
            support.validate_config(invalid)

    def test_rejects_remote_data_and_globs(self):
        for path in ("gs://bucket/data", "https://host/file", "file@1000", "*.tfrecord"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                support.validate_config(dict(self.config, data_file=path))

    def test_rejects_large_or_invalid_resource_settings(self):
        for key, value in (
            ("num_scenarios", 100), ("max_num_objects", 128),
            ("max_num_objects", 4), ("rollout_steps", 1000),
            ("hidden_units", 1024), ("training_updates", 100000),
            ("seed", True), ("learning_rate", float("nan")),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                support.validate_config(dict(self.config, **{key: value}))

    def test_required_metrics_cannot_be_silently_removed(self):
        for values in ([], ["overlap"] * 4, ["overlap", "not-a-metric"]):
            with self.assertRaises(ValueError):
                support.validate_config(dict(self.config, metrics=values))

    def test_cpu_guard_accepts_only_bounded_cpu_configuration(self):
        with mock.patch.dict(os.environ, support.CPU_ENVIRONMENT, clear=True):
            with mock.patch.object(os, "sched_getaffinity", return_value={2, 3}):
                self.assertEqual([2, 3], support.require_cpu_environment())
            with mock.patch.object(os, "sched_getaffinity", return_value=set(range(20))):
                with self.assertRaises(ValueError):
                    support.require_cpu_environment()

    def test_cpu_guard_rejects_gpu_and_preloaded_driver(self):
        for values in (
            dict(support.CPU_ENVIRONMENT, JAX_PLATFORMS="cuda"),
            dict(support.CPU_ENVIRONMENT, CUDA_VISIBLE_DEVICES="0"),
            dict(support.CPU_ENVIRONMENT, LD_PRELOAD="/tmp/driver.so"),
        ):
            with mock.patch.dict(os.environ, values, clear=True):
                with mock.patch.object(os, "sched_getaffinity", return_value={0, 1}):
                    with self.assertRaises(ValueError):
                        support.require_cpu_environment()

    def test_json_never_encodes_nonfinite_metrics_as_valid_results(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            support.write_json(path, {"valid": None})
            self.assertEqual({"valid": None}, json.loads(path.read_text()))
            with self.assertRaises(ValueError):
                support.write_json(path, {"loss": float("nan")})
            self.assertEqual({"valid": None}, json.loads(path.read_text()))

    def test_data_hash_changes_on_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture"
            path.write_bytes(b"fixture-v1")
            original = support.sha256(path)
            path.write_bytes(b"fixture-v2")
            self.assertNotEqual(original, support.sha256(path))

    def test_cpu_guard_runs_before_heavy_imports(self):
        environment = os.environ.copy()
        environment.pop("JAX_PLATFORMS", None)
        result = subprocess.run(
            ["python3", str(ROOT / "scripts/waymax/learning_lab.py"), "--stage", "inventory"],
            env=environment, capture_output=True, text=True,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("use run-cpu.sh", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_wrapper_rejects_resource_expansion_before_python_launch(self):
        environment = dict(os.environ, WAYMAX_CPU_COUNT="20", WAYMAX_PYTHON="/usr/bin/python3")
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/waymax/run-cpu.sh"), "--stage", "inventory"],
            env=environment, capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)
        self.assertIn("WAYMAX_CPU_COUNT", result.stderr)

    def test_learning_entry_does_not_launch_upstream_trainers(self):
        text = (ROOT / "scripts/waymax/learning_lab.py").read_text()
        self.assertNotIn("vmax.scripts.training", text)
        self.assertIn("fresh-process reload", text)
        wrapper = (ROOT / "scripts/waymax/run-cpu.sh").read_text()
        for token in ("CUDA_VISIBLE_DEVICES=-1", "JAX_PLATFORMS=cpu", "taskset", "timeout"):
            self.assertIn(token, wrapper)

    def make_manifest(self, root):
        payload = root / "checkpoint.bin"
        payload.write_bytes(b"small-checkpoint")
        support.write_json(root / "artifact-manifest.json", {
            "schema_version": 1,
            "files": {"checkpoint.bin": {
                "bytes": payload.stat().st_size, "sha256": support.sha256(payload),
            }},
        })
        return payload

    def test_saved_artifacts_verify_without_jax(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_manifest(root)
            self.assertEqual({"checkpoint.bin"}, set(verifier.verify_manifest(root)))

    def test_same_size_checkpoint_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self.make_manifest(root)
            payload.write_bytes(b"SMALL-checkpoint")
            with self.assertRaisesRegex(ValueError, "hash drift"):
                verifier.verify_manifest(root)

    def test_missing_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_manifest(root).unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                verifier.verify_manifest(root)

    def test_manifest_cannot_escape_run_or_claim_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for files in ({}, {"../outside": {"bytes": 0, "sha256": "0" * 64}}):
                support.write_json(root / "artifact-manifest.json", {
                    "schema_version": 1, "files": files,
                })
                with self.assertRaises(ValueError):
                    verifier.verify_manifest(root)

    def test_symlink_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = self.make_manifest(root)
            target = root / "other-checkpoint.bin"
            target.write_bytes(payload.read_bytes())
            payload.unlink()
            payload.symlink_to(target)
            with self.assertRaisesRegex(ValueError, "unsafe"):
                verifier.verify_manifest(root)


if __name__ == "__main__":
    unittest.main()
