using UnrealBuildTool;

public class CarlaAssetProbeTarget : TargetRules
{
    public CarlaAssetProbeTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Program;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        LinkType = TargetLinkType.Monolithic;
        LaunchModuleName = "CarlaAssetProbe";
        bBuildDeveloperTools = false;
        bCompileAgainstEngine = true;
        bCompileAgainstEditor = false;
        bBuildWithEditorOnlyData = true;
        bCompileISPC = false;
        GlobalDefinitions.Add("CARLA_ASSET_PROBE=1");
        bCompileAgainstCoreUObject = true;
        bCompileAgainstApplicationCore = true;
        bCompileICU = false;
        bAllowEnginePluginsEnabledByDefault = false;
        bIsBuildingConsoleApplication = true;
        ExtraModuleNames.Add("CarlaAssetProbe");
    }
}
