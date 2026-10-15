"""Flask API for the GroundGraph bounded O'Hare replay."""

from __future__ import annotations

import os
import threading
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from engine import GraphNotReadyError, GroundGraphEngine


def create_app(engine: GroundGraphEngine | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["JSON_SORT_KEYS"] = False
    holder: dict[str, GroundGraphEngine | None] = {"engine": engine}
    init_lock = threading.Lock()

    def unavailable_payload(
        exc: Exception, *, code: str = "GRAPH_UNAVAILABLE"
    ) -> dict[str, Any]:
        active_engine = holder["engine"]
        runtime_mode = (
            active_engine.backend.mode
            if active_engine is not None
            else os.getenv("FALKORDB_MODE", "server")
        )
        return {
            "ok": False,
            "status": "unavailable",
            "code": code,
            "runtime_mode": runtime_mode,
            "error": str(exc),
            "detail": (
                "FalkorDB is unavailable; operational reads, replanning, and "
                "mutations are disabled until the graph server is restored."
            ),
        }

    def get_engine() -> GroundGraphEngine:
        if holder["engine"] is None:
            with init_lock:
                if holder["engine"] is None:
                    holder["engine"] = GroundGraphEngine()
        return holder["engine"]

    @app.errorhandler(RedisConnectionError)
    @app.errorhandler(RedisTimeoutError)
    def graph_backend_unavailable(exc: RedisConnectionError | RedisTimeoutError):
        """Return one fail-closed contract for every graph-backed API route."""

        return jsonify(unavailable_payload(exc)), 503

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        try:
            return jsonify(get_engine().health())
        except GraphNotReadyError as exc:
            return jsonify(unavailable_payload(exc, code="GRAPH_NOT_READY")), 503
        except Exception as exc:
            return jsonify(unavailable_payload(exc)), 503

    @app.get("/api/snapshot")
    def snapshot():
        return jsonify(get_engine().snapshot())

    @app.get("/api/replanning")
    def replanning():
        view = get_engine().snapshot()
        return jsonify(
            {
                "ok": True,
                "clock": view["clock"],
                "state_version": view["state_version"],
                "agents": view["agents"],
                "handoffs": view["handoffs"],
                "memory": view["memory"],
                "current_plan": view["current_plan"],
                "replanning": view["replanning"],
            }
        )

    @app.route("/api/company-brain", methods=["GET", "POST"])
    def company_brain():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        question = str(body.get("question", request.args.get("question", "")))
        return jsonify(get_engine().company_brain(question))

    @app.post("/api/replan/tick")
    def tick():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            return jsonify(
                get_engine().tick(
                    minutes=int(body.get("minutes", 1)),
                    to_minute=(
                        int(body["to_minute"])
                        if body.get("to_minute") is not None
                        else None
                    ),
                )
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.post("/api/analyze")
    def analyze():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            return jsonify(
                get_engine().analyze(str(body.get("trigger", "MANUAL_REPLAN")))
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.post("/api/apply")
    def apply_plan():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            return jsonify(
                get_engine().apply(
                    str(body.get("plan_id", "")),
                    int(body.get("state_version", body.get("expected_version", -1))),
                )
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.post("/api/reset")
    def reset():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        return jsonify(
            get_engine().reset(preserve_memory=bool(body.get("preserve_memory", True)))
        )

    @app.post("/api/autonomy")
    def autonomy():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        if "enabled" not in body or not isinstance(body.get("enabled"), bool):
            return jsonify({"ok": False, "error": "enabled must be a boolean"}), 400
        try:
            return jsonify(get_engine().set_autonomy(bool(body["enabled"])))
        except (TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.post("/api/policies/supersede")
    def supersede_policy():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            return jsonify(
                get_engine().supersede_policy(
                    str(body.get("policy_id", "")),
                    replacement=(
                        body.get("replacement")
                        if isinstance(body.get("replacement"), dict)
                        else None
                    ),
                )
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    app.extensions["groundgraph_engine"] = holder
    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "5050")),
        debug=False,
        use_reloader=False,
        threaded=True,
    )
