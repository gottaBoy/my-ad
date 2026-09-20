#include <ufbx.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

static_assert(UFBX_HEADER_VERSION == ufbx_pack_version(0, 23, 0), "Wrong ufbx headers");
static_assert(sizeof(ufbx_real) == sizeof(double), "The probe requires double precision");

namespace {
constexpr const char* Scope = "ufbx static FBX backend only; not Autodesk SDK ABI, UE Editor/Cook or RPC";
constexpr const char* Commit = "fcc5d6ba444cfd3eb80677dba5e37e493941abe5";
constexpr size_t MaxTriangles = 200000;
using Writer = rapidjson::Writer<rapidjson::StringBuffer>;
using ScenePtr = std::unique_ptr<ufbx_scene, decltype(&ufbx_free_scene)>;

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

void number(Writer& out, double value) {
    require(std::isfinite(value), "Non-finite numeric data");
    out.Double(value);
}

void text(Writer& out, ufbx_string value) {
    require(value.length <= std::numeric_limits<rapidjson::SizeType>::max(), "String too large");
    out.String(value.data, static_cast<rapidjson::SizeType>(value.length));
}

void vector(Writer& out, ufbx_vec2 value) {
    out.StartArray(); number(out, value.x); number(out, value.y); out.EndArray();
}
void vector(Writer& out, ufbx_vec3 value) {
    out.StartArray(); number(out, value.x); number(out, value.y); number(out, value.z); out.EndArray();
}
void vector(Writer& out, ufbx_vec4 value) {
    out.StartArray(); number(out, value.x); number(out, value.y); number(out, value.z); number(out, value.w); out.EndArray();
}

void matrix(Writer& out, const ufbx_matrix& value) {
    out.StartArray();
    for (double v : {value.m00, value.m01, value.m02, value.m03,
                     value.m10, value.m11, value.m12, value.m13,
                     value.m20, value.m21, value.m22, value.m23}) number(out, v);
    out.EndArray();
}

template<class Attribute>
void check_attribute(const Attribute& attribute, uint32_t corner) {
    require(attribute.exists && corner < attribute.indices.count, "Missing corner attribute");
    require(attribute.indices.data[corner] < attribute.values.count, "Invalid attribute index");
}

ufbx_vec3 normal_at(const ufbx_mesh& mesh, uint32_t corner) {
    check_attribute(mesh.vertex_normal, corner);
    const ufbx_vec3 n = ufbx_vec3_normalize(ufbx_get_vertex_vec3(&mesh.vertex_normal, corner));
    require(std::isfinite(n.x) && std::isfinite(n.y) && std::isfinite(n.z) &&
            n.x*n.x + n.y*n.y + n.z*n.z > 0.0, "Invalid normal");
    return n;
}

struct Triangle {
    uint32_t corners[3];
    size_t face;
    int64_t material;
};

std::vector<Triangle> triangulate(const ufbx_mesh& mesh) {
    require(mesh.num_vertices && mesh.num_triangles && mesh.num_triangles <= MaxTriangles, "Empty or oversized mesh");
    require(mesh.max_face_triangles <= MaxTriangles, "Oversized polygon");
    require(mesh.vertices.count == mesh.num_vertices && mesh.vertex_indices.count == mesh.num_indices,
            "Inconsistent vertex counts");
    require(mesh.faces.count == mesh.num_faces, "Inconsistent face counts");
    std::vector<uint32_t> indices(mesh.max_face_triangles * 3);
    std::vector<Triangle> triangles;
    for (size_t face_index = 0; face_index < mesh.faces.count; ++face_index) {
        const ufbx_face face = mesh.faces.data[face_index];
        require(face.num_indices >= 3 && uint64_t(face.index_begin) + face.num_indices <= mesh.num_indices,
                "Invalid or unsupported primitive");
        require(!mesh.face_hole.count || !mesh.face_hole.data[face_index], "Face holes are unsupported");
        const uint32_t count = ufbx_triangulate_face(indices.data(), indices.size(), &mesh, face);
        require(count && size_t(count) * 3 <= indices.size(), "Triangulation failed");
        int64_t material = -1;
        if (mesh.face_material.count) {
            require(face_index < mesh.face_material.count, "Missing face material");
            if (mesh.face_material.data[face_index] != UFBX_NO_INDEX) material = mesh.face_material.data[face_index];
        }
        for (uint32_t index = 0; index < count; ++index) {
            Triangle t{{indices[3*index], indices[3*index+1], indices[3*index+2]}, face_index, material};
            for (uint32_t corner : t.corners) {
                require(corner >= face.index_begin && corner < uint64_t(face.index_begin) + face.num_indices,
                        "Triangulator returned an invalid corner");
                require(mesh.vertex_indices.data[corner] < mesh.vertices.count, "Invalid logical vertex");
            }
            const uint32_t a = mesh.vertex_indices.data[t.corners[0]];
            const uint32_t b = mesh.vertex_indices.data[t.corners[1]];
            const uint32_t c = mesh.vertex_indices.data[t.corners[2]];
            require(a != b && b != c && a != c, "Repeated triangle vertex");
            triangles.push_back(t);
        }
    }
    require(triangles.size() == mesh.num_triangles, "Triangulation changed the expected triangle count");
    return triangles;
}

void features(Writer& out, const ufbx_scene* scene) {
    out.Key("features"); out.StartObject();
    out.Key("animation_stacks"); out.Uint64(scene ? scene->anim_stacks.count : 0);
    out.Key("animation_curves"); out.Uint64(scene ? scene->anim_curves.count : 0);
    out.Key("skin_deformers"); out.Uint64(scene ? scene->skin_deformers.count : 0);
    out.Key("blend_deformers"); out.Uint64(scene ? scene->blend_deformers.count : 0);
    out.Key("cache_deformers"); out.Uint64(scene ? scene->cache_deformers.count : 0);
    out.EndObject();
}

void common(Writer& out, const char* file, bool converted, const char* status, const char* error_code, const char* error) {
    out.StartObject();
    out.Key("schema_version"); out.Int(1);
    out.Key("stage"); out.String("ufbx-fbx-static-backend");
    out.Key("scope"); out.String(Scope);
    out.Key("version"); out.String("0.23.0");
    out.Key("commit"); out.String(Commit);
    out.Key("input"); out.String(file);
    out.Key("space"); out.String(converted ? "ue-centimeters" : "source");
    out.Key("status"); out.String(status);
    out.Key("error_code"); out.String(error_code);
    out.Key("error"); out.String(error);
}

struct Report { std::string json; int code; };

Report failure(const char* file, bool converted, const char* status, const char* code,
               const char* error, int exit_code, const ufbx_scene* scene = nullptr) {
    rapidjson::StringBuffer buffer;
    Writer out(buffer);
    common(out, file, converted, status, code, error);
    features(out, scene);
    for (const char* key : {"nodes", "materials", "meshes", "instances"}) {
        out.Key(key); out.StartArray(); out.EndArray();
    }
    out.EndObject();
    return {buffer.GetString(), exit_code};
}

Report inspect(const char* file, bool converted) {
    ufbx_load_opts opts{};
    opts.strict = true;
    opts.file_format = UFBX_FILE_FORMAT_FBX;
    opts.index_error_handling = UFBX_INDEX_ERROR_HANDLING_ABORT_LOADING;
    opts.generate_missing_normals = true;
    opts.normalize_normals = true;
    opts.node_depth_limit = 256;
    opts.temp_allocator.memory_limit = 256u * 1024u * 1024u;
    opts.result_allocator.memory_limit = 256u * 1024u * 1024u;
    opts.geometry_transform_handling = UFBX_GEOMETRY_TRANSFORM_HANDLING_PRESERVE;
    opts.inherit_mode_handling = UFBX_INHERIT_MODE_HANDLING_PRESERVE;
    opts.space_conversion = UFBX_SPACE_CONVERSION_TRANSFORM_ROOT;
    // No external-file loading, animation suppression, baking or manual axis multiplication.
    if (converted) {
        opts.target_unit_meters = 0.01;
        opts.target_axes = {UFBX_COORDINATE_AXIS_POSITIVE_Y, UFBX_COORDINATE_AXIS_POSITIVE_Z,
                            UFBX_COORDINATE_AXIS_NEGATIVE_X};
    }
    ufbx_error error{};
    ScenePtr scene(ufbx_load_file(file, &opts, &error), &ufbx_free_scene);
    if (!scene) {
        char message[4096];
        ufbx_format_error(message, sizeof(message), &error);
        return failure(file, converted, "FAIL", "parse_error", message, 1);
    }
    require(ufbx_source_version == UFBX_HEADER_VERSION, "Header/library version mismatch");
    if (scene->anim_curves.count || scene->skin_deformers.count || scene->blend_deformers.count || scene->cache_deformers.count) {
        return failure(file, converted, "REJECTED", "unsupported_features",
                       "Animation curves, skin, morph and geometry caches are not supported by this static substage", 2, scene.get());
    }
    require(scene->root_node && scene->nodes.count <= 10000, "Invalid or oversized node graph");
    require(ufbx_coordinate_axes_valid(scene->settings.axes), "Missing or invalid source axes");
    require(std::isfinite(scene->settings.unit_meters) && scene->settings.unit_meters > 0.0, "Invalid source unit");
    require(scene->meshes.count > 0, "No static mesh");

    std::unordered_map<const ufbx_mesh*, std::vector<Triangle>> triangles;
    size_t triangle_count = 0, instanced_triangles = 0, instance_count = 0;
    for (const ufbx_mesh* mesh : scene->meshes) {
        auto value = triangulate(*mesh);
        triangle_count += value.size();
        require(triangle_count <= MaxTriangles, "Too many mesh triangles");
        triangles.emplace(mesh, std::move(value));
    }
    for (const ufbx_node* node : scene->nodes) if (node->mesh) {
        instanced_triangles += triangles.at(node->mesh).size();
        require(instanced_triangles <= MaxTriangles, "Too many instanced triangles");
        ++instance_count;
    }
    require(instance_count > 0, "No referenced static geometry");

    rapidjson::StringBuffer buffer;
    Writer out(buffer);
    common(out, file, converted, "PASS", "", "");
    features(out, scene.get());
    out.Key("policy"); out.StartObject();
    out.Key("matrix_layout"); out.String("row-major-3x4; implicit last row 0,0,0,1");
    out.Key("geometry_transforms"); out.String("preserve; geometry_to_world includes geometry_to_node");
    out.Key("inherit_modes"); out.String("preserve; node_to_world is authoritative");
    out.Key("identity"); out.String("ufbx element_id; stable for identical file bytes, not across re-export");
    out.Key("attributes"); out.String("normals, UV and RGBA sets, material names/bindings; no texture or shader conversion");
    out.Key("winding"); out.String("ufbx-returned; not UE-facing triangle order");
    out.Key("uv"); out.String("ufbx source convention; no V flip");
    out.Key("target_axes"); out.StartArray();
    if (converted) { out.Int(2); out.Int(4); out.Int(1); }
    out.EndArray();
    out.Key("target_unit_meters"); number(out, converted ? 0.01 : 0.0);
    out.EndObject();
    out.Key("source_axes"); out.StartArray();
    out.Int(scene->settings.axes.right); out.Int(scene->settings.axes.up); out.Int(scene->settings.axes.front);
    out.EndArray();
    out.Key("source_unit_meters"); number(out, scene->settings.unit_meters);
    out.Key("original_unit_meters"); number(out, scene->settings.original_unit_meters);
    out.Key("fbx_version"); out.Uint(scene->metadata.version);
    out.Key("warnings"); out.Uint64(scene->metadata.warnings.count);
    out.Key("root_id"); out.Uint(scene->root_node->element_id);
    out.Key("counts"); out.StartObject();
    out.Key("nodes"); out.Uint64(scene->nodes.count);
    out.Key("meshes"); out.Uint64(scene->meshes.count);
    out.Key("materials"); out.Uint64(scene->materials.count);
    out.Key("triangles"); out.Uint64(triangle_count);
    out.Key("mesh_instances"); out.Uint64(instance_count);
    out.Key("instanced_triangles"); out.Uint64(instanced_triangles);
    out.EndObject();
    out.Key("materials"); out.StartArray();
    for (const ufbx_material* material : scene->materials) {
        out.StartObject();
        out.Key("id"); out.Uint(material->element_id);
        out.Key("name"); text(out, material->name);
        out.EndObject();
    }
    out.EndArray();
    out.Key("nodes"); out.StartArray();
    for (const ufbx_node* node : scene->nodes) {
        out.StartObject();
        out.Key("id"); out.Uint(node->element_id);
        out.Key("name"); text(out, node->name);
        out.Key("parent"); if (node->parent) out.Uint(node->parent->element_id); else out.Null();
        out.Key("mesh"); if (node->mesh) out.Uint(node->mesh->element_id); else out.Null();
        out.Key("inherit_mode"); out.Int(node->inherit_mode);
        out.Key("has_geometry_transform"); out.Bool(node->has_geometry_transform);
        out.Key("children"); out.StartArray();
        for (const ufbx_node* child : node->children) out.Uint(child->element_id);
        out.EndArray();
        out.Key("materials"); out.StartArray();
        for (const ufbx_material* material : node->materials) out.Uint(material->element_id);
        out.EndArray();
        out.Key("node_to_parent"); matrix(out, node->node_to_parent);
        out.Key("node_to_world"); matrix(out, node->node_to_world);
        out.Key("geometry_to_node"); matrix(out, node->geometry_to_node);
        out.Key("geometry_to_world"); matrix(out, node->geometry_to_world);
        out.EndObject();
    }
    out.EndArray();
    out.Key("meshes"); out.StartArray();
    for (const ufbx_mesh* mesh : scene->meshes) {
        out.StartObject();
        out.Key("id"); out.Uint(mesh->element_id);
        out.Key("name"); text(out, mesh->name);
        out.Key("generated_normals"); out.Bool(mesh->generated_normals);
        out.Key("reversed_winding"); out.Bool(mesh->reversed_winding);
        out.Key("face_count"); out.Uint64(mesh->faces.count);
        out.Key("uv_sets"); out.StartArray();
        for (const ufbx_uv_set& set : mesh->uv_sets) text(out, set.name);
        out.EndArray();
        out.Key("color_sets"); out.StartArray();
        for (const ufbx_color_set& set : mesh->color_sets) text(out, set.name);
        out.EndArray();
        out.Key("positions"); out.StartArray();
        for (ufbx_vec3 position : mesh->vertices) vector(out, position);
        out.EndArray();
        out.Key("triangles"); out.StartArray();
        for (const Triangle& t : triangles.at(mesh)) {
            out.StartObject();
            out.Key("source_face"); out.Uint64(t.face);
            out.Key("material_slot"); out.Int64(t.material);
            out.Key("corners"); out.StartArray();
            for (uint32_t corner : t.corners) {
                out.StartObject();
                out.Key("vertex"); out.Uint(mesh->vertex_indices.data[corner]);
                out.Key("normal"); vector(out, normal_at(*mesh, corner));
                out.Key("uv"); out.StartArray();
                for (const ufbx_uv_set& set : mesh->uv_sets) {
                    check_attribute(set.vertex_uv, corner);
                    vector(out, ufbx_get_vertex_vec2(&set.vertex_uv, corner));
                }
                out.EndArray();
                out.Key("color"); out.StartArray();
                for (const ufbx_color_set& set : mesh->color_sets) {
                    check_attribute(set.vertex_color, corner);
                    vector(out, ufbx_get_vertex_vec4(&set.vertex_color, corner));
                }
                out.EndArray();
                out.EndObject();
            }
            out.EndArray();
            out.EndObject();
        }
        out.EndArray();
        out.EndObject();
    }
    out.EndArray();
    out.Key("instances"); out.StartArray();
    for (const ufbx_node* node : scene->nodes) if (node->mesh) {
        const ufbx_mesh& mesh = *node->mesh;
        const double det = ufbx_matrix_determinant(&node->geometry_to_world);
        require(std::isfinite(det) && det != 0.0, "Singular geometry transform");
        const ufbx_matrix normal_matrix = ufbx_matrix_for_normals(&node->geometry_to_world);
        std::vector<ufbx_vec3> positions;
        double bounds[6] = {INFINITY, INFINITY, INFINITY, -INFINITY, -INFINITY, -INFINITY};
        out.StartObject();
        out.Key("node"); out.Uint(node->element_id);
        out.Key("mesh"); out.Uint(mesh.element_id);
        out.Key("determinant"); number(out, det);
        out.Key("positions_world"); out.StartArray();
        for (ufbx_vec3 position : mesh.vertices) {
            const ufbx_vec3 world = ufbx_transform_position(&node->geometry_to_world, position);
            vector(out, world);
            positions.push_back(world);
            const double values[] = {world.x, world.y, world.z};
            for (size_t axis = 0; axis < 3; ++axis) {
                bounds[axis] = std::min(bounds[axis], values[axis]);
                bounds[axis+3] = std::max(bounds[axis+3], values[axis]);
            }
        }
        out.EndArray();
        out.Key("bounds_world"); out.StartArray();
        for (double value : bounds) number(out, value);
        out.EndArray();
        out.Key("triangle_materials"); out.StartArray();
        for (const Triangle& t : triangles.at(&mesh)) {
            if (t.material < 0) {
                require(node->materials.count == 0, "Material binding is missing");
                out.Null();
            } else {
                require(uint64_t(t.material) < node->materials.count, "Invalid instance material slot");
                out.Uint(node->materials.data[t.material]->element_id);
            }
        }
        out.EndArray();
        out.Key("normals_world"); out.StartArray();
        for (const Triangle& t : triangles.at(&mesh)) {
            const ufbx_vec3 a = positions[mesh.vertex_indices.data[t.corners[0]]];
            const ufbx_vec3 b = positions[mesh.vertex_indices.data[t.corners[1]]];
            const ufbx_vec3 c = positions[mesh.vertex_indices.data[t.corners[2]]];
            const double x = (b.y-a.y)*(c.z-a.z) - (b.z-a.z)*(c.y-a.y);
            const double y = (b.z-a.z)*(c.x-a.x) - (b.x-a.x)*(c.z-a.z);
            const double z = (b.x-a.x)*(c.y-a.y) - (b.y-a.y)*(c.x-a.x);
            const double area2 = x*x + y*y + z*z;
            require(std::isfinite(area2) && area2 > 0.0, "Degenerate transformed triangle");
            for (uint32_t corner : t.corners) {
                const ufbx_vec3 normal = ufbx_vec3_normalize(
                    ufbx_transform_direction(&normal_matrix, normal_at(mesh, corner)));
                require(normal.x*normal.x + normal.y*normal.y + normal.z*normal.z > 0.0, "Zero transformed normal");
                vector(out, normal);
            }
        }
        out.EndArray();
        out.EndObject();
    }
    out.EndArray();
    out.EndObject();
    return {buffer.GetString(), 0};
}
}

int main(int argc, char** argv) {
    if (argc != 3 || (std::strcmp(argv[1], "--source") && std::strcmp(argv[1], "--ue-centimeters"))) {
        std::fprintf(stderr, "usage: ufbx-probe --source|--ue-centimeters FILE.fbx\n");
        return 64;
    }
    const bool converted = !std::strcmp(argv[1], "--ue-centimeters");
    Report report;
    try {
        report = inspect(argv[2], converted);
    } catch (const std::exception& error) {
        report = failure(argv[2], converted, "FAIL", "invalid_scene", error.what(), 1);
    }
    // inspect() has already released the vendor-owned scene, including on rejection.
    if (std::fwrite(report.json.data(), 1, report.json.size(), stdout) != report.json.size() ||
        std::fputc('\n', stdout) == EOF || std::fflush(stdout)) return 3;
    return report.code;
}
