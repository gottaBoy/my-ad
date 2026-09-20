#include "CarlaUfbxLegacyScene.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include <limits>

#ifdef _FBXSDK_H_
#error Native Legacy hierarchy code must not include the Autodesk SDK
#endif

namespace
{
const TCHAR* Stage = TEXT("ue-ufbx-legacy-hierarchy");
const TCHAR* Scope = TEXT("ufbx to shared Legacy factory hierarchy; not asset import, save/reimport, Editor or Cook");

struct FChecks
{
    TArray<TSharedPtr<FJsonValue>> Names;
    FString Error;
    bool Require(const TCHAR* Name, bool Condition)
    {
        if (!Condition) { Error = FString::Printf(TEXT("%s: %s"), Name, *Error); return false; }
        Names.Add(MakeShared<FJsonValueString>(Name));
        return true;
    }
};

bool CoreChecks(FChecks& Checks)
{
    using namespace UE::Import;
    TArray<FSceneNodeInfo> Nodes;
    auto Add = [&](uint64 Id, uint64 Parent, const TCHAR* Kind)
    {
        FSceneNodeInfo Node;
        Node.UniqueId = Id; Node.ParentUniqueId = Parent; Node.AttributeType = Kind;
        Nodes.Add(MoveTemp(Node));
    };
    Add(10, 0, TEXT("eNull")); Add(20, 10, TEXT("eNull"));
    Add(30, 10, TEXT("eSkeleton")); Add(31, 30, TEXT("eMesh"));
    Add(40, 10, TEXT("eLODGroup")); Add(41, 40, TEXT("eMesh"));
    Add(42, 40, TEXT("eNull")); Add(43, 42, TEXT("eMesh"));
    TArray<FSceneImportHierarchyEntry> Output;
    if (!Checks.Require(TEXT("shared factory skeleton and LOD policy"),
        BuildSceneImportHierarchy(Nodes, Output, Checks.Error) && Output.Num() == 6
        && Output[2].SourceIndex == 4 && Output[3].ParentIndex == 2
        && Output[3].bImportNode && !Output[4].bImportNode && Output[5].bImportNode)) return false;
    for (int32 Mode = 0; Mode < 6; ++Mode)
    {
        TArray<FSceneNodeInfo> Bad = Nodes;
        if (Mode == 0) Bad[1].UniqueId = Bad[0].UniqueId;
        if (Mode == 1) Bad[1].ParentUniqueId = 999;
        if (Mode == 2) Bad.Swap(0, 1);
        if (Mode == 3) Bad[1].ParentUniqueId = Bad[1].UniqueId;
        if (Mode == 4) Bad[0].ParentUniqueId = 10;
        if (Mode == 5) Bad.Empty();
        TArray<FSceneImportHierarchyEntry> Preserved = Output;
        FString Error;
        if (!Checks.Require(TEXT("malformed hierarchy rejected atomically"),
            !BuildSceneImportHierarchy(Bad, Preserved, Error) && !Error.IsEmpty()
            && Preserved.Num() == Output.Num() && Preserved[2].SourceIndex == 4)) return false;
    }
    Nodes.Empty();
    Add(0, 0, TEXT("eNull")); Add(std::numeric_limits<uint64>::max(), 0, TEXT("eMesh"));
    if (!Checks.Require(TEXT("synthetic root and full width IDs"),
        BuildSceneImportHierarchy(Nodes, Output, Checks.Error) && Output.Num() == 2
        && Output[0].ParentIndex == INDEX_NONE && Output[1].ParentIndex == 0)) return false;
    Nodes.Empty();
    for (int32 Index = 0; Index < 10000; ++Index)
        Add(Index + 1, Index, Index == 100 ? TEXT("eSkeleton") : TEXT("eNull"));
    return Checks.Require(TEXT("deep hierarchy without recursive traversal"),
        BuildSceneImportHierarchy(Nodes, Output, Checks.Error) && Output.Num() == 100);
}

bool Unchanged(const CarlaUfbxLegacy::FSceneHierarchy& Value)
{
    return Value.SourceSha256 == TEXT("keep") && Value.SourceNodes.Num() == 1
        && Value.SourceNodes[0].ObjectName == TEXT("keep-node") && Value.ActorNodes.IsEmpty()
        && Value.MeshInstances.IsEmpty();
}

bool Reject(const CarlaUfbxMesh::FStaticScene& Scene, FChecks& Checks, const TCHAR* Name)
{
    CarlaUfbxLegacy::FSceneHierarchy Output;
    Output.SourceSha256 = TEXT("keep");
    Output.SourceNodes.AddDefaulted(); Output.SourceNodes[0].ObjectName = TEXT("keep-node");
    FString Error;
    const bool Rejected = !CarlaUfbxLegacy::BuildSceneHierarchy(Scene, Output, Error);
    return Checks.Require(Name, Rejected && !Error.IsEmpty() && Unchanged(Output));
}

bool NativeChecks(const FString& Input, const FString& ResultDir, FChecks& Checks,
    CarlaUfbxLegacy::FSceneHierarchy& Output)
{
    if (!CoreChecks(Checks)) return false;
    CarlaUfbxMesh::FStaticScene Scene;
    if (!CarlaUfbxMesh::ImportScene(Input, Scene, Checks.Error)
        || !CarlaUfbxLegacy::BuildSceneHierarchy(Scene, Output, Checks.Error)) return false;
    if (!Checks.Require(TEXT("real ufbx IDs and shared factory hierarchy"),
        Scene.Nodes.Num() == 4 && Scene.Meshes.Num() == 2 && Output.SourceNodes.Num() == 4
        && Output.ActorNodes.Num() == 4 && Output.MeshInstances.Num() == 2
        && Output.SourceNodes[0].UniqueId == 0 && Output.SourceNodes[1].UniqueId == 100
        && Output.SourceNodes[2].UniqueId == 101 && Output.SourceNodes[3].UniqueId == 102
        && Output.SourceNodes[2].ParentUniqueId == 100 && Output.ActorNodes[2].ParentIndex == 1)) return false;
    for (const auto& Instance : Output.MeshInstances)
    {
        const auto& Node = Scene.Nodes[Instance.SourceNodeIndex];
        CarlaUfbxMesh::FStaticPayload Payload;
        if (!Checks.Require(TEXT("instance transform material and native payload"),
            Instance.MaterialUids == Node.MaterialUids && Instance.GeometricTransform.Equals(Node.GeometricTransform)
            && Output.SourceNodes[Instance.SourceNodeIndex].Transform.Equals(Node.LocalTransform)
            && CarlaUfbxMesh::FetchStaticPayload(Scene, Instance.PayloadKey,
                Instance.GeometricTransform * Node.GlobalTransform, Payload, Checks.Error)
            && Payload.Mesh.Triangles().Num() == 1 && Payload.Mesh.Vertices().Num() == 3)) return false;
    }
    for (int32 Mode = 0; Mode < 11; ++Mode)
    {
        auto Bad = Scene;
        if (Mode == 0) Bad.SourceSha256 = TEXT("bad");
        if (Mode == 1) Bad.Nodes[2].ParentUid = TEXT("missing");
        if (Mode == 2) Bad.Nodes[2].SourceObjectId = 999;
        if (Mode == 3) Bad.Meshes[0].SourceObjectId = 0;
        if (Mode == 4) Bad.Nodes[2].SourceAttributeId = 999;
        if (Mode == 5) Bad.Nodes[2].MaterialUids[0] = TEXT("unknown");
        if (Mode == 6) Bad.Nodes[2].bLegacyMetadataSupported = false;
        if (Mode == 7) Bad.Nodes[2].GlobalTransform.SetTranslation(FVector(999.0));
        if (Mode == 8) Bad.Nodes[2].GeometricTransform.SetTranslation(FVector(std::numeric_limits<double>::infinity()));
        if (Mode == 9) Bad.Nodes.Swap(0, 1);
        if (Mode == 10) Bad.Nodes[2].GeometricTransform.SetScale3D(FVector(0.0, 1.0, 1.0));
        if (!Reject(Bad, Checks, TEXT("corrupt adapter input rejected atomically"))) return false;
    }
    FString Original;
    if (!FFileHelper::LoadFileToString(Original, *Input)) return false;
    const FString Anchor = TEXT("P: \"Lcl Translation\", \"Lcl Translation\", \"\", \"A\", -2, 0, 0");
    if (!Original.Contains(Anchor)) { Checks.Error = TEXT("Expected project fixture anchor"); return false; }
    FString Pivot = Original.Replace(*Anchor, *(Anchor + TEXT("\n P: \"RotationPivot\", \"Vector3D\", \"Vector\", \"\", 1, 2, 3")));
    const FString PivotPath = FPaths::Combine(ResultDir, TEXT("nonzero-pivot.fbx"));
    if (FPaths::FileExists(PivotPath) || !FFileHelper::SaveStringToFile(Pivot, *PivotPath)) return false;
    CarlaUfbxMesh::FStaticScene PivotScene;
    if (!CarlaUfbxMesh::ImportScene(PivotPath, PivotScene, Checks.Error)
        || !Reject(PivotScene, Checks, TEXT("real nonzero pivot metadata rejected"))) return false;

    const FString Camera = Original.Replace(TEXT("Objects: {"),
        TEXT("Objects: {\n NodeAttribute: 500, \"NodeAttribute::Camera\", \"Camera\" { TypeFlags: \"Camera\" }"))
        .Replace(TEXT("Connections: {"), TEXT("Connections: {\n C: \"OO\", 500, 100"));
    const FString CameraPath = FPaths::Combine(ResultDir, TEXT("camera.fbx"));
    if (Camera == Original || FPaths::FileExists(CameraPath) || !FFileHelper::SaveStringToFile(Camera, *CameraPath)) return false;
    CarlaUfbxMesh::FStaticScene CameraScene;
    if (!CarlaUfbxMesh::ImportScene(CameraPath, CameraScene, Checks.Error)
        || !Reject(CameraScene, Checks, TEXT("real camera metadata rejected"))) return false;

    const FString HierarchyPath = FPaths::Combine(FPaths::GetPath(Input), TEXT("hierarchy-geometry.fbx"));
    CarlaUfbxMesh::FStaticScene Instanced;
    CarlaUfbxLegacy::FSceneHierarchy InstancedOutput;
    if (!CarlaUfbxMesh::ImportScene(HierarchyPath, Instanced, Checks.Error)
        || !CarlaUfbxLegacy::BuildSceneHierarchy(Instanced, InstancedOutput, Checks.Error)) return false;
    bool bMirrored = false, bGeometry = false;
    for (const auto& Instance : InstancedOutput.MeshInstances)
    {
        const auto& Node = Instanced.Nodes[Instance.SourceNodeIndex];
        bMirrored |= (Instance.GeometricTransform * Node.GlobalTransform).ToMatrixWithScale().Determinant() < 0;
        bGeometry |= !Instance.GeometricTransform.Equals(FTransform::Identity);
        CarlaUfbxMesh::FStaticPayload Payload;
        if (!CarlaUfbxMesh::FetchStaticPayload(Instanced, Instance.PayloadKey,
            Instance.GeometricTransform * Node.GlobalTransform, Payload, Checks.Error)
            || Payload.Mesh.IsEmpty()) return false;
    }
    return Checks.Require(TEXT("real shared mesh mirrored instance and geometry transforms"),
        Instanced.Meshes.Num() == 1 && InstancedOutput.MeshInstances.Num() == 2 && bMirrored && bGeometry
        && InstancedOutput.MeshInstances[0].MaterialUids != InstancedOutput.MeshInstances[1].MaterialUids);
}
}

int32 RunLegacyHierarchyChecks(const FString& Input, const FString& ResultDir, const FString& OutputPath)
{
    FChecks Checks;
    CarlaUfbxLegacy::FSceneHierarchy Output;
    bool Success = !FPaths::FileExists(OutputPath) && IFileManager::Get().MakeDirectory(*ResultDir, true)
        && NativeChecks(Input, ResultDir, Checks, Output);
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("stage"), Stage);
    Report->SetStringField(TEXT("scope"), Scope);
    Report->SetStringField(TEXT("status"), Success ? TEXT("PASS") : TEXT("FAIL"));
    Report->SetStringField(TEXT("error"), Checks.Error);
    Report->SetStringField(TEXT("source_sha256"), Output.SourceSha256);
    Report->SetArrayField(TEXT("checks"), Checks.Names);
    Report->SetNumberField(TEXT("self_tests"), Checks.Names.Num());
    TArray<TSharedPtr<FJsonValue>> Nodes;
    for (const auto& Entry : Output.ActorNodes)
    {
        const auto& Node = Output.SourceNodes[Entry.SourceIndex];
        TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
        Item->SetStringField(TEXT("id"), LexToString(Node.UniqueId));
        Item->SetStringField(TEXT("parent_id"), LexToString(Node.ParentUniqueId));
        Item->SetStringField(TEXT("attribute_id"), LexToString(Node.AttributeUniqueId));
        Item->SetStringField(TEXT("name"), Node.ObjectName);
        Item->SetNumberField(TEXT("source_index"), Entry.SourceIndex);
        Item->SetNumberField(TEXT("parent_index"), Entry.ParentIndex);
        Item->SetBoolField(TEXT("import"), Entry.bImportNode);
        Nodes.Add(MakeShared<FJsonValueObject>(Item));
    }
    Report->SetArrayField(TEXT("nodes"), Nodes);
    Report->SetNumberField(TEXT("instances"), Output.MeshInstances.Num());
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    if (FPaths::FileExists(OutputPath) || !FFileHelper::SaveStringToFile(Text, *OutputPath)) return 3;
    return Success ? 0 : 2;
}
