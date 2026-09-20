#include "CoreMinimal.h"
#include "CarlaUfbxLegacyScene.h"
#include "Engine/StaticMesh.h"
#include "StaticMeshResources.h"
#include "StaticMeshAttributes.h"
#include "RenderingThread.h"
#include "Dom/JsonObject.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/StrongObjectPtr.h"
#include "RequiredProgramMainCPPInclude.h"

IMPLEMENT_APPLICATION(CarlaStaticMeshProbe, "CarlaStaticMeshProbe")

static_assert(WITH_ENGINE && !WITH_EDITOR && WITH_EDITORONLY_DATA,
    "This Program retains source metadata for loading uncooked resources, not Editor code");

namespace
{
bool CheckBuffers(const FMeshDescription& Source, const UStaticMesh& Mesh, FString& Error)
{
    const FStaticMeshRenderData* Data = Mesh.GetRenderData();
    if (!Data || Data->LODResources.Num() != 1)
    { Error = TEXT("Missing actual UStaticMesh LOD resources"); return false; }
    const auto& LOD = Data->LODResources[0];
    if (LOD.GetNumTriangles() != uint32(Source.Triangles().Num())
        || LOD.VertexBuffers.PositionVertexBuffer.GetNumVertices() != uint32(Source.VertexInstances().Num())
        || LOD.IndexBuffer.GetNumIndices() != uint32(Source.Triangles().Num() * 3))
    { Error = TEXT("Static mesh CPU buffer counts disagree"); return false; }
    const FStaticMeshConstAttributes Attributes(Source);
    TSet<int32> Matched;
    for (uint32 Index = 0; Index < LOD.VertexBuffers.PositionVertexBuffer.GetNumVertices(); ++Index)
    {
        const FVector3f Position = LOD.VertexBuffers.PositionVertexBuffer.VertexPosition(Index);
        bool Found = false;
        for (FVertexInstanceID Instance : Source.VertexInstances().GetElementIDs())
        {
            if (Matched.Contains(Instance.GetValue())) continue;
            const FVector3f Expected = Attributes.GetVertexPositions()[Source.GetVertexInstanceVertex(Instance)];
            if (!Position.Equals(Expected, 1e-5f)) continue;
            bool UVsMatch = LOD.VertexBuffers.StaticMeshVertexBuffer.GetNumTexCoords()
                == uint32(Attributes.GetVertexInstanceUVs().GetNumChannels());
            for (int32 Channel = 0; UVsMatch && Channel < Attributes.GetVertexInstanceUVs().GetNumChannels(); ++Channel)
                UVsMatch &= LOD.VertexBuffers.StaticMeshVertexBuffer.GetVertexUV(Index, Channel)
                    .Equals(Attributes.GetVertexInstanceUVs().Get(Instance, Channel), 1e-4f);
            if (!UVsMatch) continue;
            Matched.Add(Instance.GetValue()); Found = true; break;
        }
        if (!Found) { Error = TEXT("Static mesh CPU position or UV mismatch"); return false; }
    }
    for (uint32 Index = 0; Index < LOD.IndexBuffer.GetNumIndices(); ++Index)
        if (LOD.IndexBuffer.GetIndex(Index) >= LOD.GetNumVertices())
        { Error = TEXT("Out-of-range static mesh index"); return false; }
    const FBox Bounds = Source.ComputeBoundingBox();
    if (!FVector(Data->Bounds.Origin).Equals(Bounds.GetCenter(), 1e-5)
        || !FVector(Data->Bounds.BoxExtent).Equals(Bounds.GetExtent(), 1e-5))
    { Error = TEXT("Static mesh bounds mismatch"); return false; }
    return true;
}

bool BuildObject(const CarlaUfbxMesh::FStaticScene& Scene, const CarlaUfbxLegacy::FMeshInstance& Instance,
    TStrongObjectPtr<UStaticMesh>& Output, FString& Error)
{
    CarlaUfbxMesh::FStaticPayload Payload;
    if (!Scene.Nodes.IsValidIndex(Instance.SourceNodeIndex)
        || !CarlaUfbxMesh::FetchStaticPayload(Scene, Instance.PayloadKey,
            Instance.GeometricTransform * Scene.Nodes[Instance.SourceNodeIndex].GlobalTransform, Payload, Error)) return false;
    TStrongObjectPtr<UStaticMesh> Candidate(NewObject<UStaticMesh>(GetTransientPackage(), NAME_None, RF_Transient));
    Candidate->bAllowCPUAccess = true;
    Candidate->bSupportRayTracing = false;
    const FStaticMeshConstAttributes Attributes(Payload.Mesh);
    for (FPolygonGroupID Group : Payload.Mesh.PolygonGroups().GetElementIDs())
        Candidate->GetStaticMaterials().Add(FStaticMaterial(nullptr,
            Attributes.GetPolygonGroupMaterialSlotNames()[Group]));
    UStaticMesh::FBuildMeshDescriptionsParams Params;
    Params.bFastBuild = true;
    Params.bCommitMeshDescription = false;
    Params.bMarkPackageDirty = false;
    Params.bAllowCpuAccess = true;
    Params.bBuildSimpleCollision = false;
    UStaticMesh::FBuildMeshDescriptionsLODParams LODParams;
    LODParams.bUseFullPrecisionUVs = true;
    LODParams.bUseHighPrecisionTangentBasis = true;
    Params.PerLODOverrides.Add(LODParams);
    if (!Candidate->BuildFromMeshDescriptions({&Payload.Mesh}, Params))
    { Error = TEXT("UStaticMesh fast build failed"); return false; }
    FlushRenderingCommands();
    if (!CheckBuffers(Payload.Mesh, *Candidate, Error)) return false;
    Output = MoveTemp(Candidate);
    return true;
}
}

INT32_MAIN_INT32_ARGC_TCHAR_ARGV()
{
    FTaskTagScope Scope(ETaskTag::EGameThread);
    ON_SCOPE_EXIT
    {
        RequestEngineExit(TEXT("CarlaStaticMeshProbe finished"));
        FEngineLoop::AppPreExit();
        FModuleManager::Get().UnloadModulesAtShutdown();
        FEngineLoop::AppExit();
    };
    const int32 InitResult = GEngineLoop.PreInit(ArgC, ArgV);
    if (InitResult) return InitResult;
    FString Input, Output, Error;
    if (!FParse::Value(FCommandLine::Get(), TEXT("input="), Input)
        || !FParse::Value(FCommandLine::Get(), TEXT("output="), Output) || FPaths::FileExists(Output)) return 64;
    CarlaUfbxMesh::FStaticScene Scene;
    CarlaUfbxLegacy::FSceneHierarchy Hierarchy;
    bool Success = CarlaUfbxMesh::ImportScene(Input, Scene, Error)
        && CarlaUfbxLegacy::BuildSceneHierarchy(Scene, Hierarchy, Error);
    int32 Built = 0, Rejected = 0;
    if (Success)
    {
        for (const auto& Instance : Hierarchy.MeshInstances)
        {
            TStrongObjectPtr<UStaticMesh> Mesh;
            if (!BuildObject(Scene, Instance, Mesh, Error)) { Success = false; break; }
            ++Built;
            const UStaticMesh* Original = Mesh.Get();
            auto Bad = Instance; Bad.PayloadKey = TEXT("invalid-key");
            FString Rejection;
            if (BuildObject(Scene, Bad, Mesh, Rejection) || Rejection.IsEmpty() || Mesh.Get() != Original)
            { Error = TEXT("Failed build changed the existing object"); Success = false; break; }
            ++Rejected;
            Mesh->ReleaseResources();
            FlushRenderingCommands();
        }
    }
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("stage"), TEXT("ue-ufbx-runtime-staticmesh"));
    Report->SetStringField(TEXT("scope"), TEXT("Transient UStaticMesh fast-build CPU buffers; not saved assets, Editor/Cook or GPU rendering"));
    Report->SetStringField(TEXT("status"), Success ? TEXT("PASS") : TEXT("FAIL"));
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("source_sha256"), Scene.SourceSha256);
    Report->SetNumberField(TEXT("instances"), Hierarchy.MeshInstances.Num());
    Report->SetNumberField(TEXT("built_objects"), Built);
    Report->SetNumberField(TEXT("rejected_queries"), Rejected);
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *Output)) return 3;
    return Success ? 0 : 2;
}
