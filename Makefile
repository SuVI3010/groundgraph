PYTHON ?= $(shell if command -v python3.12 >/dev/null 2>&1; then printf python3.12; else printf python3; fi)
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

.PHONY: setup setup-lite setup-dev start start-lite infra pull-model run verify demo-check audit test logs down clean

setup:
	@test -f .env || cp .env.example .env
	@$(PYTHON) -c 'import sys; sys.exit("GroundGraph requires Python 3.12 or newer") if sys.version_info < (3, 12) else None'
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r requirements.txt

setup-lite: setup
	$(PIP) install -r requirements-lite.txt

setup-dev: setup
	$(PIP) install -r requirements-dev.txt

start:
	./scripts/start_local.sh server

start-lite:
	@test -x $(PY) || $(MAKE) setup-lite
	@$(PY) -c 'import redislite' >/dev/null 2>&1 || $(PIP) install -r requirements-lite.txt
	./scripts/start_local.sh lite

infra:
	./scripts/start_falkordb.sh

pull-model:
	@test -f .env || cp .env.example .env
	set -a; . ./.env; set +a; ollama pull "$${OLLAMA_MODEL:-qwen3:4b-instruct}"

run:
	@test -x $(PY) || (echo "Run 'make setup' first" && exit 1)
	set -a; . ./.env 2>/dev/null || true; set +a; $(PY) app.py

verify:
	@test -x $(PY) || (echo "Run 'make setup' first" && exit 1)
	set -a; . ./.env 2>/dev/null || true; set +a; $(PY) scripts/verify_stack.py

demo-check:
	@test -x $(PY) || (echo "Run 'make setup' first" && exit 1)
	set -a; . ./.env 2>/dev/null || true; set +a; $(PY) scripts/demo_check.py

audit:
	$(PYTHON) scripts/audit_package.py

test:
	@test -x $(PY) || (echo "Run 'make setup' first" && exit 1)
	@$(PY) -c 'import pytest, redislite' >/dev/null 2>&1 || $(PIP) install -r requirements-dev.txt
	$(PY) -m pytest -q

logs:
	podman logs -f --tail=100 groundgraph-falkordb

down:
	-podman stop groundgraph-falkordb

clean:
	-podman rm -f groundgraph-falkordb
	@echo "The persistent groundgraph-falkordb-data volume was retained."

