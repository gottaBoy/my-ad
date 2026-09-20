#include "CarlaUfbxInterchange.h"
#include "InterchangeMeshNode.h"
#include "InterchangeSceneNode.h"

static bool AddNode(UInterchangeBaseNodeContainer& Container, UInterchangeBaseNode* Node,
    TSet<FString>& Uids, FString& Error)
{
    if (!Node || Node->GetUniqueID().IsEmpty() || Uids.Contains(Node->GetUniqueID()))
    {
        Error = TEXT("Duplicate or invalid Interchange node UID");
        return false;
    }
    Uids.Add(Node->GetUniqueID());
    if (Container.AddNode(Node) != Node->GetUniqueID())
    {
        Error = TEXT("Could not add Interchange node");
        return false;
    }
    return true;
}

bool BuildGraph(const CarlaUfbxMesh::FStaticScene& Scene,
    TStrongObjectPtr<UInterchangeBaseNodeContainer>& OutContainer, FString& Error)
{
    if (Scene.Nodes.Num() == 0 || Scene.Meshes.Num() == 0 || Scene.SourceSha256.Len() != 64)
    {
        Error = TEXT("Scene graph has no nodes, meshes or source digest");
        return false;
    }
    TStrongObjectPtr<UInterchangeBaseNodeContainer> Container(
        NewObject<UInterchangeBaseNodeContainer>());
    TSet<FString> Uids;
    for (const CarlaUfbxMesh::FSceneMaterial& Material : Scene.Materials)
    {
        UInterchangeBaseNode* Node = NewObject<UInterchangeBaseNode>(Container.Get());
        Node->InitializeNode(Material.Uid, Material.DisplayLabel,
            EInterchangeNodeContainerType::TranslatedAsset);
        if (!Node->AddStringAttribute(TEXT("Carla.NodeKind"), TEXT("MaterialIdentity"))
            || !Node->AddStringAttribute(TEXT("Carla.SourceSha256"), Scene.SourceSha256)
            || !Node->AddStringAttribute(TEXT("Carla.SourceUid"), Material.Uid)
            || !AddNode(*Container, Node, Uids, Error)) return false;
    }
    for (const CarlaUfbxMesh::FSceneMesh& SourceMesh : Scene.Meshes)
    {
        UInterchangeMeshNode* Node = NewObject<UInterchangeMeshNode>(Container.Get());
        Node->InitializeNode(SourceMesh.Uid, SourceMesh.Uid,
            EInterchangeNodeContainerType::TranslatedAsset);
        Node->SetPayLoadKey(SourceMesh.PayloadKey, EInterchangeMeshPayLoadType::STATIC);
        if (!Node->SetCustomVertexCount(SourceMesh.Mesh.Vertices().Num())
            || !Node->SetCustomPolygonCount(SourceMesh.Mesh.Triangles().Num())
            || !Node->AddStringAttribute(TEXT("Carla.SourceSha256"), Scene.SourceSha256)
            || !Node->AddStringAttribute(TEXT("Carla.SourceUid"), SourceMesh.Uid)
            || SourceMesh.SlotKeys.Num() != SourceMesh.DefaultMaterialUids.Num()
            || !AddNode(*Container, Node, Uids, Error)) return false;
        for (int32 Slot = 0; Slot < SourceMesh.SlotKeys.Num(); ++Slot)
            if (!Node->SetSlotMaterialDependencyUid(SourceMesh.SlotKeys[Slot],
                SourceMesh.DefaultMaterialUids[Slot]))
            { Error = TEXT("Could not set mesh material dependency"); return false; }
        for (const CarlaUfbxMesh::FSceneNode& Instance : Scene.Nodes)
            if (Instance.MeshUid == SourceMesh.Uid && !Node->SetSceneInstanceUid(Instance.Uid))
            { Error = TEXT("Could not set mesh scene instance"); return false; }
    }
    for (const CarlaUfbxMesh::FSceneNode& SourceNode : Scene.Nodes)
    {
        UInterchangeSceneNode* Node = NewObject<UInterchangeSceneNode>(Container.Get());
        Node->InitializeNode(SourceNode.Uid, SourceNode.DisplayLabel,
            EInterchangeNodeContainerType::TranslatedScene);
        if (!Node->SetCustomLocalTransform(Container.Get(), SourceNode.LocalTransform)
            || !Node->SetCustomGeometricTransform(SourceNode.GeometricTransform)
            || !Node->AddStringAttribute(TEXT("Carla.SourceSha256"), Scene.SourceSha256)
            || !Node->AddStringAttribute(TEXT("Carla.SourceUid"), SourceNode.Uid)
            || (!SourceNode.MeshUid.IsEmpty() && !Node->SetCustomAssetInstanceUid(SourceNode.MeshUid))
            || !AddNode(*Container, Node, Uids, Error)) return false;
        for (int32 Slot = 0; Slot < SourceNode.MaterialUids.Num(); ++Slot)
            if (!Node->SetSlotMaterialDependencyUid(FString::Printf(TEXT("Slot_%d"), Slot),
                SourceNode.MaterialUids[Slot]))
            { Error = TEXT("Could not set scene material dependency"); return false; }
    }
    for (const CarlaUfbxMesh::FSceneNode& SourceNode : Scene.Nodes)
        if (!SourceNode.ParentUid.IsEmpty() && !Container->SetNodeParentUid(
            SourceNode.Uid, SourceNode.ParentUid))
        { Error = TEXT("Could not set Interchange node parent"); return false; }
    Container->ComputeChildrenCache();
    OutContainer = MoveTemp(Container);
    return true;
}

bool VerifyGraph(const CarlaUfbxMesh::FStaticScene& Scene,
    const UInterchangeBaseNodeContainer& Container, FString& Error)
{
    TArray<FString> All;
    Container.GetNodes(UInterchangeBaseNode::StaticClass(), All);
    if (All.Num() != Scene.Nodes.Num() + Scene.Meshes.Num() + Scene.Materials.Num())
    { Error = TEXT("Interchange node count changed"); return false; }
    for (const CarlaUfbxMesh::FSceneMaterial& Material : Scene.Materials)
    {
        const UInterchangeBaseNode* Node = Container.GetNode(Material.Uid);
        FString Kind, Digest, SourceUid;
        if (!Node || !Node->GetStringAttribute(TEXT("Carla.NodeKind"), Kind)
            || Kind != TEXT("MaterialIdentity")
            || !Node->GetStringAttribute(TEXT("Carla.SourceSha256"), Digest)
            || Digest != Scene.SourceSha256
            || !Node->GetStringAttribute(TEXT("Carla.SourceUid"), SourceUid)
            || SourceUid != Material.Uid)
        { Error = TEXT("Material identity changed during graph round trip"); return false; }
    }
    for (const CarlaUfbxMesh::FSceneMesh& SourceMesh : Scene.Meshes)
    {
        const UInterchangeMeshNode* Node = Cast<UInterchangeMeshNode>(Container.GetNode(SourceMesh.Uid));
        const TOptional<FInterchangeMeshPayLoadKey> Payload = Node ? Node->GetPayLoadKey() : TOptional<FInterchangeMeshPayLoadKey>();
        int32 Vertices = 0, Polygons = 0;
        TArray<FString> Instances;
        TMap<FString, FString> Dependencies;
        if (!Node || !Payload.IsSet() || Payload.GetValue().UniqueId != SourceMesh.PayloadKey
            || Payload.GetValue().Type != EInterchangeMeshPayLoadType::STATIC
            || !Node->GetCustomVertexCount(Vertices) || Vertices != SourceMesh.Mesh.Vertices().Num()
            || !Node->GetCustomPolygonCount(Polygons) || Polygons != SourceMesh.Mesh.Triangles().Num())
        { Error = TEXT("Mesh payload metadata changed during graph round trip"); return false; }
        Node->GetSceneInstanceUids(Instances);
        Node->GetSlotMaterialDependencies(Dependencies);
        if (Dependencies.Num() != SourceMesh.SlotKeys.Num())
        { Error = TEXT("Mesh slot dependencies changed during graph round trip"); return false; }
        for (int32 Slot = 0; Slot < SourceMesh.SlotKeys.Num(); ++Slot)
        {
            const FString* Value = Dependencies.Find(SourceMesh.SlotKeys[Slot]);
            if (!Value || *Value != SourceMesh.DefaultMaterialUids[Slot])
            { Error = TEXT("Mesh material slot binding changed during graph round trip"); return false; }
        }
        Instances.Sort();
        TArray<FString> Expected;
        for (const CarlaUfbxMesh::FSceneNode& Instance : Scene.Nodes)
            if (Instance.MeshUid == SourceMesh.Uid) Expected.Add(Instance.Uid);
        Expected.Sort();
        if (Instances != Expected)
        { Error = TEXT("Mesh instance bindings changed during graph round trip"); return false; }
    }
    for (const CarlaUfbxMesh::FSceneNode& SourceNode : Scene.Nodes)
    {
        const UInterchangeSceneNode* Node = Cast<UInterchangeSceneNode>(Container.GetNode(SourceNode.Uid));
        FTransform Local, Geometric;
        if (!Node || Node->GetParentUid() != SourceNode.ParentUid
            || !Node->GetCustomLocalTransform(Local) || !Local.Equals(SourceNode.LocalTransform, 1e-8f)
            || !Node->GetCustomGeometricTransform(Geometric)
            || !Geometric.Equals(SourceNode.GeometricTransform, 1e-8f))
        { Error = TEXT("Scene transform or parent changed during graph round trip"); return false; }
        FString AssetUid;
        if ((!SourceNode.MeshUid.IsEmpty()
            && (!Node->GetCustomAssetInstanceUid(AssetUid) || AssetUid != SourceNode.MeshUid))
            || (SourceNode.MeshUid.IsEmpty() && Node->GetCustomAssetInstanceUid(AssetUid) && !AssetUid.IsEmpty()))
        { Error = TEXT("Scene asset reference changed during graph round trip"); return false; }
        TMap<FString, FString> Dependencies;
        Node->GetSlotMaterialDependencies(Dependencies);
        if (Dependencies.Num() != SourceNode.MaterialUids.Num())
        { Error = TEXT("Scene material bindings changed during graph round trip"); return false; }
        for (int32 Slot = 0; Slot < SourceNode.MaterialUids.Num(); ++Slot)
        {
            const FString* Value = Dependencies.Find(FString::Printf(TEXT("Slot_%d"), Slot));
            if (!Value || *Value != SourceNode.MaterialUids[Slot])
            { Error = TEXT("Scene material slot binding changed during graph round trip"); return false; }
        }
    }
    return true;
}

