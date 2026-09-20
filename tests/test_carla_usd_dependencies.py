import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/carla/inspect_usd_dependencies.py"
spec = importlib.util.spec_from_file_location("inspect_usd_dependencies", SCRIPT)
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


class UsdDependencyTest(unittest.TestCase):
    def elf(self, machine=183, elf_type=3):
        data = bytearray(64)
        data[:4] = b"\x7fELF"
        data[4] = 2
        data[5] = 1
        struct.pack_into("<H", data, 16, elf_type)
        struct.pack_into("<H", data, 18, machine)
        return bytes(data)

    def archive(self, *members):
        result = bytearray(b"!<arch>\n")
        for index, body in enumerate(members):
            name = ("member%d.o" % index).ljust(16)
            header = (name + "0".ljust(12) + "0".ljust(6) + "0".ljust(6)
                      + "100644".ljust(8) + str(len(body)).ljust(10) + "`\n")
            self.assertEqual(60, len(header.encode("ascii")))
            result.extend(header.encode("ascii"))
            result.extend(body)
            if len(body) % 2:
                result.extend(b"\n")
        return bytes(result)

    def test_elf_info_distinguishes_arm64_and_x86_64(self):
        arm = inspector.elf_info(self.elf())
        x86 = inspector.elf_info(self.elf(62))
        self.assertEqual("AArch64", arm["architecture"])
        self.assertEqual("x86_64", x86["architecture"])
        self.assertTrue(inspector.target_elf(arm, (3,)))
        self.assertFalse(inspector.target_elf(x86, (3,)))

    def test_static_archive_rejects_mixed_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "libmixed.a"
            path.write_bytes(self.archive(self.elf(183, 1), self.elf(62, 1)))
            result = inspector.Inspector(directory).file(path, "static_library")
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual({"AArch64": 1, "x86_64": 1}, result["architectures"])

    def test_selected_tree_scans_unexpected_x86_shared_object(self):
        with tempfile.TemporaryDirectory() as directory:
            selected = Path(directory) / "aarch64-unknown-linux-gnueabi"
            selected.mkdir()
            path = selected / "libunexpected.so"
            path.write_bytes(self.elf(62))
            scan = inspector.Inspector(directory)
            candidates = scan.binary_tree(selected, "OpenUSD", [])
            self.assertEqual([path], [item["path"] for item in candidates])
            result = scan.file(path, "shared_library")
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("x86_64", result["architecture"])

    def test_missing_library_is_blocked_without_placeholder_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            result = inspector.Inspector(directory).file(Path(directory) / "missing.so", "shared_library")
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("missing or non-regular file", result["reason"])

    def test_real_ue_rules_pin_openusd_and_unreal_arm64_name(self):
        build = (REPO_ROOT / "third_party/unreal-engine/Engine/Plugins/Runtime/USDCore/Source/ThirdParty/USD/BuildForLinux.sh").read_text()
        linux_rules = (REPO_ROOT / "third_party/unreal-engine/Engine/Source/Programs/UnrealBuildTool/Platform/Linux/UEBuildLinux.cs").read_text()
        wrapper = (REPO_ROOT / "third_party/unreal-engine/Engine/Plugins/Runtime/USDCore/Source/UnrealUSDWrapper/UnrealUSDWrapper.Build.cs").read_text()
        self.assertIn("OPENUSD_VERSION=24.05", build)
        self.assertIn("ARCH_NAME=x86_64-unknown-linux-gnu", build)
        self.assertIn("UnrealArch.Arm64,         \"aarch64-unknown-linux-gnueabi\"", linux_rules)
        self.assertIn("Target.Platform == UnrealTargetPlatform.Linux", wrapper)
        self.assertIn("Target.Architecture.LinuxName", wrapper)

    def test_real_inventory_is_structured_and_blocked(self):
        report = inspector.inspect_dependencies(REPO_ROOT / "third_party/unreal-engine")
        self.assertEqual("BLOCKED", report["status"])
        self.assertEqual("24.05", report["versions_required"]["OpenUSD"])
        self.assertEqual("aarch64-unknown-linux-gnueabi", report["target"]["architecture"])
        policy_ids = {item["id"] for item in report["policies"]}
        self.assertIn("build-architecture", policy_ids)
        self.assertIn("wrapper-linux-arm64-branch", policy_ids)
        self.assertIn("python-discovery", policy_ids)
        self.assertTrue(report["required_checks"])
        self.assertTrue(all(item["status"] in ("PASS", "BLOCKED") for item in report["requirements"]))
 
    def test_inventory_has_deterministic_component_summary(self):
        report = inspector.inspect_dependencies(REPO_ROOT / "third_party/unreal-engine")
        components = report["components"]
        self.assertEqual(list(inspector.COMPONENT_ORDER), [item["id"] for item in components])
        expected_fields = {"id", "version", "depends_on", "status", "required_entries", "pass", "blocked"}
        for item in components:
            self.assertEqual(expected_fields, set(item))
            self.assertEqual(item["required_entries"], item["pass"] + item["blocked"])
            self.assertEqual(list(inspector.COMPONENT_DEPENDENCIES[item["id"]]), item["depends_on"])
            self.assertEqual("PASS" if item["required_entries"] and not item["blocked"] else "BLOCKED", item["status"])
        self.assertGreater(next(item for item in components if item["id"] == "Boost.Python")["required_entries"], 0)
        self.assertGreater(next(item for item in components if item["id"] == "native-toolchain")["required_entries"], 0)
        self.assertEqual(components, inspector.inspect_dependencies(REPO_ROOT / "third_party/unreal-engine")["components"])

    def test_cli_writes_blocked_artifact_and_returns_failure(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as artifacts:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--ue-root", root, "--artifact-dir", artifacts],
                capture_output=True, text=True)
            self.assertEqual(1, result.returncode)
            self.assertTrue(result.stdout.startswith("BLOCKED usd-native-dependency-inventory"))
            report_path = Path(result.stdout.splitlines()[0].split("report=", 1)[1])
            self.assertTrue(report_path.is_file())
            self.assertEqual("BLOCKED", json.loads(report_path.read_text())["status"])
            artifact = json.loads(report_path.read_text())
            self.assertEqual([item["id"] for item in artifact["components"]], list(inspector.COMPONENT_ORDER))


if __name__ == "__main__":
    unittest.main()
