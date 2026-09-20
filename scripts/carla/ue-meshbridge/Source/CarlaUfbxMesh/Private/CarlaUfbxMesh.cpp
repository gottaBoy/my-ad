#include "CarlaUfbxMesh.h"
#include "CarlaUfbxMeshInternal.h"
#include "CarlaAssimpMesh.h"
#include "MeshDescription.h"
#include "StaticMeshAttributes.h"
#include "StaticMeshOperations.h"
#include "Modules/ModuleManager.h"
#include "Misc/ScopeExit.h"
#include "Serialization/BufferArchive.h"
#include <limits>

THIRD_PARTY_INCLUDES_START
#include <ufbx.h>
THIRD_PARTY_INCLUDES_END

static_assert(UFBX_HEADER_VERSION == ufbx_pack_version(0, 23, 0), "Pinned ufbx v0.23.0 header required");
static_assert(sizeof(ufbx_real) == sizeof(double), "ufbx double precision is required");

IMPLEMENT_MODULE(FDefaultModuleImpl, CarlaUfbxMesh)

namespace CarlaUfbxMesh
{
namespace
{
constexpr size_t MaxElements = 10000000;
constexpr size_t MaxUVChannels = 8;

ufbx_load_opts LoadOptions()
{
    ufbx_load_opts Options = {};
    Options.file_format = UFBX_FILE_FORMAT_FBX;
    // ufbx "front" is opposite to forward: UE forward +X, right +Y, up +Z.
    Options.target_axes = {UFBX_COORDINATE_AXIS_POSITIVE_Y,
        UFBX_COORDINATE_AXIS_POSITIVE_Z, UFBX_COORDINATE_AXIS_NEGATIVE_X};
    Options.target_unit_meters = 0.01;
    Options.space_conversion = UFBX_SPACE_CONVERSION_ADJUST_TRANSFORMS;
    Options.geometry_transform_handling = UFBX_GEOMETRY_TRANSFORM_HANDLING_PRESERVE;
    Options.handedness_conversion_axis = UFBX_MIRROR_AXIS_X;
    Options.index_error_handling = UFBX_INDEX_ERROR_HANDLING_ABORT_LOADING;
    Options.load_external_files = false;
    Options.node_depth_limit = 512;
    Options.temp_allocator.memory_limit = 512 * 1024 * 1024;
    Options.result_allocator.memory_limit = 512 * 1024 * 1024;
    // Keep animation/deformers visible so static-only checks cannot hide them.
    return Options;
}

FVector3f Vector(ufbx_vec3 V) { return FVector3f(V.x, V.y, V.z); }

template<typename A, typename V> bool ReadAttribute(const A& Attribute, size_t Index, V& Value)
{
    if (!Attribute.exists || !Attribute.indices.data || Index >= Attribute.indices.count) return false;
    const size_t ValueIndex = Attribute.indices.data[Index];
    if (!Attribute.values.data || ValueIndex >= Attribute.values.count) return false;
    Value = Attribute.values.data[ValueIndex];
    return true;
}

bool ConvertStatic(const ufbx_scene& Scene, FMeshDescription& Output, FString& Error,
    const ufbx_mesh* LocalMesh = nullptr, const TArray<FString>* LocalSlotKeys = nullptr)
{
    Error.Reset();
    if (!ufbx_coordinate_axes_valid(Scene.settings.axes)
        || !FMath::IsFinite(Scene.settings.unit_meters) || Scene.settings.unit_meters <= 0)
    { Error = TEXT("Invalid FBX coordinate or unit metadata"); return false; }
    if (Scene.anim_stacks.count || Scene.anim_curves.count || Scene.anim_values.count)
    { Error = TEXT("Animation is unsupported by the static ufbx bridge"); return false; }
    if (Scene.skin_deformers.count || Scene.skin_clusters.count || Scene.blend_deformers.count
        || Scene.blend_shapes.count || Scene.cache_deformers.count)
    { Error = TEXT("Skinning, morph and cache deformation are unsupported by the static ufbx bridge"); return false; }
    if (!Scene.root_node || !Scene.nodes.data || Scene.nodes.count > 100000
        || !Scene.meshes.data || !Scene.meshes.count)
    { Error = TEXT("Missing or invalid static scene"); return false; }
    size_t UVChannels = 1;
    for (const ufbx_mesh* Mesh : Scene.meshes)
    {
        if (!Mesh || !Mesh->vertices.data || !Mesh->vertex_indices.data || !Mesh->faces.data
            || !Mesh->vertex_normal.exists || !Mesh->num_vertices || !Mesh->num_faces
            || Mesh->num_vertices > MaxElements || Mesh->num_indices > MaxElements
            || Mesh->num_triangles > MaxElements / 3 || Mesh->num_faces > MaxElements
            || Mesh->vertices.count != Mesh->num_vertices || Mesh->faces.count != Mesh->num_faces
            || Mesh->vertex_indices.count != Mesh->num_indices)
        { Error = TEXT("Incomplete or oversized static mesh"); return false; }
        if (Mesh->all_deformers.count || Mesh->skin_deformers.count || Mesh->blend_deformers.count
            || Mesh->cache_deformers.count)
        { Error = TEXT("Mesh deformation is unsupported by the static ufbx bridge"); return false; }
        if (Mesh->uv_sets.count > MaxUVChannels || (Mesh->uv_sets.count && !Mesh->uv_sets.data)
            || Mesh->color_sets.count > 1)
        { Error = TEXT("Unsupported UV or color layer count"); return false; }
        UVChannels = FMath::Max(UVChannels, Mesh->uv_sets.count);
    }

    // Build into a temporary mesh, including all instances, before committing Output.
    FMeshDescription Result;
    FStaticMeshAttributes Attributes(Result);
    Attributes.Register();
    auto Positions = Attributes.GetVertexPositions();
    auto Normals = Attributes.GetVertexInstanceNormals();
    auto UVs = Attributes.GetVertexInstanceUVs();
    auto Colors = Attributes.GetVertexInstanceColors();
    auto Slots = Attributes.GetPolygonGroupMaterialSlotNames();
    UVs.SetNumChannels(UVChannels);
    TMap<const ufbx_material*, FPolygonGroupID> Groups;
    TArray<FPolygonGroupID> LocalGroups;
    TSet<FName> UsedNames;
    auto AddMaterial = [&](const ufbx_material* Material)
    {
        const FPolygonGroupID Group = Result.CreatePolygonGroup();
        FString Base = Material && Material->name.data
            ? FString(UTF8_TO_TCHAR(Material->name.data)).TrimStartAndEnd() : TEXT("Material");
        if (Base.IsEmpty() || Base.Equals(TEXT("None"), ESearchCase::IgnoreCase)) Base = TEXT("Material");
        Base = Base.Left(NAME_SIZE - 32);
        FName Name(*Base);
        int32 Suffix = Groups.Num();
        while (Name.IsNone() || UsedNames.Contains(Name))
            Name = FName(*FString::Printf(TEXT("%s_%d"), *Base, Suffix++));
        Slots[Group] = Name;
        UsedNames.Add(Name);
        Groups.Add(Material, Group);
    };
    if (Scene.materials.count > 100000 || (Scene.materials.count && !Scene.materials.data))
    { Error = TEXT("Invalid scene material list"); return false; }
    if (LocalMesh)
    {
        if (!LocalSlotKeys || LocalSlotKeys->IsEmpty() || LocalSlotKeys->Num() > 100000)
        { Error = TEXT("Invalid local payload material slots"); return false; }
        for (const FString& Key : *LocalSlotKeys)
        {
            const FName Name(*Key);
            if (Name.IsNone() || UsedNames.Contains(Name))
            { Error = TEXT("Invalid or repeated local payload slot key"); return false; }
            const FPolygonGroupID Group = Result.CreatePolygonGroup();
            Slots[Group] = Name;
            LocalGroups.Add(Group);
            UsedNames.Add(Name);
        }
    }
    else
    {
        for (const ufbx_material* Material : Scene.materials)
        {
            if (!Material || Groups.Contains(Material)) { Error = TEXT("Invalid scene material"); return false; }
            AddMaterial(Material);
        }
    }

    TSet<const ufbx_node*> Seen;
    bool bConvertedLocalMesh = false;
    for (const ufbx_node* Node : Scene.nodes)
    {
        if (!Node || Seen.Contains(Node)) { Error = TEXT("Invalid node list"); return false; }
        Seen.Add(Node);
        if (!Node->mesh) continue;
        if (LocalMesh && (Node->mesh != LocalMesh || bConvertedLocalMesh)) continue;
        const ufbx_mesh& Mesh = *Node->mesh;
        bool bKnownMesh = false;
        for (const ufbx_mesh* Known : Scene.meshes) bKnownMesh |= Known == &Mesh;
        if (!bKnownMesh || Node->materials.count > 100000
            || (Node->materials.count && !Node->materials.data))
        { Error = TEXT("Invalid node mesh/material reference"); return false; }
        if (size_t(Result.Vertices().Num()) + Mesh.num_vertices > MaxElements
            || size_t(Result.Triangles().Num()) + Mesh.num_triangles > MaxElements / 3)
        { Error = TEXT("Flattened instance geometry exceeds limits"); return false; }
        const ufbx_matrix& World = LocalMesh ? ufbx_identity_matrix : Node->geometry_to_world;
        for (double Value : World.v)
            if (!FMath::IsFinite(Value)) { Error = TEXT("Non-finite node transform"); return false; }
        const double Determinant = ufbx_matrix_determinant(&World);
        if (!FMath::IsFinite(Determinant) || Determinant == 0)
        { Error = TEXT("Singular node transform"); return false; }
        const ufbx_matrix NormalMatrix = ufbx_matrix_for_normals(&World);
        // ufbx already updates face indices during handedness conversion.
        // UE uses the reverse cross product; an instance reflection reverses it again.
        const bool bReverse = Determinant > 0;
        if (!LocalMesh && !Node->materials.count && !Groups.Contains(nullptr)) AddMaterial(nullptr);
        TArray<FVertexID> Vertices;
        Vertices.Reserve(Mesh.num_vertices);
        for (ufbx_vec3 SourcePosition : Mesh.vertices)
        {
            const FVector3f Position = Vector(ufbx_transform_position(&World, SourcePosition));
            if (Position.ContainsNaN()) { Error = TEXT("Non-finite vertex"); return false; }
            const FVertexID Vertex = Result.CreateVertex();
            Positions[Vertex] = Position;
            Vertices.Add(Vertex);
        }
        for (size_t FaceIndex = 0; FaceIndex < Mesh.faces.count; ++FaceIndex)
        {
            const ufbx_face Face = Mesh.faces.data[FaceIndex];
            if (Face.num_indices < 3 || size_t(Face.index_begin) + Face.num_indices > Mesh.num_indices)
            { Error = TEXT("Invalid polygon index range"); return false; }
            if (Mesh.face_hole.count && (FaceIndex >= Mesh.face_hole.count
                || !Mesh.face_hole.data || Mesh.face_hole.data[FaceIndex]))
            { Error = TEXT("Polygon holes are unsupported by the static ufbx bridge"); return false; }
            uint32 MaterialIndex = 0;
            if (Mesh.face_material.count)
            {
                if (!Mesh.face_material.data || FaceIndex >= Mesh.face_material.count)
                { Error = TEXT("Invalid face material array"); return false; }
                MaterialIndex = Mesh.face_material.data[FaceIndex];
            }
            const FPolygonGroupID* Group = nullptr;
            if (LocalMesh)
            {
                if (MaterialIndex >= uint32(LocalGroups.Num()))
                { Error = TEXT("Invalid local face material slot"); return false; }
                Group = &LocalGroups[MaterialIndex];
            }
            else
            {
                if ((Node->materials.count && MaterialIndex >= Node->materials.count)
                    || (!Node->materials.count && MaterialIndex != 0))
                { Error = TEXT("Invalid face material index"); return false; }
                const ufbx_material* Material = Node->materials.count ? Node->materials.data[MaterialIndex] : nullptr;
                Group = Groups.Find(Material);
                if (!Group || (Node->materials.count && !Material))
                { Error = TEXT("Missing instance material"); return false; }
            }
            // Validate all corners before calling the library triangulator.
            for (size_t Corner = Face.index_begin; Corner < size_t(Face.index_begin) + Face.num_indices; ++Corner)
                if (Mesh.vertex_indices.data[Corner] >= Mesh.num_vertices)
                { Error = TEXT("Invalid polygon vertex index"); return false; }
            TArray<uint32> Indices;
            Indices.SetNumUninitialized((Face.num_indices - 2) * 3);
            ufbx_panic Panic = {};
            const uint32 Triangles = ufbx_catch_triangulate_face(&Panic, Indices.GetData(), Indices.Num(), &Mesh, Face);
            if (Panic.did_panic || Triangles != Face.num_indices - 2)
            { Error = TEXT("ufbx triangulation failed"); return false; }
            for (uint32 Triangle = 0; Triangle < Triangles; ++Triangle)
            {
                uint32 Corners[3], VertexIndices[3];
                for (uint32 Corner = 0; Corner < 3; ++Corner)
                {
                    Corners[Corner] = Indices[Triangle * 3 + (bReverse && Corner ? 3 - Corner : Corner)];
                    if (Corners[Corner] < Face.index_begin || Corners[Corner] >= size_t(Face.index_begin) + Face.num_indices)
                    { Error = TEXT("Invalid triangulated index"); return false; }
                    VertexIndices[Corner] = Mesh.vertex_indices.data[Corners[Corner]];
                }
                if (VertexIndices[0] == VertexIndices[1] || VertexIndices[1] == VertexIndices[2]
                    || VertexIndices[0] == VertexIndices[2])
                { Error = TEXT("Repeated triangle vertex"); return false; }
                const FVector3d P0(Positions[Vertices[VertexIndices[0]]]);
                const FVector3d E1 = FVector3d(Positions[Vertices[VertexIndices[1]]]) - P0;
                const FVector3d E2 = FVector3d(Positions[Vertices[VertexIndices[2]]]) - P0;
                const double AreaSquared = FVector3d::CrossProduct(E1, E2).SizeSquared();
                if (!FMath::IsFinite(AreaSquared) || AreaSquared == 0)
                { Error = TEXT("Degenerate triangle"); return false; }
                FVertexInstanceID Instances[3];
                for (uint32 Corner = 0; Corner < 3; ++Corner)
                {
                    ufbx_vec3 SourceNormal;
                    if (!ReadAttribute(Mesh.vertex_normal, Corners[Corner], SourceNormal))
                    { Error = TEXT("Invalid normal index"); return false; }
                    const ufbx_vec3 RawNormal = ufbx_transform_direction(&NormalMatrix, SourceNormal);
                    if (!FMath::IsFinite(RawNormal.x) || !FMath::IsFinite(RawNormal.y) || !FMath::IsFinite(RawNormal.z))
                    { Error = TEXT("Non-finite normal"); return false; }
                    const FVector3f Normal = Vector(ufbx_vec3_normalize(RawNormal));
                    if (Normal.ContainsNaN() || Normal.IsNearlyZero())
                    { Error = TEXT("Invalid normal"); return false; }
                    Instances[Corner] = Result.CreateVertexInstance(Vertices[VertexIndices[Corner]]);
                    Normals[Instances[Corner]] = Normal;
                    ufbx_vec4 SourceColor = {{{1, 1, 1, 1}}};
                    if (Mesh.vertex_color.exists && !ReadAttribute(Mesh.vertex_color, Corners[Corner], SourceColor))
                    { Error = TEXT("Invalid color index"); return false; }
                    const FVector4f Color(SourceColor.x, SourceColor.y, SourceColor.z, SourceColor.w);
                    if (Color.ContainsNaN()) { Error = TEXT("Non-finite color"); return false; }
                    Colors[Instances[Corner]] = Color;
                    for (size_t Channel = 0; Channel < UVChannels; ++Channel)
                    {
                        FVector2f UV = FVector2f::ZeroVector;
                        const ufbx_vertex_vec2* SourceUV = Channel < Mesh.uv_sets.count
                            ? &Mesh.uv_sets.data[Channel].vertex_uv : (Channel == 0 ? &Mesh.vertex_uv : nullptr);
                        if (SourceUV && SourceUV->exists)
                        {
                            ufbx_vec2 Value;
                            if (!ReadAttribute(*SourceUV, Corners[Corner], Value))
                            { Error = TEXT("Invalid UV index"); return false; }
                            UV = FVector2f(Value.x, 1.0 - Value.y);
                        }
                        if (UV.ContainsNaN()) { Error = TEXT("Non-finite UV"); return false; }
                        UVs.Set(Instances[Corner], Channel, UV);
                    }
                }
                Result.CreateTriangle(*Group, MakeArrayView(Instances));
            }
        }
        bConvertedLocalMesh = true;
    }
    if (!Result.Triangles().Num()) { Error = TEXT("No referenced triangles"); return false; }
    Output = MoveTemp(Result);
    return true;
}

struct FTestScene
{
    ufbx_scene Scene = {};
    ufbx_node Node = {}, Other = {};
    ufbx_node* Nodes[2] = {&Node, &Other};
    ufbx_mesh Mesh = {};
    ufbx_mesh* Meshes[1] = {&Mesh};
    ufbx_material Material = {}, OtherMaterial = {};
    ufbx_material* Materials[2] = {&Material, &OtherMaterial};
    ufbx_vec3 Vertices[3] = {{{{0,0,0}}}, {{{1,0,0}}}, {{{0,1,0}}}};
    ufbx_vec3 Normals[3] = {{{{0,0,1}}}, {{{0,0,1}}}, {{{0,0,1}}}};
    ufbx_vec2 UV[3] = {{{{0,0}}}, {{{1,0}}}, {{{0,1}}}};
    ufbx_vec4 Colors[3] = {{{{1,0,0,1}}}, {{{0,1,0,0.5}}}, {{{0,0,1,1}}}};
    uint32 Indices[3] = {0,1,2};
    uint32 FaceMaterial[1] = {0};
    ufbx_face Faces[1] = {{0,3}};
    ufbx_uv_set UVSets[2] = {};

    FTestScene()
    {
        Scene.settings.axes = LoadOptions().target_axes;
        Scene.settings.unit_meters = 0.01;
        Scene.root_node = &Node;
        Scene.nodes = {Nodes, 1};
        Scene.meshes = {Meshes, 1};
        Scene.materials = {Materials, 2};
        Material.name = {"Same", 4};
        OtherMaterial.name = {"same", 4};
        Node.mesh = &Mesh;
        Node.materials = {Materials, 1};
        Node.geometry_to_world = ufbx_identity_matrix;
        Mesh.num_vertices = 3; Mesh.num_indices = 3; Mesh.num_faces = 1; Mesh.num_triangles = 1;
        Mesh.vertices = {Vertices, 3};
        Mesh.vertex_indices = {Indices, 3};
        Mesh.faces = {Faces, 1};
        Mesh.face_material = {FaceMaterial, 1};
        Mesh.vertex_position.exists = true;
        Mesh.vertex_position.values = {Vertices, 3};
        Mesh.vertex_position.indices = {Indices, 3};
        Mesh.vertex_normal.exists = true;
        Mesh.vertex_normal.values = {Normals, 3};
        Mesh.vertex_normal.indices = {Indices, 3};
        Mesh.vertex_uv.exists = true;
        Mesh.vertex_uv.values = {UV, 3};
        Mesh.vertex_uv.indices = {Indices, 3};
        Mesh.vertex_color.exists = true;
        Mesh.vertex_color.values = {Colors, 3};
        Mesh.vertex_color.indices = {Indices, 3};
        UVSets[0].vertex_uv = Mesh.vertex_uv;
        UVSets[1].vertex_uv = Mesh.vertex_uv;
        Mesh.uv_sets = {UVSets, 2};
        Other = Node;
        Other.materials = {Materials + 1, 1};
        Other.geometry_to_world.m03 = 10;
    }
};

bool FacingMatchesNormals(FMeshDescription& Mesh)
{
    FStaticMeshOperations::ComputeTriangleTangentsAndNormals(Mesh, 0.0f);
    FStaticMeshConstAttributes Attributes(Mesh);
    for (FTriangleID Triangle : Mesh.Triangles().GetElementIDs())
        for (FVertexInstanceID Corner : Mesh.GetTriangleVertexInstances(Triangle))
            if (FVector3f::DotProduct(Attributes.GetTriangleNormals()[Triangle],
                Attributes.GetVertexInstanceNormals()[Corner]) < 0.99f) return false;
    return true;
}

const char AxisFixture[] = R"FBX(
; FBX 7.4.0 project file
FBXHeaderExtension: { FBXHeaderVersion: 1003
 FBXVersion: 7400
}
GlobalSettings: {
 Properties70: {
  P: "UpAxis", "int", "Integer", "",1
  P: "UpAxisSign", "int", "Integer", "",1
  P: "FrontAxis", "int", "Integer", "",2
  P: "FrontAxisSign", "int", "Integer", "",-1
  P: "CoordAxis", "int", "Integer", "",0
  P: "CoordAxisSign", "int", "Integer", "",1
  P: "UnitScaleFactor", "double", "Number", "",100
 }
}
Objects: {
 Geometry: 1, "Geometry::Triangle", "Mesh" {
  Vertices: *9 { a: 0,0,0,1,0,0,0,1,0 }
  PolygonVertexIndex: *3 { a: 0,1,-3 }
  LayerElementNormal: 0 {
   MappingInformationType: "ByPolygonVertex"
   ReferenceInformationType: "Direct"
   Normals: *9 { a: 0,0,1,0,0,1,0,0,1 }
  }
  Layer: 0 {
   LayerElement: { Type: "LayerElementNormal" TypedIndex: 0 }
  }
 }
 Model: 2, "Model::Triangle", "Mesh" {
  Properties70: {
   P: "Lcl Translation", "Lcl Translation", "", "A",2,3,4
   P: "Lcl Scaling", "Lcl Scaling", "", "A",1,1,1
   P: "GeometricTranslation", "Vector3D", "Vector", "",0,0,1
  }
 }
}
Connections: {
 C: "OO",1,2
 C: "OO",2,0
}
)FBX";
}

namespace Private
{
ufbx_load_opts StaticLoadOptions() { return LoadOptions(); }

bool ConvertFlattened(const ufbx_scene& Scene, FMeshDescription& Output, FString& Error)
{
    return ConvertStatic(Scene, Output, Error);
}

bool ConvertLocalPayload(const ufbx_scene& Scene, const ufbx_mesh& Mesh,
    const TArray<FString>& SlotKeys, FMeshDescription& Output, FString& Error)
{
    return ConvertStatic(Scene, Output, Error, &Mesh, &SlotKeys);
}
}

bool ImportStatic(const FString& Filename, FMeshDescription& Output, FString& Error)
{
    Error.Reset();
    if (ufbx_source_version != UFBX_HEADER_VERSION)
    { Error = TEXT("ufbx header/library version mismatch"); return false; }
    const ufbx_load_opts Options = LoadOptions();
    ufbx_error LoadError = {};
    ufbx_scene* Scene = ufbx_load_file(TCHAR_TO_UTF8(*Filename), &Options, &LoadError);
    if (!Scene)
    {
        char Description[1024] = {};
        ufbx_format_error(Description, sizeof(Description), &LoadError);
        Error = FString::Printf(TEXT("ufbx load failed: %s"), UTF8_TO_TCHAR(Description));
        return false;
    }
    ON_SCOPE_EXIT { ufbx_free_scene(Scene); };
    return ConvertStatic(*Scene, Output, Error);
}

bool RunSelfTests(int32& Passed, FString& Error, TArray<FCoordinateCheck>& CoordinateChecks)
{
    Passed = 0;
    Error.Reset();
    CoordinateChecks.Reset();
    auto Check = [&](bool Success, const TCHAR* Name)
    {
        if (!Success) { Error = FString::Printf(TEXT("%s: %s"), Name, *Error); return false; }
        ++Passed;
        return true;
    };
    FTestScene Base;
    FMeshDescription Baseline;
    int64 Bytes = 0;
    if (!Check(ConvertStatic(Base.Scene, Baseline, Error) && FacingMatchesNormals(Baseline)
        && CarlaAssimpMesh::CheckMemoryRoundTrip(Baseline, Bytes, Error) && Bytes > 0,
        TEXT("ufbx baseline and real UE round trip"))) return false;
    FStaticMeshConstAttributes Attributes(Baseline);
    const FVertexInstanceID First = *Baseline.VertexInstances().GetElementIDs().begin();
    if (!Check(Attributes.GetVertexInstanceUVs().GetNumChannels() == 2
        && Attributes.GetVertexInstanceUVs().Get(First, 1).Equals(FVector2f(0, 1))
        && Attributes.GetVertexInstanceColors()[First].Equals(FVector4f(1,0,0,1)),
        TEXT("UV channels, V flip and colors"))) return false;
    if (!Check(Attributes.GetPolygonGroupMaterialSlotNames()[FPolygonGroupID(0)]
        != Attributes.GetPolygonGroupMaterialSlotNames()[FPolygonGroupID(1)],
        TEXT("unique material names"))) return false;

    for (double Scale : {1.0, -1.0, 1e-8})
    {
        FTestScene Fixture;
        Fixture.Node.geometry_to_world.m00 = Scale;
        Fixture.Node.geometry_to_world.m11 = FMath::Abs(Scale);
        Fixture.Node.geometry_to_world.m22 = FMath::Abs(Scale);
        FMeshDescription Mesh;
        if (!Check(ConvertStatic(Fixture.Scene, Mesh, Error) && FacingMatchesNormals(Mesh)
            && CarlaAssimpMesh::CheckMemoryRoundTrip(Mesh, Bytes, Error),
            TEXT("mirrored/tiny-scale winding"))) return false;
    }
    {
        FTestScene Fixture;
        Fixture.Vertices[2].z = 1;
        for (ufbx_vec3& N : Fixture.Normals) N = {{{0, -1, 1}}};
        Fixture.Node.geometry_to_world.m00 = 2;
        Fixture.Node.geometry_to_world.m11 = 3;
        Fixture.Node.geometry_to_world.m22 = 4;
        FMeshDescription Mesh;
        if (!Check(ConvertStatic(Fixture.Scene, Mesh, Error) && FacingMatchesNormals(Mesh)
            && FStaticMeshConstAttributes(Mesh).GetVertexInstanceNormals()[FVertexInstanceID(0)]
                .Equals(FVector3f(0, -0.8f, 0.6f), 1e-5f),
            TEXT("nonuniform normal matrix"))) return false;
    }
    {
        FTestScene Fixture;
        Fixture.Scene.nodes.count = 2;
        FMeshDescription Mesh;
        if (!Check(ConvertStatic(Fixture.Scene, Mesh, Error) && Mesh.Vertices().Num() == 6
            && Mesh.Triangles().Num() == 2 && Mesh.ComputeBoundingBox().Max.X == 11
            && Mesh.GetTrianglePolygonGroup(FTriangleID(0)) != Mesh.GetTrianglePolygonGroup(FTriangleID(1))
            && CarlaAssimpMesh::CheckMemoryRoundTrip(Mesh, Bytes, Error),
            TEXT("flattened instances and per-instance materials"))) return false;
    }
    for (bool bRightHanded : {false, true})
    for (bool bMirrorInstance : {false, true})
    {
        const ufbx_load_opts Options = LoadOptions();
        ufbx_error LoadError = {};
        FString Text = UTF8_TO_TCHAR(AxisFixture);
        if (bRightHanded)
            Text = Text.Replace(TEXT("\"FrontAxisSign\", \"int\", \"Integer\", \"\",-1"),
                TEXT("\"FrontAxisSign\", \"int\", \"Integer\", \"\",1"));
        if (bMirrorInstance)
            Text = Text.Replace(TEXT("\"Lcl Scaling\", \"Lcl Scaling\", \"\", \"A\",1,1,1"),
                TEXT("\"Lcl Scaling\", \"Lcl Scaling\", \"\", \"A\",-1,2,3"));
        FTCHARToUTF8 Encoded(*Text);
        ufbx_scene* Scene = ufbx_load_memory(Encoded.Get(), Encoded.Length(), &Options, &LoadError);
        ON_SCOPE_EXIT { if (Scene) ufbx_free_scene(Scene); };
        FMeshDescription Mesh;
        if (!Check(Scene && Scene->meshes.count == 1
            && Scene->meshes.data[0]->reversed_winding == bRightHanded
            && ConvertStatic(*Scene, Mesh, Error), TEXT("real FBX handedness metadata"))) return false;
        const FBox Bounds = Mesh.ComputeBoundingBox();
        const double X = (bRightHanded ? -1 : 1) * (bMirrorInstance ? 700 : 500);
        const bool bFacing = FacingMatchesNormals(Mesh);
        CoordinateChecks.Add({bRightHanded ? 1 : -1, bMirrorInstance,
            Scene->meshes.data[0]->reversed_winding, bFacing, Bounds,
            FStaticMeshConstAttributes(Mesh).GetVertexInstanceNormals()[FVertexInstanceID(0)]});
        if (!Check(Bounds.Min.Equals(FVector(X, bMirrorInstance ? 100 : 200, 300), 1e-3)
            && Bounds.Max.Equals(FVector(X, bMirrorInstance ? 200 : 300, bMirrorInstance ? 500 : 400), 1e-3)
            && bFacing
            && CarlaAssimpMesh::CheckMemoryRoundTrip(Mesh, Bytes, Error),
            TEXT("real FBX axes, mirrored instance winding, cm and geometry transform"))) return false;
    }
    auto Reject = [&](auto Mutation, const TCHAR* Name)
    {
        FTestScene Fixture;
        Mutation(Fixture);
        FMeshDescription Output = Baseline;
        FBufferArchive Before, After;
        Output.Serialize(Before);
        FString Reason;
        const bool bAccepted = ConvertStatic(Fixture.Scene, Output, Reason);
        Output.Serialize(After);
        return Check(!bAccepted && !Reason.IsEmpty()
            && static_cast<const TArray<uint8>&>(Before) == static_cast<const TArray<uint8>&>(After), Name);
    };
    const double Inf = std::numeric_limits<double>::infinity();
    const double NaN = std::numeric_limits<double>::quiet_NaN();
    if (!Reject([](FTestScene& F) { F.Scene.anim_stacks.count = 1; }, TEXT("reject animation"))) return false;
    if (!Reject([](FTestScene& F) { F.Scene.skin_deformers.count = 1; }, TEXT("reject skin"))) return false;
    if (!Reject([](FTestScene& F) { F.Scene.blend_shapes.count = 1; }, TEXT("reject morph"))) return false;
    if (!Reject([](FTestScene& F) { F.Scene.cache_deformers.count = 1; }, TEXT("reject cache"))) return false;
    if (!Reject([](FTestScene& F) { F.Indices[2] = 30; }, TEXT("reject bad vertex index"))) return false;
    if (!Reject([](FTestScene& F) { F.Indices[2] = 1; }, TEXT("reject repeated index"))) return false;
    if (!Reject([](FTestScene& F) { F.Vertices[2] = {{{2,0,0}}}; }, TEXT("reject degenerate"))) return false;
    if (!Reject([](FTestScene& F) { F.FaceMaterial[0] = 2; }, TEXT("reject material index"))) return false;
    if (!Reject([](FTestScene& F) { F.Node.geometry_to_world.m00 = 0; }, TEXT("reject singular"))) return false;
    if (!Reject([&](FTestScene& F) { F.Other.geometry_to_world.m03 = Inf; F.Scene.nodes.count = 2; },
        TEXT("late instance failure preserves Output"))) return false;
    if (!Reject([&](FTestScene& F) { F.Vertices[1].x = NaN; }, TEXT("reject nonfinite vertex"))) return false;
    if (!Reject([&](FTestScene& F) { F.Normals[1].x = Inf; }, TEXT("reject nonfinite normal"))) return false;
    if (!Reject([&](FTestScene& F) { F.Colors[1].w = NaN; }, TEXT("reject nonfinite color"))) return false;
    if (!Reject([&](FTestScene& F) { F.UV[1].x = Inf; }, TEXT("reject nonfinite UV"))) return false;
    if (!Reject([](FTestScene& F) { F.Mesh.vertex_normal.values.count = 1; }, TEXT("reject normal index"))) return false;
    if (!Reject([](FTestScene& F) { F.Faces[0].num_indices = 4; }, TEXT("reject polygon range"))) return false;
    Error.Reset();
    return true;
}
}
