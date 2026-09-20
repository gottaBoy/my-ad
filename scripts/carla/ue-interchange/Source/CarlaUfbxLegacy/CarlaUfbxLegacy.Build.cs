using System.IO;
using UnrealBuildTool;

public class CarlaUfbxLegacy : ModuleRules
{
    public CarlaUfbxLegacy(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CarlaUfbxMesh" });
        // Only the SDK-free Core value/helper headers, not the UnrealEd module.
        PublicIncludePaths.Add(Path.Combine(EngineDirectory, "Source/Editor/UnrealEd/Public/ImportUtils"));
    }
}
