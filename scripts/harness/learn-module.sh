#!/usr/bin/env bash
set -Eeuo pipefail

module="${1:-}"
duration="${2:-5}"
topic_file="${3:-/config/harness/module-topics.txt}"

[[ -n "${module}" ]] || {
  echo "module is required" >&2
  exit 64
}
[[ "${duration}" =~ ^[0-9]+([.][0-9]+)?$ ]] || {
  echo "duration must be a positive number" >&2
  exit 64
}
awk -v duration="${duration}" 'BEGIN { exit !(duration > 0) }' || {
  echo "duration must be greater than zero" >&2
  exit 64
}
[[ -f "${topic_file}" ]] || {
  echo "module topic file not found: ${topic_file}" >&2
  exit 66
}

if [[ -f /opt/autoware/setup.bash ]]; then
  source /opt/autoware/setup.bash
else
  source /opt/ros/"${ROS_DISTRO:-humble}"/setup.bash
fi

if ! awk -F'|' -v wanted="${module}" '
  $0 !~ /^[[:space:]]*#/ && NF >= 3 && $1 == wanted { found = 1 }
  END { exit(found ? 0 : 1) }
' "${topic_file}"; then
  echo "unknown module: ${module}" >&2
  echo "valid modules: simulation sensing localization perception fusion planning control" >&2
  exit 64
fi

topic_list="$(ros2 topic list 2>/dev/null || true)"

print_lesson() {
  case "${module}" in
    sensing)
      cat <<'EOF'
effect_chain=sensor source -> raw Image/PointCloud2 -> preprocessing -> perception
learning_goal=verify real sensor messages, timestamps, frames, dimensions, point layout, and rate
EOF
      ;;
    localization)
      cat <<'EOF'
effect_chain=sensor/kinematic input -> localization -> map/odom/base_link TF and ego state
learning_goal=verify pose, velocity, timestamps, frames, and a connected TF tree
EOF
      ;;
    perception)
      cat <<'EOF'
effect_chain=preprocessed sensor input -> detection/tracking -> recognized objects
learning_goal=verify the producing node, object count, classes, positions, and update rate
EOF
      ;;
    fusion)
      cat <<'EOF'
effect_chain=multiple timestamped sensor results -> association/fusion -> fused objects
learning_goal=verify multiple inputs, calibration TF, synchronization, producer identity, and output changes
EOF
      ;;
    planning)
      cat <<'EOF'
effect_chain=map + ego state + route + objects -> behavior/motion planning -> trajectory
learning_goal=verify route readiness, trajectory points, velocity profile, and reaction to scenario changes
EOF
      ;;
    control)
      cat <<'EOF'
effect_chain=trajectory + vehicle feedback -> controller/gate -> steering, acceleration, and braking command
learning_goal=verify command values, update rate, timeout state, and vehicle response
EOF
      ;;
    simulation)
      cat <<'EOF'
effect_chain=simulator clock -> ROS time used by the runtime
learning_goal=verify that simulated time advances consistently
EOF
      ;;
  esac
}

print_next_step() {
  case "${module}" in
    sensing|perception|fusion)
      echo "next_step=provide AWSIM/CARLA sensor topics or replay a rosbag containing camera, LiDAR, TF, and calibration"
      ;;
    localization|planning|control)
      echo "next_step=keep make up-dgx running, or run make scenario-up for changing scene data"
      ;;
    simulation)
      echo "next_step=run Scenario Simulator or replay a bag with /clock"
      ;;
  esac
}

echo "=== Autoware module learning lab ==="
echo "module=${module}"
echo "observation_window_sec=${duration}"
print_lesson

present_count=0
received_count=0
while IFS='|' read -r _ topic meaning; do
  [[ -n "${topic}" ]] || continue
  echo
  echo "--- topic=${topic} ---"
  echo "meaning=${meaning}"
  if ! grep -Fqx -- "${topic}" <<<"${topic_list}"; then
    echo "status=MISSING"
    continue
  fi

  present_count=$((present_count + 1))
  echo "status=PRESENT"
  ros2 topic info --verbose "${topic}" || true
  if sample_output="$(
    python3 /opt/my-ad/scripts/harness/sample-topic.py \
      --topic "${topic}" \
      --duration "${duration}"
  )"; then
    printf '%s\n' "${sample_output}"
    if grep -q '^sample_status=RECEIVED' <<<"${sample_output}"; then
      received_count=$((received_count + 1))
    fi
  else
    sample_exit_code=$?
    echo "sample_status=PROBE_ERROR exit_code=${sample_exit_code}"
  fi
done < <(
  awk -F'|' -v wanted="${module}" '
    $0 !~ /^[[:space:]]*#/ && NF >= 3 && $1 == wanted { print $0 }
  ' "${topic_file}"
)

echo
echo "=== Learning result ==="
echo "present_topic_count=${present_count}"
echo "topics_with_messages=${received_count}"
if [[ "${present_count}" == "0" ]]; then
  echo "module_status=NO_RUNTIME_INPUT"
  echo "conclusion=no effect can be observed for this module in the current runtime"
  print_next_step
elif [[ "${received_count}" == "0" ]]; then
  echo "module_status=TOPICS_PRESENT_NO_MESSAGES"
  echo "conclusion=interfaces exist, but no runtime effect was captured in the observation window"
  print_next_step
else
  echo "module_status=MESSAGES_RECEIVED"
  echo "conclusion=compare publishers, sample summaries, rates, and downstream topics before and after changing one input"
fi

cat <<'EOF'
method=discover -> inspect publisher -> sample data -> measure rate -> change one input -> compare downstream output
warning=topic presence alone is not an algorithm PASS
EOF
