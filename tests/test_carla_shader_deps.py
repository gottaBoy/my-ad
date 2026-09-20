import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/carla/check-shader-output.py"
spec = importlib.util.spec_from_file_location("check_shader_output", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class ShaderOutputTest(unittest.TestCase):
    def spirv(self):
        return struct.pack("<10I", 0x07230203, 0x00010000, 0, 2, 0,
                           (5 << 16) | 15, 0, 1, 0x6E69616D, 0)

    def dxil(self):
        program = struct.pack("<2I4s3I", 0x10060, 6, b"DXIL", 0x100, 16, 0)
        part = b"DXIL" + struct.pack("<I", len(program)) + program
        return b"DXBC" + bytes(16) + struct.pack("<4I", 1, 36 + len(part), 1, 36) + part

    def test_accepts_expected_container_structure(self):
        checker.check_spirv(self.spirv())
        checker.check_dxil(self.dxil())

    def test_rejects_empty_text_and_truncated_output(self):
        for check, valid in ((checker.check_spirv, self.spirv()), (checker.check_dxil, self.dxil())):
            for data in (b"", b"compilation succeeded", valid[:-1]):
                with self.subTest(check=check.__name__, data=data):
                    with self.assertRaises(ValueError):
                        check(data)

    def test_spirv_requires_an_entry_point(self):
        with self.assertRaisesRegex(ValueError, "entry point"):
            checker.check_spirv(self.spirv()[:20])

    def test_spirv_rejects_invalid_header_fields(self):
        for field, value in ((0, 0), (1, 0x00020000), (3, 0), (4, 1)):
            data = bytearray(self.spirv())
            struct.pack_into("<I", data, field * 4, value)
            with self.assertRaises(ValueError):
                checker.check_spirv(data)

    def test_spirv_rejects_zero_and_oversized_instruction_lengths(self):
        for instruction in (15, (100 << 16) | 15):
            data = self.spirv()[:20] + struct.pack("<I", instruction)
            with self.assertRaisesRegex(ValueError, "instruction length"):
                checker.check_spirv(data)

    def test_dxil_validates_part_offsets_and_payload(self):
        bad_offset = self.dxil()[:32] + struct.pack("<I", 4) + self.dxil()[36:]
        bad_size = self.dxil()[:40] + struct.pack("<I", 999) + self.dxil()[44:]
        no_program = self.dxil()[:36] + b"STAT" + self.dxil()[40:]
        for data in (bad_offset, bad_size, no_program):
            with self.assertRaises(ValueError):
                checker.check_dxil(data)

    def test_build_script_rejects_unknown_mode_before_building(self):
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/carla/build-arm64-shader-deps.sh"), "unknown"],
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)
        self.assertIn("Usage:", result.stderr)

    def test_make_entrypoint_runs_in_the_build_container(self):
        result = subprocess.run(
            ["make", "-n", "carla-shader-deps", "JOBS=3", "SHADER_DEP=hlslcc"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=3", result.stdout)
        self.assertIn("build-arm64-shader-deps.sh hlslcc", result.stdout)

    def test_worker_entrypoint_runs_in_the_build_container(self):
        result = subprocess.run(
            ["make", "-n", "carla-scw", "JOBS=3"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=3", result.stdout)
        self.assertIn("build-arm64-scw.sh", result.stdout)

    def test_source_patch_supports_apply_and_already_applied_checks(self):
        patch = REPO_ROOT / "scripts/carla/patches/shaderconductor-native-arm64.patch"
        source = (
            "\tforeach(flagVar\n"
            "\t\tCMAKE_C_FLAGS CMAKE_CXX_FLAGS)\n"
            "\t\tset(${flagVar} \"${${flagVar}} -W -Wall -Werror -Wno-deprecated\")\n"
            "\t\tif(NOT (ANDROID OR IOS))\n"
            "\t\t\tset(${flagVar} \"${${flagVar}} -msse2\")\n"
            "\t\tendif()\n\tendforeach()\n\n"
            "\t\t\t\tset(CMAKE_RC_FLAGS \"${CMAKE_RC_FLAGS} --target=elf64-x86-64\")\n"
            "\t\t\tendif()\n\t\tendif()\n\telse()\n\t\tforeach(flagVar\n"
            "\t\t\tCMAKE_C_FLAGS CMAKE_CXX_FLAGS)\n"
            "\t\t\tset(${flagVar} \"${${flagVar}} -m32\")\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Engine/Source/ThirdParty/ShaderConductor/ShaderConductor/Source/CMakeLists.txt"
            target.parent.mkdir(parents=True)
            target.write_text(source)
            for flags in (("--check",), (), ("--reverse", "--check")):
                subprocess.run(["git", "apply", *flags, str(patch)], cwd=directory,
                               capture_output=True, text=True, check=True)
            self.assertIn("SC_ARCH_NAME MATCHES", target.read_text())
            target.write_text(source.replace("if(NOT (ANDROID OR IOS))", "if(custom_patch)"))
            result = subprocess.run(["git", "apply", "--check", str(patch)], cwd=directory,
                                    capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("if(custom_patch)", target.read_text())


if __name__ == "__main__":
    unittest.main()
