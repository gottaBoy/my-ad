#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

version="${CARLA_ISPC_VERSION:-1.24.0}"
if [[ "${version}" != 1.24.0 ]]; then
  echo "Only the Unreal-pinned ISPC version 1.24.0 is supported" >&2
  exit 64
fi
jobs="${CARLA_BUILD_JOBS:-8}"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_BUILD_JOBS must be positive" >&2; exit 64; }

ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
source_dir="${ue_dir}/Engine/Source/ThirdParty/Intel/ISPC/ispc-${version}"
build_dir="${artifact_dir}/ispc-arm64/${version}"
destination="${ue_dir}/Engine/Source/ThirdParty/Intel/ISPC/bin/Linux/ispc"
llvm_bin="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
clang_cpp="${CARLA_CLANG_CPP:-/usr/lib/llvm-18/lib/libclang-cpp.so.18.1}"
llvm_source_version="18.1.8"
llvm_project_dir="${CARLA_LLVM_PROJECT_DIR:-${artifact_dir}/llvm-project-${llvm_source_version}}"
llvm_clang_build_dir="${CARLA_LLVM_CLANG_BUILD_DIR:-${artifact_dir}/llvm-clang-headers}"
clang_source_headers="${llvm_project_dir}/clang/include"
clang_generated_headers="${CARLA_CLANG_HEADERS:-${llvm_clang_build_dir}/tools/clang/include}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "${build_dir}" "${artifact_dir}"
run_dir="$(mktemp -d "${artifact_dir}/ispc-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 ISPC Host Compiler\n\n- Status: %s\n- Step: %s\n- Version: %s\n- Scope: Unreal host compiler only; target object smoke test included\n" \
    "${status}" "${step}" "${version}" > "${run_dir}/decision.md"
  printf "%s ispc artifacts=%s step=%s\n" "${status}" "${run_dir}" "${step}"
}
trap finish EXIT

run_step() {
  step="$1"
  shift
  printf "step=%s\n" "${step}"
  "$@" 2>&1 | tee "${run_dir}/${step}.log"
}

[[ -d "${source_dir}" && -f "${source_dir}/CMakeLists.txt" ]] || {
  echo "ISPC source ${source_dir} is missing" >&2
  exit 2
}
[[ -x "${llvm_bin}/clang++" && -x "${llvm_bin}/llvm-config" ]] || {
  echo "LLVM 18 tools are missing under ${llvm_bin}" >&2
  exit 2
}
[[ -f "${clang_cpp}" ]] || { echo "Clang shared library ${clang_cpp} is missing" >&2; exit 2; }
if [[ ! -f "${clang_source_headers}/clang/Basic/CharInfo.h" ]]; then
  llvm_archive="${artifact_dir}/llvm-project-llvmorg-${llvm_source_version}.tar.gz"
  llvm_tmp="${llvm_project_dir}.tmp"
  [[ ! -e "${llvm_tmp}" ]] || { echo "Incomplete LLVM source cache exists: ${llvm_tmp}" >&2; exit 2; }
  if [[ ! -f "${llvm_archive}" ]]; then
    run_step download-llvm curl -fL --retry 3 --retry-delay 2 \
      "https://github.com/llvm/llvm-project/archive/refs/tags/llvmorg-${llvm_source_version}.tar.gz" \
      -o "${llvm_archive}"
  fi
  mkdir -p "${llvm_tmp}"
  run_step extract-llvm tar -xzf "${llvm_archive}" --strip-components=1 -C "${llvm_tmp}"
  mv "${llvm_tmp}" "${llvm_project_dir}"
fi
[[ -f "${clang_source_headers}/clang/Basic/CharInfo.h" ]] || {
  echo "Clang C++ headers are missing under ${clang_source_headers}" >&2
  exit 2
}
if [[ ! -f "${clang_generated_headers}/clang/Basic/DiagnosticCommonKinds.inc" ]]; then
  clang_cmake_args=(
    -G Ninja
    -DCMAKE_BUILD_TYPE=Release
    -DLLVM_ENABLE_PROJECTS=clang
    -DLLVM_TARGETS_TO_BUILD=AArch64
    -DLLVM_BUILD_TOOLS=ON
    -DLLVM_INCLUDE_TOOLS=ON
    -DCLANG_INCLUDE_TESTS=OFF
    -DLLVM_INCLUDE_TESTS=OFF
    -DLLVM_INCLUDE_EXAMPLES=OFF
    -DLLVM_INCLUDE_BENCHMARKS=OFF
    -DLLVM_ENABLE_TERMINFO=OFF
    -DLLVM_ENABLE_ZLIB=OFF
    -DLLVM_ENABLE_LIBXML2=OFF
    -DLLVM_ENABLE_BINDINGS=OFF
  )
  run_step clang-headers-configure cmake -S "${llvm_project_dir}/llvm" \
    -B "${llvm_clang_build_dir}" "${clang_cmake_args[@]}"
  run_step clang-headers-build cmake --build "${llvm_clang_build_dir}" \
    --parallel "${jobs}" --target clangBasic
fi
[[ -f "${clang_generated_headers}/clang/Basic/DiagnosticCommonKinds.inc" ]] || {
  echo "LLVM Clang generated headers are missing under ${clang_generated_headers}" >&2
  exit 2
}
run_step compiler "${llvm_bin}/clang++" --version
run_step llvm-config "${llvm_bin}/llvm-config" --version
git config --global --add safe.directory "${ue_dir}"

cmake_args=(
  -G Ninja
  -DCMAKE_BUILD_TYPE=Release
  -DCMAKE_C_COMPILER="${llvm_bin}/clang"
  -DCMAKE_CXX_COMPILER="${llvm_bin}/clang++"
  -DLLVM_CONFIG_EXECUTABLE="${llvm_bin}/llvm-config"
  -DLLVM_DIR="${llvm_bin}/../lib/cmake/llvm"
  -DCMAKE_C_FLAGS="-isystem/usr/include/aarch64-linux-gnu"
  -DCMAKE_CXX_FLAGS="-isystem/usr/include/aarch64-linux-gnu -isystem${clang_generated_headers} -isystem${clang_source_headers}"
  -DclangFrontendPath="${clang_cpp}"
  -DclangBasicPath="${clang_cpp}"
  -DclangEditPath="${clang_cpp}"
  -DclangLexPath="${clang_cpp}"
  -DclangSupportPath="${clang_cpp}"
  -DclangASTMatchersPath="${clang_cpp}"
  -DX86_ENABLED=OFF
  -DARM_ENABLED=ON
  -DWASM_ENABLED=OFF
  -DXE_ENABLED=OFF
  -DISPC_INCLUDE_EXAMPLES=OFF
  -DISPC_INCLUDE_TESTS=OFF
  -DISPC_INCLUDE_BENCHMARKS=OFF
  -DISPC_INCLUDE_RT=OFF
  -DISPC_INCLUDE_UTILS=OFF
  -DISPC_CROSS=OFF
)
run_step configure cmake -S "${source_dir}" -B "${build_dir}" "${cmake_args[@]}"
run_step build cmake --build "${build_dir}" --parallel "${jobs}" --target ispc

candidate="${build_dir}/bin/ispc"
[[ -x "${candidate}" ]] || { echo "ISPC build did not produce ${candidate}" >&2; exit 1; }
step=architecture
file "${candidate}" | tee "${run_dir}/architecture.txt"
grep -q "ARM aarch64" "${run_dir}/architecture.txt"
run_step version "${candidate}" --version
grep -q "1.24.0" "${run_dir}/version.log"
run_step linkage env -u LD_LIBRARY_PATH ldd -r "${candidate}"
if grep -Eq "not found|undefined symbol" "${run_dir}/linkage.log"; then
  echo "ISPC has unresolved dynamic dependencies" >&2
  exit 1
fi

step=target-smoke
smoke_dir="${run_dir}/smoke"
mkdir -p "${smoke_dir}"
run_step target-smoke "${candidate}" "${script_dir}/ispc-smoke.ispc" \
  --target=neon-i32x4 --arch=aarch64 --pic --emit-obj \
  -h "${smoke_dir}/ispc-smoke.h" -o "${smoke_dir}/ispc-smoke.o"
[[ -s "${smoke_dir}/ispc-smoke.h" && -s "${smoke_dir}/ispc-smoke.o" ]]
file "${smoke_dir}/ispc-smoke.o" | tee "${run_dir}/target-object.txt"
grep -q "ARM aarch64" "${run_dir}/target-object.txt"

step=install
if [[ -f "${destination}" ]] && file -L "${destination}" | grep -Eq "x86-64|Intel 80386"; then
  backup="${destination}.x86_64"
  [[ -e "${backup}" ]] || mv "${destination}" "${backup}"
fi
install -D -m 0755 "${candidate}" "${destination}"
run_step deployed-version "${destination}" --version
run_step deployed-smoke "${destination}" "${script_dir}/ispc-smoke.ispc" \
  --target=neon-i32x4 --arch=aarch64 --pic --emit-obj \
  -h "${smoke_dir}/deployed.h" -o "${smoke_dir}/deployed.o"
step=complete
