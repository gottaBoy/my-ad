#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
}
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
jobs="${CARLA_BUILD_JOBS:-8}"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_BUILD_JOBS must be positive" >&2; exit 64; }
mkdir -p "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/scw-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight
finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 ShaderCompileWorker\n\n- Status: %s\n- Step: %s\n- Scope: worker build and dynamic linkage only; not CARLA Server or GPU rendering\n" \
    "${status}" "${step}" > "${run_dir}/decision.md"
  printf "%s scw artifacts=%s step=%s\n" "${status}" "${run_dir}" "${step}"
}
trap finish EXIT
git_cmd=(git -c "safe.directory=${ue_dir}" -C "${ue_dir}")
"${git_cmd[@]}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
"${git_cmd[@]}" diff --binary HEAD -- > "${run_dir}/ue-working-tree.patch"
step=build
timeout "${CARLA_SCW_BUILD_TIMEOUT:-1800}" bash "${ue_dir}/Engine/Build/BatchFiles/Linux/Build.sh" \
  ShaderCompileWorker Linux Development "-MaxParallelActions=${jobs}" 2>&1 | tee "${run_dir}/build.log"
step=architecture
worker="${ue_dir}/Engine/Binaries/Linux/ShaderCompileWorker"
file "${worker}" | tee "${run_dir}/architecture.txt"
grep -q "ARM aarch64" "${run_dir}/architecture.txt"
step=dynamic-linkage
for binary in "${worker}" \
  "${ue_dir}/Engine/Binaries/Linux/libShaderCompileWorker-ShaderCompilerCommon.so" \
  "${ue_dir}/Engine/Binaries/Linux/libShaderCompileWorker-VulkanShaderFormat.so" \
  "${ue_dir}/Engine/Binaries/Linux/libShaderCompileWorker-ShaderFormatVectorVM.so" \
  "${ue_dir}/Engine/Binaries/Linux/libShaderCompileWorker-ShaderFormatOpenGL.so"; do
  log="${run_dir}/$(basename "${binary}").ldd.txt"
  env -u LD_LIBRARY_PATH ldd -r "${binary}" 2>&1 | tee "${log}"
  if grep -Eq "not found|undefined symbol" "${log}"; then
    echo "Unresolved dynamic dependency: ${binary}" >&2
    exit 1
  fi
done
step=shaderconductor-linkage
grep -q "ShaderConductor/Linux/aarch64-unknown-linux-gnueabi/libShaderConductor.so" \
  "${run_dir}/libShaderCompileWorker-ShaderCompilerCommon.so.ldd.txt"
step=complete
