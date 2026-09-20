#!/usr/bin/env bash
set -Eeuo pipefail
umask 022
jobs="${CARLA_BUILD_JOBS:-4}"
seconds="${CARLA_BOOST_TIMEOUT_SECONDS:-1800}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be 1..4" >&2; exit 64; }
[[ "${seconds}" =~ ^[1-9][0-9]{0,3}$ && "${seconds}" -le 3600 ]] || {
  echo "CARLA_BOOST_TIMEOUT_SECONDS must be 1..3600" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || { echo "native ARM64 carla-dev Docker required" >&2; exit 2; }
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}" CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
unset CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH CPLUS_INCLUDE_PATH C_INCLUDE_PATH LIBRARY_PATH
unset CC CXX MAKEFLAGS MFLAGS PYTHONHOME PYTHONPATH LD_LIBRARY_PATH LD_PRELOAD
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
helper="${script_dir}/boost_stage.py"
module="${CARLA_UE_DIR}/Engine/Source/ThirdParty/Boost"

run_step() {
  local step="$1" code
  shift
  printf '%q ' "$@" > "${run_dir}/${step}.command.txt"
  printf '\n' >> "${run_dir}/${step}.command.txt"
  printf 'step=%s\n' "${step}"
  set +e
  (set -e; "$@") > "${run_dir}/${step}.log" 2>&1
  code=$?
  set -e
  printf '%s\n' "${code}" > "${run_dir}/${step}.exit-code.txt"
  tail -n 8 "${run_dir}/${step}.log"
  [[ "${code}" == 0 ]] || return "${code}"
  touch "${run_dir}/passed/${step}"
}

check_output() {
  python3 -B - "$1" "${CARLA_UE_DIR}" "${CARLA_SOURCE_DIR:-/workspace/carla}" <<'PY'
from pathlib import Path
import sys
output, *sources = [Path(p).resolve() for p in sys.argv[1:]]
if any(output.is_relative_to(p) for p in sources):
    raise ValueError("refusing to write Boost artifacts inside source tree")
PY
}

preflight() {
  printf 'container=docker\nmachine=%s\njobs=%s\n' "$(uname -m)" "${jobs}"
  python3 -B "${helper}" toolchain --run "${run_dir}"
  git -c "safe.directory=${CARLA_UE_DIR}" -C "${CARLA_UE_DIR}" rev-parse HEAD > "${run_dir}/ue-commit.txt"
  cp "${BASH_SOURCE[0]}" "${helper}" "${script_dir}/boost-smoke.cpp" \
    "${script_dir}/boost-python-smoke.cpp" "${script_dir}/../stage_report.py" "${run_dir}/inputs/"
  cp "${module}/Boost.Build.cs" "${module}/BuildForUE/Linux/BuildForLinux.sh" "${run_dir}/inputs/"
  python3 -B - "${run_dir}" "${BASH_SOURCE[0]}" "${helper}" "${script_dir}/boost-smoke.cpp" \
    "${script_dir}/boost-python-smoke.cpp" "${script_dir}/../stage_report.py" "${module}/Boost.Build.cs" <<'PY'
import hashlib, json
from pathlib import Path
import sys
run = Path(sys.argv[1])
records = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sys.argv[2:]}
(run / "inputs.sha256.json").write_text(json.dumps(records, indent=2) + "\n")
PY
}

bootstrap() {
  cd "${run_dir}/build-source"
  PATH="${run_dir}/compiler:${PATH}" ./bootstrap.sh --with-toolset=clang \
    --with-python="${python_prefix}/bin/python3.11" --with-python-version=3.11 --prefix="${prefix}"
}

build_boost() {
  cd "${run_dir}/build-source"
  ./b2 -j"${jobs}" -d2 --ignore-site-config --user-config="${run_dir}/user-config.jam" \
    --build-dir="${run_dir}/work" --prefix="${prefix}" --includedir="${prefix}/include" --libdir="${prefix}/lib" \
    --layout=tagged --debug-configuration toolset=clang-18 target-os=linux architecture=arm address-model=64 \
    threading=multi variant=release link=static,shared runtime-link=shared cxxstd=14 \
    --with-atomic --with-chrono --with-filesystem --with-iostreams --with-program_options \
    --with-python --with-regex --with-system --with-thread python=3.11 \
    -sNO_ZLIB=1 -sNO_BZIP2=1 -sNO_LZMA=1 -sNO_ZSTD=1 --disable-icu \
    "linkflags=-Wl,-rpath,${prefix}/lib" install
}

worker() {
  run_dir="$1"
  check_output "${run_dir}"
  [[ -d "${run_dir}/passed" && ! -e "${run_dir}/source" && ! -e "${run_dir}/install" ]] || {
    echo "fresh supervisor directory required" >&2; return 2;
  }
  prefix="${run_dir}/install"
  run_step python-before python3 -B "${helper}" python-before --run "${run_dir}"
  python_prefix="$(< "${run_dir}/python-prefix.txt")"
  run_step preflight preflight
  run_step source python3 -B "${helper}" prepare --run "${run_dir}"
  run_step bootstrap bootstrap
  run_step configure python3 -B "${helper}" configure --run "${run_dir}"
  run_step build build_boost
  run_step elf python3 -B "${helper}" elf --run "${run_dir}"
  mkdir -p "${prefix}/bin" "${prefix}/lib/boost-python-static" "${prefix}/lib/boost-python-shared"
  libraries=(atomic chrono filesystem iostreams program_options python311 regex system thread)
  archives=()
  shared=()
  for name in "${libraries[@]}"; do
    archives+=("${prefix}/lib/libboost_${name}-mt-a64.a")
    shared+=("${prefix}/lib/libboost_${name}-mt-a64.so.1.82.0")
  done
  compiler=("${run_dir}/compiler/clang++" -std=c++14 "-I${prefix}/include"
    "-I${python_prefix}/include/python3.11")
  python_link=("-L${python_prefix}/lib" -lpython3.11 "-Wl,-rpath,${python_prefix}/lib")
  run_step pic-link "${compiler[@]}" -shared -Wl,-z,defs -Wl,-z,text \
    -Wl,--whole-archive "${archives[@]}" -Wl,--no-whole-archive "${python_link[@]}" \
    -o "${prefix}/lib/libboost-pic-proof.so"
  run_step smoke-static-build "${compiler[@]}" "${run_dir}/inputs/boost-smoke.cpp" \
    -Wl,--start-group "${archives[@]}" -Wl,--end-group -o "${prefix}/bin/boost-smoke-static"
  run_step smoke-shared-build "${compiler[@]}" -DBOOST_ALL_DYN_LINK "${run_dir}/inputs/boost-smoke.cpp" \
    -Wl,--no-as-needed "${shared[@]}" "${python_link[@]}" "-Wl,-rpath,${prefix}/lib" \
    -o "${prefix}/bin/boost-smoke-shared"
  run_step extension-static-build "${compiler[@]}" -DBOOST_PYTHON_STATIC_LIB -shared -Wl,-z,defs -Wl,-z,text \
    "${run_dir}/inputs/boost-python-smoke.cpp" "${prefix}/lib/libboost_python311-mt-a64.a" \
    "${python_link[@]}" -o "${prefix}/lib/boost-python-static/_boost_stage.so"
  run_step extension-shared-build "${compiler[@]}" -shared -Wl,-z,defs -Wl,-z,text \
    "${run_dir}/inputs/boost-python-smoke.cpp" "${prefix}/lib/libboost_python311-mt-a64.so.1.82.0" \
    "${python_link[@]}" "-Wl,-rpath,${prefix}/lib" -o "${prefix}/lib/boost-python-shared/_boost_stage.so"
  run_step runtime python3 -B "${helper}" runtime --run "${run_dir}"
  run_step python-after python3 -B "${helper}" python-after --run "${run_dir}"
  run_step retention python3 -B "${helper}" retention --run "${run_dir}"
}

if [[ "${1:-}" == --worker && "$#" == 2 ]]; then
  worker "$2"
  exit 0
fi
[[ "$#" == 0 ]] || { echo "configure through CARLA_* environment only" >&2; exit 64; }
root="${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd"
check_output "${root}"
mkdir -p "${root}"
run_dir="$(mktemp -d "${root}/boost-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
chmod 0755 "${run_dir}"
mkdir "${run_dir}/inputs" "${run_dir}/passed"
printf 'boost artifacts=%s\n' "${run_dir}"
command=(timeout --kill-after=20s "${seconds}s" bash "${BASH_SOURCE[0]}" --worker "${run_dir}")
printf '%q ' "${command[@]}" > "${run_dir}/worker.command.txt"
printf '\n' >> "${run_dir}/worker.command.txt"
code=0
"${command[@]}" > "${run_dir}/worker.log" 2>&1 || code=$?
printf '%s\n' "${code}" > "${run_dir}/exit-code.txt"
tail -n 35 "${run_dir}/worker.log"
python3 -B "${helper}" finalize --run "${run_dir}" --exit-code "${code}"
