"""Read-only smoke checks for the local GroundGraph stack."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _module_names(raw_modules: object) -> set[str]:
    names: set[str] = set()
    if not isinstance(raw_modules, (list, tuple)):
        return names
    for raw_module in raw_modules:
        if isinstance(raw_module, dict):
            for key, value in raw_module.items():
                if _text(key).lower() == "name":
                    names.add(_text(value).lower())
            continue
        if not isinstance(raw_module, (list, tuple)):
            continue
        values = list(raw_module)
        for index in range(0, len(values) - 1, 2):
            if _text(values[index]).lower() == "name":
                names.add(_text(values[index + 1]).lower())
    return names


def _configured_app_url() -> str:
    explicit = os.getenv("GROUNDGRAPH_APP_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"http://127.0.0.1:{os.getenv('PORT', '5050')}"


def _validate_health(
    health: dict,
    *,
    mode: str,
    graph_name: str,
    expected_endpoint: str,
) -> None:
    if not health.get("ok"):
        raise RuntimeError(f"health response did not report ok: {health}")
    reported_mode = health.get("runtime_mode")
    if reported_mode != mode:
        raise RuntimeError(
            f"API reports runtime_mode={reported_mode!r}, expected {mode!r}"
        )
    reported_graph = health.get("graph")
    if reported_graph != graph_name:
        raise RuntimeError(
            f"API reports graph={reported_graph!r}, expected {graph_name!r}"
        )
    reported_endpoint = health.get("endpoint")
    if reported_endpoint != expected_endpoint:
        raise RuntimeError(
            f"API reports endpoint={reported_endpoint!r}, expected {expected_endpoint!r}"
        )


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    mode = os.getenv("FALKORDB_MODE", "server").lower()
    host = os.getenv("FALKORDB_HOST", "127.0.0.1")
    port = int(os.getenv("FALKORDB_PORT", "6379"))
    password = os.getenv("FALKORDB_PASSWORD") or None
    graph_name = os.getenv("FALKORDB_GRAPH", "groundgraph")
    app_url = _configured_app_url()
    lite_path = os.getenv("FALKORDB_LITE_PATH", ".data/groundgraph.db")
    expected_endpoint = f"{host}:{port}" if mode == "server" else lite_path

    try:
        health = _http_json(f"{app_url.rstrip('/')}/api/health")
        _validate_health(
            health,
            mode=mode,
            graph_name=graph_name,
            expected_endpoint=expected_endpoint,
        )
        print(f"PASS GroundGraph API {app_url}/api/health")
    except (OSError, ValueError, urllib.error.URLError, RuntimeError) as exc:
        print(f"FAIL GroundGraph API: {exc}", file=sys.stderr)
        return 1

    if mode == "server":
        try:
            from falkordb import FalkorDB

            database = FalkorDB(
                host=host,
                port=port,
                password=password,
                socket_connect_timeout=3,
                socket_timeout=5,
            )
            pong = database.connection.ping()
            modules = _module_names(database.connection.execute_command("MODULE", "LIST"))
            graph = database.select_graph(graph_name)
            count_result = graph.query("MATCH (n) RETURN count(n)")
            node_count = int(count_result.result_set[0][0])
            if not pong or node_count < 1:
                raise RuntimeError("server is reachable but the GroundGraph graph is empty")
            if "graph" not in modules:
                raise RuntimeError("server answered but the FalkorDB graph module is not loaded")
            print(
                f"PASS FalkorDB server {host}:{port}/{graph_name} contains "
                f"{node_count} nodes and the graph module"
            )
        except Exception as exc:  # smoke-check output should remain concise
            print(f"FAIL FalkorDB server: {exc}", file=sys.stderr)
            return 1
    elif mode == "lite":
        if not os.path.exists(lite_path):
            print(f"FAIL FalkorDBLite data path does not exist: {lite_path}", file=sys.stderr)
            return 1
        print(f"PASS FalkorDBLite data path {lite_path}")
    else:
        print(f"FAIL unsupported FALKORDB_MODE={mode!r}", file=sys.stderr)
        return 1

    if os.getenv("GROUNDGRAPH_ENABLE_LLM", "0").lower() in {"1", "true", "yes"}:
        ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        try:
            tags = _http_json(f"{ollama_url.rstrip('/')}/api/tags")
            configured = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
            available = {item.get("name", "") for item in tags.get("models", [])}
            if not any(name == configured or name.startswith(f"{configured}:") for name in available):
                raise RuntimeError(f"configured model {configured!r} is not installed")
            print(f"PASS Ollama model {configured}")
        except (OSError, ValueError, urllib.error.URLError, RuntimeError) as exc:
            print(f"FAIL Ollama: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
