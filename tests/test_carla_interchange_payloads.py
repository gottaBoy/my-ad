"""Synthetic evaluator inputs only; never manufacture native stage reports."""

import copy
import hashlib
import json
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts/carla"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("payload_checker", SCRIPTS / "check_ue_interchange.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PayloadRecordTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)
        self.data = {"source_sha256": "a" * 64, "mesh_nodes": 2, "payload_count": 6,
                     "payload_contract_version": 2, "source_scene_self_tests": 20,
                     "payload_self_tests": 27, "payloads": [], "serialized_bytes": 24,
                     "payload_vertices": 18, "payload_triangles": 6, "payload_materials": 6}
        for mesh in range(2):
            for index, mode in enumerate(("identity", "translated", "mirrored")):
                uid = f"{mesh * 3 + index:064x}"
                (self.output / (uid + ".payload")).write_bytes(b"data")
                self.data["payloads"].append({"mesh_uid": str(mesh),
                    "payload_key": "ufbx-static-ue-cm-v1/" + "a" * 64 + f"/{mesh}",
                    "request_uid": uid, "filename": uid + ".payload", "transform": mode,
                    "bytes": 4, "vertices": 3, "triangles": 1, "materials": 1, "roundtrip": True})
                entry = self.data["payloads"][-1]
                entry["dispatcher_roundtrip"] = True
                entry["request_json"] = json.dumps({"CmdID": "Payload", "TranslatorID": "FBX",
                    "CmdData": {"PayloadKey": entry["payload_key"], "GlobalMeshTransform": mode}})
                entry["result_json"] = json.dumps({"ResultFile": str(self.output / entry["filename"])})

    def test_every_request_gets_independent_hash_even_with_equal_content(self):
        result = checker.validate_payloads(self.data, self.output)
        self.assertEqual(6, len(result))
        self.assertEqual(6, len({item["request_uid"] for item in result}))
        self.assertEqual({hashlib.sha256(b"data").hexdigest()}, {item["sha256"] for item in result})

    def test_rejects_incomplete_transform_or_mesh_coverage(self):
        for key, value in (("payload_count", 2), ("payload_count", True), ("mesh_nodes", 3),
                           ("payload_contract_version", 1), ("payload_self_tests", 0),
                           ("source_scene_self_tests", 0)):
            data = copy.deepcopy(self.data)
            data[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                checker.validate_payloads(data, self.output)
        data = copy.deepcopy(self.data)
        data["payloads"][1]["transform"] = "identity"
        with self.assertRaises(ValueError):
            checker.validate_payloads(data, self.output)

    def test_rejects_stale_duplicate_or_unsafe_request_records(self):
        for key, value in (("payload_key", "old-source-key"), ("filename", "../escape.payload"),
                           ("request_uid", "z" * 64), ("vertices", True), ("bytes", 5),
                           ("roundtrip", "true")):
            data = copy.deepcopy(self.data)
            data["payloads"][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                checker.validate_payloads(data, self.output)
        data = copy.deepcopy(self.data)
        data["payloads"][1] = data["payloads"][0]
        with self.assertRaises(ValueError):
            checker.validate_payloads(data, self.output)

    def test_rejects_missing_empty_symlink_and_unreported_payloads(self):
        path = self.output / self.data["payloads"][0]["filename"]
        path.unlink()
        with self.assertRaises(ValueError):
            checker.validate_payloads(self.data, self.output)
        path.touch()
        with self.assertRaises(ValueError):
            checker.validate_payloads(self.data, self.output)
        path.unlink()
        path.symlink_to(self.output / self.data["payloads"][1]["filename"])
        with self.assertRaises(ValueError):
            checker.validate_payloads(self.data, self.output)
        path.unlink()
        path.write_bytes(b"data")
        (self.output / "unexpected.payload").write_bytes(b"data")
        with self.assertRaises(ValueError):
            checker.validate_payloads(self.data, self.output)

    def test_rejects_first_mesh_only_aggregate_metrics(self):
        for field in ("serialized_bytes", "payload_vertices", "payload_triangles", "payload_materials"):
            data = copy.deepcopy(self.data)
            data[field] //= 2
            with self.subTest(field=field), self.assertRaises(ValueError):
                checker.validate_payloads(data, self.output)

    def test_dispatcher_json_must_bind_key_and_result_file(self):
        for field, value in (("dispatcher_roundtrip", False), ("request_json", "{}"),
                             ("request_json", "[]"), ("result_json", "{}"),
                             ("result_json", '{"ResultFile":"a","ResultFile":"b"}')):
            data = copy.deepcopy(self.data)
            data["payloads"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                checker.validate_payloads(data, self.output)
        data = copy.deepcopy(self.data)
        data["payloads"][0]["result_json"] = data["payloads"][1]["result_json"]
        with self.assertRaises(ValueError):
            checker.validate_payloads(data, self.output)


if __name__ == "__main__":
    unittest.main()
