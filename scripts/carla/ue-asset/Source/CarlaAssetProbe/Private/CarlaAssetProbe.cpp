#include "CoreMinimal.h"
#include "CarlaUfbxLegacyScene.h"
#include "Engine/StaticMesh.h"
#include "Materials/Material.h"
#include "MaterialDomain.h"
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
#include <unistd.h>
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
    {
        Error = FString::Printf(TEXT("Missing saved UStaticMesh LOD resources: renderdata=%p lods=%d streaming=%s"),
            Data, Data ? Data->LODResources.Num() : -1,
            Mesh.GetOutermost() && Mesh.GetOutermost()->HasAnyPackageFlags(PKG_IsSaving) ? TEXT("saving") : TEXT("idle"));
        return false;
    }
    const auto& LOD = Data->LODResources[0];
    if (LOD.GetNumTriangles() != uint32(Source.Triangles().Num())
        || LOD.VertexBuffers.PositionVertexBuffer.GetNumVertices() != uint32(Source.VertexInstances().Num())
        || LOD.IndexBuffer.GetNumIndices() != uint32(Source.Triangles().Num() * 3))
    { Error = TEXT("Saved static mesh CPU buffer counts disagree"); return false; }
    const FStaticMeshConstAttributes Attributes(Source);
    TSet<int32> Matched;
    FString Diagnose;
    for (uint32 Index = 0; Index < LOD.VertexBuffers.PositionVertexBuffer.GetNumVertices(); ++Index)
    {
        const FVector3f Position = LOD.VertexBuffers.PositionVertexBuffer.VertexPosition(Index);
        float BestDelta = MAX_flt;
        FVertexInstanceID BestInstance;
        bool HasBest = false;
        bool Found = false;
        for (FVertexInstanceID Instance : Source.VertexInstances().GetElementIDs())
        {
            if (Matched.Contains(Instance.GetValue())) continue;
            const FVector3f Expected = Attributes.GetVertexPositions()[Source.GetVertexInstanceVertex(Instance)];
            const float Delta = (Position - Expected).GetAbsMax();
            if (Delta < BestDelta) { BestDelta = Delta; BestInstance = Instance; HasBest = true; }
            if (Delta > 1e-5f) continue;
            const int32 BuiltChannels = LOD.VertexBuffers.StaticMeshVertexBuffer.GetNumTexCoords();
            const int32 SourceChannels = Attributes.GetVertexInstanceUVs().GetNumChannels();
            bool UVsMatch = BuiltChannels == SourceChannels;
            for (int32 Channel = 0; UVsMatch && Channel < SourceChannels; ++Channel)
            {
                const FVector2f BuiltUV = LOD.VertexBuffers.StaticMeshVertexBuffer.GetVertexUV(Index, Channel);
                const FVector2f ExpectedUV = Attributes.GetVertexInstanceUVs().Get(Instance, Channel);
                if (!BuiltUV.Equals(ExpectedUV, 1e-4f))
                {
                    UVsMatch = false;
                    if (Diagnose.IsEmpty())
                        Diagnose = FString::Printf(TEXT("uv_ch%d built=(%f,%f) expect=(%f,%f)"),
                            Channel, BuiltUV.X, BuiltUV.Y, ExpectedUV.X, ExpectedUV.Y);
                }
            }
            if (!UVsMatch) continue;
            Matched.Add(Instance.GetValue()); Found = true; break;
        }
        if (!Found)
        {
            const FVector3f Expected = HasBest
                ? Attributes.GetVertexPositions()[Source.GetVertexInstanceVertex(BestInstance)]
                : FVector3f::ZeroVector;
            Error = FString::Printf(TEXT(
                "Saved static mesh position or UV mismatch at vertex %u: built=(%f,%f,%f) "
                "nearest expect=(%f,%f,%f) delta=%g built_uv_ch=%d source_uv_ch=%d %s"),
                Index, Position.X, Position.Y, Position.Z,
                Expected.X, Expected.Y, Expected.Z, BestDelta,
                LOD.VertexBuffers.StaticMeshVertexBuffer.GetNumTexCoords(),
                Attributes.GetVertexInstanceUVs().GetNumChannels(), *Diagnose);
            return false;
        }
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
        Candidate->GetStaticMaterials().Add(FStaticMaterial(UMaterial::GetDefaultMaterial(MD_Surface),
            Attributes.GetPolygonGroupMaterialSlotNames()[Group]));
    UStaticMesh::FBuildMeshDescriptionsParams Params;
    Params.bFastBuild = true;
    // Commit the MeshDescription into the source model: uncooked packages persist
    // their geometry through the source model bulk data, not through cooked CPU
    // render buffers. Without this the saved asset has no recoverable geometry.
    Params.bCommitMeshDescription = true;
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

// Verify that a MeshDescription recovered from a persisted asset still matches the
// geometry the FBX importer produced. Counts, every vertex instance position and
// every UV channel are compared; the comparison is order-insensitive over vertex
// instances because serialization may renumber element IDs.
bool CheckMeshDescription(const FMeshDescription& Source, const FMeshDescription& Recovered, FString& Error)
{
    if (Recovered.Triangles().Num() != Source.Triangles().Num()
        || Recovered.VertexInstances().Num() != Source.VertexInstances().Num()
        || Recovered.Vertices().Num() != Source.Vertices().Num())
    { Error = TEXT("Recovered static mesh description counts disagree"); return false; }
    const FStaticMeshConstAttributes SourceAttributes(Source);
    const FStaticMeshConstAttributes RecoveredAttributes(Recovered);
    if (RecoveredAttributes.GetVertexInstanceUVs().GetNumChannels()
        != SourceAttributes.GetVertexInstanceUVs().GetNumChannels())
    { Error = TEXT("Recovered static mesh UV channel count mismatch"); return false; }
    TSet<int32> Matched;
    for (FVertexInstanceID Instance : Source.VertexInstances().GetElementIDs())
    {
        const FVector3f Position = SourceAttributes.GetVertexPositions()[Source.GetVertexInstanceVertex(Instance)];
        bool Found = false;
        for (FVertexInstanceID Candidate : Recovered.VertexInstances().GetElementIDs())
        {
            if (Matched.Contains(Candidate.GetValue())) continue;
            const FVector3f RecoveredPosition =
                RecoveredAttributes.GetVertexPositions()[Recovered.GetVertexInstanceVertex(Candidate)];
            if (!Position.Equals(RecoveredPosition, 1e-5f)) continue;
            bool UVsMatch = true;
            for (int32 Channel = 0; UVsMatch && Channel < SourceAttributes.GetVertexInstanceUVs().GetNumChannels(); ++Channel)
                UVsMatch &= RecoveredAttributes.GetVertexInstanceUVs().Get(Candidate, Channel)
                    .Equals(SourceAttributes.GetVertexInstanceUVs().Get(Instance, Channel), 1e-4f);
            if (!UVsMatch) continue;
            Matched.Add(Candidate.GetValue()); Found = true; break;
        }
        if (!Found)
        { Error = TEXT("Recovered static mesh position or UV mismatch"); return false; }
    }
    return true;
}

bool SaveObject(UStaticMesh& Mesh, const FString& PackageName, const FString& Filename,
    FGuid& OutputGuid, FString& Error)
{
    UPackage* Package = Cast<UPackage>(Mesh.GetOuter());
    Package->MarkAsFullyLoaded();
    // Preallocate the package MetaData: UPackage::GetMetaData() otherwise performs a
    // StaticFindObjectFast lookup during SavePackage, which is illegal mid-save in a
    // non-Editor Program target.
    if (Package->GetMetaData() == nullptr)
    { Error = TEXT("Failed to preallocate package MetaData"); return false; }
    Mesh.ClearFlags(RF_Transactional);
    UE_LOG(LogTemp, Log, TEXT("CarlaAssetProbe: saving %s -> %s"), *PackageName, *Filename);
    FSavePackageArgs Args;
    Args.TopLevelFlags = RF_Public | RF_Standalone;
    Args.bSlowTask = false;
    if (!UPackage::SavePackage(Package, &Mesh, *Filename, Args))
    { Error = TEXT("SavePackage failed"); return false; }
    OutputGuid = Package->GetGuid();
    return OutputGuid.IsValid();
}

}

INT32_MAIN_INT32_ARGC_TCHAR_ARGV()
{
    FTaskTagScope Scope(ETaskTag::EGameThread);
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
        // Collect every saved package in one pass. CollectGarbage is transitive: the
        // first collect can already free the remaining packages if nothing roots them,
        // so iterating one package per collect would observe them as missing.
        for (const FString& PackageName : PackageNames)
        {
            UPackage* Package = FindObject<UPackage>(nullptr, *PackageName);
            if (!Package)
            {
                Error = FString::Printf(TEXT("Package %s not found after save"), *PackageName);
                Success = false; break;
            }
            Package->ClearFlags(RF_Standalone);
            ForEachObjectWithPackage(Package, [](UObject* Object)
            {
                Object->ClearFlags(RF_Standalone);
                return true;
            });
        }
        if (Success)
        {
            UE_LOG(LogTemp, Log, TEXT("CarlaAssetProbe: collecting %d packages"), PackageNames.Num());
            TGuardValue<bool> GuardIsInitialLoad(GIsInitialLoad, false);
            CollectGarbage(GARBAGE_COLLECTION_KEEPFLAGS);
            for (const FString& PackageName : PackageNames)
            {
                if (FindObject<UObject>(nullptr, *PackageName))
                { Error = FString::Printf(TEXT("Package %s remained resident before reload"), *PackageName); Success = false; break; }
            }
        }
    }
    if (Success)
    {
        for (int32 Index = 0; Index < PackageNames.Num(); ++Index)
        {
            UPackage* LoadedPackage = LoadPackage(nullptr, *PackageNames[Index], LOAD_None);
            const FString AssetName = FString::Printf(TEXT("StaticMesh%d"), Index);
            UStaticMesh* Mesh = LoadedPackage ? FindObject<UStaticMesh>(LoadedPackage, *AssetName) : nullptr;
            UE_LOG(LogTemp, Log, TEXT("CarlaAssetProbe: reload %s package=%p mesh=%p renderdata=%p lods=%d allowcpu=%d"),
                *PackageNames[Index], LoadedPackage, Mesh,
                Mesh ? Mesh->GetRenderData() : nullptr,
                Mesh && Mesh->GetRenderData() ? Mesh->GetRenderData()->LODResources.Num() : -1,
                Mesh ? (int32)Mesh->bAllowCPUAccess : -1);
            if (!Mesh)
            { Error = TEXT("Reloaded UStaticMesh not found"); Success = false; break; }
            CarlaUfbxMesh::FStaticPayload Payload;
            const auto& Instance = Hierarchy.MeshInstances[Index];
            if (!CarlaUfbxMesh::FetchStaticPayload(Scene, Instance.PayloadKey,
                Instance.GeometricTransform * Scene.Nodes[Instance.SourceNodeIndex].GlobalTransform, Payload, Error))
            { Success = false; break; }
            // The reloaded asset is uncooked: its CPU render buffers are empty by
            // design (UStaticMesh::Serialize only inlines render data for cooked
            // assets). The round-trip proof is the persisted MeshDescription in the
            // source model: recover it, compare it against the FBX source geometry,
            // then rebuild render data from it and verify the rebuilt buffers too.
            if (Mesh->GetNumSourceModels() < 1)
            { Error = TEXT("Reloaded UStaticMesh has no source model"); Success = false; break; }
            FMeshDescription Recovered;
            if (!Mesh->GetSourceModel(0).LoadMeshDescription(Recovered))
            { Error = TEXT("Reloaded UStaticMesh source model has no mesh description"); Success = false; break; }
            if (!CheckMeshDescription(Payload.Mesh, Recovered, Error)
                || LoadedPackage->GetGuid() != SavedGuids[Index])
            { Success = false; break; }
            UStaticMesh::FBuildMeshDescriptionsParams RebuildParams;
            RebuildParams.bFastBuild = true;
            RebuildParams.bCommitMeshDescription = false;
            RebuildParams.bMarkPackageDirty = false;
            RebuildParams.bAllowCpuAccess = true;
            RebuildParams.bBuildSimpleCollision = false;
            if (!Mesh->BuildFromMeshDescriptions({&Recovered}, RebuildParams))
            { Error = TEXT("Reloaded UStaticMesh fast rebuild failed"); Success = false; break; }
            FlushRenderingCommands();
            if (!CheckBuffers(Recovered, *Mesh, Error))
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
    // The report is persisted; skip the engine shutdown path entirely. Several
    // lazy singletons in the runtime assume editor teardown ordering and assert
    // in this stripped Program context, which would otherwise mask a successful
    // round-trip behind a nonzero exit code.
    fflush(stdout);
    fflush(stderr);
    _exit(Success ? 0 : 2);
}
