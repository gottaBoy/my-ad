"""Parser evidence validators and prepare replay tests; no UBT or real stage PASS."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts/carla"
PREPARE_SCRIPT = SCRIPT_DIR / "prepare_interchange_parser.py"
spec = importlib.util.spec_from_file_location("native_parser_prepare", PREPARE_SCRIPT)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PrepareParserTest(unittest.TestCase):
    """Real prepare implementation, isolated fake engine input only."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ue = self.root / "engine"
        self.artifacts = self.root / "artifacts"
        module = self.ue / prepare.MODULE
        (module / "Public").mkdir(parents=True)
        (module / "Private").mkdir()
        for name in prepare.SDK_UNITS:
            (module / "Private" / (name + ".cpp")).write_text(
                f'// Original {name}\n#include "FbxInclude.h"\n', encoding="utf-8")
        self.rule = module / "InterchangeFbxParser.Build.cs"
        self.rule.write_text(
            "// Existing rules are retained byte for byte.\n"
            "public class InterchangeFbxParser {\n"
            "\tvoid Configure() {\n"
            '\t\t\tPrivateDependencyModuleNames.Add("InterchangeNodes");\n'
            + prepare.RULE_MARKER + '\n\t\t\t\t"FBX"\n\t\t\t);\n'
            "\t}\n}\n", encoding="utf-8")
        self.generated = module / "Private/InterchangeFbxStaticParser.cpp"
        self.sdk_files = (
            Path("Engine/Source/ThirdParty/FBX/2020.2/include/fbxsdk.h"),
            Path("Engine/Binaries/ThirdParty/FBX/2020.2/Linux/libfbxsdk.so"),
            prepare.MODULE / "Private/InterchangeFbxParserModule.cpp",
            prepare.MODULE / "Private/InterchangeFbxSettings.cpp",
            prepare.MODULE / "Public/InterchangeFbxParser.h",
        )
        for relative in self.sdk_files:
            path = self.ue / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preserve default SDK/module input\n")
        self.original = self.snapshot()

    def snapshot(self):
        return {str(path.relative_to(self.ue)): path.read_bytes()
                for path in self.ue.rglob("*") if path.is_file()}

    def test_prepare_retains_exact_changes_and_preserves_default_sdk(self):
        run = prepare.prepare(self.ue, self.artifacts)
        report = json.loads((run / "deployment.json").read_text())
        self.assertEqual("source_deployment_not_build_evidence", report["kind"])
        self.assertIs(True, report["default_sdk_path_preserved"])
        self.assertEqual(len(prepare.SDK_UNITS) + 3, report["changes"])
        self.assertEqual(len(prepare.SDK_UNITS) + 3, len(report["engine_files"]))
        for name in prepare.SDK_UNITS:
            relative = prepare.MODULE / "Private" / (name + ".cpp")
            actual = (self.ue / relative).read_bytes()
            self.assertEqual(
                prepare.BEGIN.encode() + self.original[str(relative)] + prepare.END.encode(),
                actual)
        self.assertEqual(self.original[str(self.rule.relative_to(self.ue))].decode(),
                         self.rule.read_text().replace(prepare.RULE_INSERT, "", 1))
        for relative in self.sdk_files:
            self.assertEqual(self.original[str(relative)], (self.ue / relative).read_bytes())
        for relative, digest in report["engine_files"].items():
            after = (run / "after" / relative).read_bytes()
            self.assertEqual(after, (self.ue / relative).read_bytes())
            self.assertEqual(hashlib.sha256(after).hexdigest(), digest)
            if relative in self.original:
                self.assertEqual(self.original[relative], (run / "before" / relative).read_bytes())
        patch = (run / "changes.patch").read_text()
        self.assertIn("--- /dev/null", patch)
        self.assertIn("+#if !CARLA_INTERCHANGE_UFBX_STATIC", patch)
        self.assertIn("+++ b/" + str(prepare.MODULE / "InterchangeFbxParser.Build.cs"), patch)
        self.assertNotIn("libfbxsdk.so", patch)

    def test_second_prepare_is_idempotent_and_preserves_first_evidence(self):
        first = prepare.prepare(self.ue, self.artifacts)
        first_report = (first / "deployment.json").read_bytes()
        first_patch = (first / "changes.patch").read_bytes()
        deployed = self.snapshot()
        second = prepare.prepare(self.ue, self.artifacts)
        self.assertNotEqual(first, second)
        self.assertEqual(deployed, self.snapshot())
        self.assertEqual(0, json.loads((second / "deployment.json").read_text())["changes"])
        self.assertEqual(b"", (second / "changes.patch").read_bytes())
        self.assertEqual(first_report, (first / "deployment.json").read_bytes())
        self.assertEqual(first_patch, (first / "changes.patch").read_bytes())

    def test_partial_guard_conflict_stops_before_any_engine_writes(self):
        path = self.ue / prepare.MODULE / "Private/FbxScene.cpp"
        path.write_text(prepare.BEGIN + "// incomplete\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "partial or conflicting source guard"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_rule_conflict_stops_before_any_engine_writes(self):
        self.rule.write_text("// CARLA_INTERCHANGE_UFBX_STATIC local edit\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "conflicting parser build rules"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_unowned_generated_source_is_never_overwritten(self):
        self.generated.write_bytes(b"local implementation must survive\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "local generated-source edits"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_local_changes_after_deployment_are_not_authorized_by_receipt(self):
        prepare.prepare(self.ue, self.artifacts)
        self.generated.write_bytes(self.generated.read_bytes() + b"\n// new local edit\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "local generated-source edits"):
            prepare.prepare(self.ue, self.artifacts)
        self.assertEqual(before, self.snapshot())

    def test_source_and_generated_symlinks_are_rejected_without_engine_writes(self):
        for path in (self.ue / prepare.MODULE / "Private/FbxScene.cpp", self.generated):
            with self.subTest(path=path.name):
                original = path.read_bytes() if path.exists() else None
                if path.exists():
                    path.unlink()
                external = self.root / (path.name + ".external")
                external.write_bytes(original or b"external generated source\n")
                path.symlink_to(external)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "symlink"):
                    prepare.prepare(self.ue, self.artifacts)
                self.assertEqual(before, self.snapshot())
                self.assertTrue(path.is_symlink())
                path.unlink()
                if original is not None:
                    path.write_bytes(original)

    def test_artifacts_cannot_be_placed_in_engine_tree(self):
        with self.assertRaisesRegex(ValueError, "engine tree"):
            prepare.prepare(self.ue, self.ue / "artifacts")
        self.assertEqual(self.original, self.snapshot())

    def test_opt_in_rules_keep_sdk_branch_and_reject_unsupported_targets(self):
        prepare.prepare(self.ue, self.artifacts)
        rule = self.rule.read_text()
        self.assertEqual(1, rule.count(prepare.RULE_INSERT))
        for condition in ('StaticBackend != "0" && StaticBackend != "1"',
                          'StaticBackend == "1"', "Target.Type != TargetType.Program",
                          "Target.Platform != UnrealTargetPlatform.Linux",
                          "Target.Architecture != UnrealArch.Arm64",
                          "Target.bCompileAgainstEngine"):
            self.assertIn(condition, rule)
        self.assertLess(rule.index("return;"), rule.index(prepare.RULE_MARKER))
        self.assertIn('"FBX"', rule[rule.index(prepare.RULE_MARKER):])
        self.assertNotIn("CarlaUfbxMesh", rule)
        self.assertNotIn("LinuxArm64", rule)


if __name__ == "__main__":
    unittest.main()
