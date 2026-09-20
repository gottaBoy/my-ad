import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "config/carla/change-ledger.json"


class CarlaChangeLedgerTest(unittest.TestCase):
    def test_ledger_is_machine_readable_and_covers_real_paths(self):
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertEqual(1, data["schema_version"])
        self.assertEqual("DGX Spark only", data["policy"]["build_host"])
        entries = data["entries"]
        self.assertGreaterEqual(len(entries), 8)
        identifiers = [entry["id"] for entry in entries]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for entry in entries:
            for field in ("id", "category", "change", "reason", "boundary"):
                self.assertTrue(entry[field].strip(), (entry["id"], field))
            self.assertTrue(entry["paths"])
            self.assertTrue(entry["validation"])
            for path in entry["paths"]:
                self.assertFalse(Path(path).is_absolute())
                self.assertTrue((ROOT / path).exists(), path)

    def test_ledger_keeps_required_boundaries_explicit(self):
        text = LEDGER.read_text(encoding="utf-8")
        for phrase in (
            "Does not replace Autodesk SDK ABI",
            "Does not claim InterchangeWorker",
            "Not UE shader rendering",
            "No real CARLA Server is currently running",
            "silent_feature_removal_is_allowed\": false",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
