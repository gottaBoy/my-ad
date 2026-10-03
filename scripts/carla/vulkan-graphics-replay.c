#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "captured-device.h"
#include "captured-graphics.h"

#ifndef __aarch64__
#error Native ARM64 is required
#endif

#define CHECK(call) do { \
    VkResult result_ = (call); \
    if (result_ != VK_SUCCESS) { \
        fprintf(stderr, "%s failed: %d\n", #call, (int)result_); \
        goto cleanup; \
    } \
} while (0)

static int read_file(const char *path, void **out, size_t *length, size_t limit)
{
    FILE *file = fopen(path, "rb");
    if (!file) return 0;
    if (fseek(file, 0, SEEK_END)) { fclose(file); return 0; }
    long size = ftell(file);
    if (size <= 0 || (size_t)size > limit || fseek(file, 0, SEEK_SET)) {
        fclose(file);
        return 0;
    }
    void *bytes = malloc((size_t)size);
    int ok = bytes && fread(bytes, 1, (size_t)size, file) == (size_t)size;
    fclose(file);
    if (!ok) { free(bytes); return 0; }
    *out = bytes;
    *length = (size_t)size;
    return 1;
}

int main(int argc, char **argv)
{
    if (argc != 4) {
        fprintf(stderr, "Usage: vulkan-graphics-replay VERTEX.spv FRAGMENT.spv CACHE.bin\n");
        return 64;
    }
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stderr, NULL, _IONBF, 0);
    int code = 1;
    void *vs_bytes = NULL, *ps_bytes = NULL, *cache_bytes = NULL;
    size_t vs_length = 0, ps_length = 0, cache_length = 0;
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkPipelineCache cache = VK_NULL_HANDLE;
    VkDescriptorSetLayout sets[CAPTURE_SET_COUNT] = {0};
    VkPipelineLayout layout = VK_NULL_HANDLE;
    VkRenderPass render_pass = VK_NULL_HANDLE;
    VkShaderModule vs = VK_NULL_HANDLE, ps = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkPhysicalDeviceProperties properties = {0};
    if (!read_file(argv[1], &vs_bytes, &vs_length, 2 * 1024 * 1024) ||
        !read_file(argv[2], &ps_bytes, &ps_length, 2 * 1024 * 1024) ||
        !read_file(argv[3], &cache_bytes, &cache_length, 8 * 1024 * 1024) ||
        vs_length % 4 || ps_length % 4) {
        fprintf(stderr, "Missing or invalid captured module/cache bytes\n");
        goto cleanup;
    }
    VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "CARLA isolated graphics create",
        .apiVersion = VK_API_VERSION_1_3,
    };
    VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app,
    };
    if (!configure_captured_instance(&app, &instance_info)) goto cleanup;
    CHECK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t gpu_count = 0;
    CHECK(vkEnumeratePhysicalDevices(instance, &gpu_count, NULL));
    if (!gpu_count || gpu_count > 32) goto cleanup;
    VkPhysicalDevice gpus[32], gpu = VK_NULL_HANDLE;
    CHECK(vkEnumeratePhysicalDevices(instance, &gpu_count, gpus));
    for (uint32_t index = 0; index < gpu_count; ++index) {
        vkGetPhysicalDeviceProperties(gpus[index], &properties);
        if (properties.vendorID == 0x10de && !strcmp(properties.deviceName, "NVIDIA GB10")) {
            gpu = gpus[index];
            break;
        }
    }
    if (!gpu || properties.apiVersion < VK_API_VERSION_1_3 ||
        CAPTURE_SET_COUNT > properties.limits.maxBoundDescriptorSets) {
        fprintf(stderr, "NVIDIA GB10 / Vulkan 1.3 or layout limits unavailable\n");
        goto cleanup;
    }
    uint32_t family = UINT32_MAX;
    VkDeviceCreateInfo device_info = {.sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    if (!configure_captured_device(gpu, &device_info, &family)) goto cleanup;
    fprintf(stderr, "BEGIN device-create\n");
    CHECK(vkCreateDevice(gpu, &device_info, NULL, &device));
    const VkPipelineCacheHeaderVersionOne *header = cache_bytes;
    if (cache_length < sizeof(*header) || header->headerSize != sizeof(*header) ||
        header->headerVersion != VK_PIPELINE_CACHE_HEADER_VERSION_ONE ||
        header->vendorID != properties.vendorID || header->deviceID != properties.deviceID ||
        memcmp(header->pipelineCacheUUID, properties.pipelineCacheUUID, VK_UUID_SIZE)) {
        fprintf(stderr, "Captured pipeline cache does not match this device\n");
        goto cleanup;
    }
    const VkPipelineCacheCreateInfo cache_info = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_CACHE_CREATE_INFO,
        .initialDataSize = cache_length, .pInitialData = cache_bytes,
    };
    CHECK(vkCreatePipelineCache(device, &cache_info, NULL, &cache));
    for (uint32_t index = 0; index < CAPTURE_SET_COUNT; ++index) {
        const VkDescriptorSetLayoutCreateInfo set_info = {
            .sType = VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO,
            .bindingCount = cap_binding_counts[index], .pBindings = cap_set_bindings[index],
        };
        CHECK(vkCreateDescriptorSetLayout(device, &set_info, NULL, &sets[index]));
    }
    const VkPipelineLayoutCreateInfo layout_info = {
        .sType = VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO,
        .setLayoutCount = CAPTURE_SET_COUNT, .pSetLayouts = sets,
    };
    CHECK(vkCreatePipelineLayout(device, &layout_info, NULL, &layout));
    PFN_vkCreateRenderPass2KHR create_render_pass =
        (PFN_vkCreateRenderPass2KHR)vkGetDeviceProcAddr(device, "vkCreateRenderPass2KHR");
    if (!create_render_pass) {
        fprintf(stderr, "Captured vkCreateRenderPass2KHR entry unavailable\n");
        goto cleanup;
    }
    CHECK(create_render_pass(device, &cap_renderpass, NULL, &render_pass));
    const VkShaderModuleCreateInfo vertex_info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = vs_length, .pCode = vs_bytes,
    };
    const VkShaderModuleCreateInfo fragment_info = {
        .sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO,
        .codeSize = ps_length, .pCode = ps_bytes,
    };
    CHECK(vkCreateShaderModule(device, &vertex_info, NULL, &vs));
    CHECK(vkCreateShaderModule(device, &fragment_info, NULL, &ps));
    const VkPipelineShaderStageCreateInfo stages[] = {
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_VERTEX_BIT, .module = vs, .pName = CAPTURE_VS_ENTRY},
        {.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO,
         .stage = VK_SHADER_STAGE_FRAGMENT_BIT, .module = ps, .pName = CAPTURE_PS_ENTRY},
    };
    const VkGraphicsPipelineCreateInfo graphics = {
        .sType = VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_CREATE_INFO,
        .flags = CAPTURE_GRAPHICS_FLAGS,
        .stageCount = 2, .pStages = stages,
        .pVertexInputState = &captured_VkPipelineVertexInputStateCreateInfo,
        .pInputAssemblyState = &captured_VkPipelineInputAssemblyStateCreateInfo,
        .pViewportState = &captured_VkPipelineViewportStateCreateInfo,
        .pRasterizationState = &captured_VkPipelineRasterizationStateCreateInfo,
        .pMultisampleState = &captured_VkPipelineMultisampleStateCreateInfo,
        .pDepthStencilState = &captured_VkPipelineDepthStencilStateCreateInfo,
        .pColorBlendState = &captured_VkPipelineColorBlendStateCreateInfo,
        .pDynamicState = &captured_VkPipelineDynamicStateCreateInfo,
        .layout = layout, .renderPass = render_pass, .subpass = 0,
    };
    fprintf(stderr, "BEGIN graphics-pipeline-create vs=%s ps=%s cache_bytes=%zu\n",
            CAPTURE_VS_ENTRY, CAPTURE_PS_ENTRY, cache_length);
    CHECK(vkCreateGraphicsPipelines(device, cache, 1, &graphics, NULL, &pipeline));
    fprintf(stderr, "END graphics-pipeline-create result=0\n");
    printf("{\"status\":\"PASS\",\"scope\":\"isolated graphics create only\","
           "\"backend\":\"NVIDIA GB10\",\"device_snapshot_sha256\":\"%s\","
           "\"vs_entry\":\"%s\",\"ps_entry\":\"%s\","
           "\"pipeline_cache_bytes\":%zu,\"ue_exact_replay\":false}\n",
           CARLA_DEVICE_SNAPSHOT_SHA256, CAPTURE_VS_ENTRY, CAPTURE_PS_ENTRY, cache_length);
    code = 0;

cleanup:
    if (device) {
        if (pipeline) vkDestroyPipeline(device, pipeline, NULL);
        if (ps) vkDestroyShaderModule(device, ps, NULL);
        if (vs) vkDestroyShaderModule(device, vs, NULL);
        if (render_pass) vkDestroyRenderPass(device, render_pass, NULL);
        if (layout) vkDestroyPipelineLayout(device, layout, NULL);
        for (uint32_t index = 0; index < CAPTURE_SET_COUNT; ++index)
            if (sets[index]) vkDestroyDescriptorSetLayout(device, sets[index], NULL);
        if (cache) vkDestroyPipelineCache(device, cache, NULL);
        vkDestroyDevice(device, NULL);
    }
    if (instance) vkDestroyInstance(instance, NULL);
    free(cache_bytes);
    free(ps_bytes);
    free(vs_bytes);
    return code;
}
