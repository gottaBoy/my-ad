# Runtime-only scope shared by the Vulkan configuration and pipeline captures.
source "$(dirname "${BASH_SOURCE[0]}")/arm64-renderer-scope.sh"

carla_vulkan_diagnostic_flags() {
  printf '%s\n' \
    -vulkan -sm6 -RenderOffScreen -no-rendering -quality-level=Low \
    -AllowCPUDevices -SkipVulkanProfileCheck -nosound -NoSplash -noscript \
    '-ini:Engine:[/Script/Engine.RendererSettings]:r.Nanite.ProjectEnabled=0' \
    '-ini:Engine:[/Script/Engine.RendererSettings]:r.Nanite.ForceEnableMeshes=0' \
    '-ini:Engine:[/Script/Engine.RendererSettings]:r.Shadow.Virtual.Enable=0' \
    '-ini:Engine:[/Script/Engine.RendererSettings]:r.VolumetricCloud=0' \
    '-ini:Engine:[/Script/Engine.RendererSettings]:r.RayTracing=0' \
    '-ini:Engine:[/Script/Engine.RendererSettings]:r.Lumen.TraceMeshSDFs=0' \
    '-ini:Engine:[/Script/Engine.RendererSettings]:r.AllowOcclusionQueries=0' \
    '-ini:Engine:[SystemSettings]:r.PSOPrecaching=0' \
    '-ini:Engine:[SystemSettings]:r.Vulkan.AllowPSOPrecaching=0' \
    '-ini:Engine:[SystemSettings]:r.AsyncPipelineCompile=0' \
    '-ini:Engine:[SystemSettings]:r.Vulkan.RHIThread=0'
  carla_renderer_systemsettings_flags
}
