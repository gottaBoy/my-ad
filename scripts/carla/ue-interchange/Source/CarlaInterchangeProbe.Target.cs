using UnrealBuildTool;

[SupportedPlatforms(UnrealPlatformClass.Desktop)]
public class CarlaInterchangeProbeTarget : TargetRules
{
    public CarlaInterchangeProbeTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Program;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        LinkType = TargetLinkType.Monolithic;
        LaunchModuleName = "CarlaInterchangeProbe";
        bBuildDeveloperTools = false;
        bBuildWithEditorOnlyData = false;
        bCompileAgainstEngine = false;
        bCompileAgainstCoreUObject = true;
        bCompileAgainstApplicationCore = true;
        bCompileICU = false;
        bAllowEnginePluginsEnabledByDefault = false;
        bIsBuildingConsoleApplication = true;
    }
}
