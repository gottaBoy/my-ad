#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

profile="${CARLA_EDITOR_PROFILE:-full}"
case "${profile}" in
  full|no-usd|legacy-fbx-headers-only|fbx-skip) ;;
  *) echo "CARLA_EDITOR_PROFILE must be full, no-usd, legacy-fbx-headers-only or fbx-skip" >&2; exit 64 ;;
esac
headers_only="${CARLA_ARM64_FBX_HEADERS_ONLY:-0}"
case "${headers_only}" in
  0|1) ;;
  *) echo "CARLA_ARM64_FBX_HEADERS_ONLY must be 0 or 1" >&2; exit 64 ;;
esac
if [[ "${headers_only}" == 1 && "${profile}" != legacy-fbx-headers-only ]]; then
  echo "CARLA_ARM64_FBX_HEADERS_ONLY=1 requires the legacy-fbx-headers-only profile" >&2
  exit 64
fi
export CARLA_ARM64_FBX_HEADERS_ONLY=0
fbx_skip=0
if [[ "${profile}" == fbx-skip ]]; then
  fbx_skip=1
  export CARLA_DISABLE_ISPC=1
fi
export CARLA_ARM64_FBX_SKIP="${fbx_skip}"
check_timeout="${CARLA_EDITOR_CHECK_TIMEOUT:-120}"
[[ "${check_timeout}" =~ ^[1-9][0-9]*$ ]] || {
  echo "CARLA_EDITOR_CHECK_TIMEOUT must be positive" >&2
  exit 64
}
build_mode="${CARLA_EDITOR_BUILD:-0}"
case "${build_mode}" in
  0|1) ;;
  *) echo "CARLA_EDITOR_BUILD must be 0 or 1" >&2; exit 64 ;;
esac
if [[ "${build_mode}" == 1 ]]; then
  check_timeout="${CARLA_EDITOR_BUILD_TIMEOUT:-14400}"
  [[ "${check_timeout}" =~ ^[1-9][0-9]*$ ]] || {
    echo "CARLA_EDITOR_BUILD_TIMEOUT must be positive" >&2
    exit 64
  }
fi
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
project="${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/editor-check-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight

finish() {
  local code=$? status=BLOCKED
  [[ "${code}" == 0 ]] && status=PASS
  if [[ "${build_mode}" == 1 ]]; then
    printf "# ARM64 CarlaUnrealEditor Build\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Profile: %s\n- Scope: native Editor link; no Cook or runtime validation implied\n- Editor acceptance: NOT RUN; fbx-skip omits the FBX library\n" \
      "${status}" "${step}" "${code}" "${profile}" > "${run_dir}/decision.md"
  else
    printf "# ARM64 CarlaUnrealEditor Dependency Check\n\n- Status: %s\n- Step: %s\n- Exit code: %s\n- Profile: %s\n- Scope: UBT dependency graph only; no successful Editor build, Cook or runtime implied\n- Editor acceptance: NOT RUN; headers-only omits the required FBX library\n" \
      "${status}" "${step}" "${code}" "${profile}" > "${run_dir}/decision.md"
  fi
  printf "%s editor-check artifacts=%s step=%s exit=%s\n" "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

[[ -f "${project}" ]] || { echo "CarlaUnreal project is missing: ${project}" >&2; exit 2; }
printf "%s\n" "${profile}" > "${run_dir}/profile.txt"
if [[ -n "${CARLA_USD_NATIVE_ROOT:-}" ]]; then
  [[ "${profile}" == full || "${profile}" == legacy-fbx-headers-only ]] || {
    echo "Native SDK binding requires the full or legacy-fbx-headers-only profile" >&2
    exit 64
  }
  step=native-sdk-verification
  python3 -B "${script_dir}/prepare_native_usd_sdk.py" verify --ue-root "${ue_dir}" \
    --sdk-root "${CARLA_USD_NATIVE_ROOT}" > "${run_dir}/native-sdk-verification.log" 2>&1
  cp "${CARLA_USD_NATIVE_ROOT}/native-sdk.json" "${run_dir}/native-sdk.json"
fi
export CARLA_UE_DISABLE_USD=0
if [[ "${profile}" == no-usd ]]; then
  step=usd-policy
  git_cmd=(git -c "safe.directory=${ue_dir}" -C "${ue_dir}")
  patch="${script_dir}/patches/usd-arm64-opt-out.patch"
  if "${git_cmd[@]}" apply --reverse --check "${patch}" > "${run_dir}/patch-check.log" 2>&1; then
    printf "USD opt-out patch is already applied\n"
  else
    "${git_cmd[@]}" apply --check "${patch}" >> "${run_dir}/patch-check.log" 2>&1
    "${git_cmd[@]}" apply "${patch}"
  fi
  cp "${patch}" "${run_dir}/"
  export CARLA_UE_DISABLE_USD=1
fi
if [[ "${profile}" == legacy-fbx-headers-only ]]; then
  export CARLA_ARM64_FBX_HEADERS_ONLY=1
fi
printf "CARLA_UE_DISABLE_USD=%s\n" "${CARLA_UE_DISABLE_USD}" > "${run_dir}/profile.env"
if [[ -n "${CARLA_ARM64_FBX_HEADERS_ONLY:-}" ]]; then
  printf "CARLA_ARM64_FBX_HEADERS_ONLY=%s\n" "${CARLA_ARM64_FBX_HEADERS_ONLY}" >> "${run_dir}/profile.env"
fi
printf "CARLA_ARM64_FBX_SKIP=%s\n" "${CARLA_ARM64_FBX_SKIP}" >> "${run_dir}/profile.env"
if [[ -n "${CARLA_DISABLE_ISPC:-}" ]]; then
  printf "CARLA_DISABLE_ISPC=%s\n" "${CARLA_DISABLE_ISPC}" >> "${run_dir}/profile.env"
fi
printf "CARLA_EDITOR_BUILD=%s\n" "${build_mode}" >> "${run_dir}/profile.env"
for source in carla ue; do
  source_var="${source}_dir"
  git_cmd=(git -c "safe.directory=${!source_var}" -C "${!source_var}")
  "${git_cmd[@]}" rev-parse HEAD > "${run_dir}/${source}-commit.txt"
  "${git_cmd[@]}" diff --binary HEAD > "${run_dir}/${source}-tracked.patch"
done

# LinuxArm64 keeps compiler definitions and runtime plugin lookup on the same ARM64 platform.
command=(
  timeout --kill-after=10 "${check_timeout}"
  bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh"
  CarlaUnrealEditor LinuxArm64 Development
  "-project=${project}"
  -buildubt
  "-Log=${run_dir}/ubt.log"
)
if [[ "${build_mode}" == 0 ]]; then
  command+=(-SkipBuild -NoUBTMakefiles)
else
  command+=(-NoDumpSyms)
fi
printf "%q " "${command[@]}" > "${run_dir}/command.txt"
printf "\n" >> "${run_dir}/command.txt"
step=dependency-graph
"${command[@]}" 2>&1 | tee "${run_dir}/check.log"
step=complete
