#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

cook_root="${CARLA_FULL_COOK_OUTPUT:-}"
stage_root="${CARLA_COOKED_CLIENT_STAGE:-/artifacts/carla/cooked-client-full/CarlaUnreal}"
shader_cache="${CARLA_GLOBAL_SHADER_CACHE:-/artifacts/carla/cooked-server-full/Engine/OverrideGlobalShaderCache-VULKAN_SM6.bin}"
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[[ -n "${cook_root}" && "${cook_root}" == /* ]] || {
  echo "CARLA_FULL_COOK_OUTPUT must name the full cook output directory" >&2
  exit 64
}
[[ -d "${cook_root}/CarlaUnreal" ]] || {
  echo "Cook output project directory is missing: ${cook_root}/CarlaUnreal" >&2
  exit 2
}
[[ -f "${cook_root}/CarlaUnreal/AssetRegistry.bin" ]] || {
  echo "Cook output AssetRegistry.bin is missing: ${cook_root}/CarlaUnreal/AssetRegistry.bin" >&2
  exit 2
}
[[ -x "${carla_dir}/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal" ]] || {
  echo "ARM64 client binary is missing" >&2
  exit 2
}
[[ -f "${shader_cache}" ]] || {
  echo "Global shader cache is missing: ${shader_cache}" >&2
  exit 2
}
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

run_dir="$(mktemp -d "${artifact_dir}/cooked-client-stage-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
stage_parent="$(dirname "${stage_root}")"
mkdir -p "${run_dir}" "${stage_parent}/CarlaUnreal"
rm -rf "${stage_root}" "${stage_parent}/Engine"
mkdir -p "${stage_root}" "${stage_parent}/Engine"

cp -a "${cook_root}/CarlaUnreal/." "${stage_root}/"
if [[ -d "${cook_root}/Engine" ]]; then
  cp -a "${cook_root}/Engine/." "${stage_parent}/Engine/"
fi

cp -a "${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject" "${stage_root}/"
mkdir -p "${stage_root}/Binaries/LinuxArm64"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal" \
  "${stage_root}/Binaries/LinuxArm64/"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnreal.target" \
  "${stage_root}/Binaries/LinuxArm64/"

mkdir -p "${stage_root}/Content/Carla/Config" \
  "${stage_root}/Content/Carla/Maps/OpenDrive" "${stage_root}/Content/Carla/Maps/Nav"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Content/Carla/Config/." \
  "${stage_root}/Content/Carla/Config/"
find "${carla_dir}/Unreal/CarlaUnreal/Content/Carla/Maps/OpenDrive" \
  -maxdepth 1 -type f -name '*.xodr' -exec cp -a {} "${stage_root}/Content/Carla/Maps/OpenDrive/" \;
find "${carla_dir}/Unreal/CarlaUnreal/Content/Carla/Maps/Nav" \
  -maxdepth 1 -type f -name '*.bin' -exec cp -a {} "${stage_root}/Content/Carla/Maps/Nav/" \;

if [[ -d "${carla_dir}/Unreal/CarlaUnreal/Config" ]]; then
  cp -a "${carla_dir}/Unreal/CarlaUnreal/Config" "${stage_root}/"
fi
python3 "${script_dir}/normalize-runtime-config.py" --stage-root "${stage_root}"

mkdir -p "${stage_root}/Plugins/Carla"
if [[ -f "${cook_root}/CarlaUnreal/Plugins/Carla/Carla.uplugin" ]]; then
  cp -a "${cook_root}/CarlaUnreal/Plugins/Carla/Carla.uplugin" \
    "${stage_root}/Plugins/Carla/"
else
  cp -a "${carla_dir}/Unreal/CarlaUnreal/Plugins/Carla/Carla.uplugin" \
    "${stage_root}/Plugins/Carla/"
fi
if [[ -d "${cook_root}/CarlaUnreal/Plugins/Carla/Content" ]]; then
  rm -rf "${stage_root}/Plugins/Carla/Content"
  cp -a "${cook_root}/CarlaUnreal/Plugins/Carla/Content" \
    "${stage_root}/Plugins/Carla/"
fi
rm -rf "${stage_root}/Plugins/Carla/Shaders"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Plugins/Carla/Shaders" \
  "${stage_root}/Plugins/Carla/"

rm -rf "${stage_parent}/Engine/Config"
cp -a "${ue_dir}/Engine/Config" "${stage_parent}/Engine/"
mkdir -p "${stage_parent}/Engine/Content/Renderer"
cp -a "${ue_dir}/Engine/Content/Renderer/TessellationTable.bin" \
  "${stage_parent}/Engine/Content/Renderer/"
cp -a "${ue_dir}/Engine/Content/Slate" \
  "${stage_parent}/Engine/Content/"
if [[ ! -d "${stage_parent}/Engine/Content/Internationalization" ]]; then
  cp -a "${ue_dir}/Engine/Content/Internationalization" \
    "${stage_parent}/Engine/Content/"
fi
cp -a "${shader_cache}" \
  "${stage_parent}/Engine/OverrideGlobalShaderCache-VULKAN_SM6.bin"

stage_plugin() {
  local source_plugin="$1" target_plugin="$2"
  mkdir -p "${target_plugin}"
  cp -a "${source_plugin}"/*.uplugin "${target_plugin}/"
  local optional_dir
  for optional_dir in Config Content Resources; do
    if [[ -d "${source_plugin}/${optional_dir}" ]]; then
      cp -a "${source_plugin}/${optional_dir}" "${target_plugin}/"
    fi
  done
  if [[ -d "${source_plugin}/Binaries/LinuxArm64" ]]; then
    mkdir -p "${target_plugin}/Binaries"
    cp -a "${source_plugin}/Binaries/LinuxArm64" "${target_plugin}/Binaries/"
  fi
}

# Client and server link these modules statically, but UE resolves the enabled
# plugins through their staged descriptors while initializing the world.
for plugin in OnlineBase OnlineSubsystem OnlineSubsystemUtils OnlineServices; do
  stage_plugin \
    "${ue_dir}/Engine/Plugins/Online/${plugin}" \
    "${stage_parent}/Engine/Plugins/Online/${plugin}"
done

for plugin_spec in \
  "Runtime/ProceduralMeshComponent:ProceduralMeshComponent" \
  "Experimental/ChaosVehiclesPlugin:ChaosVehiclesPlugin" \
  "Runtime/SunPosition:SunPosition" \
  "EnhancedInput:EnhancedInput"; do
  plugin_path="${plugin_spec%%:*}"
  plugin="${plugin_spec##*:}"
  stage_plugin \
    "${ue_dir}/Engine/Plugins/${plugin_path}" \
    "${stage_parent}/Engine/Plugins/${plugin_path}"
done

find "${stage_root}" -type f -size +0c -printf "%P %s\n" | sort > "${run_dir}/files.txt"
du -sb "${stage_root}" | awk '{print $1}' > "${run_dir}/bytes.txt"
printf "%s\n" "${stage_root}" > "${run_dir}/stage-root.txt"
printf "%s\n" "${cook_root}" > "${run_dir}/cook-root.txt"
printf "%s\n" "${shader_cache}" > "${run_dir}/shader-cache.txt"
printf "PASS cooked-client-stage=%s stage=%s\n" "${run_dir}" "${stage_root}"
