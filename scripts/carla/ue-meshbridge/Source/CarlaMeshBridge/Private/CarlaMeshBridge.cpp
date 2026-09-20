#include "CoreMinimal.h"
#include "CarlaAssimpMesh.h"
#if CARLA_WITH_UFBX
#include "CarlaUfbxMesh.h"
#endif
#include "MeshDescription.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/ScopeExit.h"
#include "RequiredProgramMainCPPInclude.h"

IMPLEMENT_APPLICATION(CarlaMeshBridge, "CarlaMeshBridge")

INT32_MAIN_INT32_ARGC_TCHAR_ARGV()
{
    FTaskTagScope Scope(ETaskTag::EGameThread);
    ON_SCOPE_EXIT
    {
        RequestEngineExit(TEXT("CarlaMeshBridge finished"));
        FEngineLoop::AppPreExit();
        FModuleManager::Get().UnloadModulesAtShutdown();
        FEngineLoop::AppExit();
    };
    const int32 InitResult = GEngineLoop.PreInit(ArgC, ArgV);
    if (InitResult) return InitResult;
    FString Input, Output, Error;
    FParse::Value(FCommandLine::Get(), TEXT("input="), Input);
    if (!FParse::Value(FCommandLine::Get(), TEXT("output="), Output)) return 64;
    const bool bSelfTest = FParse::Param(FCommandLine::Get(), TEXT("self-test"));
    FString Backend = TEXT("assimp");
    FParse::Value(FCommandLine::Get(), TEXT("backend="), Backend);
    const bool bUfbx = Backend == TEXT("ufbx");
    int32 Tests = 0;
    bool bSuccess = false;
    FMeshDescription Mesh;
    int64 Bytes = 0;
#if CARLA_WITH_UFBX
    TArray<CarlaUfbxMesh::FCoordinateCheck> CoordinateChecks;
#endif
    if (bUfbx)
    {
#if CARLA_WITH_UFBX
        if (bSelfTest) bSuccess = CarlaUfbxMesh::RunSelfTests(Tests, Error, CoordinateChecks);
        else if (!Input.IsEmpty())
        {
            FMeshDescription Candidate;
            bSuccess = CarlaUfbxMesh::ImportStatic(Input, Candidate, Error)
                && CarlaAssimpMesh::CheckMemoryRoundTrip(Candidate, Bytes, Error);
            if (bSuccess) Mesh = MoveTemp(Candidate);
            else Bytes = 0;
        }
        else Error = TEXT("Expected -self-test or -input=FILE");
#else
        Error = TEXT("ufbx backend was not built; CARLA_UFBX_INSTALL is required");
#endif
    }
    else if (Backend != TEXT("assimp")) Error = TEXT("Unknown backend");
    else if (bSelfTest) bSuccess = CarlaAssimpMesh::RunSelfTests(Tests, Error);
    else if (!Input.IsEmpty())
        bSuccess = CarlaAssimpMesh::ImportStatic(Input, Mesh, Error) && CarlaAssimpMesh::CheckMemoryRoundTrip(Mesh, Bytes, Error);
    else Error = TEXT("Expected -self-test or -input=FILE");

    TSharedRef<FJsonObject> Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("stage"), bUfbx ? TEXT("ue-ufbx-meshdescription-static") : TEXT("ue-meshdescription-static"));
    Report->SetStringField(TEXT("scope"), bUfbx
        ? TEXT("ufbx to UE FMeshDescription in-memory bridge; dual backend, not SDK replacement, UStaticMesh, Editor or Cook")
        : TEXT("UE FMeshDescription in-memory bridge; not UStaticMesh, Editor or Cook"));
    Report->SetStringField(TEXT("coordinate_policy"), bUfbx
        ? TEXT("ufbx right +Y/up +Z/front -X, centimeters, UV V flip")
        : TEXT("FBX Front/Coord/Up to UE X/Y/Z, centimeters, UV V flip"));
    if (bUfbx)
    {
        Report->SetStringField(TEXT("backend"), TEXT("ufbx"));
        Report->SetStringField(TEXT("equivalence_to_assimp"), TEXT("not asserted; front-vs-forward policies differ"));
#if CARLA_WITH_UFBX
        if (bSelfTest)
        {
            TArray<TSharedPtr<FJsonValue>> Details;
            for (const auto& Item : CoordinateChecks)
            {
                TSharedRef<FJsonObject> Detail = MakeShared<FJsonObject>();
                Detail->SetNumberField(TEXT("source_front_sign"), Item.SourceFrontSign);
                Detail->SetBoolField(TEXT("mirrored_instance"), Item.bMirroredInstance);
                Detail->SetBoolField(TEXT("ufbx_reversed_winding"), Item.bReversedWinding);
                Detail->SetBoolField(TEXT("ue_facing_matches_normals"), Item.bFacingMatchesNormals);
                TArray<TSharedPtr<FJsonValue>> Bounds, Normal;
                for (double V : {Item.Bounds.Min.X, Item.Bounds.Min.Y, Item.Bounds.Min.Z,
                    Item.Bounds.Max.X, Item.Bounds.Max.Y, Item.Bounds.Max.Z})
                    Bounds.Add(MakeShared<FJsonValueNumber>(V));
                for (float V : {Item.Normal.X, Item.Normal.Y, Item.Normal.Z})
                    Normal.Add(MakeShared<FJsonValueNumber>(V));
                Detail->SetArrayField(TEXT("bounds_cm"), Bounds);
                Detail->SetArrayField(TEXT("normal_world"), Normal);
                Details.Add(MakeShared<FJsonValueObject>(Detail));
            }
            Report->SetArrayField(TEXT("coordinate_checks"), Details);
        }
#endif
    }
    Report->SetStringField(TEXT("status"), bSuccess ? TEXT("PASS") : TEXT("FAIL"));
    Report->SetStringField(TEXT("error"), Error);
    Report->SetStringField(TEXT("input"), Input);
    Report->SetNumberField(TEXT("self_tests"), Tests);
    Report->SetNumberField(TEXT("vertices"), Mesh.Vertices().Num());
    Report->SetNumberField(TEXT("triangles"), Mesh.Triangles().Num());
    Report->SetNumberField(TEXT("material_slots"), Mesh.PolygonGroups().Num());
    Report->SetNumberField(TEXT("serialized_bytes"), Bytes);
    if (!Mesh.IsEmpty())
    {
        const FBox Bounds = Mesh.ComputeBoundingBox();
        TArray<TSharedPtr<FJsonValue>> Values;
        for (double Value : {Bounds.Min.X, Bounds.Min.Y, Bounds.Min.Z, Bounds.Max.X, Bounds.Max.Y, Bounds.Max.Z})
            Values.Add(MakeShared<FJsonValueNumber>(Value));
        Report->SetArrayField(TEXT("bounds_cm"), Values);
    }
    FString Text;
    FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Text));
    if (!FFileHelper::SaveStringToFile(Text, *Output)) return 3;
    UE_LOG(LogTemp, Display, TEXT("CarlaMeshBridge status=%s error=%s"), bSuccess ? TEXT("PASS") : TEXT("FAIL"), *Error);
    return bSuccess ? 0 : 2;
}
