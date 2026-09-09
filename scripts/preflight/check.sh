#!/usr/bin/env bash
set -Eeuo pipefail

role="${HOST_ROLE:-}"
failures=0

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; failures=$((failures + 1)); }
warn() { printf 'WARN %s\n' "$1" >&2; }

[[ -n "${role}" ]] || fail "HOST_ROLE must be dgx or sim-x86"
[[ "${role}" == "dgx" || "${role}" == "sim-x86" ]] || fail "HOST_ROLE=$role is invalid"

arch="$(uname -m)"
if [[ "${role}" == "dgx" ]]; then
  [[ "${arch}" == "aarch64" || "${arch}" == "arm64" ]] && pass "DGX host architecture ${arch}" || fail "DGX requires aarch64, got ${arch}"
else
  [[ "${arch}" == "x86_64" || "${arch}" == "amd64" ]] && pass "simulation host architecture ${arch}" || fail "simulation host requires x86_64, got ${arch}"
fi

command -v docker >/dev/null 2>&1 && pass "docker installed" || fail "docker is not installed"
docker compose version >/dev/null 2>&1 && pass "docker compose v2 available" || fail "docker compose v2 is not available"
docker info >/dev/null 2>&1 && pass "docker daemon reachable" || fail "docker daemon is not reachable"

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi -L >/dev/null 2>&1 && pass "NVIDIA GPU visible" || fail "NVIDIA GPU is not usable"
else
  warn "nvidia-smi is not installed; container GPU validation is still required"
fi

for path in data/maps data/bags data/ground_truth data/datasets data/models data/engines data/logs data/reports data/cache artifacts; do
  mkdir -p "${path}"
  [[ -w "${path}" ]] && pass "writable ${path}" || fail "not writable ${path}"
done

if [[ -f .env ]]; then
  if grep -n 'REPLACE_' .env >/dev/null 2>&1; then
    fail ".env still contains REPLACE_ values"
  else
    pass ".env has no REPLACE_ placeholders"
  fi
else
  fail ".env is missing; copy .env.example and configure it"
fi

if [[ "${failures}" -gt 0 ]]; then
  printf 'BLOCKED preflight failures=%s\n' "${failures}" >&2
  exit 1
fi

printf 'PASS preflight role=%s\n' "${role}"
