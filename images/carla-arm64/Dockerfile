FROM mcr.microsoft.com/dotnet/sdk:8.0-jammy AS dotnet-sdk

FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 LANGUAGE=C.UTF-8

COPY --from=dotnet-sdk /usr/share/dotnet /usr/share/dotnet
RUN ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet
ENV DOTNET_ROOT=/usr/share/dotnet
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        bison \
        ca-certificates \
        clang \
        curl \
        cmake \
        file \
        flex \
        git \
        git-lfs \
        gnupg \
        libasound2-dev \
        libatk-bridge2.0-dev \
        libgbm-dev \
        libglib2.0-dev \
        libgtk-3-dev \
        libnss3-dev \
        libpango1.0-dev \
        libsdl2-2.0-0 \
        libsdl2-dev \
        libssl-dev \
        libvulkan1 \
        libvulkan-dev \
        mesa-vulkan-drivers \
        lld \
        locales \
        make \
        m4 \
        ninja-build \
        pkg-config \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        rsync \
        sudo \
        unzip \
        wget \
        vulkan-tools \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key \
      | gpg --dearmor -o /etc/apt/keyrings/llvm.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/llvm.gpg] https://apt.llvm.org/jammy/ llvm-toolchain-jammy-18 main" \
      > /etc/apt/sources.list.d/llvm.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      clang-18 \
      lld-18 \
      llvm-18 \
      llvm-18-dev \
      libc++-18-dev \
      libc++abi-18-dev \
      libclang-rt-18-dev; \
    rm -rf /var/lib/apt/lists/*

ARG CMAKE_VERSION="3.28.3"
RUN set -eux; \
    cmake_arch="$(dpkg --print-architecture)"; \
    case "${cmake_arch}" in \
        amd64) cmake_arch=x86_64 ;; \
        arm64) cmake_arch=aarch64 ;; \
        *) echo "unsupported Debian architecture: ${cmake_arch}" >&2; exit 1 ;; \
    esac; \
    cmake_dir="cmake-${CMAKE_VERSION}-linux-${cmake_arch}"; \
    curl -fsSL "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/${cmake_dir}.tar.gz" \
        -o "/tmp/${cmake_dir}.tar.gz"; \
    tar -xzf "/tmp/${cmake_dir}.tar.gz" -C /opt; \
    ln -s "/opt/${cmake_dir}" /opt/cmake; \
    ln -s "/opt/${cmake_dir}/bin/cmake" /usr/local/bin/cmake3; \
    ln -s "/opt/${cmake_dir}/bin/ctest" /usr/local/bin/ctest3; \
    rm -f "/tmp/${cmake_dir}.tar.gz"
ENV PATH=/opt/cmake/bin:$PATH

RUN cmake --version && dotnet --info
RUN python3 -m venv /opt/carla-python \
    && /opt/carla-python/bin/python -m pip install --no-cache-dir numpy setuptools wheel build
ENV PATH=/opt/carla-python/bin:$PATH
RUN mkdir -p /opt/carla-python/include/aarch64-linux-gnu/python3.10 \
    && ln -s /usr/include/aarch64-linux-gnu/python3.10/pyconfig.h /opt/carla-python/include/aarch64-linux-gnu/python3.10/pyconfig.h

COPY scripts/carla/g0-probe.sh /usr/local/bin/carla-g0-probe
RUN chmod 0755 /usr/local/bin/carla-g0-probe

WORKDIR /workspace
CMD ["bash"]
