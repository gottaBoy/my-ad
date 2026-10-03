import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts/carla"
spec = importlib.util.spec_from_file_location("world_readiness_test", SCRIPTS / "wait_carla_world.py")
ready = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    spec.loader.exec_module(ready)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def pause(self, seconds):
        self.now += seconds


class FakeClient:
    def __init__(self, map_name="Carla/Maps/Town10HD_Opt", error=None):
        self.name, self.error = map_name, error
        self.timeout = None

    def set_timeout(self, timeout):
        self.timeout = timeout

    def get_client_version(self):
        return "0.10.0"

    def get_server_version(self):
        return "0.10.0"

    def get_world(self):
        if self.error:
            raise RuntimeError(self.error)
        return types.SimpleNamespace(
            get_map=lambda: types.SimpleNamespace(name=self.name),
            get_snapshot=lambda: types.SimpleNamespace(
                frame=10, timestamp=types.SimpleNamespace(frame=10, elapsed_seconds=0.5, delta_seconds=0.05)),
        )


class Gb10RuntimeTest(unittest.TestCase):
    def invoke(self, client, *, seconds=3, alive=lambda: True):
        clock = Clock()
        api = types.SimpleNamespace(Client=lambda host, port: client)
        result = ready.wait(api=api, host="127.0.0.1", port=20220,
                            map_suffix="Town10HD_Opt", seconds=seconds, server_pid=1,
                            clock=clock, pause=clock.pause, alive=alive)
        return result, clock

    def test_ready_requires_real_map_and_snapshot(self):
        result, _ = self.invoke(FakeClient())
        self.assertEqual("PASS", result["status"])
        self.assertEqual("Carla/Maps/Town10HD_Opt", result["map"])
        self.assertEqual(10, result["snapshot"]["frame"])

    def test_handshake_without_world_is_not_ready(self):
        result, clock = self.invoke(FakeClient(error="std::exception"))
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(3, result["attempts"])
        self.assertEqual(3, clock.now)
        self.assertEqual("std::exception", result["error"])

    def test_wrong_map_is_rejected(self):
        result, _ = self.invoke(FakeClient(map_name="Carla/Maps/Town01_Opt"))
        self.assertEqual("FAIL", result["status"])
        self.assertIn("unexpected world map", result["error"])

    def test_server_exit_ends_wait(self):
        result, clock = self.invoke(FakeClient(), alive=lambda: False)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(0, result["attempts"])
        self.assertEqual(0, clock.now)

    def test_transient_world_failure_can_recover(self):
        clock, attempts = Clock(), []
        def client(host, port):
            attempts.append(port)
            return FakeClient(error="not ready" if len(attempts) == 1 else None)
        result = ready.wait(api=types.SimpleNamespace(Client=client), host="127.0.0.1",
                            port=20220, map_suffix="Town10HD_Opt", seconds=3, server_pid=1,
                            clock=clock, pause=clock.pause, alive=lambda: True)
        self.assertEqual("PASS", result["status"])
        self.assertEqual(2, result["attempts"])

    def test_gpu_entry_is_explicit_and_uses_world_gate(self):
        result = subprocess.run(["make", "-n", "carla-town10-gb10-runtime"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("--profile gpu run --rm -T", result.stdout)
        self.assertIn("CARLA_RUNTIME_MODE=rpc", result.stdout)
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        for contract in ("VK_ICD_FILENAMES", "nvidia_icd.json", "NVIDIA GB10",
                         "wait_carla_world.py", "check_carla_runtime.py", "--allow-world-mutation",
                         "/proc/${pid}/exe", "/proc/${pid}/maps", "server-stop-code.txt",
                         "CARLA_RUNTIME_SHADER_DIAGNOSTICS", "CarlaVulkanShaderDiagnostics=",
                         "CARLA_GB10_SERIALIZE_COMPUTE_PIPELINES",
                         "CarlaVulkanSerializeComputePipelineCreation",
                         "CARLA_RUNTIME_PIPELINE_HISTORY",
                         "CarlaVulkanPipelineHistory=",
                         "CarlaVulkanPipelineHistoryTarget=",
                         "CARLA_GB10_SERIALIZE_GRAPHICS_PIPELINES",
                         "CarlaVulkanSerializeGraphicsPipelineCreation",
                         "CARLA_GB10_SERIALIZE_MIXED_PIPELINES",
                         "CarlaVulkanSerializeMixedPipelineCreation"):
            self.assertIn(contract, script)
        self.assertNotIn("New episode", script)
        self.assertNotIn("-nullrhi", script)
        self.assertIn("graceful server shutdown", script)

    def test_limits_are_rejected_before_docker_or_mutation(self):
        for key, value in (("CARLA_RUNTIME_MODE", "unknown"), ("CARLA_RUNTIME_PORT", "65535"),
                           ("CARLA_CLIENT_STARTUP_TIMEOUT", "301"),
                           ("CARLA_RUNTIME_TOTAL_TIMEOUT", "601"), ("CARLA_RUNTIME_TICKS", "0"),
                           ("CARLA_GB10_RENDER_PROFILE", "unknown"),
                           ("CARLA_RUNTIME_SHADER_DIAGNOSTICS", "yes"),
                           ("CARLA_GB10_SERIALIZE_COMPUTE_PIPELINES", "yes"),
                           ("CARLA_RUNTIME_PIPELINE_HISTORY", "yes"),
                           ("CARLA_RUNTIME_PIPELINE_HISTORY_TARGET", "bad entry"),
                           ("CARLA_RUNTIME_PIPELINE_HISTORY_TARGET", "one,two,three"),
                           ("CARLA_RUNTIME_PIPELINE_HISTORY_TARGET", "one/../two"),
                           ("CARLA_GB10_SERIALIZE_GRAPHICS_PIPELINES", "yes"),
                           ("CARLA_RUNTIME_CACHE_LIFECYCLE", "yes"),
                           ("CARLA_RUNTIME_NULL_PIPELINE_CACHE", "yes"),
                           ("CARLA_RUNTIME_GRAPHICS_CACHE_SNAPSHOT", "yes"),
                           ("CARLA_GB10_SERIALIZE_MIXED_PIPELINES", "yes"),
                           ("CARLA_GB10_SERIALIZE_DRIVER_CALLS", "yes"),
                           ("CARLA_RUNTIME_VULKAN_DEBUG_UTILS", "yes"),
                           ("CARLA_RUNTIME_VULKAN_VALIDATION_STACK_TRACE", "bad;rm -rf"),
                           ("CARLA_RUNTIME_VULKAN_VALIDATION_STACK_TRACE", "x" * 40)):
            result = subprocess.run(["bash", str(SCRIPTS / "probe-town10-gb10-runtime.sh")],
                                    env={**os.environ, key: value}, capture_output=True, text=True)
            self.assertEqual(64, result.returncode, (key, result.stderr))

    def test_history_targets_are_explicit_and_at_most_two(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn("CarlaVulkanPipelineHistoryTarget=${pipeline_history_target}", script)
        self.assertIn("comma-separated shader entry names", script)
        self.assertIn('pipeline_history="${CARLA_RUNTIME_PIPELINE_HISTORY:-0}"', script)
        source = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
                  / "VulkanPipeline.cpp").read_text()
        self.assertIn("ConfiguredTargets.ParseIntoArray(Targets, TEXT(\",\"), true)", source)
        self.assertIn("ClaimedTargets.Contains(ClaimKey)", source)
        self.assertIn("pipeline-history-%s.txt", source)
        self.assertIn('TEXT("pipeline-history.txt")', source)
        self.assertIn('TEXT("pipeline-history-checkpoint.txt")', source)
        self.assertIn("NextSequence < 1000", source)
        self.assertIn('const FString ClaimKey = bCheckpoint ? TEXT("checkpoint") : Target;', source)
        self.assertIn('pipeline_history_target}" == checkpoint', script)

    def test_mixed_pipeline_serialization_uses_one_driver_lock_and_is_default_off(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('serialize_mixed="${CARLA_GB10_SERIALIZE_MIXED_PIPELINES:-0}"', script)
        self.assertIn('if [[ "${serialize_mixed}" == 1 ]]', script)
        source = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
                  / "VulkanPipeline.cpp").read_text()
        gfx = source.split("VkResult CreateGraphicsPipeline(", 1)[1].split("VkResult CreateComputePipeline(", 1)[0]
        compute = source.split("VkResult CreateComputePipeline(", 1)[1].split("struct FStage", 1)[0]
        self.assertIn("FScopeLock Scope(&GraphicsCreateLock)", gfx)
        self.assertIn("FScopeLock Scope(&GraphicsCreateLock)", compute)
        self.assertIn('TEXT("driver-graphics-%llu-%u")', gfx)
        self.assertIn('TEXT(".enter.txt")', gfx)
        self.assertIn('TEXT(".result.txt")', gfx)
        self.assertIn("ModuleHashes.Find(Module)", gfx)
        self.assertIn("FScopeLock HistoryScope(&Lock)", gfx)
        self.assertIn("ModuleFiles.Find(Module)", gfx)
        self.assertIn("GraphicsDriverState(Info)", gfx)
        self.assertLess(gfx.index("GraphicsDriverState(Info)"),
                        gfx.index('TEXT(".enter.txt")'))
        self.assertIn("CarlaVulkanGraphicsCacheSnapshot", gfx)
        self.assertIn("128u * 1024u * 1024u", gfx)
        gfx_enter = gfx.index("FScopeLock Scope(&GraphicsCreateLock)")
        self.assertLess(gfx_enter, gfx.index('TEXT(".enter.txt")'))
        self.assertLess(gfx.index('TEXT(".enter.txt")'),
                        gfx.index("VulkanRHI::vkCreateGraphicsPipelines(", gfx_enter))
        self.assertIn("CarlaPipelineHistory::CreateComputePipeline(", source)
        self.assertIn("CARLA diagnostic: serializing mixed graphics/compute pipeline driver calls",
                      source)
        self.assertIn('TEXT("driver-compute-%llu-%u-%s")', source)
        self.assertIn('TEXT(".enter.txt")', source)
        self.assertIn('TEXT(".result.txt")', source)
        compute_enter = compute.index("FScopeLock Scope(&GraphicsCreateLock)")
        self.assertLess(compute_enter, compute.index('TEXT(".enter.txt")'))
        self.assertLess(compute.index('TEXT(".enter.txt")'),
                        compute.index("VulkanRHI::vkCreateComputePipelines(", compute_enter))

    def test_graphics_cache_capture_requires_history_and_mixed_lock(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn("Graphics cache capture requires mixed pipeline serialization", script)
        self.assertIn("Graphics cache capture requires pipeline history", script)
        self.assertIn("command+=(-CarlaVulkanGraphicsCacheSnapshot)", script)
        self.assertIn("--run-dir \"${run_dir}\" --output \"${run_dir}/driver-entry-analysis.json\"", script)
        self.assertIn("driver-entry-analysis-code.txt", script)
        for overrides in ({"CARLA_RUNTIME_GRAPHICS_CACHE_SNAPSHOT": "1"},
                          {"CARLA_RUNTIME_GRAPHICS_CACHE_SNAPSHOT": "1",
                           "CARLA_GB10_SERIALIZE_MIXED_PIPELINES": "1"}):
            env = {**os.environ, "CARLA_RUNTIME_PIPELINE_HISTORY": "0",
                   "CARLA_GB10_SERIALIZE_MIXED_PIPELINES": "0", **overrides}
            result = subprocess.run(["bash", str(SCRIPTS / "probe-town10-gb10-runtime.sh")],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(64, result.returncode, result.stderr)

    def test_cache_lifecycle_requires_explicit_history_and_mixed_lock(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('cache_lifecycle="${CARLA_RUNTIME_CACHE_LIFECYCLE:-0}"', script)
        self.assertIn("command+=(-CarlaVulkanCacheLifecycle)", script)
        for mixed, history in (("0", "0"), ("1", "0"), ("0", "1")):
            with self.subTest(mixed=mixed, history=history):
                result = subprocess.run(
                    ["bash", str(SCRIPTS / "probe-town10-gb10-runtime.sh")],
                    env={**os.environ, "CARLA_RUNTIME_CACHE_LIFECYCLE": "1",
                         "CARLA_GB10_SERIALIZE_MIXED_PIPELINES": mixed,
                         "CARLA_RUNTIME_PIPELINE_HISTORY": history},
                    capture_output=True, text=True,
                )
                self.assertEqual(64, result.returncode, result.stderr)

    def test_null_pipeline_cache_requires_opt_in_diagnostic_scope(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('null_pipeline_cache="${CARLA_RUNTIME_NULL_PIPELINE_CACHE:-0}"', script)
        self.assertIn("command+=(-CarlaVulkanSubmitNullPipelineCache)", script)
        source = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
                  / "VulkanPipeline.cpp").read_text()
        self.assertIn("SubmittedCache(Cache)", source)
        self.assertIn("Device, DriverCache, 1, &Info, DriverAllocator, Pipeline", source)
        self.assertIn("submitted_cache=0x%llx", source)
        self.assertIn("allocator=0x%llx allocator_user_data=0x%llx", source)
        self.assertIn("const VkAllocationCallbacks* DriverAllocator = VULKAN_CPU_ALLOCATOR", source)
        for mixed, history, lifecycle in (
            ("0", "0", "0"), ("1", "0", "1"), ("1", "1", "0")
        ):
            with self.subTest(mixed=mixed, history=history, lifecycle=lifecycle):
                result = subprocess.run(
                    ["bash", str(SCRIPTS / "probe-town10-gb10-runtime.sh")],
                    env={**os.environ, "CARLA_RUNTIME_NULL_PIPELINE_CACHE": "1",
                         "CARLA_GB10_SERIALIZE_MIXED_PIPELINES": mixed,
                         "CARLA_RUNTIME_PIPELINE_HISTORY": history,
                         "CARLA_RUNTIME_CACHE_LIFECYCLE": lifecycle},
                    capture_output=True, text=True,
                )
                self.assertEqual(64, result.returncode, result.stderr)

    def test_graphics_module_capture_uses_bytes_passed_to_vulkan(self):
        resources = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
                     / "VulkanResources.h").read_text()
        shaders = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
                   / "VulkanShaders.cpp").read_text()
        pipeline = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
                    / "VulkanPipeline.cpp").read_text()
        self.assertIn("CaptureDiagnosticSpirv(Spirv)", shaders)
        self.assertIn("const TArrayView<uint32> Spirv = SpirvCode.GetCodeView()", shaders)
        self.assertIn("ModuleCreateInfo.pCode = Spirv.GetData()", shaders)
        self.assertIn("const TArray<uint32>& GetDiagnosticSpirv() const", resources)
        self.assertIn("Modules[Index]->GetDiagnosticSpirv()", pipeline)
        self.assertIn("module-%llu.spv", pipeline)
        self.assertIn("RecordGraphicsLayoutInput(PSO->Layout, PSO->UsesBindless())", pipeline)
        self.assertIn("color_ref%u=%u,%u", pipeline)
        self.assertIn("push_constant_ranges=%s", pipeline)
        self.assertNotIn("Shaders[Index]->GetSpirvCode()", pipeline.split(
            "void RecordGraphicsShaderInputs(", 1)[1].split("void RecordGraphicsLayoutInput(", 1)[0])

    def test_render_pass_snapshot_uses_actual_driver_create_info_for_both_apis(self):
        base = ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private"
        header = (base / "VulkanRenderpass.h").read_text()
        source = (base / "VulkanRenderpass.cpp").read_text()
        pipeline = (base / "VulkanPipeline.cpp").read_text()
        self.assertIn("CarlaRenderPassSnapshot::Record(Handle, *this)", header)
        self.assertIn("VulkanRHI::vkCreateRenderPass2KHR(", header)
        self.assertIn("VulkanRHI::vkCreateRenderPass(", header)
        self.assertIn("AppendAttachment(Output,", source)
        self.assertIn("AppendSubpass(Output,", source)
        self.assertIn("AppendDependency(Output,", source)
        self.assertIn("capture_complete=%u", source)
        self.assertIn("AppendChain(Output, TEXT(\"root\"), Info.pNext", source)
        self.assertIn("CarlaRenderPassSnapshot::FileFor(Info.renderPass)", pipeline)

    def test_client_abort_reason_is_archived_from_the_ue_log(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('client_log="${CARLA_RUNTIME_CLIENT_LOG:-${client_root}/Saved/Logs/CarlaUnreal.log}"', script)
        self.assertIn('cp -a "${client_log}" "${run_dir}/client-ue.log"', script)
        self.assertIn('client_abort=device-creation', script)
        self.assertIn('client_abort=shader-compiler-sigsegv', script)
        self.assertIn('client_abort=nvidia-driver-sigsegv', script)
        self.assertIn("Cannot create a Vulkan device", script)
        self.assertIn("- Client abort: %s", script)
        self.assertIn('"${validation}" "${debug_utils}" "${client_abort}"', script)

    def test_driver_call_serialization_covers_non_pipeline_entry_points(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('serialize_driver_calls="${CARLA_GB10_SERIALIZE_DRIVER_CALLS:-0}"', script)
        self.assertIn("command+=(-CarlaVulkanSerializeDriverCalls)", script)
        private = (ROOT / "third_party/unreal-engine/Engine/Source/Runtime/VulkanRHI/Private")
        resources = (private / "VulkanResources.h").read_text()
        pipeline = (private / "VulkanPipeline.cpp").read_text()
        shaders = (private / "VulkanShaders.cpp").read_text()
        descriptors = (private / "VulkanDescriptorSets.cpp").read_text()
        rhi = (private / "VulkanRHI.cpp").read_text()
        self.assertIn("namespace CarlaDriverSerialization", resources)
        self.assertIn("struct FDriverCallScope", resources)
        self.assertIn('TEXT("CarlaVulkanSerializeDriverCalls")', pipeline)
        # The flag is opt-in and the lock is one shared critical section.
        self.assertIn("static FCriticalSection DriverCallLock;", pipeline)
        # Pipeline creation joins the same lock instead of only the mixed switch.
        self.assertIn("&& !CarlaDriverSerialization::Enabled())", pipeline)
        self.assertIn("if (!SerializeMixedPipelines() && !CarlaDriverSerialization::Enabled())", pipeline)
        self.assertEqual(2, pipeline.count("CarlaDriverSerialization::FDriverCallScope DriverCallScope;"))
        self.assertEqual(2, shaders.count("CarlaDriverSerialization::FDriverCallScope DriverCallScope;"))
        self.assertEqual(4, descriptors.count("CarlaDriverSerialization::FDriverCallScope DriverCallScope;"))
        self.assertEqual(1, rhi.count("CarlaDriverSerialization::FDriverCallScope DriverCallScope;"))
        pending = (private / "VulkanPendingState.cpp").read_text()
        pso = (private / "VulkanPipelineState.cpp").read_text()
        self.assertEqual(3, pending.count("CarlaDriverSerialization::FDriverCallScope DriverCallScope;"))
        self.assertEqual(2, pso.count("CarlaDriverSerialization::FDriverCallScope DriverCallScope;"))
        # The scope is taken before the driver call it is meant to serialize.
        for body, call in (
            (shaders, "VERIFYVULKANRESULT(VulkanRHI::vkCreateShaderModule("),
            (shaders, "VERIFYVULKANRESULT(VulkanRHI::vkCreatePipelineLayout("),
            (descriptors, "VERIFYVULKANRESULT(VulkanRHI::vkCreateDescriptorSetLayout("),
            (descriptors, "VERIFYVULKANRESULT(VulkanRHI::vkCreatePipelineLayout("),
            (rhi, "VERIFYVULKANRESULT(VulkanRHI::vkCreateDescriptorSetLayout("),
        ):
            self.assertLess(body.index("CarlaDriverSerialization::FDriverCallScope DriverCallScope;"),
                            body.index(call))
        self.assertIn("VulkanRHI::vkMergePipelineCaches(", pipeline)
        self.assertIn("VulkanRHI::vkGetPipelineCacheData(", pipeline)
        # Descriptor allocation and update are covered too.
        self.assertIn("VulkanRHI::vkAllocateDescriptorSets(", pending)
        self.assertIn("VulkanRHI::vkUpdateDescriptorSets(", pending)
        self.assertIn("VulkanRHI::vkUpdateDescriptorSets(", pso)

    def test_serial_profile_is_explicit_not_default(self):
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('render_profile="${CARLA_GB10_RENDER_PROFILE:-default}"', script)
        self.assertIn('if [[ "${render_profile}" == serial-translate ]]', script)
        self.assertIn("r.RHICmd.ParallelTranslate.Enable=0", script)

    def test_gb10_runtime_vulkan_validation_is_opt_in_and_archived(self):
        result = subprocess.run(["make", "-n", "carla-town10-gb10-runtime"], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertIn("CARLA_RUNTIME_VULKAN_VALIDATION=0", result.stdout)
        script = (SCRIPTS / "probe-town10-gb10-runtime.sh").read_text()
        self.assertIn('validation="${CARLA_RUNTIME_VULKAN_VALIDATION:-0}"', script)
        self.assertIn('validation_dir="${CARLA_VALIDATION_LAYER_DIR:-/opt/vulkan-validation-layer}"', script)
        self.assertIn("CARLA_RUNTIME_VULKAN_VALIDATION must be 0 or 1", script)
        self.assertIn("command+=(-vulkanvalidation=1)", script)
        # The layer path is published before the GB10 device probe so the run's
        # vulkaninfo.log records the layer, and the enable flag is only added to
        # the client launch.
        self.assertLess(script.index('export VK_LAYER_PATH='),
                        script.index("vulkaninfo --summary"))
        self.assertLess(script.index("vulkaninfo --summary"),
                        script.index('command+=(-vulkanvalidation=1)'))
        self.assertIn('export VK_LAYER_PATH="${validation_dir}"', script)
        self.assertIn("vulkan-validation.json", script)
        self.assertIn('"enablement": "VK_LAYER_PATH plus -vulkanvalidation=1"', script)
        self.assertIn("Requested Vulkan validation layer is missing", script)
        self.assertIn("fetch-vulkan-validation-layer.sh", script)
        self.assertIn("- Vulkan validation: %s", script)
        result = subprocess.run(
            ["bash", str(SCRIPTS / "probe-town10-gb10-runtime.sh")],
            env={**os.environ, "CARLA_RUNTIME_VULKAN_VALIDATION": "yes"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode, result.stderr)

    def test_validation_layer_fetch_is_pinned_and_self_contained(self):
        script = (SCRIPTS / "fetch-vulkan-validation-layer.sh").read_text()
        self.assertIn("vulkan-validationlayers_${layer_version}_arm64.deb", script)
        self.assertIn("ports.ubuntu.com/ubuntu-ports/pool/universe/v/vulkan-validationlayers", script)
        self.assertIn("deb_sha256='dbc3a59a0191e4b4973b9b1405b9ccc4754d51c1c7ba69f9977d7e94fa5a316f'", script)
        self.assertIn("sha256sum --check --status -", script)
        self.assertIn("VkLayer_khronos_validation.json", script)
        self.assertIn("layer library_path is not relative", script)
        self.assertIn("provenance.json", script)
        self.assertIn("not writable; remove it first", script)
        self.assertTrue(os.access(SCRIPTS / "fetch-vulkan-validation-layer.sh", os.X_OK))
        compose = (ROOT / "compose.carla-arm64.yaml").read_text()
        self.assertIn(":/opt/vulkan-validation-layer:ro", compose)
        self.assertIn("${CARLA_VALIDATION_LAYER_DIR:-./data/cache/vulkan-validation-layer}", compose)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("carla-vulkan-validation-layer:", makefile)

    def test_port_preflight_tolerates_time_wait_sockets(self):
        for name in ("probe-town10-gb10-runtime.sh", "probe-town10-nullrhi-rpc.sh",
                     "probe-ue-vulkan-fault.sh", "probe-ue-vulkan-memory-trace.sh"):
            with self.subTest(probe=name):
                script = (SCRIPTS / name).read_text()
                self.assertIn("listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)", script)


if __name__ == "__main__":
    unittest.main()
