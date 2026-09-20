#include "CarlaAssimpMesh.h"
#include "MeshDescription.h"
#include "StaticMeshAttributes.h"
#include "StaticMeshOperations.h"
#include "Modules/ModuleManager.h"
#include "Misc/ScopeExit.h"
#include "Serialization/BufferArchive.h"
#include "Serialization/MemoryReader.h"
#include <limits>

THIRD_PARTY_INCLUDES_START
#include <assimp/cimport.h>
#include <assimp/config.h>
#include <assimp/material.h>
#include <assimp/postprocess.h>
#include <assimp/scene.h>
THIRD_PARTY_INCLUDES_END

IMPLEMENT_MODULE(FDefaultModuleImpl, CarlaAssimpMesh)

namespace CarlaAssimpMesh
{
namespace
{
struct FAxisPolicy
{
    int32 Axis[3] = {};
    int32 Sign[3] = {};
    double Centimeters = 0.0;

    template<typename T> FVector3f Map(const aiVector3t<T>& Value, bool bPosition) const
    {
        const double Scale = bPosition ? Centimeters : 1.0;
        return FVector3f(Value[Axis[0]] * Sign[0] * Scale,
            Value[Axis[1]] * Sign[1] * Scale, Value[Axis[2]] * Sign[2] * Scale);
    }

    double Determinant() const
    {
        const int32 Inversions = (Axis[0] > Axis[1]) + (Axis[0] > Axis[2]) + (Axis[1] > Axis[2]);
        return ((Inversions % 2) ? -1.0 : 1.0) * Sign[0] * Sign[1] * Sign[2];
    }
};

bool ReadPolicy(const aiScene& Scene, FAxisPolicy& Policy, FString& Error)
{
    const char* Axes[] = { "FrontAxis", "CoordAxis", "UpAxis" };
    const char* Signs[] = { "FrontAxisSign", "CoordAxisSign", "UpAxisSign" };
    bool bHasScale = false;
    if (Scene.mMetaData)
    {
        float FloatScale = 0;
        if (Scene.mMetaData->Get(aiString("UnitScaleFactor"), FloatScale))
        {
            Policy.Centimeters = FloatScale;
            bHasScale = true;
        }
        else bHasScale = Scene.mMetaData->Get(aiString("UnitScaleFactor"), Policy.Centimeters);
    }
    if (!bHasScale || !FMath::IsFinite(Policy.Centimeters) || Policy.Centimeters <= 0.0)
    {
        Error = TEXT("Missing or invalid FBX unit metadata");
        return false;
    }
    for (int32 Index = 0; Index < 3; ++Index)
    {
        if (!Scene.mMetaData->Get(aiString(Axes[Index]), Policy.Axis[Index]) ||
            !Scene.mMetaData->Get(aiString(Signs[Index]), Policy.Sign[Index]) ||
            Policy.Axis[Index] < 0 || Policy.Axis[Index] > 2 ||
            (Policy.Sign[Index] != 1 && Policy.Sign[Index] != -1))
        {
            Error = TEXT("Missing or invalid FBX axis metadata");
            return false;
        }
    }
    if (Policy.Axis[0] == Policy.Axis[1] || Policy.Axis[0] == Policy.Axis[2] || Policy.Axis[1] == Policy.Axis[2])
    {
        Error = TEXT("FBX axes are not a basis");
        return false;
    }
    return true;
}

bool ConvertStatic(const aiScene& Scene, FMeshDescription& Output, FString& Error)
{
    FAxisPolicy Policy;
    if (!ReadPolicy(Scene, Policy, Error)) return false;
    if (!Scene.mRootNode || !Scene.HasMeshes()) { Error = TEXT("No scene geometry"); return false; }
    if (Scene.mNumAnimations) { Error = TEXT("Animation is unsupported by the static bridge"); return false; }
    uint32 UVChannels = 1;
    for (uint32 Index = 0; Index < Scene.mNumMeshes; ++Index)
    {
        const aiMesh* Mesh = Scene.mMeshes[Index];
        if (!Mesh || !Mesh->HasPositions() || !Mesh->HasFaces() || !Mesh->HasNormals())
        { Error = TEXT("Incomplete mesh geometry"); return false; }
        if (Mesh->mNumBones || Mesh->mNumAnimMeshes)
        { Error = TEXT("Skinning and morph targets are unsupported by the static bridge"); return false; }
        UVChannels = FMath::Max(UVChannels, Mesh->GetNumUVChannels());
    }

    // Commit only after the entire scene converts, so failure leaves the caller untouched.
    FMeshDescription Result;
    FStaticMeshAttributes Attributes(Result);
    Attributes.Register();
    auto Positions = Attributes.GetVertexPositions();
    auto Normals = Attributes.GetVertexInstanceNormals();
    auto UVs = Attributes.GetVertexInstanceUVs();
    auto Colors = Attributes.GetVertexInstanceColors();
    UVs.SetNumChannels(UVChannels);
    auto Slots = Attributes.GetPolygonGroupMaterialSlotNames();
    TArray<FPolygonGroupID> Groups;
    TSet<FName> UsedSlots;
    for (uint32 Index = 0; Index < Scene.mNumMaterials; ++Index)
    {
        aiString Name;
        if (!Scene.mMaterials[Index]) { Error = TEXT("Missing material"); return false; }
        Scene.mMaterials[Index]->Get(AI_MATKEY_NAME, Name);
        FPolygonGroupID Group = Result.CreatePolygonGroup();
        FString Base = Name.length ? FString(UTF8_TO_TCHAR(Name.C_Str())).TrimStartAndEnd() : FString();
        if (Base.IsEmpty() || Base.Equals(TEXT("None"), ESearchCase::IgnoreCase)) Base = TEXT("Material");
        Base = Base.Left(NAME_SIZE - 32);
        FName Slot(*Base);
        uint32 Suffix = Index;
        while (Slot.IsNone() || UsedSlots.Contains(Slot)) Slot = FName(*FString::Printf(TEXT("%s_%u"), *Base, Suffix++));
        Slots[Group] = Slot;
        UsedSlots.Add(Slot);
        Groups.Add(Group);
    }

    struct FVisit { const aiNode* Node; aiMatrix4x4t<double> Parent; };
    TArray<FVisit> Queue;
    Queue.Add({Scene.mRootNode, aiMatrix4x4t<double>()});
    TSet<const aiNode*> Seen;
    while (!Queue.IsEmpty())
    {
        FVisit Visit = Queue.Pop(EAllowShrinking::No);
        if (!Visit.Node || Seen.Contains(Visit.Node) || Seen.Num() >= 100000)
        { Error = TEXT("Invalid node graph"); return false; }
        Seen.Add(Visit.Node);
        const aiMatrix4x4t<double> World = Visit.Parent * static_cast<aiMatrix4x4t<double>>(Visit.Node->mTransformation);
        aiMatrix3x3t<double> NormalMatrix(World);
        const double Determinant = NormalMatrix.Determinant();
        // A tiny uniform scale is invertible; an absolute determinant cutoff is not scale-invariant.
        if (!FMath::IsFinite(Determinant) || Determinant == 0.0)
        { Error = TEXT("Singular node transform"); return false; }
        NormalMatrix.Inverse().Transpose();
        // Assimp uses the conventional cross product; UE triangle normals use its reverse.
        const bool bReverse = Determinant * Policy.Determinant() > 0;
        for (uint32 Child = 0; Child < Visit.Node->mNumChildren; ++Child)
            Queue.Add({Visit.Node->mChildren[Child], World});
        for (uint32 Reference = 0; Reference < Visit.Node->mNumMeshes; ++Reference)
        {
            const uint32 MeshIndex = Visit.Node->mMeshes[Reference];
            if (MeshIndex >= Scene.mNumMeshes) { Error = TEXT("Invalid node mesh reference"); return false; }
            const aiMesh& Mesh = *Scene.mMeshes[MeshIndex];
            if (Mesh.mMaterialIndex >= uint32(Groups.Num())) { Error = TEXT("Invalid material reference"); return false; }
            TArray<FVertexID> Vertices;
            Vertices.Reserve(Mesh.mNumVertices);
            for (uint32 Index = 0; Index < Mesh.mNumVertices; ++Index)
            {
                const FVector3f Position = Policy.Map(World * static_cast<aiVector3t<double>>(Mesh.mVertices[Index]), true);
                if (Position.ContainsNaN()) { Error = TEXT("Non-finite vertex"); return false; }
                FVertexID Vertex = Result.CreateVertex();
                Positions[Vertex] = Position;
                Vertices.Add(Vertex);
            }
            for (uint32 FaceIndex = 0; FaceIndex < Mesh.mNumFaces; ++FaceIndex)
            {
                const aiFace& Face = Mesh.mFaces[FaceIndex];
                if (Face.mNumIndices != 3 || !Face.mIndices) { Error = TEXT("Non-triangulated face"); return false; }
                for (uint32 Corner = 0; Corner < 3; ++Corner)
                    if (Face.mIndices[Corner] >= Mesh.mNumVertices)
                    { Error = TEXT("Invalid face index"); return false; }
                if (Face.mIndices[0] == Face.mIndices[1] || Face.mIndices[1] == Face.mIndices[2] || Face.mIndices[0] == Face.mIndices[2])
                { Error = TEXT("Repeated triangle vertex"); return false; }
                const FVector3d P0(Positions[Vertices[Face.mIndices[0]]]);
                const FVector3d E1 = FVector3d(Positions[Vertices[Face.mIndices[1]]]) - P0;
                const FVector3d E2 = FVector3d(Positions[Vertices[Face.mIndices[2]]]) - P0;
                const double AreaSquared = FVector3d::CrossProduct(E1, E2).SizeSquared();
                if (!FMath::IsFinite(AreaSquared) || AreaSquared == 0.0)
                { Error = TEXT("Degenerate triangle"); return false; }
                FVertexInstanceID Corners[3];
                for (uint32 Corner = 0; Corner < 3; ++Corner)
                {
                    const uint32 Index = Face.mIndices[bReverse && Corner ? 3 - Corner : Corner];
                    if (Index >= Mesh.mNumVertices) { Error = TEXT("Invalid face index"); return false; }
                    aiVector3t<double> SourceNormal = NormalMatrix * static_cast<aiVector3t<double>>(Mesh.mNormals[Index]);
                    SourceNormal.NormalizeSafe();
                    const FVector3f Normal = Policy.Map(SourceNormal, false);
                    if (Normal.ContainsNaN() || Normal.IsNearlyZero()) { Error = TEXT("Invalid normal"); return false; }
                    Corners[Corner] = Result.CreateVertexInstance(Vertices[Index]);
                    Normals[Corners[Corner]] = Normal;
                    const FVector4f Color = Mesh.HasVertexColors(0)
                        ? FVector4f(Mesh.mColors[0][Index].r, Mesh.mColors[0][Index].g, Mesh.mColors[0][Index].b, Mesh.mColors[0][Index].a)
                        : FVector4f(1, 1, 1, 1);
                    if (Color.ContainsNaN()) { Error = TEXT("Non-finite color"); return false; }
                    Colors[Corners[Corner]] = Color;
                    for (uint32 Channel = 0; Channel < UVChannels; ++Channel)
                    {
                        const FVector2f UV = Mesh.HasTextureCoords(Channel)
                            ? FVector2f(Mesh.mTextureCoords[Channel][Index].x, 1.0f - Mesh.mTextureCoords[Channel][Index].y)
                            : FVector2f::ZeroVector;
                        if (UV.ContainsNaN()) { Error = TEXT("Non-finite UV"); return false; }
                        UVs.Set(Corners[Corner], Channel, UV);
                    }
                }
                Result.CreateTriangle(Groups[Mesh.mMaterialIndex], MakeArrayView(Corners));
            }
        }
    }
    if (Result.Triangles().Num() == 0) { Error = TEXT("No referenced triangles"); return false; }
    Output = MoveTemp(Result);
    return true;
}

struct FTestScene
{
    aiScene Scene;
    aiNode Node;
    aiMesh Mesh;
    aiMaterial Material;
    aiMaterial MoreMaterials[3];
    aiBone Bone;
    aiMetadata Metadata;
    aiString Keys[7];
    aiMetadataEntry Entries[7] = {};
    int32 Values[6] = {2, -1, 0, 1, 1, 1};
    float FloatUnitScale = 100;
    double UnitScale = 100;
    aiVector3D Vertices[3] = {{0,0,0}, {1,0,0}, {0,1,0}};
    aiVector3D Normals[3] = {{0,0,1}, {0,0,1}, {0,0,1}};
    aiVector3D UVs[3] = {{0,0,0}, {1,0,0}, {0,1,0}};
    aiColor4D Colors[3] = {{1,0,0,1}, {0,1,0,0.5f}, {0,0,1,1}};
    unsigned Indices[3] = {0,1,2};
    unsigned MeshIndex[1] = {0};
    aiFace Face;
    aiMesh* Meshes[1] = {&Mesh};
    aiMaterial* Materials[4] = {&Material, &MoreMaterials[0], &MoreMaterials[1], &MoreMaterials[2]};
    aiBone* Bones[1] = {&Bone};

    FTestScene()
    {
        Scene.mRootNode = &Node;
        Scene.mNumMeshes = 1;
        Scene.mMeshes = Meshes;
        Scene.mNumMaterials = 1;
        Scene.mMaterials = Materials;
        Scene.mMetaData = &Metadata;
        Node.mNumMeshes = 1;
        Node.mMeshes = MeshIndex;
        Node.mTransformation.a4 = 2;
        Node.mTransformation.b4 = 3;
        Node.mTransformation.c4 = 4;
        Mesh.mNumVertices = 3;
        Mesh.mVertices = Vertices;
        Mesh.mNormals = Normals;
        Mesh.mTextureCoords[0] = UVs;
        Mesh.mColors[0] = Colors;
        Mesh.mNumUVComponents[0] = 2;
        Mesh.mNumFaces = 1;
        Mesh.mFaces = &Face;
        Face.mNumIndices = 3;
        Face.mIndices = Indices;
        const char* Names[] = {"FrontAxis", "FrontAxisSign", "CoordAxis", "CoordAxisSign", "UpAxis", "UpAxisSign", "UnitScaleFactor"};
        for (int32 Index = 0; Index < 7; ++Index)
        {
            Keys[Index].Set(Names[Index]);
            Entries[Index].mType = Index < 6 ? AI_INT32 : AI_FLOAT;
            Entries[Index].mData = Index < 6 ? static_cast<void*>(&Values[Index]) : static_cast<void*>(&FloatUnitScale);
        }
        Metadata.mNumProperties = 7;
        Metadata.mKeys = Keys;
        Metadata.mValues = Entries;
    }

    ~FTestScene()
    {
        // Fixture views borrow stack storage; Assimp destructors must not own it.
        Scene.mRootNode = nullptr;
        Scene.mMeshes = nullptr; Scene.mNumMeshes = 0;
        Scene.mMaterials = nullptr; Scene.mNumMaterials = 0;
        Scene.mMetaData = nullptr;
        Node.mMeshes = nullptr; Node.mNumMeshes = 0;
        Mesh.mVertices = nullptr; Mesh.mNormals = nullptr; Mesh.mNumVertices = 0;
        Mesh.mTextureCoords[0] = nullptr;
        Mesh.mColors[0] = nullptr;
        Mesh.mFaces = nullptr; Mesh.mNumFaces = 0;
        Mesh.mBones = nullptr; Mesh.mNumBones = 0;
        Face.mIndices = nullptr; Face.mNumIndices = 0;
        Metadata.mKeys = nullptr; Metadata.mValues = nullptr; Metadata.mNumProperties = 0;
    }

    FTestScene(const FTestScene&) = delete;
    FTestScene& operator=(const FTestScene&) = delete;
};

bool FacingMatchesNormals(FMeshDescription& Mesh)
{
    FStaticMeshOperations::ComputeTriangleTangentsAndNormals(Mesh, 0.0f);
    FStaticMeshConstAttributes Attributes(Mesh);
    auto Normals = Attributes.GetVertexInstanceNormals();
    for (FTriangleID Triangle : Mesh.Triangles().GetElementIDs())
    {
        auto Corners = Mesh.GetTriangleVertexInstances(Triangle);
        if (FVector3f::DotProduct(Attributes.GetTriangleNormals()[Triangle], Normals[Corners[0]]) <= 0.99f) return false;
    }
    return true;
}

bool SameMesh(const FMeshDescription& BeforeMesh, const FMeshDescription& AfterMesh, FString& Error)
{
    if (BeforeMesh.Vertices().Num() != AfterMesh.Vertices().Num() ||
        BeforeMesh.VertexInstances().Num() != AfterMesh.VertexInstances().Num() ||
        BeforeMesh.Edges().Num() != AfterMesh.Edges().Num() ||
        BeforeMesh.Triangles().Num() != AfterMesh.Triangles().Num() ||
        BeforeMesh.Polygons().Num() != AfterMesh.Polygons().Num() ||
        BeforeMesh.PolygonGroups().Num() != AfterMesh.PolygonGroups().Num() ||
        BeforeMesh.GetNumUVElementChannels() != AfterMesh.GetNumUVElementChannels())
    { Error = TEXT("MeshDescription element counts differ"); return false; }
    FStaticMeshConstAttributes Before(BeforeMesh), After(AfterMesh);
    if (Before.GetVertexInstanceUVs().GetNumChannels() != After.GetVertexInstanceUVs().GetNumChannels())
    { Error = TEXT("MeshDescription UV channel counts differ"); return false; }
    const int32 TriangleUVChannels = Before.GetTriangleUVIndices().GetNumChannels();
    if (TriangleUVChannels != After.GetTriangleUVIndices().GetNumChannels())
    { Error = TEXT("MeshDescription triangle UV channel counts differ"); return false; }
    for (FVertexID Vertex : BeforeMesh.Vertices().GetElementIDs())
        if (!AfterMesh.Vertices().IsValid(Vertex) || !Before.GetVertexPositions()[Vertex].Equals(After.GetVertexPositions()[Vertex], 1e-5f))
        { Error = TEXT("MeshDescription positions differ"); return false; }
    for (FVertexInstanceID Instance : BeforeMesh.VertexInstances().GetElementIDs())
    {
        if (!AfterMesh.VertexInstances().IsValid(Instance) ||
            BeforeMesh.GetVertexInstanceVertex(Instance) != AfterMesh.GetVertexInstanceVertex(Instance) ||
            !Before.GetVertexInstanceNormals()[Instance].Equals(After.GetVertexInstanceNormals()[Instance], 1e-5f) ||
            !Before.GetVertexInstanceColors()[Instance].Equals(After.GetVertexInstanceColors()[Instance], 1e-5f))
        { Error = TEXT("MeshDescription vertex instances differ"); return false; }
        for (int32 Channel = 0; Channel < Before.GetVertexInstanceUVs().GetNumChannels(); ++Channel)
            if (!Before.GetVertexInstanceUVs().Get(Instance, Channel).Equals(After.GetVertexInstanceUVs().Get(Instance, Channel), 1e-5f))
            { Error = TEXT("MeshDescription UVs differ"); return false; }
    }
    for (FPolygonGroupID Group : BeforeMesh.PolygonGroups().GetElementIDs())
        if (!AfterMesh.PolygonGroups().IsValid(Group) || Before.GetPolygonGroupMaterialSlotNames()[Group] != After.GetPolygonGroupMaterialSlotNames()[Group])
        { Error = TEXT("MeshDescription material slots differ"); return false; }
    for (FEdgeID Edge : BeforeMesh.Edges().GetElementIDs())
        if (!AfterMesh.Edges().IsValid(Edge) || BeforeMesh.GetEdgeVertex(Edge, 0) != AfterMesh.GetEdgeVertex(Edge, 0) ||
            BeforeMesh.GetEdgeVertex(Edge, 1) != AfterMesh.GetEdgeVertex(Edge, 1))
        { Error = TEXT("MeshDescription edges differ"); return false; }
    for (FPolygonID Polygon : BeforeMesh.Polygons().GetElementIDs())
        if (!AfterMesh.Polygons().IsValid(Polygon) ||
            BeforeMesh.GetPolygonPolygonGroup(Polygon) != AfterMesh.GetPolygonPolygonGroup(Polygon))
        { Error = TEXT("MeshDescription polygon ownership differs"); return false; }
    for (int32 Channel = 0; Channel < BeforeMesh.GetNumUVElementChannels(); ++Channel)
    {
        if (BeforeMesh.UVs(Channel).Num() != AfterMesh.UVs(Channel).Num())
        { Error = TEXT("MeshDescription UV element counts differ"); return false; }
        for (FUVID UV : BeforeMesh.UVs(Channel).GetElementIDs())
            if (!AfterMesh.UVs(Channel).IsValid(UV) || !Before.GetUVCoordinates(Channel)[UV].Equals(After.GetUVCoordinates(Channel)[UV], 1e-5f))
            { Error = TEXT("MeshDescription UV elements differ"); return false; }
    }
    for (FTriangleID Triangle : BeforeMesh.Triangles().GetElementIDs())
    {
        if (!AfterMesh.Triangles().IsValid(Triangle) ||
            BeforeMesh.GetTrianglePolygon(Triangle) != AfterMesh.GetTrianglePolygon(Triangle) ||
            BeforeMesh.GetTrianglePolygonGroup(Triangle) != AfterMesh.GetTrianglePolygonGroup(Triangle))
        { Error = TEXT("MeshDescription triangle ownership differs"); return false; }
        const auto A = BeforeMesh.GetTriangleVertexInstances(Triangle);
        const auto B = AfterMesh.GetTriangleVertexInstances(Triangle);
        if (A.Num() != B.Num()) { Error = TEXT("MeshDescription corner counts differ"); return false; }
        for (int32 Corner = 0; Corner < A.Num(); ++Corner)
            if (A[Corner] != B[Corner]) { Error = TEXT("MeshDescription corner topology differs"); return false; }
        // Legacy per-instance UVs may have no separate triangle UV-index channels.
        for (int32 Channel = 0; Channel < TriangleUVChannels; ++Channel)
        {
            const auto UVsA = BeforeMesh.GetTriangleUVIndices(Triangle, Channel);
            const auto UVsB = AfterMesh.GetTriangleUVIndices(Triangle, Channel);
            if (UVsA.Num() != UVsB.Num()) { Error = TEXT("MeshDescription UV corner counts differ"); return false; }
            for (int32 Corner = 0; Corner < UVsA.Num(); ++Corner)
                if (UVsA[Corner] != UVsB[Corner]) { Error = TEXT("MeshDescription UV topology differs"); return false; }
        }
    }
    return true;
}
}

bool ImportStatic(const FString& Filename, FMeshDescription& Output, FString& Error)
{
    Error.Reset();
    aiPropertyStore* Properties = aiCreatePropertyStore();
    if (!Properties) { Error = TEXT("Assimp property allocation failed"); return false; }
    ON_SCOPE_EXIT { aiReleasePropertyStore(Properties); };
    // Apply axis metadata and centimeters exactly once in the UE bridge.
    aiSetImportPropertyInteger(Properties, AI_CONFIG_IMPORT_FBX_IGNORE_UP_DIRECTION, 1);
    const aiScene* Scene = aiImportFileExWithProperties(TCHAR_TO_UTF8(*Filename),
        aiProcess_Triangulate | aiProcess_ValidateDataStructure | aiProcess_GenSmoothNormals, nullptr, Properties);
    if (!Scene) { Error = UTF8_TO_TCHAR(aiGetErrorString()); return false; }
    ON_SCOPE_EXIT { aiReleaseImport(Scene); };
    return ConvertStatic(*Scene, Output, Error);
}

bool CheckMemoryRoundTrip(FMeshDescription& Mesh, int64& Bytes, FString& Error)
{
    FBufferArchive Buffer;
    Buffer << Mesh;
    Bytes = Buffer.Num();
    FMemoryReader Reader(Buffer);
    Reader.SetCustomVersions(Buffer.GetCustomVersions());
    FMeshDescription Reloaded;
    Reader << Reloaded;
    if (Reader.IsError() || Reader.Tell() != Buffer.Num())
    { Error = TEXT("MeshDescription archive was not fully read"); return false; }
    return SameMesh(Mesh, Reloaded, Error);
}

bool RunSelfTests(int32& Passed, FString& Error)
{
    Passed = 0;
    FTestScene Test;
    aiScene& Scene = Test.Scene;
    FMeshDescription Mesh;
    if (!ConvertStatic(Scene, Mesh, Error)) return false;
    FStaticMeshConstAttributes Attributes(Mesh);
    if (!Attributes.GetVertexPositions()[FVertexID(0)].Equals(FVector3f(-400,200,300), 1e-4f) ||
        !Attributes.GetVertexPositions()[FVertexID(1)].Equals(FVector3f(-400,300,300), 1e-4f) ||
        !Attributes.GetVertexPositions()[FVertexID(2)].Equals(FVector3f(-400,200,400), 1e-4f) ||
        !FacingMatchesNormals(Mesh))
    { Error = TEXT("Unit/axis/translation/winding test failed"); return false; }
    ++Passed;
    auto Corners = Mesh.GetTriangleVertexInstances(FTriangleID(0));
    if (!Attributes.GetVertexInstanceUVs().Get(Corners[0], 0).Equals(FVector2f(0,1), 1e-5f))
    { Error = TEXT("UV convention test failed"); return false; }
    ++Passed;
    int64 Bytes = 0;
    if (!CheckMemoryRoundTrip(Mesh, Bytes, Error)) return false;
    ++Passed;
    Scene.mRootNode->mTransformation.a1 = -1;
    if (!ConvertStatic(Scene, Mesh, Error) || !FacingMatchesNormals(Mesh))
    { Error = TEXT("Mirrored transform test failed"); return false; }
    ++Passed;
    Test.Entries[6].mType = AI_DOUBLE;
    Test.Entries[6].mData = &Test.UnitScale;
    if (!ConvertStatic(Scene, Mesh, Error) ||
        !Mesh.GetVertexPositions()[FVertexID(0)].Equals(FVector3f(-400,200,300), 1e-4f))
    { Error = TEXT("Double unit metadata test failed"); return false; }
    ++Passed;
    Test.Values[4] = 0;
    if (ConvertStatic(Scene, Mesh, Error) || Mesh.Triangles().Num() != 1)
    { Error = TEXT("Invalid metadata was not rejected atomically"); return false; }
    ++Passed;
    Test.Values[4] = 1;
    Scene.mMeshes[0]->mNumBones = 1;
    Scene.mMeshes[0]->mBones = Test.Bones;
    if (ConvertStatic(Scene, Mesh, Error) || Mesh.Triangles().Num() != 1)
    { Error = TEXT("Unsupported skin data was not rejected atomically"); return false; }
    ++Passed;
    Test.Mesh.mNumBones = 0;
    Test.Mesh.mBones = nullptr;
    Test.Node.mTransformation = aiMatrix4x4();
    Test.Node.mTransformation.a1 = Test.Node.mTransformation.b2 = Test.Node.mTransformation.c3 = 6e-6f;
    if (!ConvertStatic(Scene, Mesh, Error) || !FacingMatchesNormals(Mesh))
    { Error = TEXT("Small invertible scale test failed"); return false; }
    ++Passed;
    auto ExpectRejected = [&](const TCHAR* Description)
    {
        FMeshDescription Before(Mesh);
        FString Rejection;
        if (ConvertStatic(Scene, Mesh, Rejection) || Rejection.IsEmpty() || !SameMesh(Before, Mesh, Error))
        { Error = Description; return false; }
        ++Passed;
        return true;
    };
    for (int32 Sign : {MIN_int32, 0, 2})
    {
        Test.Values[1] = Sign;
        if (!ExpectRejected(TEXT("Invalid sign was not rejected atomically"))) return false;
    }
    Test.Values[1] = -1;
    Test.Indices[2] = 3;
    if (!ExpectRejected(TEXT("Out-of-range index was not rejected atomically"))) return false;
    Test.Indices[2] = 1;
    if (!ExpectRejected(TEXT("Repeated index was not rejected atomically"))) return false;
    Test.Indices[2] = 2;
    Test.Vertices[2] = aiVector3D(2,0,0);
    if (!ExpectRejected(TEXT("Collinear face was not rejected atomically"))) return false;
    Test.Vertices[2] = aiVector3D(0,1,0);
    for (float Invalid : {std::numeric_limits<float>::quiet_NaN(), std::numeric_limits<float>::infinity()})
    {
        Test.Colors[0].r = Invalid;
        if (!ExpectRejected(TEXT("Non-finite color was not rejected atomically"))) return false;
    }
    Test.Colors[0].r = 1;
    FMeshDescription Changed(Mesh);
    FStaticMeshAttributes(Changed).GetVertexInstanceColors()[FVertexInstanceID(0)] = FVector4f(0,0,0,0);
    if (SameMesh(Mesh, Changed, Error)) { Error = TEXT("Color corruption escaped comparison"); return false; }
    ++Passed;
    Changed = Mesh;
    FStaticMeshAttributes(Changed).GetVertexInstanceVertexIndices()[FVertexInstanceID(0)] = FVertexID(1);
    if (SameMesh(Mesh, Changed, Error)) { Error = TEXT("Topology corruption escaped comparison"); return false; }
    ++Passed;
    Changed = Mesh;
    FStaticMeshAttributes(Changed).GetVertexInstanceUVs().SetNumChannels(0);
    if (SameMesh(Mesh, Changed, Error)) { Error = TEXT("UV channel corruption escaped comparison"); return false; }
    ++Passed;
    Changed = Mesh;
    FStaticMeshAttributes(Changed).GetEdgeVertexIndices()[FEdgeID(0)][0] = FVertexID(2);
    if (SameMesh(Mesh, Changed, Error)) { Error = TEXT("Edge corruption escaped comparison"); return false; }
    ++Passed;
    FPolygonGroupID ExtraGroup = Mesh.CreatePolygonGroup();
    FStaticMeshAttributes(Mesh).GetPolygonGroupMaterialSlotNames()[ExtraGroup] = TEXT("Extra");
    Changed = Mesh;
    FStaticMeshAttributes(Changed).GetPolygonPolygonGroupIndices()[FPolygonID(0)] = ExtraGroup;
    if (SameMesh(Mesh, Changed, Error)) { Error = TEXT("Polygon ownership corruption escaped comparison"); return false; }
    ++Passed;
    Mesh.SetNumUVChannels(1);
    FStaticMeshAttributes UVAttributes(Mesh);
    FUVID TriangleUVs[3];
    auto MeshCorners = Mesh.GetTriangleVertexInstances(FTriangleID(0));
    for (int32 Corner = 0; Corner < 3; ++Corner)
    {
        TriangleUVs[Corner] = Mesh.CreateUV(0);
        UVAttributes.GetUVCoordinates(0)[TriangleUVs[Corner]] = UVAttributes.GetVertexInstanceUVs().Get(MeshCorners[Corner], 0);
    }
    Mesh.SetTriangleUVIndices(FTriangleID(0), MakeArrayView(TriangleUVs), 0);
    if (!CheckMemoryRoundTrip(Mesh, Bytes, Error)) return false;
    ++Passed;
    Changed = Mesh;
    Changed.GetTriangleUVIndices(FTriangleID(0), 0)[0] = TriangleUVs[1];
    if (SameMesh(Mesh, Changed, Error)) { Error = TEXT("UV topology corruption escaped comparison"); return false; }
    ++Passed;
    Scene.mNumMaterials = 4;
    const char* MaterialNames[] = {"Paint", "paint", "None", "Paint_1"};
    for (int32 Index = 0; Index < 4; ++Index)
    {
        aiString MaterialName(MaterialNames[Index]);
        Scene.mMaterials[Index]->AddProperty(&MaterialName, AI_MATKEY_NAME);
    }
    if (!ConvertStatic(Scene, Mesh, Error)) return false;
    TSet<FName> SlotKeys;
    for (FPolygonGroupID Group : Mesh.PolygonGroups().GetElementIDs())
    {
        const FName Key = FStaticMeshConstAttributes(Mesh).GetPolygonGroupMaterialSlotNames()[Group];
        if (Key.IsNone() || SlotKeys.Contains(Key)) { Error = TEXT("Invalid or colliding material slot key"); return false; }
        SlotKeys.Add(Key);
    }
    ++Passed;
    if (!ConvertStatic(Scene, Changed, Error) || !SameMesh(Mesh, Changed, Error))
    { Error = TEXT("Material slot keys are not deterministic"); return false; }
    ++Passed;
    Error.Reset();
    return true;
}
}
