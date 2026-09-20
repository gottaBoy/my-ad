#include <assimp/Exporter.hpp>
#include <assimp/Importer.hpp>
#include <assimp/postprocess.h>
#include <assimp/scene.h>
#include <assimp/version.h>
#include <rapidjson/stringbuffer.h>
#include <rapidjson/writer.h>

#include <cmath>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

struct Metrics {
    uint64_t nodes = 0;
    uint64_t vertices = 0;
    uint64_t faces = 0;
    uint64_t bones = 0;
    uint64_t weights = 0;
    uint64_t morph_targets = 0;
    uint64_t animation_channels = 0;
    uint64_t animation_keys = 0;
};

static Metrics Validate(const aiScene &scene) {
    if (!scene.mRootNode || !scene.HasMeshes() || (scene.mFlags & AI_SCENE_FLAGS_INCOMPLETE)) {
        throw std::runtime_error("Scene is incomplete or has no meshes");
    }
    Metrics metrics;
    std::vector<const aiNode *> nodes{scene.mRootNode};
    while (!nodes.empty()) {
        const aiNode *node = nodes.back();
        nodes.pop_back();
        if (!node || ++metrics.nodes > 1000000) throw std::runtime_error("Invalid node graph");
        for (unsigned i = 0; i < node->mNumChildren; ++i) nodes.push_back(node->mChildren[i]);
    }
    for (unsigned i = 0; i < scene.mNumMeshes; ++i) {
        const aiMesh &mesh = *scene.mMeshes[i];
        metrics.vertices += mesh.mNumVertices;
        metrics.faces += mesh.mNumFaces;
        metrics.bones += mesh.mNumBones;
        metrics.morph_targets += mesh.mNumAnimMeshes;
        for (unsigned vertex = 0; vertex < mesh.mNumVertices; ++vertex) {
            const auto &position = mesh.mVertices[vertex];
            if (!std::isfinite(position.x) || !std::isfinite(position.y) || !std::isfinite(position.z)) {
                throw std::runtime_error("Non-finite vertex");
            }
        }
        for (unsigned face = 0; face < mesh.mNumFaces; ++face) {
            const auto &polygon = mesh.mFaces[face];
            for (unsigned index = 0; index < polygon.mNumIndices; ++index) {
                if (polygon.mIndices[index] >= mesh.mNumVertices) throw std::runtime_error("Invalid vertex index");
            }
        }
        for (unsigned bone = 0; bone < mesh.mNumBones; ++bone) {
            const aiBone &skin = *mesh.mBones[bone];
            metrics.weights += skin.mNumWeights;
            for (unsigned weight = 0; weight < skin.mNumWeights; ++weight) {
                const auto &value = skin.mWeights[weight];
                if (value.mVertexId >= mesh.mNumVertices || !std::isfinite(value.mWeight) || value.mWeight < 0) {
                    throw std::runtime_error("Invalid skin weight");
                }
            }
        }
    }
    for (unsigned i = 0; i < scene.mNumAnimations; ++i) {
        const aiAnimation &animation = *scene.mAnimations[i];
        if (!std::isfinite(animation.mDuration) || !std::isfinite(animation.mTicksPerSecond)) {
            throw std::runtime_error("Invalid animation timing");
        }
        metrics.animation_channels += animation.mNumChannels;
        for (unsigned channel = 0; channel < animation.mNumChannels; ++channel) {
            const auto &track = *animation.mChannels[channel];
            metrics.animation_keys += uint64_t(track.mNumPositionKeys) + track.mNumRotationKeys + track.mNumScalingKeys;
        }
    }
    if (!metrics.vertices || !metrics.faces) throw std::runtime_error("Empty geometry");
    return metrics;
}

int main(int argc, char **argv) {
    if (argc != 2 && argc != 4) {
        std::cerr << "Usage: fbx-probe INPUT [fbx|fbxa OUTPUT]\n";
        return 64;
    }
    if (argc == 4 && std::string(argv[2]) != "fbx" && std::string(argv[2]) != "fbxa") return 64;
    try {
        Assimp::Importer importer;
        const aiScene *scene = importer.ReadFile(argv[1], aiProcess_Triangulate | aiProcess_ValidateDataStructure);
        if (!scene) throw std::runtime_error(importer.GetErrorString());
        const Metrics metrics = Validate(*scene);
        if (argc == 4) {
            Assimp::Exporter exporter;
            if (exporter.Export(scene, argv[2], argv[3]) != aiReturn_SUCCESS) {
                throw std::runtime_error(exporter.GetErrorString());
            }
        }
        rapidjson::StringBuffer buffer;
        rapidjson::Writer<rapidjson::StringBuffer> writer(buffer);
        writer.StartObject();
        writer.Key("input"); writer.String(argv[1]);
        writer.Key("version");
        const std::string version = std::to_string(aiGetVersionMajor()) + "." +
            std::to_string(aiGetVersionMinor()) + "." + std::to_string(aiGetVersionPatch());
        writer.String(version.c_str());
        writer.Key("meshes"); writer.Uint(scene->mNumMeshes);
        writer.Key("materials"); writer.Uint(scene->mNumMaterials);
        writer.Key("animations"); writer.Uint(scene->mNumAnimations);
        writer.Key("nodes"); writer.Uint64(metrics.nodes);
        writer.Key("vertices"); writer.Uint64(metrics.vertices);
        writer.Key("faces"); writer.Uint64(metrics.faces);
        writer.Key("bones"); writer.Uint64(metrics.bones);
        writer.Key("weights"); writer.Uint64(metrics.weights);
        writer.Key("morph_targets"); writer.Uint64(metrics.morph_targets);
        writer.Key("animation_channels"); writer.Uint64(metrics.animation_channels);
        writer.Key("animation_keys"); writer.Uint64(metrics.animation_keys);
        writer.Key("exported"); writer.Bool(argc == 4);
        writer.EndObject();
        std::cout << buffer.GetString() << "\n";
    } catch (const std::exception &error) {
        std::cerr << error.what() << "\n";
        return 1;
    }
    return 0;
}
