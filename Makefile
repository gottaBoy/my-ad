SHELL := /usr/bin/env bash

ENV_FILE ?= .env
MODULE ?=
DURATION ?= 5
DGX_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.dgx.yaml
SIM_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.sim-x86.yaml
CARLA_COMPOSE_PROJECT_NAME ?= my-ad-carla
CARLA_COMPOSE := docker compose --project-name "$(CARLA_COMPOSE_PROJECT_NAME)" --env-file $(ENV_FILE) -f compose.carla-arm64.yaml
# The manifest records this image and its resolved ID, so a manifest can answer
# which toolchain produced it; a tag alone is mutable.
CARLA_TOOLCHAIN_IMAGE ?= my-ad/carla-toolchain:arm64

.PHONY: init preflight config build-tools build-tools-sim build-navsim up-dgx up-sim collect-sim down-dgx down-sim \
	record record-sim replay viz scenario scenario-up scenario-prepare isaac navsim navsim-cache data deploy test-compose test-local \
	harness-host harness-gpu harness-network harness-clock harness-runtime harness-ros harness-ros-scenario harness-navsim inspect-modules learn-module collect-env \
carla-config carla-g0 carla-shell carla-build-shell carla-ue-check carla-ue-setup carla-shader-deps carla-scw carla-ispc carla-ue-build carla-startup-probe carla-editor-check carla-editor-deps carla-editor-build carla-editor-startup carla-editor-cook carla-full-cook carla-stage-cooked-server carla-cooked-server carla-lavapipe-sensors carla-town10-nullrhi-rpc carla-ue-shutdown-crash carla-vulkan-compute carla-vulkan-compute-gpu carla-ue-vulkan-device-state carla-ue-vulkan-device-state-gpu carla-audio-deps carla-assimp carla-ue-meshbridge carla-vulkan carla-ufbx carla-ue-ufbxbridge carla-runtime-check carla-interchange carla-manifest carla-manifest-verify carla-usd-inventory carla-vulkan-validation-layer carla-gb10-vulkan-comparison carla-symbolize-client-crash carla-gb10-driver-report

init:
	mkdir -p data/maps/sample-map-planning data/bags data/ground_truth data/datasets data/models data/engines data/logs data/reports data/cache \
	        data/cache/vulkan-validation-layer \
	        data/navsim/dataset/maps data/navsim/exp data/models/navsim data/reports/navsim data/cache/navsim \
		third_party/navsim artifacts
	@test -f "$(ENV_FILE)" || cp .env.example "$(ENV_FILE)"
	@echo "DGX core defaults are configured in $(ENV_FILE)."
	@echo "Place lanelet2_map.osm and pointcloud_map.pcd in data/maps/sample-map-planning."
	@echo "Replace REPLACE_* values only before enabling their optional profiles."

preflight:
	ENV_FILE="$(ENV_FILE)" ./scripts/preflight/check.sh

collect-env:
	./scripts/preflight/collect-env.sh

config:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/compose-config.sh

build-tools:
	$(DGX_COMPOSE) --profile record build recorder replay

build-tools-sim:
	$(SIM_COMPOSE) --profile record-source build recorder-source

up-dgx:
	ENV_FILE="$(ENV_FILE)" ./scripts/ops/run-dgx-mode.sh planning

up-sim:
	$(SIM_COMPOSE) up -d awsim

collect-sim:
	$(SIM_COMPOSE) --profile collect up -d ground-truth scenario-metadata

record:
	$(DGX_COMPOSE) --profile record up recorder

record-sim:
	$(SIM_COMPOSE) --profile record-source up recorder-source

replay:
	$(DGX_COMPOSE) --profile replay run --rm replay

viz:
	$(DGX_COMPOSE) --profile viz up -d foxglove-bridge

carla-config:
	$(CARLA_COMPOSE) --profile '*' config --quiet

.PHONY: carla-manifest carla-manifest-verify carla-test
carla-manifest:
	$(CARLA_COMPOSE) --profile g0 run --rm -T \
		-v "$(CURDIR):/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
		-e CARLA_TOOLCHAIN_IMAGE="$(CARLA_TOOLCHAIN_IMAGE)" \
		-e CARLA_TOOLCHAIN_IMAGE_ID="$$(docker image inspect --format '{{.Id}}' $(CARLA_TOOLCHAIN_IMAGE))" \
		carla-dev python3 /opt/my-ad/scripts/carla/capture_build_manifest.py capture \
		--project-root /repo --carla-root /workspace/carla --ue-root /workspace/unreal-engine \
		--artifact-root /artifacts/carla/build-manifests --command make carla-manifest

carla-manifest-verify:
	@test -n "$(MANIFEST)" || { echo "MANIFEST is required (container path)"; exit 64; }
	$(CARLA_COMPOSE) --profile g0 run --rm -T \
		-v "$(CURDIR):/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
		-e CARLA_TOOLCHAIN_IMAGE="$(CARLA_TOOLCHAIN_IMAGE)" \
		-e CARLA_TOOLCHAIN_IMAGE_ID="$$(docker image inspect --format '{{.Id}}' $(CARLA_TOOLCHAIN_IMAGE))" \
		carla-dev python3 /opt/my-ad/scripts/carla/capture_build_manifest.py verify \
		--manifest "$(MANIFEST)" --project-root /repo

carla-usd-inventory:
	$(CARLA_COMPOSE) --profile g0 run --rm -T carla-dev \
		python3 /opt/my-ad/scripts/carla/inspect_usd_dependencies.py \
		--ue-root /workspace/unreal-engine --artifact-dir /artifacts/carla/usd

.PHONY: carla-imath
carla-imath:
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) carla-dev \
		bash /opt/my-ad/scripts/carla/usd/build-arm64-imath.sh

.PHONY: carla-alembic carla-opensubdiv
carla-alembic:
	@test -n "$(IMATH_REPORT)" || { echo "IMATH_REPORT is required (verified container report path)"; exit 64; }
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-e CARLA_IMATH_REPORT="$(IMATH_REPORT)" carla-dev \
		bash /opt/my-ad/scripts/carla/usd/build-arm64-alembic.sh

carla-opensubdiv:
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) carla-dev \
		bash /opt/my-ad/scripts/carla/usd/build-arm64-opensubdiv.sh

.PHONY: carla-materialx carla-python
carla-materialx:
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) carla-dev \
		bash /opt/my-ad/scripts/carla/usd/build-arm64-materialx.sh

carla-python:
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) carla-dev \
		bash /opt/my-ad/scripts/carla/usd/build-arm64-python.sh

.PHONY: carla-openusd
carla-openusd:
	@test -n "$(DEPENDENCIES)" || { echo "DEPENDENCIES is required (container JSON report map)"; exit 64; }
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-v "$(CURDIR)/config/carla:/opt/my-ad/config/carla:ro" \
		-e CARLA_USD_DEPENDENCIES="$(DEPENDENCIES)" carla-dev \
		bash /opt/my-ad/scripts/carla/usd/build-arm64-openusd.sh

.PHONY: carla-boost carla-tbb-static
carla-boost:
	@test -n "$(PYTHON_REPORT)" -a -n "$(PYTHON_REPORT_SHA256)" || { echo "PYTHON_REPORT and PYTHON_REPORT_SHA256 are required"; exit 64; }
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-e CARLA_PYTHON_REPORT="$(PYTHON_REPORT)" -e CARLA_PYTHON_REPORT_SHA256="$(PYTHON_REPORT_SHA256)" \
		carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-boost.sh

carla-tbb-static:
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		carla-dev bash /opt/my-ad/scripts/carla/usd/build-arm64-tbb-static.sh

carla-test:
	$(CARLA_COMPOSE) --profile g0 run --rm -T \
		-v "$(CURDIR):/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
		carla-dev make test-local

carla-g0:
	$(CARLA_COMPOSE) --profile g0 run --rm carla-dev

carla-shell:
	$(CARLA_COMPOSE) --profile g0 run --rm carla-dev bash

carla-build-shell:
	$(CARLA_COMPOSE) --profile build run --rm carla-build bash

carla-ue-check:
	$(CARLA_COMPOSE) --profile ue-setup run --rm carla-ue-setup check

carla-ue-setup:
	$(CARLA_COMPOSE) --profile ue-setup run --rm carla-ue-setup setup

carla-shader-deps:
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-shader-deps.sh $(or $(SHADER_DEP),all)

carla-scw:
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-scw.sh

carla-ispc:
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-ispc.sh

carla-ue-build:
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-carla-ue.sh

carla-startup-probe:
	$(CARLA_COMPOSE) --profile g0 run --rm -T carla-dev \
		bash /opt/my-ad/scripts/carla/probe-arm64-startup.sh

carla-editor-check:
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_EDITOR_PROFILE=$(or $(EDITOR_PROFILE),full) \
		-e CARLA_USD_NATIVE_ROOT="$(NATIVE_SDK_ROOT)" \
		-e CARLA_ARM64_FBX_HEADERS_ONLY="$(or $(ARM64_FBX_HEADERS_ONLY),0)" carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-editor.sh


carla-editor-deps:
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-editor-deps.sh

carla-editor-build:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_EDITOR_PROFILE=$(or $(EDITOR_PROFILE),fbx-skip) \
		-e CARLA_EDITOR_BUILD=1 -e CARLA_EDITOR_BUILD_TIMEOUT=$(or $(EDITOR_BUILD_TIMEOUT),14400) carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-editor.sh

carla-editor-startup:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_EDITOR_STARTUP_TIMEOUT=$(or $(EDITOR_STARTUP_TIMEOUT),60) carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-editor-startup.sh

carla-editor-cook:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_EDITOR_COOK_TIMEOUT=$(or $(EDITOR_COOK_TIMEOUT),600) \
		-e CARLA_EDITOR_COOK_PACKAGE="$(or $(EDITOR_COOK_PACKAGE),/Game/Carla/RT_LuminanceCapture)" \
		-e CARLA_EDITOR_COOK_PACKAGE_EXTENSION=$(or $(EDITOR_COOK_PACKAGE_EXTENSION),uasset) carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-editor-cook.sh

carla-full-cook:
	$(CARLA_COMPOSE) --profile build run --rm -T \
	-e CARLA_FULL_COOK_TIMEOUT=$(or $(FULL_COOK_TIMEOUT),1800) \
	-e CARLA_FULL_COOK_TARGET_PLATFORM=$(or $(FULL_COOK_TARGET_PLATFORM),LinuxArm64Server) \
	-e CARLA_FULL_COOK_RENDERING=$(or $(FULL_COOK_RENDERING),0) \
	-e CARLA_VK_ICD_FILENAMES="$(or $(CARLA_VK_ICD_FILENAMES),)" carla-build \
	bash /opt/my-ad/scripts/carla/probe-arm64-full-cook.sh

carla-stage-cooked-server:
	@test -n "$(FULL_COOK_OUTPUT)" || { echo "FULL_COOK_OUTPUT is required (container path)"; exit 64; }
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_FULL_COOK_OUTPUT="$(FULL_COOK_OUTPUT)" \
		-e CARLA_COOKED_SERVER_STAGE="$(or $(COOKED_SERVER_STAGE),/artifacts/carla/cooked-server-full/CarlaUnreal)" carla-build \
		bash /opt/my-ad/scripts/carla/stage-arm64-cooked-server.sh

carla-stage-cooked-client:
	@test -n "$(FULL_COOK_OUTPUT)" || { echo "FULL_COOK_OUTPUT is required (container path)"; exit 64; }
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_FULL_COOK_OUTPUT="$(FULL_COOK_OUTPUT)" \
		-e CARLA_COOKED_CLIENT_STAGE="$(or $(COOKED_CLIENT_STAGE),/artifacts/carla/cooked-client-full/CarlaUnreal)" carla-build \
		bash /opt/my-ad/scripts/carla/stage-arm64-cooked-client.sh

carla-cooked-server:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_COOKED_SERVER_TIMEOUT=$(or $(COOKED_SERVER_TIMEOUT),30) \
		-e CARLA_COOKED_SERVER_ROOT="$(or $(COOKED_SERVER_ROOT),/artifacts/carla/cooked-server/CarlaUnreal)" \
		-e CARLA_COOKED_SERVER_MAP="$(or $(COOKED_SERVER_MAP),/Game/Carla/Maps/OpenDriveMap)" \
		-e CARLA_COOKED_SERVER_PORT=$(or $(COOKED_SERVER_PORT),7777) \
		-e CARLA_COOKED_SERVER_REQUIRE_CONTENT=$(or $(COOKED_SERVER_REQUIRE_CONTENT),1) carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-cooked-server.sh

carla-lavapipe-sensors:
	bash scripts/carla/run-carla-lavapipe-sensors.sh

carla-town10-nullrhi-rpc:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_NULLRHI_PORT=$(or $(NULLRHI_PORT),20200) \
		-e CARLA_NULLRHI_TICKS=$(or $(NULLRHI_TICKS),20) carla-build \
		bash /opt/my-ad/scripts/carla/probe-town10-nullrhi-rpc.sh

# Captures the failing check behind the shutdown crash. No GPU is needed: the defect
# reproduces under NullRHI, which is why this stays in the build profile.
.PHONY: carla-ue-shutdown-crash
carla-ue-shutdown-crash:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		--cap-add SYS_PTRACE \
		-e CARLA_UE_SHUTDOWN_PORT=$(or $(UE_SHUTDOWN_PORT),20221) \
		-e CARLA_UE_SHUTDOWN_STARTUP_TIMEOUT=$(or $(UE_SHUTDOWN_STARTUP_TIMEOUT),120) \
		-e CARLA_UE_SHUTDOWN_SETTLE=$(or $(UE_SHUTDOWN_SETTLE),10) \
		-e CARLA_UE_SHUTDOWN_GRACE=$(or $(UE_SHUTDOWN_GRACE),60) carla-build \
		bash /opt/my-ad/scripts/carla/probe-ue-shutdown-crash.sh

carla-vulkan-compute:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e CARLA_COMPUTE_BACKEND=$(or $(COMPUTE_BACKEND),lavapipe) \
		-e CARLA_COMPUTE_MODE=$(or $(COMPUTE_MODE),smoke) \
		-e CARLA_COMPUTE_SHADER="$(or $(COMPUTE_SHADER),)" \
		-e CARLA_COMPUTE_SHADER_DIR="$(or $(COMPUTE_SHADER_DIR),)" \
		-e CARLA_COMPUTE_LAYOUT_FILE="$(or $(COMPUTE_LAYOUT_FILE),)" \
		-e CARLA_COMPUTE_DEVICE_STATE="$(or $(COMPUTE_DEVICE_STATE),)" \
		-e CARLA_COMPUTE_CACHE_FILE="$(or $(COMPUTE_CACHE_FILE),)" \
		-e CARLA_COMPUTE_CACHE_FLAGS=$(or $(COMPUTE_CACHE_FLAGS),0) \
		-e CARLA_COMPUTE_TIMEOUT=$(or $(COMPUTE_TIMEOUT),30) carla-build \
		bash /opt/my-ad/scripts/carla/probe-vulkan-compute-replay.sh

carla-vulkan-compute-gpu:
	$(CARLA_COMPOSE) --profile gpu run --rm -T \
		-e CARLA_COMPUTE_BACKEND=gb10 \
		-e CARLA_COMPUTE_MODE=$(or $(COMPUTE_MODE),create) \
		-e CARLA_COMPUTE_SHADER="$(or $(COMPUTE_SHADER),)" \
		-e CARLA_COMPUTE_SHADER_DIR="$(or $(COMPUTE_SHADER_DIR),)" \
		-e CARLA_COMPUTE_LAYOUT_FILE="$(or $(COMPUTE_LAYOUT_FILE),)" \
		-e CARLA_COMPUTE_DEVICE_STATE="$(or $(COMPUTE_DEVICE_STATE),)" \
		-e CARLA_COMPUTE_CACHE_FILE="$(or $(COMPUTE_CACHE_FILE),)" \
		-e CARLA_COMPUTE_CACHE_FLAGS=$(or $(COMPUTE_CACHE_FLAGS),0) \
		-e CARLA_COMPUTE_TIMEOUT=$(or $(COMPUTE_TIMEOUT),45) carla-vulkan \
		bash /opt/my-ad/scripts/carla/probe-vulkan-compute-replay.sh

carla-ue-vulkan-device-state:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		--cap-add SYS_PTRACE \
		-e CARLA_UE_DEVICE_STATE_PORT=$(or $(UE_DEVICE_STATE_PORT),20207) \
		-e CARLA_VK_ICD_FILENAMES="$(or $(UE_DEVICE_STATE_ICD),)" \
		-e CARLA_UE_DEVICE_STATE_TIMEOUT=$(or $(UE_DEVICE_STATE_TIMEOUT),90) carla-build \
		bash /opt/my-ad/scripts/carla/probe-ue-vulkan-device-state.sh

carla-ue-vulkan-device-state-gpu:
	$(CARLA_COMPOSE) --profile gpu run --rm -T \
		--cap-add SYS_PTRACE \
		-e CARLA_UE_DEVICE_STATE_PORT=$(or $(UE_DEVICE_STATE_PORT),20207) \
		-e CARLA_VK_ICD_FILENAMES="$(or $(UE_DEVICE_STATE_ICD),/etc/vulkan/icd.d/nvidia_icd.json)" \
		-e CARLA_UE_DEVICE_STATE_TIMEOUT=$(or $(UE_DEVICE_STATE_TIMEOUT),90) carla-vulkan \
		bash /opt/my-ad/scripts/carla/probe-ue-vulkan-device-state.sh

.PHONY: carla-ue-vulkan-pipeline
carla-ue-vulkan-pipeline:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		--cap-add SYS_PTRACE \
		-e CARLA_UE_PIPELINE_TARGET="$(or $(UE_PIPELINE_TARGET),FRDGMemcpyCS)" \
		-e CARLA_UE_PIPELINE_MODE=$(or $(UE_PIPELINE_MODE),entry) \
		-e CARLA_UE_PIPELINE_INTERVENTION=$(or $(UE_PIPELINE_INTERVENTION),none) \
		-e CARLA_UE_PIPELINE_TIMEOUT=$(or $(UE_PIPELINE_TIMEOUT),120) \
		-e CARLA_UE_PIPELINE_PORT=$(or $(UE_PIPELINE_PORT),20211) carla-build \
		bash /opt/my-ad/scripts/carla/probe-ue-vulkan-pipeline.sh

.PHONY: carla-town10-gb10-runtime
carla-town10-gb10-runtime:
	$(CARLA_COMPOSE) --profile gpu run --rm -T \
		-e CARLA_RUNTIME_MODE=$(or $(RUNTIME_MODE),rpc) \
		-e CARLA_GB10_RENDER_PROFILE=$(or $(GB10_RENDER_PROFILE),default) \
		-e CARLA_RUNTIME_PORT=$(or $(RUNTIME_PORT),20220) \
		-e CARLA_RUNTIME_TICKS=$(or $(RUNTIME_TICKS),20) \
		-e CARLA_CLIENT_STARTUP_TIMEOUT=$(or $(CLIENT_STARTUP_TIMEOUT),120) \
		-e CARLA_RUNTIME_TOTAL_TIMEOUT=$(or $(RUNTIME_TOTAL_TIMEOUT),180) \
		-e CARLA_RUNTIME_SHADER_DIAGNOSTICS=$(or $(RUNTIME_SHADER_DIAGNOSTICS),0) \
		-e CARLA_GB10_SERIALIZE_COMPUTE_PIPELINES=$(or $(GB10_SERIALIZE_COMPUTE_PIPELINES),0) \
		-e CARLA_RUNTIME_PIPELINE_HISTORY=$(or $(RUNTIME_PIPELINE_HISTORY),0) \
		-e CARLA_RUNTIME_PIPELINE_HISTORY_TARGET=$(or $(RUNTIME_PIPELINE_HISTORY_TARGET),main_0000142c_a6b37050) \
		-e CARLA_GB10_SERIALIZE_GRAPHICS_PIPELINES=$(or $(GB10_SERIALIZE_GRAPHICS_PIPELINES),0) \
		-e CARLA_RUNTIME_GRAPHICS_CACHE_SNAPSHOT=$(or $(RUNTIME_GRAPHICS_CACHE_SNAPSHOT),0) \
		-e CARLA_RUNTIME_CACHE_LIFECYCLE=$(or $(RUNTIME_CACHE_LIFECYCLE),0) \
		-e CARLA_RUNTIME_NULL_PIPELINE_CACHE=$(or $(RUNTIME_NULL_PIPELINE_CACHE),0) \
		-e CARLA_RUNTIME_VULKAN_VALIDATION=$(or $(RUNTIME_VULKAN_VALIDATION),0) \
		-e CARLA_RUNTIME_VULKAN_DEBUG_UTILS=$(or $(RUNTIME_VULKAN_DEBUG_UTILS),0) \
		-e CARLA_RUNTIME_VULKAN_VALIDATION_STACK_TRACE="$(or $(RUNTIME_VULKAN_VALIDATION_STACK_TRACE),)" \
		-e CARLA_GB10_SERIALIZE_DRIVER_CALLS=$(or $(GB10_SERIALIZE_DRIVER_CALLS),0) \
		-e CARLA_GB10_SERIALIZE_MIXED_PIPELINES=$(or $(GB10_SERIALIZE_MIXED_PIPELINES),0) carla-vulkan \
		bash /opt/my-ad/scripts/carla/probe-town10-gb10-runtime.sh

.PHONY: carla-vulkan-validation-layer
carla-vulkan-validation-layer:
	bash scripts/carla/fetch-vulkan-validation-layer.sh

# Runs the GB10 Town10 gate under each Vulkan diagnostic configuration and
# classifies the results; a vkCreateDevice rejection is reported as BLOCKED
# instead of a product result. See docs/carla-dgx-audit.md 9.76.
.PHONY: carla-gb10-vulkan-comparison
carla-gb10-vulkan-comparison:
	python3 scripts/carla/compare_gb10_vulkan_diagnostics.py $(if $(CONFIGS),--configs $(CONFIGS),) $(if $(RUN_ALL),--run-all,)

# Symbolizes a crashed GB10 run with the ARM64 Development debug binary; the
# staged client is built with -NoDumpSyms so UE prints UnknownFunction there.
.PHONY: carla-symbolize-client-crash
carla-symbolize-client-crash:
	@test -n "$(RUN_DIR)" || { echo "RUN_DIR is required (container path, e.g. /artifacts/carla/town10-gb10-rpc-...)"; exit 64; }
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_CRASH_RUN_DIR="$(RUN_DIR)" carla-build \
		python3 /opt/my-ad/scripts/carla/symbolize_client_crash.py $(if $(MODE),--mode $(MODE),)

# Collects the existing run evidence into a vendor-escalation bundle; no new
# run is executed, so the report cannot drift from the artifacts it cites.
.PHONY: carla-gb10-driver-report
carla-gb10-driver-report:
	@test -n "$(RUN_DIRS)" || { echo "RUN_DIRS is required (space separated host run directories)"; exit 64; }
	python3 scripts/carla/collect_gb10_driver_report.py $(addprefix --run-dir ,$(RUN_DIRS))

# Records what the two CARLA forks actually are: HEAD plus the tracked
# working-tree delta the staged binaries are built from. A bare commit hash
# cannot pin these trees, because the ARM64/editor-only patches and the
# diagnostic instrumentation used to live as uncommitted modifications. Since
# the fork work was committed and pushed, both fields record an empty delta -
# which is the point: the record now says the trees are clean. See
# docs/carla-dgx-audit.md 9.86 and 9.93.
.PHONY: carla-fork-provenance
carla-fork-provenance:
	python3 scripts/carla/report_fork_provenance.py $(if $(OUTPUT),--output $(OUTPUT),)

# Installs the runtime record the probes consume. It lives under artifacts/, which the container
# owns, so the write has to happen in there; the paths recorded are container paths for the same
# reason. The container runs as root while the mounted forks are owned by the host user, so git
# refuses them as "dubious ownership" and has to be told to trust these two checkouts.
.PHONY: carla-fork-provenance-install
carla-fork-provenance-install:
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-e GIT_CONFIG_COUNT=2 \
		-e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/workspace/carla \
		-e GIT_CONFIG_KEY_1=safe.directory -e GIT_CONFIG_VALUE_1=/workspace/unreal-engine \
		carla-build \
		python3 /opt/my-ad/scripts/carla/report_fork_provenance.py \
		--carla-repo /workspace/carla --ue-repo /workspace/unreal-engine \
		--output /artifacts/carla/cooked-server-full/runtime-provenance.json

# Re-derives the provenance from the live trees and reports every field where
# the recording disagrees, so a stale revision is detected instead of being
# inherited by the next run.
.PHONY: carla-fork-provenance-verify
carla-fork-provenance-verify:
	python3 scripts/carla/report_fork_provenance.py \
		--verify $(or $(PROVENANCE_FILE),artifacts/carla/cooked-server-full/runtime-provenance.json)

# Freezes each fork's uncommitted tracked delta as a patch beside the curated
# ones, so the tree the staged binaries are built from is described by the
# repository instead of only by a point-in-time manifest under artifacts/.
.PHONY: carla-fork-delta
carla-fork-delta:
	python3 scripts/carla/freeze_fork_delta.py freeze

# Recomputes the delta and compares it with the frozen patch, so a tree that has
# moved on is reported rather than silently inherited.
.PHONY: carla-fork-delta-verify
carla-fork-delta-verify:
	python3 scripts/carla/freeze_fork_delta.py verify

.PHONY: carla-ue-vulkan-fault-gpu
carla-ue-vulkan-fault-gpu:
	$(CARLA_COMPOSE) --profile gpu run --rm -T \
		--cap-add SYS_PTRACE \
		-e CARLA_UE_FAULT_TIMEOUT=$(or $(UE_FAULT_TIMEOUT),120) \
		-e CARLA_UE_FAULT_TRACE_GFX=$(or $(UE_FAULT_TRACE_GFX),0) \
		-e CARLA_UE_FAULT_MEMORY_TRACE=$(or $(UE_FAULT_MEMORY_TRACE),0) \
		-e CARLA_UE_FAULT_PORT=$(or $(UE_FAULT_PORT),20231) carla-vulkan \
		bash /opt/my-ad/scripts/carla/probe-ue-vulkan-fault.sh

.PHONY: carla-ue-vulkan-memory-trace-gpu
carla-ue-vulkan-memory-trace-gpu:
	$(CARLA_COMPOSE) --profile gpu run --rm -T \
		-e CARLA_UE_MEMORY_TRACE_TIMEOUT=$(or $(UE_MEMORY_TRACE_TIMEOUT),180) \
		-e CARLA_UE_MEMORY_TRACE_PORT=$(or $(UE_MEMORY_TRACE_PORT),20232) carla-vulkan \
		bash /opt/my-ad/scripts/carla/probe-ue-vulkan-memory-trace.sh

carla-lavapipe-soak:
	CARLA_RUNTIME_MODE=$(or $(CARLA_RUNTIME_MODE),sensors) \
	CARLA_RUNTIME_TICKS=$(or $(CARLA_RUNTIME_TICKS),6000) \
	CARLA_RUNTIME_TOTAL_TIMEOUT=$(or $(CARLA_RUNTIME_TOTAL_TIMEOUT),10800) \
	bash scripts/carla/run-carla-lavapipe-sensors.sh

.PHONY: carla-legacy-fbx carla-usd-sdk
carla-legacy-fbx:
	@test -n "$(NATIVE_SDK_ROOT)" || { echo "NATIVE_SDK_ROOT is required"; exit 64; }
	$(CARLA_COMPOSE) --profile build run --rm -T carla-build \
		python3 -B /opt/my-ad/scripts/carla/probe_legacy_fbx.py \
		--sdk-root "$(NATIVE_SDK_ROOT)" --jobs "$(or $(JOBS),4)" --timeout "$(or $(TIMEOUT),600)"

carla-usd-sdk:
	@test -n "$(OPENUSD_REPORT)" -a -n "$(DEPENDENCIES)" || { echo "OPENUSD_REPORT and DEPENDENCIES are required"; exit 64; }
	$(CARLA_COMPOSE) --profile build run --rm -T \
		-v "$(CURDIR)/config/carla:/opt/my-ad/config/carla:ro" carla-build \
		python3 -B /opt/my-ad/scripts/carla/prepare_native_usd_sdk.py prepare \
		--ue-root /workspace/unreal-engine --openusd-report "$(OPENUSD_REPORT)" \
		--dependencies "$(DEPENDENCIES)" --artifact-root /artifacts/carla/sdk-bindings

carla-audio-deps:
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-audio-deps.sh

carla-assimp:
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-dev \
		bash /opt/my-ad/scripts/carla/build-arm64-assimp.sh

carla-ue-meshbridge: carla-assimp
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),8) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-ue-meshbridge.sh

carla-vulkan:
	$(CARLA_COMPOSE) --profile gpu run --rm -T carla-vulkan

carla-runtime-check:
	@test -n "$(PROVENANCE)" || { echo "PROVENANCE is required (absolute path inside the container)"; exit 64; }
	$(CARLA_COMPOSE) --profile g0 run --rm -T \
		-e CARLA_RUNTIME_PROVENANCE="$(PROVENANCE)" \
		-e CARLA_RUNTIME_MODE="$(or $(RUNTIME_MODE),rpc)" \
		-e CARLA_RUNTIME_TICKS="$(or $(TICKS),100)" \
		-e CARLA_RUNTIME_PORT="$(or $(PORT),2000)" \
		-e CARLA_ALLOW_WORLD_MUTATION="$(or $(ALLOW_WORLD_MUTATION),0)" \
		carla-dev bash /opt/my-ad/scripts/carla/probe-arm64-runtime.sh

carla-interchange: carla-assimp carla-ufbx
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-interchange-nodes.sh

.PHONY: carla-legacy-hierarchy
carla-legacy-hierarchy: carla-assimp carla-ufbx
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-e CARLA_INTERCHANGE_MODE=legacy carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-interchange-nodes.sh

.PHONY: carla-staticmesh
carla-staticmesh: carla-assimp carla-ufbx
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-e CARLA_STATICMESH_BUILD_TIMEOUT=$(or $(TIMEOUT),1800) carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-staticmesh.sh

.PHONY: carla-asset
carla-asset: carla-assimp carla-ufbx
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-e CARLA_ASSET_BUILD_TIMEOUT=$(or $(TIMEOUT),1800) carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-asset.sh

.PHONY: carla-interchange-parser
carla-interchange-parser: carla-assimp carla-ufbx
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-e CARLA_INTERCHANGE_MODE=parser carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-interchange-nodes.sh

.PHONY: carla-interchange-worker
carla-interchange-worker: carla-assimp carla-ufbx
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) \
		-e CARLA_INTERCHANGE_MODE=worker carla-build \
		bash /opt/my-ad/scripts/carla/probe-arm64-interchange-nodes.sh

carla-ufbx:
	$(CARLA_COMPOSE) --profile g0 run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) carla-dev \
		bash /opt/my-ad/scripts/carla/build-arm64-ufbx.sh

carla-ue-ufbxbridge: carla-assimp carla-ufbx
	$(CARLA_COMPOSE) --profile build run --rm -T -e CARLA_BUILD_JOBS=$(or $(JOBS),4) carla-build \
		bash /opt/my-ad/scripts/carla/build-arm64-ue-ufbxbridge.sh

scenario:
	ENV_FILE="$(ENV_FILE)" ./scripts/ops/run-dgx-mode.sh scenario

scenario-up:
	ENV_FILE="$(ENV_FILE)" ./scripts/ops/run-dgx-mode.sh scenario-up

scenario-prepare:
	ENV_FILE="$(ENV_FILE)" ./scripts/ops/prepare-scenario.sh

isaac:
	$(DGX_COMPOSE) --profile isaac up -d isaac-sim autoware

build-navsim:
	$(DGX_COMPOSE) --profile navsim build navsim

navsim:
	$(DGX_COMPOSE) --profile navsim run --rm --no-deps navsim

navsim-cache:
	$(DGX_COMPOSE) --profile navsim run --rm --no-deps navsim \
		bash -lc 'cd "$$NAVSIM_DEVKIT_ROOT/scripts/evaluation" && ./run_metric_caching.sh'

data:
	$(DGX_COMPOSE) --profile data build dataset-converter
	$(DGX_COMPOSE) --profile data run --rm dataset-converter

deploy:
	$(DGX_COMPOSE) --profile deploy up -d tensorrt-build

test-compose:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh compose

test-local:
	python3 -m unittest discover -s tests -v
	@find scripts -type f -name '*.sh' -print0 | xargs -0 -n1 bash -n

harness-host:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh host

harness-gpu:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh gpu

harness-network:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh network

harness-clock:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh clock

harness-runtime:
	@test -n "$(SERVICE)" || { echo "SERVICE is required"; exit 64; }
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh runtime "$(SERVICE)"

harness-ros:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh ros

harness-ros-scenario:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh ros /config/harness/required-topics-scenario.txt

inspect-modules:
	$(DGX_COMPOSE) --profile harness run --build --rm --no-deps ros-probe \
		/opt/my-ad/scripts/harness/inspect-modules.sh /config/harness/module-topics.txt "$(MODULE)"

learn-module:
	@test -n "$(MODULE)" || { echo "MODULE is required"; exit 64; }
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh learning "$(MODULE)" "$(DURATION)"

harness-navsim:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh navsim

down-dgx:
	$(DGX_COMPOSE) down --remove-orphans

down-sim:
	$(SIM_COMPOSE) down --remove-orphans
