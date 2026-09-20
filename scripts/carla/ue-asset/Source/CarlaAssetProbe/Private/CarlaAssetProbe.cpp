#include "CoreMinimal.h"
#include "CarlaUfbxLegacyScene.h"
#include "Engine/StaticMesh.h"
#include "StaticMeshResources.h"
#include "StaticMeshAttributes.h"
#include "RenderingThread.h"
#include "Dom/JsonObject.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "Misc/ScopeExit.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/GCObject.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "UObject/StrongObjectPtr.h"
#include "UObject/UObjectGlobals.h"
#include "RequiredProgramMainCPPInclude.h"

IMPLEMENT_APPLICATION(CarlaAssetProbe, "CarlaAssetProbe")

static_assert(WITH_ENGINE && !WITH_EDITOR && WITH_EDITORONLY_DATA,
    "This Program saves and reloads uncooked assets without Editor code");

namespace
{
bool CheckBuffers(const FMeshDescription& Source, const UStaticMesh& Mesh, FString& Error)
{
    const FStaticMeshRenderData* Data = Mesh.GetRenderData();
    if (!Data || Data->LODResources.Num() != 1)
    { Error = TEXT("Missing saved UStaticMesh LOD resources"); return false; }
    const auto& LOD = Data->LODResources[0];
    if (LOD.GetNumTriangles() != uint32(Source.Triangles().Num())
        || LOD.VertexBuffers.PositionVertexBuffer.GetNumVertices() != uint32(Source.VertexInstances().Num())
        || LOD.IndexBuffer.GetNumIndices() != uint32(Source.Triangles().Num() * 3))
    { Error = TEXT("Saved static mesh CPU buffer counts disagree"); return false; }
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
        if (!Found) { Error = TEXT("Saved static mesh position or UV mismatch"); return false; }
    }
    for (uint32 Index = 0; Index < LOD.IndexBuffer.GetNumIndices(); ++Index)
        if (LOD.IndexBuffer.GetIndex(Index) >= LOD.GetNumVertices())
        { Error = TEXT("Out-of-range saved static mesh index"); return false; }
    const FBox Bounds = Source.ComputeBoundingBox();
    if (!FVector(Data->Bounds.Origin).Equals(Bounds.GetCenter(), 1e-5)
        || !FVector(Data->Bounds.BoxExtent).Equals(Bounds.GetExtent(), 1e-5))
    { Error = TEXT("Saved static mesh bounds mismatch"); return false; }
    return true;
}

bool BuildObject(const CarlaUfbxMesh::FStaticScene& Scene,
    const CarlaUfbxLegacy::FMeshInstance& Instance, UPackage* Outer, FName Name,
    TStrongObjectPtr<UStaticMesh>& Output, FString& Error)
{
    CarlaUfbxMesh::FStaticPayload Payload;
    if (!Scene.Nodes.IsValidIndex(Instance.SourceNodeIndex)
        || !CarlaUfbxMesh::FetchStaticPayload(Scene, Instance.PayloadKey,
            Instance.GeometricTransform * Scene.Nodes[Instance.SourceNodeIndex].GlobalTransform, Payload, Error))
        return false;
    TStrongObjectPtr<UStaticMesh> Candidate(NewObject<UStaticMesh>(Outer, Name,
        RF_Public | RF_Standalone | RF_Transactional));
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
    { Error = TEXT("Saved-path UStaticMesh fast build failed"); return false; }
    FlushRenderingCommands();
    if (!CheckBuffers(Payload.Mesh, *Candidate, Error)) return false;
    Output = MoveTemp(Candidate);
    return true;
}

bool SaveObject(UStaticMesh& Mesh, const FString& PackageName, const FString& Filename,
    FGuid& OutputGuid, FString& Error)
{
    UPackage* Package = Cast<UPackage>(Mesh.GetOuter());
    Package->MarkAsFullyLoaded();
    Mesh.ClearFlags(RF_Transactional);
    FSavePackageArgs Args;
    Args.TopLevelFlags = RF_Public | RF_Standalone;
    Args.bSlowTask = false;
    if (!UPackage::SavePackage(Package, &Mesh, *Filename, Args))
    { Error = TEXT("SavePackage failed"); return false; }
    OutputGuid = Package->GetGuid();
    return OutputGuid.IsValid();
}

bool RemoveFromRootAndCollect(UPackage* Package, FString& Error)
{
    Package->ClearFlags(RF_Standalone);
    ForEachObjectWithPackage(Package, [](UObject* Object)
    {
        Object->ClearFlags(RF_Standalone);
        return true;
    });
    {
        TGuardValue<bool> GuardIsInitialLoad(GIsInitialLoad, false);
        CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS);
    }
    if (FindObject<UObject>(nullptr, *Package->GetName()))
    { Error = TEXT("Package remained resident before reload"); return false; }
    return true;
}
}

INT32_MAIN_INT32_ARGC_TCHAR_ARGV()
{
    FTaskTagScope Scope(ETaskTag::EGameThread);
    ON_SCOPE_EXIT
    {
        RequestEngineExit(TEXT("CarlaAssetProbe finished"));
        FEngineLoop::AppPreExit();
        FModuleManager::Get().UnloadModulesAtShutdown();
        FEngineLoop::AppExit();
    };
    const int32 InitResult = GEngineLoop.PreInit(ArgC, ArgV);
    if (InitResult) return InitResult;
    FString Input, Output, ContentRoot, Error;
    if (!FParse::Value(FCommandLine::Get(), TEXT("input="), Input)
        || !FParse::Value(FCommandLine::Get(), TEXT("output="), Output)
        || !FParse::Value(FCommandLine::Get(), TEXT("content-root="), ContentRoot)
        || FPaths::FileExists(Output)) return 64;
    CarlaUfbxMesh::FStaticScene Scene;
    CarlaUfbxLegacy::FSceneHierarchy Hierarchy;
    bool Success = CarlaUfbxMesh::ImportScene(Input, Scene, Error)
        && CarlaUfbxLegacy::BuildSceneHierarchy(Scene, Hierarchy, Error);
    int32 SavedAssets = 0, ReloadedAssets = 0, RebuiltObjects = 0;
    TArray<FString> PackageNames;
    TArray<FGuid> SavedGuids;
    if (Success)
    {
        FPackageName::RegisterMountPoint(TEXT("/CarlaAssets/"), ContentRoot + TEXT("/"));
    }
    if (Success)
    {
        for (int32 Index = 0; Index < Hierarchy.MeshInstances.Num(); ++Index)
        {
            const FString AssetName = FString::Printf(TEXT("StaticMesh%d"), Index);
            const FString PackageName = TEXT("/CarlaAssets/") + AssetName;
            UPackage* Package = CreatePackage(*PackageName);
            TStrongObjectPtr<UStaticMesh> Mesh;
            FGuid SavedGuid;
            if (!Package || !BuildObject(Scene, Hierarchy.MeshInstances[Index], Package, FName(*AssetName), Mesh, Error)
                || !SaveObject(*Mesh, PackageName, FPaths::Combine(ContentRoot, AssetName + TEXT(".uasset")), SavedGuid, Error))
            { Success = false; break; }
            ++SavedAssets;
            PackageNames.Add(PackageName);
            SavedGuids.Add(SavedGuid);
        }
    }
    if (Success)
    {
        for (const FString& PackageName : PackageNames)
        {
            UPackage* Package = FindObject<UPackage>(nullptr, *PackageName);
            if (!Package || !RemoveFromRootAndCollect(Package, Error)) { Success = false; break; }
        }
    }
    if (Success)
    {
        for (int32 Index = 0; Index < PackageNames.Num(); ++Index)
        {
            UPackage* LoadedPackage = LoadPackage(nullptr, *PackageNames[Index], LOAD_None);
            const FString AssetName = FString::Printf(TEXT("StaticMesh%d"), Index);
            UStaticMesh* Mesh = LoadedPackage ? FindObject<UStaticMesh>(LoadedPackage, *AssetName) : nullptr;
            if (!Mesh)
            { Error = TEXT("Reloaded UStaticMesh not found"); Success = false; break; }
            CarlaUfbxMesh::FStaticPayload Payload;
            const auto& Instance = Hierarchy.MeshInstances[Index];
            if (!CarlaUfbxMesh::FetchStaticPayload(Scene, Instance.PayloadKey,
                Instance.GeometricTransform * Scene.Nodes[Instance.SourceNodeIndex].GlobalTransform, Payload, Error)
                || !CheckBuffers(Payload.Mesh, *Mesh, Error)
                || LoadedPackage->GetGuid() != SavedGuids[Index])
            { Success = false; break; }
            ++ReloadedAssets;
        }
    }
    if (Success)
    {
        for (int32 Index = 0; Index < Hierarchy.MeshInstances.Num(); ++Index)
        {
            const FString AssetName = FString::Printf(TEXT("Rebuilt%d"), Index);
            UPackage* Package = CreatePackage(*(TEXT("/CarlaAssets/") + AssetName));
            TStrongObjectPtr<UStaticMesh> Mesh;
            if (!Package || !BuildObject(Scene, Hierarchy.MeshInstances[Index], Package, FName(*AssetName), Mesh, Error))
            { Success = false; break; }
            ++RebuiltObjects;
        }
    }
    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("stage"), TEXT("ue-ufbx-asset-roundtrip"));
    Report->SetStringField(TEXT("scope"), TEXT("Saved and reloaded uncooked UStaticMesh assets; not Editor reimport, Cook or GPU rendering"));
    Report->SetStringField(TEXT("status"), Success ? TEXT("PASS") : TEXT("FAIL"));
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("source_sha256"), Scene.SourceSha256);
    Report->SetNumberField(TEXT("instances"), Hierarchy.MeshInstances.Num());
    Report->SetNumberField(TEXT("saved_assets"), SavedAssets);
    Report->SetNumberField(TEXT("reloaded_assets"), ReloadedAssets);
    Report->SetNumberField(TEXT("rebuilt_objects"), RebuiltObjects);
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *Output)) return 3;
    return Success ? 0 : 2;
}
