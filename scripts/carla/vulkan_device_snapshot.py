#!/usr/bin/env python3
"""Validate actual Vulkan device snapshots and generate bounded replay config."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import re


CORE_FIELDS = """robustBufferAccess fullDrawIndexUint32 imageCubeArray independentBlend
geometryShader tessellationShader sampleRateShading dualSrcBlend logicOp multiDrawIndirect
drawIndirectFirstInstance depthClamp depthBiasClamp fillModeNonSolid depthBounds wideLines
largePoints alphaToOne multiViewport samplerAnisotropy textureCompressionETC2
textureCompressionASTC_LDR textureCompressionBC occlusionQueryPrecise pipelineStatisticsQuery
vertexPipelineStoresAndAtomics fragmentStoresAndAtomics shaderTessellationAndGeometryPointSize
shaderImageGatherExtended shaderStorageImageExtendedFormats shaderStorageImageMultisample
shaderStorageImageReadWithoutFormat shaderStorageImageWriteWithoutFormat
shaderUniformBufferArrayDynamicIndexing shaderSampledImageArrayDynamicIndexing
shaderStorageBufferArrayDynamicIndexing shaderStorageImageArrayDynamicIndexing
shaderClipDistance shaderCullDistance shaderFloat64 shaderInt64 shaderInt16 shaderResourceResidency
shaderResourceMinLod sparseBinding sparseResidencyBuffer sparseResidencyImage2D sparseResidencyImage3D
sparseResidency2Samples sparseResidency4Samples sparseResidency8Samples sparseResidency16Samples
sparseResidencyAliased variableMultisampleRate inheritedQueries""".split()

# Only typed VkBool32 feature structs are accepted; unknown pNext nodes fail closed.
FEATURE_STRUCTS = {
    1000257000: ("VkPhysicalDeviceBufferDeviceAddressFeatures",
                 "bufferDeviceAddress bufferDeviceAddressCaptureReplay bufferDeviceAddressMultiDevice"),
    1000207000: ("VkPhysicalDeviceTimelineSemaphoreFeatures", "timelineSemaphore"),
    1000297000: ("VkPhysicalDevicePipelineCreationCacheControlFeatures", "pipelineCreationCacheControl"),
    1000276000: ("VkPhysicalDeviceShaderDemoteToHelperInvocationFeatures", "shaderDemoteToHelperInvocation"),
    1000316002: ("VkPhysicalDeviceDescriptorBufferFeaturesEXT",
                 "descriptorBuffer descriptorBufferCaptureReplay descriptorBufferImageLayoutIgnored descriptorBufferPushDescriptors"),
    1000225002: ("VkPhysicalDeviceSubgroupSizeControlFeatures", "subgroupSizeControl computeFullSubgroups"),
    1000261000: ("VkPhysicalDeviceHostQueryResetFeatures", "hostQueryReset"),
    1000161001: ("VkPhysicalDeviceDescriptorIndexingFeatures", """shaderInputAttachmentArrayDynamicIndexing
        shaderUniformTexelBufferArrayDynamicIndexing shaderStorageTexelBufferArrayDynamicIndexing
        shaderUniformBufferArrayNonUniformIndexing shaderSampledImageArrayNonUniformIndexing
        shaderStorageBufferArrayNonUniformIndexing shaderStorageImageArrayNonUniformIndexing
        shaderInputAttachmentArrayNonUniformIndexing shaderUniformTexelBufferArrayNonUniformIndexing
        shaderStorageTexelBufferArrayNonUniformIndexing descriptorBindingUniformBufferUpdateAfterBind
        descriptorBindingSampledImageUpdateAfterBind descriptorBindingStorageImageUpdateAfterBind
        descriptorBindingStorageBufferUpdateAfterBind descriptorBindingUniformTexelBufferUpdateAfterBind
        descriptorBindingStorageTexelBufferUpdateAfterBind descriptorBindingUpdateUnusedWhilePending
        descriptorBindingPartiallyBound descriptorBindingVariableDescriptorCount runtimeDescriptorArray"""),
    1000053001: ("VkPhysicalDeviceMultiviewFeatures",
                 "multiview multiviewGeometryShader multiviewTessellationShader"),
    1000314007: ("VkPhysicalDeviceSynchronization2Features", "synchronization2"),
    1000241000: ("VkPhysicalDeviceSeparateDepthStencilLayoutsFeatures", "separateDepthStencilLayouts"),
    1000221000: ("VkPhysicalDeviceScalarBlockLayoutFeatures", "scalarBlockLayout"),
    1000180000: ("VkPhysicalDeviceShaderAtomicInt64Features", "shaderBufferInt64Atomics shaderSharedInt64Atomics"),
    1000413000: ("VkPhysicalDeviceMaintenance4Features", "maintenance4"),
    1000470000: ("VkPhysicalDeviceMaintenance5FeaturesKHR", "maintenance5"),
    1000234000: ("VkPhysicalDeviceShaderImageAtomicInt64FeaturesEXT",
                 "shaderImageInt64Atomics sparseImageInt64Atomics"),
    1000238000: ("VkPhysicalDeviceMemoryPriorityFeaturesEXT", "memoryPriority"),
    1000150013: ("VkPhysicalDeviceAccelerationStructureFeaturesKHR", """accelerationStructure
        accelerationStructureCaptureReplay accelerationStructureIndirectBuild
        accelerationStructureHostCommands descriptorBindingAccelerationStructureUpdateAfterBind"""),
    1000347000: ("VkPhysicalDeviceRayTracingPipelineFeaturesKHR", """rayTracingPipeline
        rayTracingPipelineShaderGroupHandleCaptureReplay rayTracingPipelineShaderGroupHandleCaptureReplayMixed
        rayTracingPipelineTraceRaysIndirect rayTraversalPrimitiveCulling"""),
    1000348013: ("VkPhysicalDeviceRayQueryFeaturesKHR", "rayQuery"),
    1000481000: ("VkPhysicalDeviceRayTracingPositionFetchFeaturesKHR", "rayTracingPositionFetch"),
    1000328000: ("VkPhysicalDeviceMeshShaderFeaturesEXT",
                 "taskShader meshShader multiviewMeshShader primitiveFragmentShadingRateMeshShader meshShaderQueries"),
    1000156004: ("VkPhysicalDeviceSamplerYcbcrConversionFeatures", "samplerYcbcrConversion"),
    1000201000: ("VkPhysicalDeviceComputeShaderDerivativesFeaturesNV",
                 "computeDerivativeGroupQuads computeDerivativeGroupLinear"),
    1000203000: ("VkPhysicalDeviceFragmentShaderBarycentricFeaturesKHR", "fragmentShaderBarycentric"),
    1000341000: ("VkPhysicalDeviceFaultFeaturesEXT", "deviceFault deviceFaultVendorBinary"),
    1000226003: ("VkPhysicalDeviceFragmentShadingRateFeaturesKHR",
                 "pipelineFragmentShadingRate primitiveFragmentShadingRate attachmentFragmentShadingRate"),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load(path):
    def invalid(value):
        raise ValueError(f"non-finite JSON value: {value}")
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_object, parse_constant=invalid)


def bool_fields(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names),
            f"incomplete or unexpected {label}")
    require(all(type(flag) is bool for flag in value.values()), f"non-boolean {label}")


def extensions(value, count, label):
    require(type(count) is int and 0 <= count <= 128 and isinstance(value, list)
            and len(value) == count, f"invalid {label} inventory")
    require(all(isinstance(name, str) and re.fullmatch(r"VK_[A-Za-z0-9_]+", name)
                for name in value), f"invalid {label} name")
    require(len(set(value)) == len(value), f"duplicate {label}")


def validate(state):
    require(isinstance(state, dict) and state.get("schema_version") == 2
            and type(state.get("schema_version")) is int, "complete schema_version=2 snapshot required")
    require(state.get("capture_complete") is True, "snapshot capture is incomplete")
    require(state.get("capture_errors") == [], "snapshot has capture errors")
    require(state.get("capture_point") == "vkCreateDevice:entry",
            "snapshot must capture actual vkCreateDevice entry arguments")
    instance = state.get("instance")
    require(isinstance(instance, dict), "missing actual instance configuration")
    require(type(instance.get("api_version")) is int
            and (1 << 22 | 3 << 12) <= instance["api_version"] < (2 << 22),
            "Vulkan 1.3+ instance required")
    require(type(instance.get("flags")) is int and instance["flags"] == 0
            and instance.get("pnext_empty") is True
            and type(instance.get("layer_count")) is int and instance["layer_count"] == 0,
            "unsupported instance flags, pNext or layers")
    extensions(instance.get("extensions"), instance.get("extension_count"), "instance extensions")
    require(type(state.get("flags")) is int and state["flags"] == 0
            and type(state.get("layer_count")) is int and state["layer_count"] == 0,
            "unsupported device flags or layers")
    extensions(state.get("device_extensions"), state.get("device_extension_count"), "device extensions")
    bool_fields(state.get("enabled_core_features"), CORE_FIELDS, "enabled core features")
    chain = state.get("feature_chain")
    require(isinstance(chain, list) and len(chain) <= 64
            and state.get("pnext_terminated") is True, "incomplete feature chain")
    seen = set()
    for node in chain:
        require(isinstance(node, dict) and type(node.get("sType")) is int, "invalid pNext node")
        stype = node["sType"]
        require(stype in FEATURE_STRUCTS and stype not in seen, "unknown or duplicate pNext sType")
        seen.add(stype)
        name, fields = FEATURE_STRUCTS[stype]
        require(node.get("type") == name, "pNext type differs from sType schema")
        bool_fields(node.get("features"), fields.split(), f"{name} feature values")
    queues = state.get("queue_create_infos")
    require(isinstance(queues, list) and 1 <= len(queues) <= 16, "missing actual queue-create infos")
    families = set()
    for queue in queues:
        require(isinstance(queue, dict), "invalid queue configuration")
        family, count = queue.get("queueFamilyIndex"), queue.get("queueCount")
        require(type(family) is int and 0 <= family < 64 and family not in families,
                "invalid or duplicate queue family")
        families.add(family)
        require(type(count) is int and 1 <= count <= 64, "invalid queue count")
        require(type(queue.get("flags")) is int and queue["flags"] == 0
                and queue.get("pnext_empty") is True,
                "unsupported queue flags/pNext")
        priorities = queue.get("priorities")
        require(isinstance(priorities, list) and len(priorities) == count
                and all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1
                        for p in priorities), "invalid queue priorities")
    return state


def c_bool_fields(features):
    return ", ".join(f".{key} = {'VK_TRUE' if value else 'VK_FALSE'}"
                     for key, value in sorted(features.items()))


def strings(names):
    return ", ".join(json.dumps(name) for name in names) or "NULL"


def generate_header(state, snapshot_sha256=None):
    validate(state)
    config_hash = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if snapshot_sha256 is None:
        snapshot_sha256 = config_hash
    require(isinstance(snapshot_sha256, str) and re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256),
            "invalid snapshot SHA256")
    text = [
        "/* Generated from validated vkCreateDevice entry arguments. */",
        "#define CARLA_CAPTURED_DEVICE_STATE 1",
        f'#define CARLA_DEVICE_SNAPSHOT_SHA256 "{snapshot_sha256}"',
        f"static VkPhysicalDeviceFeatures captured_core = {{{c_bool_fields(state['enabled_core_features'])}}};",
        f"static const char *captured_extensions[] = {{{strings(state['device_extensions'])}}};",
        f"static const char *captured_instance_extensions[] = {{{strings(state['instance']['extensions'])}}};",
    ]
    chain = state["feature_chain"]
    for i, node in enumerate(chain):
        init = f".sType = (VkStructureType){node['sType']}, {c_bool_fields(node['features'])}"
        text += [f"static {node['type']} captured_node_{i} = {{{init}}};",
                 f"static {node['type']} supported_node_{i} = {{.sType = (VkStructureType){node['sType']}}};"]
    queues = state["queue_create_infos"]
    for i, queue in enumerate(queues):
        priorities = ", ".join(f"{float(p):.9f}f" for p in queue["priorities"])
        text.append(f"static const float captured_priorities_{i}[] = {{{priorities}}};")
    text.append("static VkDeviceQueueCreateInfo captured_queues[] = {")
    for i, queue in enumerate(queues):
        text.append("{.sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,"
                    f".queueFamilyIndex = {queue['queueFamilyIndex']}, .queueCount = {queue['queueCount']},"
                    f".pQueuePriorities = captured_priorities_{i}" + "},")
    text += ["};", """
static int captured_extensions_supported(VkPhysicalDevice gpu, int instance)
{
    uint32_t count = 0;
    VkResult result = instance ? vkEnumerateInstanceExtensionProperties(NULL, &count, NULL)
                              : vkEnumerateDeviceExtensionProperties(gpu, NULL, &count, NULL);
    if (result != VK_SUCCESS || count > 1024) return 0;
    VkExtensionProperties *properties = calloc(count ? count : 1, sizeof(*properties));
    if (!properties) return 0;
    result = instance ? vkEnumerateInstanceExtensionProperties(NULL, &count, properties)
                      : vkEnumerateDeviceExtensionProperties(gpu, NULL, &count, properties);
    const char **names = instance ? captured_instance_extensions : captured_extensions;
""",
        f"    uint32_t required = instance ? {len(state['instance']['extensions'])} : {len(state['device_extensions'])};",
        """    int ok = result == VK_SUCCESS;
    for (uint32_t i = 0; i < required; ++i) {
        int found = 0;
        for (uint32_t j = 0; j < count; ++j)
            if (!strcmp(names[i], properties[j].extensionName)) found = 1;
        if (!found) {
            fprintf(stderr, "Captured extension unavailable: %s\\n", names[i]);
            ok = 0;
        }
    }
    free(properties);
    return ok;
}
static int configure_captured_instance(VkApplicationInfo *app, VkInstanceCreateInfo *info)
{
    if (!captured_extensions_supported(VK_NULL_HANDLE, 1)) return 0;
""",
        f"    app->apiVersion = {state['instance']['api_version']}u;",
        f"    info->enabledExtensionCount = {len(state['instance']['extensions'])};",
        "    info->ppEnabledExtensionNames = captured_instance_extensions;",
        "    return 1;\n}", """
static int configure_captured_device(VkPhysicalDevice gpu, VkDeviceCreateInfo *info, uint32_t *family)
{
    if (!captured_extensions_supported(gpu, 0)) return 0;
    VkPhysicalDeviceFeatures2 supported = {.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
"""]
    for i in range(len(chain)):
        next_node = f"&supported_node_{i + 1}" if i + 1 < len(chain) else "NULL"
        text.append(f"    supported_node_{i}.pNext = {next_node};")
        next_capture = f"&captured_node_{i + 1}" if i + 1 < len(chain) else "NULL"
        text.append(f"    captured_node_{i}.pNext = {next_capture};")
    if chain:
        text.append("    supported.pNext = &supported_node_0;")
    text.append("    vkGetPhysicalDeviceFeatures2(gpu, &supported);")
    checks = [(f"supported.features.{key}", key)
              for key, enabled in state["enabled_core_features"].items() if enabled]
    for i, node in enumerate(chain):
        checks += [(f"supported_node_{i}.{key}", f"{node['type']}.{key}")
                   for key, enabled in node["features"].items() if enabled]
    for expression, label in checks:
        text.append(f'    if (!{expression}) {{ fprintf(stderr, "Captured feature unavailable: {label}\\n"); return 0; }}')
    text += ["""
    uint32_t count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &count, NULL);
    if (!count || count > 64) return 0;
    VkQueueFamilyProperties properties[64];
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &count, properties);
    *family = UINT32_MAX;
"""]
    for i, queue in enumerate(queues):
        family, count = queue["queueFamilyIndex"], queue["queueCount"]
        text.append(f"    if ({family} >= count || properties[{family}].queueCount < {count}) return 0;")
        text.append(f"    if (*family == UINT32_MAX && (properties[{family}].queueFlags & VK_QUEUE_COMPUTE_BIT)) *family = {family};")
    text += ["    if (*family == UINT32_MAX) return 0;",
             "    info->pEnabledFeatures = &captured_core;",
             f"    info->pNext = {'&captured_node_0' if chain else 'NULL'};",
             f"    info->enabledExtensionCount = {len(state['device_extensions'])};",
             "    info->ppEnabledExtensionNames = captured_extensions;",
             f"    info->queueCreateInfoCount = {len(queues)};",
             "    info->pQueueCreateInfos = captured_queues;",
             "    return 1;\n}"]
    return "\n".join(text) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--header", type=Path)
    args = parser.parse_args()
    try:
        state = validate(load(args.input))
        digest = hashlib.sha256(args.input.read_bytes()).hexdigest()
        if args.header:
            args.header.write_text(generate_header(state, digest))
    except (OSError, ValueError) as error:
        parser.exit(2, f"device snapshot rejected: {error}\n")
    print(f"validated device snapshot sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
