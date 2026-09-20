using System;
using System.IO;
using UnrealBuildTool;

public class CarlaAssimpMesh : ModuleRules
{
    public CarlaAssimpMesh(ReadOnlyTargetRules Target) : base(Target)
    {
        if (!Target.IsInPlatformGroup(UnrealPlatformGroup.Unix) || Target.Architecture != UnrealArch.Arm64)
        {
            throw new BuildException("CarlaAssimpMesh currently requires native Linux ARM64");
        }
        string Root = Environment.GetEnvironmentVariable("CARLA_ASSIMP_INSTALL");
        if (String.IsNullOrEmpty(Root) || !File.Exists(Path.Combine(Root, "lib", "libassimp.so.6")))
        {
            throw new BuildException("CARLA_ASSIMP_INSTALL must point to the verified Assimp 6 SDK");
        }
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "MeshDescription" });
        PrivateDependencyModuleNames.AddRange(new string[] { "CoreUObject", "StaticMeshDescription" });
        PublicSystemIncludePaths.Add(Path.Combine(Root, "include"));
        string Library = Path.Combine(Root, "lib", "libassimp.so.6");
        // Track the actual file; a stable SONAME symlink may keep its old timestamp.
        string LibraryFile = File.ResolveLinkTarget(Library, true)?.FullName ?? Library;
        // UBT recognizes the .so linker name; use the resolved file for freshness/copy.
        PublicAdditionalLibraries.Add(Path.Combine(Root, "lib", "libassimp.so"));
        ExternalDependencies.Add(LibraryFile);
        PublicRuntimeLibraryPaths.Add("$(TargetOutputDir)");
        RuntimeDependencies.Add("$(TargetOutputDir)/libassimp.so.6", LibraryFile);
    }
}
