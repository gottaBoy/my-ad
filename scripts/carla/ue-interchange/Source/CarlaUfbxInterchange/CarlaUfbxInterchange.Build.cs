using UnrealBuildTool;

public class CarlaUfbxInterchange : ModuleRules
{
    public CarlaUfbxInterchange(ReadOnlyTargetRules Target) : base(Target)
    {
        bool Static = System.Environment.GetEnvironmentVariable("CARLA_INTERCHANGE_UFBX_STATIC") == "1";
        PublicDefinitions.Add("CARLA_INTERCHANGE_UFBX_STATIC=" + (Static ? "1" : "0"));
        PublicDependencyModuleNames.AddRange(new string[] {
            "Core", "CoreUObject", "InterchangeCore", "InterchangeNodes", "CarlaUfbxMesh"
        });
        if (Static) PublicDependencyModuleNames.Add("InterchangeFbxParser");
    }
}
