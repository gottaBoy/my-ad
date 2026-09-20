import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/probe-arm64-runtime.sh"


class RuntimeEntryTest(unittest.TestCase):
    def test_make_entry_keeps_mutation_opt_in(self):
        result = subprocess.run(
            ["make", "-n", "carla-runtime-check", "PROVENANCE=/artifacts/carla/build.json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile g0 run --rm -T", result.stdout)
        self.assertIn("CARLA_ALLOW_WORLD_MUTATION=\"0\"", result.stdout)
        self.assertIn("probe-arm64-runtime.sh", result.stdout)

    def invoke(self, **values):
        return subprocess.run(["bash", str(SCRIPT)],
                              env={**os.environ, **values}, capture_output=True, text=True)

    def test_explicit_world_mutation_permission_is_required(self):
        result = self.invoke(CARLA_ALLOW_WORLD_MUTATION="0")
        self.assertEqual(64, result.returncode)
        self.assertIn("exclusively owned test world", result.stderr)

    def test_bad_options_fail_before_installation_or_network(self):
        for name, value in (("CARLA_RUNTIME_MODE", "unknown"),
                            ("CARLA_RUNTIME_TICKS", "0"), ("CARLA_RUNTIME_TICKS", "10001"),
                            ("CARLA_RUNTIME_PORT", "65536"), ("CARLA_RUNTIME_TIMEOUT", "121"),
                            ("CARLA_RUNTIME_TOTAL_TIMEOUT", "2;true"),
                            ("CARLA_RUNTIME_PORT", "02000")):
            with self.subTest(name=name, value=value):
                result = self.invoke(**{name: value})
                self.assertEqual(64, result.returncode)
                self.assertNotIn("pip", result.stdout)

    def test_sensors_require_multiple_frames(self):
        result = self.invoke(CARLA_RUNTIME_MODE="sensors", CARLA_RUNTIME_TICKS="1")
        self.assertEqual(64, result.returncode)
        self.assertIn("at least two ticks", result.stderr)

    def test_entry_is_native_local_bounded_and_offline(self):
        text = SCRIPT.read_text()
        self.assertIn('[[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]', text)
        self.assertIn("--host 127.0.0.1", text)
        self.assertIn("--no-index --no-deps --force-reinstall", text)
        self.assertIn("timeout --signal=INT --kill-after=30", text)
        self.assertIn("wheel.sha256", text)
        self.assertIn("sha256sum --check", text)
        self.assertIn("runtime_entry_report.py\" capture", text)
        self.assertIn("runtime_entry_report.py\" finalize", text)
        self.assertIn("runtime_entry_report.py\" verify-inputs", text)
        self.assertIn("--run-dir \"${run_dir}/endpoint\"", text)
        self.assertIn("[[ \"${code}\" != 0 ]] || code=\"${final_code}\"", text)
        self.assertNotIn("load_world", text)
        self.assertNotIn("linux/amd64", text)


if __name__ == "__main__":
    unittest.main()
