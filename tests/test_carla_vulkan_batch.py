import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/batch_vulkan_compute.py"
spec = importlib.util.spec_from_file_location("vulkan_batch_tested", SCRIPT)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)

SHADER = "A" * 40
ENTRY = "main_00000628_26d5e8e8"


class VulkanComputeBatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.shaders = self.root / "shaders"
        self.output = self.root / "run"
        self.shaders.mkdir()
        self.output.mkdir()
        (self.shaders / f"{SHADER}.spv").write_bytes(b"\x03\x02\x23\x07" * 5)
        (self.shaders / f"{SHADER}.txt").write_text(
            f"shader=\nentry={ENTRY}\nbytes=20\nbindless=0\nwave_size=0\nflags=0\n"
        )
        self.validator = self.root / "spirv-val"
        self.validator.write_text("#!/bin/sh\nexit 0\n")
        self.validator.chmod(0o755)
        self.program = self.root / "replay"
        self.program.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "print(json.dumps({'status':'PASS', 'backend':sys.argv[2],"
            "'entry':sys.argv[3], 'mode':'create', 'ue_exact_replay':False,"
            "'layout_source':'spirv-reflect', 'readback_words':0}))\n"
        )
        self.program.chmod(0o755)

    def run_batch(self):
        return batch.run(
            self.program, self.validator, self.shaders, self.output, "lavapipe", 2
        )

    def test_pass_captures_inputs_and_commands(self):
        result = self.run_batch()
        self.assertEqual(("PASS", 1, 1), (
            result["status"], result["passed"], result["shader_count"]
        ))
        self.assertFalse(json.loads(
            (self.output / SHADER / "result.json").read_text()
        )["ue_exact_replay"])
        self.assertEqual(5, len(json.loads(
            (self.output / SHADER / "command.json").read_text()
        )))
        self.assertEqual(result, json.loads(
            (self.output / "batch-result.json").read_text()
        ))

    def test_tampered_metadata_fails_before_any_pipeline(self):
        path = self.shaders / f"{SHADER}.txt"
        path.write_text(path.read_text().replace("bytes=20", "bytes=21"))
        with self.assertRaisesRegex(ValueError, "byte count differs"):
            self.run_batch()
        self.assertFalse((self.output / SHADER).exists())
        self.assertFalse((self.output / "batch-result.json").exists())

    def test_process_failure_or_timeout_cannot_pass(self):
        self.program.write_text("#!/bin/sh\nexit 7\n")
        failed = self.run_batch()
        self.assertEqual(("FAIL", 0, "create exited 7"),
                         (failed["status"], failed["passed"], failed["results"][0]["error"]))
        self.assertEqual(7, failed["results"][0]["returncode"])

        second = self.root / "second"
        second.mkdir()
        self.program.write_text("#!/bin/sh\nsleep 2\n")
        timed_out = batch.run(
            self.program, self.validator, self.shaders, second, "lavapipe", 1
        )
        self.assertEqual("FAIL", timed_out["status"])
        self.assertIn("timed out", timed_out["results"][0]["error"])

    def test_missing_metadata_and_bad_entry_are_rejected(self):
        (self.shaders / f"{SHADER}.txt").unlink()
        with self.assertRaisesRegex(ValueError, "inventories differ"):
            self.run_batch()
        (self.shaders / f"{SHADER}.txt").write_text(
            "shader=\nentry=main\nbytes=20\nbindless=0\nwave_size=0\nflags=0\n"
        )
        with self.assertRaisesRegex(ValueError, "incomplete compute metadata"):
            self.run_batch()
