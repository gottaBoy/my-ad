#include "CarlaInterchangePayloadChecks.h"

#include "Dom/JsonObject.h"
#include "InterchangeDispatcherTask.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/LargeMemoryReader.h"
#include "Serialization/LargeMemoryWriter.h"
#include "StaticMeshAttributes.h"
#include <limits>

namespace
{
TArray64<uint8> MeshBytes(FMeshDescription Mesh)
{
    FLargeMemoryWriter Writer;
    Mesh.Serialize(Writer);
    return TArray64<uint8>(Writer.GetData(), Writer.TotalSize());
}

bool CheckGeometry(const FMeshDescription& Source, const FMeshDescription& Payload,
    const FTransform& Transform, FString& Error)
{
    if (Source.Vertices().Num() != Payload.Vertices().Num()
        || Source.Triangles().Num() != Payload.Triangles().Num()
        || Source.VertexInstances().Num() != Payload.VertexInstances().Num()
        || Source.PolygonGroups().Num() != Payload.PolygonGroups().Num())
    { Error = TEXT("Payload topology counts changed"); return false; }
    const FStaticMeshConstAttributes Before(Source), After(Payload);
    const FMatrix Matrix = Transform.ToMatrixWithScale();
    const FMatrix Normals = Matrix.Inverse().GetTransposed();
    if (Before.GetVertexInstanceUVs().GetNumChannels() != After.GetVertexInstanceUVs().GetNumChannels())
    { Error = TEXT("Payload UV channel count changed"); return false; }
    for (FPolygonGroupID Group : Source.PolygonGroups().GetElementIDs())
        if (Before.GetPolygonGroupMaterialSlotNames()[Group] != After.GetPolygonGroupMaterialSlotNames()[Group])
        { Error = TEXT("Payload material slot identity changed"); return false; }
    for (FPolygonID Polygon : Source.Polygons().GetElementIDs())
        if (Source.GetPolygonPolygonGroup(Polygon) != Payload.GetPolygonPolygonGroup(Polygon))
        { Error = TEXT("Payload polygon material assignment changed"); return false; }
    for (FVertexID Vertex : Source.Vertices().GetElementIDs())
    {
        const FVector Expected = Transform.TransformPosition(FVector(Before.GetVertexPositions()[Vertex]));
        if (!Expected.Equals(FVector(After.GetVertexPositions()[Vertex]), 1e-3))
        { Error = TEXT("Requested transform was not applied to every vertex"); return false; }
    }
    for (FVertexInstanceID Corner : Source.VertexInstances().GetElementIDs())
    {
        const FVector Expected = FVector(Normals.TransformVector(FVector(Before.GetVertexInstanceNormals()[Corner]))).GetSafeNormal();
        if (!Expected.Equals(FVector(After.GetVertexInstanceNormals()[Corner]), 1e-4)
            || Before.GetVertexInstanceColors()[Corner] != After.GetVertexInstanceColors()[Corner]
            || Source.GetVertexInstanceVertex(Corner) != Payload.GetVertexInstanceVertex(Corner))
        { Error = TEXT("Payload normal transform or UV preservation failed"); return false; }
        for (int32 Channel = 0; Channel < Before.GetVertexInstanceUVs().GetNumChannels(); ++Channel)
            if (Before.GetVertexInstanceUVs().Get(Corner, Channel) != After.GetVertexInstanceUVs().Get(Corner, Channel))
            { Error = TEXT("Payload UV data changed"); return false; }
        const FVector RawTangent = FVector(Matrix.TransformVector(FVector(Before.GetVertexInstanceTangents()[Corner])));
        const FVector Tangent = RawTangent.GetAbsMax() > 0.0
            ? (RawTangent / RawTangent.GetAbsMax()).GetSafeNormal() : FVector::ZeroVector;
        const float Sign = Before.GetVertexInstanceBinormalSigns()[Corner] * (Matrix.Determinant() < 0 ? -1.f : 1.f);
        if (!Tangent.Equals(FVector(After.GetVertexInstanceTangents()[Corner]), 1e-4)
            || !FMath::IsNearlyEqual(Sign, After.GetVertexInstanceBinormalSigns()[Corner]))
        { Error = TEXT("Payload tangent transform or handedness failed"); return false; }
    }
    // The owned planar controls permit an independent UE winding/normal oracle.
    for (FTriangleID Triangle : Payload.Triangles().GetElementIDs())
    {
        const auto Corners = Payload.GetTriangleVertexInstances(Triangle);
        FVector Points[3];
        for (int32 Index = 0; Index < 3; ++Index)
            Points[Index] = FVector(After.GetVertexPositions()[Payload.GetVertexInstanceVertex(Corners[Index])]);
        const FVector Cross = FVector::CrossProduct(Points[2] - Points[0], Points[1] - Points[0]);
        const FVector Facing = Cross.GetAbsMax() > 0.0 ? (Cross / Cross.GetAbsMax()).GetSafeNormal() : FVector::ZeroVector;
        for (FVertexInstanceID Corner : Corners)
            if (FVector::DotProduct(Facing, FVector(After.GetVertexInstanceNormals()[Corner])) < 0.99)
            { Error = TEXT("Mirrored payload winding disagrees with normals"); return false; }
    }
    return true;
}

bool WriteAndReadPayload(const CarlaUfbxMesh::FStaticPayload& Payload, const FString& ResultDir,
    int64& Bytes, FString& Error)
{
    FMeshDescription Mesh = Payload.Mesh;
    FLargeMemoryWriter Writer;
    Mesh.Serialize(Writer);
    bool bSkinned = false;
    Writer << bSkinned;
    if (Writer.IsError() || Writer.TotalSize() <= 0)
    { Error = TEXT("Payload serialization failed"); return false; }
    const FString Path = FPaths::Combine(ResultDir, Payload.RequestUid + TEXT(".payload"));
    if (FPaths::FileExists(Path) || !FFileHelper::SaveArrayToFile(
        TArray64<uint8>(Writer.GetData(), Writer.TotalSize()), *Path))
    { Error = TEXT("Could not create request-specific static payload"); return false; }
    TArray64<uint8> Data;
    if (!FFileHelper::LoadFileToArray(Data, *Path))
    { Error = TEXT("Could not read payload file"); return false; }
    FLargeMemoryReader Reader(Data.GetData(), Data.Num());
    FMeshDescription Loaded;
    Loaded.Serialize(Reader);
    bool bLoadedSkinned = true;
    Reader << bLoadedSkinned;
    if (Reader.IsError() || Reader.Tell() != Data.Num() || bLoadedSkinned
        || MeshBytes(Loaded) != MeshBytes(Payload.Mesh))
    { Error = TEXT("Static payload data did not round trip"); return false; }
    Bytes = Data.Num();
    return true;
}
}

bool RunPayloadChecks(const CarlaUfbxMesh::FStaticScene& Scene, const FString& ResultDir,
    TArray<TSharedPtr<FJsonValue>>& Records, int32& Passed, int64& Bytes,
    int32& Vertices, int32& Triangles, int32& Materials, FString& Error)
{
    Passed = 0;
    Records.Reset();
    Bytes = 0; Vertices = 0; Triangles = 0; Materials = 0;
    const TCHAR* Labels[] = {TEXT("identity"), TEXT("translated"), TEXT("mirrored")};
    const FTransform Transforms[] = {
        FTransform::Identity, FTransform(FVector(17, -23, 31)),
        FTransform(FQuat(FVector(1, 2, 3).GetSafeNormal(), 0.7), FVector(-7, 11, 19), FVector(-2, 3, 0.5))
    };
    TSet<FString> Uids;
    for (const CarlaUfbxMesh::FSceneMesh& SourceMesh : Scene.Meshes)
    {
        const TArray64<uint8> Original = MeshBytes(SourceMesh.Mesh);
        for (int32 Index = 0; Index < UE_ARRAY_COUNT(Transforms); ++Index)
        {
            UE::Interchange::FJsonFetchMeshPayloadCmd Command(TEXT("FBX"), SourceMesh.PayloadKey, Transforms[Index]);
            UE::Interchange::FJsonFetchMeshPayloadCmd Decoded;
            const FString RequestJson = Command.ToJson();
            if (!Decoded.FromJson(RequestJson) || Decoded.GetPayloadKey() != SourceMesh.PayloadKey
                || Decoded.GetTranslatorID() != TEXT("FBX")
                || !Decoded.GetMeshGlobalTransform().Equals(Transforms[Index], 1e-5))
            { Error = TEXT("UE Dispatcher request lost key or transform"); return false; }
            const FTransform Requested = Decoded.GetMeshGlobalTransform();
            CarlaUfbxMesh::FStaticPayload Payload, Repeat;
            if (!CarlaUfbxMesh::FetchStaticPayload(Scene, Decoded.GetPayloadKey(), Requested, Payload, Error)
                || !CarlaUfbxMesh::FetchStaticPayload(Scene, Decoded.GetPayloadKey(), Requested, Repeat, Error)) return false;
            if (Payload.RequestUid.Len() != 64 || Uids.Contains(Payload.RequestUid)
                || Repeat.RequestUid != Payload.RequestUid || MeshBytes(Repeat.Mesh) != MeshBytes(Payload.Mesh)
                || Payload.MeshUid != SourceMesh.Uid || Payload.PayloadKey != SourceMesh.PayloadKey)
            { Error = TEXT("Payload lookup, repeatability or request identity failed"); return false; }
            Uids.Add(Payload.RequestUid);
            if (!CheckGeometry(SourceMesh.Mesh, Payload.Mesh, Requested, Error)) return false;
            int64 PayloadBytes = 0;
            if (!WriteAndReadPayload(Payload, ResultDir, PayloadBytes, Error)) return false;
            UE::Interchange::FJsonFetchMeshPayloadCmd::JsonResultParser Result, DecodedResult;
            const FString Filename = Payload.RequestUid + TEXT(".payload");
            Result.SetResultFilename(FPaths::Combine(ResultDir, Filename));
            const FString ResultJson = Result.ToJson();
            if (!DecodedResult.FromJson(ResultJson) || DecodedResult.GetResultFilename() != Result.GetResultFilename()
                || !FPaths::FileExists(DecodedResult.GetResultFilename()))
            { Error = TEXT("UE Dispatcher result lost the payload file"); return false; }
            TSharedRef<FJsonObject> Record = MakeShared<FJsonObject>();
            Record->SetStringField(TEXT("mesh_uid"), Payload.MeshUid);
            Record->SetStringField(TEXT("payload_key"), Payload.PayloadKey);
            Record->SetStringField(TEXT("request_uid"), Payload.RequestUid);
            Record->SetStringField(TEXT("transform"), Labels[Index]);
            Record->SetStringField(TEXT("filename"), Payload.RequestUid + TEXT(".payload"));
            Record->SetNumberField(TEXT("bytes"), PayloadBytes);
            Record->SetNumberField(TEXT("vertices"), Payload.Mesh.Vertices().Num());
            Record->SetNumberField(TEXT("triangles"), Payload.Mesh.Triangles().Num());
            Record->SetNumberField(TEXT("materials"), Payload.Mesh.PolygonGroups().Num());
            Record->SetBoolField(TEXT("roundtrip"), true);
            Record->SetBoolField(TEXT("dispatcher_roundtrip"), true);
            Record->SetStringField(TEXT("request_json"), RequestJson);
            Record->SetStringField(TEXT("result_json"), ResultJson);
            Records.Add(MakeShared<FJsonValueObject>(Record));
            Bytes += PayloadBytes;
            Vertices += Payload.Mesh.Vertices().Num(); Triangles += Payload.Mesh.Triangles().Num();
            Materials += Payload.Mesh.PolygonGroups().Num();
            ++Passed;
        }
        if (MeshBytes(SourceMesh.Mesh) != Original)
        { Error = TEXT("Fetching payload mutated the imported source mesh"); return false; }
        ++Passed;
    }
    if (Scene.Meshes.IsEmpty()) { Error = TEXT("No mesh for negative payload tests"); return false; }
    const FString Key = Scene.Meshes[0].PayloadKey;
    CarlaUfbxMesh::FStaticPayload Sentinel;
    if (!CarlaUfbxMesh::FetchStaticPayload(Scene, Key, FTransform::Identity, Sentinel, Error)) return false;
    const TArray64<uint8> SentinelBytes = MeshBytes(Sentinel.Mesh);
    const FString SentinelUid = Sentinel.RequestUid;
    auto Reject = [&](const CarlaUfbxMesh::FStaticScene& Input, const FString& Query, const FTransform& Transform)
    {
        FString Reason;
        if (CarlaUfbxMesh::FetchStaticPayload(Input, Query, Transform, Sentinel, Reason)
            || Reason.IsEmpty() || Sentinel.RequestUid != SentinelUid || Sentinel.PayloadKey != Key
            || Sentinel.MeshUid != Scene.Meshes[0].Uid || MeshBytes(Sentinel.Mesh) != SentinelBytes)
        { Error = TEXT("Invalid request was accepted or mutated the output"); return false; }
        ++Passed;
        return true;
    };
    if (!Reject(Scene, TEXT(""), FTransform::Identity)
        || !Reject(Scene, TEXT("unknown-key"), FTransform::Identity)
        || !Reject(Scene, Key.ToUpper(), FTransform::Identity)
        || !Reject(Scene, Key, FTransform(FQuat(0, 0, 0, 0)))
        || !Reject(Scene, Key, FTransform(FQuat::Identity, FVector::ZeroVector, FVector(1, 0, 1)))
        || !Reject(Scene, Key, FTransform(FVector(std::numeric_limits<double>::infinity(), 0, 0)))
        || !Reject(Scene, Key, FTransform(FVector(1e300, 0, 0)))
        || !Reject(Scene, Key, FTransform(FVector(1e12, 1e12, 1e12)))) return false;
    CarlaUfbxMesh::FStaticScene Ambiguous = Scene;
    Ambiguous.Meshes.Add(Scene.Meshes[0]);
    if (!Reject(Ambiguous, Key, FTransform::Identity)) return false;
    CarlaUfbxMesh::FStaticScene Stale = Scene;
    Stale.SourceSha256 = FString::ChrN(64, TCHAR(48));
    if (!Reject(Stale, Key, FTransform::Identity)) return false;
    // Identical geometry from different FBX objects still needs separate request files.
    CarlaUfbxMesh::FStaticScene Duplicate = Scene;
    auto& Copy = Duplicate.Meshes.Add_GetRef(Scene.Meshes[0]);
    Copy.Uid = TEXT("FBX/Object/duplicate-test");
    Copy.PayloadKey = TEXT("ufbx-static-ue-cm-v1/") + Scene.SourceSha256 + TEXT("/") + Copy.Uid;
    CarlaUfbxMesh::FStaticPayload Other;
    if (!CarlaUfbxMesh::FetchStaticPayload(Duplicate, Copy.PayloadKey, FTransform::Identity, Other, Error)) return false;
    if (Other.RequestUid == SentinelUid || MeshBytes(Other.Mesh) != SentinelBytes)
    { Error = TEXT("Identical geometry collapsed distinct request identities"); return false; }
    ++Passed;
    FTransform SignedZero = FTransform::Identity;
    SignedZero.SetTranslation(FVector(-0.0, 0.0, -0.0));
    if (!CarlaUfbxMesh::FetchStaticPayload(Scene, Key, SignedZero, Other, Error)
        || Other.RequestUid != SentinelUid)
    { Error = TEXT("Equivalent signed-zero requests have different identities"); return false; }
    ++Passed;
    for (double Sign : {1.0, -1.0})
    {
        const FTransform Small(FQuat::Identity, FVector::ZeroVector, FVector(Sign * 1e-8, 1e-8, 1e-8));
        CarlaUfbxMesh::FStaticPayload Payload;
        if (!CarlaUfbxMesh::FetchStaticPayload(Scene, Key, Small, Payload, Error)
            || !CheckGeometry(Scene.Meshes[0].Mesh, Payload.Mesh, Small, Error)) return false;
        const auto Positions = FStaticMeshConstAttributes(Payload.Mesh).GetVertexPositions();
        const auto OriginalPositions = FStaticMeshConstAttributes(Scene.Meshes[0].Mesh).GetVertexPositions();
        for (FVertexID Vertex : Payload.Mesh.Vertices().GetElementIDs())
            if (!FVector(Positions[Vertex]).Equals(Small.TransformPosition(FVector(OriginalPositions[Vertex])), 1e-12))
            { Error = TEXT("Small-scale payload lost precision"); return false; }
        ++Passed;
    }
    // Add nonzero tangents, a second UV channel and a reassigned material slot so
    // the preservation checks cannot succeed just by comparing default zero data.
    CarlaUfbxMesh::FStaticScene Rich = Scene;
    FMeshDescription& RichMesh = Rich.Meshes[0].Mesh;
    FStaticMeshAttributes RichAttributes(RichMesh);
    RichAttributes.GetVertexInstanceUVs().SetNumChannels(2);
    const FPolygonGroupID ExtraGroup = RichMesh.CreatePolygonGroup();
    RichAttributes.GetPolygonGroupMaterialSlotNames()[ExtraGroup] = TEXT("ExtraSlot");
    for (FPolygonID Polygon : RichMesh.Polygons().GetElementIDs())
        RichMesh.SetPolygonPolygonGroup(Polygon, ExtraGroup);
    for (FVertexInstanceID Corner : RichMesh.VertexInstances().GetElementIDs())
    {
        RichAttributes.GetVertexInstanceUVs().Set(Corner, 1, FVector2f(0.125f * Corner.GetValue(), 0.75f));
        RichAttributes.GetVertexInstanceColors()[Corner] = FVector4f(0.2f, 0.3f, 0.7f, 1.f);
        RichAttributes.GetVertexInstanceTangents()[Corner] = FVector3f(FVector::CrossProduct(
            FVector(RichAttributes.GetVertexInstanceNormals()[Corner]), FVector(1, 2, 3)).GetSafeNormal());
        RichAttributes.GetVertexInstanceBinormalSigns()[Corner] = 1.f;
    }
    CarlaUfbxMesh::FStaticPayload RichPayload;
    if (!CarlaUfbxMesh::FetchStaticPayload(Rich, Key, Transforms[2], RichPayload, Error)
        || !CheckGeometry(RichMesh, RichPayload.Mesh, Transforms[2], Error)) return false;
    ++Passed;
    for (int32 Mutation = 0; Mutation < 3; ++Mutation)
    {
        FMeshDescription Corrupt = RichPayload.Mesh;
        FStaticMeshAttributes Attributes(Corrupt);
        if (Mutation == 0) Attributes.GetVertexInstanceUVs().Set(FVertexInstanceID(0), 1, FVector2f(9, 9));
        if (Mutation == 1) Attributes.GetPolygonGroupMaterialSlotNames()[ExtraGroup] = TEXT("WrongSlot");
        if (Mutation == 2) Corrupt.SetPolygonPolygonGroup(FPolygonID(0), FPolygonGroupID(0));
        FString Reason;
        if (CheckGeometry(RichMesh, Corrupt, Transforms[2], Reason) || Reason.IsEmpty())
        { Error = TEXT("Same-count payload corruption escaped the geometry check"); return false; }
        ++Passed;
    }
    UE::Interchange::FJsonFetchMeshPayloadCmd InvalidCommand;
    if (InvalidCommand.FromJson(TEXT("{}")) || InvalidCommand.FromJson(TEXT("not-json")))
    { Error = TEXT("Malformed Dispatcher commands were accepted"); return false; }
    ++Passed;
    return true;
}
