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
editor="${CARLA_EDITOR_BINARY:-${ue_dir}/Engine/Binaries/LinuxArm64/UnrealEditor}"
project="${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/full-cook-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
output_dir="${run_dir}/cooked"
step=preflight

finish() {
  local code=$? status=BLOCKED
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 CarlaUnreal Full Cook\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Timeout: %ss\n- Target platform: %s\n- Rendering cook: %s\n- Output: %s\n- Scope: full project cook for one ARM64 runtime target\n- Excluded: server staging, CARLA client RPC, sensors, and traffic/walker gameplay\n" \
    "${status}" "${step}" "${code}" "${timeout_seconds}" "${target_platform}" "${rendering}" "${output_dir}" \
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
  -COOKDIR="${carla_dir}/Unreal/CarlaUnreal/Content/Carla"
  -outputdir="${output_dir}"
  -SkipZenStore
  -nosound
  -unattended
  -NoSplash
  -notraceserver
  -stdout
  -FullStdOutLogOutput
  -ddpi:LinuxArm64:bIsEnabled=true
  -ddc=NoZenLocalFallback
  -NoAssetRegistryCacheWrite
  -NoP4
)
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
    -ini:Engine:[SystemSettings]:r.RayTracing=0
    -ini:Engine:[SystemSettings]:r.RayTracing.EnableOnDemand=0
    -ini:Engine:[SystemSettings]:r.Lumen.DiffuseIndirect.Allow=0
    -ini:Engine:[SystemSettings]:r.Shadow.Virtual.Enable=0
    -ini:Engine:[SystemSettings]:r.VolumetricCloud=0
    -ini:Engine:[SystemSettings]:r.VirtualTextures=0
  )
fi
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

# COOKDIR is a filesystem path. A package path such as /Game/Carla is silently
# ignored by recursive enumeration and under-cooks the client.
for package_name in SM_PlasticBag SM_StreetAD01 SM_calibration; do
  package_path="${output_dir}/CarlaUnreal/Content/Carla/${package_name}.uasset"
  [[ -f "${package_path}" ]] || {
    echo "Expected cooked CARLA package is missing: ${package_name}" >&2
    exit 3
  }
done

step=complete

echo "Runtime Cook scope: ${carla_dir}/Unreal/CarlaUnreal/Content/Carla; project-declared exclusions: /CarlaTools,/Game/Carla/HoudiniEngine"
