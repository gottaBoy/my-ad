using System;
using UnrealBuildTool;

public class CarlaMeshBridge : ModuleRules
{
    public CarlaMeshBridge(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicIncludePathModuleNames.Add("Launch");
        PrivateDependencyModuleNames.AddRange(new string[] {
            "Core", "CoreUObject", "Projects", "Json", "MeshDescription", "CarlaAssimpMesh"
        });
        bool WithUfbx = Environment.GetEnvironmentVariable("CARLA_UFBX_INSTALL") != null;
        PrivateDefinitions.Add("CARLA_WITH_UFBX=" + (WithUfbx ? "1" : "0"));
        if (WithUfbx) PrivateDependencyModuleNames.Add("CarlaUfbxMesh");
    }
}
