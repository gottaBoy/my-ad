#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

timeout_seconds="${CARLA_FULL_COOK_TIMEOUT:-1800}"
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || {
  echo "CARLA_FULL_COOK_TIMEOUT must be positive" >&2
  exit 64
}
target_platform="${CARLA_FULL_COOK_TARGET_PLATFORM:-LinuxArm64Server}"
rendering="${CARLA_FULL_COOK_RENDERING:-0}"
vk_icd_filenames="${CARLA_VK_ICD_FILENAMES:-}"
case "${target_platform}" in
  LinuxArm64Server|LinuxArm64Client|LinuxArm64) ;;
  *) echo "CARLA_FULL_COOK_TARGET_PLATFORM must be LinuxArm64Server, LinuxArm64Client or LinuxArm64" >&2; exit 64 ;;
esac
[[ "${rendering}" == 0 || "${rendering}" == 1 ]] || {
  echo "CARLA_FULL_COOK_RENDERING must be 0 or 1" >&2
  exit 64
}
if [[ -n "${vk_icd_filenames}" && ! -f "${vk_icd_filenames}" ]]; then
  echo "CARLA_VK_ICD_FILENAMES must name an existing Vulkan ICD JSON" >&2
  exit 64
fi
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/arm64-renderer-scope.sh"
editor="${CARLA_EDITOR_BINARY:-${ue_dir}/Engine/Binaries/LinuxArm64/UnrealEditor}"
project="${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject"
maps_root="${carla_dir}/Unreal/CarlaUnreal/Content/Carla/Maps"
game_ini="${carla_dir}/Unreal/CarlaUnreal/Config/DefaultGame.ini"
[[ -d "${maps_root}" ]] || { echo "CARLA maps directory is missing: ${maps_root}" >&2; exit 2; }
[[ -f "${game_ini}" ]] || { echo "Project packaging config is missing: ${game_ini}" >&2; exit 2; }
[[ -d "${maps_root}/OpenDrive" && -d "${maps_root}/Sublevels" ]] || {
  echo "CARLA OpenDrive or streaming sublevel directory is missing: ${maps_root}" >&2
  exit 2
}

# Runtime map set for the coverage assertion: every map that owns a staged CARLA
# OpenDrive spec, merged with the project's own MapsToCook list.
map_requests=()
spec_count=0
while IFS= read -r -d '' spec; do
  spec_count=$((spec_count + 1))
  spec_name="$(basename "${spec}" .xodr)"
  [[ -f "${maps_root}/${spec_name}.umap" ]] || {
    echo "OpenDrive spec has no matching runtime map: ${spec}" >&2
    exit 2
  }
  map_requests+=("/Game/Carla/Maps/${spec_name}")
done < <(find "${maps_root}/OpenDrive" -maxdepth 1 -type f -name '*.xodr' -print0)
[[ "${spec_count}" -gt 0 ]] || {
  echo "No OpenDrive specs found in ${maps_root}/OpenDrive" >&2
  exit 2
}
while IFS= read -r ini_map; do
  [[ -n "${ini_map}" ]] || continue
  [[ "${ini_map}" == /Game/Carla/Maps/* && \
    -f "${maps_root}/${ini_map#/Game/Carla/Maps/}.umap" ]] || {
    echo "MapsToCook entry has no matching CARLA map: ${ini_map}" >&2
    exit 2
  }
  map_requests+=("${ini_map}")
done < <(sed -nE 's/^\+MapsToCook=\(FilePath="([^"]+)"\).*/\1/p' "${game_ini}")
mapfile -t cook_maps < <(printf '%s\n' "${map_requests[@]}" | grep -v '^$' | sort -u)
[[ "${#cook_maps[@]}" -ge 2 ]] || {
  echo "Derived fewer than two cook map requests; refusing an asset-only cook" >&2
  exit 2
}

mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/full-cook-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
output_dir="${run_dir}/cooked"
step=preflight

finish() {
  local code=$? status=BLOCKED
  [[ "${code}" == 0 ]] && status=PASS
  # Report the coverage that was actually asserted, so a narrower scope can never be
  # read back as a full project cook.
  local cooked_maps=0 cooked_sublevels=0
  if [[ -s "${run_dir}/cooked-maps.txt" ]]; then
    cooked_maps="$(wc -l < "${run_dir}/cooked-maps.txt")"
    cooked_sublevels="$(grep -ac '^Sublevels/' "${run_dir}/cooked-maps.txt" || true)"
  fi
  printf "# ARM64 CarlaUnreal Full Cook\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Timeout: %ss\n- Target platform: %s\n- Rendering cook: %s\n- Output: %s\n- Scope: project-wide -cookall for one ARM64 runtime target; %s map requests, %s cooked .umap of which %s streaming sublevels; every Carla sensor material asserted present\n- Excluded: server staging, CARLA client RPC, sensors, and traffic/walker gameplay\n" \
    "${status}" "${step}" "${code}" "${timeout_seconds}" "${target_platform}" "${rendering}" \
    "${output_dir}" "${#cook_maps[@]}" "${cooked_maps}" "${cooked_sublevels}" \
    > "${run_dir}/decision.md"
  printf "%s full-cook artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

[[ -x "${editor}" ]] || { echo "Editor binary is missing: ${editor}" >&2; exit 2; }
[[ -f "${project}" ]] || { echo "CarlaUnreal project is missing: ${project}" >&2; exit 2; }

command=(
  timeout --signal=INT --kill-after=30 "${timeout_seconds}" "${editor}"
  "${project}"
  -run=Cook
  -targetplatform="${target_platform}"
  -outputdir="${output_dir}"
  -SkipZenStore
  -nosound
  -unattended
  -NoSplash
  -notraceserver
  -stdout
  -FullStdOutLogOutput
  -ddpi:LinuxArm64:bIsEnabled=true
  -handleensurepercent=0
  -ddc=NoZenLocalFallback
  -NoAssetRegistryCacheWrite
  -NoP4
  -cookall
)
# A -COOKDIR walk only enumerates *.uasset (UCookOnTheFlyServer::CollectFilesToCook calls
# FindFilesRecursive with FPackageName::GetAssetPackageExtension), so it silently drops
# every *.umap and all plugin content, including the Carla/PostProcessingMaterials sensor
# materials. -cookall is the real project-wide scope; the runtime maps are still requested
# explicitly so a dropped map fails the gate instead of the staged client.
for cook_map in "${cook_maps[@]}"; do
  command+=(-MAP="${cook_map}")
done
[[ -z "${vk_icd_filenames}" ]] || export VK_ICD_FILENAMES="${vk_icd_filenames}"
if [[ "${rendering}" == 0 ]]; then
  command+=(-nullrhi)
else
  [[ -n "${vk_icd_filenames}" ]] || {
    echo "CARLA_VK_ICD_FILENAMES is required when CARLA_FULL_COOK_RENDERING=1" >&2
    exit 64
  }
  command+=(-AllowCommandletRendering -RenderOffScreen -AllowCPUDevices -SkipVulkanProfileCheck)
  command+=(
    -ini:Engine:[SF_VULKAN_SM6]:BindlessResources=Disabled
    -ini:Engine:[SF_VULKAN_SM6]:BindlessSamplers=Disabled
    -ini:Engine:[/Script/LinuxTargetPlatform.LinuxTargetSettings]:bEnableRayTracing=False
  )
  # Cook-time and run-time shader scope come from one shared list. r.VirtualTextures and
  # r.RayTracing are ECVF_ReadOnly, so a permutation the cook leaves out cannot be
  # switched back on by the cooked client: it aborts in FMaterial::GetShaderMap instead.
  while IFS= read -r renderer_flag; do
    command+=("${renderer_flag}")
  done < <(carla_renderer_systemsettings_flags)
fi
printf '%s\n' "${cook_maps[@]}" > "${run_dir}/cook-maps.txt"
printf "%q " "${command[@]}" > "${run_dir}/command.txt"
printf "\n" >> "${run_dir}/command.txt"
printf "%s\n" "${target_platform}" > "${run_dir}/target-platform.txt"
for source in carla ue; do
  source_var="${source}_dir"
  git_cmd=(git -c "safe.directory=${!source_var}" -C "${!source_var}")
  command -v git >/dev/null 2>&1 || git_cmd=(true)
  "${git_cmd[@]}" rev-parse HEAD > "${run_dir}/${source}-commit.txt"
  "${git_cmd[@]}" diff --binary HEAD > "${run_dir}/${source}-tracked.patch"
done

step=full-cook
ulimit -c 0
set +e
"${command[@]}" > "${run_dir}/cook.log" 2>&1
exit_code=$?
set -e
printf "%s\n" "${exit_code}" > "${run_dir}/exit-code.txt"
if [[ "${exit_code}" -ne 0 ]]; then
  echo "Full Editor cook failed: exit ${exit_code}; timeout and signals are failures" >&2
  exit "${exit_code}"
fi

step=log-validation
required_markers=(
  "Loaded TargetPlatform '${target_platform}'"
  "Building Assets For ${target_platform}"
  "Cook by the book total time in tick"
  "Packages Cooked:"
)
for marker in "${required_markers[@]}"; do
  if ! grep -aFq "${marker}" "${run_dir}/cook.log"; then
    echo "Required full cook marker is missing: ${marker}" >&2
    exit 3
  fi
done
rejected_markers=(
  "Invalid target platform specified"
  "No target platforms found"
  "LogCook: Error:"
  "LogCookCommandlet: Error:"
  "LogInit: Display: Failure -"
  "SIGSEGV"
  "Fatal error!"
  "Unhandled Exception:"
  "Signal 3 caught"
  "Signal 7 caught"
  "Exiting abnormally"
)
for marker in "${rejected_markers[@]}"; do
  if grep -aFq "${marker}" "${run_dir}/cook.log"; then
    echo "Forbidden full cook marker is present: ${marker}" >&2
    exit 3
  fi
done

step=output-validation
[[ -d "${output_dir}" ]] || { echo "Full cook output directory is missing: ${output_dir}" >&2; exit 3; }
if ! find "${output_dir}" -type f -print -quit | grep -q .; then
  echo "Full cook output directory is empty: ${output_dir}" >&2
  exit 3
fi
find "${output_dir}" -type f -printf "%P %s\n" | sort > "${run_dir}/output-files.txt"
du -sb "${output_dir}" | awk '{print $1}' > "${run_dir}/output-bytes.txt"

# Sentinels for the project Content/Carla asset tree. A package path such as
# /Game/Carla is silently ignored by recursive enumeration and under-cooks the client.
expected_packages=(
  "Static/Dynamic/00_LegacyAssets/PedestrianProps/SM_PlasticBag.uasset"
  "Static/Static/00_LegacyAssets/SM_StreetAD01.uasset"
  "Static/Static/Materials/Calibrator/SM_calibration.uasset"
)
for package_suffix in "${expected_packages[@]}"; do
  package_path="${output_dir}/CarlaUnreal/Content/Carla/${package_suffix}"
  [[ -f "${package_path}" ]] || {
    echo "Expected cooked CARLA package is missing: ${package_path}" >&2
    exit 3
  }
done

# Runtime content coverage. A cook that silently drops maps or sensor materials is not
# a full client cook: the staged client then dies in CARLA world setup ("has no SM
# assigned to the ISM") or in the sensor material path.
cooked_map_count=0
for cook_map in "${cook_maps[@]}"; do
  map_relative="${cook_map#/Game/Carla/Maps/}"
  cooked_map="${output_dir}/CarlaUnreal/Content/Carla/Maps/${map_relative}.umap"
  [[ -f "${cooked_map}" ]] || {
    echo "Requested map was not cooked: ${cooked_map}" >&2
    exit 3
  }
  cooked_map_count=$((cooked_map_count + 1))
done
[[ "${cooked_map_count}" -ge 2 ]] || {
  echo "Cooked map coverage is too narrow: ${cooked_map_count}" >&2
  exit 3
}
find "${output_dir}/CarlaUnreal/Content/Carla/Maps" -type f -name '*.umap' -printf '%P\n' \
  | sort > "${run_dir}/cooked-maps.txt"

# Every streaming sublevel the runtime maps load must be cooked.
missing_sublevels=0
source_sublevels=0
while IFS= read -r -d '' source_sublevel; do
  source_sublevels=$((source_sublevels + 1))
  relative="${source_sublevel#"${maps_root}/"}"
  [[ -f "${output_dir}/CarlaUnreal/Content/Carla/Maps/${relative}" ]] || {
    echo "Streaming sublevel was not cooked: ${relative}" >&2
    missing_sublevels=$((missing_sublevels + 1))
  }
done < <(find "${maps_root}/Sublevels" -type f -name '*.umap' -print0)
[[ "${source_sublevels}" -gt 0 ]] || {
  echo "No CARLA streaming sublevel maps found in ${maps_root}/Sublevels" >&2
  exit 3
}
[[ "${missing_sublevels}" -eq 0 ]] || {
  echo "${missing_sublevels} streaming sublevel map(s) missing from the cook" >&2
  exit 3
}

# Carla plugin sensor materials live outside Content/Carla and are invisible to a
# project-content directory walk.
sensor_materials="${carla_dir}/Unreal/CarlaUnreal/Plugins/Carla/Content/PostProcessingMaterials"
[[ -d "${sensor_materials}" ]] || {
  echo "CARLA sensor material directory is missing: ${sensor_materials}" >&2
  exit 2
}
missing_materials=0
source_materials=0
while IFS= read -r -d '' source_material; do
  source_materials=$((source_materials + 1))
  relative="${source_material#"${sensor_materials}/"}"
  [[ -f "${output_dir}/CarlaUnreal/Plugins/Carla/Content/PostProcessingMaterials/${relative}" ]] || {
    echo "Sensor material was not cooked: ${relative}" >&2
    missing_materials=$((missing_materials + 1))
  }
done < <(find "${sensor_materials}" -type f -name '*.uasset' -print0)
[[ "${source_materials}" -gt 0 ]] || {
  echo "No CARLA sensor materials found in ${sensor_materials}" >&2
  exit 3
}
[[ "${missing_materials}" -eq 0 ]] || {
  echo "${missing_materials} CARLA sensor material(s) missing from the cook" >&2
  exit 3
}

step=complete

echo "Runtime Cook scope: whole project (-cookall) plus ${#cook_maps[@]} explicit map requests; project-declared exclusions: /CarlaTools,/Game/Carla/HoudiniEngine"
