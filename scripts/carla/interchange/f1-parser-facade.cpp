#include "f1-parser-facade.h"

#include <sstream>
#include <unordered_set>

namespace carla::interchange::f1 {

namespace {
void ClearError(Error& error) {
    error.code = ErrorCode::None;
    error.message.clear();
}

bool Fail(Error& error, ErrorCode code, const char* message) {
    error.code = code;
    error.message = message;
    return false;
}
}  // namespace

Transform Transform::Identity() {
    Transform transform;
    transform.values[0] = 1.0;
    transform.values[5] = 1.0;
    transform.values[10] = 1.0;
    transform.values[15] = 1.0;
    return transform;
}

bool Transform::operator==(const Transform& other) const {
    return values == other.values;
}

ParserFacade::ParserFacade(IParserBackend& backend)
    : backend_(backend) {}

bool ParserFacade::LoadScene(const LoadSceneRequest& request, Scene& scene, Error& error) const {
    ClearError(error);
    scene = Scene{};
    if (request.source_file.empty()) {
        return Fail(error, ErrorCode::InvalidArgument, "LoadScene requires a source file");
    }
    if (!backend_.LoadScene(request, scene, error)) {
        if (error.code == ErrorCode::None) {
            error.code = ErrorCode::BackendFailure;
            error.message = "parser backend rejected LoadScene";
        }
        return false;
    }
    if (scene.source_file.empty()) {
        return Fail(error, ErrorCode::BackendFailure, "parser backend returned no source file");
    }
    std::unordered_set<std::string> keys;
    for (const MeshPayloadKey& payload : scene.mesh_payloads) {
        if (payload.unique_id.empty() || !keys.insert(payload.unique_id).second) {
            return Fail(error, ErrorCode::BackendFailure, "parser backend returned duplicate or empty mesh payload key");
        }
    }
    return true;
}

bool ParserFacade::FetchMeshPayload(const MeshPayloadKey& key,
                                     const Transform& requested_transform,
                                     MeshPayloadResponse& response, Error& error) const {
    ClearError(error);
    response = MeshPayloadResponse{};
    if (key.unique_id.empty()) {
        return Fail(error, ErrorCode::InvalidArgument, "FetchMeshPayload requires a payload key");
    }
    if (key.kind == PayloadKind::Animation) {
        return Fail(error, ErrorCode::UnsupportedAnimation,
                    "F1 static facade does not support animation payloads");
    }
    if (key.kind == PayloadKind::MorphTarget) {
        return Fail(error, ErrorCode::UnsupportedMorph,
                    "F1 static facade does not support morph-target payloads");
    }
    const MeshPayloadRequest request{key.unique_id, requested_transform};
    if (!backend_.FetchMeshPayload(request, response, error)) {
        if (error.code == ErrorCode::None) {
            error.code = ErrorCode::BackendFailure;
            error.message = "parser backend rejected FetchMeshPayload";
        }
        return false;
    }
    if (response.result_payload_unique_id.empty() || response.payload_file.empty()
        || response.payload_key != key.unique_id
        || !(response.requested_transform == requested_transform)) {
        return Fail(error, ErrorCode::BackendFailure,
                    "parser backend returned an incomplete mesh payload response");
    }
    return true;
}

}  // namespace carla::interchange::f1
