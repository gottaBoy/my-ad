FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        clang \
        cmake \
        file \
        git \
        git-lfs \
        libssl-dev \
        libvulkan1 \
        lld \
        ninja-build \
        pkg-config \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        vulkan-tools \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/carla/g0-probe.sh /usr/local/bin/carla-g0-probe
RUN chmod 0755 /usr/local/bin/carla-g0-probe

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/carla-g0-probe"]
