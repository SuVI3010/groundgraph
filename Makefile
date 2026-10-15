PYTHON ?= $(shell if command -v python3.12 >/dev/null 2>&1; then printf python3.12; else printf python3; fi)
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

.PHONY: setup start start-lite infra pull-model run verify audit test logs down clean

setup:
	@test -f .env || cp .env.example .env
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r requirements.txt

start:
	./scripts/start_local.sh server

start-lite:
	./scripts/start_local.sh lite

infra:
	./scripts/start_falkordb.sh

pull-model:
	ollama pull $${OLLAMA_MODEL:-qwen3:4b-instruct}

run:
	@test -x $(PY) || (echo "Run 'make setup' first" && exit 1)
	set -a; . ./.env 2>/dev/null || true; set +a; $(PY) app.py

verify:
	@test -x $(PY) || (echo "Run 'make setup' first" && exit 1)
	set -a; . ./.env 2>/dev/null || true; set +a; $(PY) scripts/verify_stack.py

audit:
	$(PYTHON) scripts/audit_package.py

test:
	@test -x $(PY) || (echo "Run 'make setup' first" && exit 1)
	$(PY) -m pytest -q

logs:
	podman logs -f --tail=100 groundgraph-falkordb

down:
	-podman stop groundgraph-falkordb

clean:
	-podman rm -f groundgraph-falkordb
	@echo "The persistent groundgraph-falkordb-data volume was retained."

