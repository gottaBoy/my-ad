#pragma once

#include "CarlaUfbxScene.h"
#include "SceneImportHierarchy.h"

namespace CarlaUfbxLegacy
{
struct FMeshInstance
{
    int32 SourceNodeIndex = INDEX_NONE;
    int32 MeshIndex = INDEX_NONE;
    FString PayloadKey;
    FTransform GeometricTransform = FTransform::Identity;
    TArray<FString> MaterialUids;
};

struct FSceneHierarchy
{
    FString SourceSha256;
    TArray<UE::Import::FSceneNodeInfo> SourceNodes;
    TArray<UE::Import::FSceneImportHierarchyEntry> ActorNodes;
    TArray<FMeshInstance> MeshInstances;
};

// Source IDs, evaluated UE-cm transforms and instance bindings, not saved assets.
// This static profile rejects unsupported metadata; failures leave Output untouched.
CARLAUFBXLEGACY_API bool BuildSceneHierarchy(const CarlaUfbxMesh::FStaticScene& Scene,
    FSceneHierarchy& Output, FString& Error);
}
