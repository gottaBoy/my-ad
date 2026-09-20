import os
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
        self.assertIn("CarlaUnrealEditor Linux Development -architecture=arm64", script)
        self.assertIn("-SkipBuild", script)
        self.assertIn("-NoUBTMakefiles", script)
        self.assertIn("UBT dependency graph only", script)

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
        self.assertIn("must be full, no-usd or legacy-fbx-headers-only", result.stderr)

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
        self.assertIn("-ExecCmds=quit", script)
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
