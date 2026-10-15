"""Flask API for the GroundGraph bounded O'Hare replay."""

from __future__ import annotations

import os
import threading
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from engine import GroundGraphEngine


def create_app(engine: GroundGraphEngine | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["JSON_SORT_KEYS"] = False
    holder: dict[str, GroundGraphEngine | None] = {"engine": engine}
    init_lock = threading.Lock()

    def get_engine() -> GroundGraphEngine:
        if holder["engine"] is None:
            with init_lock:
                if holder["engine"] is None:
                    holder["engine"] = GroundGraphEngine()
        return holder["engine"]

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        try:
            return jsonify(get_engine().health())
        except Exception as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "status": "unavailable",
                        "runtime_mode": os.getenv("FALKORDB_MODE", "server"),
                        "error": str(exc),
                        "detail": "Start the local FalkorDB server or explicitly select FALKORDB_MODE=lite.",
                    }
                ),
                503,
            )

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
