SHELL := /usr/bin/env bash

ENV_FILE ?= .env
DGX_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.dgx.yaml
SIM_COMPOSE := docker compose --env-file $(ENV_FILE) -f compose.sim-x86.yaml

.PHONY: init preflight config up-dgx up-sim down-dgx down-sim record replay data test-compose

init:
	mkdir -p data/maps data/bags data/ground_truth data/datasets data/models data/engines data/logs data/reports data/cache artifacts
	@test -f $(ENV_FILE) || cp .env.example $(ENV_FILE)
	@echo "Review $(ENV_FILE) and replace every REPLACE_ value before starting containers."

preflight:
	@set -a; source $(ENV_FILE); set +a; ./scripts/preflight/check.sh

config:
	ENV_FILE=$(ENV_FILE) ./scripts/harness/compose-config.sh

up-dgx:
	$(DGX_COMPOSE) up -d autoware

up-sim:
	$(SIM_COMPOSE) up -d awsim

record:
	$(DGX_COMPOSE) --profile record up recorder

replay:
	$(DGX_COMPOSE) --profile replay run --rm replay

data:
	$(DGX_COMPOSE) --profile data build dataset-converter
	$(DGX_COMPOSE) --profile data run --rm dataset-converter

test-compose:
	ENV_FILE=$(ENV_FILE) ./scripts/harness/run.sh compose

down-dgx:
	$(DGX_COMPOSE) down --remove-orphans

down-sim:
	$(SIM_COMPOSE) down --remove-orphans
