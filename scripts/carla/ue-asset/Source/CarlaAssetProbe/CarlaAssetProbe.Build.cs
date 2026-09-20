using UnrealBuildTool;

public class CarlaAssetProbe : ModuleRules
{
    public CarlaAssetProbe(ReadOnlyTargetRules Target) : base(Target)
    {
        if (Target.Platform != UnrealTargetPlatform.Linux || Target.Architecture != UnrealArch.Arm64
            || Target.Type != TargetType.Program || !Target.bCompileAgainstEngine
            || Target.bCompileAgainstEditor || !Target.bBuildWithEditorOnlyData || Target.bCompileISPC)
            throw new BuildException("CarlaAssetProbe requires an ARM64 Engine Program with Editor-only data and scalar ISPC policy");
        PublicIncludePathModuleNames.Add("Launch");
        PrivateIncludePathModuleNames.Add("DerivedDataCache");
        PrivateDependencyModuleNames.AddRange(new string[] {
            "Core", "CoreUObject", "Engine", "ApplicationCore", "Projects", "Json",
            "RenderCore", "RHI", "MeshDescription", "StaticMeshDescription", "CarlaUfbxMesh",
            "CarlaUfbxLegacy", "HeadMountedDisplay", "InstallBundleManager", "MediaUtils",
            "MRMesh", "MoviePlayer", "MoviePlayerProxy", "MovieScene", "PreLoadScreen",
            "SessionServices", "SlateNullRenderer", "SlateRHIRenderer", "ProfileVisualizer",
            "AutomationController", "AutomationWorker", "ShaderPreprocessor", "ShaderFormatVectorVM"
        });
    }
}
