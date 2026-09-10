#!/usr/bin/env bash
set -Eeuo pipefail

source /opt/ros/"${ROS_DISTRO:-humble}"/setup.bash

topic_file="${1:-/config/harness/required-topics.txt}"
timeout_sec="${ROS_DISCOVERY_TIMEOUT_SEC:-60}"
[[ -f "${topic_file}" ]] || {
  echo "topic file not found: ${topic_file}" >&2
  exit 66
}

mapfile -t required_topics < <(
  sed -e 's/#.*$//' -e '/^[[:space:]]*$/d' "${topic_file}"
)
(( ${#required_topics[@]} > 0 )) || {
  echo "topic file contains no topics: ${topic_file}" >&2
  exit 66
}

deadline=$((SECONDS + timeout_sec))
while (( SECONDS < deadline )); do
  mapfile -t discovered_topics < <(ros2 topic list)
  missing=()
  for topic in "${required_topics[@]}"; do
    found=0
    for discovered in "${discovered_topics[@]}"; do
      if [[ "${topic}" == "${discovered}" ]]; then
        found=1
        break
      fi
    done
    [[ "${found}" == "1" ]] || missing+=("${topic}")
  done

  if (( ${#missing[@]} == 0 )); then
    printf 'PASS discovered %s required ROS topics\n' "${#required_topics[@]}"
    printf '%s\n' "${required_topics[@]}"
    exit 0
  fi
  sleep 2
done

printf 'FAIL missing ROS topics after %ss:\n' "${timeout_sec}" >&2
printf '%s\n' "${missing[@]}" >&2
exit 1
