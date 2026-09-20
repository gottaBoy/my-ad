#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

mode="${1:-all}"
case "${mode}" in
  all|hlslcc|shaderconductor) ;;
  *) echo "Usage: build-arm64-shader-deps.sh [all|hlslcc|shaderconductor]" >&2; exit 64 ;;
esac
[[ -f /.dockerenv ]] || { echo "Run this script in the carla-build container" >&2; exit 2; }
[[ "$(uname -m)" == aarch64 ]] || { echo "Native ARM64 is required" >&2; exit 2; }
jobs="${CARLA_BUILD_JOBS:-8}"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_BUILD_JOBS must be positive" >&2; exit 64; }

export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
third_party="${CARLA_UE_DIR}/Engine/Source/ThirdParty"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
build_dir="${artifact_dir}/shader-deps-arm64"
mkdir -p "${build_dir}"
run_dir="$(mktemp -d "${artifact_dir}/shader-deps-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 Shader Dependencies\n\n- Status: %s\n- Step: %s\n- Mode: %s\n- Scope: third-party shader tools only; not CARLA runtime\n" \
    "${status}" "${step}" "${mode}" > "${run_dir}/decision.md"
  printf "%s shader-deps artifacts=%s step=%s\n" "${status}" "${run_dir}" "${step}"
}
trap finish EXIT
run_step() {
  step="$1"
  shift
  printf "step=%s\n" "${step}"
  "$@" 2>&1 | tee "${run_dir}/${step}.log"
}
git_cmd=(git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}")
if [[ "${mode}" == all || "${mode}" == shaderconductor ]]; then
  step=source-patch
  patch="${script_dir}/patches/shaderconductor-native-arm64.patch"
  if "${git_cmd[@]}" apply --reverse --check "${patch}" 2>/dev/null; then
    echo "ShaderConductor ARM64 source patch is already applied"
  else
    "${git_cmd[@]}" apply --check "${patch}"
    "${git_cmd[@]}" apply "${patch}"
  fi
fi
"${git_cmd[@]}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
"${git_cmd[@]}" status --short > "${run_dir}/ue-status.txt"
"${git_cmd[@]}" diff --binary HEAD -- > "${run_dir}/ue-working-tree.patch"
sha256sum "${script_dir}/ue-arm64-third-party.cmake" "${BASH_SOURCE[0]}" \
  "${script_dir}/shader-smoke.hlsl" "${script_dir}/check-shader-output.py" \
  "${script_dir}/patches/shaderconductor-native-arm64.patch" > "${run_dir}/scripts.sha256"
run_step compiler "${CARLA_LLVM_BIN}/clang++" --version
cmake_args=(-G Ninja --toolchain "${script_dir}/ue-arm64-third-party.cmake" -DCMAKE_BUILD_TYPE=Release)

check_arm64() {
  "${CARLA_LLVM_BIN}/llvm-readelf" -h "$1" | tee "${run_dir}/$(basename "$1").elf.txt" \
    | awk '/Machine:/{found=1; if ($2 != "AArch64") bad=1} END{exit !found || bad}'
}
if [[ "${mode}" == all || "${mode}" == hlslcc ]]; then
  run_step hlslcc-configure cmake -S "${third_party}/hlslcc/hlslcc/projects" \
    -B "${build_dir}/hlslcc" "${cmake_args[@]}"
  run_step hlslcc-build cmake --build "${build_dir}/hlslcc" --parallel "${jobs}"
  step=hlslcc-architecture
  check_arm64 "${build_dir}/hlslcc/libhlslcc.a"
  run_step hlslcc-install install -D -m 0644 "${build_dir}/hlslcc/libhlslcc.a" \
    "${third_party}/hlslcc/hlslcc/lib/Linux/aarch64-unknown-linux-gnueabi/libhlslcc.a"
fi
if [[ "${mode}" == all || "${mode}" == shaderconductor ]]; then
  sc_build="${build_dir}/shaderconductor"
  sc_source="${third_party}/ShaderConductor/ShaderConductor"
  run_step shaderconductor-configure cmake -S "${sc_source}" -B "${sc_build}" "${cmake_args[@]}" \
    -DSC_ARCH_NAME=arm64 -DSC_EXPLICIT_DLLSHUTDOWN=ON -DDXC_EXPLICIT_DLLSHUTDOWN=ON \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH=\$ORIGIN \
    -DSPIRV_CROSS_ENABLE_TESTS=OFF -DSPIRV_TOOLS_BUILD_STATIC=ON \
    -DPYTHON_EXECUTABLE=/usr/bin/python3 -DPython3_EXECUTABLE=/usr/bin/python3
  run_step shaderconductor-build cmake --build "${sc_build}" --parallel "${jobs}" \
    --target ShaderConductorCmd
  step=shaderconductor-architecture
  for binary in Lib/libdxcompiler.so Lib/libShaderConductor.so Bin/ShaderConductorCmd; do
    check_arm64 "${sc_build}/${binary}"
  done
  for format in spirv dxil; do
    output="${run_dir}/smoke.${format}"
    [[ "${format}" == spirv ]] && output="${run_dir}/smoke.spv"
    run_step "smoke-${format}" env LD_LIBRARY_PATH="${sc_build}/Lib" \
      timeout 60 "${sc_build}/Bin/ShaderConductorCmd" \
      --input "${script_dir}/shader-smoke.hlsl" --output "${output}" --stage vs --target "${format}"
  done
  run_step smoke-containers python3 "${script_dir}/check-shader-output.py" \
    "${run_dir}/smoke.spv" "${run_dir}/smoke.dxil"
  destination="${CARLA_UE_DIR}/Engine/Binaries/ThirdParty/ShaderConductor/Linux/aarch64-unknown-linux-gnueabi"
  for binary in Lib/libdxcompiler.so Lib/libShaderConductor.so Bin/ShaderConductorCmd; do
    run_step "install-$(basename "${binary}")" install -D -m 0755 "${sc_build}/${binary}" \
      "${destination}/$(basename "${binary}")"
  done
  run_step smoke-deployed env -u LD_LIBRARY_PATH timeout 60 "${destination}/ShaderConductorCmd" \
    --input "${script_dir}/shader-smoke.hlsl" --output "${run_dir}/deployed.spv" --stage vs --target spirv
  run_step smoke-deployed-container python3 "${script_dir}/check-shader-output.py" \
    "${run_dir}/deployed.spv" "${run_dir}/smoke.dxil"
fi
step=complete
