#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${GROUNDGRAPH_NODE:-}" && -x "${GROUNDGRAPH_NODE}" ]]; then
  node_bin="${GROUNDGRAPH_NODE}"
elif command -v node >/dev/null 2>&1; then
  node_bin="$(command -v node)"
elif [[ -x "${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node" ]]; then
  node_bin="${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
else
  cat >&2 <<'EOF'
Node.js 20 or newer is required.
Install Playwright without its bundled browser, then reuse local Chrome:
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install --no-save playwright@1.62.1
EOF
  exit 1
fi

exec "${node_bin}" "${repo_root}/scripts/record_demo.mjs" "$@"
