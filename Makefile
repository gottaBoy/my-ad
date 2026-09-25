SHELL := /usr/bin/env bash

ENV_FILE ?= .env
MODULE ?=
DURATION ?= 5
DGX_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.dgx.yaml
SIM_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.sim-x86.yaml
CARLA_COMPOSE_PROJECT_NAME ?= my-ad-carla
CARLA_COMPOSE := docker compose --project-name "$(CARLA_COMPOSE_PROJECT_NAME)" --env-file $(ENV_FILE) -f compose.carla-arm64.yaml

.PHONY: init preflight config build-tools build-tools-sim build-navsim up-dgx up-sim collect-sim down-dgx down-sim \
	record record-sim replay viz scenario scenario-up scenario-prepare isaac navsim navsim-cache data deploy test-compose test-local \
	harness-host harness-gpu harness-network harness-clock harness-runtime harness-ros harness-ros-scenario harness-navsim inspect-modules learn-module collect-env \
	carla-config carla-g0 carla-shell carla-build-shell carla-ue-check carla-ue-setup carla-shader-deps carla-scw carla-ispc carla-ue-build carla-startup-probe carla-editor-check carla-editor-deps carla-editor-build carla-editor-startup carla-editor-cook carla-full-cook carla-stage-cooked-server carla-cooked-server carla-lavapipe-sensors carla-audio-deps carla-assimp carla-ue-meshbridge carla-vulkan carla-ufbx carla-ue-ufbxbridge carla-runtime-check carla-interchange carla-manifest carla-manifest-verify carla-usd-inventory

init:
	mkdir -p data/maps/sample-map-planning data/bags data/ground_truth data/datasets data/models data/engines data/logs data/reports data/cache \
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
		carla-dev python3 /opt/my-ad/scripts/carla/capture_build_manifest.py capture \
		--project-root /repo --carla-root /workspace/carla --ue-root /workspace/unreal-engine \
		--artifact-root /artifacts/carla/build-manifests --command make carla-manifest

carla-manifest-verify:
	@test -n "$(MANIFEST)" || { echo "MANIFEST is required (container path)"; exit 64; }
	$(CARLA_COMPOSE) --profile g0 run --rm -T \
		-v "$(CURDIR):/repo:ro" -w /repo -e PYTHONDONTWRITEBYTECODE=1 \
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
