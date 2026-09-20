#include "CarlaUfbxScene.h"

#include "Hash/Blake3.h"
#include "Serialization/BufferArchive.h"
#include "StaticMeshAttributes.h"
#include "StaticMeshOperations.h"

namespace CarlaUfbxMesh
{
namespace
{
bool StableDirectionMatrix(FMatrix& Matrix)
{
    double Largest = 0.0;
    for (int32 Row = 0; Row < 3; ++Row)
        for (int32 Column = 0; Column < 3; ++Column)
        {
            const double Value = Matrix.M[Row][Column];
            if (!FMath::IsFinite(Value)) return false;
            Largest = FMath::Max(Largest, FMath::Abs(Value));
        }
    if (Largest == 0.0) return false;
    for (int32 Row = 0; Row < 3; ++Row)
        for (int32 Column = 0; Column < 3; ++Column)
            Matrix.M[Row][Column] /= Largest;
    return true;
}

FVector StableUnit(const FVector& Direction)
{
    const double Largest = Direction.GetAbsMax();
    return Largest > 0.0 ? (Direction / Largest).GetSafeNormal() : FVector::ZeroVector;
}
}

bool FetchStaticPayload(const FStaticScene& Scene, const FString& PayloadKey,
    const FTransform& BakeTransform, FStaticPayload& Output, FString& Error)
{
    Error.Reset();
    if (PayloadKey.IsEmpty() || Scene.SourceSha256.Len() != 64)
    { Error = TEXT("Invalid source-bound payload key"); return false; }
    const FSceneMesh* Selected = nullptr;
    for (const FSceneMesh& Mesh : Scene.Meshes)
    {
        if (!Mesh.PayloadKey.Equals(PayloadKey, ESearchCase::CaseSensitive)) continue;
        if (Selected) { Error = TEXT("Ambiguous payload key"); return false; }
        Selected = &Mesh;
    }
    if (!Selected) { Error = TEXT("Unknown payload key"); return false; }
    const FString ExpectedKey = TEXT("ufbx-static-ue-cm-v1/") + Scene.SourceSha256 + TEXT("/") + Selected->Uid;
    if (Selected->Uid.IsEmpty() || !PayloadKey.Equals(ExpectedKey, ESearchCase::CaseSensitive))
    { Error = TEXT("Payload key does not match the current source"); return false; }
    const FVector Scale = BakeTransform.GetScale3D();
    if (BakeTransform.ContainsNaN() || !BakeTransform.GetRotation().IsNormalized()
        || Scale.X == 0.0 || Scale.Y == 0.0 || Scale.Z == 0.0)
    { Error = TEXT("Invalid or singular payload transform"); return false; }
    const FMatrix Matrix = BakeTransform.ToMatrixWithScale();
    const double Determinant = Matrix.Determinant();
    if (!FMath::IsFinite(Determinant) || Determinant == 0.0)
    { Error = TEXT("Non-finite or singular payload matrix"); return false; }
    FMatrix Normals = Matrix.TransposeAdjoint() * (Determinant < 0.0 ? -1.0 : 1.0);
    FMatrix Tangents = Matrix;
    if (!StableDirectionMatrix(Normals) || !StableDirectionMatrix(Tangents))
    { Error = TEXT("Unrepresentable payload direction transform"); return false; }
    FBufferArchive Identity;
    FString Key = PayloadKey;
    Identity << Key;
    for (int32 Row = 0; Row < 4; ++Row)
        for (int32 Column = 0; Column < 4; ++Column)
        {
            double Value = Matrix.M[Row][Column];
            if (!FMath::IsFinite(Value))
            { Error = TEXT("Non-finite payload matrix"); return false; }
            if (Value == 0.0) Value = 0.0; // Normalize negative zero before hashing.
            Identity << Value;
        }
    const FBlake3Hash Hash = FBlake3::HashBuffer(Identity.GetData(), Identity.Num());
    FStaticPayload Result;
    Result.RequestUid = BytesToHexLower(Hash.GetBytes(), UE_ARRAY_COUNT(Hash.GetBytes()));
    Result.MeshUid = Selected->Uid;
    Result.PayloadKey = PayloadKey;
    Result.Mesh = Selected->Mesh;
    if (Result.Mesh.Vertices().Num() == 0 || Result.Mesh.Triangles().Num() == 0)
    { Error = TEXT("Empty static payload"); return false; }
    // UE handles positions, tangent handedness and mirrored winding.
    FStaticMeshOperations::ApplyTransform(Result.Mesh, Matrix, true);
    const FStaticMeshConstAttributes SourceAttributes(Selected->Mesh);
    FStaticMeshAttributes Attributes(Result.Mesh);
    for (FVertexID Vertex : Result.Mesh.Vertices().GetElementIDs())
        if (Attributes.GetVertexPositions()[Vertex].ContainsNaN())
        { Error = TEXT("Payload transform overflowed vertex positions"); return false; }
    // Normalize relative to component magnitude: UE GetSafeNormal has an absolute
    // threshold which otherwise zeros directions for valid small-scale requests.
    for (FVertexInstanceID Instance : Result.Mesh.VertexInstances().GetElementIDs())
    {
        const FVector Normal = StableUnit(FVector(Normals.TransformVector(FVector(SourceAttributes.GetVertexInstanceNormals()[Instance]))));
        const FVector Tangent = StableUnit(FVector(Tangents.TransformVector(FVector(SourceAttributes.GetVertexInstanceTangents()[Instance]))));
        if (Normal.ContainsNaN() || Tangent.ContainsNaN() || Normal.IsZero())
        { Error = TEXT("Payload transform produced invalid directions"); return false; }
        Attributes.GetVertexInstanceNormals()[Instance] = FVector3f(Normal);
        Attributes.GetVertexInstanceTangents()[Instance] = FVector3f(Tangent);
    }
    for (FTriangleID Triangle : Result.Mesh.Triangles().GetElementIDs())
    {
        const auto Corners = Result.Mesh.GetTriangleVertexInstances(Triangle);
        const FVector A(Attributes.GetVertexPositions()[Result.Mesh.GetVertexInstanceVertex(Corners[0])]);
        const FVector B(Attributes.GetVertexPositions()[Result.Mesh.GetVertexInstanceVertex(Corners[1])]);
        const FVector C(Attributes.GetVertexPositions()[Result.Mesh.GetVertexInstanceVertex(Corners[2])]);
        if (FVector::CrossProduct(B - A, C - A).IsZero())
        { Error = TEXT("Payload transform collapsed a triangle at float precision"); return false; }
    }
    Output = MoveTemp(Result);
    return true;
}
}
