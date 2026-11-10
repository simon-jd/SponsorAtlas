.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

# Project-owned toolchains only; nothing here relies on host Node/Python.
ROOT := $(abspath .)
NODE_BIN := $(ROOT)/.tools/node-runtime/node-v24.22.1-linux-x64/bin
PYTHON := $(ROOT)/.venv/bin/python
export PATH := $(NODE_BIN):$(ROOT)/.venv/bin:$(PATH)
export PYTHONDONTWRITEBYTECODE := 1
export TZ := UTC
PYTEST := $(PYTHON) -m pytest -q -p no:cacheprovider

.PHONY: help test check lint-fix test-python test-orchestration test-semantic test-web test-js \
	semantic-fixture toolchains azure-reap azure-down staging-actual cdc-drill \
	semantic-actual viz-worksite-export viz-frame-build viz-frame-measure viz-e2e site-e2e-actual airbyte-up airbyte-status airbyte-down

# Opt-in only (never set by `make test`): RUN_SNOWFLAKE_GRANTS=1, RUN_SNOWFLAKE_POLICIES=1,
# SPONSORATLAS_ER_ACCEPTANCE=1, SPONSORATLAS_SPARK_CONNECT_TEST=1, SPONSORATLAS_WRITE_EVIDENCE=1.
# Browser/Storybook/Playwright/LHCI runs use the shared GPU and stay in
# web/scripts/quality-ci.sh behind flock /var/tmp/rtx4090.lock; they are not part of `make test`.
help:
	@printf '%s\n' \
		'make test        all offline suites (live/cloud/GPU suites skip unless opted in)' \
		'make check       ruff + TypeScript typecheck' \
		'make lint-fix    apply safe ruff fixes' \
		'make test-python | test-orchestration | test-semantic | test-web | test-js' \
		'make azure-reap  clean expired project resources' \
		'make azure-down  remove the verified project resource group'

test: toolchains test-python test-orchestration test-semantic test-web test-js
	@echo 'make test: all offline suites passed'

toolchains:
	@test -x $(PYTHON) || { echo 'missing .venv (see README: uv sync)'; exit 1; }
	@test "$$($(NODE_BIN)/node --version)" = v24.22.1 || { echo 'missing project Node 24.22.1 in .tools/node-runtime'; exit 1; }
	@test -x .tools/orchestration/venv/bin/pytest || { echo 'missing .tools/orchestration/venv'; exit 1; }
	@test -d .tools/hyper-api/tableauhyperapi || { echo 'missing .tools/hyper-api (see bi/public/BUILD.md)'; exit 1; }

test-python:
	$(PYTEST)

# Builds the synthetic Parquet fixture under .tools/semantic/fixture (idempotent).
semantic-fixture:
	semantic/run.sh prepare fixture >/dev/null

test-orchestration: semantic-fixture
	orchestration/run.sh pytest -q -p no:cacheprovider orchestration/tests

test-semantic:
	semantic/run.sh test fixture
	semantic/run.sh test-source

test-web:
	cd web/apps/site && node --conditions=react-server --test tests/*.test.ts
	cd web/packages/ui && node --test tests/tokens.test.mjs
	node --test web/scripts/quality-environment.test.mjs

NODE_TEST := node --max-old-space-size=768 --test --test-concurrency=1 --test-force-exit \
	--test-timeout=120000

test-js: semantic-fixture
	$(NODE_TEST) orchestration/tests/*.test.cjs azure/tests/*.test.cjs
	cd semantic && $(NODE_TEST) tests/*.test.cjs

# Real-file staging build (T3) on the local DuckDB loaded by scripts.load_staging_files.
# Writes the dbt log and a summary JSON to $(STAGING_EVIDENCE); exits with dbt's status.
STAGING_ACTUAL_DB ?= $(ROOT)/.tools/staging/fy2025-full.duckdb
STAGING_EVIDENCE ?= $(ROOT)/docs/evidence/task03-staging
STAGING_RUN := SPONSORATLAS_DUCKDB_PATH=$(STAGING_ACTUAL_DB) \
	DBT_TARGET_PATH=$(ROOT)/.tools/staging/actual-target DBT_LOG_PATH=$(ROOT)/.tools/staging/actual-logs
staging-actual:
	mkdir -p $(STAGING_EVIDENCE)
	$(STAGING_RUN) dbt/run.sh seed > $(STAGING_EVIDENCE)/seed.log
	status=0; $(STAGING_RUN) dbt/run.sh build --select staging > $(STAGING_EVIDENCE)/build.log || status=$$?; \
	cp .tools/staging/actual-target/run_results.json $(STAGING_EVIDENCE)/run_results.json; \
	$(PYTHON) -m scripts.staging_actual_report --database $(STAGING_ACTUAL_DB) \
		--run-results $(STAGING_EVIDENCE)/run_results.json --exit-code $$status \
		--output $(STAGING_EVIDENCE)/summary.json; \
	exit $$status

# Local CDC drill (T2): throw-away project Postgres (wal_level=logical, pgoutput slot)
# -> DuckDB RAW_JOBS. The container is removed on exit, success or failure.
CDC_PG := sponsoratlas-cdc-pg
CDC_PORT ?= 55439
CDC_EVIDENCE ?= $(ROOT)/docs/evidence/task02-cdc
CDC_RAW ?= $(ROOT)/.tools/cdc/raw-$(shell date -u +%Y%m%dT%H%M%SZ).duckdb
CDC_INTERVAL ?= 30
CDC_PSQL := docker exec -i $(CDC_PG) psql -v ON_ERROR_STOP=1 -q -U postgres -d sponsoratlas
cdc-drill:
	mkdir -p $(CDC_EVIDENCE)
	trap 'docker rm -f $(CDC_PG) >/dev/null' EXIT; \
	docker run -d --name $(CDC_PG) --label project=sponsoratlas -p 127.0.0.1:$(CDC_PORT):5432 \
		-e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=sponsoratlas --memory 512m \
		postgres:16-alpine -c wal_level=logical -c max_replication_slots=4 \
		-c max_wal_senders=4 -c max_slot_wal_keep_size=4GB >/dev/null; \
	for i in $$(seq 60); do docker exec $(CDC_PG) pg_isready -q -U postgres -d sponsoratlas && break; sleep 1; done; \
	sleep 2; \
	$(CDC_PSQL) < ingestion/postgres/demo_schema.sql; \
	echo 'CREATE ROLE airbyte_replication LOGIN REPLICATION;' | $(CDC_PSQL); \
	$(CDC_PSQL) < ingestion/postgres/configure_cdc.sql > $(CDC_EVIDENCE)/configure_cdc.out; \
	$(PYTHON) -m scripts.cdc_drill --dsn 'host=127.0.0.1 port=$(CDC_PORT) user=postgres dbname=sponsoratlas' \
		--raw $(CDC_RAW) --interval $(CDC_INTERVAL) --output $(CDC_EVIDENCE)/drill.json

# Exports from the canonical full-year DBs (scripts/canonical_paths.py): the Cube Parquet in
# .tools/semantic/actual plus Cube's metric/oracle test on it, then the map projection
# (which records the Cube lca.parquet SHA, so run semantic-actual first).
semantic-actual:
	nproc; uptime
	semantic/run.sh test actual
	uptime
viz-worksite-export:
	$(PYTHON) -m semantic.scripts.export_worksite_coordinates

# T14 frame-work measurement on the worksite projection (.tools/viz/worksite-fy2025). The build
# needs no GPU; the browser run queues on the shared GPU lock and releases it when done.
VIZ_LABEL ?= worksite
viz-frame-build:
	web/scripts/viz-frame-run.sh build
viz-frame-measure:
	nproc; uptime
	flock /var/tmp/rtx4090.lock web/scripts/viz-frame-run.sh measure $(VIZ_LABEL)
	uptime
# Browser E2E on the built site (run viz-frame-build first), each under the shared GPU lock:
# every viz.spec test on the actual projection, and the site journeys on actual Cube + Parquet.
viz-e2e:
	nproc; uptime
	flock /var/tmp/rtx4090.lock bash web/scripts/site-command.sh node ../../scripts/viz-browser-run.mjs
	uptime
site-e2e-actual:
	nproc; uptime
	flock /var/tmp/rtx4090.lock bash web/scripts/site-command.sh node ../../scripts/site-browser-run.mjs actual
	uptime

# T2 Airbyte OSS via abctl v0.30.4 (checksum-verified GitHub release in .tools/abctl).
# abctl names its kind cluster airbyte-abctl and keeps state in ~/.airbyte; it has no
# option to change either. Telemetry off; low-resource mode for the 16 GB memory share.
ABCTL := $(ROOT)/.tools/abctl/bin/abctl-v0.30.4-linux-amd64/abctl
AIRBYTE_PORT ?= 18000
AIRBYTE_EVIDENCE ?= $(ROOT)/docs/evidence/task02-airbyte
airbyte-up: export DO_NOT_TRACK := 1
airbyte-up:
	mkdir -p $(AIRBYTE_EVIDENCE)
	nproc; uptime
	$(ABCTL) local install --verbose --chart-version 1.8.5 --values ingestion/deploy/airbyte-values.yaml \
		--host localhost --low-resource-mode --no-browser --port $(AIRBYTE_PORT)
airbyte-status: export DO_NOT_TRACK := 1
airbyte-status:
	$(ABCTL) local status
airbyte-down: export DO_NOT_TRACK := 1
airbyte-down:
	$(ABCTL) local uninstall

lint-fix:
	$(PYTHON) -m ruff check --fix .

check:
	$(PYTHON) -m ruff check .
	npm run --silent --prefix web/apps/site typecheck
	npm run --silent --prefix web/packages/ui typecheck

# User-authorized project cleanup. The script itself defaults to a dry run.
azure-reap:
	./scripts/azure_reaper.sh --apply

# Deletes only rg-sponsoratlas-lab after checking its project/owner identity.
azure-down:
	./scripts/azure_reaper.sh --down --apply
