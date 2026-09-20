#include <cstdio>

#ifdef OPENSUBDIV_SMOKE_DRIVER

extern "C" int opensubdiv_smoke();

int main()
{
    const int code = opensubdiv_smoke();
    if (code != 0)
    {
        std::fprintf(stderr, "OpenSubdiv smoke FAIL check=%d\n", code);
        return code;
    }
    std::puts("OpenSubdiv 3.6.0 osdCPU smoke PASS Catmull-Clark/stencils/libc++");
    return 0;
}

#else

#include <opensubdiv/far/topologyDescriptor.h>
#include <opensubdiv/far/topologyRefinerFactory.h>
#include <opensubdiv/far/stencilTableFactory.h>
#include <opensubdiv/osd/cpuEvaluator.h>
#include <cmath>
#include <memory>
#include <vector>

#ifndef _LIBCPP_VERSION
#error "This smoke requires UE libc++ headers"
#endif
static_assert(OPENSUBDIV_VERSION_NUMBER == 30600, "OpenSubdiv must be 3.6.0");

static bool near(float actual, float expected)
{
    return std::isfinite(actual) && std::abs(actual - expected) < 1e-6f;
}

extern "C" int opensubdiv_smoke()
{
    using namespace OpenSubdiv;
    const int sizes[] = {4};
    const int indices[] = {0, 1, 2, 3};
    const float positions[] = {0,0,0, 2,0,0, 2,2,0, 0,2,0};
    Far::TopologyDescriptor descriptor;
    descriptor.numVertices = 4;
    descriptor.numFaces = 1;
    descriptor.numVertsPerFace = sizes;
    descriptor.vertIndicesPerFace = indices;
    Sdc::Options scheme;
    scheme.SetVtxBoundaryInterpolation(Sdc::Options::VTX_BOUNDARY_EDGE_ONLY);
    Far::TopologyRefinerFactory<Far::TopologyDescriptor>::Options options(
        Sdc::SCHEME_CATMARK, scheme);
    options.validateFullTopology = true;
    std::unique_ptr<Far::TopologyRefiner> refiner(
        Far::TopologyRefinerFactory<Far::TopologyDescriptor>::Create(descriptor, options));
    if (!refiner) return 1;
    Far::TopologyRefiner::UniformOptions uniform(1);
    uniform.fullTopologyInLastLevel = true;
    refiner->RefineUniform(uniform);
    const auto& base = refiner->GetLevel(0);
    const auto& refined = refiner->GetLevel(1);
    if (base.GetNumVertices() != 4 || base.GetNumEdges() != 4 || base.GetNumFaces() != 1
        || refined.GetNumVertices() != 9 || refined.GetNumFaces() != 4)
        return 2;

    Far::StencilTableFactory::Options stencilOptions;
    stencilOptions.generateOffsets = true;
    stencilOptions.generateIntermediateLevels = false;
    std::unique_ptr<const Far::StencilTable> stencils(
        Far::StencilTableFactory::Create(*refiner, stencilOptions));
    if (!stencils || stencils->GetNumStencils() != 9
        || stencils->GetNumControlVertices() != 4)
        return 3;
    const Osd::BufferDescriptor layout(0, 3, 3);
    std::vector<float> values(27, -1000.0f);
    auto evaluate = [&](const float* input) {
        return Osd::CpuEvaluator::EvalStencils(input, layout, values.data(), layout,
            stencils->GetSizes().data(), stencils->GetOffsets().data(),
            stencils->GetControlIndices().data(), stencils->GetWeights().data(), 0, 9);
    };
    if (!evaluate(positions)) return 4;

    // Independent one-level boundary rule: (6*P + neighbor0 + neighbor1) / 8.
    const float corners[][3] = {{0.25f,0.25f,0}, {1.75f,0.25f,0},
                               {1.75f,1.75f,0}, {0.25f,1.75f,0}};
    std::vector<float> expected(27, -2000.0f);
    for (int vertex = 0; vertex < 4; ++vertex)
        for (int axis = 0; axis < 3; ++axis)
            expected[3 * base.GetVertexChildVertex(vertex) + axis] = corners[vertex][axis];
    for (int edge = 0; edge < 4; ++edge)
    {
        const auto endpoints = base.GetEdgeVertices(edge);
        for (int axis = 0; axis < 3; ++axis)
            expected[3 * base.GetEdgeChildVertex(edge) + axis] =
                (positions[3 * endpoints[0] + axis] + positions[3 * endpoints[1] + axis]) / 2;
    }
    const int center = base.GetFaceChildVertex(0);
    expected[3 * center] = expected[3 * center + 1] = 1;
    expected[3 * center + 2] = 0;
    for (int value = 0; value < 27; ++value)
        if (!near(values[value], expected[value])) return 5;
    for (int face = 0; face < 4; ++face)
    {
        const auto vertices = refined.GetFaceVertices(face);
        if (vertices.size() != 4) return 6;
        for (int vertex = 0; vertex < 4; ++vertex)
            if (vertices[vertex] < 0 || vertices[vertex] >= 9) return 7;
    }

    // Reuse the real stencils with changed input, ruling out fixed output.
    const float offset[] = {3, -2, 4};
    float moved[12];
    for (int value = 0; value < 12; ++value)
        moved[value] = positions[value] + offset[value % 3];
    if (!evaluate(moved)) return 8;
    for (int value = 0; value < 27; ++value)
        if (!near(values[value], expected[value] + offset[value % 3])) return 9;
    std::puts("{\"scheme\":\"Catmull-Clark\",\"levels\":1,\"control_vertices\":4,"
              "\"refined_vertices\":9,\"refined_faces\":4,\"stencils\":9,"
              "\"checked_components\":54,\"translated_input\":true}");
    return 0;
}

#endif
