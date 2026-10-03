import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATERIAL_CPP = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/Engine"
                       "/Private/Materials/Material.cpp")

GATE = "ShouldLogCarlaMaterialSerialize()"
MARKERS = ("Material::Serialize post-super", "Material::Serialize pre-shadermaps")


class MaterialSerializeLogGateTest(unittest.TestCase):
    """Per-asset engine diagnostics must be opt-in.

    The two UMaterial::Serialize probes record the archive offset around
    Super::Serialize and around SerializeInlineShaderMaps. They were added while
    diagnosing cooked material stream offsets, but logged unconditionally at
    Warning level, which emitted ~240 lines per GB10 run and buried the gate log
    that every other finding is read against. They are still the only same-run
    evidence for the tell offsets, so they are gated rather than deleted.
    """

    @classmethod
    def setUpClass(cls):
        cls.text = MATERIAL_CPP.read_text(encoding="utf-8", errors="replace")
        cls.lines = cls.text.splitlines()

    def test_diagnostics_are_preserved(self):
        for marker in MARKERS:
            self.assertIn(marker, self.text)

    def test_diagnostics_are_guarded_by_the_opt_in(self):
        guarded = 0
        for index, line in enumerate(self.lines):
            if not ("UE_LOG" in line and "Material::Serialize" in line):
                continue
            guarded += 1
            window = self.lines[max(0, index - 4):index]
            self.assertTrue(
                any(GATE in earlier for earlier in window),
                f"unconditional material serialize log at line {index + 1}",
            )
        self.assertEqual(guarded, len(MARKERS))

    def test_gate_is_off_by_default(self):
        self.assertIn("int32 GCarlaLogMaterialSerialize = 0;", self.text)

    def test_gate_is_reachable_from_both_switches(self):
        # A CVar for interactive/config use and a command-line switch for probe
        # runs, matching carla.AllowEditorContentInServerBuilds and
        # -CarlaVulkanValidationStackTrace respectively.
        self.assertIn('TEXT("carla.LogMaterialSerialize")', self.text)
        self.assertIn(
            'FParse::Param(FCommandLine::Get(), TEXT("CarlaLogMaterialSerialize"))',
            self.text,
        )

    def test_gate_includes_are_declared(self):
        for header in ("HAL/IConsoleManager.h", "Misc/CommandLine.h", "Misc/Parse.h"):
            self.assertIn(f'#include "{header}"', self.text)


if __name__ == "__main__":
    unittest.main()
