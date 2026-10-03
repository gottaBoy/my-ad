# Shared ARM64/Lavapipe shader scope for the CarlaUnreal cook and client launch.
#
# Cook-time and run-time scope must stay identical. Shader permutations are baked at
# cook time, and features such as r.VirtualTextures / r.RayTracing are ECVF_ReadOnly:
# a cooked client can only be switched to the cook's scope through init-time ini, so a
# feature the runtime still enables aborts in FMaterial::GetShaderMap, e.g.
#   Failed to find shader map for default material DefaultDeferredDecalMaterial
#   Incomplete material ... missing (TVirtualTextureVSBaseColorNormalRoughness, 0)
# Each entry is a software-renderer workaround for this Lavapipe/ARM64 path, not a
# CARLA feature. probe-arm64-full-cook.sh and run-carla-lavapipe-sensors.sh both source
# this file, so neither side can drift and silently disable or re-enable a feature.
CARLA_RENDERER_DISABLED_CVARS=(
  r.RayTracing
  r.RayTracing.EnableOnDemand
  r.Lumen.DiffuseIndirect.Allow
  r.Shadow.Virtual.Enable
  r.VolumetricCloud
  r.VirtualTextures
)

# Emit -ini:Engine:[SystemSettings]:<cvar>=0 for every disabled feature. Both callers
# consume this; the array is the single list that defines the ARM64 rendering scope.
carla_renderer_systemsettings_flags() {
  local cvar
  for cvar in "${CARLA_RENDERER_DISABLED_CVARS[@]}"; do
    printf -- '-ini:Engine:[SystemSettings]:%s=0\n' "${cvar}"
  done
}
