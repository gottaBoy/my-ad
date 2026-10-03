import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INPUT_INI = ROOT / "third_party/carla/Unreal/CarlaUnreal/Config/DefaultInput.ini"
PROJECT = ROOT / "third_party/carla/Unreal/CarlaUnreal/CarlaUnreal.uproject"


class InputSettingsConfigTest(unittest.TestCase):
    """The client must not reference a plugin this project cannot load.

    CarlaUnreal.uproject sets DisableEnginePluginsByDefault, so only the engine
    plugins it lists are mounted at runtime. EnhancedInput is not listed, and
    its descriptor depends on DataValidation, which lives under
    Engine/Plugins/Editor and is therefore unavailable outside the editor. A
    DefaultInput.ini that points at /Script/EnhancedInput.* therefore makes
    UInputSettings::GetDefaultPlayerInputClass/ComponentClass fail their
    Class.IsValid() ensure and silently fall back to the engine classes.
    """

    def test_default_input_uses_engine_classes(self):
        text = INPUT_INI.read_text(encoding="utf-8", errors="replace")
        self.assertIn("DefaultPlayerInputClass=/Script/Engine.PlayerInput", text)
        self.assertIn("DefaultInputComponentClass=/Script/Engine.InputComponent", text)

    def test_default_input_does_not_reference_unmountable_plugin(self):
        text = INPUT_INI.read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("EnhancedInput", text)

    def test_project_disables_engine_plugins_by_default(self):
        # Guards the premise above: if this ever flips, the engine classes are
        # still valid but EnhancedInput would become mountable again.
        self.assertIn('"DisableEnginePluginsByDefault": true',
                      PROJECT.read_text(encoding="utf-8", errors="replace"))

    def test_enhanced_input_dependency_is_editor_only(self):
        descriptor = (ROOT / "third_party/unreal-engine/Engine/Plugins/EnhancedInput"
                            "/EnhancedInput.uplugin")
        self.assertIn('"Name": "DataValidation"',
                      descriptor.read_text(encoding="utf-8", errors="replace"))
        self.assertTrue((ROOT / "third_party/unreal-engine/Engine/Plugins/Editor"
                               "/DataValidation/DataValidation.uplugin").is_file())


if __name__ == "__main__":
    unittest.main()
