#!/usr/bin/env bash
set -Eeuo pipefail

source /opt/ros/"${ROS_DISTRO:-humble}"/setup.bash

bag_root="${BAG_OUTPUT_DIR:-/data/bags}"
bag_name="${BAG_NAME:-run_$(date -u +%Y%m%dT%H%M%SZ)}"
topic_file="${TOPIC_FILE:-/config/record/topics.txt}"
storage="${BAG_STORAGE:-mcap}"

[[ "${bag_name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || {
  echo "BAG_NAME contains unsupported characters: ${bag_name}" >&2
  exit 64
}

[[ -f "${topic_file}" ]] || {
  echo "topic file not found: ${topic_file}" >&2
  exit 66
}

mapfile -t topics < <(sed -e 's/#.*$//' -e '/^[[:space:]]*$/d' "${topic_file}")
(( ${#topics[@]} > 0 )) || {
  echo "topic file contains no topics: ${topic_file}" >&2
  exit 66
}

mkdir -p "${bag_root}"
bag_path="${bag_root}/${bag_name}"
[[ ! -e "${bag_path}" ]] || {
  echo "bag output already exists: ${bag_path}" >&2
  exit 73
}

exec ros2 bag record \
  --storage "${storage}" \
  --output "${bag_path}" \
  "${topics[@]}"
