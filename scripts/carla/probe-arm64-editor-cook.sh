#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

timeout_seconds="${CARLA_EDITOR_COOK_TIMEOUT:-600}"
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || {
  echo "CARLA_EDITOR_COOK_TIMEOUT must be positive" >&2
  exit 64
}
target_platform="${CARLA_EDITOR_COOK_TARGET_PLATFORM:-LinuxArm64Server}"
case "${target_platform}" in
  LinuxArm64Server|LinuxArm64Client|LinuxArm64) ;;
  *) echo "CARLA_EDITOR_COOK_TARGET_PLATFORM must be LinuxArm64Server, LinuxArm64Client or LinuxArm64" >&2; exit 64 ;;
esac
package_name="${CARLA_EDITOR_COOK_PACKAGE:-/Game/Carla/RT_LuminanceCapture}"
package_extension="${CARLA_EDITOR_COOK_PACKAGE_EXTENSION:-uasset}"
[[ "${package_name}" == /Game/* ]] || {
  echo "CARLA_EDITOR_COOK_PACKAGE must be a /Game package path" >&2
  exit 64
}
case "${package_extension}" in
  uasset|umap) ;;
  *) echo "CARLA_EDITOR_COOK_PACKAGE_EXTENSION must be uasset or umap" >&2; exit 64 ;;
esac
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
editor="${ue_dir}/Engine/Binaries/LinuxArm64/UnrealEditor"
project="${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/editor-cook-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
output_dir="${run_dir}/cooked"
package_file="$(basename "${package_name}").${package_extension}"
step=preflight

finish() {
  local code=$? status=BLOCKED
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 CarlaUnrealEditor Cook Probe\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Timeout: %ss\n- Target platform: %s\n- Package: %s\n- Scope: one explicitly requested project package with hard and soft references skipped\n- Excluded: full project Cook, Vulkan rendering, RPC, sensors, and full CARLA runtime support\n" \
    "${status}" "${step}" "${code}" "${timeout_seconds}" "${target_platform}" "${package_name}" \
    > "${run_dir}/decision.md"
  printf "%s editor-cook artifacts=%s step=%s exit=%s\n" \
    "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

[[ -x "${editor}" ]] || { echo "Editor binary is missing: ${editor}" >&2; exit 2; }
[[ -f "${project}" ]] || { echo "CarlaUnreal project is missing: ${project}" >&2; exit 2; }

command=(
  timeout --signal=INT --kill-after=10 "${timeout_seconds}" "${editor}"
  "${project}"
  -run=Cook
  -targetplatform="${target_platform}"
  -cooksinglepackagenorefs
  -cookfaststartup
  -map="${package_name}"
  -package_extension="${package_extension}"
  -outputdir="${output_dir}"
  -SkipZenStore
  -nullrhi
  -nosound
  -unattended
  -NoSplash
  -notraceserver
  -stdout
  -FullStdOutLogOutput
  -ddpi:LinuxArm64:bIsEnabled=true
  -ddc=NoZenLocalFallback
  -NoAssetRegistryCacheWrite
  "-ini:Editor:[/Script/SourceControl.SourceControlPreferences]:bEnableUncontrolledChangelists=False"
)
printf "%q " "${command[@]}" > "${run_dir}/command.txt"
printf "\n" >> "${run_dir}/command.txt"
printf "%s\n" "${target_platform}" > "${run_dir}/target-platform.txt"
printf "%s\n" "${package_name}" > "${run_dir}/package.txt"
printf "%s\n" "${package_extension}" > "${run_dir}/package-extension.txt"
for source in carla ue; do
  source_var="${source}_dir"
  git_cmd=(git -c "safe.directory=${!source_var}" -C "${!source_var}")
  "${git_cmd[@]}" rev-parse HEAD > "${run_dir}/${source}-commit.txt"
  "${git_cmd[@]}" diff --binary HEAD > "${run_dir}/${source}-tracked.patch"
done

step=editor-cook
ulimit -c 0
set +e
"${command[@]}" 2>&1 | tee "${run_dir}/cook.log"
exit_code=${PIPESTATUS[0]}
set -e
printf "%s\n" "${exit_code}" > "${run_dir}/exit-code.txt"
step=log-validation

if [[ "${exit_code}" -ne 0 ]]; then
  echo "Editor cook failed: exit ${exit_code}; timeout and signals are failures" >&2
  exit "${exit_code}"
fi

required_markers=(
  "Cook by the book total time in tick"
  "Peak Used virtual"
)
for marker in "${required_markers[@]}"; do
  if ! grep -aFq "${marker}" "${run_dir}/cook.log"; then
    echo "Required Editor cook marker is missing: ${marker}" >&2
    exit 3
  fi
done
if ! grep -aEq "Packages Cooked: 1,.* Packages Skipped by Platform: 0, Total Packages: 1" \
  "${run_dir}/cook.log"; then
  echo "Cook did not complete exactly the requested package with no platform skips" >&2
  exit 3
fi

rejected_markers=(
  "LogCook: Error:"
  "LogCookCommandlet: Error:"
  "SIGSEGV"
  "Fatal error!"
  "Unhandled Exception:"
  "Signal 3 caught"
  "Signal 7 caught"
  "Exiting abnormally"
)
for marker in "${rejected_markers[@]}"; do
  if grep -aFq "${marker}" "${run_dir}/cook.log"; then
    echo "Forbidden Editor cook marker is present: ${marker}" >&2
    exit 3
  fi
done

step=output-validation
cooked_packages=()
while IFS= read -r -d '' cooked_package; do
  cooked_packages+=("${cooked_package}")
done < <(find "${output_dir}" -type f -name "${package_file}" -print0 2>/dev/null)
if [[ "${#cooked_packages[@]}" -ne 1 ]]; then
  echo "Expected exactly one cooked package named ${package_file}, found ${#cooked_packages[@]}" >&2
  exit 3
fi
printf "%s\n" "${cooked_packages[0]}" > "${run_dir}/cooked-package.txt"

step=complete
