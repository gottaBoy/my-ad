import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location(
    "memory_trace_snapshot_test", SCRIPTS / "vulkan_graphics_memory_trace.py")
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def trace():
    return "\n".join([
        "schema=1",
        "kind=uniform_buffer_mismatch thread=42 context=0x10 pending_state=0x20 pipeline=0x30 shader=0x40 frequency=3 buffer_index=1 shader_key=99 pending_key=0",
        "event_count=2",
        "event=0 kind=pipeline thread=42 changed=1 context=0x10 pending_state=0x20 pipeline=0x30 shader=0x0 frequency=0 buffer_index=0 shader_key=0 pending_key=0 keys=10,0,0,0,0",
        "event=1 kind=graphics_parameters thread=42 changed=0 context=0x10 pending_state=0x20 pipeline=0x30 shader=0x40 frequency=3 buffer_index=0 shader_key=99 pending_key=0 keys=10,0,0,0,0",
        "end=1",
        "",
    ])


class MemoryTraceTest(unittest.TestCase):
    def test_trace_is_typed_and_not_runtime_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graphics-memory-trace.txt"
            path.write_text(trace())
            result = snapshot.validate(path)
        self.assertEqual("PASS", result["status"])
        self.assertFalse(result["runtime_acceptance"])
        self.assertEqual(1, result["matching_pipeline_events"])
        self.assertEqual(1, result["matching_parameter_events"])

    def test_mismatch_and_ring_bounds_are_fail_closed(self):
        mutations = [
            trace().replace("shader_key=99 pending_key=0", "shader_key=0 pending_key=0"),
            trace().replace("event_count=2", "event_count=4097"),
            trace().replace("event=1", "event=4"),
            trace().replace("keys=10,0,0,0,0", "keys=10,0,0"),
        ]
        for value in mutations:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "trace.txt"
                path.write_text(value)
                with self.subTest(value=value[:30]), self.assertRaises(ValueError):
                    snapshot.validate(path)

    def test_cli_writes_analysis_only_for_valid_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "trace.txt"
            output = root / "analysis.json"
            trace_path.write_text(trace())
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "vulkan_graphics_memory_trace.py"),
                 "--trace", str(trace_path), "--output", str(output)],
                capture_output=True, text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse(json.loads(output.read_text())["runtime_acceptance"])

    def test_direct_runner_is_gdb_free_and_explicit(self):
        script = (SCRIPTS / "probe-ue-vulkan-memory-trace.sh").read_text()
        self.assertIn("direct binary, no GDB", script)
        self.assertIn("CarlaVulkanGraphicsMemoryTrace=", script)
        self.assertNotIn("gdb ", script)
        self.assertNotIn("|| true", script)
        result = subprocess.run(
            ["bash", str(SCRIPTS / "probe-ue-vulkan-memory-trace.sh")],
            env={**os.environ, "CARLA_UE_MEMORY_TRACE_TIMEOUT": "0"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)

    def test_make_exposes_direct_memory_trace_target(self):
        result = subprocess.run(["make", "-n", "carla-ue-vulkan-memory-trace-gpu"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("probe-ue-vulkan-memory-trace.sh", result.stdout)
        self.assertNotIn("SYS_PTRACE", result.stdout)


if __name__ == "__main__":
    unittest.main()
