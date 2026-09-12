SHELL := /usr/bin/env bash

ENV_FILE ?= .env
DGX_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.dgx.yaml
SIM_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.sim-x86.yaml

.PHONY: init preflight config build-tools build-tools-sim build-navsim up-dgx up-sim collect-sim down-dgx down-sim \
	record record-sim replay viz scenario scenario-up scenario-prepare isaac navsim navsim-cache data deploy test-compose test-local \
	harness-host harness-gpu harness-network harness-clock harness-runtime harness-ros harness-ros-scenario harness-navsim inspect-modules collect-env

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

harness-navsim:
	ENV_FILE="$(ENV_FILE)" ./scripts/harness/run.sh navsim

down-dgx:
	$(DGX_COMPOSE) down --remove-orphans

down-sim:
	$(SIM_COMPOSE) down --remove-orphans
