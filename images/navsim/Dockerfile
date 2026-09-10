ARG NAVSIM_BASE_IMAGE
FROM ${NAVSIM_BASE_IMAGE}

ARG NAVSIM_REPO_URL=https://github.com/autonomousvision/navsim.git
ARG NAVSIM_GIT_REF

RUN test -n "${NAVSIM_GIT_REF}" \
    && test "${NAVSIM_GIT_REF}" != "REPLACE_NAVSIM_GIT_REF"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        libgl1 \
        libglib2.0-0 \
        libgeos-dev \
        libproj-dev \
        libsqlite3-0 \
        libspatialindex-dev \
        libspatialite7 \
        proj-bin \
        python3-dev \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -c 'import sys; assert sys.version_info[:2] == (3, 9), f"NAVSIM requires Python 3.9, found {sys.version_info[0]}.{sys.version_info[1]}"'

WORKDIR /workspace/navsim
RUN git init \
    && git remote add origin "${NAVSIM_REPO_URL}" \
    && git fetch --depth 1 origin "${NAVSIM_GIT_REF}" \
    && git checkout --detach FETCH_HEAD

RUN python3 -m pip install --no-cache-dir --upgrade "pip<25" \
    && python3 -m pip install --no-cache-dir -e .

WORKDIR /workspace/navsim
