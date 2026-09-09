#!/usr/bin/env bash
set -Eeuo pipefail

source /opt/ros/"${ROS_DISTRO:-humble}"/setup.bash

bag="${REPLAY_BAG:-}"
[[ -n "${bag}" ]] || {
  echo "REPLAY_BAG must point to a bag directory" >&2
  exit 64
}
[[ -d "${bag}" ]] || {
  echo "bag directory not found: ${bag}" >&2
  exit 66
}

exec ros2 bag play "${bag}" --clock
