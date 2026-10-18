"""Strict, read-only readiness check for the GroundGraph full-server demonstration."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

try:
    from .verify_stack import _configured_app_url, _http_json, main as verify_stack
except ImportError:  # Direct execution adds scripts/ rather than the repository root.
    from verify_stack import _configured_app_url, _http_json, main as verify_stack


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _check_official_container() -> None:
    expected_image = os.getenv(
        "FALKORDB_IMAGE", "docker.io/falkordb/falkordb:6.0.0"
    )
    if not expected_image.startswith("docker.io/falkordb/falkordb:"):
        raise RuntimeError(
            f"full-server demo requires the official FalkorDB image, got {expected_image!r}"
        )
    completed = subprocess.run(
        [
            "podman",
            "inspect",
            "--format",
            "{{.Config.Image}}",
            "groundgraph-falkordb",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    actual_image = completed.stdout.strip()
    if actual_image != expected_image:
        raise RuntimeError(
            f"container image is {actual_image!r}, expected {expected_image!r}"
        )
    print(f"PASS official FalkorDB container image {actual_image}")


def _check_live_model() -> None:
    health = _http_json(f"{_configured_app_url()}/api/health")
    llm = health.get("llm") or {}
    model = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
    if not llm.get("enabled"):
        raise RuntimeError("GroundGraph reports that local-model coordination is disabled")
    if llm.get("provider") != "ollama" or llm.get("model") != model:
        raise RuntimeError(
            "GroundGraph model configuration does not match the requested local Ollama model"
        )

    ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    timeout = max(5.0, float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "30")))
    response = _post_json(
        f"{ollama_url}/api/chat",
        {
            "model": model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "seed": 7, "num_predict": 32},
            "messages": [
                {"role": "system", "content": "Return one JSON object only."},
                {
                    "role": "user",
                    "content": 'Reply with exactly this JSON object: {"status":"ready"}',
                },
            ],
        },
        timeout,
    )
    content = (response.get("message") or {}).get("content", "")
    parsed = json.loads(content)
    if parsed != {"status": "ready"}:
        raise RuntimeError(f"unexpected structured model response: {parsed!r}")
    print(f"PASS live structured Ollama inference with {model}")


def main() -> int:
    if os.getenv("FALKORDB_MODE", "server").lower() != "server":
        print("FAIL full-server demo check requires FALKORDB_MODE=server", file=sys.stderr)
        return 1
    if verify_stack() != 0:
        return 1
    try:
        _check_official_container()
        _check_live_model()
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        urllib.error.URLError,
        ValueError,
    ) as exc:
        print(f"FAIL full-server demo readiness: {exc}", file=sys.stderr)
        return 1
    print("PASS GroundGraph full-server demo is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
