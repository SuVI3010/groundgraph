from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from scripts import demo_check, verify_stack


def test_verify_uses_port_and_requires_the_same_graph_and_endpoint(
    tmp_path: Path, monkeypatch, capsys
):
    lite_path = tmp_path / "verify.db"
    lite_path.write_bytes(b"present")
    observed: list[str] = []
    monkeypatch.delenv("GROUNDGRAPH_APP_URL", raising=False)
    monkeypatch.setenv("PORT", "5999")
    monkeypatch.setenv("FALKORDB_MODE", "lite")
    monkeypatch.setenv("FALKORDB_GRAPH", "verify_graph")
    monkeypatch.setenv("FALKORDB_LITE_PATH", str(lite_path))
    monkeypatch.setenv("GROUNDGRAPH_ENABLE_LLM", "0")

    def fake_http_json(url: str) -> dict:
        observed.append(url)
        return {
            "ok": True,
            "runtime_mode": "lite",
            "graph": "verify_graph",
            "endpoint": str(lite_path),
        }

    monkeypatch.setattr(verify_stack, "_http_json", fake_http_json)
    assert verify_stack.main() == 0
    assert observed == ["http://127.0.0.1:5999/api/health"]

    monkeypatch.setattr(
        verify_stack,
        "_http_json",
        lambda _url: {
            "ok": True,
            "runtime_mode": "lite",
            "graph": "another_graph",
            "endpoint": str(lite_path),
        },
    )
    assert verify_stack.main() == 1
    assert "expected 'verify_graph'" in capsys.readouterr().err


def test_verify_catches_unhealthy_runtime_error(monkeypatch, capsys):
    monkeypatch.setenv("FALKORDB_MODE", "lite")
    monkeypatch.setattr(verify_stack, "_http_json", lambda _url: {"ok": False})
    assert verify_stack.main() == 1
    assert "health response did not report ok" in capsys.readouterr().err


def test_graph_module_parser_accepts_string_and_byte_responses():
    assert verify_stack._module_names(
        [
            ["name", "vectorset", "ver", 1],
            [b"name", b"graph", b"ver", 60000],
        ]
    ) == {"vectorset", "graph"}


def test_demo_check_performs_live_structured_model_inference(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:4b-instruct")
    monkeypatch.setattr(
        demo_check,
        "_http_json",
        lambda _url: {
            "llm": {
                "enabled": True,
                "provider": "ollama",
                "model": "qwen3:4b-instruct",
            }
        },
    )
    monkeypatch.setattr(
        demo_check,
        "_post_json",
        lambda *_args: {"message": {"content": '{"status":"ready"}'}},
    )
    demo_check._check_live_model()


def test_judged_requirements_exclude_optional_lite_runtime():
    root = Path(__file__).resolve().parents[1]
    judged = (root / "requirements.txt").read_text(encoding="utf-8")
    optional = (root / "requirements-lite.txt").read_text(encoding="utf-8")
    development = (root / "requirements-dev.txt").read_text(encoding="utf-8")

    assert "FalkorDBLite" not in judged
    assert "pytest" not in judged
    assert "FalkorDBLite==0.10.0" in optional
    assert "pytest==9.1.1" in development


def test_falkordb_launcher_replaces_mismatched_container(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    scripts = project / "scripts"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    fake_bin.mkdir()
    shutil.copy2(root / "scripts" / "start_falkordb.sh", scripts)
    (project / ".env").write_text(
        "FALKORDB_IMAGE=docker.io/falkordb/falkordb:6.0.0\n"
        "FALKORDB_PORT=6388\n"
        "FALKORDB_BROWSER_PORT=3008\n",
        encoding="utf-8",
    )
    state = tmp_path / "container-exists"
    state.touch()
    log = tmp_path / "podman.log"
    podman = fake_bin / "podman"
    podman.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$PODMAN_LOG"
case "${1:-}" in
  info) exit 0 ;;
  container)
    [[ "${2:-}" == "exists" && -f "$PODMAN_STATE" ]]
    ;;
  inspect)
    if [[ "$*" == *"Config.Image"* ]]; then
      printf '%s\\n' 'docker.io/library/redis:latest'
    else
      printf '%s\\n' 'true'
    fi
    ;;
  port)
    if [[ "${3:-}" == "6379/tcp" ]]; then
      printf '%s\\n' '127.0.0.1:9999'
    else
      printf '%s\\n' '127.0.0.1:9998'
    fi
    ;;
  rm)
    command rm "$PODMAN_STATE"
    ;;
  run)
    : > "$PODMAN_STATE"
    printf '%s\\n' 'new-container-id'
    ;;
  exec)
    if [[ "$*" == *" ping"* ]]; then
      printf '%s\\n' 'PONG'
    else
      printf '%s\\n' 'name' 'graph'
    fi
    ;;
  *) exit 1 ;;
esac
""",
        encoding="utf-8",
    )
    podman.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "PODMAN_LOG": str(log),
            "PODMAN_STATE": str(state),
        }
    )

    completed = subprocess.run(
        ["bash", str(scripts / "start_falkordb.sh")],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )
    calls = log.read_text(encoding="utf-8")
    assert "Replacing a mismatched groundgraph-falkordb container" in completed.stdout
    assert "rm -f groundgraph-falkordb" in calls
    assert "-p 127.0.0.1:6388:6379" in calls
    assert "-p 127.0.0.1:3008:3000" in calls
    assert "docker.io/falkordb/falkordb:6.0.0" in calls
    assert "Official FalkorDB is healthy" in completed.stdout


def test_pull_model_uses_env_model_name(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    fake_bin = tmp_path / "bin"
    project.mkdir()
    fake_bin.mkdir()
    shutil.copy2(root / "Makefile", project)
    (project / ".env").write_text(
        "OLLAMA_MODEL=groundgraph-test-model\n", encoding="utf-8"
    )
    log = tmp_path / "ollama.log"
    ollama = fake_bin / "ollama"
    ollama.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" > \"$OLLAMA_LOG\"\n",
        encoding="utf-8",
    )
    ollama.chmod(0o755)
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "OLLAMA_LOG": str(log),
        }
    )

    subprocess.run(
        ["make", "pull-model"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )
    assert log.read_text(encoding="utf-8").strip() == "pull groundgraph-test-model"
