#include <cstdio>
#include <cstring>

#ifdef ALEMBIC_SMOKE_DRIVER

extern "C" int alembic_smoke(const char* mode, const char* path);

int main(int argc, char** argv)
{
    if (argc != 3 || (std::strcmp(argv[1], "write") != 0 && std::strcmp(argv[1], "read") != 0))
    {
        std::fprintf(stderr, "usage: alembic-smoke write|read ARCHIVE\n");
        return 64;
    }
    return alembic_smoke(argv[1], argv[2]);
}

#else

#include <Alembic/AbcCoreOgawa/All.h>
#include <Alembic/AbcGeom/All.h>
#include <Alembic/Util/Config.h>
#include <cmath>
#include <fstream>
#include <stdexcept>
#include <sys/stat.h>

#ifndef _LIBCPP_VERSION
#error "This smoke requires UE libc++"
#endif
#ifdef ALEMBIC_WITH_HDF5
#error "This slice does not enable HDF5"
#endif
static_assert((ALEMBIC_LIBRARY_VERSION) == 10806, "Alembic must be 1.8.6");

namespace Abc = Alembic::Abc;
namespace Geom = Alembic::AbcGeom;
namespace Ogawa = Alembic::AbcCoreOgawa;

static void require(bool value, const char* message)
{
    if (!value)
        throw std::runtime_error(message);
}

static Abc::V3f vertex(int index, int sample)
{
    const Abc::V3f points[] = {{-1, 0, 0}, {2, 0, 0}, {2, 3, 0}, {-1, 3, 0}};
    return points[index] + Abc::V3f(0, 0, float(sample) * 0.5f);
}

static void write_archive(const char* path)
{
    struct stat existing;
    require(lstat(path, &existing) != 0, "refusing to overwrite an archive");
    Abc::OArchive archive(Ogawa::WriteArchive(), path, Abc::ErrorHandler::kThrowPolicy);
    Abc::TimeSampling sampling(0.25, 1.0);
    const auto timeIndex = archive.addTimeSampling(sampling);
    Geom::OXform transform(archive.getTop(), "root", timeIndex);
    Geom::XformSample xform;
    xform.setTranslation(Abc::V3d(5, -2, 4));
    transform.getSchema().set(xform);
    Geom::OPolyMesh mesh(transform, "mesh", timeIndex);
    Abc::OStringProperty label(mesh.getSchema().getUserProperties(), "label");
    label.set("arm64-ogawa-control");
    const Alembic::Util::int32_t indices[] = {0, 1, 2, 0, 2, 3};
    const Alembic::Util::int32_t counts[] = {3, 3};
    for (int sample = 0; sample != 2; ++sample)
    {
        Abc::V3f positions[4];
        for (int i = 0; i != 4; ++i)
            positions[i] = vertex(i, sample);
        mesh.getSchema().set(Geom::OPolyMeshSchema::Sample(
            Abc::P3fArraySample(positions, 4), Abc::Int32ArraySample(indices, 6),
            Abc::Int32ArraySample(counts, 2)));
    }
}

static void read_archive(const char* path)
{
    // Force the Ogawa reader, rather than silently selecting another backend.
    Abc::IArchive archive(Ogawa::ReadArchive(), path, Abc::ErrorHandler::kThrowPolicy);
    require(archive.valid(), "invalid Ogawa archive");
    require(archive.getTop().getNumChildren() == 1, "top-level child count mismatch");
    Geom::IXform transform(archive.getTop(), "root");
    require(transform.valid() && transform.getNumChildren() == 1, "missing transform/mesh hierarchy");
    Geom::XformSample xform;
    transform.getSchema().get(xform);
    require(xform.getTranslation() == Abc::V3d(5, -2, 4), "translation mismatch");
    Geom::IPolyMesh mesh(transform, "mesh");
    require(mesh.valid(), "invalid polygon mesh schema");
    auto& schema = mesh.getSchema();
    require(schema.getNumSamples() == 2, "mesh sample count mismatch");
    Abc::IStringProperty label(schema.getUserProperties(), "label");
    require(label.getValue() == "arm64-ogawa-control", "metadata mismatch");
    const int expectedIndices[] = {0, 1, 2, 0, 2, 3};
    for (int sample = 0; sample != 2; ++sample)
    {
        const double time = schema.getTimeSampling()->getSampleTime(sample);
        require(std::isfinite(time) && std::abs(time - (1.0 + sample * 0.25)) < 1e-12,
                "sample time mismatch");
        Geom::IPolyMeshSchema::Sample value;
        schema.get(value, Abc::ISampleSelector(static_cast<Abc::index_t>(sample)));
        const auto positions = value.getPositions();
        const auto indices = value.getFaceIndices();
        const auto counts = value.getFaceCounts();
        require(positions && indices && counts, "missing mesh arrays");
        require(positions->size() == 4 && indices->size() == 6 && counts->size() == 2,
                "mesh array size mismatch");
        for (int i = 0; i != 4; ++i)
            require((*positions)[i] == vertex(i, sample), "vertex value mismatch");
        for (int i = 0; i != 6; ++i)
            require((*indices)[i] == expectedIndices[i], "topology index mismatch");
        require((*counts)[0] == 3 && (*counts)[1] == 3, "face count mismatch");
    }
}

extern "C" int alembic_smoke(const char* mode, const char* path)
{
    try
    {
        if (std::strcmp(mode, "write") == 0)
        {
            write_archive(path); // All writer objects close before this returns.
            std::puts("Alembic 1.8.6 Ogawa write PASS vertices=4 faces=2 samples=2");
        }
        else
        {
            read_archive(path);
            std::puts("Alembic 1.8.6 Ogawa read PASS vertices=4 faces=2 samples=2");
        }
        return 0;
    }
    catch (const std::exception& error)
    {
        std::fprintf(stderr, "Alembic Ogawa FAIL: %s\n", error.what());
        return 2;
    }
}

#endif
