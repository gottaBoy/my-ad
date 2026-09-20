#pragma once

#include "CoreMinimal.h"
#include "MeshDescription.h"

namespace CarlaUfbxMesh
{
    struct FSceneMaterial
    {
        FString Uid;
        FString DisplayLabel;
    };

    struct FSceneMesh
    {
        int64 SourceObjectId = 0;
        FString Uid;
        FString PayloadKey;
        FMeshDescription Mesh;
        TArray<FString> SlotKeys;
        TArray<FString> DefaultMaterialUids;
    };

    struct FSceneNode
    {
        int64 SourceObjectId = 0;
        int64 SourceAttributeId = 0;
        FString SourceAttributeType;
        bool bLegacyMetadataSupported = false;
        FString Uid;
        FString ParentUid;
        FString DisplayLabel;
        FString MeshUid;
        FTransform LocalTransform = FTransform::Identity;
        FTransform GlobalTransform = FTransform::Identity;
        FTransform GeometricTransform = FTransform::Identity;
        TArray<FString> MaterialUids;
    };

    struct FStaticScene
    {
        TArray<FSceneNode> Nodes;
        TArray<FSceneMesh> Meshes;
        TArray<FSceneMaterial> Materials;
        FString SourceSha256;
    };

    // Payload positions are local centimeters. Apply geometry, then global transform.
    // MaterialUids/DefaultMaterialUids index SlotKeys, never unique material pointers.
    // FBX numeric object IDs are required; failed imports leave Output unchanged.
    CARLAUFBXMESH_API bool ImportScene(const FString& Filename, FStaticScene& Output, FString& Error);
    CARLAUFBXMESH_API bool RunSceneSelfTests(int32& Passed, FString& Error);

    struct FStaticPayload
    {
        FString RequestUid;
        FString MeshUid;
        FString PayloadKey;
        FMeshDescription Mesh;
    };

    // Source-bound key and canonical bake matrix determine request identity.
    // Callers provide geometry/global transforms exactly once; failures are atomic.
    CARLAUFBXMESH_API bool FetchStaticPayload(const FStaticScene& Scene, const FString& PayloadKey,
        const FTransform& BakeTransform, FStaticPayload& Output, FString& Error);
}
