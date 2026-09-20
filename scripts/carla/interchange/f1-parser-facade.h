#pragma once

#include <array>
#include <string>
#include <vector>

namespace carla::interchange::f1 {

inline constexpr const char* kTranslatorId = "FBX";

struct Transform {
    std::array<double, 16> values{};
    static Transform Identity();
    bool operator==(const Transform& other) const;
};

enum class PayloadKind {
    StaticMesh,
    Animation,
    MorphTarget,
};

struct MeshPayloadKey {
    std::string unique_id;
    PayloadKind kind = PayloadKind::StaticMesh;
};

struct LoadSceneRequest {
    std::string source_file;
    bool convert_scene = true;
    bool force_front_x_axis = false;
    bool convert_scene_unit = true;
    bool keep_fbx_namespace = false;
};

struct Scene {
    std::string source_file;
    std::vector<MeshPayloadKey> mesh_payloads;
};

struct MeshPayloadRequest {
    std::string payload_key;
    Transform requested_transform = Transform::Identity();
};

struct MeshPayloadResponse {
    std::string result_payload_unique_id;
    std::string payload_file;
    std::string payload_key;
    Transform requested_transform = Transform::Identity();
};

enum class ErrorCode {
    None,
    InvalidArgument,
    BackendFailure,
    UnsupportedAnimation,
    UnsupportedMorph,
};

struct Error {
    ErrorCode code = ErrorCode::None;
    std::string message;
};

class IParserBackend {
public:
    virtual ~IParserBackend() = default;

    virtual bool LoadScene(const LoadSceneRequest& request, Scene& scene, Error& error) = 0;
    virtual bool FetchMeshPayload(const MeshPayloadRequest& request,
                                  MeshPayloadResponse& response, Error& error) = 0;
};

class ParserFacade {
public:
    explicit ParserFacade(IParserBackend& backend);

    bool LoadScene(const LoadSceneRequest& request, Scene& scene, Error& error) const;
    bool FetchMeshPayload(const MeshPayloadKey& key, const Transform& requested_transform,
                          MeshPayloadResponse& response, Error& error) const;

private:
    IParserBackend& backend_;
};

}  // namespace carla::interchange::f1
