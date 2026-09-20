import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FbxFeatureMatrixTest(unittest.TestCase):
    def test_matrix_keeps_product_acceptance_and_fixture_gaps_explicit(self):
        data = json.loads((ROOT / "config/carla/fbx-feature-matrix.json").read_text())
        self.assertEqual("required_workflows_not_acceptance", data["kind"])
        self.assertFalse(data["policy"]["silent_feature_removal"])
        self.assertFalse(data["policy"]["fixtures_or_object_compilation_establish_product_acceptance"])
        self.assertTrue(data["policy"]["deferred_features_remain_required"])
        identifiers = [feature["id"] for feature in data["features"]]
        self.assertEqual(len(set(identifiers)), len(identifiers))
        self.assertTrue({"static-assets", "materials-textures", "collision-sockets", "lods",
                         "skeletal-animation-morph", "replace-reimport-tiles", "cinematic-export",
                         "navigation-geometry-conversion"} <= set(identifiers))
        for feature in data["features"]:
            self.assertEqual("NOT_RUN", feature["product_acceptance"])
            self.assertTrue(feature["acceptance"])
            self.assertTrue(feature["source_paths"])
            self.assertTrue(feature["fixture_paths"] or feature.get("fixture_gaps"))
            for relative in feature["source_paths"] + feature["fixture_paths"]:
                self.assertFalse(Path(relative).is_absolute())
                self.assertTrue((ROOT / relative).is_file(), relative)

    def test_actual_carla_import_path_still_requests_legacy_factory_features(self):
        text = (ROOT / "third_party/carla/Util/Tools/Import.py").read_text()
        for setting in ('"FactoryName": "FbxFactory"', '"bReplaceExisting": "true"',
                        '"bImportMaterials": 1', '"bImportTextures": 1',
                        '"bForceVerticesRelativeToTile": do_tiles'):
            self.assertIn(setting, text)


if __name__ == "__main__":
    unittest.main()
