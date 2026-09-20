using UnrealBuildTool;

[SupportedPlatforms(UnrealPlatformClass.Desktop)]
public class CarlaMeshBridgeTarget : TargetRules
{
    public CarlaMeshBridgeTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Program;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        LinkType = TargetLinkType.Monolithic;
        LaunchModuleName = "CarlaMeshBridge";
        bBuildDeveloperTools = false;
        bBuildWithEditorOnlyData = false;
        bCompileAgainstEngine = false;
        bCompileAgainstCoreUObject = true;
        bCompileAgainstApplicationCore = false;
        bCompileICU = false;
        bAllowEnginePluginsEnabledByDefault = false;
        bIsBuildingConsoleApplication = true;
    }
}
