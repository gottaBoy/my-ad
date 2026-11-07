#!/usr/bin/env bash
set -Eeuo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${WAYMAX_PYTHON:-${repo}/../.venv-waymax/bin/python}"
cores="${WAYMAX_CPU_COUNT:-2}"
seconds="${WAYMAX_TIMEOUT:-900}"
[[ -x "${python}" ]] || { echo "Missing existing Python environment: ${python}" >&2; exit 2; }
[[ "${cores}" =~ ^[1-4]$ ]] || { echo "WAYMAX_CPU_COUNT must be 1..4" >&2; exit 64; }
[[ "${seconds}" =~ ^[1-9][0-9]*$ && "${seconds}" -le 1800 ]] || {
  echo "WAYMAX_TIMEOUT must be 1..1800 seconds" >&2; exit 64;
}
command -v taskset >/dev/null

export CUDA_VISIBLE_DEVICES=-1
export NVIDIA_VISIBLE_DEVICES=void
export JAX_PLATFORMS=cpu
export JAX_PLATFORM_NAME=cpu
export XLA_PYTHON_CLIENT_PREALLOCATE=false
export TF_CPP_MIN_LOG_LEVEL=2
export TF_NUM_INTEROP_THREADS=1
export TF_NUM_INTRAOP_THREADS="${cores}"
export OMP_NUM_THREADS="${cores}"
export OPENBLAS_NUM_THREADS="${cores}"
export MKL_NUM_THREADS="${cores}"
export MPLBACKEND=Agg
export PYTHONDONTWRITEBYTECODE=1
unset LD_PRELOAD

cpus="$(python3 -c 'import os,sys; print(",".join(map(str,sorted(os.sched_getaffinity(0))[:int(sys.argv[1])])))' "${cores}")"
exec timeout --signal=TERM --kill-after=10 "${seconds}" \
  taskset -c "${cpus}" "${python}" "${repo}/scripts/waymax/learning_lab.py" "$@"
