#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifndef __aarch64__
#error This probe is intended for native DGX ARM64 only
#endif

enum { WIDTH = 64, HEIGHT = 48, FRAME_BYTES = WIDTH * HEIGHT * 4 };

#define VK_CHECK(call) do { \
    VkResult result_ = (call); \
    if (result_ != VK_SUCCESS) { \
        fprintf(stderr, "%s failed: %d\n", #call, (int)result_); \
        goto cleanup; \
    } \
} while (0)

static uint32_t memory_type(VkPhysicalDevice gpu, uint32_t bits, VkMemoryPropertyFlags flags)
{
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(gpu, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i)
        if ((bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & flags) == flags)
            return i;
    return UINT32_MAX;
}

static int write_frame(const char *directory, unsigned frame, const void *pixels)
{
    char path[4096];
    int length = snprintf(path, sizeof(path), "%s/frame-%u.rgba", directory, frame);
    if (length < 0 || (size_t)length >= sizeof(path)) return 0;
    FILE *file = fopen(path, "wb");
    if (!file) return 0;
    int ok = fwrite(pixels, 1, FRAME_BYTES, file) == FRAME_BYTES;
    if (fclose(file) != 0) ok = 0;
    return ok;
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "Usage: vulkan-readback OUTPUT_DIRECTORY\n");
        return 64;
    }
    int code = 1;
    VkInstance instance = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkImage image = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
    VkDeviceMemory image_memory = VK_NULL_HANDLE, host_memory = VK_NULL_HANDLE;
    VkBuffer buffer = VK_NULL_HANDLE;
    VkRenderPass render_pass = VK_NULL_HANDLE;
    VkFramebuffer framebuffer = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    void *mapped = NULL;
    const VkApplicationInfo app = {
        .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO,
        .pApplicationName = "DGX Vulkan readback",
        .apiVersion = VK_API_VERSION_1_2,
    };
    const VkInstanceCreateInfo instance_info = {
        .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app,
    };
    VK_CHECK(vkCreateInstance(&instance_info, NULL, &instance));
    uint32_t count = 0;
    VK_CHECK(vkEnumeratePhysicalDevices(instance, &count, NULL));
    if (!count || count > 64) { fprintf(stderr, "Unexpected GPU count\n"); goto cleanup; }
    VkPhysicalDevice devices[64], gpu = VK_NULL_HANDLE;
    VK_CHECK(vkEnumeratePhysicalDevices(instance, &count, devices));
    VkPhysicalDeviceProperties props = {0};
    for (uint32_t i = 0; i < count; ++i) {
        vkGetPhysicalDeviceProperties(devices[i], &props);
        if (props.vendorID == 0x10de && strcmp(props.deviceName, "NVIDIA GB10") == 0) {
            gpu = devices[i];
            break;
        }
    }
    if (!gpu) { fprintf(stderr, "A real NVIDIA GB10 device is required\n"); goto cleanup; }
    uint32_t queue_count = 0, family = UINT32_MAX;
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_count, NULL);
    if (!queue_count || queue_count > 64) goto cleanup;
    VkQueueFamilyProperties families[64];
    vkGetPhysicalDeviceQueueFamilyProperties(gpu, &queue_count, families);
    for (uint32_t i = 0; i < queue_count; ++i)
        if (families[i].queueCount && (families[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)) {
            family = i;
            break;
        }
    if (family == UINT32_MAX) { fprintf(stderr, "No graphics queue\n"); goto cleanup; }
    VkPhysicalDeviceVulkan12Features features12 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES,
    };
    VkPhysicalDeviceFeatures2 features = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &features12,
    };
    vkGetPhysicalDeviceFeatures2(gpu, &features);
    const float priority = 1.0f;
    const VkDeviceQueueCreateInfo queue_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO,
        .queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority,
    };
    const VkDeviceCreateInfo device_info = {
        .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO,
        .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info,
    };
    VK_CHECK(vkCreateDevice(gpu, &device_info, NULL, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);
    const VkImageCreateInfo image_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO,
        .imageType = VK_IMAGE_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
        .extent = {WIDTH, HEIGHT, 1}, .mipLevels = 1, .arrayLayers = 1,
        .samples = VK_SAMPLE_COUNT_1_BIT, .tiling = VK_IMAGE_TILING_OPTIMAL,
        .usage = VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_SRC_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE, .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED,
    };
    VK_CHECK(vkCreateImage(device, &image_info, NULL, &image));
    VkMemoryRequirements requirements;
    vkGetImageMemoryRequirements(device, image, &requirements);
    VkMemoryAllocateInfo allocation = {
        .sType = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO, .allocationSize = requirements.size,
        .memoryTypeIndex = memory_type(gpu, requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT),
    };
    if (allocation.memoryTypeIndex == UINT32_MAX) goto cleanup;
    VK_CHECK(vkAllocateMemory(device, &allocation, NULL, &image_memory));
    VK_CHECK(vkBindImageMemory(device, image, image_memory, 0));
    const VkImageViewCreateInfo view_info = {
        .sType = VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO, .image = image,
        .viewType = VK_IMAGE_VIEW_TYPE_2D, .format = VK_FORMAT_R8G8B8A8_UNORM,
        .subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1},
    };
    VK_CHECK(vkCreateImageView(device, &view_info, NULL, &view));
    const VkBufferCreateInfo buffer_info = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO, .size = FRAME_BYTES,
        .usage = VK_BUFFER_USAGE_TRANSFER_DST_BIT, .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
    };
    VK_CHECK(vkCreateBuffer(device, &buffer_info, NULL, &buffer));
    vkGetBufferMemoryRequirements(device, buffer, &requirements);
    allocation.allocationSize = requirements.size;
    allocation.memoryTypeIndex = memory_type(gpu, requirements.memoryTypeBits,
        VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (allocation.memoryTypeIndex == UINT32_MAX) goto cleanup;
    VK_CHECK(vkAllocateMemory(device, &allocation, NULL, &host_memory));
    VK_CHECK(vkBindBufferMemory(device, buffer, host_memory, 0));
    const VkAttachmentDescription attachment = {
        .format = VK_FORMAT_R8G8B8A8_UNORM, .samples = VK_SAMPLE_COUNT_1_BIT,
        .loadOp = VK_ATTACHMENT_LOAD_OP_CLEAR, .storeOp = VK_ATTACHMENT_STORE_OP_STORE,
        .stencilLoadOp = VK_ATTACHMENT_LOAD_OP_DONT_CARE,
        .stencilStoreOp = VK_ATTACHMENT_STORE_OP_DONT_CARE,
        .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED, .finalLayout = VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL,
    };
    const VkAttachmentReference color = {0, VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL};
    const VkSubpassDescription subpass = {
        .pipelineBindPoint = VK_PIPELINE_BIND_POINT_GRAPHICS,
        .colorAttachmentCount = 1, .pColorAttachments = &color,
    };
    const VkSubpassDependency dependencies[2] = {
        {.srcSubpass = VK_SUBPASS_EXTERNAL, .dstSubpass = 0,
         .srcStageMask = VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
         .dstStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
         .dstAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT},
        {.srcSubpass = 0, .dstSubpass = VK_SUBPASS_EXTERNAL,
         .srcStageMask = VK_PIPELINE_STAGE_COLOR_ATTACHMENT_OUTPUT_BIT,
         .dstStageMask = VK_PIPELINE_STAGE_TRANSFER_BIT,
         .srcAccessMask = VK_ACCESS_COLOR_ATTACHMENT_WRITE_BIT, .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT},
    };
    const VkRenderPassCreateInfo pass_info = {
        .sType = VK_STRUCTURE_TYPE_RENDER_PASS_CREATE_INFO,
        .attachmentCount = 1, .pAttachments = &attachment,
        .subpassCount = 1, .pSubpasses = &subpass,
        .dependencyCount = 2, .pDependencies = dependencies,
    };
    VK_CHECK(vkCreateRenderPass(device, &pass_info, NULL, &render_pass));
    const VkFramebufferCreateInfo framebuffer_info = {
        .sType = VK_STRUCTURE_TYPE_FRAMEBUFFER_CREATE_INFO, .renderPass = render_pass,
        .attachmentCount = 1, .pAttachments = &view, .width = WIDTH, .height = HEIGHT, .layers = 1,
    };
    VK_CHECK(vkCreateFramebuffer(device, &framebuffer_info, NULL, &framebuffer));
    const VkCommandPoolCreateInfo pool_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO, .queueFamilyIndex = family,
    };
    VK_CHECK(vkCreateCommandPool(device, &pool_info, NULL, &pool));
    const VkCommandBufferAllocateInfo command_info = {
        .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = pool,
        .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = 1,
    };
    VkCommandBuffer command;
    VK_CHECK(vkAllocateCommandBuffers(device, &command_info, &command));
    const VkFenceCreateInfo fence_info = {.sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VK_CHECK(vkCreateFence(device, &fence_info, NULL, &fence));
    for (unsigned frame = 0; frame < 2; ++frame) {
        VK_CHECK(vkResetCommandPool(device, pool, 0));
        VK_CHECK(vkResetFences(device, 1, &fence));
        const VkCommandBufferBeginInfo begin = {
            .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
            .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT,
        };
        VK_CHECK(vkBeginCommandBuffer(command, &begin));
        VkClearValue left = {.color = {.float32 = {1, 0, 0, 1}}};
        if (frame) { left.color.float32[0] = 0; left.color.float32[1] = 1; }
        const VkRenderPassBeginInfo pass_begin = {
            .sType = VK_STRUCTURE_TYPE_RENDER_PASS_BEGIN_INFO,
            .renderPass = render_pass, .framebuffer = framebuffer,
            .renderArea = {{0, 0}, {WIDTH, HEIGHT}}, .clearValueCount = 1, .pClearValues = &left,
        };
        vkCmdBeginRenderPass(command, &pass_begin, VK_SUBPASS_CONTENTS_INLINE);
        VkClearAttachment right = {
            .aspectMask = VK_IMAGE_ASPECT_COLOR_BIT, .colorAttachment = 0,
            .clearValue = {.color = {.float32 = {0, 0, 1, 1}}},
        };
        if (frame) { right.clearValue.color.float32[0] = 1; right.clearValue.color.float32[1] = 1; }
        const VkClearRect rect = {{{WIDTH / 2, 0}, {WIDTH / 2, HEIGHT}}, 0, 1};
        vkCmdClearAttachments(command, 1, &right, 1, &rect);
        vkCmdEndRenderPass(command);
        const VkBufferImageCopy copy = {
            .imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1},
            .imageExtent = {WIDTH, HEIGHT, 1},
        };
        vkCmdCopyImageToBuffer(command, image, VK_IMAGE_LAYOUT_TRANSFER_SRC_OPTIMAL, buffer, 1, &copy);
        const VkBufferMemoryBarrier host_barrier = {
            .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
            .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT, .dstAccessMask = VK_ACCESS_HOST_READ_BIT,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .buffer = buffer, .offset = 0, .size = VK_WHOLE_SIZE,
        };
        vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
            0, 0, NULL, 1, &host_barrier, 0, NULL);
        VK_CHECK(vkEndCommandBuffer(command));
        const VkSubmitInfo submit = {
            .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .commandBufferCount = 1, .pCommandBuffers = &command,
        };
        VK_CHECK(vkQueueSubmit(queue, 1, &submit, fence));
        VK_CHECK(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_C(10000000000)));
        VK_CHECK(vkMapMemory(device, host_memory, 0, FRAME_BYTES, 0, &mapped));
        if (!write_frame(argv[1], frame, mapped)) { fprintf(stderr, "Cannot write readback\n"); goto cleanup; }
        vkUnmapMemory(device, host_memory);
        mapped = NULL;
    }
    printf("{\"schema_version\":1,\"device_name\":\"NVIDIA GB10\",\"vendor_id\":%u,"
        "\"device_id\":%u,\"api_version\":%u,\"driver_version\":%u,\"queue_family\":%u,"
        "\"graphics_queue\":true,\"width\":%u,\"height\":%u,\"frames\":2,"
        "\"runtime_descriptor_array\":%s,\"descriptor_binding_partially_bound\":%s,"
        "\"sampled_image_update_after_bind\":%s}\n",
        props.vendorID, props.deviceID, props.apiVersion, props.driverVersion, family, WIDTH, HEIGHT,
        features12.runtimeDescriptorArray ? "true" : "false",
        features12.descriptorBindingPartiallyBound ? "true" : "false",
        features12.descriptorBindingSampledImageUpdateAfterBind ? "true" : "false");
    code = ferror(stdout) ? 1 : 0;
cleanup:
    if (device) {
        if (vkDeviceWaitIdle(device) != VK_SUCCESS) code = 1;
        if (mapped) vkUnmapMemory(device, host_memory);
        if (fence) vkDestroyFence(device, fence, NULL);
        if (pool) vkDestroyCommandPool(device, pool, NULL);
        if (framebuffer) vkDestroyFramebuffer(device, framebuffer, NULL);
        if (render_pass) vkDestroyRenderPass(device, render_pass, NULL);
        if (view) vkDestroyImageView(device, view, NULL);
        if (image) vkDestroyImage(device, image, NULL);
        if (buffer) vkDestroyBuffer(device, buffer, NULL);
        if (image_memory) vkFreeMemory(device, image_memory, NULL);
        if (host_memory) vkFreeMemory(device, host_memory, NULL);
        vkDestroyDevice(device, NULL);
    }
    if (instance) vkDestroyInstance(instance, NULL);
    return code;
}
