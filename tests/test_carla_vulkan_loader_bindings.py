import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "loader_bindings_test", SCRIPTS / "analyze_vulkan_loader_bindings.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)
CLIENT = "/artifacts/carla/cooked-client-full/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal"
DRIVER = "/lib/aarch64-linux-gnu/libnvidia-glvkspirv.so.580.173.02"
LIBC = "/lib/aarch64-linux-gnu/libc.so.6"


def binding(symbol, target=LIBC, source=DRIVER, pid=321):
    return (f"       {pid}: binding file {source} [0] to {target} [0]: "
            f"normal symbol `{symbol}' [GLIBC_2.17]\n")


class LoaderBindingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "server-command.json").write_text(json.dumps([
            "timeout", "--signal=INT", "--kill-after=10", "330", CLIENT,
            "/Game/Carla/Maps/Town10HD_Opt",
        ]))
        self.log = self.root / "loader-bindings.321"

    def test_normal_driver_bindings_do_not_claim_runtime_success(self):
        self.log.write_text(binding("malloc") + binding("pthread_once")
                            + binding("memcpy", source="/usr/bin/timeout"))
        report = loader.analyze(self.root)
        self.assertEqual("NO_DRIVER_TO_CLIENT_BINDINGS", report["status"])
        self.assertEqual(2, report["driver_binding_count"])
        self.assertEqual([], report["driver_to_client"])
        self.assertFalse(report["runtime_acceptance"])
        self.assertEqual(64, len(report["evidence"][0]["sha256"]))

    def test_reports_symbols_resolved_to_the_actual_client(self):
        self.log.write_text(binding("stdout", CLIENT) + binding("stdout", CLIENT)
                            + binding("calloc"))
        report = loader.analyze(self.root)
        self.assertEqual("DRIVER_TO_CLIENT_BINDINGS", report["status"])
        self.assertEqual([{"source": DRIVER, "symbol": "stdout", "count": 2}],
                         report["driver_to_client"])
        self.assertFalse(report["runtime_acceptance"])

    def test_missing_empty_and_partial_trace_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "missing or unbounded"):
            loader.analyze(self.root)
        self.log.write_text("       321: calling init: /lib/libc.so.6\n")
        with self.assertRaisesRegex(ValueError, "no NVIDIA"):
            loader.analyze(self.root)
        self.log.write_text(binding("malloc").rstrip("\n"))
        with self.assertRaisesRegex(ValueError, "partial loader line"):
            loader.analyze(self.root)
        self.log.write_text("x" * (loader.MAX_LINE + 1) + "\n")
        with self.assertRaisesRegex(ValueError, "oversized"):
            loader.analyze(self.root)

    def test_pid_symlink_and_unknown_driver_format_are_rejected(self):
        self.log.write_text(binding("malloc", pid=123))
        with self.assertRaisesRegex(ValueError, "PID"):
            loader.analyze(self.root)
        self.log.write_text(f" 321: binding file {DRIVER}: changed-format\n")
        with self.assertRaisesRegex(ValueError, "unrecognized driver binding"):
            loader.analyze(self.root)
        target = self.root / "actual"
        self.log.rename(target)
        self.log.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "unsafe loader log"):
            loader.analyze(self.root)

    def test_driver_dependencies_are_not_client_interposition(self):
        other_driver = "/lib/aarch64-linux-gnu/libnvidia-eglcore.so.580.173.02"
        self.log.write_text(binding("_nv014nvvm", DRIVER, other_driver))
        report = loader.analyze(self.root)
        self.assertEqual([], report["driver_to_client"])
        self.assertEqual(DRIVER, report["provider_counts"][0]["provider"])

    def test_concatenated_thread_output_preserves_each_binding(self):
        first = binding("memchr").replace(" [GLIBC_2.17]\n", "")
        self.log.write_text(first + binding("memchr"))
        report = loader.analyze(self.root)
        self.assertEqual(2, report["driver_binding_count"])
        self.assertEqual([{"source": DRIVER, "provider": LIBC, "count": 2}],
                         report["provider_counts"])
        self.log.write_text(first[:-1] + binding("memchr"))
        with self.assertRaisesRegex(ValueError, "unrecognized driver binding"):
            loader.analyze(self.root)

    def test_invalid_switch_precedes_environment_checks(self):
        script = SCRIPTS / "probe-town10-gb10-runtime.sh"
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                                env={**os.environ, "CARLA_RUNTIME_LOADER_TRACE": "yes"})
        self.assertEqual(64, result.returncode, result.stderr)

    def test_trace_is_default_off_and_confined_to_server_subshell(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('loader_trace="${CARLA_RUNTIME_LOADER_TRACE:-0}"', script)
        block = script.split("step=startup", 1)[1].split("server_pid=$!", 1)[0]
        self.assertIn("export LD_DEBUG=bindings", block)
        self.assertIn('export LD_DEBUG_OUTPUT="${run_dir}/loader-bindings"', block)
        self.assertNotIn("export LD_DEBUG", script.split("step=startup", 1)[0])
        self.assertIn("loader-binding-analysis-code.txt", script)
        self.assertIn("loader-trace-config.json", script)
        self.assertIn("Loader trace: %s", script)
        command = subprocess.run(
            ["make", "-n", "carla-town10-gb10-runtime", "RUNTIME_LOADER_TRACE=1"],
            cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertIn("CARLA_RUNTIME_LOADER_TRACE=1", command.stdout)


if __name__ == "__main__":
    unittest.main()
