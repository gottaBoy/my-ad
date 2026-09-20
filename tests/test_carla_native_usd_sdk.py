"""SDK rule replay and fail-closed binding checks; not Editor runtime evidence."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("native_usd_sdk", SCRIPTS / "prepare_native_usd_sdk.py")
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


class NativeUsdSdkTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ue = self.root / "ue"
        self.actual = ROOT / "third_party/unreal-engine"
        # Use the actual effective engine rules; repeat preparation must preserve
        # unrelated edits instead of resetting them to the source.lock baseline.
        self.edits = sdk.patches(self.actual)
        for relative, (before, _) in self.edits.items():
            path = self.ue / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before)

    def test_rule_generation_is_idempotent_and_read_only(self):
        first = sdk.patches(self.ue)
        for relative, (before, after) in first.items():
            self.assertEqual(before, (self.ue / relative).read_bytes())
            (self.ue / relative).write_bytes(after)
        second = sdk.patches(self.ue)
        self.assertEqual(set(first), set(second))
        self.assertEqual(5, len(first))
        for relative, (before, after) in second.items():
            self.assertEqual(before, after)
            self.assertEqual(first[relative][1], after)

    def test_explicit_arm64_selection_preserves_sdk_feature_and_shared_runtime(self):
        data = {p.name: after.decode() for p, (_, after) in self.edits.items()}
        for name in ("Boost.Build.cs", "IntelTBB.Build.cs", "Python3.Build.cs", "UnrealUSDWrapper.Build.cs"):
            self.assertIn("CARLA_USD_NATIVE_ROOT", data[name])
            self.assertIn("Target.Architecture == UnrealArch.Arm64", data[name])
            self.assertIn("Target.Platform == UnrealTargetPlatform.Linux", data[name])
        boost = data["Boost.Build.cs"]
        self.assertIn('"-mt-a64.so.1.82.0"', boost)
        self.assertIn('"python311"', boost)
        self.assertIn('"boost_" + BoostLib + "-mt-x64"', boost)
        self.assertIn('"libtbb.so.2", "libtbbmalloc.so.2"', data["IntelTBB.Build.cs"])
        self.assertIn('"libtbb.a"', data["IntelTBB.Build.cs"])
        self.assertIn('"include", "python3.11"', data["Python3.Build.cs"])
        self.assertIn('"WITH_PYTHON=1"', data["Python3.Build.cs"])
        self.assertIn('"USE_USD_SDK=1"', data["UnrealUSDWrapper.Build.cs"])
        self.assertIn("Path.IsPathFullyQualified", sdk.rule_prefix())
        self.assertIn("native-sdk.json", sdk.rule_prefix())
        self.assertIn("_LIBCPP_TYPEINFO_COMPARISON_IMPLEMENTATION=2", data["UnrealUSDWrapper.Build.cs"])

    def test_runtime_selection_does_not_rewrite_verified_plugin_metadata(self):
        text = next(after.decode() for path, (_, after) in self.edits.items() if path.suffix == ".cpp")
        start = text.index("#ifdef CARLA_USD_NATIVE_ROOT")
        branch = text[start:text.index("#endif // CARLA_USD_NATIVE_ROOT", start)]
        before, otherwise = branch.split("#else", 1)
        self.assertIn("openusd/lib/usd", before)
        self.assertIn("materialx/libraries", before)
        self.assertNotIn("UpdatePlugInfoFiles", before)
        self.assertIn("UpdatePlugInfoFiles", otherwise)
        self.assertIn("openusd/plugin/usd", text)

    def test_conflicting_anchor_and_symlink_sources_are_rejected(self):
        file = self.ue / sdk.TP / "Boost/Boost.Build.cs"
        file.write_text("unexpected local contents\n")
        with self.assertRaisesRegex(ValueError, "anchor"):
            sdk.patches(self.ue)
        file.unlink()
        external = self.root / "external.cs"
        external.write_text("caller data\n")
        file.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "symlink"):
            sdk.patches(self.ue)
        self.assertEqual("caller data\n", external.read_text())

    def test_failed_openusd_report_prevents_any_binding_or_engine_write(self):
        before = {p: (self.ue / p).read_bytes() for p in self.edits}
        output = self.root / "artifacts"
        with mock.patch.object(sdk, "validate_report", side_effect=ValueError("native smoke FAIL")):
            with self.assertRaisesRegex(ValueError, "smoke FAIL"):
                sdk.prepare(self.ue, self.root / "report.json", self.root / "deps.json", output)
        self.assertFalse(output.exists())
        self.assertEqual(before, {p: (self.ue / p).read_bytes() for p in self.edits})

    def test_artifacts_cannot_be_inside_engine(self):
        with self.assertRaisesRegex(ValueError, "cannot live in UE"):
            sdk.prepare(self.ue, self.root / "report.json", self.root / "deps.json", self.ue / "artifacts")

    def test_verify_rejects_wrong_identity_and_changed_report_before_rule_access(self):
        (self.root / "native-sdk.json").write_text(json.dumps({"kind": "other"}))
        with self.assertRaisesRegex(ValueError, "identity"):
            sdk.verify(self.ue, self.root)
        report = self.root / "report.json"
        report.write_text("{}")
        (self.root / "native-sdk.json").write_text(json.dumps({
            "kind": "verified_sdk_binding_not_editor_pass", "root": str(self.root),
            "openusd_report": str(report), "openusd_report_sha256": "0" * 64}))
        with self.assertRaisesRegex(ValueError, "report changed"):
            sdk.verify(self.ue, self.root)


if __name__ == "__main__":
    unittest.main()
