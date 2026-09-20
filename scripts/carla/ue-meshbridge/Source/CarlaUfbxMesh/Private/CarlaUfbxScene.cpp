#include "CarlaUfbxScene.h"
#include "CarlaUfbxMeshInternal.h"
#include "CarlaAssimpMesh.h"
#include "GenericPlatform/GenericPlatformFile.h"
#include "HAL/PlatformFileManager.h"
#include "HAL/PlatformProcess.h"
#include "Misc/ScopeExit.h"
#include "Serialization/BufferArchive.h"
#include "StaticMeshAttributes.h"
#include <limits>

namespace CarlaUfbxMesh
{
namespace
{
constexpr int64 MaxSourceBytes = 512 * 1024 * 1024;
constexpr double TransformTolerance = 1e-8;
const FString RootUid = TEXT("FBX/Root");
const FString DefaultMaterialUid = TEXT("FBX/DefaultMaterial");

bool SourceHash(const void* Data, SIZE_T Size, FString& Hash, FString& Error)
{
    // Core has no Linux SHA256 implementation. Use the installed OpenSSL C ABI.
    void* Library = FPlatformProcess::GetDllHandle(TEXT("libcrypto.so.3"));
    if (!Library) Library = FPlatformProcess::GetDllHandle(TEXT("libcrypto.so.1.1"));
    if (!Library) { Error = TEXT("OpenSSL libcrypto is required for source SHA256"); return false; }
    ON_SCOPE_EXIT { FPlatformProcess::FreeDllHandle(Library); };
    using FHashFunction = uint8* (*)(const uint8*, SIZE_T, uint8*);
    const auto Function = reinterpret_cast<FHashFunction>(FPlatformProcess::GetDllExport(Library, TEXT("SHA256")));
    uint8 Digest[32] = {};
    if (!Function || Function(static_cast<const uint8*>(Data), Size, Digest) != Digest)
    { Error = TEXT("OpenSSL SHA256 failed"); return false; }
    Hash = BytesToHexLower(Digest, UE_ARRAY_COUNT(Digest));
    return true;
}

ufbx_load_opts SceneLoadOptions()
{
    ufbx_load_opts Options = Private::StaticLoadOptions();
    Options.space_conversion = UFBX_SPACE_CONVERSION_MODIFY_GEOMETRY;
    Options.retain_dom = true;
    Options.inherit_mode_handling = UFBX_INHERIT_MODE_HANDLING_PRESERVE;
    return Options;
}

bool NumericId(const ufbx_dom_node* Dom, int64& Id, FString& Error)
{
    if (!Dom || !Dom->values.data || !Dom->values.count
        || Dom->values.data[0].type != UFBX_DOM_VALUE_NUMBER)
    { Error = TEXT("Unsupported FBX object without a numeric ID"); return false; }
    const ufbx_dom_value& Value = Dom->values.data[0];
    if (!FMath::IsFinite(Value.value_float) || Value.value_float != double(Value.value_int) || Value.value_int == 0)
    { Error = TEXT("Invalid, fractional or reserved FBX object ID"); return false; }
    Id = Value.value_int;
    return true;
}

bool DomInteger(const ufbx_dom_node* Dom, int64& Value)
{
    if (!Dom || !Dom->values.data || Dom->values.count == 0
        || Dom->values.data[0].type != UFBX_DOM_VALUE_NUMBER)
        return false;
    const ufbx_dom_value& Entry = Dom->values.data[0];
    if (!FMath::IsFinite(Entry.value_float)
        || Entry.value_float != double(Entry.value_int))
        return false;
    Value = Entry.value_int;
    return true;
}

bool ObjectIds(const ufbx_scene& Scene, TSet<int64>& Ids, FString& Error)
{
    if (!Scene.dom_root)
    { Error = TEXT("Unsupported legacy FBX: numeric object IDs and retained DOM are required"); return false; }
    const ufbx_dom_node* Header = ufbx_dom_find(Scene.dom_root, "FBXHeaderExtension");
    const ufbx_dom_node* VersionNode = Header ? ufbx_dom_find(Header, "FBXVersion") : nullptr;
    int64 Version = 0;
    if (!DomInteger(VersionNode, Version) || Version < 7000)
    { Error = TEXT("Unsupported legacy FBX: numeric object IDs and retained DOM are required"); return false; }
    const ufbx_dom_node* Objects = ufbx_dom_find(Scene.dom_root, "Objects");
    if (!Objects || !Objects->children.data || !Objects->children.count || Objects->children.count > 1000000)
    { Error = TEXT("Missing or oversized FBX Objects DOM"); return false; }
    for (const ufbx_dom_node* Object : Objects->children)
    {
        int64 Id = 0;
        if (!NumericId(Object, Id, Error)) return false;
        if (Ids.Contains(Id))
        { Error = FString::Printf(TEXT("Duplicate FBX object ID: %lld"), static_cast<long long>(Id)); return false; }
        Ids.Add(Id);
    }
    return true;
}

bool ObjectUid(const ufbx_element& Element, const char* Kind, const TSet<int64>& Ids,
    TSet<FString>& Used, FString& Uid, FString& Error, int64* SourceId = nullptr)
{
    int64 Id = 0;
    if (!NumericId(Element.dom_node, Id, Error)) return false;
    if (!Ids.Contains(Id) || !Element.dom_node->name.data || FCStringAnsi::Strcmp(Element.dom_node->name.data, Kind) != 0)
    { Error = TEXT("Unsupported embedded or synthetic FBX object identity"); return false; }
    Uid = FString::Printf(TEXT("FBX/Object/%lld"), static_cast<long long>(Id));
    if (Used.Contains(Uid)) { Error = TEXT("Repeated exported FBX object identity"); return false; }
    Used.Add(Uid);
    if (SourceId) *SourceId = Id;
    return true;
}

void ReadLegacyMetadata(const ufbx_node& Node, const TSet<int64>& Ids, FSceneNode& Output)
{
    if (Node.all_attribs.count > 1) return;
    switch (Node.attrib_type)
    {
    case UFBX_ELEMENT_UNKNOWN:
    case UFBX_ELEMENT_EMPTY: Output.SourceAttributeType = TEXT("eNull"); break;
    case UFBX_ELEMENT_MESH: Output.SourceAttributeType = TEXT("eMesh"); break;
    case UFBX_ELEMENT_LOD_GROUP: Output.SourceAttributeType = TEXT("eLODGroup"); break;
    default: return;
    }
    if (Node.attrib)
    {
        FString Error;
        if (!NumericId(Node.attrib->dom_node, Output.SourceAttributeId, Error)
            || !Ids.Contains(Output.SourceAttributeId)) return;
    }
    // The static mesh path evaluates pivots, but the Legacy factory also uses their
    // original values for grouping/reimport. Reject them until that conversion is ported.
    for (const char* Name : {"RotationPivot", "ScalingPivot"})
    {
        const ufbx_vec3 Pivot = ufbx_find_vec3(&Node.props, Name, ufbx_zero_vec3);
        if (Pivot.x != 0.0 || Pivot.y != 0.0 || Pivot.z != 0.0) return;
    }
    Output.bLegacyMetadataSupported = true;
}

FMatrix Matrix(const ufbx_matrix& Source)
{
    FMatrix Result = FMatrix::Identity;
    for (int32 Column = 0; Column < 4; ++Column)
        for (int32 Row = 0; Row < 3; ++Row)
            Result.M[Column][Row] = Source.v[Column * 3 + Row];
    return Result;
}

bool MatricesMatch(const FMatrix& A, const FMatrix& B)
{
    for (int32 Row = 0; Row < 4; ++Row)
    {
        double BasisScale = std::numeric_limits<double>::min();
        if (Row < 3)
            for (int32 Column = 0; Column < 3; ++Column)
                BasisScale = FMath::Max(BasisScale, FMath::Max(FMath::Abs(A.M[Row][Column]), FMath::Abs(B.M[Row][Column])));
        for (int32 Column = 0; Column < 4; ++Column)
        {
            const double X = A.M[Row][Column], Y = B.M[Row][Column];
            const double Scale = Row < 3 && Column < 3 ? BasisScale : FMath::Max(1.0, FMath::Abs(X));
            if (!FMath::IsFinite(X) || !FMath::IsFinite(Y)
                || FMath::Abs(X - Y) > TransformTolerance * Scale)
                return false;
        }
    }
    return true;
}

bool ReadTransform(const ufbx_matrix& Source, FTransform& Transform, FString& Error)
{
    for (double Value : Source.v)
        if (!FMath::IsFinite(Value)) { Error = TEXT("Non-finite scene transform"); return false; }
    const double Determinant = ufbx_matrix_determinant(&Source);
    if (!FMath::IsFinite(Determinant) || Determinant == 0)
    { Error = TEXT("Singular scene transform is unsupported"); return false; }
    const FMatrix Original = Matrix(Source);
    const ufbx_transform Parts = ufbx_matrix_to_transform(&Source);
    const FTransform Candidate(
        FQuat(Parts.rotation.x, Parts.rotation.y, Parts.rotation.z, Parts.rotation.w),
        FVector(Parts.translation.x, Parts.translation.y, Parts.translation.z),
        FVector(Parts.scale.x, Parts.scale.y, Parts.scale.z));
    if (Candidate.ContainsNaN() || !MatricesMatch(Original, Candidate.ToMatrixWithScale()))
    { Error = TEXT("Shear or a non-TRS scene transform is unsupported"); return false; }
    Transform = Candidate;
    return true;
}

bool ConvertScene(const ufbx_scene& Source, const FString& Hash, FStaticScene& Output, FString& Error)
{
    Error.Reset();
    if (Source.anim_stacks.count || Source.anim_curves.count || Source.anim_values.count
        || Source.skin_deformers.count || Source.skin_clusters.count || Source.blend_deformers.count
        || Source.blend_shapes.count || Source.cache_deformers.count)
    { Error = TEXT("Animation, skin, morph and cache deformation are unsupported by the static scene API"); return false; }
    if (!Source.root_node || Source.root_node->parent || !Source.root_node->is_root
        || !Source.nodes.data || !Source.nodes.count || Source.nodes.count > 100000
        || !Source.meshes.data || !Source.meshes.count || Source.meshes.count > 100000
        || Source.materials.count > 100000 || (Source.materials.count && !Source.materials.data))
    { Error = TEXT("Invalid or oversized static scene graph"); return false; }
    TSet<int64> Ids;
    if (!ObjectIds(Source, Ids, Error)) return false;
    TSet<FString> UsedUids;
    UsedUids.Add(RootUid);
    FStaticScene Result;
    Result.SourceSha256 = Hash;
    TMap<const ufbx_material*, FString> MaterialUids;
    for (const ufbx_material* Material : Source.materials)
    {
        if (!Material || MaterialUids.Contains(Material)) { Error = TEXT("Invalid scene material list"); return false; }
        FSceneMaterial Item;
        if (!ObjectUid(Material->element, "Material", Ids, UsedUids, Item.Uid, Error)) return false;
        Item.DisplayLabel = UTF8_TO_TCHAR(Material->name.data);
        MaterialUids.Add(Material, Item.Uid);
        Result.Materials.Add(MoveTemp(Item));
    }
    auto MaterialUid = [&](const ufbx_material* Material, FString& Uid)
    {
        if (!Material)
        {
            if (!UsedUids.Contains(DefaultMaterialUid))
            {
                UsedUids.Add(DefaultMaterialUid);
                Result.Materials.Add({DefaultMaterialUid, TEXT("DefaultMaterial")});
            }
            Uid = DefaultMaterialUid;
            return true;
        }
        const FString* Known = MaterialUids.Find(Material);
        if (!Known) { Error = TEXT("Material binding references an unknown FBX material"); return false; }
        Uid = *Known;
        return true;
    };

    TSet<const ufbx_node*> KnownNodes;
    for (const ufbx_node* Node : Source.nodes)
    {
        if (!Node || KnownNodes.Contains(Node)) { Error = TEXT("Invalid or repeated scene node"); return false; }
        KnownNodes.Add(Node);
    }
    if (!KnownNodes.Contains(Source.root_node)) { Error = TEXT("Scene root is absent from nodes"); return false; }
    TArray<const ufbx_node*> Order;
    TSet<const ufbx_node*> Visited;
    Order.Add(Source.root_node);
    Visited.Add(Source.root_node);
    for (int32 Index = 0; Index < Order.Num(); ++Index)
    {
        const ufbx_node* Node = Order[Index];
        if (Node->children.count > Source.nodes.count || (Node->children.count && !Node->children.data))
        { Error = TEXT("Invalid child list"); return false; }
        for (const ufbx_node* Child : Node->children)
        {
            if (!KnownNodes.Contains(Child) || Visited.Contains(Child) || Child->parent != Node)
            { Error = TEXT("Cyclic or inconsistent scene parent/child links"); return false; }
            Visited.Add(Child);
            Order.Add(Child);
        }
    }
    if (size_t(Visited.Num()) != Source.nodes.count)
    { Error = TEXT("Disconnected scene nodes are unsupported"); return false; }

    TMap<const ufbx_mesh*, int32> MeshIndices;
    for (const ufbx_mesh* Mesh : Source.meshes)
    {
        if (!Mesh || MeshIndices.Contains(Mesh) || Mesh->materials.count > 100000
            || (Mesh->materials.count && !Mesh->materials.data))
        { Error = TEXT("Invalid scene mesh list"); return false; }
        FSceneMesh Item;
        if (!ObjectUid(Mesh->element, "Geometry", Ids, UsedUids, Item.Uid, Error, &Item.SourceObjectId)) return false;
        Item.PayloadKey = TEXT("ufbx-static-ue-cm-v1/") + Hash + TEXT("/") + Item.Uid;
        size_t NumSlots = FMath::Max(size_t(1), Mesh->materials.count);
        bool bReferenced = false;
        for (const ufbx_node* Node : Order)
        {
            if (Node->mesh != Mesh) continue;
            bReferenced = true;
            if (Node->materials.count > 100000 || (Node->materials.count && !Node->materials.data))
            { Error = TEXT("Invalid instance material list"); return false; }
            NumSlots = FMath::Max(NumSlots, Node->materials.count);
        }
        if (!bReferenced) { Error = TEXT("Unreferenced mesh payload is unsupported"); return false; }
        if (Mesh->face_material.count && !Mesh->face_material.data)
        { Error = TEXT("Invalid face material list"); return false; }
        for (uint32 Slot : Mesh->face_material)
        {
            if (Slot >= 100000) { Error = TEXT("Invalid face material slot"); return false; }
            NumSlots = FMath::Max(NumSlots, size_t(Slot) + 1);
        }
        for (size_t Slot = 0; Slot < NumSlots; ++Slot)
        {
            Item.SlotKeys.Add(FString::Printf(TEXT("Slot_%u"), uint32(Slot)));
            FString Uid;
            const ufbx_material* Material = Slot < Mesh->materials.count ? Mesh->materials.data[Slot] : nullptr;
            if (!MaterialUid(Material, Uid)) return false;
            Item.DefaultMaterialUids.Add(MoveTemp(Uid));
        }
        if (!Private::ConvertLocalPayload(Source, *Mesh, Item.SlotKeys, Item.Mesh, Error)) return false;
        MeshIndices.Add(Mesh, Result.Meshes.Num());
        Result.Meshes.Add(MoveTemp(Item));
    }

    TMap<const ufbx_node*, int32> NodeIndices;
    for (const ufbx_node* Node : Order)
    {
        FSceneNode Item;
        Item.Uid = RootUid;
        Item.DisplayLabel = UTF8_TO_TCHAR(Node->name.data);
        if (Node != Source.root_node && !ObjectUid(Node->element, "Model", Ids, UsedUids, Item.Uid, Error, &Item.SourceObjectId)) return false;
        ReadLegacyMetadata(*Node, Ids, Item);
        if (!ReadTransform(Node->node_to_parent, Item.LocalTransform, Error)
            || !ReadTransform(Node->node_to_world, Item.GlobalTransform, Error)
            || !ReadTransform(Node->geometry_to_node, Item.GeometricTransform, Error)) return false;
        if (Node->parent)
        {
            const int32* ParentIndex = NodeIndices.Find(Node->parent);
            if (!ParentIndex) { Error = TEXT("Missing scene parent identity"); return false; }
            const FSceneNode& Parent = Result.Nodes[*ParentIndex];
            Item.ParentUid = Parent.Uid;
            const ufbx_matrix Expected = ufbx_matrix_mul(&Node->parent->node_to_world, &Node->node_to_parent);
            if (!MatricesMatch(Matrix(Expected), Matrix(Node->node_to_world))
                || !MatricesMatch(Matrix(Node->node_to_world),
                    (Item.LocalTransform * Parent.GlobalTransform).ToMatrixWithScale()))
            { Error = TEXT("Shear or unsupported transform inheritance would lose the parent/global relationship"); return false; }
        }
        else if (!MatricesMatch(Item.LocalTransform.ToMatrixWithScale(), Item.GlobalTransform.ToMatrixWithScale()))
        { Error = TEXT("Root local/global transforms disagree"); return false; }
        const ufbx_matrix GeometryWorld = ufbx_matrix_mul(&Node->node_to_world, &Node->geometry_to_node);
        if (!MatricesMatch(Matrix(GeometryWorld), Matrix(Node->geometry_to_world))
            || !MatricesMatch(Matrix(Node->geometry_to_world),
                (Item.GeometricTransform * Item.GlobalTransform).ToMatrixWithScale()))
        { Error = TEXT("Shear in the composed geometry/global transform is unsupported"); return false; }
        if (Node->mesh)
        {
            const int32* MeshIndex = MeshIndices.Find(Node->mesh);
            if (!MeshIndex) { Error = TEXT("Unknown mesh instance identity"); return false; }
            const FSceneMesh& Mesh = Result.Meshes[*MeshIndex];
            Item.MeshUid = Mesh.Uid;
            for (int32 Slot = 0; Slot < Mesh.SlotKeys.Num(); ++Slot)
            {
                FString Uid = Mesh.DefaultMaterialUids[Slot];
                if (size_t(Slot) < Node->materials.count && !MaterialUid(Node->materials.data[Slot], Uid)) return false;
                Item.MaterialUids.Add(MoveTemp(Uid));
            }
        }
        NodeIndices.Add(Node, Result.Nodes.Num());
        Result.Nodes.Add(MoveTemp(Item));
    }
    Output = MoveTemp(Result);
    return true;
}

bool ImportMemory(const void* Data, SIZE_T Size, FStaticScene& Output, FString& Error)
{
    Error.Reset();
    if (!Size || Size > SIZE_T(MaxSourceBytes) || ufbx_source_version != UFBX_HEADER_VERSION)
    { Error = TEXT("Invalid source size or ufbx header/library version"); return false; }
    FString Hash;
    if (!SourceHash(Data, Size, Hash, Error)) return false;
    const ufbx_load_opts Options = SceneLoadOptions();
    ufbx_error LoadError = {};
    ufbx_scene* Scene = ufbx_load_memory(Data, Size, &Options, &LoadError);
    if (!Scene)
    {
        char Description[1024] = {};
        ufbx_format_error(Description, sizeof(Description), &LoadError);
        Error = FString::Printf(TEXT("ufbx scene load failed: %s"), UTF8_TO_TCHAR(Description));
        return false;
    }
    ON_SCOPE_EXIT { ufbx_free_scene(Scene); };
    return ConvertScene(*Scene, Hash, Output, Error);
}

const char SceneFixture[] = R"FBX(
; FBX 7.4.0 project-owned scene fixture
FBXHeaderExtension: {
 FBXHeaderVersion: 1003
 FBXVersion: 7400
}
GlobalSettings: {
 Properties70: {
  P: "UpAxis", "int", "Integer", "",1
  P: "UpAxisSign", "int", "Integer", "",1
  P: "FrontAxis", "int", "Integer", "",2
  P: "FrontAxisSign", "int", "Integer", "",1
  P: "CoordAxis", "int", "Integer", "",0
  P: "CoordAxisSign", "int", "Integer", "",1
  P: "UnitScaleFactor", "double", "Number", "",100
 }
}
Objects: {
 Model: 100, "Model::Same", "Null" {
  Properties70: {
   P: "Lcl Translation", "Lcl Translation", "", "A",0,2,0
   P: "Lcl Scaling", "Lcl Scaling", "", "A",1,1,1
  }
 }
 Model: 101, "Model::Same", "Mesh" {
  Properties70: {
   P: "Lcl Translation", "Lcl Translation", "", "A",3,0,0
   P: "Lcl Rotation", "Lcl Rotation", "", "A",0,0,0
   P: "InheritType", "enum", "", "",1
   P: "GeometricTranslation", "Vector3D", "Vector", "",0,0,4
   P: "GeometricScaling", "Vector3D", "Vector", "",-2,1,1
  }
 }
 Model: 102, "Model::Child", "Null" {
  Properties70: {
   P: "Lcl Translation", "Lcl Translation", "", "A",0,0,1
  }
 }
 Model: 103, "Model::Same", "Mesh" {
  Properties70: {
   P: "Lcl Translation", "Lcl Translation", "", "A",-3,0,0
  }
 }
 Geometry: 200, "Geometry::Shared", "Mesh" {
  Vertices: *12 { a: 0,0,0,1,0,0,1,1,0,0,1,0 }
  PolygonVertexIndex: *6 { a: 0,1,-3,0,2,-4 }
  LayerElementNormal: 0 {
   MappingInformationType: "ByPolygonVertex"
   ReferenceInformationType: "Direct"
   Normals: *18 { a: 0,0,1,0,0,1,0,0,1,0,0,1,0,0,1,0,0,1 }
  }
  LayerElementUV: 0 {
   MappingInformationType: "ByPolygonVertex"
   ReferenceInformationType: "Direct"
   UV: *12 { a: 0,0,1,0,1,1,0,0,1,1,0,1 }
  }
  LayerElementMaterial: 0 {
   MappingInformationType: "ByPolygon"
   ReferenceInformationType: "IndexToDirect"
   Materials: *2 { a: 0,1 }
  }
  Layer: 0 {
   LayerElement: { Type: "LayerElementNormal" TypedIndex: 0 }
   LayerElement: { Type: "LayerElementUV" TypedIndex: 0 }
   LayerElement: { Type: "LayerElementMaterial" TypedIndex: 0 }
  }
 }
 Material: 300, "Material::Same", "" { ShadingModel: "phong" }
 Material: 301, "Material::Same", "" { ShadingModel: "phong" }
}
Connections: {
 C: "OO",100,0
 C: "OO",101,100
 C: "OO",102,101
 C: "OO",103,100
 C: "OO",200,101
 C: "OO",200,103
 C: "OO",300,101
 C: "OO",301,101
 C: "OO",301,103
 C: "OO",300,103
}
)FBX";

bool ImportText(const FString& Text, FStaticScene& Output, FString& Error)
{
    FTCHARToUTF8 Data(*Text);
    return ImportMemory(Data.Get(), Data.Length(), Output, Error);
}

const FSceneNode* FindNode(const FStaticScene& Scene, const TCHAR* Uid)
{
    return Scene.Nodes.FindByPredicate([&](const FSceneNode& Node) { return Node.Uid == Uid; });
}

TArray<uint8> SceneBytes(FStaticScene& Scene)
{
    FBufferArchive Bytes;
    Bytes << Scene.SourceSha256;
    for (FSceneNode& Node : Scene.Nodes)
        Bytes << Node.Uid << Node.ParentUid << Node.DisplayLabel << Node.MeshUid
            << Node.LocalTransform << Node.GlobalTransform << Node.GeometricTransform << Node.MaterialUids;
    for (FSceneMesh& Mesh : Scene.Meshes)
    {
        Bytes << Mesh.Uid << Mesh.PayloadKey << Mesh.SlotKeys << Mesh.DefaultMaterialUids;
        Mesh.Mesh.Serialize(Bytes);
    }
    for (FSceneMaterial& Material : Scene.Materials) Bytes << Material.Uid << Material.DisplayLabel;
    return MoveTemp(static_cast<TArray<uint8>&>(Bytes));
}

bool WorldMatchesFlattened(const FString& Text, const FStaticScene& Scene, FString& Error)
{
    FTCHARToUTF8 Data(*Text);
    const ufbx_load_opts Options = Private::StaticLoadOptions();
    ufbx_error LoadError = {};
    ufbx_scene* Source = ufbx_load_memory(Data.Get(), Data.Length(), &Options, &LoadError);
    if (!Source) { Error = TEXT("Cannot read ADJUST_TRANSFORMS baseline"); return false; }
    ON_SCOPE_EXIT { ufbx_free_scene(Source); };
    FMeshDescription Flat;
    if (!Private::ConvertFlattened(*Source, Flat, Error)) return false;
    TArray<FVector> Expected, Actual;
    const auto FlatPositions = FStaticMeshConstAttributes(Flat).GetVertexPositions();
    for (FVertexID Vertex : Flat.Vertices().GetElementIDs()) Expected.Add(FVector(FlatPositions[Vertex]));
    int32 Triangles = 0;
    for (const FSceneNode& Node : Scene.Nodes)
    {
        if (Node.MeshUid.IsEmpty()) continue;
        const FSceneMesh* Mesh = Scene.Meshes.FindByPredicate([&](const FSceneMesh& Item) { return Item.Uid == Node.MeshUid; });
        if (!Mesh) { Error = TEXT("Missing local payload in world reconstruction"); return false; }
        const FStaticMeshConstAttributes Attributes(Mesh->Mesh);
        const auto Positions = Attributes.GetVertexPositions();
        const FMatrix World = (Node.GeometricTransform * Node.GlobalTransform).ToMatrixWithScale();
        const FMatrix NormalMatrix = World.Inverse().GetTransposed();
        for (FVertexID Vertex : Mesh->Mesh.Vertices().GetElementIDs())
            Actual.Add(Node.GlobalTransform.TransformPosition(Node.GeometricTransform.TransformPosition(FVector(Positions[Vertex]))));
        for (FTriangleID Triangle : Mesh->Mesh.Triangles().GetElementIDs())
        {
            ++Triangles;
            const auto Corners = Mesh->Mesh.GetTriangleVertexInstances(Triangle);
            FVector Points[3], Normals[3];
            for (int32 Index = 0; Index < 3; ++Index)
            {
                const int32 Corner = World.Determinant() < 0 && Index ? 3 - Index : Index;
                const FVertexInstanceID Instance = Corners[Corner];
                Points[Index] = World.TransformPosition(FVector(Positions[Mesh->Mesh.GetVertexInstanceVertex(Instance)]));
                Normals[Index] = FVector(NormalMatrix.TransformVector(FVector(Attributes.GetVertexInstanceNormals()[Instance]))).GetSafeNormal();
            }
            const FVector Facing = FVector::CrossProduct(Points[2] - Points[0], Points[1] - Points[0]).GetSafeNormal();
            for (const FVector& Normal : Normals)
                if (FVector::DotProduct(Facing, Normal) < 0.99)
                { Error = TEXT("Mirrored local payload reconstruction lost UE normal/winding"); return false; }
        }
    }
    auto Sort = [](const FVector& A, const FVector& B)
    {
        if (A.X != B.X) return A.X < B.X;
        if (A.Y != B.Y) return A.Y < B.Y;
        return A.Z < B.Z;
    };
    Expected.Sort(Sort);
    Actual.Sort(Sort);
    if (Expected.Num() != Actual.Num() || Triangles != Flat.Triangles().Num())
    { Error = TEXT("Scene reconstruction and flattened counts differ"); return false; }
    for (int32 Index = 0; Index < Actual.Num(); ++Index)
        if (!Expected[Index].Equals(Actual[Index], 1e-3))
        { Error = TEXT("MODIFY_GEOMETRY scene differs from ADJUST_TRANSFORMS world geometry"); return false; }
    return true;
}
}

bool ImportScene(const FString& Filename, FStaticScene& Output, FString& Error)
{
    Error.Reset();
    TUniquePtr<IFileHandle> File(FPlatformFileManager::Get().GetPlatformFile().OpenRead(*Filename));
    if (!File) { Error = TEXT("Cannot open FBX source"); return false; }
    const int64 Size = File->Size();
    if (Size <= 0 || Size > MaxSourceBytes)
    { Error = TEXT("FBX source size is outside the static scene limit"); return false; }
    TArray<uint8> Data;
    Data.SetNumUninitialized(int32(Size));
    if (!File->Read(Data.GetData(), Size)) { Error = TEXT("Cannot read complete FBX source"); return false; }
    return ImportMemory(Data.GetData(), Data.Num(), Output, Error);
}

bool RunSceneSelfTests(int32& Passed, FString& Error)
{
    Passed = 0;
    Error.Reset();
    auto Check = [&](bool Success, const TCHAR* Name)
    {
        if (!Success) { Error = FString::Printf(TEXT("%s: %s"), Name, *Error); return false; }
        ++Passed;
        return true;
    };
    FString Hash;
    if (!Check(SourceHash("abc", 3, Hash, Error)
        && Hash == TEXT("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
        TEXT("known SHA256 vector"))) return false;
    {
        ufbx_matrix Small = ufbx_identity_matrix;
        Small.m00 = Small.m11 = Small.m22 = 1e-8;
        FTransform Converted;
        if (!Check(ReadTransform(Small, Converted, Error)
            && Converted.GetScale3D().Equals(FVector(1e-8), 1e-16)
            && !MatricesMatch(Matrix(Small), FTransform(FQuat::Identity, FVector::ZeroVector, FVector::ZeroVector).ToMatrixWithScale()),
            TEXT("tiny scales remain nonzero and cannot match a collapsed matrix"))) return false;
    }
    const FString Text = UTF8_TO_TCHAR(SceneFixture);
    FStaticScene Scene;
    if (!Check(ImportText(Text, Scene, Error), TEXT("real hierarchical FBX import"))) return false;
    const FSceneNode* A = FindNode(Scene, TEXT("FBX/Object/101"));
    const FSceneNode* B = FindNode(Scene, TEXT("FBX/Object/103"));
    const FSceneNode* Child = FindNode(Scene, TEXT("FBX/Object/102"));
    const FSceneNode* Root = FindNode(Scene, TEXT("FBX/Root"));
    if (!Check(A && B && Child && Root && A->Uid != B->Uid && A->DisplayLabel == B->DisplayLabel
        && Root->ParentUid.IsEmpty() && Child->ParentUid == A->Uid, TEXT("numeric UIDs and duplicate labels"))) return false;
    if (!Check(Scene.Meshes.Num() == 1 && A->MeshUid == B->MeshUid
        && A->MeshUid == TEXT("FBX/Object/200"), TEXT("shared mesh identity"))) return false;
    const FSceneMesh& Mesh = Scene.Meshes[0];
    if (!Check(Mesh.SlotKeys.Num() == 2 && Mesh.Mesh.PolygonGroups().Num() == 2
        && A->MaterialUids.Num() == 2 && B->MaterialUids.Num() == 2
        && A->MaterialUids[0] == B->MaterialUids[1] && A->MaterialUids[1] == B->MaterialUids[0]
        && A->MaterialUids[0] != A->MaterialUids[1], TEXT("per-instance indexed material override"))) return false;
    if (!Check(Mesh.Mesh.ComputeBoundingBox().GetSize().Equals(FVector(100,100,0), 1e-3)
        && Child->GlobalTransform.GetTranslation().Equals(FVector(-100,300,200), 1e-3),
        TEXT("centimeter payload and geometry transform exclusion from child"))) return false;
    if (!Check(WorldMatchesFlattened(Text, Scene, Error), TEXT("same-policy world geometry and mirrored normals"))) return false;
    int64 Bytes = 0;
    if (!Check(CarlaAssimpMesh::CheckMemoryRoundTrip(Scene.Meshes[0].Mesh, Bytes, Error) && Bytes > 0,
        TEXT("real local MeshDescription serialization"))) return false;
    FStaticScene Repeat;
    if (!Check(ImportText(Text, Repeat, Error) && SceneBytes(Scene) == SceneBytes(Repeat),
        TEXT("stable repeated source identity and payload"))) return false;
    FStaticScene Renamed;
    if (!Check(ImportText(Text.Replace(TEXT("::Same"), TEXT("::Renamed")), Renamed, Error)
        && Renamed.Meshes[0].Uid == Mesh.Uid && Renamed.SourceSha256 != Scene.SourceSha256
        && Renamed.Meshes[0].PayloadKey != Mesh.PayloadKey,
        TEXT("UID independent of name and payload bound to source bytes"))) return false;
    FStaticScene SharedMaterial;
    if (!Check(ImportText(Text.Replace(TEXT("C: \"OO\",301,101"), TEXT("C: \"OO\",300,101")), SharedMaterial, Error)
        && SharedMaterial.Meshes[0].SlotKeys.Num() == 2
        && SharedMaterial.Meshes[0].Mesh.PolygonGroups().Num() == 2
        && FindNode(SharedMaterial, TEXT("FBX/Object/101"))->MaterialUids[0]
            == FindNode(SharedMaterial, TEXT("FBX/Object/101"))->MaterialUids[1]
        && FindNode(SharedMaterial, TEXT("FBX/Object/103"))->MaterialUids[0]
            != FindNode(SharedMaterial, TEXT("FBX/Object/103"))->MaterialUids[1],
        TEXT("same material pointer does not merge two slots"))) return false;
    FString NoMaterials = Text;
    for (const TCHAR* Connection : {TEXT("C: \"OO\",300,101"), TEXT("C: \"OO\",301,101"),
        TEXT("C: \"OO\",301,103"), TEXT("C: \"OO\",300,103")})
        NoMaterials = NoMaterials.Replace(Connection, TEXT("; no material connection"));
    FStaticScene Default;
    if (!Check(ImportText(NoMaterials, Default, Error)
        && Default.Materials.ContainsByPredicate([](const FSceneMaterial& Item) { return Item.Uid == DefaultMaterialUid; }),
        TEXT("explicit missing-material identity"))) return false;
    auto Reject = [&](const FString& Rejected, const TCHAR* Name)
    {
        FStaticScene Output = Scene;
        const TArray<uint8> Before = SceneBytes(Output);
        FString Reason;
        const bool bAccepted = ImportText(Rejected, Output, Reason);
        return Check(!bAccepted && !Reason.IsEmpty() && Before == SceneBytes(Output), Name);
    };
    if (!Reject(Text.Replace(TEXT("Material: 301,"), TEXT("Material: 300,")), TEXT("reject duplicate object IDs atomically"))) return false;
    if (!Reject(Text.Replace(TEXT("FBXVersion: 7400"), TEXT("FBXVersion: 6100")), TEXT("reject unsupported legacy IDs"))) return false;
    if (!Reject(Text.Replace(TEXT("Geometry: 200,"), TEXT("Geometry: \"legacy\",")), TEXT("reject nonnumeric ID"))) return false;
    if (!Reject(Text.Replace(TEXT("\"A\",1,1,1"), TEXT("\"A\",2,1,1"))
        .Replace(TEXT("\"A\",0,0,0"), TEXT("\"A\",0,0,45")), TEXT("reject actual FBX shear"))) return false;
    if (!Reject(Text.Replace(TEXT("Objects: {"), TEXT("Objects: {\n AnimationStack: 400, \"AnimStack::Take\", \"\" {}")),
        TEXT("reject animation"))) return false;
    if (!Reject(Text.Replace(TEXT("Objects: {"), TEXT("Objects: {\n Deformer: 500, \"Deformer::Skin\", \"Skin\" {}")),
        TEXT("reject skin"))) return false;
    if (!Reject(Text.Left(32), TEXT("reject truncated scene atomically"))) return false;
    Error.Reset();
    return true;
}
}
