import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/carla/analyze_vulkan_pipeline_history.py"
spec = importlib.util.spec_from_file_location("pipeline_history_test", SCRIPT)
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)


FIXTURE = """\
schema=1
phase=before_compute_create
target=main_target
first_sequence=1
last_sequence=4
dropped=0
event=1 call=1 kind=graphics phase=begin thread=10 cache=0x1 layout=0x2 render_pass=0x3 flags=0 stage_count=2 result=1 stage0=1,0x11,vs_entry stage1=16,0x12,ps_entry
event=2 call=1 kind=graphics phase=result thread=10 cache=0x1 layout=0x2 render_pass=0x3 flags=0 stage_count=2 result=0 stage0=1,0x11,vs_entry stage1=16,0x12,ps_entry
event=3 call=3 kind=graphics phase=begin thread=11 cache=0x1 layout=0x4 render_pass=0x3 flags=0 stage_count=2 result=1 stage0=1,0x21,vs2 stage1=16,0x22,ps2
graphics_state call=3 replay_ready=0 root_pnext=0 subpass=0 stages=2 vertex=1 raster=1
event=4 call=4 kind=compute phase=begin thread=12 cache=0x1 layout=0x5 render_pass=0x0 flags=0 stage_count=1 result=1 stage0=32,0x31,main_target
module=0x21 hash=VS_HASH
module=0x22 hash=PS_HASH
end=1
"""


class PipelineHistoryTest(unittest.TestCase):
    def test_target_snapshot_precedes_shader_file_work_and_is_single_writer(self):
        source = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
                  / "VulkanPipeline.cpp").read_text()
        body = source.split("FVulkanPipelineStateCacheManager::CreateComputePipelineFromShader(", 1)[1]
        self.assertLess(body.index("CarlaPipelineHistory::DumpBeforeTarget(PipelineInfo);"),
                        body.index("FString DiagnosticDir;"))
        self.assertLess(body.index("CarlaPipelineHistory::DumpBeforeTarget(PipelineInfo);"),
                        body.index("Result = CarlaPipelineHistory::CreateComputePipeline("))
        self.assertIn("ClaimedTargets.Contains(ClaimKey)", source)
        self.assertIn("ClaimedTargets.Add(ClaimKey)", source)
        self.assertIn("ConfiguredTargets, false))", source)
        self.assertIn('bCheckpoint ? TEXT("checkpoint") : TEXT("selected_target")', source)
        self.assertIn("if (!Enabled())", source)

    def test_unmatched_graphics_are_correlated_to_shader_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.txt"
            path.write_text(FIXTURE)
            result = history.analyze(path, "main_target")
        self.assertEqual("CAPTURED_HISTORY", result["status"])
        self.assertEqual(4, result["event_count"])
        self.assertEqual(1, result["result_count"])
        self.assertEqual(1, result["unmatched_graphics_count"])
        unmatched = result["unmatched_graphics"][0]
        self.assertEqual(11, unmatched["thread"])
        self.assertEqual("VS_HASH", unmatched["stages"][0]["shader_hash"])
        self.assertEqual("PS_HASH", unmatched["stages"][1]["shader_hash"])
        self.assertEqual("0", unmatched["fixed_state"]["replay_ready"])
        self.assertEqual(1, result["fixed_state_count"])
        self.assertFalse(result["replay_ready"])
        self.assertEqual(1, len(result["target_events"]))

    def test_bad_schema_and_duplicate_result_are_rejected_or_accounted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.txt"
            path.write_text("schema=2\n")
            with self.assertRaises(ValueError):
                history.analyze(path, "main_target")
            path.write_text(FIXTURE.replace("event=2 call=1", "event=2 call=9"))
            result = history.analyze(path, "main_target")
            self.assertEqual(2, result["unmatched_graphics_count"])

    def test_checkpoint_is_explicit_and_cannot_claim_exact_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.txt"
            path.write_text(FIXTURE.replace("target=main_target",
                                            "target=main_target\ntrigger=checkpoint"))
            result = history.analyze(path, "main_target")
            self.assertEqual("CAPTURED_CHECKPOINT", result["status"])
            self.assertFalse(result["replay_ready"])
            self.assertEqual("checkpoint", result["trigger"])
            with self.assertRaisesRegex(ValueError, "target differs"):
                history.analyze(path, "other_target")

    def test_truncated_or_unlinked_fixed_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.txt"
            path.write_text(FIXTURE.replace("end=1\n", ""))
            with self.assertRaises(ValueError):
                history.analyze(path, "main_target")
            path.write_text(FIXTURE.replace("graphics_state call=3", "graphics_state call=99"))
            with self.assertRaisesRegex(ValueError, "lacks a graphics begin"):
                history.analyze(path, "main_target")

    def test_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.txt"
            generated = "\n".join(
                f"event={index} call={index} kind=graphics phase=begin thread=1 cache=0x1 layout=0x2 render_pass=0x3 flags=0 stage_count=0 result=1"
                for index in range(5000)
            )
            path.write_text(FIXTURE.replace("end=1", generated + "\nend=1"))
            with self.assertRaisesRegex(ValueError, "bounded"):
                history.analyze(path, "main_target")


if __name__ == "__main__":
    unittest.main()
