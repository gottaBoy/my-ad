#!/usr/bin/env bash
set -Eeuo pipefail
jobs="${CARLA_BUILD_JOBS:-4}"
seconds="${CARLA_OPENUSD_TIMEOUT_SECONDS:-3600}"
[[ "${jobs}" =~ ^[1-4]$ ]] || { echo "CARLA_BUILD_JOBS must be 1..4" >&2; exit 64; }
[[ "${seconds}" =~ ^[1-9][0-9]{0,4}$ && "${seconds}" -le 14400 ]] || {
  echo "CARLA_OPENUSD_TIMEOUT_SECONDS must be 1..14400" >&2; exit 64;
}
[[ -f /.dockerenv && "$(uname -m)" == aarch64 ]] || {
  echo "Run in native ARM64 Docker" >&2; exit 2;
}
[[ -n "${CARLA_USD_DEPENDENCIES:-}" ]] || {
  echo "CARLA_USD_DEPENDENCIES is required (JSON report map)" >&2; exit 64;
}
export PYTHONDONTWRITEBYTECODE=1 GIT_OPTIONAL_LOCKS=0
export CARLA_UE_DIR="${CARLA_UE_DIR:-/workspace/unreal-engine}"
export CARLA_LLVM_BIN="${CARLA_LLVM_BIN:-/usr/lib/llvm-18/bin}"
unset CC CXX CFLAGS CXXFLAGS CPPFLAGS LDFLAGS CPATH CPLUS_INCLUDE_PATH C_INCLUDE_PATH
unset LIBRARY_PATH LD_LIBRARY_PATH LD_PRELOAD PYTHONPATH PYTHONHOME MAKEFLAGS CMAKE_PREFIX_PATH
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "${script_dir}/openusd_stage.py" \
  --source "${CARLA_OPENUSD_SOURCE:-/artifacts/carla/sources/OpenUSD-v24.05}" \
  --ue-root "${CARLA_UE_DIR}" --dependencies "${CARLA_USD_DEPENDENCIES}" \
  --artifact-root "${CARLA_ARTIFACT_DIR:-/artifacts/carla}/usd" \
  --jobs "${jobs}" --timeout "${seconds}"
