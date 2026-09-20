from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts/carla/ue-interchange/Source/CarlaInterchangeProbe/Private/CarlaInterchangeProbe.cpp"


class CarlaUfbxSceneSelfTestGateTest(unittest.TestCase):
    """Source contract only; never invokes UE, UBT, Docker, or hardware."""

    @classmethod
    def setUpClass(cls):
        cls.source = PROBE.read_text(encoding="utf-8")

    def test_mode_calls_existing_scene_selftest_api_and_reports_real_count(self):
        source = self.source
        self.assertIn('#include "CarlaUfbxScene.h"', source)
        self.assertIn('TEXT("source-scene-self-test")', source)
        self.assertIn("CarlaUfbxMesh::RunSceneSelfTests(Tests, Error)", source)
        self.assertIn("int32 Tests = 0;", source)
        self.assertIn('if (bSourceSceneSelfTest) Report->SetNumberField(TEXT("self_tests"), Tests);', source)
        self.assertIn('Report->SetStringField(TEXT("error"), Error);', source)
        self.assertIn("return Success ? 0 : 2;", source)

    def test_zero_passing_selftests_cannot_be_reported_as_pass(self):
        source = self.source
        self.assertIn("if (Success && Tests <= 0)", source)
        self.assertIn('TEXT("Source-scene selftests returned zero passing tests")', source)
        self.assertIn("Success = false;", source)

    def test_new_stage_identity_and_scope_are_exact(self):
        source = self.source
        self.assertIn('TEXT("ue-ufbx-scene-selftests")', source)
        self.assertIn(
            'TEXT("source-scene API selftests ONLY; not Interchange worker, Editor or Cook")',
            source,
        )

    def test_selftest_mode_does_not_claim_scene_graph_or_payload_roundtrips(self):
        source = self.source
        self.assertIn("const bool bSceneReport = bScene && !bSourceSceneSelfTest;", source)
        self.assertIn(
            'Report->SetBoolField(TEXT("graph_roundtrip"), bSceneReport && Success);',
            source,
        )
        self.assertIn(
            'Report->SetBoolField(TEXT("payload_roundtrip"), bSceneReport && Success);',
            source,
        )

    def test_bootstrap_and_scene_modes_keep_their_existing_contract_strings(self):
        source = self.source
        self.assertIn('TEXT("interchange-node-bootstrap")', source)
        self.assertIn(
            'TEXT("Interchange node construction/serialization only; not FBX translation or Cook")',
            source,
        )
        self.assertIn('TEXT("ue-ufbx-interchange-static")', source)
        self.assertIn(
            'TEXT("ufbx to UE Interchange static nodes and mesh payloads; not translator/worker, material shading, factory assets, Editor or Cook")',
            source,
        )
        self.assertIn('TEXT("bootstrap-test")', source)
        self.assertIn('TEXT("scene-test")', source)
        self.assertIn(
            'TEXT("Expected bootstrap-test or scene-test with input and result-dir")',
            source,
        )


if __name__ == "__main__":
    unittest.main()
