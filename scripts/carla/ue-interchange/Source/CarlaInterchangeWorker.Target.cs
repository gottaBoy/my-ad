using UnrealBuildTool;

[SupportedPlatforms(UnrealPlatformClass.Desktop)]
public class CarlaInterchangeWorkerTarget : TargetRules
{
    public CarlaInterchangeWorkerTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Program;
        DefaultBuildSettings = BuildSettingsVersion.Latest;
        IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
        LinkType = TargetLinkType.Monolithic;
        LaunchModuleName = "InterchangeWorker";
        ExtraModuleNames.Add("CarlaUfbxInterchange");
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
