#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-8}"
[[ "${jobs}" =~ ^[1-9][0-9]*$ ]] || { echo "CARLA_BUILD_JOBS must be positive" >&2; exit 64; }
if [[ ! -f /.dockerenv || "$(uname -m)" != aarch64 ]]; then
  echo "Run this script in the native ARM64 carla-build container" >&2
  exit 2
fi

export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
third_party="${CARLA_UE_DIR}/Engine/Source/ThirdParty"
ogg_source="${third_party}/Ogg/libogg-1.2.2"
opus_source="${third_party}/libOpus/opus-1.1"
triple=aarch64-unknown-linux-gnueabi
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build_dir="${artifact_dir}/audio-deps-arm64"
mkdir -p "${build_dir}"
run_dir="$(mktemp -d "${artifact_dir}/audio-deps-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
step=preflight

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 ]] && status=PASS
  printf "# ARM64 Editor Audio Dependencies\n\n- Status: %s\n- Step: %s\n- Scope: Ogg 1.2.2 and Opus 1.1 PIC archives, whole-archive shared linkage and codec smoke only; not Editor/Cook/runtime validation\n" \
    "${status}" "${step}" > "${run_dir}/decision.md"
  printf "%s audio-deps artifacts=%s step=%s\n" "${status}" "${run_dir}" "${step}"
}
trap finish EXIT

run_step() {
  step="$1"
  shift
  printf "step=%s\n" "${step}"
  printf "%q " "$@" > "${run_dir}/${step}.command.txt"
  printf "\n" >> "${run_dir}/${step}.command.txt"
  if "$@" > "${run_dir}/${step}.log" 2>&1; then
    tail -n 5 "${run_dir}/${step}.log"
  else
    local code=$?
    tail -n 60 "${run_dir}/${step}.log"
    return "${code}"
  fi
}

[[ -f "${ogg_source}/CMakeLists.txt" && -f "${opus_source}/Makefile.unix" ]] || {
  echo "Engine Ogg/Opus build sources are missing" >&2
  exit 2
}
shopt -s nullglob
sysroots=("${CARLA_UE_DIR}"/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v*_clang-*/"${triple}")
[[ "${#sysroots[@]}" == 1 ]] || { echo "Expected exactly one ARM64 UE sysroot" >&2; exit 2; }
sysroot="${sysroots[0]}"
cc=("${CARLA_LLVM_BIN}/clang" "--target=${triple}" "--sysroot=${sysroot}" -fuse-ld=lld)
git_cmd=(git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}")
"${git_cmd[@]}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
"${git_cmd[@]}" diff --binary HEAD > "${run_dir}/ue-tracked.patch"
sha256sum "${BASH_SOURCE[0]}" "${script_dir}/audio-smoke.c" \
  "${script_dir}/ue-arm64-third-party.cmake" > "${run_dir}/scripts.sha256"
find "${ogg_source}/src" "${ogg_source}/include" "${opus_source}/src" \
  "${opus_source}/silk" "${opus_source}/celt" "${opus_source}/include" \
  -type f \( -name "*.c" -o -name "*.h" \) -print0 \
  | sort -z | xargs -0 sha256sum > "${run_dir}/source-files.sha256"
sha256sum "${ogg_source}/CMakeLists.txt" "${ogg_source}/configure.ac" "${ogg_source}/ogg.pc.in" \
  "${opus_source}/Makefile.unix" "${opus_source}/package_version" "${opus_source}/"*.mk \
  >> "${run_dir}/source-files.sha256"
run_step compiler "${cc[@]}" --version

# The in-tree Ogg CMake writes generated headers into its source directory.
# Configure a copy to keep generated files out of the engine source tree.
ogg_copy="${build_dir}/ogg-source"
opus_copy="${build_dir}/opus-source"
mkdir -p "${ogg_copy}" "${opus_copy}"
cp -a "${ogg_source}/CMakeLists.txt" "${ogg_source}/configure.ac" "${ogg_source}/ogg.pc.in" \
  "${ogg_source}/COPYING" "${ogg_source}/src" "${ogg_source}/include" "${ogg_copy}/"
run_step ogg-configure cmake -S "${ogg_copy}" -B "${build_dir}/ogg" -G Ninja \
  --toolchain "${script_dir}/ue-arm64-third-party.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON
run_step ogg-build cmake --build "${build_dir}/ogg" --parallel "${jobs}" --target ogg

cp -a "${opus_source}/Makefile.unix" "${opus_source}/package_version" "${opus_source}/"*.mk \
  "${opus_source}/COPYING" "${opus_source}/src" "${opus_source}/silk" \
  "${opus_source}/celt" "${opus_source}/include" "${opus_copy}/"
cd "${opus_copy}"
# This upstream makefile selects the portable floating-point implementation.
# Force rebuilding so changes to compiler flags cannot reuse stale objects.
run_step opus-build env "CFLAGS=-fPIC -DHAVE_LRINT=1 -DHAVE_LRINTF=1 --target=${triple} --sysroot=${sysroot}" \
  make -s -B -f Makefile.unix -j "${jobs}" lib \
  "CC=${CARLA_LLVM_BIN}/clang" "AR=${CARLA_LLVM_BIN}/llvm-ar" \
  "RANLIB=${CARLA_LLVM_BIN}/llvm-ranlib"

ogg_archive="${build_dir}/ogg/libogg.a"
opus_archive="${opus_copy}/libopus.a"
step=architecture
for library in "${ogg_archive}" "${opus_archive}"; do
  header="${run_dir}/$(basename "${library}").elf.txt"
  "${CARLA_LLVM_BIN}/llvm-readelf" -h "${library}" > "${header}"
  awk '/Machine:/{found=1; if ($2 != "AArch64") bad=1} END{exit !found || bad}' "${header}"
done
probe_library="${run_dir}/libcarla_audio_probe.so"
run_step pic-link "${cc[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${ogg_archive}" "${opus_archive}" -Wl,--no-whole-archive -lm -o "${probe_library}"
run_step smoke-build "${cc[@]}" -DLINUX=1 -O2 -Wall -Wextra -Werror \
  -I"${ogg_source}/include" -I"${opus_source}/include" "${script_dir}/audio-smoke.c" \
  -L"${run_dir}" -lcarla_audio_probe -Wl,-rpath,\$ORIGIN -lm -o "${run_dir}/audio-smoke"
run_step smoke env -u LD_LIBRARY_PATH timeout 60 "${run_dir}/audio-smoke"

ogg_destination="${ogg_source}/lib/Unix/${triple}/libogg_fPIC.a"
opus_destination="${opus_source}/Unix/${triple}/libopus_fPIC.a"
run_step ogg-install install -D -m 0644 "${ogg_archive}" "${ogg_destination}"
run_step opus-install install -D -m 0644 "${opus_archive}" "${opus_destination}"
run_step deployed-pic-link "${cc[@]}" -shared -Wl,-z,defs -Wl,--whole-archive \
  "${ogg_destination}" "${opus_destination}" -Wl,--no-whole-archive -lm -o "${probe_library}"
run_step deployed-linkage env -u LD_LIBRARY_PATH ldd -r "${run_dir}/audio-smoke"
if grep -Eq "not found|undefined symbol" "${run_dir}/deployed-linkage.log"; then
  echo "Audio smoke has unresolved dynamic dependencies" >&2
  exit 1
fi
run_step deployed-smoke env -u LD_LIBRARY_PATH timeout 60 "${run_dir}/audio-smoke"
sha256sum "${ogg_destination}" "${opus_destination}" "${probe_library}" > "${run_dir}/installed.sha256"
step=complete
