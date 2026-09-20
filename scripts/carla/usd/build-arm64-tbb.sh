#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
timeout_seconds="${CARLA_TBB_TIMEOUT_SECONDS:-1200}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be an integer from 1 to 4" >&2; exit 64; }
[[ "${timeout_seconds}" =~ ^[1-9][0-9]*$ && "${timeout_seconds}" -ge 61 && "${timeout_seconds}" -le 1200 ]] || {
  echo "CARLA_TBB_TIMEOUT_SECONDS must be an integer from 61 to 1200" >&2
  exit 64
}

if [[ "${CARLA_TBB_TIMEOUT_GUARD:-0}" != 1 ]]; then
  export CARLA_TBB_TIMEOUT_GUARD=1
  exec timeout --kill-after=30s "${timeout_seconds}s" bash "${BASH_SOURCE[0]}" "$@"
fi

[[ -f /.dockerenv && "$(uname -m)" == "aarch64" ]] || {
  echo "Run this subtask in the native ARM64 carla-dev container" >&2
  exit 2
}

version="2019u8"
triple="aarch64-unknown-linux-gnueabi"
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_root="${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scope="Native ARM64 TBB 2019u8 dynamic libraries and smoke; not static TBB, OpenUSD, UE Editor or USD SDK"
stage_id="carla-tbb-native-arm64"

mkdir -p "${artifact_root}"
run_dir="$(mktemp -d "${artifact_root}/tbb-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"

ue_source="${ue_dir}/Engine/Source/ThirdParty/Intel/TBB/IntelTBB-${version}"
source_copy="${run_dir}/source/IntelTBB-${version}"
build_root="${run_dir}/work/tbb"
install_dir="${run_dir}/install"
install_lib="${install_dir}/lib"
smoke_src="${script_dir}/tbb-smoke.cpp"
smoke_bin="${install_dir}/bin/tbb-smoke"
clang_root="${CARLA_TBB_CLANG_ROOT:-${ue_dir}/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v23_clang-18.1.0-rockylinux8/${triple}}"
clang="${clang_root}/bin/clang"
clangxx="${clang_root}/bin/clang++"
llvm_readelf="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}/llvm-readelf"
libcxx_root="${ue_dir}/Engine/Source/ThirdParty/Unix/LibCxx"
libcxx_include="${libcxx_root}/include"
libcxx_lib="${libcxx_root}/lib/Unix/${triple}"
step=preflight
ue_commit=unverified
mkdir -p "${run_dir}/source" "${run_dir}/work" "${install_lib}" "${install_dir}/bin"

finish() {
  local code=$? status=FAIL
  [[ "${code}" == 0 && "${step}" == complete ]] && status=PASS
  printf "# Native ARM64 TBB 2019u8\\n\\n- Status: %s\\n- Step: %s\\n- Exit code: %s\\n- Scope: %s\\n" "${status}" "${step}" "${code}" "${scope}" > "${run_dir}/decision.md"
  if [[ "${status}" == PASS ]]; then
    ln -sfn stage-report.json "${run_dir}/last-verified-stage.json"
  fi
  if [[ ! -s "${run_dir}/stage-report.json" || "${code}" != 0 ]]; then
    local failure_evidence="${run_dir}/${step}.log"
    [[ -f "${failure_evidence}" ]] || failure_evidence="${run_dir}/decision.md"
    python3 "${script_dir}/../stage_report.py" write --output "${run_dir}/stage-report.json" \
      --stage-id "${stage_id}" --scope "${scope}" --exit-code "${code}" \
      --required-check "${step}" --check "${step}" FAIL --evidence "${step}" "${failure_evidence}" \
      --source tbb "${source_copy}" "${ue_commit}" --source ue "${ue_dir}" "${ue_commit}" \
      --command bash "${BASH_SOURCE[0]}" > "${run_dir}/report-error.log" 2>&1 || true
  fi
  printf "%s tbb artifacts=%s step=%s exit=%s\\n" "${status}" "${run_dir}" "${step}" "${code}"
}
trap finish EXIT

run_step() {
  step="$1"
  shift
  printf "step=%s\\n" "${step}"
  printf "%q " "$@" > "${run_dir}/${step}.command.txt"
  printf "\\n" >> "${run_dir}/${step}.command.txt"
  if "$@" > "${run_dir}/${step}.log" 2>&1; then
    tail -n 12 "${run_dir}/${step}.log"
  else
    local code=$?
    tail -n 80 "${run_dir}/${step}.log"
    return "${code}"
  fi
}

[[ -d "${ue_source}" ]] || { echo "TBB source is missing: ${ue_source}" >&2; exit 2; }
[[ -x "${clang}" && -x "${clangxx}" ]] || { echo "UE ARM64 clang toolchain is missing: ${clang_root}" >&2; exit 2; }
[[ -x "${llvm_readelf}" ]] || { echo "llvm-readelf is missing: ${llvm_readelf}" >&2; exit 2; }
[[ -f "${libcxx_include}/c++/v1/__config" && -f "${libcxx_lib}/libc++.a" && -f "${libcxx_lib}/libc++abi.a" ]] || {
  echo "UE ARM64 libc++ inputs are incomplete" >&2
  exit 2
}
[[ -f "${smoke_src}" ]] || { echo "TBB smoke source is missing: ${smoke_src}" >&2; exit 2; }
ue_commit="$(git -c "safe.directory=${ue_dir}" -C "${ue_dir}" rev-parse HEAD 2>/dev/null || printf unverified)"
printf "%s\\n" "${ue_commit}" > "${run_dir}/ue-commit.txt"
git -c "safe.directory=${ue_dir}" -C "${ue_dir}" diff --binary HEAD > "${run_dir}/ue-tracked.patch" || true

run_step source-copy cp -a --no-preserve=ownership "${ue_source}/." "${source_copy}/"
source_diff() {
  diff -qr "${ue_source}" "${source_copy}" > "${run_dir}/source-diff.txt"
}
run_step source-diff source_diff
source_hash() {
  (cd "${ue_source}" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "${run_dir}/source-original.sha256"
  (cd "${source_copy}" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "${run_dir}/source-copy.sha256"
  cmp "${run_dir}/source-original.sha256" "${run_dir}/source-copy.sha256"
}
run_step source-hash source_hash
run_step source-version grep -E "TBB_VERSION_(MAJOR|MINOR|UPDATE)|2019|8" "${source_copy}/include/tbb/tbb_stddef.h"
printf "Static archive: NOT_IMPLEMENTED\\nReason: TBB 2019u8 Linux Makefile.tbb and Makefile.tbbmalloc link shared libraries only; no archive target is used.\\nNo .a was fabricated.\\n" > "${run_dir}/static-mode.txt"

toolchain_version() {
  printf "clang="
  "${clangxx}" --version
  printf "target="
  "${clangxx}" --target="${triple}" -dumpmachine
  printf "libcxx="
  sha256sum "${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a"
}
run_step toolchain-version toolchain_version

cxxflags="-std=c++11 -fPIC -stdlib=libc++ -nostdinc++ -isystem ${libcxx_include} -isystem ${libcxx_include}/c++/v1"
libs="-L${libcxx_lib} -lc++ -lc++abi -lm -lc -lgcc_s -lgcc -lutil"
build_command=(
  timeout --kill-after=30s "$((${timeout_seconds} - 60))s"
  make -C "${source_copy}" -j"${jobs}"
  tbb_build_dir="${run_dir}/work" work_dir="${build_root}"
  CPLUS="${clangxx}" CONLY="${clang}" compiler=clang arch=aarch64 runtime=ue-libcxx-arm64
  CXXFLAGS="${cxxflags}" LDFLAGS="-L${libcxx_lib}" LIBS="${libs}" stdlib=libc++
  tbb tbbmalloc
)
printf "%q " "${build_command[@]}" > "${run_dir}/build.command.txt"
printf "\\n" >> "${run_dir}/build.command.txt"
run_step build "${build_command[@]}"

copy_release_libraries() {
  local release_dir="${build_root}_release"
  for name in libtbb.so libtbb.so.2 libtbbmalloc.so libtbbmalloc.so.2; do
    [[ -e "${release_dir}/${name}" ]] || { echo "missing release output: ${release_dir}/${name}" >&2; return 1; }
    cp -a "${release_dir}/${name}" "${install_lib}/${name}"
  done
}
run_step stage-release copy_release_libraries
printf "\\n"
find "${install_lib}" -maxdepth 1 -type f -o -type l | sort > "${run_dir}/installed-files.txt"
sha256sum "${install_lib}"/* > "${run_dir}/installed.sha256"

architecture_check() {
  local binary
  for binary in "${install_lib}/libtbb.so.2" "${install_lib}/libtbbmalloc.so.2"; do
    file -L "${binary}" | tee -a "${run_dir}/architecture.txt"
    "${llvm_readelf}" -h "${binary}" > "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq "Class:[[:space:]]+ELF64" "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq "Machine:[[:space:]]+AArch64" "${run_dir}/$(basename "${binary}").readelf.txt"
    grep -Eq "Type:[[:space:]]+DYN" "${run_dir}/$(basename "${binary}").readelf.txt"
  done
}
run_step architecture architecture_check

smoke_command=(
  "${clangxx}" -std=c++11 -stdlib=libc++ -nostdinc++
  -isystem "${libcxx_include}" -isystem "${libcxx_include}/c++/v1"
  "${smoke_src}" -I"${source_copy}/include"
  -L"${install_lib}" -L"${libcxx_lib}" "-Wl,-rpath,\$ORIGIN/../lib"
  "${libcxx_lib}/libc++.a" "${libcxx_lib}/libc++abi.a"
  -ltbb -ltbbmalloc -lm -lpthread -ldl -o "${smoke_bin}"
)
printf "%q " "${smoke_command[@]}" > "${run_dir}/smoke-compile.command.txt"
printf "\\n" >> "${run_dir}/smoke-compile.command.txt"
run_step smoke-compile "${smoke_command[@]}"
architecture_check_smoke() {
  file -L "${smoke_bin}" | tee -a "${run_dir}/architecture.txt"
  "${llvm_readelf}" -h "${smoke_bin}" > "${run_dir}/tbb-smoke.readelf.txt"
  grep -Eq "Class:[[:space:]]+ELF64" "${run_dir}/tbb-smoke.readelf.txt"
  grep -Eq "Machine:[[:space:]]+AArch64" "${run_dir}/tbb-smoke.readelf.txt"
}
run_step smoke-architecture architecture_check_smoke
run_step linkage env -u LD_LIBRARY_PATH ldd -r "${smoke_bin}"
if grep -Eq "not found|undefined symbol" "${run_dir}/linkage.log"; then
  echo "TBB smoke has unresolved dynamic dependencies" >&2
  exit 1
fi
run_step smoke env -u LD_LIBRARY_PATH timeout 60 "${smoke_bin}"
grep -Fq "TBB smoke PASS" "${run_dir}/smoke.log"

retention_check() {
  local path
  for path in "${run_dir}/source-diff.txt" "${run_dir}/source-original.sha256" "${run_dir}/source-copy.sha256" "${run_dir}/source-version.log" "${run_dir}/toolchain-version.log" "${run_dir}/build.log" "${run_dir}/architecture.txt" "${run_dir}/linkage.log" "${run_dir}/smoke.log" "${run_dir}/static-mode.txt" "${run_dir}/scripts.sha256" "${run_dir}/ue-commit.txt" "${run_dir}/ue-tracked.patch" "${run_dir}/installed.sha256"; do
    [[ -f "${path}" ]] || { echo "missing retained evidence: ${path}" >&2; return 1; }
  done
}
sha256sum "${BASH_SOURCE[0]}" "${smoke_src}" "${script_dir}/../stage_report.py" > "${run_dir}/scripts.sha256"
run_step retention retention_check

source_revision="$(sha256sum "${run_dir}/source-copy.sha256")"
source_revision="${source_revision%% *}"
step=stage-report
run_step stage-report python3 "${script_dir}/../stage_report.py" write \
  --output "${run_dir}/stage-report.json" --stage-id "${stage_id}" --scope "${scope}" --exit-code 0 \
  --required-check source --required-check toolchain --required-check build --required-check architecture \
  --required-check linkage --required-check smoke --required-check retention \
  --check source PASS --check toolchain PASS --check build PASS --check architecture PASS \
  --check linkage PASS --check smoke PASS --check retention PASS \
  --evidence source "${run_dir}/source-copy.sha256" \
  --evidence toolchain "${run_dir}/toolchain-version.log" \
  --evidence build "${run_dir}/build.log" \
  --evidence architecture "${run_dir}/architecture.txt" \
  --evidence linkage "${run_dir}/linkage.log" \
  --evidence smoke "${run_dir}/smoke.log" \
  --evidence retention "${run_dir}/installed.sha256" \
  --evidence source-diff "${run_dir}/source-diff.txt" --evidence source-original "${run_dir}/source-original.sha256" \
  --evidence static-mode "${run_dir}/static-mode.txt" --evidence scripts "${run_dir}/scripts.sha256" \
  --evidence smoke-binary "${smoke_bin}" --evidence tbb-shared "${install_lib}/libtbb.so.2" \
  --evidence tbbmalloc-shared "${install_lib}/libtbbmalloc.so.2" \
  --source tbb "${source_copy}" "${source_revision}" --source ue "${ue_dir}" "${ue_commit}" \
  --command bash "${BASH_SOURCE[0]}"
run_step report-validation python3 "${script_dir}/../stage_report.py" validate "${run_dir}/stage-report.json" \
  --stage-id "${stage_id}" --scope "${scope}"
step=complete
