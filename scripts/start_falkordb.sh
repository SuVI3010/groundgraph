#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if ! command -v podman >/dev/null 2>&1; then
  cat >&2 <<'EOF'
Podman is required for the official FalkorDB server path.
On macOS, install it once with:
  brew install podman
On other hosts, follow Podman's installation guide:
  https://podman.io/docs/installation
Then run `make start` again. GroundGraph supports the Podman path only.
EOF
  exit 1
fi

if ! podman info >/dev/null 2>&1; then
  if ! podman machine list --format '{{.Name}}' 2>/dev/null | grep -q .; then
    echo "Creating the local Podman machine..."
    echo "Using Podman's default machine provider for this host."
    if ! podman machine init --cpus 2 --memory 4096 --disk-size 20; then
      podman machine list --format '{{.Name}}' 2>/dev/null | grep -q . || exit 1
      echo "A Podman machine was created concurrently; continuing."
    fi
  fi
  echo "Starting the local Podman machine..."
  START_LOG="$(mktemp)"
  if ! podman machine start >"$START_LOG" 2>&1; then
    if ! podman info >/dev/null 2>&1; then
      cat "$START_LOG" >&2
      echo "Podman machine failed to start. Diagnostic state:" >&2
      podman machine list >&2 || true
      rm -f "$START_LOG"
      exit 1
    fi
  else
    cat "$START_LOG"
  fi
  rm -f "$START_LOG"
fi

for _ in {1..20}; do
  if podman info >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
if ! podman info >/dev/null 2>&1; then
  echo "Podman machine started but its API did not become ready." >&2
  podman machine list >&2 || true
  exit 1
fi

CONTAINER_NAME="groundgraph-falkordb"
IMAGE="${FALKORDB_IMAGE:-docker.io/falkordb/falkordb:6.0.0}"
DB_PORT="${FALKORDB_PORT:-6379}"
BROWSER_PORT="${FALKORDB_BROWSER_PORT:-3000}"

if podman container exists "$CONTAINER_NAME"; then
  if [[ "$(podman inspect -f '{{.State.Running}}' "$CONTAINER_NAME")" != "true" ]]; then
    echo "Starting the existing FalkorDB container..."
    podman start "$CONTAINER_NAME" >/dev/null
  fi
else
  echo "Creating official FalkorDB container from $IMAGE..."
  podman run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "127.0.0.1:${DB_PORT}:6379" \
    -p "127.0.0.1:${BROWSER_PORT}:3000" \
    -e REDIS_ARGS="--appendonly yes --appendfsync everysec" \
    -e AUTH_SECRET="groundgraph-local-browser-session" \
    -v groundgraph-falkordb-data:/var/lib/falkordb/data \
    "$IMAGE" >/dev/null
fi

echo "Waiting for FalkorDB on 127.0.0.1:${DB_PORT}..."
for _ in {1..60}; do
  if podman exec "$CONTAINER_NAME" redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q PONG; then
    echo "Official FalkorDB is healthy. Browser: http://127.0.0.1:${BROWSER_PORT}"
    exit 0
  fi
  sleep 1
done

echo "FalkorDB did not become healthy. Inspect with: podman logs $CONTAINER_NAME" >&2
exit 1
