#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

cook_root="${CARLA_FULL_COOK_OUTPUT:-}"
stage_root="${CARLA_COOKED_SERVER_STAGE:-/artifacts/carla/cooked-server-full/CarlaUnreal}"
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi
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
[[ -x "${carla_dir}/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnrealServer" ]] || {
  echo "ARM64 server binary is missing" >&2
  exit 2
}

run_dir="$(mktemp -d "${artifact_dir}/cooked-server-stage-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
mkdir -p "${run_dir}/CarlaUnreal"
rm -rf "${stage_root}"
mkdir -p "${stage_root}"
cp -a "${cook_root}/CarlaUnreal/." "${stage_root}/"
if [[ -d "${cook_root}/Engine" ]]; then
  cp -a "${cook_root}/Engine" "${stage_root}/../"
fi

cp -a "${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject" "${stage_root}/"
mkdir -p "${stage_root}/Binaries/LinuxArm64"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnrealServer" \
  "${stage_root}/Binaries/LinuxArm64/"
if [[ -f "${carla_dir}/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnrealServer.sym" ]]; then
  cp -a "${carla_dir}/Unreal/CarlaUnreal/Binaries/LinuxArm64/CarlaUnrealServer.sym" \
    "${stage_root}/Binaries/LinuxArm64/"
fi

mkdir -p "${stage_root}/Content/Carla/Config" \
  "${stage_root}/Content/Carla/Maps/OpenDrive" "${stage_root}/Content/Carla/Maps/Nav"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Content/Carla/Config/." \
  "${stage_root}/Content/Carla/Config/"
python3 "${script_dir}/normalize-runtime-config.py" --stage-root "${stage_root}"
find "${carla_dir}/Unreal/CarlaUnreal/Content/Carla/Maps/OpenDrive" \
  -maxdepth 1 -type f -name '*.xodr' -exec cp -a {} "${stage_root}/Content/Carla/Maps/OpenDrive/" \;
find "${carla_dir}/Unreal/CarlaUnreal/Content/Carla/Maps/Nav" \
  -maxdepth 1 -type f -name '*.bin' -exec cp -a {} "${stage_root}/Content/Carla/Maps/Nav/" \;

if [[ -d "${carla_dir}/Unreal/CarlaUnreal/Config" ]]; then
  cp -a "${carla_dir}/Unreal/CarlaUnreal/Config" "${stage_root}/"
fi
mkdir -p "${stage_root}/Plugins/Carla"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Plugins/Carla/Carla.uplugin" \
  "${stage_root}/Plugins/Carla/"
rm -rf "${stage_root}/Plugins/Carla/Shaders"
cp -a "${carla_dir}/Unreal/CarlaUnreal/Plugins/Carla/Shaders" \
  "${stage_root}/Plugins/Carla/"
mkdir -p "${stage_root}/../Engine/Content"
rm -rf "${stage_root}/../Engine/Content/Internationalization"
cp -a "${ue_dir}/Engine/Content/Internationalization" \
  "${stage_root}/../Engine/Content/"
mkdir -p "${stage_root}/../Engine/Content/Renderer"
cp -a "${ue_dir}/Engine/Content/Renderer/TessellationTable.bin" \
  "${stage_root}/../Engine/Content/Renderer/"
rm -rf "${stage_root}/../Engine/Config"
cp -a "${ue_dir}/Engine/Config" "${stage_root}/../Engine/"

# The server binary links against OSS interfaces, but UE still resolves the
# plugin modules through their staged descriptors at world initialization.
for plugin in OnlineBase OnlineSubsystem OnlineSubsystemUtils OnlineServices; do
  source_plugin="${ue_dir}/Engine/Plugins/Online/${plugin}"
  target_plugin="${stage_root}/../Engine/Plugins/Online/${plugin}"
  mkdir -p "${target_plugin}"
  cp -a "${source_plugin}/${plugin}.uplugin" "${target_plugin}/"
  for optional_dir in Config Content; do
    if [[ -d "${source_plugin}/${optional_dir}" ]]; then
      cp -a "${source_plugin}/${optional_dir}" "${target_plugin}/"
    fi
  done
  if [[ -d "${source_plugin}/Binaries/LinuxArm64" ]]; then
    mkdir -p "${target_plugin}/Binaries"
    cp -a "${source_plugin}/Binaries/LinuxArm64" "${target_plugin}/Binaries/"
  fi
done

for plugin_spec in \
  "Runtime/ProceduralMeshComponent:ProceduralMeshComponent" \
  "Experimental/ChaosVehiclesPlugin:ChaosVehiclesPlugin" \
  "Runtime/SunPosition:SunPosition" \
  "Experimental/Volumetrics:Volumetrics" \
  "EnhancedInput:EnhancedInput"; do
  plugin_path="${plugin_spec%%:*}"
  plugin="${plugin_spec##*:}"
  source_plugin="${ue_dir}/Engine/Plugins/${plugin_path}"
  target_plugin="${stage_root}/../Engine/Plugins/${plugin_path}"
  mkdir -p "${target_plugin}"
  cp -a "${source_plugin}/${plugin}.uplugin" "${target_plugin}/"
  if [[ -d "${source_plugin}/Binaries/LinuxArm64" ]]; then
    mkdir -p "${target_plugin}/Binaries"
    cp -a "${source_plugin}/Binaries/LinuxArm64" "${target_plugin}/Binaries/"
  fi
done

rm -rf "${stage_root}/Plugins/CarlaTools"
find "${stage_root}" -type f -size +0c -printf "%P %s\n" | sort > "${run_dir}/files.txt"
du -sb "${stage_root}" | awk '{print $1}' > "${run_dir}/bytes.txt"
printf "%s\n" "${stage_root}" > "${run_dir}/stage-root.txt"
printf "%s\n" "${cook_root}" > "${run_dir}/cook-root.txt"
printf "PASS cooked-server-stage=%s stage=%s\n" "${run_dir}" "${stage_root}"
