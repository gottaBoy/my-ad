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
            "-cookall",
            "-outputdir=\"${output_dir}\"",
            "-ddpi:LinuxArm64:bIsEnabled=true",
            "-SkipZenStore",
            "-ddc=NoZenLocalFallback",
            "-NoAssetRegistryCacheWrite",
            "-NoP4",
        ):
            self.assertIn(argument, script)
        # A -COOKDIR walk only enumerates *.uasset: it silently drops every *.umap and
        # all plugin content, so the probe must not go back to that scope.
        self.assertNotIn("-COOKDIR=", script)
        self.assertIn("Loaded TargetPlatform '${target_platform}'", script)
        self.assertIn("Building Assets For ${target_platform}", script)
        self.assertIn("Cook by the book total time in tick", script)
        self.assertIn("Invalid target platform specified", script)
        self.assertIn("No target platforms found", script)
        self.assertIn("LogInit: Display: Failure -", script)
        self.assertIn("CARLA_FULL_COOK_RENDERING", script)
        self.assertIn("CARLA_VK_ICD_FILENAMES is required when CARLA_FULL_COOK_RENDERING=1", script)
        self.assertIn("-AllowCommandletRendering", script)
        # Cook-time shader scope comes from the shared ARM64 scope file, never an
        # inline list that the runtime wrapper can drift away from.
        self.assertIn('source "${script_dir}/arm64-renderer-scope.sh"', script)
        self.assertIn("carla_renderer_systemsettings_flags", script)
        self.assertNotIn("-ini:Engine:[SystemSettings]:r.VirtualTextures=0\n  )", script)
        self.assertIn("output-files.txt", script)
        self.assertIn("Expected cooked CARLA package is missing", script)
        for package_name in ("SM_PlasticBag", "SM_StreetAD01", "SM_calibration"):
            self.assertIn(package_name, script)

        # -COOKDIR only enumerates *.uasset, so *.umap files must be requested explicitly
        # and the gate must reject a cook that silently drops them.
        for contract in (
            'map_requests+=("/Game/Carla/Maps/${spec_name}")',
            '-MAP="${cook_map}"',
            'find "${maps_root}/OpenDrive"',
            "+MapsToCook=",
            "cook-maps.txt",
            "Requested map was not cooked",
            "Streaming sublevel was not cooked",
            "Sensor material was not cooked",
            "refusing an asset-only cook",
            "OpenDrive spec has no matching runtime map",
            "No OpenDrive specs found",
            "MapsToCook entry has no matching CARLA map",
            "No CARLA streaming sublevel maps found",
            "No CARLA sensor materials found",
        ):
            self.assertIn(contract, script)
        maps_dir = REPO_ROOT / "third_party/carla/Unreal/CarlaUnreal/Content/Carla/Maps"
        for runtime_map in (
            "Town01_Opt", "Town02_Opt", "Town03_Opt", "Town04_Opt",
            "Town05_Opt", "Town06_Opt", "Town07_Opt", "Town10HD_Opt", "Town_C",
        ):
            self.assertTrue(
                (maps_dir / f"{runtime_map}.umap").is_file(),
                f"runtime map source is missing: {runtime_map}.umap",
            )
            self.assertTrue(
                (maps_dir / "OpenDrive" / f"{runtime_map}.xodr").is_file(),
                f"runtime map OpenDrive spec is missing: {runtime_map}.xodr",
            )
        sublevel_maps = sorted(
            path.relative_to(maps_dir).as_posix()
            for path in (maps_dir / "Sublevels").rglob("*.umap")
        )
        self.assertTrue(sublevel_maps, "CARLA streaming sublevel maps are missing")

        project = (REPO_ROOT / "third_party/carla/Unreal/CarlaUnreal/Config/DefaultGame.ini").read_text()
        self.assertIn('+DirectoriesToNeverCook=(Path="/CarlaTools")', project)
        self.assertIn('+DirectoriesToNeverCook=(Path="/Game/Carla/HoudiniEngine")', project)

    def test_rendering_cook_image_contains_lavapipe_driver(self):
        dockerfile = (REPO_ROOT / "images/carla-arm64/Dockerfile").read_text()
        self.assertIn("mesa-vulkan-drivers", dockerfile)
        self.assertIn("glslang-tools", dockerfile)
        self.assertIn("gdb", dockerfile)

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
            "normalize-runtime-config.py",
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
            "normalize-runtime-config.py",
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

    def test_runtime_config_alias_is_structurally_normalized(self):
        helper = REPO_ROOT / "scripts/carla/normalize-runtime-config.py"
        helper_text = helper.read_text()
        self.assertIn("json.loads", helper_text)
        self.assertIn("json.dumps", helper_text)
        self.assertIn("SM_DebrisContainer.SM_DebrisContainer", helper_text)
        with tempfile.TemporaryDirectory() as directory:
            stage_root = Path(directory)
            asset = stage_root / (
                "Content/Carla/Static/Dynamic/Construction/"
                "SM_DebrisContainer.uasset"
            )
            config_root = stage_root / "Content/Carla/Config"
            asset.parent.mkdir(parents=True)
            config_root.mkdir(parents=True)
            asset.write_bytes(b"cooked")
            old = (
                "/Game/Carla/Static/Dynamic/Construction/"
                "Sm_ConstructionDebrie.Sm_ConstructionDebrie"
            )
            new = (
                "/Game/Carla/Static/Dynamic/Construction/"
                "SM_DebrisContainer.SM_DebrisContainer"
            )
            for name in ("PropParameters.json", "Default.Package.json"):
                (config_root / name).write_text(
                    json.dumps({"mesh": old, "nested": [{"path": old}]})
                )
            result = subprocess.run(
                ["python3", str(helper), "--stage-root", str(stage_root)],
                capture_output=True, text=True, check=True,
            )
            self.assertIn("references=4", result.stdout)
            for name in ("PropParameters.json", "Default.Package.json"):
                content = json.loads((config_root / name).read_text())
                self.assertEqual(new, content["mesh"])
                self.assertEqual(new, content["nested"][0]["path"])
            result = subprocess.run(
                ["python3", str(helper), "--stage-root", str(stage_root)],
                capture_output=True, text=True, check=True,
            )
            self.assertIn("references=0", result.stdout)

    def test_runtime_config_alias_rejects_bad_inputs_without_partial_rewrite(self):
        helper = REPO_ROOT / "scripts/carla/normalize-runtime-config.py"
        with tempfile.TemporaryDirectory() as directory:
            stage_root = Path(directory)
            config_root = stage_root / "Content/Carla/Config"
            config_root.mkdir(parents=True)
            old = (
                "/Game/Carla/Static/Dynamic/Construction/"
                "Sm_ConstructionDebrie.Sm_ConstructionDebrie"
            )
            first = config_root / "PropParameters.json"
            second = config_root / "Default.Package.json"
            first.write_text(json.dumps({"mesh": old, "note": f"prefix:{old}"}))
            second.write_text("{invalid")
            command = ["python3", str(helper), "--stage-root", str(stage_root)]
            missing_asset = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(0, missing_asset.returncode)
            self.assertIn("replacement cooked asset is missing", missing_asset.stderr)

            asset = stage_root / (
                "Content/Carla/Static/Dynamic/Construction/"
                "SM_DebrisContainer.uasset"
            )
            asset.parent.mkdir(parents=True)
            asset.write_bytes(b"cooked")
            bad_json = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(0, bad_json.returncode)
            self.assertEqual(old, json.loads(first.read_text())["mesh"])
            self.assertEqual("{invalid", second.read_text())

            second.write_text(json.dumps({"mesh": old}))
            subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(f"prefix:{old}", json.loads(first.read_text())["note"])

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
        # The default must be the map that actually passes: Town10HD_Opt kills the
        # cooked client right after episode start, so it is not a usable default.
        self.assertIn("CARLA_COOKED_CLIENT_MAP:-/Game/Carla/Maps/Town01_Opt", script)
        self.assertNotIn(
            'client_map="${CARLA_COOKED_CLIENT_MAP:-/Game/Carla/Maps/Town10HD_Opt}"',
            script,
        )
        self.assertIn("CARLA_LAVAPIPE_IMAGE:-ubuntu:24.04", script)
        self.assertIn('apt_timeout="${CARLA_LAVAPIPE_APT_TIMEOUT:-180}"', script)
        self.assertIn('timeout --signal=TERM --kill-after=10 "${apt_timeout}"', script)
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
        # The client launch must reuse the exact shader scope the cook used.
        # r.VirtualTextures and r.RayTracing are ECVF_ReadOnly, so a cooked client that
        # still enables them aborts in FMaterial::GetShaderMap ("Failed to find shader
        # map for default material DefaultDeferredDecalMaterial").
        self.assertIn('source "${script_dir}/arm64-renderer-scope.sh"', script)
        self.assertIn("carla_renderer_systemsettings_flags", script)
        flags = subprocess.run(
            ["bash", "-c", 'source "$1"; carla_renderer_systemsettings_flags',
             "bash", str(REPO_ROOT / "scripts/carla/arm64-renderer-scope.sh")],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        self.assertEqual(6, len(flags))
        self.assertEqual(len(flags), len(set(flags)))
        self.assertIn("-ini:Engine:[SystemSettings]:r.VirtualTextures=0", flags)
        self.assertIn("-ini:Engine:[SystemSettings]:r.RayTracing=0", flags)
        self.assertIn("DeviceName: llvmpipe", script)
        self.assertIn("libvulkan_lvp.so", script)
        self.assertIn('mode="${CARLA_RUNTIME_MODE:-sensors}"', script)
        self.assertIn('render_profile="${CARLA_LAVAPIPE_RENDER_PROFILE:-default}"', script)
        self.assertIn('[[ "${render_profile}" == default || "${render_profile}" == no-pso ]]', script)
        for cvar in (
            "r.PSOPrecaching=0",
            "r.Vulkan.AllowPSOPrecaching=0",
            "r.AsyncPipelineCompile=0",
            "r.Vulkan.RHIThread=0",
            "[ConsoleVariables]:g.TimeoutForBlockOnRenderFence=300000",
        ):
            self.assertIn(cvar, script)
        self.assertIn('-e CARLA_RUNTIME_MODE="${mode}"', script)
        self.assertIn('mode="${CARLA_RUNTIME_MODE:-sensors}"', script)
        self.assertIn('[[ "${mode}" == rpc || "${mode}" == sensors || "${mode}" == actors ]]', script)
        # `if ! cmd; then code=$?` reverses cmd's status before $?, so the old
        # form reported gate failures as success.
        self.assertIn('code=0\ndocker exec', script)
        self.assertIn('bash /opt/my-ad/scripts/carla/probe-arm64-runtime.sh || code=$?', script)
        # ${client_command@Q} without a subscript expands to element 0 only, which made
        # every archived client-command.json record ["timeout"] instead of the launch
        # arguments. The array must reach the JSON writer through argv.
        self.assertNotIn("${client_command@Q}", script)
        self.assertIn('python3 - "${client_command[@]}"', script)
        self.assertIn("CARLA_ALLOW_WORLD_MUTATION=1", script)
        self.assertIn("runtime-provenance.json", script)
        self.assertIn("carla-0.10.0-cp310-cp310-linux_aarch64.whl", script)
        self.assertIn("Plugins/Carla/Content/PostProcessingMaterials", script)
        self.assertIn('docker exec "${build_container}" grep -aFq', script)
        self.assertIn('docker exec "${build_container}" tail -n 80', script)
        self.assertIn('docker exec -i "${build_container}" sh -c \'cat > "$0"\'', script)
        self.assertIn("server-diagnostics.txt", script)
        for diagnostic in (
            "Couldn't find file for package",
            "Found 0 dependent packages",
            "Ensure condition failed",
            "Expected source texture to be in VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL",
            "has no SM assigned to the ISM",
            "RequestExitWithStatus",
        ):
            self.assertIn(diagnostic, script)
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

    def test_town10_nullrhi_rpc_gate_uses_real_world_readiness(self):
        result = subprocess.run(
            ["make", "-n", "carla-town10-nullrhi-rpc"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("CARLA_NULLRHI_PORT=20200", result.stdout)
        self.assertIn("CARLA_NULLRHI_TICKS=20", result.stdout)
        self.assertIn("probe-town10-nullrhi-rpc.sh", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-town10-nullrhi-rpc.sh").read_text()
        for contract in (
            "arm64-renderer-scope.sh",
            "Town10HD_Opt.umap",
            "Town10HD_Opt.xodr",
            "Town10HD_Opt.bin",
            '-carla-rpc-port="${port}"',
            "-nullrhi -no-rendering",
            "client.get_world()",
            'assert world.get_map().name.endswith("Town10HD_Opt")',
            "check_carla_runtime.py",
            "--allow-world-mutation",
            "server-exit-code.txt",
            "Excluded: RGB/LiDAR",
            # The stop path must signal the server, not a wrapper: GNU timeout does not
            # forward a SIGTERM it receives, so signalling it left the server orphaned and
            # no signal ever reached UE. `exec` is what makes server_pid the server.
            'exec "${server_command[@]}"',
            "Shutdown: %s",
            "CARLA_NULLRHI_STOP_GRACE",
        ):
            self.assertIn(contract, script)
        # A shutdown is classified from the status first, because the log is racy: a crash
        # truncates it mid-write. UE's own handler requests exit with 128+signal, so a
        # handled shutdown and an unhandled kill both report 143 and only the log separates
        # them, while a crash signal is unambiguous.
        self.assertIn("LogExit: (Preparing to exit|Exiting)", script)
        self.assertIn("139|134|135|136) shutdown=crashed", script)
        self.assertNotIn("timeout --signal=INT --kill-after=10 \"${server_timeout}\"", script)
        invalid = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/carla/probe-town10-nullrhi-rpc.sh")],
            env={**os.environ, "CARLA_NULLRHI_PORT": "65535"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, invalid.returncode)
        self.assertIn("out of range", invalid.stderr)
        invalid_startup = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts/carla/probe-town10-nullrhi-rpc.sh")],
            env={**os.environ, "CARLA_NULLRHI_STARTUP_TIMEOUT": "invalid"},
            capture_output=True, text=True,
        )
        self.assertEqual(64, invalid_startup.returncode)
        self.assertIn("must be positive integers", invalid_startup.stderr)

    def test_vulkan_compute_replay_gate_is_explicit(self):
        result = subprocess.run(
            ["make", "-n", "carla-vulkan-compute"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("probe-vulkan-compute-replay.sh", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-vulkan-compute-replay.sh").read_text()
        for contract in (
            "vulkan-compute-replay.c",
            "vulkan-compute-smoke.comp",
            "spirv_reflect.c",
            "spirv-val --target-env vulkan1.3",
            "CARLA_COMPUTE_BACKEND",
            "CARLA_COMPUTE_MODE",
            "CARLA_COMPUTE_LAYOUT_FILE",
            "CARLA_COMPUTE_DEVICE_STATE",
            "CARLA_COMPUTE_HISTORY_FILE",
            "captured-device.h",
            "lvp_icd*.json",
            "VK_ICD_FILENAMES=",
        ):
            self.assertIn(contract, script)
        replay = (REPO_ROOT / "scripts/carla/vulkan-compute-replay.c").read_text()
        for contract in (
            "spvReflectCreateShaderModule",
            "vkCreateComputePipelines",
            "vkCmdDispatch",
            "readback_words",
            "ue_exact_replay",
            "layout_source",
            "--history",
            "history_pipeline_count",
        ):
            self.assertIn(contract, replay)
        gpu = subprocess.run(
            ["make", "-n", "carla-vulkan-compute-gpu"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile gpu run --rm -T", gpu.stdout)
        self.assertIn("CARLA_COMPUTE_BACKEND=gb10", gpu.stdout)
        self.assertIn("CARLA_COMPUTE_LAYOUT_FILE=", gpu.stdout)
        batch_make = subprocess.run(
            ["make", "-n", "carla-vulkan-compute", "COMPUTE_MODE=create",
             "COMPUTE_SHADER_DIR=/artifacts/carla/captured/shaders"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn('CARLA_COMPUTE_SHADER_DIR="/artifacts/carla/captured/shaders"',
                      batch_make.stdout)
        self.assertIn("batch_vulkan_compute.py", script)
        self.assertIn("Batch create requires only CARLA_COMPUTE_SHADER_DIR", script)

    def test_ue_vulkan_device_state_probe_is_diagnostic_only(self):
        result = subprocess.run(
            ["make", "-n", "carla-ue-vulkan-device-state"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile build run --rm -T", result.stdout)
        self.assertIn("probe-ue-vulkan-device-state.sh", result.stdout)
        script = (REPO_ROOT / "scripts/carla/probe-ue-vulkan-device-state.sh").read_text()
        gdb = (REPO_ROOT / "scripts/carla/dump-ue-vulkan-device-state.gdb").read_text()
        capture = (REPO_ROOT / "scripts/carla/gdb_vulkan_device_capture.py").read_text()
        for contract in (
            "CARLA_UE_VULKAN_DEVICE_STATE",
            "device-create.json",
            "VkDeviceCreateInfo",
            "vkCreateDevice",
            "device_extension_count",
        ):
            self.assertIn(contract, script + gdb + capture)
        self.assertIn("--cap-add SYS_PTRACE", result.stdout)
        # `docker compose run` has no --security-opt in compose v5, so passing it made this
        # target exit with "unknown flag" before the container started. CAP_SYS_PTRACE alone
        # was measured sufficient for gdb under the default seccomp profile.
        self.assertNotIn("--security-opt", result.stdout)
        self.assertNotIn("|| true", script)
        self.assertIn("vulkan_device_snapshot.py", script)
        self.assertIn("arm64-renderer-scope.sh", script)
        self.assertNotIn("VulkanDevice.cpp:391", gdb)
        gpu = subprocess.run(
            ["make", "-n", "carla-ue-vulkan-device-state-gpu"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        self.assertIn("--profile gpu run --rm -T", gpu.stdout)
        self.assertIn("/etc/vulkan/icd.d/nvidia_icd.json", gpu.stdout)
        self.assertIn("--cap-add SYS_PTRACE", gpu.stdout)

    def test_vulkan_device_capability_comparator_is_declared(self):
        script = (REPO_ROOT / "scripts/carla/compare_vulkan_device_state.py").read_text()
        for contract in (
            "device_extensions",
            "core_features",
            "missing_extensions",
            "missing_core_features",
            "not UE VkDeviceCreateInfo replay",
        ):
            self.assertIn(contract, script)
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
