import os
import json
import shlex
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]


class CarlaRuntimeProbeTest(unittest.TestCase):
    def test_startup_uses_read_only_source_profile(self):
        result = subprocess.run(
            ["make", "-n", "carla-startup-probe"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile g0 run --rm -T carla-dev", result.stdout)
        self.assertIn("probe-arm64-startup.sh", result.stdout)

    def test_editor_check_uses_build_profile(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-check"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        argv = shlex.split(result.stdout.replace("\\\n", ""))
        self.assertIn("CARLA_EDITOR_PROFILE=full", argv)
        self.assertIn("CARLA_USD_NATIVE_ROOT=", argv)
        self.assertIn("CARLA_ARM64_FBX_HEADERS_ONLY=0", argv)
        self.assertIn("carla-build", argv)
        self.assertIn("probe-arm64-editor.sh", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-arm64-editor.sh").read_text()
        self.assertIn("CarlaUnrealEditor LinuxArm64 Development", script)
        self.assertIn("-SkipBuild", script)
        self.assertIn("-NoUBTMakefiles", script)
        self.assertIn("UBT dependency graph only", script)

    def test_fbx_skip_profile_is_explicit_and_recorded(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-check", "EDITOR_PROFILE=fbx-skip"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("CARLA_EDITOR_PROFILE=fbx-skip", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-arm64-editor.sh").read_text()
        self.assertIn("legacy-fbx-headers-only|fbx-skip", script)
        self.assertIn("export CARLA_ARM64_FBX_SKIP=""", script)
        self.assertIn("CARLA_ARM64_FBX_SKIP=%s", script)
        rules = (
            REPO_ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/FBX/FBX.Build.cs"
        ).read_text()
        self.assertIn("CARLA_FBX_UNAVAILABLE=1", rules)

    def test_editor_deps_target_uses_build_profile(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-deps"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_BUILD_JOBS=8", result.stdout)
        self.assertIn("build-arm64-editor-deps.sh", result.stdout)

    def test_editor_deps_preserves_fontconfig_bootstrap_cache(self):
        script = (
            REPO_ROOT / "scripts/carla/build-arm64-editor-deps.sh"
        ).read_text()
        self.assertIn('fontconfig_bootstrap="${build_dir}/fontconfig-bootstrap"', script)
        self.assertIn('fontconfig_build="${build_dir}/fontconfig"', script)
        self.assertIn('rm -rf "${fontconfig_build}"', script)
        self.assertNotIn('rm -rf "${fontconfig_bootstrap}"', script)
        self.assertIn("FontConfig bootstrap cache is missing generated configure/Makefile.in", script)
        self.assertIn('cp -a "${fontconfig_bootstrap}/."', script)
        self.assertIn('"${fontconfig_build}/source/"', script)
        self.assertNotIn("fontconfig-makefile-in", script)
        self.assertNotIn("fontconfig-configure-in", script)
        self.assertIn("fontconfig-generated-headers", script)
        self.assertIn("fcobjshash.gperf", script)
        self.assertIn("libfontconfig.la", script)
        self.assertIn("fontconfig_clean_archive", script)
        self.assertIn("fontconfig-archive-${member}", script)
        self.assertIn("fontconfig-pic-link", script)
        self.assertIn(
            'if [[ ! -f "${fontconfig_bootstrap}/Makefile.in" || ! -x "${fontconfig_bootstrap}/configure" ]]; then',
            script,
        )

    def test_arm64_editor_rules_use_architecture_aware_third_party_paths(self):
        target = (
            REPO_ROOT / "third_party/carla/Unreal/CarlaUnreal/Source/CarlaUnrealEditor.Target.cs"
        ).read_text()
        self.assertIn("Target.Platform == UnrealTargetPlatform.LinuxArm64", target)
        self.assertIn("(Target.Platform == UnrealTargetPlatform.Linux && Architecture == UnrealArch.Arm64)", target)
        self.assertIn("DisablePlugins.AddRange", target)
        for plugin in (
            "BinkMedia", "ChangelistReview", "OodleNetwork",
            "PerforceSourceControl", "SequencerScripting", "SpeedTreeImporter",
        ):
            self.assertIn(f'"{plugin}"', target)

        boost = (
            REPO_ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/Boost/Boost.Build.cs"
        ).read_text()
        self.assertIn("Target.Architecture == UnrealArch.Arm64", boost)
        self.assertIn("Target.Platform == UnrealTargetPlatform.LinuxArm64", boost)

        metis = (
            REPO_ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/metis/metis.Build.cs"
        ).read_text()
        self.assertIn('"/libmetis/Linux/" + Target.Architecture.LinuxName', metis)
        self.assertIn('Target.IsInPlatformGroup(UnrealPlatformGroup.Linux)', metis)

    def test_server_target_disables_editor_only_animation_plugins(self):
        target = (
            REPO_ROOT / "third_party/carla/Unreal/CarlaUnreal/Source/CarlaUnrealServer.Target.cs"
        ).read_text()
        self.assertIn("TargetType.Server", target)
        self.assertIn('"AnimationData"', target)
        self.assertIn('"CarlaTools"', target)
        self.assertIn('"ControlRig"', target)

    def test_linux_linker_uses_exact_name_for_unprefixed_so(self):
        toolchain = (
            REPO_ROOT / "third_party/unreal-engine/Engine/Source/Programs/UnrealBuildTool/Platform/Linux/LinuxToolChain.cs"
        ).read_text()
        self.assertIn('String.Format(":{0}", LibraryDependency.Name)', toolchain)
        self.assertNotIn('else if (LibraryDependency.Exists)', toolchain)

        sparse_volume_texture = (
            REPO_ROOT / "third_party/unreal-engine/Engine/Source/Editor/SparseVolumeTexture/SparseVolumeTexture.Build.cs"
        ).read_text()
        self.assertIn(
            'Target.IsInPlatformGroup(UnrealPlatformGroup.Linux)',
            sparse_volume_texture,
        )

    def test_editor_project_disables_sequencer_scripting_plugin(self):
        project = json.loads(
            (REPO_ROOT / "third_party/carla/Unreal/CarlaUnreal/CarlaUnreal.uproject").read_text()
        )
        self.assertTrue(project["DisableEnginePluginsByDefault"])
        plugins = {plugin["Name"]: plugin.get("Enabled") for plugin in project["Plugins"]}
        self.assertIs(False, plugins["SequencerScripting"])
        self.assertTrue(plugins["OnlineSubsystem"])
        animation_data = next(
            plugin for plugin in project["Plugins"] if plugin["Name"] == "AnimationData"
        )
        control_rig = next(
            plugin for plugin in project["Plugins"] if plugin["Name"] == "ControlRig"
        )
        self.assertEqual(["Editor"], animation_data["TargetAllowList"])
        self.assertEqual(["Editor"], control_rig["TargetAllowList"])

    def test_editor_build_defaults_to_fbx_skip_and_explicit_link_mode(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-build"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("CARLA_EDITOR_PROFILE=fbx-skip", result.stdout)
        self.assertIn("CARLA_EDITOR_BUILD=1", result.stdout)
        self.assertIn("CARLA_EDITOR_BUILD_TIMEOUT=14400", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-arm64-editor.sh").read_text()
        self.assertIn('command+=(-SkipBuild -NoUBTMakefiles)', script)
        self.assertIn('  -buildubt', script)
        self.assertIn('export CARLA_DISABLE_ISPC=1', script)
        self.assertIn('command+=(-NoDumpSyms)', script)
        self.assertIn("no Cook or runtime validation implied", script)

    def test_editor_startup_probe_is_a_clean_native_editor_gate(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-startup"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_EDITOR_STARTUP_TIMEOUT=60", result.stdout)
        self.assertIn("carla-build", result.stdout)
        self.assertIn("probe-arm64-editor-startup.sh", result.stdout)
        script = (
            REPO_ROOT / "scripts/carla/probe-arm64-editor-startup.sh"
        ).read_text()
        self.assertIn("Run this script in the native ARM64 carla-build container", script)
        for argument in (
            "-nullrhi",
            "-nosound",
            "-unattended",
            "-NoSplash",
            "-notraceserver",
            "-ddc=NoZenLocalFallback",
            "-NoAssetRegistryCacheWrite",
            "-ExecCmds=QUIT_EDITOR",
        ):
            self.assertIn(argument, script)
        self.assertIn(
            '-ini:EditorPerProjectUserSettings:[/Script/UnrealEd.EditorLoadingSavingSettings]:LoadLevelAtStartup=None',
            script,
        )
        self.assertIn("timeout --signal=INT --kill-after=10", script)
        self.assertIn('[[ "${exit_code}" -ne 0 ]]', script)
        self.assertIn("Required Editor startup marker is missing", script)
        self.assertIn("Forbidden Editor startup marker is present", script)
        self.assertIn("Engine is initialized. Leaving FEngineLoop::Init()", script)
        self.assertIn("Cmd: QUIT_EDITOR", script)
        self.assertIn("UUnrealEdEngine::CloseEditor()", script)
        self.assertIn('"MAP LOAD"', script)
        self.assertIn('"LoadDefaultMapAtStartup"', script)
        self.assertIn('"SIGSEGV"', script)
        self.assertIn('"Fatal error!"', script)

    def test_editor_cook_probe_is_a_minimal_single_package_gate(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-cook"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_EDITOR_COOK_TIMEOUT=600", result.stdout)
        self.assertIn('CARLA_EDITOR_COOK_PACKAGE="/Game/Carla/RT_LuminanceCapture"', result.stdout)
        self.assertIn("CARLA_EDITOR_COOK_PACKAGE_EXTENSION=uasset", result.stdout)
        self.assertIn("carla-build", result.stdout)
        self.assertIn("probe-arm64-editor-cook.sh", result.stdout)
        script = (
            REPO_ROOT / "scripts/carla/probe-arm64-editor-cook.sh"
        ).read_text()
        self.assertIn("Run this script in the native ARM64 carla-build container", script)
        for argument in (
            "-run=Cook",
            "-targetplatform=\"${target_platform}\"",
            "-cooksinglepackagenorefs",
            "-map=\"${package_name}\"",
            "-package_extension=\"${package_extension}\"",
            "-outputdir=\"${output_dir}\"",
            "-SkipZenStore",
            "-nullrhi",
            "-ddc=NoZenLocalFallback",
            "-NoAssetRegistryCacheWrite",
        ):
            self.assertIn(argument, script)
        self.assertIn("timeout --signal=INT --kill-after=10", script)
        self.assertIn('[[ "${exit_code}" -ne 0 ]]', script)
        self.assertIn("Required Editor cook marker is missing", script)
        self.assertIn("Forbidden Editor cook marker is present", script)
        self.assertIn("Packages Cooked: 1,", script)
        self.assertIn("Expected exactly one cooked package", script)

    def test_cooked_server_probe_is_a_stability_gate(self):
        result = subprocess.run(
            ["make", "-n", "carla-cooked-server"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_COOKED_SERVER_TIMEOUT=30", result.stdout)
        self.assertIn('CARLA_COOKED_SERVER_ROOT="/artifacts/carla/cooked-server/CarlaUnreal"', result.stdout)
        self.assertIn('CARLA_COOKED_SERVER_MAP="/Game/Carla/Maps/OpenDriveMap"', result.stdout)
        self.assertIn("CARLA_COOKED_SERVER_PORT=7777", result.stdout)
        self.assertIn("CARLA_COOKED_SERVER_REQUIRE_CONTENT=1", result.stdout)
        self.assertIn("carla-build", result.stdout)
        self.assertIn("probe-arm64-cooked-server.sh", result.stdout)
        script = (
            REPO_ROOT / "scripts/carla/probe-arm64-cooked-server.sh"
        ).read_text()
        self.assertIn("Run this script in the native ARM64 carla-build container", script)
        for argument in (
            "-server",
            "-port=\"${server_port}\"",
            "-nullrhi",
            "-nosound",
            "-unattended",
            "-NoSplash",
            "-notraceserver",
            "-stdout",
            "-FullStdOutLogOutput",
            "-log",
        ):
            self.assertIn(argument, script)
        self.assertIn("timeout --signal=INT --kill-after=10", script)
        self.assertIn('[[ "${exit_code}" -ne 124 ]]', script)
        self.assertIn("did not remain stable until the probe timeout", script)
        self.assertIn("Required cooked server marker is missing", script)
        self.assertIn("Forbidden cooked server marker is present", script)
        self.assertIn("LogAssetRegistry: Premade AssetRegistry loaded", script)
        self.assertIn("LogLoad: Game class is 'CarlaGameMode_C'", script)
        self.assertIn("IpNetDriver listening on port ${server_port}", script)
        self.assertIn('"Assertion failed:"', script)
        self.assertIn('"Ensure condition failed:"', script)
        self.assertIn('"Invalid InputComponent class"', script)
        self.assertIn('"SIGSEGV"', script)
        self.assertIn('"Fatal error!"', script)
        self.assertIn('"ICU data directory was not discovered"', script)
        self.assertIn('"No OpenDrive file found for map"', script)
        self.assertIn('"failed to load because module"', script)
        self.assertIn("missing-soft-references.txt", script)
        self.assertIn("CARLA_COOKED_SERVER_REQUIRE_CONTENT", script)

    def test_full_cook_probe_uses_registered_arm64_target_platform(self):
        result = subprocess.run(
            ["make", "-n", "carla-full-cook"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_FULL_COOK_TIMEOUT=1800", result.stdout)
        self.assertIn("CARLA_FULL_COOK_TARGET_PLATFORM=LinuxArm64Server", result.stdout)
        self.assertIn("CARLA_FULL_COOK_RENDERING=0", result.stdout)
        self.assertIn('CARLA_VK_ICD_FILENAMES=""', result.stdout)
        self.assertIn("probe-arm64-full-cook.sh", result.stdout)
        script = (
            REPO_ROOT / "scripts/carla/probe-arm64-full-cook.sh"
        ).read_text()
        for argument in (
            "-run=Cook",
            "-targetplatform=\"${target_platform}\"",
            '-COOKDIR="${carla_dir}/Unreal/CarlaUnreal/Content/Carla"',
            "-outputdir=\"${output_dir}\"",
            "-ddpi:LinuxArm64:bIsEnabled=true",
            "-SkipZenStore",
            "-ddc=NoZenLocalFallback",
            "-NoAssetRegistryCacheWrite",
            "-NoP4",
        ):
            self.assertIn(argument, script)
        self.assertIn("Loaded TargetPlatform '${target_platform}'", script)
        self.assertIn("Building Assets For ${target_platform}", script)
        self.assertIn("Cook by the book total time in tick", script)
        self.assertIn("Invalid target platform specified", script)
        self.assertIn("No target platforms found", script)
        self.assertIn("LogInit: Display: Failure -", script)
        self.assertIn("CARLA_FULL_COOK_RENDERING", script)
        self.assertIn("CARLA_VK_ICD_FILENAMES is required when CARLA_FULL_COOK_RENDERING=1", script)
        self.assertIn("-AllowCommandletRendering", script)
        self.assertIn("-ini:Engine:[SystemSettings]:r.VirtualTextures=0", script)
        self.assertIn("output-files.txt", script)
        self.assertIn("Expected cooked CARLA package is missing", script)
        for package_name in ("SM_PlasticBag", "SM_StreetAD01", "SM_calibration"):
            self.assertIn(package_name, script)

        project = (REPO_ROOT / "third_party/carla/Unreal/CarlaUnreal/Config/DefaultGame.ini").read_text()
        self.assertIn('+DirectoriesToNeverCook=(Path="/CarlaTools")', project)
        self.assertIn('+DirectoriesToNeverCook=(Path="/Game/Carla/HoudiniEngine")', project)

    def test_rendering_cook_image_contains_lavapipe_driver(self):
        dockerfile = (REPO_ROOT / "images/carla-arm64/Dockerfile").read_text()
        self.assertIn("mesa-vulkan-drivers", dockerfile)

    def test_cooked_server_stage_carries_runtime_content(self):
        result = subprocess.run(
            ["make", "-n", "carla-stage-cooked-server",
             "FULL_COOK_OUTPUT=/artifacts/carla/full-cook/cooked"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn('CARLA_FULL_COOK_OUTPUT="/artifacts/carla/full-cook/cooked"', result.stdout)
        self.assertIn("stage-arm64-cooked-server.sh", result.stdout)
        script = (
            REPO_ROOT / "scripts/carla/stage-arm64-cooked-server.sh"
        ).read_text()
        for required in (
            "CarlaUnrealServer",
            "CarlaUnreal.uproject",
            "AssetRegistry.bin",
            "Content/Carla/Config/.",
            "Internationalization",
            "TessellationTable.bin",
            "-name '*.xodr'",
            "Maps/Nav",
            "-name '*.bin'",
            "Plugins/Carla/Shaders",
            "OnlineBase",
            "OnlineSubsystemUtils",
            "ProceduralMeshComponent",
            "EnhancedInput",
            "rm -rf \"${stage_root}/Plugins/CarlaTools\"",
        ):
            self.assertIn(required, script)

    def test_cooked_client_stage_keeps_full_render_content(self):
        result = subprocess.run(
            ["make", "-n", "carla-stage-cooked-client",
             "FULL_COOK_OUTPUT=/artifacts/carla/client-full-cook/cooked"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("stage-arm64-cooked-client.sh", result.stdout)
        script = (
            REPO_ROOT / "scripts/carla/stage-arm64-cooked-client.sh"
        ).read_text()
        for required in (
            "CarlaUnreal",
            "CarlaUnreal.uproject",
            "AssetRegistry.bin",
            "OverrideGlobalShaderCache-VULKAN_SM6.bin",
            "Internationalization",
            "Engine/Content/Slate",
            "TessellationTable.bin",
            "Plugins/Carla/Content",
            "Maps/Nav",
            "-name '*.bin'",
            "Plugins/Carla/Shaders",
            "cp -a \"${cook_root}/CarlaUnreal/Plugins/Carla/Content\"",
            "rm -rf \"${stage_root}/Plugins/Carla/Content\"",
            "rm -rf \"${stage_root}/Plugins/Carla/Shaders\"",
            "OnlineSubsystemUtils",
            "ProceduralMeshComponent",
            "ChaosVehiclesPlugin",
        ):
            self.assertIn(required, script)

    def test_lavapipe_sensor_gate_is_reproducible(self):
        result = subprocess.run(
            ["make", "-n", "carla-lavapipe-sensors"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("run-carla-lavapipe-sensors.sh", result.stdout)
        script = (
            REPO_ROOT / "scripts/carla/run-carla-lavapipe-sensors.sh"
        ).read_text()
        self.assertIn("Run on the DGX Spark host", script)
        self.assertIn("CARLA_COOKED_CLIENT_ROOT:-/artifacts/carla/cooked-client-full/CarlaUnreal", script)
        self.assertIn("CARLA_COOKED_CLIENT_MAP:-/Game/Carla/Maps/Town10HD_Opt", script)
        self.assertIn("CARLA_LAVAPIPE_IMAGE:-ubuntu:24.04", script)
        self.assertIn('docker inspect "${build_container}" --format', script)
        self.assertIn('-v "${shared_artifact_host}:/artifacts/carla:rw"', script)
        self.assertNotIn("--volumes-from", script)
        self.assertIn('vk_icd="/usr/share/vulkan/icd.d/lvp_icd.json"', script)
        self.assertIn('-e VK_ICD_FILENAMES="${vk_icd}"', script)
        self.assertIn("-AllowCPUDevices", script)
        self.assertIn("-SkipVulkanProfileCheck", script)
        self.assertIn("-no-rendering", script)
        self.assertIn("-quality-level=Low", script)
        for cvar in (
            "r.Nanite.ProjectEnabled=0",
            "r.Nanite.ForceEnableMeshes=0",
            "r.Shadow.Virtual.Enable=0",
            "r.VolumetricCloud=0",
            "r.RayTracing=0",
            "r.Lumen.TraceMeshSDFs=0",
            "r.AllowOcclusionQueries=0",
        ):
            self.assertIn(cvar, script)
        self.assertNotIn("r.VirtualShadowMaps=0", script)
        self.assertIn("DeviceName: llvmpipe", script)
        self.assertIn("libvulkan_lvp.so", script)
        self.assertIn('mode="${CARLA_RUNTIME_MODE:-sensors}"', script)
        self.assertIn('-e CARLA_RUNTIME_MODE="${mode}"', script)
        self.assertIn('mode="${CARLA_RUNTIME_MODE:-sensors}"', script)
        self.assertIn('[[ "${mode}" == rpc || "${mode}" == sensors || "${mode}" == actors ]]', script)
        # `if ! cmd; then code=$?` reverses cmd's status before $?, so the old
        # form reported gate failures as success.
        self.assertIn('code=0\ndocker exec', script)
        self.assertIn('bash /opt/my-ad/scripts/carla/probe-arm64-runtime.sh || code=$?', script)
        self.assertIn("CARLA_ALLOW_WORLD_MUTATION=1", script)
        self.assertIn("runtime-provenance.json", script)
        self.assertIn("carla-0.10.0-cp310-cp310-linux_aarch64.whl", script)
        self.assertIn("Plugins/Carla/Content/PostProcessingMaterials", script)
        self.assertIn('docker exec "${build_container}" grep -aFq', script)
        self.assertIn('docker exec "${build_container}" tail -n 80', script)
        self.assertIn('docker exec -i "${build_container}" sh -c \'cat > "$0"\'', script)
        self.assertIn('docker rm -f "${container_name}"', script)

    def test_lavapipe_soak_target_uses_reproducible_defaults(self):
        result = subprocess.run(
            ["make", "-n", "carla-lavapipe-soak"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("run-carla-lavapipe-sensors.sh", result.stdout)
        self.assertIn("CARLA_RUNTIME_MODE=sensors", result.stdout)
        self.assertIn("CARLA_RUNTIME_TICKS=6000", result.stdout)
        self.assertIn("CARLA_RUNTIME_TOTAL_TIMEOUT=10800", result.stdout)

        override = subprocess.run(
            ["make", "-n", "carla-lavapipe-soak",
             "CARLA_RUNTIME_MODE=actors", "CARLA_RUNTIME_TICKS=240",
             "CARLA_RUNTIME_TOTAL_TIMEOUT=300"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("CARLA_RUNTIME_MODE=actors", override.stdout)
        self.assertIn("CARLA_RUNTIME_TICKS=240", override.stdout)
        self.assertIn("CARLA_RUNTIME_TOTAL_TIMEOUT=300", override.stdout)

    def test_scw_worker_is_staged_under_linux_arm64_binary_directory(self):
        script = (
            REPO_ROOT / "scripts/carla/build-arm64-scw.sh"
        ).read_text()
        self.assertIn('worker_arm64="${ue_dir}/Engine/Binaries/LinuxArm64/ShaderCompileWorker"', script)
        self.assertIn('libShaderCompileWorker-*', script)
        self.assertIn('cp -a "${worker}.${worker_metadata}" "${worker_arm64}.${worker_metadata}"', script)

    def test_no_usd_profile_is_explicit_and_recorded(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-check", "EDITOR_PROFILE=no-usd"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        )
        self.assertIn("CARLA_EDITOR_PROFILE=no-usd", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-arm64-editor.sh").read_text()
        self.assertIn("export CARLA_UE_DISABLE_USD=0", script)
        self.assertIn("profile.env", script)
        self.assertIn("profile.txt", script)
        self.assertIn("--reverse --check", script)
        self.assertIn("apply --check", script)
        self.assertLess(script.index("apply --check"), script.index("diff --binary HEAD"))

    def test_editor_rejects_unknown_profile_before_mutation(self):
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/carla/probe-arm64-editor.sh")],
            env={**os.environ, "CARLA_EDITOR_PROFILE": "unknown"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)
        self.assertIn("must be full, no-usd, legacy-fbx-headers-only or fbx-skip", result.stderr)

    def test_legacy_fbx_headers_only_is_explicit_arm64_diagnostic(self):
        result = subprocess.run(
            ["make", "-n", "carla-editor-check", "EDITOR_PROFILE=legacy-fbx-headers-only"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("CARLA_EDITOR_PROFILE=legacy-fbx-headers-only", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-arm64-editor.sh").read_text()
        self.assertIn("export CARLA_ARM64_FBX_HEADERS_ONLY=1", script)
        self.assertIn("CARLA_ARM64_FBX_HEADERS_ONLY=%s", script)
        rules = (
            REPO_ROOT / "third_party/unreal-engine/Engine/Source/ThirdParty/FBX/FBX.Build.cs"
        ).read_text()
        self.assertIn("CARLA_ARM64_FBX_HEADERS_ONLY must be 0 or 1", rules)
        self.assertIn("CARLA_ARM64_FBX_HEADERS_ONLY is a Linux ARM64 diagnostic profile only", rules)
        self.assertIn("Target.Platform != UnrealTargetPlatform.Linux", rules)
        self.assertIn("Target.Architecture != UnrealArch.Arm64", rules)
        self.assertIn("Target.Type != TargetType.Editor", rules)
        self.assertIn("return;", rules)
        unix = rules[rules.index("else if (Target.IsInPlatformGroup(UnrealPlatformGroup.Unix))"):]
        self.assertLess(unix.index("CARLA_ARM64_FBX_HEADERS_ONLY"), unix.index("PublicAdditionalLibraries.Add(FBxDllPath"))

    def test_headers_only_cannot_leak_into_other_editor_profiles(self):
        for profile in ("full", "no-usd"):
            result = subprocess.run(
                ["bash", str(REPO_ROOT / "scripts/carla/probe-arm64-editor.sh")],
                env={**os.environ, "CARLA_EDITOR_PROFILE": profile,
                     "CARLA_ARM64_FBX_HEADERS_ONLY": "1"},
                capture_output=True, text=True,
            )
            self.assertEqual(64, result.returncode)
            self.assertIn("requires the legacy-fbx-headers-only profile", result.stderr)
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/carla/probe-arm64-editor.sh")],
            env={**os.environ, "CARLA_ARM64_FBX_HEADERS_ONLY": "yes"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, result.returncode)
        self.assertIn("must be 0 or 1", result.stderr)

    def test_usd_patch_is_opt_in_idempotent_and_conflict_safe(self):
        patch = REPO_ROOT / "scripts/carla/patches/usd-arm64-opt-out.patch"
        text = patch.read_text()
        for condition in (
            "Target.Type == TargetType.Editor",
            "Target.IsInPlatformGroup(UnrealPlatformGroup.Unix)",
            "Target.Architecture == UnrealArch.Arm64",
            "GetEnvironmentVariable(\"CARLA_UE_DISABLE_USD\") == \"1\"",
        ):
            self.assertIn(condition, text)
        source = (
            "\t\tbool EnableUsdSdk(ReadOnlyTargetRules Target)\n\t\t{\n"
            "\t\t\t// USD SDK has been built against Python 3 and won't launch if the editor is using Python 2\n"
            "\n\t\t\tbool bEnableUsdSdk = (\n"
            "\t\t\t\tTarget.WindowsPlatform.Compiler != WindowsCompiler.Clang &&\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "Engine/Plugins/Runtime/USDCore/Source/UnrealUSDWrapper/UnrealUSDWrapper.Build.cs"
            target.parent.mkdir(parents=True)
            target.write_text(source)
            for arguments in (("--check",), (), ("--reverse", "--check")):
                subprocess.run(
                    ["git", "apply", *arguments, str(patch)], cwd=directory,
                    capture_output=True, text=True, check=True,
                )
            self.assertIn("USD SDK functionality is disabled", target.read_text())
            modified = source.replace("bool EnableUsdSdk", "bool CustomUsdPolicy")
            target.write_text(modified)
            result = subprocess.run(
                ["git", "apply", "--check", str(patch)], cwd=directory,
                capture_output=True, text=True,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertEqual(modified, target.read_text())

    def test_startup_is_diagnostic_not_a_rendering_gate(self):
        script = (REPO_ROOT / "scripts/carla/probe-arm64-startup.sh").read_text()
        self.assertIn("cp --reflink=auto", script)
        self.assertIn("${layout}/Engine/Saved", script)
        self.assertIn("-nullrhi", script)
        self.assertIn("-notraceserver", script)
        self.assertIn("-ExecCmds=carla.AllowEditorContentInServerBuilds 1,quit", script)
        self.assertIn("timeout --kill-after=10", script)
        self.assertIn("ulimit -c 0", script)
        self.assertIn("not a cooked package or RPC/Vulkan/sensor validation", script)
        self.assertNotIn("status=PASS", script)
        self.assertIn("sha256sum", script)
        self.assertIn("diff --binary HEAD", script)
        self.assertIn("exit \"${code}\"", script)

    def test_probes_reject_invalid_timeouts_before_running(self):
        for script, variable in (
            ("probe-arm64-startup.sh", "CARLA_STARTUP_TIMEOUT"),
            ("probe-arm64-editor.sh", "CARLA_EDITOR_CHECK_TIMEOUT"),
            ("probe-arm64-editor-startup.sh", "CARLA_EDITOR_STARTUP_TIMEOUT"),
            ("probe-arm64-editor-cook.sh", "CARLA_EDITOR_COOK_TIMEOUT"),
            ("probe-arm64-cooked-server.sh", "CARLA_COOKED_SERVER_TIMEOUT"),
        ):
            for value in ("0", "-1", "abc", "1s", "1;exit 0"):
                with self.subTest(script=script, timeout=value):
                    result = subprocess.run(
                        ["bash", str(REPO_ROOT / "scripts/carla" / script)],
                        env={**os.environ, variable: value},
                        capture_output=True, text=True,
                    )
                    self.assertEqual(64, result.returncode)
                    self.assertIn(f"{variable} must be positive", result.stderr)


if __name__ == "__main__":
    unittest.main()
