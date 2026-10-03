#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include "spirv_reflect.h"
#include <stdbool.h>
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef CARLA_USE_DEVICE_SNAPSHOT
#include "captured-device.h"
#endif

#ifndef __aarch64__
#error Native ARM64 is required
#endif

enum { MAX_SETS = 8, MAX_BINDINGS = 64, SMOKE_WORDS = 64 };

#define CHECK(call) do { \
    VkResult result_ = (call); \
    if (result_ != VK_SUCCESS) { \
        fprintf(stderr, "%s failed: %d\n", #call, (int)result_); \
        goto cleanup; \
    } \
} while (0)

static double seconds(void)
{
    struct timespec stamp;
    clock_gettime(CLOCK_MONOTONIC, &stamp);
    return (double)stamp.tv_sec + (double)stamp.tv_nsec / 1e9;
}

static void json_string(const char *text)
{
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p == '"' || *p == '\\') printf("\\%c", *p);
        else if (*p < 32) printf("\\u%04x", *p);
        else putchar(*p);
    }
    putchar('"');
}

static uint32_t memory_type(VkPhysicalDevice gpu, uint32_t bits)
{
    VkPhysicalDeviceMemoryProperties properties;
    vkGetPhysicalDeviceMemoryProperties(gpu, &properties);
    VkMemoryPropertyFlags required = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                                     VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    for (uint32_t i = 0; i < properties.memoryTypeCount; ++i) {
        if ((bits & (1u << i)) &&
            (properties.memoryTypes[i].propertyFlags & required) == required)
            return i;
    }
    return UINT32_MAX;
}

static VkDescriptorType descriptor_type(SpvReflectDescriptorType type)
{
    switch (type) {
    case SPV_REFLECT_DESCRIPTOR_TYPE_SAMPLER: return VK_DESCRIPTOR_TYPE_SAMPLER;
    case SPV_REFLECT_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER: return VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    case SPV_REFLECT_DESCRIPTOR_TYPE_SAMPLED_IMAGE: return VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE;
    case SPV_REFLECT_DESCRIPTOR_TYPE_STORAGE_IMAGE: return VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
    case SPV_REFLECT_DESCRIPTOR_TYPE_UNIFORM_TEXEL_BUFFER: return VK_DESCRIPTOR_TYPE_UNIFORM_TEXEL_BUFFER;
    case SPV_REFLECT_DESCRIPTOR_TYPE_STORAGE_TEXEL_BUFFER: return VK_DESCRIPTOR_TYPE_STORAGE_TEXEL_BUFFER;
    case SPV_REFLECT_DESCRIPTOR_TYPE_UNIFORM_BUFFER: return VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
    case SPV_REFLECT_DESCRIPTOR_TYPE_STORAGE_BUFFER: return VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    case SPV_REFLECT_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC: return VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
    case SPV_REFLECT_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC: return VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC;
    case SPV_REFLECT_DESCRIPTOR_TYPE_INPUT_ATTACHMENT: return VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT;
    case SPV_REFLECT_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR:
        return VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
    default: return VK_DESCRIPTOR_TYPE_MAX_ENUM;
    }
}

typedef struct {
    bool active;
    uint32_t set_count;
    uint32_t binding_counts[MAX_SETS];
    VkDescriptorSetLayoutBinding bindings[MAX_SETS][MAX_BINDINGS];
    uint32_t push_count;
    uint32_t pipeline_flags;
    uint32_t required_subgroup_size;
} LayoutOverride;

typedef struct {
    VkShaderModule module;
    VkPipelineLayout layout;
    VkPipeline pipeline;
    VkDescriptorSetLayout sets[MAX_SETS];
    uint32_t set_count;
} HistoryPipeline;

static VkDescriptorType descriptor_type_name(const char *name)
{
    if (!strcmp(name, "sampler")) return VK_DESCRIPTOR_TYPE_SAMPLER;
    if (!strcmp(name, "combined_image_sampler")) return VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
    if (!strcmp(name, "sampled_image")) return VK_DESCRIPTOR_TYPE_SAMPLED_IMAGE;
    if (!strcmp(name, "storage_image")) return VK_DESCRIPTOR_TYPE_STORAGE_IMAGE;
    if (!strcmp(name, "uniform_texel_buffer")) return VK_DESCRIPTOR_TYPE_UNIFORM_TEXEL_BUFFER;
    if (!strcmp(name, "storage_texel_buffer")) return VK_DESCRIPTOR_TYPE_STORAGE_TEXEL_BUFFER;
    if (!strcmp(name, "uniform_buffer")) return VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
    if (!strcmp(name, "uniform_buffer_dynamic")) return VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
    if (!strcmp(name, "storage_buffer")) return VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    if (!strcmp(name, "storage_buffer_dynamic")) return VK_DESCRIPTOR_TYPE_STORAGE_BUFFER_DYNAMIC;
    if (!strcmp(name, "input_attachment")) return VK_DESCRIPTOR_TYPE_INPUT_ATTACHMENT;
    return VK_DESCRIPTOR_TYPE_MAX_ENUM;
}

static int parse_layout_override(const char *path, LayoutOverride *override)
{
    FILE *file = fopen(path, "r");
    if (!file) return 0;
    char line[256];
    override->active = true;
    while (fgets(line, sizeof(line), file)) {
        char *cursor = line;
        while (isspace((unsigned char)*cursor)) ++cursor;
        if (!*cursor || *cursor == '#') continue;
        char type[64], stage[32];
        uint32_t set, binding, count;
        if (sscanf(cursor, "set_count=%u", &set) == 1) {
            override->set_count = set;
            continue;
        }
        if (sscanf(cursor, "push_constants=%u", &set) == 1) {
            override->push_count = set;
            continue;
        }
        if (sscanf(cursor, "pipeline_flags=%u", &set) == 1) {
            override->pipeline_flags = set;
            continue;
        }
        if (sscanf(cursor, "required_subgroup_size=%u", &set) == 1) {
            override->required_subgroup_size = set;
            continue;
        }
        if (sscanf(cursor, "binding %u %u %63s %u %31s",
                   &set, &binding, type, &count, stage) == 5) {
            if (set >= MAX_SETS || binding >= MAX_BINDINGS ||
                override->binding_counts[set] >= MAX_BINDINGS ||
                !count || strcmp(stage, "compute")) {
                fclose(file);
                return 0;
            }
            VkDescriptorType descriptor = descriptor_type_name(type);
            if (descriptor == VK_DESCRIPTOR_TYPE_MAX_ENUM) {
                fclose(file);
                return 0;
            }
            VkDescriptorSetLayoutBinding *output =
                &override->bindings[set][override->binding_counts[set]++];
            *output = (VkDescriptorSetLayoutBinding) {
                .binding = binding,
                .descriptorType = descriptor,
                .descriptorCount = count,
                .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
            };
            continue;
        }
        fclose(file);
        return 0;
    }
    fclose(file);
    if (!override->set_count || override->set_count > MAX_SETS) return 0;
    for (uint32_t set = 0; set < override->set_count; ++set) {
        if (!override->binding_counts[set]) return 0;
    }
    return 1;
}

static int read_file(const char *path, void **data_out, size_t *size_out)
{
    FILE *file = fopen(path, "rb");
    if (!file || fseek(file, 0, SEEK_END)) {
        if (file) fclose(file);
        return 0;
    }
    long size = ftell(file);
    if (size < 20 || size > 16 * 1024 * 1024 || size % 4) {
        fclose(file);
        return 0;
    }
    rewind(file);
    void *data = malloc((size_t)size);
    if (!data || fread(data, 1, (size_t)size, file) != (size_t)size) {
        free(data);
        fclose(file);
        return 0;
    }
    fclose(file);
    *data_out = data;
    *size_out = (size_t)size;
    return 1;
}

static int create_history_pipeline(
    VkDevice device, VkPipelineCache cache, const char *shader_path,
    const char *entry_name, const char *layout_path, HistoryPipeline *history)
{
    void *shader_bytes = NULL;
    size_t shader_size = 0;
    LayoutOverride layout_override = {0};
    if (!read_file(shader_path, &shader_bytes, &shader_size) ||
        !parse_layout_override(layout_path, &layout_override)) {
        free(shader_bytes);
        return 0;
    }

    for (uint32_t set = 0; set < layout_override.set_count; ++set) {
        const VkDescriptorSetLayoutCreateInfo info = {
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
            .bindingCount = layout_override.binding_counts[set],
            .pBindings = layout_override.bindings[set],
        };
        if (vkCreateDescriptorSetLayout(device, &info, NULL, &history->sets[set]) != VK_SUCCESS) {
            free(shader_bytes);
            return 0;
        }
    }
    history->set_count = layout_override.set_count;

    const VkPipelineLayoutCreateInfo layout_info = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = history->set_count,
        .pSetLayouts = history->sets,
    };
    if (vkCreatePipelineLayout(device, &layout_info, NULL, &history->layout) != VK_SUCCESS) {
        free(shader_bytes);
        return 0;
    }
    const VkShaderModuleCreateInfo module_info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = shader_size,
        .pCode = shader_bytes,
    };
    if (vkCreateShaderModule(device, &module_info, NULL, &history->module) != VK_SUCCESS) {
        free(shader_bytes);
        return 0;
    }
    const VkComputePipelineCreateInfo pipeline_info = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
            .stage = VK_SHADER_STAGE_COMPUTE_BIT,
            .module = history->module,
            .pName = entry_name,
        },
        .layout = history->layout,
        .flags = layout_override.pipeline_flags,
    };
    fprintf(stderr, "BEGIN history-pipeline entry=%s bytes=%zu\n", entry_name, shader_size);
    VkResult result = vkCreateComputePipelines(
        device, cache, 1, &pipeline_info, NULL, &history->pipeline);
    fprintf(stderr, "END history-pipeline entry=%s result=%d\n", entry_name, (int)result);
    free(shader_bytes);
    return result == VK_SUCCESS;
}

static int load_history_file(
    const char *path, VkDevice device, VkPipelineCache cache,
    HistoryPipeline *history, uint32_t *history_count)
{
    FILE *file = fopen(path, "r");
    if (!file) return 0;
    char line[4096];
    while (fgets(line, sizeof(line), file)) {
        char *cursor = line;
        while (isspace((unsigned char)*cursor)) ++cursor;
        if (!*cursor || *cursor == '#') continue;
        char *first = strchr(cursor, '|');
        char *second = first ? strchr(first + 1, '|') : NULL;
        if (!first || !second || *history_count >= 16) {
            fclose(file);
            return 0;
        }
        *first = '\0';
        *second = '\0';
        char *entry = first + 1;
        char *layout = second + 1;
        layout[strcspn(layout, "\r\n")] = '\0';
        if (!*cursor || !*entry || !*layout ||
            !create_history_pipeline(device, cache, cursor, entry, layout,
                                     &history[(*history_count)++])) {
            fclose(file);
            return 0;
        }
    }
    fclose(file);
    return *history_count > 0;
}

int main(int argc, char **argv)
{
    if (argc < 5 ||
        (strcmp(argv[2], "lavapipe") && strcmp(argv[2], "gb10")) ||
        (strcmp(argv[4], "create") && strcmp(argv[4], "smoke"))) {
        fprintf(stderr, "Usage: vulkan-compute-replay SHADER lavapipe|gb10 ENTRY|- create|smoke [--ue-layout FILE] [--pipeline-cache FILE 0|1] [--history FILE]\n");
        return 64;
    }
    const char *layout_file = NULL, *cache_file = NULL;
    const char *history_file = NULL;
    VkPipelineCacheCreateFlags cache_flags = 0;
    for (int i = 5; i < argc;) {
        if (!strcmp(argv[i], "--ue-layout") && !layout_file && i + 1 < argc) {
            layout_file = argv[i + 1];
            i += 2;
        } else if (!strcmp(argv[i], "--pipeline-cache") && !cache_file && i + 2 < argc &&
                   (!strcmp(argv[i + 2], "0") || !strcmp(argv[i + 2], "1"))) {
            cache_file = argv[i + 1];
            cache_flags = !strcmp(argv[i + 2], "1") ? 1 : 0;
            i += 3;
        } else if (!strcmp(argv[i], "--history") && !history_file && i + 1 < argc) {
            history_file = argv[i + 1];
            i += 2;
        } else {
            fprintf(stderr, "Invalid or duplicate replay option: %s\n", argv[i]);
            return 64;
        }
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    bool smoke = strcmp(argv[4], "smoke") == 0;
    LayoutOverride layout_override = {0};
    int code = 1;
    void *shader_bytes = NULL, *mapped = NULL;
    void *cache_bytes = NULL;
    size_t cache_size = 0;
    FILE *input = NULL;
    SpvReflectShaderModule reflection = {0};
    bool reflected = false;
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkShaderModule module = VK_NULL_HANDLE;
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkPipelineCache pipeline_cache = VK_NULL_HANDLE;
    VkDescriptorSetLayout sets[MAX_SETS] = {0};
    uint32_t set_count = 0;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkDescriptorPool descriptors = VK_NULL_HANDLE;
    VkCommandPool commands = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    HistoryPipeline history[16] = {0};
    uint32_t history_count = 0;

    input = fopen(argv[1], "rb");
    if (!input || fseek(input, 0, SEEK_END)) goto cleanup;
    long size = ftell(input);
    if (size < 20 || size > 16 * 1024 * 1024 || size % 4) {
        fprintf(stderr, "Invalid SPIR-V size\n");
        goto cleanup;
    }
    rewind(input);
    shader_bytes = malloc((size_t)size);
    if (!shader_bytes || fread(shader_bytes, 1, (size_t)size, input) != (size_t)size)
        goto cleanup;
    fclose(input);
    input = NULL;
    SpvReflectResult reflect_result = spvReflectCreateShaderModule(
        (size_t)size, shader_bytes, &reflection, 0);
    if (reflect_result != SPV_REFLECT_RESULT_SUCCESS) {
        fprintf(stderr, "SPIRV-Reflect failed: %d\n", (int)reflect_result);
        goto cleanup;
    }
    reflected = true;
    if (reflection.entry_point_count != 1) {
        fprintf(stderr, "Exactly one compute entry point is required\n");
        goto cleanup;
    }
    const char *entry_name = strcmp(argv[3], "-") ? argv[3] : reflection.entry_point_name;
    const SpvReflectEntryPoint *entry = spvReflectGetEntryPoint(&reflection, entry_name);
    if (!entry || entry->shader_stage != SPV_REFLECT_SHADER_STAGE_COMPUTE_BIT) {
        fprintf(stderr, "Entry point must exist and be compute\n");
        goto cleanup;
    }
    if (layout_file && !parse_layout_override(layout_file, &layout_override)) {
        fprintf(stderr, "Invalid UE layout override: %s\n", layout_file);
        goto cleanup;
    }
    VkDescriptorSetLayoutBinding bindings[MAX_SETS][MAX_BINDINGS] = {{{0}}};
    uint32_t binding_counts[MAX_SETS] = {0};
    for (uint32_t s = 0; s < entry->descriptor_set_count; ++s) {
        const SpvReflectDescriptorSet *set = &entry->descriptor_sets[s];
        if (set->set >= MAX_SETS || set->binding_count > MAX_BINDINGS) {
            fprintf(stderr, "Descriptor layout exceeds probe limits\n");
            goto cleanup;
        }
        if (set_count <= set->set) set_count = set->set + 1;
        for (uint32_t b = 0; b < set->binding_count; ++b) {
            const SpvReflectDescriptorBinding *binding = set->bindings[b];
            VkDescriptorType vk_type = descriptor_type(binding->descriptor_type);
            if (!binding->count || binding->count > 1024 ||
                vk_type == VK_DESCRIPTOR_TYPE_MAX_ENUM) {
                fprintf(stderr, "Unsupported/runtime descriptor type or count\n");
                goto cleanup;
            }
            for (uint32_t d = 0; d < binding->array.dims_count; ++d) {
                if (!binding->array.dims[d]) {
                    fprintf(stderr, "Runtime descriptor arrays are not supported\n");
                    goto cleanup;
                }
            }
            bindings[set->set][b] = (VkDescriptorSetLayoutBinding) {
                .binding = binding->binding,
                .descriptorType = vk_type,
                .descriptorCount = binding->count,
                .stageFlags = VK_SHADER_STAGE_COMPUTE_BIT,
            };
        }
        binding_counts[set->set] = set->binding_count;
    }
    uint32_t push_count = 0;
    if (spvReflectEnumerateEntryPointPushConstantBlocks(
            &reflection, entry_name, &push_count, NULL) != SPV_REFLECT_RESULT_SUCCESS ||
        push_count > 1) {
        fprintf(stderr, "Unsupported push constant blocks\n");
        goto cleanup;
    }
    SpvReflectBlockVariable *push = NULL;
    VkPushConstantRange push_range = {.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT};
    if (push_count) {
        if (spvReflectEnumerateEntryPointPushConstantBlocks(
                &reflection, entry_name, &push_count, &push) != SPV_REFLECT_RESULT_SUCCESS)
            goto cleanup;
        push_range.offset = push->offset;
        push_range.size = push->size;
        if (push_range.offset % 4 || push_range.size % 4 || !push_range.size) goto cleanup;
    }
    if (layout_override.active) {
        set_count = layout_override.set_count;
        memcpy(binding_counts, layout_override.binding_counts, sizeof(binding_counts));
        memcpy(bindings, layout_override.bindings, sizeof(bindings));
        push_count = layout_override.push_count;
        memset(&push_range, 0, sizeof(push_range));
        push_range.stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    if (smoke && (set_count != 1 || binding_counts[0] != 1 || push_count ||
                  bindings[0][0].binding != 0 ||
                  bindings[0][0].descriptorType != VK_DESCRIPTOR_TYPE_STORAGE_BUFFER ||
                  bindings[0][0].descriptorCount != 1)) {
        fprintf(stderr, "Smoke requires only set=0 binding=0 storage buffer\n");
        goto cleanup;
    }

    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "CARLA isolated compute baseline",
        .apiVersion = VK_API_VERSION_1_3,
    };
    VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app,
    };
#ifdef CARLA_USE_DEVICE_SNAPSHOT
    if (!configure_captured_instance(&app, &instance_info)) goto cleanup;
#endif
    CHECK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t gpu_count = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &gpu_count, NULL));
    if (!gpu_count || gpu_count > 32) goto cleanup;
    VkPhysicalDevice gpus[32], gpu = VK_NULL_HANDLE;
    CHECK(vkEnumeratePhysicalDevices(instance, &gpu_count, gpus));
    VkPhysicalDeviceProperties properties = {0};
    for (uint32_t i = 0; i < gpu_count; ++i) {
        vkGetPhysicalDeviceProperties(gpus[i], &properties);
        if ((!strcmp(argv[2], "gb10") && properties.vendorID == 0x10de &&
             !strcmp(properties.deviceName, "NVIDIA GB10")) ||
            (!strcmp(argv[2], "lavapipe") && properties.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU &&
             strstr(properties.deviceName, "llvmpipe"))) {
            gpu = gpus[i];
            break;
        }
    }
    if (!gpu || properties.apiVersion < VK_API_VERSION_1_3 ||
        set_count > properties.limits.maxBoundDescriptorSets ||
        push_range.offset + push_range.size > properties.limits.maxPushConstantsSize) {
        fprintf(stderr, "Expected backend, Vulkan 1.3 or required layout limits unavailable\n");
        goto cleanup;
    }
    uint32_t family = UINT32_MAX;
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
#ifdef CARLA_USE_DEVICE_SNAPSHOT
    if (!configure_captured_device(gpu, &device_info, &family)) {
        fprintf(stderr, "Captured device configuration is not supported on this backend\n");
        goto cleanup;
    }
#else
    uint32_t queue_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_count, NULL);
    if (!queue_count || queue_count > 64) goto cleanup;
    VkQueueFamilyProperties families[64];
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_count, families);
    for (uint32_t i = 0; i < queue_count; ++i) {
        if (families[i].queueCount && (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT)) {
            family = i;
            break;
        }
    }
    if (family == UINT32_MAX) goto cleanup;
    VkPhysicalDeviceVulkan13Features core13 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES,
    };
    VkPhysicalDeviceVulkan12Features core12 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES, .pNext = &core13,
    };
    VkPhysicalDeviceVulkan11Features core11 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES, .pNext = &core12,
    };
    VkPhysicalDeviceFeatures2 features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &core11,
    };
    vkGetPhysicalDeviceFeatures2(gpu, &features);
    const float priority = 1.0f;
    const VkDeviceQueueCreateInfo queue_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = family,
        .queueCount = 1, .pQueuePriorities = &priority,
    };
    device_info = (VkDeviceCreateInfo) {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &features,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
    };
#endif
    fprintf(stderr, "BEGIN device-create backend=%s\n", argv[2]);
    CHECK(vkCreateDevice(gpu, &device_info, NULL, &device));
    if (cache_file) {
        input = fopen(cache_file, "rb");
        if (!input || fseek(input, 0, SEEK_END)) goto cleanup;
        long bytes = ftell(input);
        if (bytes < 0 || bytes > 64 * 1024 * 1024) {
            fprintf(stderr, "Invalid pipeline cache size\n");
            goto cleanup;
        }
        cache_size = (size_t)bytes;
        rewind(input);
        if (cache_size) {
            cache_bytes = malloc(cache_size);
            if (!cache_bytes || fread(cache_bytes, 1, cache_size, input) != cache_size)
                goto cleanup;
            VkPipelineCacheHeaderVersionOne header;
            if (cache_size < sizeof(header)) {
                fprintf(stderr, "Pipeline cache header is truncated\n");
                goto cleanup;
            }
            memcpy(&header, cache_bytes, sizeof(header));
            if (header.headerSize != sizeof(header) ||
                header.headerVersion != VK_PIPELINE_CACHE_HEADER_VERSION_ONE ||
                header.vendorID != properties.vendorID || header.deviceID != properties.deviceID ||
                memcmp(header.pipelineCacheUUID, properties.pipelineCacheUUID, VK_UUID_SIZE)) {
                fprintf(stderr, "Pipeline cache header does not match this backend\n");
                goto cleanup;
            }
        }
        fclose(input);
        input = NULL;
        const VkPipelineCacheCreateInfo cache_info = {
            .sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO,
            .flags = cache_flags, .initialDataSize = cache_size, .pInitialData = cache_bytes,
        };
        CHECK(vkCreatePipelineCache(device, &cache_info, NULL, &pipeline_cache));
    }
    if (history_file && !load_history_file(history_file, device, pipeline_cache, history, &history_count)) {
        fprintf(stderr, "Failed to create history pipelines from %s\n", history_file);
        goto cleanup;
    }
    for (uint32_t s = 0; s < set_count; ++s) {
        const VkDescriptorSetLayoutCreateInfo set_info = {
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
            .bindingCount = binding_counts[s], .pBindings = bindings[s],
        };
        CHECK(vkCreateDescriptorSetLayout(device, &set_info, NULL, &sets[s]));
    }
    const VkPipelineLayoutCreateInfo layout_info = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = set_count, .pSetLayouts = sets,
        .pushConstantRangeCount = push_count, .pPushConstantRanges = &push_range,
    };
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    const VkShaderModuleCreateInfo module_info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = (size_t)size, .pCode = shader_bytes,
    };
    CHECK(vkCreateShaderModule(device, &module_info, NULL, &module));
    VkComputePipelineCreateInfo pipeline_info = {
        .sType = VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO,
        .stage = {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
                  .stage = VK_SHADER_STAGE_COMPUTE_BIT, .module = module, .pName = entry_name},
        .layout = layout,
        .flags = layout_override.pipeline_flags,
    };
    VkPipelineShaderStageRequiredSubgroupSizeCreateInfo subgroup_info = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_REQUIRED_SUBGROUP_SIZE_CREATE_INFO,
        .requiredSubgroupSize = layout_override.required_subgroup_size,
    };
    if (layout_override.required_subgroup_size) {
        pipeline_info.stage.pNext = &subgroup_info;
    }
    fprintf(stderr, "BEGIN pipeline-create entry=%s bytes=%ld\n", entry_name, size);
    double begin = seconds();
    CHECK(vkCreateComputePipelines(device, pipeline_cache, 1, &pipeline_info, NULL, &pipeline));
    double pipeline_seconds = seconds() - begin;
    fprintf(stderr, "END pipeline-create seconds=%.6f\n", pipeline_seconds);

    if (smoke) {
        const VkBufferCreateInfo buffer_info = {
            .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = SMOKE_WORDS * 4,
            .usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT,
            .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        };
        CHECK(vkCreateBuffer(device, &buffer_info, NULL, &buffer));
        VkMemoryRequirements requirements;
        vkGetBufferMemoryRequirements(device, buffer, &requirements);
        uint32_t index = memory_type(gpu, requirements.memoryTypeBits);
        if (index == UINT32_MAX) goto cleanup;
        const VkMemoryAllocateInfo allocation = {
            .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO,
            .allocationSize = requirements.size, .memoryTypeIndex = index,
        };
        CHECK(vkAllocateMemory(device, &allocation, NULL, &memory));
        CHECK(vkBindBufferMemory(device, buffer, memory, 0));
        CHECK(vkMapMemory(device, memory, 0, SMOKE_WORDS * 4, 0, &mapped));
        memset(mapped, 0, SMOKE_WORDS * 4);
        const VkDescriptorPoolSize pool_size = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 1};
        const VkDescriptorPoolCreateInfo descriptor_info = {
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO, .maxSets = 1,
            .poolSizeCount = 1, .pPoolSizes = &pool_size,
        };
        CHECK(vkCreateDescriptorPool(device, &descriptor_info, NULL, &descriptors));
        const VkDescriptorSetAllocateInfo allocation_info = {
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO,
            .descriptorPool = descriptors, .descriptorSetCount = 1, .pSetLayouts = sets,
        };
        VkDescriptorSet descriptor;
        CHECK(vkAllocateDescriptorSets(device, &allocation_info, &descriptor));
        const VkDescriptorBufferInfo buffer_binding = {buffer, 0, SMOKE_WORDS * 4};
        const VkWriteDescriptorSet write = {
            .sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET, .dstSet = descriptor,
            .dstBinding = 0, .descriptorCount = 1,
            .descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, .pBufferInfo = &buffer_binding,
        };
        vkUpdateDescriptorSets(device, 1, &write, 0, NULL);
        const VkCommandPoolCreateInfo command_info = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family,
        };
        CHECK(vkCreateCommandPool(device, &command_info, NULL, &commands));
        const VkCommandBufferAllocateInfo cmd_allocation = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = commands,
            .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1,
        };
        VkCommandBuffer command;
        CHECK(vkAllocateCommandBuffers(device, &cmd_allocation, &command));
        const VkCommandBufferBeginInfo cmd_begin = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
            .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
        };
        CHECK(vkBeginCommandBuffer(command, &cmd_begin));
        vkCmdBindPipeline(command, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
        vkCmdBindDescriptorSets(command, VK_PIPELINE_BIND_POINT_COMPUTE,
                                layout, 0, 1, &descriptor, 0, NULL);
        vkCmdDispatch(command, 1, 1, 1);
        const VkMemoryBarrier barrier = {
            .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
        };
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &barrier, 0, NULL, 0, NULL);
        CHECK(vkEndCommandBuffer(command));
        const VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
        CHECK(vkCreateFence(device, &fence_info, NULL, &fence));
        VkQueue queue;
        vkGetDeviceQueue(device, family, 0, &queue);
        const VkSubmitInfo submit = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1,
            .pCommandBuffers = &command,
        };
        fprintf(stderr, "BEGIN smoke-dispatch\n");
        CHECK(vkQueueSubmit(queue, 1, &submit, fence));
        CHECK(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_C(5000000000)));
        for (uint32_t i = 0; i < SMOKE_WORDS; ++i) {
            if (((uint32_t *)mapped)[i] != i * 3 + 7) {
                fprintf(stderr, "Readback mismatch at %u\n", i);
                goto cleanup;
            }
        }
        fprintf(stderr, "END smoke-dispatch words=64 verified\n");
    }
    printf("{\"schema_version\":1,\"status\":\"PASS\",\"backend\":");
    json_string(argv[2]);
    printf(",\"device_name\":");
    json_string(properties.deviceName);
    printf(",\"vendor_id\":%u,\"api_version\":%u,\"driver_version\":%u,\"entry\":",
           properties.vendorID, properties.apiVersion, properties.driverVersion);
    json_string(entry_name);
    printf(",\"mode\":");
    json_string(argv[4]);
    printf(",\"pipeline_seconds\":%.9f,\"readback_words\":%u,"
           "\"ue_exact_replay\":false,", pipeline_seconds, smoke ? SMOKE_WORDS : 0);
#ifdef CARLA_USE_DEVICE_SNAPSHOT
    printf("\"feature_policy\":\"captured_vkCreateDevice\","
           "\"device_configuration_reused\":true,\"device_snapshot_sha256\":");
    json_string(CARLA_DEVICE_SNAPSHOT_SHA256);
#else
    printf("\"feature_policy\":\"all_supported_core_1_0_to_1_3\","
           "\"device_configuration_reused\":false,\"device_snapshot_sha256\":null");
#endif
    printf(",\"layout_source\":");
    json_string(layout_override.active ? "ue-layout-baseline" : "spirv-reflect");
    printf(",\"pipeline_flags\":%u,\"required_subgroup_size\":%u,"
           "\"ue_layout_file\":", layout_override.pipeline_flags,
           layout_override.required_subgroup_size);
    if (layout_file) json_string(layout_file);
    else printf("null");
    printf(",\"pipeline_cache_policy\":");
    json_string(cache_file ? "captured_initial_data" : "null");
    printf(",\"pipeline_cache_initial_bytes\":%zu,\"pipeline_cache_flags\":%u",
           cache_size, cache_flags);
    printf(",\"history_pipeline_count\":%u,\"ue_shader_header_reused\":false,"
           "\"queue_family\":%u,\"descriptor_sets\":[", history_count, family);
    for (uint32_t s = 0; s < set_count; ++s) {
        if (s) putchar(',');
        printf("{\"set\":%u,\"bindings\":[", s);
        for (uint32_t b = 0; b < binding_counts[s]; ++b) {
            if (b) putchar(',');
            printf("{\"binding\":%u,\"descriptor_type\":%u,\"count\":%u}",
                   bindings[s][b].binding, bindings[s][b].descriptorType, bindings[s][b].descriptorCount);
        }
        printf("]}");
    }
    printf("],\"push_constant_offset\":%u,\"push_constant_size\":%u}\n", push_range.offset, push_range.size);
    code = 0;

cleanup:
    if (device) {
        if (fence) {
            /* DeviceWaitIdle may hang after GPU failure; the parent owns the hard timeout. */
            vkDeviceWaitIdle(device);
        }
        if (mapped) vkUnmapMemory(device, memory);
        if (fence) vkDestroyFence(device, fence, NULL);
        if (commands) vkDestroyCommandPool(device, commands, NULL);
        if (descriptors) vkDestroyDescriptorPool(device, descriptors, NULL);
        if (buffer) vkDestroyBuffer(device, buffer, NULL);
        if (memory) vkFreeMemory(device, memory, NULL);
        if (pipeline) vkDestroyPipeline(device, pipeline, NULL);
        if (pipeline_cache) vkDestroyPipelineCache(device, pipeline_cache, NULL);
        for (uint32_t i = 0; i < history_count; ++i) {
            if (history[i].pipeline) vkDestroyPipeline(device, history[i].pipeline, NULL);
            if (history[i].module) vkDestroyShaderModule(device, history[i].module, NULL);
            if (history[i].layout) vkDestroyPipelineLayout(device, history[i].layout, NULL);
            for (uint32_t set = 0; set < history[i].set_count; ++set)
                if (history[i].sets[set])
                    vkDestroyDescriptorSetLayout(device, history[i].sets[set], NULL);
        }
        if (module) vkDestroyShaderModule(device, module, NULL);
        if (layout) vkDestroyPipelineLayout(device, layout, NULL);
        for (uint32_t s = 0; s < set_count; ++s)
            if (sets[s]) vkDestroyDescriptorSetLayout(device, sets[s], NULL);
        vkDestroyDevice(device, NULL);
    }
    if (instance) vkDestroyInstance(instance, NULL);
    if (reflected) spvReflectDestroyShaderModule(&reflection);
    if (input) fclose(input);
    free(shader_bytes);
    free(cache_bytes);
    return code;
}
