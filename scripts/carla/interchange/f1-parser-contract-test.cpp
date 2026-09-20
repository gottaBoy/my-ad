#include "f1-parser-facade.h"

#include <cstdlib>
#include <iostream>
#include <string>

namespace f1 = carla::interchange::f1;

namespace {
void Expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << "\\n";
        std::exit(1);
    }
}

class RecordingBackend final : public f1::IParserBackend {
public:
    f1::LoadSceneRequest last_load;
    f1::MeshPayloadRequest last_fetch;
    int load_calls = 0;
    int fetch_calls = 0;

    bool LoadScene(const f1::LoadSceneRequest& request, f1::Scene& scene, f1::Error& error) override {
        last_load = request;
        ++load_calls;
        error = f1::Error{};
        scene.source_file = request.source_file;
        scene.mesh_payloads = {{"mesh/body", f1::PayloadKind::StaticMesh},
                                {"mesh/wheel", f1::PayloadKind::StaticMesh}};
        return true;
    }

    bool FetchMeshPayload(const f1::MeshPayloadRequest& request,
                          f1::MeshPayloadResponse& response, f1::Error& error) override {
        last_fetch = request;
        ++fetch_calls;
        error = f1::Error{};
        response.payload_key = request.payload_key;
        response.requested_transform = request.requested_transform;
        response.result_payload_unique_id = request.payload_key + ":payload";
        response.payload_file = "/artifact/" + request.payload_key + ".payload";
        return true;
    }
};
}  // namespace

int main() {
    RecordingBackend backend;
    f1::ParserFacade facade(backend);
    f1::Error error;
    f1::Scene scene;
    const f1::LoadSceneRequest load{
        "/input/scene.fbx", false, true, false, true};
    Expect(facade.LoadScene(load, scene, error), "LoadScene should succeed");
    Expect(error.code == f1::ErrorCode::None, "LoadScene should clear errors");
    Expect(backend.load_calls == 1, "LoadScene should call backend once");
    Expect(backend.last_load.source_file == "/input/scene.fbx", "source file must be preserved");
    Expect(!backend.last_load.convert_scene && backend.last_load.force_front_x_axis
               && !backend.last_load.convert_scene_unit && backend.last_load.keep_fbx_namespace,
           "LoadScene settings must be preserved");
    Expect(scene.mesh_payloads.size() == 2, "LoadScene must preserve multiple mesh keys");
    Expect(scene.mesh_payloads[0].unique_id != scene.mesh_payloads[1].unique_id,
           "mesh payload keys must remain distinct");

    f1::Transform first_transform = f1::Transform::Identity();
    first_transform.values[12] = 10.0;
    f1::MeshPayloadResponse first_response;
    Expect(facade.FetchMeshPayload(scene.mesh_payloads[0], first_transform, first_response, error),
           "first mesh payload should succeed");
    Expect(first_response.payload_key == "mesh/body", "first payload key must be returned");
    Expect(first_response.requested_transform == first_transform,
           "first requested transform must be returned");

    f1::Transform second_transform = f1::Transform::Identity();
    second_transform.values[13] = -3.0;
    f1::MeshPayloadResponse second_response;
    Expect(facade.FetchMeshPayload(scene.mesh_payloads[1], second_transform, second_response, error),
           "second mesh payload should succeed");
    Expect(backend.fetch_calls == 2, "each mesh key must produce one backend request");
    Expect(backend.last_fetch.payload_key == "mesh/wheel", "second payload key must be distinct");
    Expect(backend.last_fetch.requested_transform == second_transform,
           "second requested transform must be exact");
    Expect(first_response.result_payload_unique_id != second_response.result_payload_unique_id,
           "result payload identities must remain distinct");

    const f1::MeshPayloadKey animation{"anim/run", f1::PayloadKind::Animation};
    f1::MeshPayloadResponse unused;
    Expect(!facade.FetchMeshPayload(animation, f1::Transform::Identity(), unused, error),
           "animation must be rejected");
    Expect(error.code == f1::ErrorCode::UnsupportedAnimation,
           "animation must have an explicit unsupported code");
    Expect(error.message.find("animation") != std::string::npos,
           "animation error must be actionable");

    const f1::MeshPayloadKey morph{"mesh/morph", f1::PayloadKind::MorphTarget};
    Expect(!facade.FetchMeshPayload(morph, f1::Transform::Identity(), unused, error),
           "morph target must be rejected");
    Expect(error.code == f1::ErrorCode::UnsupportedMorph,
           "morph target must have an explicit unsupported code");
    Expect(error.message.find("morph") != std::string::npos,
           "morph error must be actionable");
    Expect(backend.fetch_calls == 2, "unsupported payloads must not reach backend");

    f1::LoadSceneRequest invalid;
    Expect(!facade.LoadScene(invalid, scene, error), "empty LoadScene must fail");
    Expect(error.code == f1::ErrorCode::InvalidArgument,
           "empty LoadScene must have an explicit error code");

    std::cout << "F1 parser facade contract PASS\\n";
    return 0;
}
