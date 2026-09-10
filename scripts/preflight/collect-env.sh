#!/usr/bin/env bash
set -Eeuo pipefail

# Collect DGX Spark host environment info for .env configuration.
# Usage: bash scripts/preflight/collect-env.sh
# Output: stdout + artifacts/env-report/env-<timestamp>.txt

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${repo_root}"

report_dir="${ARTIFACT_ROOT:-./artifacts}/env-report"
mkdir -p "${report_dir}"
ts="$(date -u '+%Y%m%dT%H%M%SZ')"
report_file="${report_dir}/env-${ts}.txt"

section() { printf '\n========== %s ==========\n' "$1"; }

{
  printf 'DGX Spark Environment Report\n'
  printf 'Generated: %s\n' "${ts}"

  # --- Host architecture and OS ---
  section 'Host Architecture'
  printf 'uname -m:        %s\n' "$(uname -m)"
  printf 'uname -s:        %s\n' "$(uname -s)"
  printf 'uname -a:        %s\n' "$(uname -a 2>/dev/null || echo N/A)"

  section 'OS Release'
  if [[ -f /etc/os-release ]]; then
    cat /etc/os-release
  else
    echo '/etc/os-release not found'
  fi

  # --- Memory ---
  section 'Memory'
  if [[ -r /proc/meminfo ]]; then
    head -3 /proc/meminfo
    mem_kb="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
    mem_gb=$((mem_kb / 1024 / 1024))
    printf 'Total memory:    %s GiB\n' "${mem_gb}"
  else
    echo '/proc/meminfo not readable'
  fi

  # --- Disk ---
  section 'Disk'
  df -h . 2>/dev/null || echo 'df failed'

  # --- Docker ---
  section 'Docker'
  if command -v docker >/dev/null 2>&1; then
    printf 'Docker version:  %s\n' "$(docker --version 2>/dev/null)"
    printf 'Docker arch:     %s\n' "$(docker info --format '{{.Architecture}}' 2>/dev/null || echo 'N/A')"
    printf 'Docker server:   %s\n' "$(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo 'N/A')"
    printf 'Docker root dir: %s\n' "$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo 'N/A')"
    if docker info >/dev/null 2>&1; then
      echo 'Docker daemon:   reachable'
    else
      echo 'Docker daemon:   NOT reachable'
    fi
    printf 'Compose version: %s\n' "$(docker compose version 2>/dev/null || echo 'N/A')"
  else
    echo 'docker is NOT installed'
  fi

  # --- NVIDIA GPU ---
  section 'NVIDIA GPU'
  if command -v nvidia-smi >/dev/null 2>&1; then
    printf 'GPU list:\n'
    nvidia-smi -L 2>/dev/null || echo 'nvidia-smi -L failed'
    printf '\nGPU summary:\n'
    nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv 2>/dev/null \
      || echo 'nvidia-smi query failed'
  else
    echo 'nvidia-smi is NOT installed'
  fi

  # --- NVIDIA Container Toolkit ---
  section 'NVIDIA Container Toolkit'
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if command -v nvidia-ctk >/dev/null 2>&1; then
      printf 'nvidia-ctk:      %s\n' "$(nvidia-ctk --version 2>/dev/null || echo 'installed, version unavailable')"
      printf 'CDI devices:\n'
      nvidia-ctk cdi list 2>/dev/null || echo 'nvidia-ctk could not list CDI devices'
    else
      echo 'nvidia-ctk:      NOT found'
    fi

    docker_runtimes="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || echo '{}')"
    printf 'Docker runtimes: %s\n' "${docker_runtimes}"
    if [[ "${docker_runtimes}" == *'"nvidia"'* ]]; then
      echo 'nvidia runtime:  available'
      gpu_runtime_args=(--runtime=nvidia --gpus all)
    else
      echo 'nvidia runtime:  NOT found in Docker runtimes'
      gpu_runtime_args=(--gpus all)
    fi

    gpu_probe_image="${GPU_CONTAINER_PROBE_IMAGE:-ubuntu:24.04}"
    gpu_probe_log="$(mktemp)"
    printf 'GPU probe image: %s\n' "${gpu_probe_image}"
    if docker run --rm "${gpu_runtime_args[@]}" "${gpu_probe_image}" nvidia-smi -L \
      >"${gpu_probe_log}" 2>&1; then
      echo 'GPU in container: accessible'
    else
      echo 'GPU in container: NOT accessible'
      echo 'GPU probe error:'
      tail -n 5 "${gpu_probe_log}" | sed 's/^/  /'
    fi
    rm -f "${gpu_probe_log}"
  else
    echo 'Docker daemon not reachable, skip container GPU check'
  fi

  # --- Network interfaces ---
  section 'Network Interfaces'
  if command -v ip >/dev/null 2>&1; then
    ip -o addr show 2>/dev/null | awk '{print $2, $3, $4}' || echo 'ip command failed'
  else
    echo 'ip command not available'
  fi

  # --- ROS ---
  section 'ROS'
  if command -v ros2 >/dev/null 2>&1; then
    printf 'ROS 2 version:   %s\n' "$(ros2 --version 2>/dev/null || echo N/A)"
  else
    echo 'ros2 not found on host (expected if using containers only)'
  fi

  # --- Existing .env ---
  section 'Current .env (REPLACE_ count)'
  if [[ -f .env ]]; then
    replace_count="$(grep -c 'REPLACE_' .env || true)"
    echo ".env has ${replace_count} REPLACE_ values remaining"
  else
    echo '.env does not exist yet'
  fi

  section 'Summary'
  arch="$(uname -m)"
  if [[ "${arch}" == "aarch64" || "${arch}" == "arm64" ]]; then
    echo 'Architecture: ARM64 (DGX Spark compatible)'
  else
    echo "Architecture: ${arch} (NOT ARM64, this is not a DGX Spark)"
  fi
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    echo 'GPU: present'
  else
    echo 'GPU: missing or not accessible'
  fi
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo 'Docker: installed and reachable'
  else
    echo 'Docker: NOT available'
  fi

} | tee "${report_file}"

echo ""
echo "Report saved to: ${report_file}"
echo "Copy the content above back to configure .env."
