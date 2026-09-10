#!/usr/bin/env bash

load_env_file() {
  local env_file="$1"
  local line key value

  [[ -f "${env_file}" ]] || {
    echo "env file not found: ${env_file}" >&2
    return 66
  }

  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    [[ "${line}" == *=* ]] || {
      echo "invalid env line: ${line}" >&2
      return 65
    }

    key="${line%%=*}"
    value="${line#*=}"
    [[ "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
      echo "invalid env key: ${key}" >&2
      return 65
    }

    if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi

    if [[ ! -v "${key}" ]]; then
      export "${key}=${value}"
    fi
  done < "${env_file}"
}
