#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-server}"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  echo "Preparing the local Python environment..."
  make setup
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

if [[ "$MODE" == "lite" ]] && ! .venv/bin/python -c 'import redislite' >/dev/null 2>&1; then
  echo "Installing the optional FalkorDBLite fallback..."
  .venv/bin/pip install -r requirements-lite.txt
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ "${GROUNDGRAPH_ENABLE_LLM:-1}" =~ ^(1|true|TRUE|yes|YES)$ ]]; then
  if command -v ollama >/dev/null 2>&1 && ollama list 2>/dev/null | awk 'NR > 1 {print $1}' | grep -Fxq "${OLLAMA_MODEL:-qwen3:4b-instruct}"; then
    echo "Local coordinator ready: ${OLLAMA_MODEL:-qwen3:4b-instruct}"
  else
    echo "Warning: local model is unavailable; GroundGraph will label and use its deterministic fallback." >&2
    echo "To enable the model, start Ollama and run: make pull-model" >&2
  fi
fi

if [[ "$MODE" == "lite" ]]; then
  export FALKORDB_MODE=lite
  echo "Starting GroundGraph with the FalkorDBLite fallback."
  exec .venv/bin/python app.py
fi

if [[ "$MODE" != "server" ]]; then
  echo "Usage: $0 [server|lite]" >&2
  exit 2
fi

./scripts/start_falkordb.sh

export FALKORDB_MODE=server
export FALKORDB_HOST=127.0.0.1
export FALKORDB_PORT="${FALKORDB_PORT:-6379}"
echo "Starting GroundGraph at http://127.0.0.1:${PORT:-5050}"
exec .venv/bin/python app.py
