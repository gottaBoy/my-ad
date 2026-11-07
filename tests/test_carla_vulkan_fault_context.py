import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FaultContextTest(unittest.TestCase):
    def test_context_contract_is_read_only_and_bounded(self):
        source = (ROOT / "scripts/carla/gdb_vulkan_device_capture.py").read_text()
        self.assertIn("Read-only procfs/GDB register snapshot", source)
        self.assertIn("threads[:128]", source)
        self.assertIn("/proc/{pid}/task/{tid}/status", source)
        self.assertIn("/proc/{pid}/task/{tid}/stat", source)
        self.assertIn("FTaskTagScope::ActiveTaskTag", source)
        self.assertIn("LowLevelTasks::FTask::ActiveTask", source)
        self.assertIn("UE::Tasks::Private::CurrentTask", source)
        self.assertIn("typed PackedData address", source)
        self.assertIn('"utf-16-le"', source)
        self.assertIn("no inferior calls", source)
        self.assertNotIn("gdb.execute(", source)

    def test_context_fixture_preserves_thread_environment_fields(self):
        context = {
            "schema_version": 2,
            "signal": "SIGSEGV",
            "selected_thread_name": "Backgro-rker #4",
            "registers": {"pc": "0x1", "sp": "0x2", "tpidr_el0": None},
            "selected_thread_procfs": {
                "status": {"Cpus_allowed_list": "0-15"},
                "stat": {"processor": 3},
            },
            "ue_task_context": {
                "task_tag": {"value": 536870912, "names": ["EWorkerThread"]},
                "low_level_active_task": {
                    "address": "0x10", "debug_name": "PipelineTask",
                },
                "ue_tasks_current_task": {"address": "0x20"},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fault-thread-context.json"
            path.write_text(json.dumps(context))
            loaded = json.loads(path.read_text())
        self.assertEqual("SIGSEGV", loaded["signal"])
        self.assertEqual(3, loaded["selected_thread_procfs"]["stat"]["processor"])
        self.assertEqual("0-15", loaded["selected_thread_procfs"]["status"]["Cpus_allowed_list"])
        self.assertEqual(
            "PipelineTask",
            loaded["ue_task_context"]["low_level_active_task"]["debug_name"],
        )
