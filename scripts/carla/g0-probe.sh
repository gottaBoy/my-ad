#!/usr/bin/env bash
set -Eeuo pipefail

source_dir="${CARLA_SOURCE_DIR:-/workspace/carla}"
ue_dir="${CARLA_UE_DIR:-/workspace/unreal-engine}"
artifact_dir="${CARLA_ARTIFACT_DIR:-/artifacts/carla}"
mkdir -p "${artifact_dir}"

run_id="$(date -u +%Y%m%dT%H%M%SZ)-arm64-g0"
report="${artifact_dir}/g0-${run_id}.txt"
exec > >(tee "${report}") 2>&1

failures=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
warn() { printf 'WARN %s\n' "$1" >&2; }

printf 'CARLA G0 ARM64 toolchain probe\n'
printf 'report=%s\n' "${report}"
printf 'source_dir=%s\n' "${source_dir}"
printf 'ue_dir=%s\n' "${ue_dir}"
printf 'source_ref=%s\n' "${CARLA_SOURCE_REF:-unset}"
printf 'source_commit_expected=%s\n' "${CARLA_SOURCE_COMMIT:-unset}"
printf 'ue_ref=%s\n' "${CARLA_UE_REF:-unset}"
printf 'ue_commit_expected=%s\n' "${CARLA_UE_COMMIT:-unset}"

arch="$(uname -m)"
[[ "${arch}" == "aarch64" || "${arch}" == "arm64" ]] \
  && pass "container architecture ${arch}" \
  || fail "expected ARM64 container, got ${arch}"

for command_name in cmake ninja clang++ git file readelf python3; do
  command -v "${command_name}" >/dev/null 2>&1 \
    && pass "tool ${command_name}" \
    || fail "missing tool ${command_name}"
done

if command -v cmake >/dev/null; then
  cmake_version="$(cmake --version | head -n 1 | awk '{print $3}')"
  dpkg --compare-versions "${cmake_version}" ge 3.27.2 \
    && pass "CMake ${cmake_version} meets CARLA's minimum" \
    || fail "CMake ${cmake_version} is below CARLA's minimum 3.27.2"
fi

if [[ ! -d "${source_dir}/.git" ]]; then
  fail "CARLA source checkout is missing at ${source_dir}"
else
  pass "CARLA source checkout exists"
  git config --global --add safe.directory "${source_dir}"
  actual_commit="$(git -C "${source_dir}" rev-parse HEAD)"
  printf 'source_commit_actual=%s\n' "${actual_commit}"
  if [[ -n "${CARLA_SOURCE_COMMIT:-}" ]]; then
    [[ "${actual_commit}" == "${CARLA_SOURCE_COMMIT}" ]] \
      && pass "CARLA source commit is pinned" \
      || fail "CARLA source commit does not match the configured pin"
  else
    warn "CARLA_SOURCE_COMMIT is not configured"
  fi
  branch="$(git -C "${source_dir}" symbolic-ref --short HEAD 2>/dev/null || printf 'detached')"
  printf 'source_branch=%s\n' "${branch}"
  [[ "${branch}" == "${CARLA_SOURCE_REF:-}" ]] \
    && pass "CARLA source branch is ${branch}" \
    || fail "CARLA source branch does not match the configured ref"
fi

if [[ ! -d "${ue_dir}/Engine" ]]; then
  fail "CARLA Unreal Engine checkout is missing at ${ue_dir}"
else
  pass "CARLA Unreal Engine checkout exists"
  git config --global --add safe.directory "${ue_dir}"
  ue_commit_actual="$(git -C "${ue_dir}" rev-parse HEAD)"
  printf 'ue_commit_actual=%s\n' "${ue_commit_actual}"
  if [[ -n "${CARLA_UE_COMMIT:-}" ]]; then
    [[ "${ue_commit_actual}" == "${CARLA_UE_COMMIT}" ]] \
      && pass "Unreal source commit is pinned" \
      || fail "Unreal source commit does not match the configured pin"
  fi
  ue_branch="$(git -C "${ue_dir}" symbolic-ref --short HEAD 2>/dev/null || printf 'detached')"
  printf 'ue_branch=%s\n' "${ue_branch}"
  [[ "${ue_branch}" == "${CARLA_UE_REF:-}" ]] \
    && pass "Unreal source branch is ${ue_branch}" \
    || fail "Unreal source branch is ${ue_branch}, expected ${CARLA_UE_REF:-unset}"
  sdk_dir="${ue_dir}/Engine/Extras/ThirdPartyNotUE/SDKs"
  if [[ ! -d "${sdk_dir}" ]]; then
    fail "Unreal Engine SDK directory is missing"
  else
    arm_sdk="$(find "${sdk_dir}" -type d -iname '*aarch64*' -print -quit 2>/dev/null || true)"
    [[ -n "${arm_sdk}" ]] \
      && pass "Unreal SDK contains an ARM64 candidate: ${arm_sdk}" \
      || fail "Unreal SDK has no aarch64 directory"

    arm_clang="$(find "${sdk_dir}" -path '*aarch64-unknown-linux-gnueabi/bin/clang' -print -quit 2>/dev/null || true)"
    if [[ -n "${arm_clang}" ]]; then
      file -L "${arm_clang}"
      if grep -q 'CARLA_ARM64_NATIVE_WRAPPER=1' "${arm_clang}"; then
        timeout 10 "${arm_clang}" --version \
          && pass "Unreal clang uses the native ARM64 LLVM wrapper" \
          || fail "native ARM64 Unreal clang wrapper does not execute"
      else
        compiler_header="$(readelf -h "${arm_clang}" 2>&1 || true)"
        printf '%s\n' "${compiler_header}"
        if grep -Eq 'Machine:.*AArch64' <<<"${compiler_header}"; then
          timeout 10 "${arm_clang}" --version \
            && pass "Unreal clang runs natively on ARM64" \
            || fail "Unreal clang is ARM64 but does not execute"
        else
          fail "Unreal target SDK exists but its clang is not an ARM64 host executable"
        fi
      fi
    else
      fail "ARM64 target clang is missing"
    fi
  fi
fi

toolchain="${source_dir}/CMake/Toolchain.cmake"
if [[ -f "${toolchain}" ]]; then
  openssl_block="$(sed -n '/UE_OPENSSL_LIBS/,/)/p' "${toolchain}" || true)"
  if grep -q 'x86_64-unknown-linux-gnu' <<<"${openssl_block}"; then
    fail "CMake toolchain still hardcodes x86_64 OpenSSL for the selected target"
  else
    pass "CMake toolchain has no x86_64 OpenSSL hardcode in the selected block"
  fi
else
  fail "CARLA CMake toolchain is missing"
fi

base_dockerfile="${source_dir}/Util/Docker/Base.Dockerfile"
if [[ ! -f "${base_dockerfile}" ]]; then
  fail "CARLA Docker base file is missing"
elif grep -q 'linux-x86_64' "${base_dockerfile}"; then
  fail "CARLA Docker base still downloads an x86_64-only tool"
else
  pass "CARLA Docker base has no obvious x86_64-only tool download"
fi

if [[ "${failures}" -gt 0 ]]; then
  printf 'BLOCKED G0 failures=%s\n' "${failures}" >&2
  exit 2
fi

printf 'PASS G0 static ARM64 toolchain probe\n'
