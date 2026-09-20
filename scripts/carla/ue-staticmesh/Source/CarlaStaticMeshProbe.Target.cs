using UnrealBuildTool;

[SupportedPlatforms(UnrealPlatformClass.Desktop)]
public class CarlaStaticMeshProbeTarget : TargetRules
{
    public CarlaStaticMeshProbeTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Program;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        LinkType = TargetLinkType.Monolithic;
        LaunchModuleName = "CarlaStaticMeshProbe";
        bBuildDeveloperTools = false;
        bCompileAgainstEngine = true;
        bCompileAgainstEditor = false;
        bBuildWithEditorOnlyData = true;
        bCompileISPC = false;
        GlobalDefinitions.Add("CARLA_STATICMESH_PROBE=1");
        bCompileAgainstCoreUObject = true;
        bCompileAgainstApplicationCore = true;
        bCompileICU = false;
        bAllowEnginePluginsEnabledByDefault = false;
        bIsBuildingConsoleApplication = true;
    }
}
