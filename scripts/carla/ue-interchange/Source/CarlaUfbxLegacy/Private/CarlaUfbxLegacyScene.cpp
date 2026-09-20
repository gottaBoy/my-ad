#include "CarlaUfbxLegacyScene.h"
#include "Modules/ModuleManager.h"

IMPLEMENT_MODULE(FDefaultModuleImpl, CarlaUfbxLegacy)

namespace CarlaUfbxLegacy
{
namespace
{
FString ObjectUid(int64 Id)
{
    return FString::Printf(TEXT("FBX/Object/%lld"), static_cast<long long>(Id));
}

bool ValidTransform(const FTransform& Transform)
{
    const FVector Scale = Transform.GetScale3D();
    return !Transform.ContainsNaN() && Transform.GetRotation().IsNormalized()
        && Scale.X != 0.0 && Scale.Y != 0.0 && Scale.Z != 0.0;
}

bool MatchesTransform(const FTransform& A, const FTransform& B)
{
    const FMatrix Left = A.ToMatrixWithScale(), Right = B.ToMatrixWithScale();
    for (int32 Row = 0; Row < 4; ++Row)
        for (int32 Column = 0; Column < 4; ++Column)
            if (!FMath::IsFinite(Left.M[Row][Column]) || !FMath::IsFinite(Right.M[Row][Column])
                || FMath::Abs(Left.M[Row][Column] - Right.M[Row][Column])
                > 1e-8 * FMath::Max(1.0, FMath::Abs(Right.M[Row][Column]))) return false;
    return true;
}
}

bool BuildSceneHierarchy(const CarlaUfbxMesh::FStaticScene& Scene, FSceneHierarchy& Output, FString& Error)
{
    Error.Reset();
    if (Scene.Nodes.IsEmpty() || Scene.Meshes.IsEmpty() || Scene.SourceSha256.Len() != 64)
    { Error = TEXT("Missing static scene or source identity"); return false; }
    for (TCHAR Character : Scene.SourceSha256)
        if (!((Character >= '0' && Character <= '9') || (Character >= 'a' && Character <= 'f')))
        { Error = TEXT("Invalid source digest"); return false; }

    TMap<FString, int32> MeshIndices;
    TSet<int64> ObjectIds;
    TSet<FString> MaterialIds;
    for (const auto& Material : Scene.Materials)
    {
        if (Material.Uid.IsEmpty() || MaterialIds.Contains(Material.Uid))
        { Error = TEXT("Invalid scene material identity"); return false; }
        MaterialIds.Add(Material.Uid);
    }
    for (int32 Index = 0; Index < Scene.Meshes.Num(); ++Index)
    {
        const auto& Mesh = Scene.Meshes[Index];
        if (!Mesh.SourceObjectId || Mesh.Uid != ObjectUid(Mesh.SourceObjectId)
            || ObjectIds.Contains(Mesh.SourceObjectId)
            || Mesh.PayloadKey != TEXT("ufbx-static-ue-cm-v1/") + Scene.SourceSha256 + TEXT("/") + Mesh.Uid)
        { Error = TEXT("Invalid or duplicate source mesh identity"); return false; }
        ObjectIds.Add(Mesh.SourceObjectId);
        MeshIndices.Add(Mesh.Uid, Index);
    }

    FSceneHierarchy Result;
    Result.SourceSha256 = Scene.SourceSha256;
    TMap<FString, int32> NodeIndices;
    TSet<int32> ReferencedMeshes;
    for (int32 Index = 0; Index < Scene.Nodes.Num(); ++Index)
    {
        const auto& Node = Scene.Nodes[Index];
        const bool bRoot = Index == 0;
        if (!Node.bLegacyMetadataSupported || (Node.SourceAttributeType != TEXT("eNull")
            && Node.SourceAttributeType != TEXT("eMesh") && Node.SourceAttributeType != TEXT("eLODGroup")))
        { Error = TEXT("Unsupported Legacy metadata: attribute kind, multiple attributes or nonzero pivots"); return false; }
        if ((bRoot && (Node.Uid != TEXT("FBX/Root") || Node.SourceObjectId || !Node.ParentUid.IsEmpty()))
            || (!bRoot && (!Node.SourceObjectId || Node.Uid != ObjectUid(Node.SourceObjectId)))
            || NodeIndices.Contains(Node.Uid) || ObjectIds.Contains(Node.SourceObjectId))
        { Error = TEXT("Invalid or duplicate source node identity"); return false; }
        if (!ValidTransform(Node.LocalTransform) || !ValidTransform(Node.GlobalTransform)
            || !ValidTransform(Node.GeometricTransform))
        { Error = TEXT("Invalid scene transform"); return false; }

        UE::Import::FSceneNodeInfo Item;
        Item.UniqueId = static_cast<uint64>(Node.SourceObjectId);
        Item.ObjectName = Node.DisplayLabel;
        Item.AttributeType = Node.SourceAttributeType;
        Item.AttributeUniqueId = static_cast<uint64>(Node.SourceAttributeId);
        Item.Transform = bRoot ? Node.GlobalTransform : Node.LocalTransform;
        if (bRoot && !MatchesTransform(Node.LocalTransform, Node.GlobalTransform))
        { Error = TEXT("Root transforms disagree"); return false; }
        if (!bRoot)
        {
            const int32* Parent = NodeIndices.Find(Node.ParentUid);
            if (!Parent) { Error = TEXT("Missing or non-parent-first source node"); return false; }
            Item.ParentUniqueId = Result.SourceNodes[*Parent].UniqueId;
            Item.ParentName = Result.SourceNodes[*Parent].ObjectName;
            if (!MatchesTransform(Node.LocalTransform * Scene.Nodes[*Parent].GlobalTransform, Node.GlobalTransform))
            { Error = TEXT("Parent and global transforms disagree"); return false; }
        }
        if (Node.SourceAttributeType == TEXT("eMesh"))
        {
            const int32* MeshIndex = MeshIndices.Find(Node.MeshUid);
            if (!MeshIndex || Node.SourceAttributeId != Scene.Meshes[*MeshIndex].SourceObjectId
                || Node.MaterialUids.Num() != Scene.Meshes[*MeshIndex].SlotKeys.Num())
            { Error = TEXT("Invalid mesh instance binding"); return false; }
            FMeshInstance Instance;
            Instance.SourceNodeIndex = Index;
            Instance.MeshIndex = *MeshIndex;
            Instance.PayloadKey = Scene.Meshes[*MeshIndex].PayloadKey;
            Instance.GeometricTransform = Node.GeometricTransform;
            Instance.MaterialUids = Node.MaterialUids;
            for (const FString& MaterialUid : Instance.MaterialUids)
                if (!MaterialIds.Contains(MaterialUid))
                { Error = TEXT("Unknown instance material"); return false; }
            Result.MeshInstances.Add(MoveTemp(Instance));
            ReferencedMeshes.Add(*MeshIndex);
        }
        else if (!Node.MeshUid.IsEmpty() || !Node.MaterialUids.IsEmpty())
        { Error = TEXT("Non-mesh node has mesh bindings"); return false; }
        NodeIndices.Add(Node.Uid, Index);
        ObjectIds.Add(Node.SourceObjectId);
        Result.SourceNodes.Add(MoveTemp(Item));
    }
    if (ReferencedMeshes.Num() != Scene.Meshes.Num())
    { Error = TEXT("Unreferenced scene mesh"); return false; }
    if (!UE::Import::BuildSceneImportHierarchy(Result.SourceNodes, Result.ActorNodes, Error)) return false;
    Output = MoveTemp(Result);
    return true;
}
}
