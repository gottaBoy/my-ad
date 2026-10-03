import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


snapshot = load("fault_snapshot_test", SCRIPTS / "vulkan_fault_snapshot.py")


def fixture():
    return {
        "schema_version": 1, "capture_complete": True, "runtime_acceptance": False,
        "creation_api_hooks": False, "inferior_function_calls": False, "stop_reason": "vulkan-check",
        "assertion": {"file": "/source/Runtime/VulkanRHI/Private/VulkanCommands.cpp", "line": 532,
                      "expression": "Shader->GetShaderKey() == PendingGfxState->GetCurrentShaderKey(Stage)"},
        "fault_thread": {"num": 9}, "signal": None, "errors": [],
        "threads": [{"num": 9, "frames": [{"name": "RHISetShaderUniformBuffer", "library": None}]}],
        "graphics_binding": {
            "stage": 1, "buffer_index": 4, "requested_shader": {"key": 99},
            "pipeline_keys": [10, 11, 0, 0, 0], "shader_key_matches": False,
            "descriptor_sets": [[{"binding": 0}]], "descriptor_index_in_bounds": False,
            "read_errors": [],
        },
        "compute_creation": None,
    }


def traced_fixture():
    state = fixture()
    state["graphics_binding"].update(context_address="0x100", requested_shader_rhi_address="0x300",
                                    pipeline_address="0x200")
    state["graphics_command"] = {"address": "0x500", "list_address": "0x400", "read_errors": []}
    pipeline = {"address": "0x200", "keys": [10, 11, 0, 0, 0], "pixel_shader_address": "0x250"}
    state["graphics_trace"] = {
        "enabled": True, "event_count": 2, "dropped_events": 0, "errors": [],
        "events": [
            {"kind": "gfx-bind-applied", "sequence": 1, "context_address": "0x100", "thread_num": 9,
             "pipeline": pipeline, "submitted_pipeline": dict(pipeline),
             "command": {"address": "0x600", "list_address": "0x400", "read_errors": []}, "read_errors": []},
            {"kind": "gfx-parameters", "sequence": 2, "context_address": "0x100", "thread_num": 9,
             "pipeline": pipeline, "shader_rhi_address": "0x300",
             "command": dict(state["graphics_command"]), "read_errors": []},
        ],
    }
    return state


class FaultSnapshotTest(unittest.TestCase):
    def test_mismatch_remains_runtime_failure(self):
        result = snapshot.analyze(fixture())
        self.assertEqual("ue-graphics-binding", result["fault_bucket"])
        self.assertTrue(result["typed_graphics_contract"])
        self.assertFalse(result["shader_key_matches"])
        self.assertFalse(result["runtime_acceptance"])
        self.assertEqual((99, 11), (result["requested_key"], result["pending_key"]))

    def test_inconsistent_or_unobserved_capture_rejected(self):
        for key, value in (("capture_complete", False), ("runtime_acceptance", True),
                           ("creation_api_hooks", True), ("inferior_function_calls", True),
                           ("stop_reason", "observation-ended"), ("threads", [])):
            state = fixture()
            state[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                snapshot.analyze(state)
        state = fixture()
        state["graphics_binding"]["shader_key_matches"] = True
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            snapshot.analyze(state)

    def test_partial_optimized_state_is_not_a_proven_contract(self):
        state = fixture()
        state["graphics_binding"] = {"read_errors": ["Shader: optimized out"]}
        result = snapshot.analyze(state)
        self.assertFalse(result["typed_graphics_contract"])
        self.assertIn("optimized out", result["read_errors"][0])

    def test_nvidia_signal_is_separate_from_graphics_check(self):
        state = fixture()
        state.update(stop_reason="fatal-signal", assertion=None, signal="SIGSEGV",
                     graphics_binding=None, compute_creation={"exact_api_inputs": False, "read_errors": []})
        state["threads"][0]["frames"][0]["library"] = "/lib/libnvidia-glvkspirv.so.580.173.02"
        result = snapshot.analyze(state)
        self.assertEqual("nvidia-compute-compiler", result["fault_bucket"])
        self.assertFalse(result["exact_compute_api_inputs"])
        state["compute_creation"]["exact_api_inputs"] = True
        with self.assertRaisesRegex(ValueError, "provenance"):
            snapshot.analyze(state)

    def test_library_name_without_compute_caller_does_not_invent_compute(self):
        state = fixture()
        state.update(stop_reason="fatal-signal", assertion=None, signal="SIGSEGV", graphics_binding=None)
        state["threads"][0]["frames"][0]["library"] = "/lib/libnvidia-glvkspirv.so.580.173.02"
        self.assertEqual("unclassified", snapshot.analyze(state)["fault_bucket"])

    def test_assertion_requires_actual_vulkan_file(self):
        state = fixture()
        state["assertion"]["file"] = "/source/Core/AssertionMacros.cpp"
        with self.assertRaisesRegex(ValueError, "ungrounded"):
            snapshot.analyze(state)

    def test_bounds_and_pipeline_association_are_independently_checked(self):
        state = fixture()
        state["graphics_binding"]["descriptor_index_in_bounds"] = True
        with self.assertRaisesRegex(ValueError, "descriptor-index"):
            snapshot.analyze(state)
        state = fixture()
        state["graphics_binding"].update(pipeline_address="0x10", descriptor_pipeline_address="0x10",
                                        descriptor_pipeline_matches=True)
        self.assertTrue(snapshot.analyze(state)["descriptor_pipeline_matches"])
        state["graphics_binding"]["descriptor_pipeline_matches"] = False
        with self.assertRaisesRegex(ValueError, "descriptor-pipeline"):
            snapshot.analyze(state)

    def test_cli_preserves_capture_and_reports_diagnostic_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fault-capture.json"
            original = json.dumps(fixture())
            path.write_text(original)
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "vulkan_fault_snapshot.py"), "--run-dir", directory],
                capture_output=True, text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(original, path.read_text())
            self.assertFalse(json.loads(result.stdout)["runtime_acceptance"])

    def test_binding_history_correlates_actual_command_and_context(self):
        result = snapshot.analyze(traced_fixture())["graphics_trace"]
        self.assertEqual("correlated", result["status"])
        self.assertTrue(result["same_command_list"])
        self.assertTrue(result["bind_applied_matches_submitted"])
        self.assertTrue(result["bind_pipeline_matches_fault"])
        self.assertEqual((1, 2), (result["bind_sequence"], result["parameter_sequence"]))

    def test_other_context_or_shader_or_command_is_not_used_as_history(self):
        for key in ("context_address", "shader_rhi_address", "command"):
            state = traced_fixture()
            event = state["graphics_trace"]["events"][-1]
            event[key] = {"address": "0xother"} if key == "command" else "0xother"
            with self.subTest(key=key):
                self.assertEqual("not-correlated", snapshot.analyze(state)["graphics_trace"]["status"])

    def test_graphics_trace_budget_and_sequence_are_checked(self):
        for mutation in ("budget", "sequence", "disabled"):
            state = traced_fixture()
            if mutation == "budget":
                state["graphics_trace"]["event_count"] = 999
            elif mutation == "sequence":
                state["graphics_trace"]["events"][1]["sequence"] = 8
            else:
                state["graphics_trace"]["enabled"] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                snapshot.analyze(state)

    def test_cross_list_bind_is_reported_without_claiming_cause(self):
        state = traced_fixture()
        state["graphics_trace"]["events"][0]["command"]["list_address"] = "0x800"
        result = snapshot.analyze(state)["graphics_trace"]
        self.assertFalse(result["same_command_list"])
        self.assertEqual("correlated", result["status"])
        self.assertNotIn("root_cause", result)

    def test_optimized_command_node_does_not_hide_list_identity_or_claim_node_identity(self):
        state = traced_fixture()
        del state["graphics_command"]["address"]
        del state["graphics_trace"]["events"][1]["command"]["address"]
        result = snapshot.analyze(state)["graphics_trace"]
        self.assertEqual("correlated", result["status"])
        self.assertFalse(result["command_identity_observed"])
        self.assertTrue(result["same_command_list"])

    def test_other_thread_is_not_used_as_parameter_entry(self):
        state = traced_fixture()
        state["graphics_trace"]["events"][1]["thread_num"] = 88
        self.assertEqual("not-correlated", snapshot.analyze(state)["graphics_trace"]["status"])

    def test_reused_pipeline_address_without_same_shader_keys_is_not_a_match(self):
        state = traced_fixture()
        state["graphics_trace"]["events"][0]["pipeline"]["keys"] = [100, 101, 0, 0, 0]
        result = snapshot.analyze(state)["graphics_trace"]
        self.assertFalse(result["bind_pipeline_matches_fault"])


class Pointer:
    def __init__(self, address, fields):
        self.address, self.fields = address, fields

    def __int__(self):
        return self.address

    def __getitem__(self, name):
        return self.fields[name]

    def dereference(self):
        return self.fields


class FaultCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gdb = types.SimpleNamespace(
            error=RuntimeError, Breakpoint=type("Breakpoint", (), {"__init__": lambda *a, **kw: None}),
            newest_frame=Mock(), selected_inferior=Mock(), selected_thread=Mock(),
        )
        with patch.dict(sys.modules, {"gdb": cls.gdb}):
            cls.capture = load("fault_capture_test", SCRIPTS / "gdb_vulkan_fault_capture.py")

    def test_graphics_state_reads_keys_layout_and_bounds_without_function_calls(self):
        layout = Pointer(60, {"DescriptorSetLayout": types.SimpleNamespace(address=70)})
        pipeline = Pointer(40, {"ShaderKeys": [10, 11, 0, 0, 0], "VulkanShaders": [None] * 5,
                                "Layout": layout, "bUsesBindless": False})
        descriptor = Pointer(50, {"DescriptorSetsLayout": 70, "GfxPipeline": 40, "bUseBindless": False})
        pending = Pointer(20, {"CurrentPipeline": pipeline, "CurrentState": descriptor})
        context = Pointer(10, {"PendingGfxState": pending})
        variables = {"this": context, "Stage": 1, "BufferIndex": 4, "Shader": "requested", "ShaderRHI": 33}
        frame = types.SimpleNamespace(select=Mock(), read_var=lambda name: variables[name])
        with patch.object(self.capture, "shader_record", side_effect=lambda value, errors: {"key": 99}), \
             patch.object(self.capture, "layout_record", return_value=[[{"binding": 0}]]):
            record = self.capture.graphics_record(frame)
        self.assertFalse(record["shader_key_matches"])
        self.assertFalse(record["descriptor_index_in_bounds"])
        self.assertEqual("0x28", record["descriptor_pipeline_address"])
        self.assertTrue(record["descriptor_pipeline_matches"])
        self.assertTrue(record["descriptor_layout_matches"])
        self.assertEqual([], record["read_errors"])

    def test_first_vulkan_check_stops_before_failed_check_body(self):
        state = types.SimpleNamespace(capture=Mock(), report={"errors": []}, write=Mock(),
                                      pending_assertion=None, timer=Mock())
        values = {"File": types.SimpleNamespace(string=lambda **kw: "/source/Runtime/VulkanRHI/Private/VulkanCommands.cpp"),
                  "Expr": types.SimpleNamespace(string=lambda **kw: "key mismatch"), "Line": 532}
        frame = types.SimpleNamespace(read_var=lambda name: values[name])
        check = self.capture.FirstVulkanCheck(state)
        with patch.object(self.gdb, "newest_frame", return_value=frame):
            self.assertTrue(check.stop())
        state.capture.assert_not_called()
        with patch.object(self.capture, "state", state, create=True):
            self.capture.stopped(object())
        state.capture.assert_called_once_with(
            "vulkan-check", assertion={"file": values["File"].string(), "line": 532, "expression": "key mismatch"},
        )

    def test_nonvulkan_check_is_not_classified_as_render_fault(self):
        state = types.SimpleNamespace(capture=Mock(), report={"errors": []}, write=Mock())
        frame = types.SimpleNamespace(read_var=lambda _: types.SimpleNamespace(string=lambda **kw: "/Core/file.cpp"))
        with patch.object(self.gdb, "newest_frame", return_value=frame):
            self.assertFalse(self.capture.FirstVulkanCheck(state).stop())
        state.capture.assert_not_called()

    def test_bounded_cstrings_stop_at_null_instead_of_including_adjacent_literals(self):
        value = types.SimpleNamespace(string=lambda **kw: "actual\0unrelated\0")
        self.assertEqual("actual", self.capture.c_string(value, 64))

    def test_later_signal_cannot_replace_first_fault(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"CARLA_UE_FAULT_CAPTURE_DIR": root}):
            state = self.capture.CaptureState()
            state.report["stop_reason"] = "vulkan-check"
            state.capture("fatal-signal", fault_signal="SIGSEGV")
            self.assertEqual("vulkan-check", state.report["stop_reason"])
            self.assertIsNone(state.report["signal"])

    def test_capture_bounds_reject_invalid_counts(self):
        for value in (-1, 129):
            with self.assertRaises(ValueError):
                self.capture.bounded(value, 128, "fixture")
        with self.assertRaisesRegex(ValueError, "capacity"):
            self.capture.array_data({"ArrayNum": 2, "ArrayMax": 1})

    def test_entry_is_minimal_and_has_explicit_diagnostic_boundary(self):
        capture = (SCRIPTS / "gdb_vulkan_fault_capture.py").read_text()
        self.assertNotIn("vkCreateShaderModule", capture)
        self.assertNotIn("gdb.execute(", capture)
        self.assertNotIn("gdb.parse_and_eval(", capture)
        self.assertIn("set may-call-functions off", (SCRIPTS / "dump-ue-vulkan-fault.gdb").read_text())
        wrapper = (SCRIPTS / "probe-ue-vulkan-fault.sh").read_text()
        self.assertIn("CAPTURED_FAULT", wrapper)
        self.assertIn("Runtime acceptance: false", wrapper)
        dry = subprocess.run(["make", "-n", "carla-ue-vulkan-fault-gpu"], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(0, dry.returncode, dry.stderr)
        self.assertIn("--cap-add SYS_PTRACE", dry.stdout)

    def test_invalid_limits_do_not_start_debugger(self):
        for key, value in (("CARLA_UE_FAULT_TIMEOUT", "0"), ("CARLA_UE_FAULT_TIMEOUT", "9999"),
                           ("CARLA_UE_FAULT_PORT", "65533"), ("CARLA_UE_FAULT_TRACE_GFX", "yes")):
            result = subprocess.run(["bash", str(SCRIPTS / "probe-ue-vulkan-fault.sh")],
                                    env={**os.environ, key: value}, text=True, capture_output=True)
            self.assertEqual(64, result.returncode, result.stderr)

    def test_graphics_trace_defaults_off_and_keeps_bounded_ring(self):
        with tempfile.TemporaryDirectory() as root, \
             patch.dict(os.environ, {"CARLA_UE_FAULT_CAPTURE_DIR": root, "CARLA_UE_FAULT_TRACE_GFX": "0"}):
            state = self.capture.CaptureState()
            self.assertFalse(state.trace_enabled)
            trace = self.capture.GraphicsTrace(state, "bind")
            with patch.object(self.capture, "trace_bind_record",
                              side_effect=lambda _: {"kind": "gfx-bind-applied"}), \
                 patch.object(self.gdb, "selected_thread", return_value=types.SimpleNamespace(num=9)):
                for _ in range(260):
                    self.assertFalse(trace.stop())
            self.assertEqual(260, state.report["graphics_trace"]["event_count"])
            self.assertEqual(4, state.report["graphics_trace"]["dropped_events"])
            self.assertEqual(256, len(state.report["graphics_trace"]["events"]))
            self.assertEqual(5, state.report["graphics_trace"]["events"][0]["sequence"])

    def test_trace_metadata_error_stops_without_inventing_event(self):
        state = types.SimpleNamespace(report={"graphics_trace": {"events": [], "event_count": 0, "errors": []}},
                                      write=Mock())
        trace = self.capture.GraphicsTrace(state, "bind")
        with patch.object(self.capture, "trace_bind_record", side_effect=RuntimeError("optimized out")):
            self.assertTrue(trace.stop())
        self.assertEqual([], state.report["graphics_trace"]["events"])
        self.assertIn("optimized out", state.report["graphics_trace"]["errors"][0])

    def test_bind_capture_uses_live_caller_after_pending_state_is_applied(self):
        pipeline = Pointer(40, {"ShaderKeys": [10, 0, 0, 0, 0], "VulkanShaders": [0] * 5})
        pending = Pointer(20, {"CurrentPipeline": pipeline, "CurrentState": 50})
        context = Pointer(10, {"PendingGfxState": pending})
        variables = {"this": context, "Pipeline": pipeline}
        frame = types.SimpleNamespace(read_var=lambda name: variables[name])
        with patch.object(self.capture, "frames_from", return_value=[]), \
             patch.object(self.capture, "frame_record", return_value={"line": 560}):
            record = self.capture.trace_bind_record(frame)
        self.assertEqual("0xa", record["context_address"])
        self.assertEqual(record["pipeline"], record["submitted_pipeline"])
        self.assertEqual(560, record["location"]["line"])

    def test_optimized_command_list_is_recovered_from_live_execute_frame(self):
        command = types.SimpleNamespace(
            name=lambda: "FRHICommandSetGraphicsPipelineState::Execute",
            read_var=Mock(side_effect=RuntimeError("optimized out")),
        )
        pointer = Pointer(100, {"bExecuting": True})
        execute = types.SimpleNamespace(name=lambda: "FRHICommandListBase::Execute",
                                        read_var=lambda name: pointer)
        record = self.capture.command_record([command, execute])
        self.assertEqual("0x64", record["list_address"])
        self.assertTrue(record["list_executing"])
        self.assertNotIn("address", record)
        self.assertEqual(2, len(record["unavailable_fields"]))
        self.assertEqual([], record["read_errors"])

    def test_parameter_trace_selects_only_missing_pixel_stage_contract(self):
        shader = Pointer(70, {"Frequency": 3})
        shader.type = "FRHIGraphicsShader *"
        pipeline = Pointer(40, {"ShaderKeys": [10, 0, 0, 0, 0], "VulkanShaders": [0] * 5})
        context = Pointer(10, {"PendingGfxState": {"CurrentPipeline": pipeline}})
        frame = types.SimpleNamespace(read_var=lambda name: {"this": context, "Shader": shader}[name])
        with patch.object(self.capture, "frames_from", return_value=[]):
            self.assertIsNotNone(self.capture.trace_parameters_record(frame))
            pipeline.fields["ShaderKeys"][1] = 20
            self.assertIsNone(self.capture.trace_parameters_record(frame))

    def test_main_thread_timeout_keeps_render_thread_ahead_of_worker_pool(self):
        main = types.SimpleNamespace(num=1, name="CarlaUnreal", ptid=(100, 100, 0))
        worker = types.SimpleNamespace(num=2, name="Backgro-Pool #0", ptid=(100, 101, 0))
        render = types.SimpleNamespace(num=42, name="RenderThread 0", ptid=(100, 142, 0))
        ordered = sorted([worker, render, main], key=lambda thread: self.capture.thread_priority(thread, main, 100))
        self.assertEqual([main, render, worker], ordered)

    def test_event_budget_stop_preserves_accounting(self):
        trace_state = {"events": [], "event_count": 1000000, "errors": [], "dropped_events": 1000000}
        state = types.SimpleNamespace(report={"graphics_trace": trace_state}, write=Mock())
        trace = self.capture.GraphicsTrace(state, "bind")
        with patch.object(self.capture, "trace_bind_record", return_value={"kind": "gfx-bind-applied"}):
            self.assertTrue(trace.stop())
        self.assertEqual(1000000, trace_state["event_count"])
        self.assertEqual([], trace_state["events"])


if __name__ == "__main__":
    unittest.main()
