#!/usr/bin/env bash
set -Eeuo pipefail

variable_name="${1:-}"
[[ -n "${variable_name}" ]] || {
  echo "healthcheck variable name is required" >&2
  exit 64
}

command_value="${!variable_name:-}"
[[ -n "${command_value}" && "${command_value}" != REPLACE_* ]] || {
  echo "${variable_name} is not configured" >&2
  exit 78
}

exec bash -lc "${command_value}"
