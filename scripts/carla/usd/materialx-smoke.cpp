#include <cstdio>
#include <cstring>

#ifdef MATERIALX_SMOKE_DRIVER

extern "C" int materialx_smoke(const char* mode, const char* filename, const char* libraries);

int main(int argc, char** argv)
{
    if (argc != 4 || (std::strcmp(argv[1], "write") != 0 && std::strcmp(argv[1], "read") != 0))
    {
        std::fprintf(stderr, "usage: materialx-smoke write|read FILE LIBRARIES\n");
        return 64;
    }
    return materialx_smoke(argv[1], argv[2], argv[3]);
}

#else

#include <MaterialXCore/Document.h>
#include <MaterialXCore/Util.h>
#include <MaterialXFormat/Util.h>
#include <MaterialXFormat/XmlIo.h>
#include <MaterialXGenGlsl/GlslShaderGenerator.h>
#include <MaterialXGenMdl/MdlShaderGenerator.h>
#include <MaterialXGenOsl/OslShaderGenerator.h>
#include <cmath>
#include <stdexcept>
#include <sys/stat.h>

#ifndef _LIBCPP_VERSION
#error "This smoke requires UE libc++ headers"
#endif
static_assert(MATERIALX_MAJOR_VERSION == 1 && MATERIALX_MINOR_VERSION == 38 && MATERIALX_BUILD_VERSION == 5,
              "USD requires MaterialX 1.38.5, not the generic 1.38.10 default");
namespace mx = MaterialX;

static void require(bool value, const std::string& message)
{
    if (!value)
        throw std::runtime_error(message);
}

static void validate(const mx::DocumentPtr& doc)
{
    std::string errors;
    const bool valid = doc->validate(&errors);
    require(valid, "MaterialX document validation failed: " + errors);
}

static mx::DocumentPtr load_resources(const char* path)
{
    auto library = mx::createDocument();
    const auto loaded = mx::loadLibraries({"stdlib", "pbrlib", "bxdf"}, mx::FileSearchPath(path), library);
    require(!loaded.empty(), "no installed MaterialX libraries loaded");
    require(bool(library->getNodeDef("ND_standard_surface_surfaceshader")), "standard_surface nodedef missing");
    require(bool(library->getNodeDef("ND_surfacematerial")), "surfacematerial nodedef missing");
    validate(library);
    return library;
}

static void check_material(const mx::DocumentPtr& doc)
{
    validate(doc);
    auto shader = doc->getNode("surface");
    auto material = doc->getNode("material_control");
    require(bool(shader) && bool(material), "material/shader node missing");
    require(shader->getCategory() == "standard_surface" && shader->getType() == "surfaceshader",
            "wrong surface shader");
    require(material->getCategory() == "surfacematerial" && material->getType() == "material",
            "wrong material node");
    require(material->getConnectedNode("surfaceshader") == shader, "material shader connection mismatch");
    require(doc->getMaterialNodes().size() == 1, "material count mismatch");
    require(bool(shader->getNodeDef()), "unresolved standard_surface nodedef");
    auto color = shader->getInputValue("base_color");
    auto roughness = shader->getInputValue("specular_roughness");
    require(bool(color) && bool(roughness), "material parameters missing");
    require(color->isA<mx::Color3>() && color->asA<mx::Color3>() == mx::Color3(0.25f, 0.5f, 0.75f),
            "base color mismatch");
    require(roughness->isA<float>() && std::isfinite(roughness->asA<float>()) &&
            std::abs(roughness->asA<float>() - 0.35f) < 1e-6f, "roughness mismatch");
}

extern "C" int materialx_smoke(const char* mode, const char* filename, const char* libraries)
{
    try
    {
        require(mx::getVersionString() == "1.38.5", "compiled MaterialX version mismatch");
        require(mx::GlslShaderGenerator::create()->getTarget() == "genglsl", "GLSL generator unavailable");
        require(mx::OslShaderGenerator::create()->getTarget() == "genosl", "OSL generator unavailable");
        require(mx::MdlShaderGenerator::create()->getTarget() == "genmdl", "MDL generator unavailable");
        auto library = load_resources(libraries);
        auto doc = mx::createDocument();
        if (std::strcmp(mode, "write") == 0)
        {
            struct stat existing;
            require(lstat(filename, &existing) != 0, "refusing to overwrite XML document");
            doc->importLibrary(library);
            auto shader = doc->addNode("standard_surface", "surface", "surfaceshader");
            shader->setInputValue("base_color", mx::Color3(0.25f, 0.5f, 0.75f));
            shader->setInputValue("specular_roughness", 0.35f);
            doc->addMaterialNode("material_control", shader);
            check_material(doc);
            mx::XmlWriteOptions options;
            options.writeXIncludeEnable = false;
            mx::writeToXmlFile(doc, filename, &options);
            std::puts("MaterialX 1.38.5 XML write/validate PASS material=material_control shader=standard_surface generators=3");
        }
        else
        {
            mx::readFromXmlFile(doc, filename);
            check_material(doc);
            std::puts("MaterialX 1.38.5 XML read/validate PASS material=material_control shader=standard_surface generators=3");
        }
        return 0;
    }
    catch (const std::exception& error)
    {
        std::fprintf(stderr, "MaterialX XML FAIL: %s\n", error.what());
        return 2;
    }
}

#endif
