using UnrealBuildTool;

public class CarlaStaticMeshProbe : ModuleRules
{
    public CarlaStaticMeshProbe(ReadOnlyTargetRules Target) : base(Target)
    {
        if (Target.Platform != UnrealTargetPlatform.Linux || Target.Architecture != UnrealArch.Arm64
            || Target.Type != TargetType.Program || !Target.bCompileAgainstEngine
            || Target.bCompileAgainstEditor || !Target.bBuildWithEditorOnlyData || Target.bCompileISPC)
            throw new BuildException("CarlaStaticMeshProbe requires an ARM64 Engine Program with Editor-only data and scalar ISPC policy");
        System.Console.WriteLine("CarlaStaticMeshProbe: Engine=1 Editor=0 EditorOnlyData=1 ISPC=0 ARM64");
        PublicIncludePathModuleNames.Add("Launch");
        PrivateIncludePathModuleNames.Add("DerivedDataCache");
        PrivateDependencyModuleNames.AddRange(new string[] {
            "Core", "CoreUObject", "Engine", "ApplicationCore", "Projects", "Json",
            "RenderCore", "RHI", "MeshDescription", "StaticMeshDescription", "CarlaUfbxMesh",
            "CarlaUfbxLegacy", "HeadMountedDisplay", "InstallBundleManager", "MediaUtils",
            "MRMesh", "MoviePlayer", "MoviePlayerProxy", "MovieScene", "PreLoadScreen",
            "SessionServices", "SlateNullRenderer", "SlateRHIRenderer", "ProfileVisualizer",
            "AutomationController", "AutomationWorker"
        });
    }
}
