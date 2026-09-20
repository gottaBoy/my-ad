import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("vulkan_readback_tested", SCRIPTS / "check_vulkan_readback.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def fixture_frame(index):
    colors = ((bytes((255, 0, 0, 255)), bytes((0, 0, 255, 255))),
              (bytes((0, 255, 0, 255)), bytes((255, 255, 255, 255))))
    left, right = colors[index]
    return (left * 32 + right * 32) * 48


def fixture_device():
    return dict(schema_version=1, device_name="NVIDIA GB10", vendor_id=0x10DE,
                device_id=0x2E12, api_version=1 << 22 | 4 << 12, driver_version=123,
                queue_family=0, graphics_queue=True, width=64, height=48, frames=2,
                runtime_descriptor_array=True, descriptor_binding_partially_bound=True,
                sampled_image_update_after_bind=True)


class VulkanReadbackTest(unittest.TestCase):
    def test_make_uses_gpu_profile_and_native_compose_service(self):
        result = subprocess.run(["make", "-n", "carla-vulkan"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("--profile gpu run --rm -T carla-vulkan", result.stdout)
        compose = (ROOT / "compose.carla-arm64.yaml").read_text()
        self.assertIn("profiles: [\"gpu\"]", compose)
        self.assertIn("NVIDIA_DRIVER_CAPABILITIES: graphics,utility,compute", compose)
        self.assertIn("capabilities: [gpu]", compose)

    def test_device_requires_real_vendor_and_exact_types(self):
        PROBE.validate_device(fixture_device())
        for key, value in (("vendor_id", 0x10005), ("device_name", "llvmpipe"),
                           ("graphics_queue", 1), ("width", True), ("frames", 0),
                           ("api_version", 0), ("driver_version", False)):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    PROBE.validate_device({**fixture_device(), key: value})

    def test_descriptor_observations_are_not_a_shader_gate(self):
        value = fixture_device()
        value["runtime_descriptor_array"] = False
        PROBE.validate_device(value)
        self.assertIn("not UE shaders or CARLA sensors", PROBE.SCOPE)

    def test_every_pixel_is_checked_in_both_frames(self):
        for frame in (0, 1):
            raw = fixture_frame(frame)
            self.assertEqual(3072, PROBE.validate_frame(raw, frame)["pixels_checked"])
            for offset in (0, 127, 128, len(raw) - 1):
                damaged = bytearray(raw)
                damaged[offset] ^= 1
                with self.subTest(frame=frame, offset=offset):
                    with self.assertRaises(ValueError):
                        PROBE.validate_frame(bytes(damaged), frame)

    def test_empty_black_and_unchanged_frames_fail(self):
        for raw in (b"", bytes(64 * 48 * 4), fixture_frame(0)[:-1], fixture_frame(0)):
            with self.assertRaises(ValueError):
                PROBE.validate_frame(raw, 1)

    def test_png_preserves_readback_bytes(self):
        raw = fixture_frame(0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            PROBE.write_png(path, raw)
            content = path.read_bytes()
        self.assertEqual(b"\x89PNG\r\n\x1a\n", content[:8])
        offset, data = 8, b""
        while offset < len(content):
            size = struct.unpack(">I", content[offset:offset + 4])[0]
            kind = content[offset + 4:offset + 8]
            payload = content[offset + 8:offset + 8 + size]
            checksum = struct.unpack(">I", content[offset + 8 + size:offset + 12 + size])[0]
            self.assertEqual(checksum, zlib.crc32(kind + payload) & 0xFFFFFFFF)
            if kind == b"IDAT":
                data += payload
            offset += 12 + size
        rows = zlib.decompress(data)
        self.assertEqual(raw, b"".join(rows[y * 257 + 1:(y + 1) * 257] for y in range(48)))

    def test_native_elf_rejects_x86_and_truncated_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "program"
            header = bytearray(20)
            header[:6] = b"\x7fELF\x02\x01"
            for machine, expected in ((183, True), (62, False)):
                header[18:20] = machine.to_bytes(2, "little")
                path.write_bytes(header)
                self.assertEqual(expected, PROBE.native_elf(path))
            path.write_bytes(b"\x7fELF")
            self.assertFalse(PROBE.native_elf(path))

    def test_preflight_failure_writes_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(1, PROBE.run(root / "missing", root / "run", 1))
            report = json.loads((root / "run/stage-report.json").read_text())
            self.assertEqual("FAIL", report["status"])
            self.assertEqual(PROBE.STAGE_ID, report["stage_id"])
            self.assertEqual(list(PROBE.CHECKS), report["required_checks"])
            self.assertTrue(report["errors"])

    def test_existing_evidence_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "stage-report.json"
            report.write_text("previous")
            with self.assertRaises(ValueError):
                PROBE.run(root / "missing", root, 1)
            self.assertEqual("previous", report.read_text())

    def test_dangling_report_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "not-created.json"
            (root / "stage-report.json").symlink_to(target)
            with self.assertRaises(ValueError):
                PROBE.run(root / "missing", root, 1)
            self.assertFalse(target.exists())

    def test_duplicate_or_nonfinite_device_json_is_rejected(self):
        for text in ('{"frames":2,"frames":0}', '{"driver_version":NaN}'):
            with self.assertRaises(ValueError):
                json.loads(text, object_pairs_hook=PROBE.unique_object,
                           parse_constant=PROBE.invalid_constant)

    def test_invalid_timeout_is_rejected_before_execution(self):
        for value in ("0", "-1", "abc", "1s"):
            result = subprocess.run(
                ["bash", str(SCRIPTS / "probe-arm64-vulkan.sh")],
                env={"PATH": "/usr/bin:/bin", "CARLA_VULKAN_TIMEOUT": value},
                capture_output=True, text=True,
            )
            self.assertEqual(64, result.returncode)


if __name__ == "__main__":
    unittest.main()
