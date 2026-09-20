#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

jobs="${CARLA_BUILD_JOBS:-4}"
timeout_seconds="${CARLA_PYTHON_TIMEOUT_SECONDS:-1800}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be 1..4" >&2; exit 64; }
[[ "${timeout_seconds}" =~ ^[1-9][0-9]{0,3}$ && "${timeout_seconds}" -le 3600 ]] || {
  echo "CARLA_PYTHON_TIMEOUT_SECONDS must be 1..3600" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run in native ARM64 carla-dev Docker" >&2; exit 2;
}
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
unset PYTHONHOME PYTHONPATH _PYTHON_HOST_PLATFORM LD_LIBRARY_PATH LD_PRELOAD
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH C_INCLUDE_PATH CPLUS_INCLUDE_PATH LIBRARY_PATH
unset CC CXX MAKEFLAGS MFLAGS CONFIG_SITE PKG_CONFIG_PATH
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
export CARLA_BUILD_JOBS="${jobs}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
helper="${script_dir}/python_stage.py"
triple=aarch64-unknown-linux-gnueabi
module="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Python3"
artifact_root="${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd"

check_output() {
  python3 -B - "$1" "${CARLA_UE_DIR}" "${CARLA_SOURCE_DIR:-/workspace/carla}" <<'PY'
from pathlib import Path
import sys
output, *sources = [Path(p).resolve() for p in sys.argv[1:]]
if any(output.is_relative_to(root) for root in sources):
    raise ValueError("refusing to write Python artifacts into a source tree")
PY
}

run_step() {
  local step="$1" code
  shift
  printf '%q ' "$@" > "${run_dir}/${step}.command.txt"
  printf '\n' >> "${run_dir}/${step}.command.txt"
  printf 'step=%s\n' "${step}"
  set +e
  ( set -e; "$@" ) > "${run_dir}/${step}.log" 2>&1
  code=$?
  set -e
  printf '%s\n' "${code}" > "${run_dir}/${step}.exit-code.txt"
  tail -n 12 "${run_dir}/${step}.log"
  [[ "${code}" == 0 ]] || return "${code}"
  touch "${run_dir}/passed/${step}"
}

preflight() {
  python3 -B "${helper}" version --ue "${CARLA_UE_DIR}"
  printf 'docker=yes\nmachine=%s\ntarget=%s\n' "$(uname -m)" "${triple}"
  local tool
  for tool in clang clang++ llvm-ar llvm-ranlib llvm-readelf ld.lld; do
    [[ -x "${CARLA_LLVM_BIN}/${tool}" ]] || { echo "missing native tool: ${tool}" >&2; return 2; }
  done
  command -v curl make
  shopt -s nullglob
  sysroots=("${CARLA_UE_DIR}"/Engine/Extras/ThirdPartyNotUE/SDKs/HostLinux/Linux_x64/v*_clang-*/"${triple}")
  [[ "${#sysroots[@]}" == 1 ]] || { echo "expected one UE ARM64 sysroot" >&2; return 2; }
  printf '%s\n' "${sysroots[0]}" > "${run_dir}/sysroot.txt"
  "${CARLA_LLVM_BIN}/clang" --version
  "${CARLA_LLVM_BIN}/clang" --target="${triple}" --sysroot="${sysroots[0]}" -dumpmachine
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
  cp "${BASH_SOURCE[0]}" "${helper}" "${script_dir}/python-smoke.c" "${run_dir}/inputs/"
  cp "${script_dir}/../stage_report.py" "${run_dir}/inputs/"
  cp "${module}/Linux/include/patchlevel.h" "${module}/Python3.Build.cs" \
    "${module}/python_v3.11.x.tps" "${run_dir}/inputs/"
  sha256sum "${BASH_SOURCE[0]}" "${helper}" "${script_dir}/python-smoke.c" \
    "${script_dir}/../stage_report.py" "${module}/Linux/include/patchlevel.h" \
    "${module}/Python3.Build.cs" "${module}/python_v3.11.x.tps" > "${run_dir}/inputs.sha256"
  sha256sum "${CARLA_LLVM_BIN}/clang" "${CARLA_LLVM_BIN}/clang++" \
    "${CARLA_LLVM_BIN}/llvm-ar" "${CARLA_LLVM_BIN}/llvm-ranlib" \
    "${CARLA_LLVM_BIN}/llvm-readelf" "${CARLA_LLVM_BIN}/ld.lld" > "${run_dir}/toolchain.sha256"
}

configure_python() {
  mkdir "${run_dir}/work"
  cd "${run_dir}/work"
  "${run_dir}/build-source/configure" --prefix="${prefix}" --enable-shared \
    --with-static-libpython --without-ensurepip --with-pkg-config=no \
    --disable-test-modules --without-lto
}

install_python() {
  make -C "${run_dir}/work" -j"${jobs}" altinstall
  # CPython installs its static archive in lib/python3.11/config-*; expose the
  # same actually compiled PIC archive at the explicit stage dependency path.
  install -m 0644 "${run_dir}/work/libpython3.11.a" "${prefix}/lib/libpython3.11.a"
  cmp "${run_dir}/work/libpython3.11.a" "${prefix}/lib/libpython3.11.a"
}

retention() {
  sha256sum --check "${run_dir}/inputs.sha256"
  sha256sum --check "${run_dir}/toolchain.sha256"
  python3 -B "${helper}" retention --run "${run_dir}"
  (cd "${prefix}" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "${run_dir}/installed.sha256"
}

worker() {
  run_dir="$1"
  check_output "${run_dir}"
  [[ -d "${run_dir}/inputs" && -d "${run_dir}/passed" \
    && ! -e "${run_dir}/source" && ! -e "${run_dir}/work" && ! -e "${run_dir}/install" ]] || {
    echo "fresh supervisor directory required" >&2; return 2;
  }
  prefix="${run_dir}/install"
  run_step preflight preflight
  run_step source python3 -B "${helper}" prepare --run "${run_dir}" --ue "${CARLA_UE_DIR}" \
    --jobs "${jobs}" --local-source "${CARLA_PYTHON_SOURCE_DIR:-}"
  export CARLA_PYTHON_SYSROOT
  CARLA_PYTHON_SYSROOT="$(< "${run_dir}/sysroot.txt")"
  export CC="${CARLA_LLVM_BIN}/clang --target=${triple} --sysroot=${CARLA_PYTHON_SYSROOT}"
  export CXX="${CARLA_LLVM_BIN}/clang++ --target=${triple} --sysroot=${CARLA_PYTHON_SYSROOT}"
  export AR="${CARLA_LLVM_BIN}/llvm-ar" RANLIB="${CARLA_LLVM_BIN}/llvm-ranlib"
  export READELF="${CARLA_LLVM_BIN}/llvm-readelf"
  export CFLAGS="-O2 -fPIC" CPPFLAGS="" CONFIG_SITE=/dev/null
  export LDFLAGS="-fuse-ld=lld -Wl,-rpath,${prefix}/lib"
  printf 'CC=%s\nCXX=%s\nCFLAGS=%s\nLDFLAGS=%s\nCONFIG_SITE=%s\n' \
    "${CC}" "${CXX}" "${CFLAGS}" "${LDFLAGS}" "${CONFIG_SITE}" > "${run_dir}/build-environment.txt"
  run_step configure configure_python
  run_step build make -C "${run_dir}/work" -j"${jobs}" all
  run_step install install_python
  mkdir -p "${prefix}/lib/python-smoke"
  compiler=("${CARLA_LLVM_BIN}/clang" "--target=${triple}" "--sysroot=${CARLA_PYTHON_SYSROOT}"
    -O2 -fPIC -fuse-ld=lld "-I${prefix}/include/python3.11")
  libraries=(-ldl -lm -lpthread -lutil)
  run_step extension-compile "${compiler[@]}" -shared -DPYTHON_SMOKE_EXTENSION \
    "${run_dir}/inputs/python-smoke.c" -o "${prefix}/lib/python-smoke/_ue_native.so"
  run_step embed-shared-compile "${compiler[@]}" "${run_dir}/inputs/python-smoke.c" \
    "-L${prefix}/lib" -lpython3.11 "-Wl,-rpath,${prefix}/lib" "${libraries[@]}" \
    -o "${prefix}/bin/python-embed-shared"
  run_step embed-static-compile "${compiler[@]}" "${run_dir}/inputs/python-smoke.c" \
    -Wl,--export-dynamic "${prefix}/lib/libpython3.11.a" "${libraries[@]}" \
    -o "${prefix}/bin/python-embed-static"
  run_step pic-link "${compiler[@]}" -shared -Wl,-z,defs -Wl,-z,text \
    -Wl,--whole-archive "${prefix}/lib/libpython3.11.a" -Wl,--no-whole-archive \
    "${libraries[@]}" -o "${prefix}/lib/libpython-pic-proof.so"
  run_step elf python3 -B "${helper}" elf --run "${run_dir}"
  run_step runtime "${prefix}/bin/python3.11" -I -B "${helper}" runtime --run "${run_dir}"
  run_step embed-shared "${prefix}/bin/python-embed-shared" "${prefix}"
  run_step embed-static "${prefix}/bin/python-embed-static" "${prefix}"
  run_step retention retention
}

if [[ "${1:-}" == --worker && "$#" == 2 ]]; then
  worker "$2"
  exit 0
fi
[[ "$#" == 0 ]] || { echo "configure through CARLA_* environment only" >&2; exit 64; }
check_output "${artifact_root}"
mkdir -p "${artifact_root}"
run_dir="$(mktemp -d "${artifact_root}/python-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
mkdir "${run_dir}/inputs" "${run_dir}/passed"
printf 'python artifacts=%s\n' "${run_dir}"
command=(timeout --kill-after=20s "${timeout_seconds}s" bash "${BASH_SOURCE[0]}" --worker "${run_dir}")
printf '%q ' "${command[@]}" > "${run_dir}/worker.command.txt"
printf '\n' >> "${run_dir}/worker.command.txt"
code=0
"${command[@]}" > "${run_dir}/worker.log" 2>&1 || code=$?
printf '%s\n' "${code}" > "${run_dir}/exit-code.txt"
tail -n 35 "${run_dir}/worker.log"
python3 -B "${helper}" finalize --run "${run_dir}" --exit-code "${code}" \
  --reporter "${script_dir}/../stage_report.py" --ue "${CARLA_UE_DIR}" --recipe "${BASH_SOURCE[0]}"
