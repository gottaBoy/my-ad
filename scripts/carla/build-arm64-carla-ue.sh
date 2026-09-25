#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi
jobs="${CARLA_BUILD_JOBS:-8}"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_BUILD_JOBS must be positive" >&2; exit 64; }
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
carla_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
carla_build_dir="${artifact_dir}/cmake-ue-arm64"
carla_toolchain="${carla_dir}/CMake/Toolchain.cmake"
ispc="${ue_dir}/Engine/Source/ThirdParty/Intel/ISPC/bin/Linux/ispc"
project="${carla_dir}/Unreal/CarlaUnreal/CarlaUnreal.uproject"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/carla-ue-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 CarlaUnreal\n\n- Status: %s\n- Step: %s\n- Scope: CarlaUnreal target build only; Server/RPC/Vulkan runtime still separate gates\n" \
    "${status}" "${step}" > "${run_dir}/decision.md"
  printf "%s carla-ue artifacts=%s step=%s\n" "${status}" "${run_dir}" "${step}"
}
trap finish EXIT

run_step() {
  step="$1"
  shift
  printf "step=%s\n" "${step}"
  "$@" 2>&1 | tee "${run_dir}/${step}.log"
}

[[ -f "${project}" ]] || { echo "CarlaUnreal project is missing: ${project}" >&2; exit 2; }
[[ -x "${ispc}" ]] || { echo "ARM64 ISPC is missing: ${ispc}" >&2; exit 2; }
[[ -f /usr/lib/llvm-18/lib/libc++.a && -f /usr/lib/llvm-18/lib/libc++abi.a ]] || {
  echo "LLVM 18 ARM64 libc++ libraries are missing" >&2
  exit 2
}
git config --global --add safe.directory "${carla_dir}"
git config --global --add safe.directory "${ue_dir}"
run_step carla-configure cmake -S "${carla_dir}" -B "${carla_build_dir}" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="${carla_toolchain}" \
  -DCARLA_UNREAL_ENGINE_PATH="${ue_dir}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_CARLA_UNREAL=ON -DBUILD_CARLA_SERVER=ON -DBUILD_CARLA_CLIENT=OFF \
  -DBUILD_PYTHON_API=OFF -DBUILD_EXAMPLES=OFF -DBUILD_LIBCARLA_TESTS=OFF \
  -DBOOST_ENABLE_PYTHON=ON -DBOOST_ENABLE_MPI=OFF \
  -DENABLE_ROS2=OFF -DCARLA_DLSS_SDK_PATH=disabled \
  -DCMAKE_CXX_STANDARD_LIBRARIES="/usr/lib/llvm-18/lib/libc++.a /usr/lib/llvm-18/lib/libc++abi.a -lm"
run_step libcarla-build cmake --build "${carla_build_dir}" --target carla-server \
  --parallel "${jobs}"
file -L "${ispc}" | tee "${run_dir}/ispc.txt"
grep -q "ARM aarch64" "${run_dir}/ispc.txt"
run_step ispc-version "${ispc}" --version
grep -q "1.24.0" "${run_dir}/ispc-version.log"

run_step rebuild-ubt dotnet build "${ue_dir}/Engine/Source/Programs/UnrealBuildTool/UnrealBuildTool.csproj" \
  -c Development -v quiet
run_step build-scw timeout "${CARLA_SCW_BUILD_TIMEOUT:-1800}" \
  bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh" \
  ShaderCompileWorker Linux Development -NoDumpSyms "-MaxParallelActions=${jobs}"
run_step build timeout "${CARLA_UE_BUILD_TIMEOUT:-7200}" \
  bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh" CarlaUnreal LinuxArm64 Development \
  "-project=${project}" -game -buildscw -NoDumpSyms "-MaxParallelActions=${jobs}"

step=artifacts
find "${carla_dir}" "${ue_dir}/Engine/Binaries" -type f -name CarlaUnreal -perm /111 \
  -print > "${run_dir}/carla-unreal-files.txt"
[[ -s "${run_dir}/carla-unreal-files.txt" ]] || {
  echo "CarlaUnreal executable was not produced" >&2
  exit 1
}
while IFS= read -r binary; do
  file -L "${binary}" | tee -a "${run_dir}/binaries.txt"
  grep -q "ARM aarch64" "${run_dir}/binaries.txt"
done < "${run_dir}/carla-unreal-files.txt"
step=complete
