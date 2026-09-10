ARG ROS_BASE_IMAGE=ros:humble-ros-base
FROM ${ROS_BASE_IMAGE}

ARG ROS_DISTRO=humble

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        "ros-${ROS_DISTRO}-rmw-cyclonedds-cpp" \
        "ros-${ROS_DISTRO}-rosbag2" \
        "ros-${ROS_DISTRO}-rosbag2-storage-mcap" \
    && rm -rf /var/lib/apt/lists/*
