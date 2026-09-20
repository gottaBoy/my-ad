using UnrealBuildTool;

public class CarlaInterchangeProbe : ModuleRules
{
    public CarlaInterchangeProbe(ReadOnlyTargetRules Target) : base(Target)
    {
        bool bStaticParser = System.Environment.GetEnvironmentVariable("CARLA_INTERCHANGE_UFBX_STATIC") == "1";
        PrivateDefinitions.Add("CARLA_INTERCHANGE_UFBX_STATIC=" + (bStaticParser ? "1" : "0"));
        if (bStaticParser) PrivateDependencyModuleNames.Add("InterchangeFbxParser");
        PublicIncludePathModuleNames.Add("Launch");
        PrivateDependencyModuleNames.AddRange(new string[] {
            "ApplicationCore", "Core", "CoreUObject", "Json", "Projects",
            "InterchangeCore", "InterchangeNodes", "InterchangeDispatcher", "MeshDescription",
            "StaticMeshDescription", "CarlaUfbxMesh", "CarlaUfbxInterchange", "CarlaUfbxLegacy"
        });
    }
}
