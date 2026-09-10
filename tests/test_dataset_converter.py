from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "dataset-converter" / "convert.py"
SPEC = importlib.util.spec_from_file_location("dataset_converter", MODULE_PATH)
assert SPEC and SPEC.loader
CONVERTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONVERTER)


class DatasetConverterTest(unittest.TestCase):
    def test_manifest_is_deterministic_and_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bags = root / "bags"
            run_b = bags / "run_b"
            run_a = bags / "run_a"
            run_b.mkdir(parents=True)
            run_a.mkdir(parents=True)
            (run_b / "z.mcap").write_bytes(b"z")
            (run_a / "b.yaml").write_text("b\n", encoding="utf-8")
            (run_a / "a.mcap").write_bytes(b"a")

            first = CONVERTER.build_manifest(bags, "awsim-test")
            second = CONVERTER.build_manifest(bags, "awsim-test")

            self.assertEqual(first, second)
            self.assertEqual(
                [run["run_id"] for run in first["runs"]],
                ["run_a", "run_b"],
            )
            self.assertEqual(
                [file["path"] for file in first["runs"][0]["files"]],
                ["a.mcap", "b.yaml"],
            )

    def test_atomic_output_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dataset-manifest.json"
            payload = {"format": "test", "runs": []}
            CONVERTER.write_atomic_json(output, payload)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), payload)

    def test_missing_input_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "input directory not found"):
            CONVERTER.build_manifest(Path("/definitely/missing"), "test")


if __name__ == "__main__":
    unittest.main()
