import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/prepare_vulkan_graphics_replay.py"
spec = importlib.util.spec_from_file_location("graphics_replay", SCRIPT)
graphics = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
    spec.loader.exec_module(graphics)


def module(name, model):
    text = name.encode("ascii") + b"\0"
    text += b"\0" * (-len(text) % 4)
    return (struct.pack("<5I", 0x07230203, 0x00010600, 0, 4, 0)
            + struct.pack("<III", ((3 + len(text) // 4) << 16) | 15, model, 1)
            + text)


class GraphicsReplayInputTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "graphics-shaders").mkdir()
        (self.root / "renderpasses").mkdir()
        (self.root / "graphics-shaders/module-1.spv").write_bytes(module("vs_main", 0))
        (self.root / "graphics-shaders/module-2.spv").write_bytes(module("ps_main", 4))
        self.cache = self.root / "driver-graphics-1-4.cache.bin"
        self.cache.write_bytes(b"example cache")
        self.render_pass = self.root / "renderpasses/renderpass-5-0x6.txt"
        self.render_pass.write_text(
            "schema=1\napi=vkCreateRenderPass2KHR\nhandle=0x6\nflags=0\n"
            "attachments=1\nsubpasses=1\ndependencies=0\ncorrelated_masks=0\n"
            "attachment0=0,122,1,2,1,2,1,2,2\n"
            "subpass0_flags=0\nsubpass0_bind_point=0\nsubpass0_inputs=0\n"
            "subpass0_colors=1\nsubpass0_resolves=0\nsubpass0_depth=0\n"
            "subpass0_preserves=0\nsubpass0_view_mask=0\n"
            "subpass0_color0=0,2,0\ncapture_complete=1\nend=1\n")
        self.marker = self.root / "driver-graphics-1-4.enter.txt"
        self.marker.write_text(
            "schema=1\nphase=enter\nlayout=0x1\nrender_pass=0x6\ncache=0x2\n"
            "stages=2\n"
            "stage0=1,0x4,vs_main hash=" + "A" * 40
            + " file=graphics-shaders/module-1.spv\n"
            "stage1=16,0x5,ps_main hash=" + "B" * 40
            + " file=graphics-shaders/module-2.spv\n"
            "render_pass_state=attachments=1\n"
            "layout_state=sets=2 descriptor_hash=1 bindless=0 push_constant_ranges=0"
            " set0_bindings=1 set0_binding0=0,8,1,1,0"
            " set1_bindings=1 set1_binding0=0,8,1,16,0\n"
            "render_pass_file=renderpasses/renderpass-5-0x6.txt\nreplay_ready=0\n"
            "flags=0\nsubpass=0\nbase_present=0\nbase_index=0\nroot_pnext=0\n"
            "stage0_flags=0 pnext=0 specialization=0\n"
            "stage1_flags=0 pnext=0 specialization=0\n"
            "vertex flags=0 pnext=0 bindings=0 attributes=0\n"
            "assembly flags=0 pnext=0 topology=3 restart=0\n"
            "viewport flags=0 pnext=0 viewports=1 scissors=1 static_viewports=0 static_scissors=0\n"
            "raster flags=0 pnext=0 depth_clamp=0 discard=0 polygon=0 cull=0 front=1"
            " depth_bias=0 bias_constant=0 bias_clamp=0 bias_slope=0 line_width=1\n"
            "multisample flags=0 pnext=0 samples=1 sample_shading=0"
            " min_sample_shading=0 sample_mask=0 alpha_to_coverage=0 alpha_to_one=0\n"
            "depth flags=0 pnext=0 test=0 write=0 compare=7 bounds=0"
            " stencil=0 min_bounds=0 max_bounds=0\n"
            "stencil0=0,0,0,7,255,255,0\nstencil1=0,0,0,7,255,255,0\n"
            "blend flags=0 pnext=0 logic=0 op=0 attachments=1 constants=1,1,1,1\n"
            "blend_attachment0=0,1,0,0,1,0,0,15\n"
            "dynamic flags=0 pnext=0 states=2\n"
            "dynamic_state0=0\ndynamic_state1=1\n"
            f"cache_data={self.cache}\ncache_bytes={self.cache.stat().st_size}\nend=1\n"
        )
        (self.root / "driver-compute-1-7.enter.txt").write_text(
            "schema=1\nphase=enter\nentry=main_compute\nend=1\n")
        (self.root / "driver-compute-1-7.result.txt").write_text(
            "schema=1\nphase=return\nresult=0\nend=1\n")

    def test_generates_bounded_graphics_creation_input(self):
        report, header = graphics.prepare(self.root)
        self.assertFalse(report["replay_ready"])
        self.assertIn('CAPTURE_VS_ENTRY "vs_main"', header)
        self.assertIn('CAPTURE_PS_ENTRY "ps_main"', header)
        self.assertIn("cap_renderpass", header)
        self.assertIn(".subpassCount=1", header)
        self.assertIn(".setLayoutCount", (ROOT / "scripts/carla/vulkan-graphics-replay.c").read_text())

    def test_rejects_unknown_extension_and_incomplete_render_pass(self):
        self.marker.write_text(self.marker.read_text().replace("root_pnext=0", "root_pnext=1"))
        with self.assertRaisesRegex(ValueError, "root state"):
            graphics.prepare(self.root)
        self.marker.write_text(self.marker.read_text().replace("root_pnext=1", "root_pnext=0"))
        self.render_pass.write_text(self.render_pass.read_text().replace(
            "capture_complete=1", "capture_complete=0"))
        with self.assertRaisesRegex(ValueError, "complete single-subpass"):
            graphics.prepare(self.root)

    def test_rejects_unlinked_cache_and_missing_shader(self):
        self.cache.unlink()
        with self.assertRaisesRegex(ValueError, "unlinked cache"):
            graphics.prepare(self.root)
        self.cache.write_bytes(b"example cache")
        (self.root / "graphics-shaders/module-2.spv").unlink()
        with self.assertRaisesRegex(ValueError, "missing exact module"):
            graphics.prepare(self.root)

    def test_rejects_unknown_layout_fields(self):
        self.marker.write_text(self.marker.read_text().replace(
            "set1_bindings=1", "set1_bindings=1 set2_binding0=0,8,1,16,0"))
        with self.assertRaisesRegex(ValueError, "unexpected descriptor layout"):
            graphics.prepare(self.root)

    def test_two_phase_runner_rejects_unknown_mode(self):
        script = ROOT / "scripts/carla/probe-vulkan-graphics-replay.sh"
        result = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                                env={**os.environ, "CARLA_GRAPHICS_REPLAY_ACTION": "other"})
        self.assertEqual(64, result.returncode)
        text = script.read_text()
        self.assertIn("spirv-val --target-env vulkan1.3", text)
        self.assertIn("sha256sum --check inputs.sha256", text)
        self.assertIn("ue_exact_replay", text)


if __name__ == "__main__":
    unittest.main()
