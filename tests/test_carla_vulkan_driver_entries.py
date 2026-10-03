import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/analyze_vulkan_driver_entries.py"
spec = importlib.util.spec_from_file_location("vulkan_driver_entries", SCRIPT)
entries = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entries)


def module(name):
    text = name.encode("ascii") + b"\0"
    text += b"\0" * (-len(text) % 4)
    instruction = struct.pack("<III", ((3 + len(text) // 4) << 16) | 15, 0, 1) + text
    return struct.pack("<5I", 0x07230203, 0x00010600, 0, 4, 0) + instruction


class DriverEntryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name)
        shaders = self.run_dir / "graphics-shaders"
        shaders.mkdir()
        (shaders / "module-123.spv").write_bytes(module("main_vertex"))
        self.graphics = self.run_dir / "driver-graphics-1-42.enter.txt"
        self.graphics.write_text(
            "schema=1\nphase=enter\nthread=42\nlayout=0x2\nrender_pass=0x3\n"
            "cache=0x0\nstages=1\nstage0=1,0x4,main_vertex "
            "hash=" + "A" * 40 + " file=graphics-shaders/module-123.spv\n"
            "render_pass_state=attachments=1\nlayout_state=sets=1\n"
            "replay_ready=0\ncache_data=null\ncache_bytes=0\nend=1\n"
        )
        (self.run_dir / "driver-compute-2-43-main_compute.enter.txt").write_text(
            "schema=1\nphase=enter\nentry=main_compute\nend=1\n"
        )
        self.compute_result = self.run_dir / "driver-compute-2-43-main_compute.result.txt"
        self.compute_result.write_text("schema=1\nphase=return\nresult=0\nend=1\n")

    def test_correlates_exact_module_and_unreturned_graphics(self):
        report = entries.analyze(self.run_dir)
        self.assertEqual("CAPTURED_DRIVER_ENTRY", report["status"])
        self.assertEqual(1, report["validated_modules"])
        self.assertEqual((1, 0, 1, 1), (
            report["graphics_enters"], report["graphics_returns"],
            report["compute_enters"], report["compute_returns"]))
        self.assertEqual("main_vertex", report["unreturned"][0]["stages"][0]["entry"])
        self.assertFalse(report["replay_ready"])

    def test_rejects_missing_or_mismatched_spirv(self):
        module_path = self.run_dir / "graphics-shaders/module-123.spv"
        module_path.write_bytes(module("different_entry"))
        with self.assertRaisesRegex(ValueError, "entry differs"):
            entries.analyze(self.run_dir)
        module_path.unlink()
        with self.assertRaisesRegex(ValueError, "missing exact module"):
            entries.analyze(self.run_dir)

    def test_rejects_path_escape_and_multiple_unreturned_calls(self):
        self.graphics.write_text(self.graphics.read_text().replace(
            "graphics-shaders/module-123.spv", "../module-123.spv"))
        with self.assertRaisesRegex(ValueError, "missing exact stage"):
            entries.analyze(self.run_dir)
        self.graphics.write_text(self.graphics.read_text().replace(
            "../module-123.spv", "graphics-shaders/module-123.spv"))
        self.compute_result.unlink()
        with self.assertRaisesRegex(ValueError, "multiple unreturned"):
            entries.analyze(self.run_dir)

    def test_rejects_partial_marker_and_unlinked_cache(self):
        self.graphics.write_text(self.graphics.read_text().replace("end=1\n", ""))
        with self.assertRaisesRegex(ValueError, "partial marker"):
            entries.analyze(self.run_dir)
        self.graphics.write_text(self.graphics.read_text() + "end=1\n")
        self.graphics.write_text(self.graphics.read_text().replace(
            "cache_data=null\ncache_bytes=0",
            f"cache_data={self.graphics.with_name('wrong.cache.bin')}\ncache_bytes=10"))
        with self.assertRaisesRegex(ValueError, "unlinked cache"):
            entries.analyze(self.run_dir)

    def test_validates_captured_cache_size(self):
        cache = self.run_dir / "driver-graphics-1-42.cache.bin"
        cache.write_bytes(b"example cache")
        self.graphics.write_text(self.graphics.read_text().replace(
            "cache_data=null\ncache_bytes=0",
            f"cache_data={cache}\ncache_bytes={cache.stat().st_size}"))
        self.assertEqual("CAPTURED_DRIVER_ENTRY", entries.analyze(self.run_dir)["status"])
        self.graphics.write_text(self.graphics.read_text().replace(
            str(cache), f"/container/{self.run_dir.parent.name}/{self.run_dir.name}/{cache.name}"))
        self.assertEqual("CAPTURED_DRIVER_ENTRY", entries.analyze(self.run_dir)["status"])
        cache.write_bytes(b"short")
        with self.assertRaisesRegex(ValueError, "cache byte count mismatch"):
            entries.analyze(self.run_dir)

    def test_rejects_unsupported_graphics_stage(self):
        self.graphics.write_text(self.graphics.read_text().replace(
            "stage0=1,", "stage0=64,"))
        with self.assertRaisesRegex(ValueError, "entry differs from exact module"):
            entries.analyze(self.run_dir)

    def test_validates_actual_render_pass_object_graph(self):
        folder = self.run_dir / "renderpasses"
        folder.mkdir()
        snapshot = folder / "renderpass-123-0x3.txt"
        snapshot.write_text(
            "schema=1\napi=vkCreateRenderPass2KHR\nhandle=0x3\nflags=0\n"
            "attachments=1\nsubpasses=1\ndependencies=0\n"
            "attachment0=0,122,1,2,1,2,1,2,2\n"
            "subpass0_flags=0\nsubpass0_bind_point=0\nsubpass0_inputs=0\n"
            "subpass0_colors=1\nsubpass0_resolves=0\nsubpass0_depth=0\n"
            "subpass0_preserves=0\nsubpass0_view_mask=0\n"
            "subpass0_color0=0,2,1\ncorrelated_masks=0\n"
            "capture_complete=1\nend=1\n")
        self.graphics.write_text(self.graphics.read_text().replace(
            "render_pass_state=attachments=1\n",
            "render_pass_state=attachments=1\n"
            "render_pass_file=renderpasses/renderpass-123-0x3.txt\n"))
        report = entries.analyze(self.run_dir)
        self.assertEqual(1, report["validated_render_pass_snapshots"])
        self.assertEqual("complete", report["unreturned"][0]["render_pass_snapshot"]["status"])
        snapshot.write_text(snapshot.read_text().replace("subpass0_colors=1", "subpass0_colors=2"))
        with self.assertRaisesRegex(ValueError, "missing render-pass references"):
            entries.analyze(self.run_dir)
        snapshot.write_text(snapshot.read_text().replace("subpass0_colors=2", "subpass0_colors=1")
                            .replace("handle=0x3", "handle=0x4"))
        with self.assertRaisesRegex(ValueError, "render-pass identity mismatch"):
            entries.analyze(self.run_dir)

    def test_rejects_render_pass_path_escape(self):
        self.graphics.write_text(self.graphics.read_text().replace(
            "render_pass_state=attachments=1\n",
            "render_pass_state=attachments=1\nrender_pass_file=../renderpass-123-0x3.txt\n"))
        with self.assertRaisesRegex(ValueError, "invalid render-pass path"):
            entries.analyze(self.run_dir)


if __name__ == "__main__":
    unittest.main()
