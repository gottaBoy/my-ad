using System;
using System.IO;
using UnrealBuildTool;

public class CarlaUfbxMesh : ModuleRules
{
    public CarlaUfbxMesh(ReadOnlyTargetRules Target) : base(Target)
    {
        if (Target.Platform != UnrealTargetPlatform.Linux || Target.Architecture != UnrealArch.Arm64)
            throw new BuildException("CarlaUfbxMesh requires native Linux ARM64");
        string Root = Environment.GetEnvironmentVariable("CARLA_UFBX_INSTALL");
        if (String.IsNullOrWhiteSpace(Root) || !Path.IsPathFullyQualified(Root)
            || !File.Exists(Path.Combine(Root, "include", "ufbx.h"))
            || !File.Exists(Path.Combine(Root, "lib", "libufbx.a")))
            throw new BuildException("CARLA_UFBX_INSTALL must be an absolute verified ufbx SDK root");
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "MeshDescription" });
        PrivateDependencyModuleNames.AddRange(new string[] {
            "CoreUObject", "StaticMeshDescription", "CarlaAssimpMesh"
        });
        PublicSystemIncludePaths.Add(Path.Combine(Root, "include"));
        string Library = Path.Combine(Root, "lib", "libufbx.a");
        PublicAdditionalLibraries.Add(Library);
        ExternalDependencies.Add(File.ResolveLinkTarget(Library, true)?.FullName ?? Library);
        ExternalDependencies.Add(Path.Combine(Root, "include", "ufbx.h"));
    }
}
