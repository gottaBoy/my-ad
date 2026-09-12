#!/usr/bin/env bash
set -Eeuo pipefail

source /opt/ros/"${ROS_DISTRO:-humble}"/setup.bash

topic_file="${1:-/config/harness/module-topics.txt}"
module_filter="${2:-}"
[[ -f "${topic_file}" ]] || {
  echo "module topic file not found: ${topic_file}" >&2
  exit 66
}

modules=(simulation sensing localization perception fusion planning control)
if [[ -n "${module_filter}" ]]; then
  valid_module=0
  for module in "${modules[@]}"; do
    if [[ "${module}" == "${module_filter}" ]]; then
      valid_module=1
      break
    fi
  done
  if [[ "${valid_module}" != "1" ]]; then
    printf 'unknown module: %s\nvalid modules: %s\n' \
      "${module_filter}" "${modules[*]}" >&2
    exit 64
  fi
fi

topic_list="$(ros2 topic list 2>/dev/null || true)"
node_list="$(ros2 node list 2>/dev/null || true)"

echo "=== Autoware module observation ==="
if [[ -n "${module_filter}" ]]; then
  echo "module_filter=${module_filter}"
fi
if [[ -z "${topic_list}" ]]; then
  echo "runtime_status=NO_TOPICS_DISCOVERED"
  echo "hint=keep Autoware or Scenario Simulator running, then retry"
  exit 0
fi
echo "runtime_status=TOPICS_DISCOVERED"

show_module() {
  local module="$1"
  local observed=0
  local topic=""
  local meaning=""
  local type=""

  echo "module=${module}"
  while IFS='|' read -r _ topic meaning; do
    [[ -n "${topic}" ]] || continue
    if grep -Fqx -- "${topic}" <<<"${topic_list}"; then
      observed=1
      type="$(ros2 topic type "${topic}" 2>/dev/null | head -n 1 || true)"
      printf '  status=PRESENT topic=%s type=%s meaning=%s\n' \
        "${topic}" "${type:-unknown}" "${meaning}"
    else
      printf '  status=MISSING topic=%s meaning=%s\n' "${topic}" "${meaning}"
    fi
  done < <(
    awk -F'|' -v wanted="${module}" '
      $0 !~ /^[[:space:]]*#/ && NF >= 3 && $1 == wanted { print $0 }
    ' "${topic_file}"
  )

  if [[ "${observed}" == "1" ]]; then
    echo "  module_status=OBSERVED"
  else
    echo "  module_status=NOT_OBSERVED"
  fi
}

for module in "${modules[@]}"; do
  [[ -z "${module_filter}" || "${module}" == "${module_filter}" ]] || continue
  show_module "${module}"
done

echo "=== Candidate processing nodes ==="
if ! grep -Ei 'sensing|localization|perception|fusion|detection|tracking|planning|trajectory|control' \
  <<<"${node_list}"; then
  echo "  no matching nodes discovered"
fi

cat <<'EOF'
=== Interpretation ===
PRESENT only proves that a topic is discoverable in this runtime.
For perception and fusion, also inspect publishers, message content, timestamps,
TF, and sensor calibration before treating the result as a valid algorithm demo.
EOF
