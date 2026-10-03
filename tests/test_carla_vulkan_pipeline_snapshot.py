import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location("pipeline_snapshot_test", SCRIPTS / "vulkan_pipeline_snapshot.py")
snapshot = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    spec.loader.exec_module(snapshot)
import vulkan_device_snapshot as device_snapshot


def device():
    return {
        "schema_version": 2, "capture_complete": True, "capture_errors": [],
        "capture_point": "vkCreateDevice:entry",
        "instance": {"api_version": 4206592, "flags": 0, "pnext_empty": True, "layer_count": 0,
                     "extension_count": 0, "extensions": []},
        "flags": 0, "layer_count": 0, "device_extension_count": 0, "device_extensions": [],
        "enabled_core_features": {name: False for name in device_snapshot.CORE_FIELDS},
        "pnext_terminated": True, "feature_chain": [],
        "queue_create_infos": [{"queueFamilyIndex": 0, "queueCount": 1, "flags": 0,
                                "pnext_empty": True, "priorities": [1.0]}],
    }


def callbacks():
    return {
        "is_null": False, "address": "0x100", "user_data": "0x0",
        "functions": {name: {"address": hex(index + 1), "symbol": name}
                      for index, name in enumerate(snapshot.ALLOCATOR_FIELDS)},
    }


def fixture(root):
    shader = struct.pack("<5I", 0x07230203, 0x00010600, 0, 1, 0)
    (root / "module-0004.spv").write_bytes(shader)
    (root / "cache-0001.bin").write_bytes(b"")
    (root / "device-create.json").write_text(json.dumps(device()))
    call = {
        "entry": "main_test", "flags": 0, "stage_flags": 0, "required_subgroup_size": 0,
        "base_pipeline_handle": 0, "base_pipeline_index": 0,
        "module_handle": "0x10", "layout_handle": "0x20", "cache_handle": "0x30",
        "module": {"file": "module-0004.spv", "bytes": len(shader),
                   "sha256": hashlib.sha256(shader).hexdigest(), "flags": 0},
        "cache_initial": {"file": "cache-0001.bin", "bytes": 0,
                          "sha256": hashlib.sha256(b"").hexdigest(), "flags": 0},
        "layout": {"flags": 0, "push_constants": [], "sets": [{
            "flags": 0, "bindings": [{"binding": 0, "descriptor_type": 8, "count": 1,
                                     "stage_flags": 32, "immutable_samplers": []}],
        }]},
        "graphics_calls_before": 12, "stack": ["vkCreateComputePipelines", "Dispatch<FRDGMemcpyCS>"],
    }
    return {
        "schema_version": 1, "capture_complete": True, "errors": [],
        "capture_point": "vkCreateComputePipelines:entry",
        "target": "FRDGMemcpyCS", "target_call": call, "calls": [call],
    }


class PipelineSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = fixture(self.root)

    def test_correlated_target_layout_and_initial_cache(self):
        call, shader, cache = snapshot.validate(self.state, self.root)
        self.assertEqual(20, shader.stat().st_size)
        self.assertEqual(0, cache.stat().st_size)
        self.assertIn("binding 0 0 uniform_buffer_dynamic 1 compute", snapshot.layout_text(call))
        self.assertIn("pipeline_flags=0", snapshot.layout_text(call))

    def test_incomplete_or_ungrounded_target_rejected(self):
        for name, value in (("capture_complete", False), ("errors", ["lost handle"]),
                            ("capture_point", "source-line"), ("target", "unobserved"),
                            ("calls", [])):
            state = copy.deepcopy(self.state)
            state[name] = value
            with self.subTest(name=name), self.assertRaises(ValueError):
                snapshot.validate(state, self.root)

    def test_tampered_binary_rejected(self):
        (self.root / "module-0004.spv").write_bytes(b"x" * 20)
        with self.assertRaisesRegex(ValueError, "hash"):
            snapshot.validate(self.state, self.root)

    def test_path_escape_and_symlink_rejected(self):
        call = self.state["target_call"]
        call["module"]["file"] = "../module.spv"
        with self.assertRaisesRegex(ValueError, "unsafe"):
            snapshot.validate(self.state, self.root)
        call["module"]["file"] = "linked.spv"
        (self.root / "linked.spv").symlink_to(self.root / "module-0004.spv")
        with self.assertRaisesRegex(ValueError, "regular"):
            snapshot.validate(self.state, self.root)

    def test_unsupported_layout_features_fail_closed(self):
        for mutation in ("flags", "push", "immutable", "stage", "duplicate", "type", "count"):
            state = copy.deepcopy(self.state)
            layout = state["target_call"]["layout"]
            binding = layout["sets"][0]["bindings"][0]
            if mutation == "flags":
                layout["sets"][0]["flags"] = 1
            elif mutation == "push":
                layout["push_constants"] = [{"offset": 0, "size": 4, "stage_flags": 32}]
            elif mutation == "immutable":
                binding["immutable_samplers"] = [1]
            elif mutation == "stage":
                binding["stage_flags"] = 16
            elif mutation == "duplicate":
                layout["sets"][0]["bindings"].append(copy.deepcopy(binding))
            elif mutation == "type":
                binding["descriptor_type"] = 1000
            else:
                binding["count"] = True
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                snapshot.validate(state, self.root)

    def test_invalid_device_and_unobserved_handle_rejected(self):
        (self.root / "device-create.json").write_text('{"schema_version":1}')
        with self.assertRaises(ValueError):
            snapshot.validate(self.state, self.root)
        (self.root / "device-create.json").write_text(json.dumps(device()))
        self.state["target_call"]["cache_handle"] = "0x0"
        with self.assertRaises(ValueError):
            snapshot.validate(self.state, self.root)

    def test_cache_hash_and_flag_validation(self):
        for key, value in (("bytes", 1), ("flags", 2), ("flags", True)):
            state = copy.deepcopy(self.state)
            state["target_call"]["cache_initial"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                snapshot.validate(state, self.root)

    def test_cli_materializes_only_validated_inputs(self):
        (self.root / "pipeline-capture.json").write_text(json.dumps(self.state))
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "vulkan_pipeline_snapshot.py"), "--run-dir", str(self.root)],
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual((self.root / "module-0004.spv").read_bytes(),
                         (self.root / "shader.spv").read_bytes())
        self.assertEqual(b"", (self.root / "pipeline-cache-initial.bin").read_bytes())
        self.assertIn("cache_initial_bytes=0", result.stdout)

    def test_observe_does_not_confuse_valid_capture_with_driver_success(self):
        (self.root / "pipeline-capture.json").write_text(json.dumps(self.state))
        command = [sys.executable, str(SCRIPTS / "vulkan_pipeline_snapshot.py"),
                   "--run-dir", str(self.root), "--require-driver-return"]
        pending = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(3, pending.returncode)
        self.assertIn("input capture remains valid", pending.stderr)
        call = self.state["target_call"]
        call["driver_return_observed"], call["result"] = True, 0
        (self.root / "pipeline-capture.json").write_text(json.dumps(self.state))
        completed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_make_and_probes_preserve_diagnostic_boundaries(self):
        result = subprocess.run(["make", "-n", "carla-ue-vulkan-pipeline"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("--cap-add SYS_PTRACE", result.stdout)
        self.assertIn("FRDGMemcpyCS", result.stdout)
        self.assertIn("probe-ue-vulkan-pipeline.sh", result.stdout)
        script = (SCRIPTS / "probe-ue-vulkan-pipeline.sh").read_text()
        self.assertIn("no runtime/sensor acceptance", script)
        self.assertNotIn("|| true", script)
        self.assertIn("CARLA_UE_PIPELINE_BINARY", script)
        self.assertIn("CARLA_UE_PIPELINE_DEFER_SPIRV_VALIDATION", script)
        self.assertIn("deferred-to-build-container", script)
        self.assertIn("CARLA_UE_PIPELINE_ALLOW_DEVICE_ONLY", script)
        self.assertIn("CAPTURED_DEVICE_ONLY", script)
        capture = (SCRIPTS / "gdb_vulkan_pipeline_capture.py").read_text()
        self.assertIn("VulkanDynamicAPI::", capture)
        self.assertIn("vkCreateShaderModule", capture)
        self.assertNotIn("gdb.execute(", capture)
        self.assertIn("graphics_calls_before", capture)

    def test_invalid_bounds_do_not_launch(self):
        result = subprocess.run(["bash", str(SCRIPTS / "probe-ue-vulkan-pipeline.sh")],
                                env={**os.environ, "CARLA_UE_PIPELINE_TIMEOUT": "invalid"},
                                capture_output=True, text=True)
        self.assertEqual(64, result.returncode)

    def test_invalid_deferred_spirv_validation_does_not_launch(self):
        result = subprocess.run(
            ["bash", str(SCRIPTS / "probe-ue-vulkan-pipeline.sh")],
            env={**os.environ, "CARLA_UE_PIPELINE_DEFER_SPIRV_VALIDATION": "invalid"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)

    def test_invalid_device_only_mode_does_not_launch(self):
        result = subprocess.run(
            ["bash", str(SCRIPTS / "probe-ue-vulkan-pipeline.sh")],
            env={**os.environ, "CARLA_UE_PIPELINE_ALLOW_DEVICE_ONLY": "invalid"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)

    def test_interventions_are_default_off_and_observe_only(self):
        for mode, intervention in (("entry", "cache-null"), ("entry", "allocator-null"),
                                   ("observe", "unknown")):
            result = subprocess.run(
                ["bash", str(SCRIPTS / "probe-ue-vulkan-pipeline.sh")],
                env={**os.environ, "CARLA_UE_PIPELINE_MODE": mode,
                     "CARLA_UE_PIPELINE_INTERVENTION": intervention},
                capture_output=True, text=True,
            )
            self.assertEqual(64, result.returncode, result.stderr)

    def test_allocator_values_and_required_functions_are_complete(self):
        snapshot.validate_allocator(callbacks())
        snapshot.validate_allocator({"is_null": True, "address": "0x0"})
        for mutation in ("address", "missing", "notification"):
            value = callbacks()
            if mutation == "address":
                value["address"] = "0x0"
            elif mutation == "missing":
                value["functions"]["pfnAllocation"]["address"] = "0x0"
            else:
                value["functions"]["pfnInternalFree"]["address"] = "0x0"
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                snapshot.validate_allocator(value)

    def test_intervened_data_cannot_be_used_as_unmodified_replay(self):
        call = self.state["target_call"]
        self.state["mode"], self.state["intervention"] = "observe", "cache-null"
        call.update(allocator=callbacks(), submitted_allocator=callbacks(), intervention="cache-null",
                    submitted_cache_handle="0x0", intervention_applied=True)
        with self.assertRaisesRegex(ValueError, "unmodified"):
            snapshot.validate(self.state, self.root)
        snapshot.validate(self.state, self.root, allow_intervention=True)
        call["submitted_allocator"] = {"is_null": True, "address": "0x0"}
        with self.assertRaisesRegex(ValueError, "unexpected"):
            snapshot.validate(self.state, self.root, allow_intervention=True)

    def test_allocator_null_noop_is_not_counted_as_an_intervention(self):
        call = self.state["target_call"]
        self.state["mode"], self.state["intervention"] = "observe", "allocator-null"
        null = {"is_null": True, "address": "0x0"}
        call.update(allocator=null, submitted_allocator=null, intervention="allocator-null",
                    submitted_cache_handle=call["cache_handle"], intervention_applied=False)
        snapshot.validate(self.state, self.root, allow_intervention=True)
        call["intervention_applied"] = True
        with self.assertRaisesRegex(ValueError, "unexpected"):
            snapshot.validate(self.state, self.root, allow_intervention=True)


class DummyBreakpoint:
    def __init__(self, *args, **kwargs):
        pass


class TypedPipelineCaptureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_gdb = types.SimpleNamespace(Breakpoint=DummyBreakpoint,
                                         FinishBreakpoint=DummyBreakpoint, error=RuntimeError,
                                         newest_frame=lambda: None,
                                         events=types.SimpleNamespace(stop=types.SimpleNamespace(connect=lambda fn: None)),
                                         SignalEvent=type("SignalEvent", (), {}))
        helpers = types.SimpleNamespace(Capture=lambda: None, device_state=lambda _: None,
                                        instance_state=lambda _: None, pointed=lambda value, _: value)
        spec = importlib.util.spec_from_file_location("pipeline_capture_test",
                                                     SCRIPTS / "gdb_vulkan_pipeline_capture.py")
        cls.capture = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as root, \
             patch.dict(sys.modules, {"gdb": fake_gdb, "gdb_vulkan_device_capture": helpers}), \
             patch.dict(os.environ, {"CARLA_UE_PIPELINE_CAPTURE_DIR": root, "CARLA_UE_PIPELINE_TIMEOUT": "300"}):
            spec.loader.exec_module(cls.capture)
            cls.capture.state.timer.cancel()

    def test_typed_binding_and_layout_correlation(self):
        info = {"pNext": 0, "flags": 0, "bindingCount": 1, "pBindings": [{
            "binding": 3, "descriptorCount": 2, "descriptorType": 5,
            "stageFlags": 32, "pImmutableSamplers": 0,
        }]}
        descriptor = self.capture.descriptor_layout(info)
        layout = self.capture.pipeline_layout(
            {"pNext": 0, "flags": 0, "setLayoutCount": 1, "pSetLayouts": [99],
             "pushConstantRangeCount": 0}, {99: descriptor},
        )
        self.assertEqual(3, layout["sets"][0]["bindings"][0]["binding"])
        self.assertEqual(2, layout["sets"][0]["bindings"][0]["count"])

    def test_missing_handle_and_unknown_pnext_rejected(self):
        with self.assertRaisesRegex(ValueError, "unobserved"):
            self.capture.pipeline_layout(
                {"pNext": 0, "flags": 0, "setLayoutCount": 1, "pSetLayouts": [99]}, {},
            )
        with self.assertRaisesRegex(ValueError, "pNext"):
            self.capture.descriptor_layout({"pNext": 123})

    def test_vkresult_uses_signed_32bit_aarch64_return(self):
        with patch.object(self.capture, "register", return_value=0xfffffffe):
            self.assertEqual(-2, self.capture.vk_result())
        with patch.object(self.capture, "register", return_value=0):
            self.assertEqual(0, self.capture.vk_result())

    def test_observe_stops_after_actual_return_and_cancels_timer(self):
        timer = types.SimpleNamespace(cancel=unittest.mock.Mock())
        state = types.SimpleNamespace(timer=timer, write=unittest.mock.Mock())
        record = {"created_at_monotonic": 10.0, "driver_return_observed": False}
        returned = self.capture.ComputeReturned(state, record, stop_after=True)
        with patch.object(self.capture, "register", return_value=0), \
             patch.object(self.capture.time, "monotonic", return_value=12.0):
            self.assertTrue(returned.stop())
        self.assertTrue(record["driver_return_observed"])
        self.assertEqual(0, record["result"])
        self.assertEqual(2.0, record["instrumented_call_seconds"])
        timer.cancel.assert_called_once()

    def test_single_argument_interventions_preserve_other_inputs(self):
        for intervention, assignment in (("allocator-null", "$x4 = 0"), ("cache-null", "$x1 = 0")):
            record = {"cache_handle": "0x123", "allocator": callbacks()}
            setter = unittest.mock.Mock()
            with patch.object(self.capture.gdb, "parse_and_eval", setter, create=True), \
                 patch.object(self.capture, "register", return_value=0):
                self.capture.target_intervention(record, intervention)
            setter.assert_called_once_with(assignment)
            self.assertTrue(record["intervention_applied"])
            if intervention == "allocator-null":
                self.assertEqual("0x123", record["submitted_cache_handle"])
            else:
                self.assertEqual(record["allocator"], record["submitted_allocator"])

    def test_noop_does_not_write_any_register(self):
        record = {"cache_handle": "0x123", "allocator": {"is_null": True, "address": "0x0"}}
        setter = unittest.mock.Mock()
        with patch.object(self.capture.gdb, "parse_and_eval", setter, create=True):
            self.capture.target_intervention(record, "allocator-null")
        setter.assert_not_called()
        self.assertFalse(record["intervention_applied"])

    def test_failed_register_write_is_not_reported_as_applied(self):
        record = {"cache_handle": "0x123", "allocator": callbacks()}
        with patch.object(self.capture.gdb, "parse_and_eval", create=True), \
             patch.object(self.capture, "register", return_value=99), \
             self.assertRaisesRegex(ValueError, "did not apply"):
            self.capture.target_intervention(record, "cache-null")
        self.assertFalse(record["intervention_applied"])

    def test_crash_selects_only_pending_compute_on_faulting_thread(self):
        call = {"thread_num": 9, "driver_return_observed": False,
                "cache_handle": "0x123", "allocator": {"is_null": True, "address": "0x0"}}
        state = types.SimpleNamespace(mode="crash", calls=[
            {"thread_num": 8, "driver_return_observed": False},
            {"thread_num": 9, "driver_return_observed": True},
            call,
        ], target_call=None, target="original", write=unittest.mock.Mock())
        event = self.capture.gdb.SignalEvent()
        event.stop_signal = "SIGSEGV"
        with patch.object(self.capture, "state", state), \
             patch.object(self.capture.gdb, "selected_thread",
                          return_value=types.SimpleNamespace(num=9), create=True):
            self.capture.signal_stop(event)
        self.assertIs(call, state.target_call)
        self.assertEqual("CreateComputePipelineFromShader", state.target)
        self.assertEqual("SIGSEGV", state.failure_signal)

    def test_noncompute_crash_does_not_invent_target(self):
        state = types.SimpleNamespace(mode="crash", calls=[
            {"thread_num": 9, "driver_return_observed": True},
        ], target_call=None, target="original", write=unittest.mock.Mock())
        event = self.capture.gdb.SignalEvent()
        event.stop_signal = "SIGSEGV"
        with patch.object(self.capture, "state", state), \
             patch.object(self.capture.gdb, "selected_thread",
                          return_value=types.SimpleNamespace(num=9), create=True):
            self.capture.signal_stop(event)
        self.assertIsNone(state.target_call)
        self.assertEqual("original", state.target)


class MatchedReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("matched_pipeline_test",
                                                     SCRIPTS / "replay_captured_vulkan_pipeline.py")
        cls.replay = importlib.util.module_from_spec(spec)
        with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
            spec.loader.exec_module(cls.replay)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture"
        self.compiled = self.root / "compiled"
        self.artifacts = self.root / "artifacts"
        for path in (self.capture, self.compiled, self.artifacts):
            path.mkdir()
        self.state = fixture(self.capture)
        (self.capture / "pipeline-capture.json").write_text(json.dumps(self.state))
        for source, target in (("module-0004.spv", "shader.spv"),
                               ("cache-0001.bin", "pipeline-cache-initial.bin"),
                               ("device-create.json", "device-create.json")):
            (self.compiled / target).write_bytes((self.capture / source).read_bytes())
        (self.compiled / "ue-layout.txt").write_text(snapshot.layout_text(self.state["target_call"]))
        (self.compiled / "vulkan-compute-replay").write_bytes(b"fixture program, not native evidence")

    def invoke(self):
        return self.replay.run(self.capture, self.compiled, self.artifacts, "lavapipe", 1)

    def test_compiled_inputs_must_match_target(self):
        (self.compiled / "shader.spv").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "compiled shader"):
            self.invoke()
        self.assertEqual([], list(self.artifacts.iterdir()))

    def test_failed_program_retains_failure_and_inputs(self):
        with patch.object(self.replay.subprocess, "run",
                          return_value=types.SimpleNamespace(returncode=1)):
            self.assertEqual(1, self.invoke())
        run = next(self.artifacts.iterdir())
        self.assertEqual("FAIL", json.loads((run / "decision.json").read_text())["status"])
        self.assertTrue((run / "inputs.sha256").is_file())
        self.assertEqual(0o755, run.stat().st_mode & 0o777)

    def test_timeout_remains_failure(self):
        with patch.object(self.replay.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("fixture", 1)):
            self.assertEqual(1, self.invoke())
        run = next(self.artifacts.iterdir())
        self.assertEqual("FAIL", json.loads((run / "decision.json").read_text())["status"])

    def test_success_requires_exact_report_metadata(self):
        def process(command, stdout, **kwargs):
            stdout.write(json.dumps({
                "status": "PASS", "backend": "lavapipe", "entry": "main_test",
                "device_snapshot_sha256": self.replay.digest(self.capture / "device-create.json"),
                "device_configuration_reused": True, "pipeline_cache_policy": "captured_initial_data",
                "pipeline_cache_initial_bytes": 0, "pipeline_cache_flags": 0,
                "layout_source": "ue-layout-baseline", "pipeline_flags": 0,
                "required_subgroup_size": 0, "ue_exact_replay": False,
            }))
            return types.SimpleNamespace(returncode=0)
        with patch.object(self.replay.subprocess, "run", side_effect=process):
            self.assertEqual(0, self.invoke())
