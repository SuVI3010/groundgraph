"""Flask API for GroundGraph's bounded airport replay scenarios."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, jsonify, request, send_from_directory
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from backtest import DEFAULT_BACKTEST_ROOT, HistoricalBacktestRuntime, discover_backtests
from decision_story import build_decision_story
from engine import GraphNotReadyError, GroundGraphEngine


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = BASE_DIR / "data" / "scenario-registry.json"


def load_scenario_registry(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the allowlisted local replay catalog.

    The registry may select only checked-in files under this repository. Graph
    names are validated before they can reach FalkorDB. This keeps scenario
    switching a bounded product feature instead of an arbitrary file/graph API.
    """

    source = Path(path or DEFAULT_REGISTRY_PATH).resolve()
    if not source.is_file():
        default_path = (BASE_DIR / "data" / "scenario.json").resolve()
        return {
            "default_scenario_id": "ORD-2024-01-12-WINTER-AM",
            "scenarios": [
                {
                    "id": "ORD-2024-01-12-WINTER-AM",
                    "airport": "ORD",
                    "airport_name": "Chicago O'Hare International Airport",
                    "label": "ORD · Snow + rotation recovery",
                    "data_path": "data/scenario.json",
                    "resolved_data_path": str(default_path),
                    "graph_name": "groundgraph",
                    "sha256": hashlib.sha256(default_path.read_bytes()).hexdigest(),
                    "summary": "Bounded O'Hare winter-operations replay.",
                }
            ],
        }
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("scenarios"), list):
        raise ValueError("Scenario registry must contain a scenarios list")
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_graphs: set[str] = set()
    for raw in payload["scenarios"]:
        if not isinstance(raw, dict):
            raise ValueError("Each scenario registry entry must be an object")
        entry = dict(raw)
        scenario_id = str(entry.get("id", "")).strip()
        graph_name = str(entry.get("graph_name", "")).strip()
        relative_path = str(entry.get("data_path", "")).strip()
        if not scenario_id or scenario_id in seen_ids:
            raise ValueError(f"Scenario id is missing or duplicated: {scenario_id!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", graph_name):
            raise ValueError(f"Unsafe scenario graph name: {graph_name!r}")
        if graph_name in seen_graphs:
            raise ValueError(f"Scenario graph name is duplicated: {graph_name!r}")
        data_path = (BASE_DIR / relative_path).resolve()
        try:
            data_path.relative_to(BASE_DIR)
        except ValueError as exc:
            raise ValueError(f"Scenario file escapes the repository: {relative_path!r}") from exc
        if not data_path.is_file():
            raise ValueError(f"Scenario file is missing: {relative_path!r}")
        expected_sha = str(entry.get("sha256", "")).strip()
        actual_sha = hashlib.sha256(data_path.read_bytes()).hexdigest()
        if not expected_sha or actual_sha != expected_sha:
            raise ValueError(
                f"Scenario checksum mismatch for {relative_path!r}: "
                f"expected {expected_sha or '<missing>'}, got {actual_sha}"
            )
        scenario_payload = json.loads(data_path.read_text(encoding="utf-8"))
        scenario_metadata = dict(scenario_payload.get("scenario", {}))
        packaged_id = str(scenario_metadata.get("id", ""))
        if packaged_id != scenario_id:
            raise ValueError(
                f"Registry id {scenario_id!r} does not match packaged id {packaged_id!r}"
            )
        entry["id"] = scenario_id
        entry["graph_name"] = graph_name
        entry["data_path"] = relative_path
        entry["resolved_data_path"] = str(data_path)
        entry.setdefault("airport", scenario_metadata.get("airport"))
        entry.setdefault("airport_name", scenario_metadata.get("airport_name"))
        entry.setdefault("date", str(scenario_metadata.get("start_at", ""))[:10])
        entry.setdefault("timezone", scenario_metadata.get("timezone"))
        entries.append(entry)
        seen_ids.add(scenario_id)
        seen_graphs.add(graph_name)
    default_id = str(payload.get("default_scenario_id", ""))
    if default_id not in seen_ids:
        raise ValueError("Scenario registry default_scenario_id is not registered")
    return {"default_scenario_id": default_id, "scenarios": entries}


def create_app(
    engine: GroundGraphEngine | None = None,
    *,
    registry_path: Path | str | None = None,
    scenario_engine_factory: Any | None = None,
    backtest_root: Path | str | None = None,
    backtest_runtime_factory: Any | None = None,
) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config["JSON_SORT_KEYS"] = False
    registry = load_scenario_registry(registry_path)
    registry_by_id = {
        str(item["id"]): dict(item) for item in registry["scenarios"]
    }
    default_scenario_id = str(registry["default_scenario_id"])
    packaged_default_graph = str(
        registry_by_id[default_scenario_id]["graph_name"]
    )
    configured_graph_base = os.getenv("FALKORDB_GRAPH", packaged_default_graph)
    initial_id = (
        str(engine.scenario_id)
        if engine is not None
        else str(registry["default_scenario_id"])
    )
    holder: dict[str, Any] = {
        "engine": engine,
        "engines": ({initial_id: engine} if engine is not None else {}),
        "active_scenario_id": initial_id,
        "registry": registry,
    }
    selected_backtest_root = Path(
        backtest_root
        or os.getenv("GROUNDGRAPH_BACKTEST_ROOT", str(DEFAULT_BACKTEST_ROOT))
    ).resolve()
    backtest_holder: dict[str, Any] = {
        "active_backtest_id": None,
        "runtimes": {},
        "root": str(selected_backtest_root),
    }
    init_lock = threading.RLock()

    def public_entry(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in entry.items()
            if key not in {"resolved_data_path"}
        }

    def runtime_graph_name(scenario_id: str) -> str:
        if scenario_id == default_scenario_id:
            return configured_graph_base
        entry = registry_by_id[scenario_id]
        if configured_graph_base == packaged_default_graph:
            return str(entry["graph_name"])
        airport_suffix = re.sub(
            r"[^A-Za-z0-9_]", "_", str(entry.get("airport", scenario_id)).lower()
        )
        return f"{configured_graph_base}_{airport_suffix}"

    def build_engine(scenario_id: str) -> GroundGraphEngine:
        entry = registry_by_id[scenario_id]
        if scenario_engine_factory is not None:
            return scenario_engine_factory(dict(entry))
        mode = os.getenv("FALKORDB_MODE", "server").strip().lower()
        graph_name = runtime_graph_name(scenario_id)
        lite_path: Path | str | None = None
        if mode == "lite":
            if scenario_id == default_scenario_id:
                lite_path = os.getenv(
                    "FALKORDB_LITE_PATH", str(BASE_DIR / ".data" / "groundgraph.db")
                )
            else:
                lite_path = BASE_DIR / ".data" / f"{graph_name}.db"
        return GroundGraphEngine(
            data_path=entry["resolved_data_path"],
            mode=mode,
            graph_name=graph_name,
            lite_path=lite_path,
        )

    def unavailable_payload(
        exc: Exception,
        *,
        code: str = "GRAPH_UNAVAILABLE",
        runtime_mode: str | None = None,
    ) -> dict[str, Any]:
        active_engine = holder["engine"]
        selected_runtime_mode = runtime_mode or (
            active_engine.backend.mode
            if active_engine is not None
            else os.getenv("FALKORDB_MODE", "server")
        )
        return {
            "ok": False,
            "status": "unavailable",
            "code": code,
            "runtime_mode": selected_runtime_mode,
            "error": str(exc),
            "detail": (
                "FalkorDB is unavailable; operational reads, replanning, and "
                "mutations are disabled until the graph server is restored."
            ),
        }

    def backtest_unavailable_payload(exc: Exception) -> dict[str, Any]:
        active_id = backtest_holder.get("active_backtest_id")
        runtime = backtest_holder["runtimes"].get(str(active_id))
        mode = runtime.backend.mode if runtime is not None else os.getenv(
            "FALKORDB_MODE", "server"
        )
        return unavailable_payload(
            exc,
            code="GRAPH_NOT_READY",
            runtime_mode=mode,
        )

    def get_engine() -> GroundGraphEngine:
        active_id = str(holder["active_scenario_id"])
        active = holder["engines"].get(active_id)
        if active is None:
            with init_lock:
                active = holder["engines"].get(active_id)
                if active is None:
                    active = build_engine(active_id)
                    holder["engines"][active_id] = active
                holder["engine"] = active
        return active

    def activate_scenario(scenario_id: str) -> GroundGraphEngine:
        with init_lock:
            selected = holder["engines"].get(scenario_id)
            if selected is None:
                selected = build_engine(scenario_id)
                if str(selected.scenario_id) != scenario_id:
                    raise RuntimeError("Selected engine returned the wrong scenario")
                holder["engines"][scenario_id] = selected
            holder["active_scenario_id"] = scenario_id
            holder["engine"] = selected
            return selected

    def backtest_registry() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        entries = discover_backtests(selected_backtest_root)
        return entries, {str(item["id"]): item for item in entries}

    def public_backtest_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in entry.items() if key != "resolved_path"}

    def activate_backtest(backtest_id: str) -> HistoricalBacktestRuntime:
        entries, by_id = backtest_registry()
        del entries
        if backtest_id not in by_id:
            raise KeyError(backtest_id)
        with init_lock:
            runtime = backtest_holder["runtimes"].get(backtest_id)
            if runtime is None:
                entry = by_id[backtest_id]
                if backtest_runtime_factory is not None:
                    runtime = backtest_runtime_factory(dict(entry))
                else:
                    mode = os.getenv("FALKORDB_MODE", "server").strip().lower()
                    graph_name = "groundgraph_backtest_" + re.sub(
                        r"[^A-Za-z0-9_]", "_", backtest_id.lower()
                    ).strip("_")
                    lite_path: Path | str | None = None
                    if mode == "lite":
                        lite_path = BASE_DIR / ".data" / f"{graph_name}.db"
                    runtime = HistoricalBacktestRuntime(
                        entry["resolved_path"],
                        mode=mode,
                        graph_name=graph_name,
                        lite_path=lite_path,
                    )
                backtest_holder["runtimes"][backtest_id] = runtime
            backtest_holder["active_backtest_id"] = backtest_id
            return runtime

    def get_backtest() -> HistoricalBacktestRuntime:
        active_id = backtest_holder.get("active_backtest_id")
        if not active_id:
            raise RuntimeError("No backtest is selected; choose one returned by /api/backtests")
        runtime = backtest_holder["runtimes"].get(str(active_id))
        if runtime is None:
            raise RuntimeError("The selected backtest runtime is unavailable")
        return runtime

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

    @app.get("/api/decision-story")
    def decision_story():
        episode_id = str(request.args.get("episode_id", "")).strip() or None
        try:
            return jsonify(build_decision_story(get_engine(), episode_id))
        except KeyError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404

    @app.get("/api/scenarios")
    def scenarios():
        active_id = str(holder["active_scenario_id"])
        return jsonify(
            {
                "ok": True,
                "active_scenario_id": active_id,
                "default_scenario_id": registry["default_scenario_id"],
                "scenarios": [
                    {
                        **public_entry(entry),
                        "runtime_graph_name": runtime_graph_name(str(entry["id"])),
                        "active": str(entry["id"]) == active_id,
                        "loaded": str(entry["id"]) in holder["engines"],
                    }
                    for entry in registry["scenarios"]
                ],
            }
        )

    @app.get("/api/airports")
    def airports():
        active_id = str(holder["active_scenario_id"])
        active_entry = registry_by_id.get(active_id, {})
        return jsonify(
            {
                "ok": True,
                "active_airport": active_entry.get("airport"),
                "airports": [
                    {
                        "code": entry.get("airport"),
                        "name": entry.get("airport_name"),
                        "label": entry.get("label"),
                        "date": entry.get("date"),
                        "timezone": entry.get("timezone"),
                        "summary": entry.get("summary"),
                        "truth_note": entry.get("modeled_boundary"),
                        "scenario_id": entry.get("id"),
                    }
                    for entry in registry["scenarios"]
                ],
            }
        )

    @app.get("/api/backtests")
    def backtests():
        entries, _by_id = backtest_registry()
        active_id = backtest_holder.get("active_backtest_id")
        return jsonify(
            {
                "ok": True,
                "active_backtest_id": active_id,
                "backtests": [
                    {
                        **public_backtest_entry(entry),
                        "active": str(entry["id"]) == str(active_id),
                        "loaded": str(entry["id"]) in backtest_holder["runtimes"],
                    }
                    for entry in entries
                ],
            }
        )

    @app.post("/api/backtests/select")
    def select_backtest():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        backtest_id = str(body.get("backtest_id", body.get("id", ""))).strip()
        _entries, by_id = backtest_registry()
        if backtest_id not in by_id:
            return jsonify(
                {
                    "ok": False,
                    "error": "Unknown backtest_id; choose one returned by /api/backtests",
                }
            ), 404
        try:
            # Selection, reset, ticking, and result reads share this lock so a
            # reset cannot close FalkorDB while another request is using it.
            with init_lock:
                if body.get("reset") is True:
                    prior = backtest_holder["runtimes"].pop(backtest_id, None)
                    if prior is not None:
                        prior.close()
                runtime = activate_backtest(backtest_id)
                return jsonify(
                    {
                        "ok": True,
                        "selected": public_backtest_entry(by_id[backtest_id]),
                        "snapshot": runtime.snapshot(),
                    }
                )
        except GraphNotReadyError as exc:
            return jsonify(backtest_unavailable_payload(exc)), 503
        except (FileNotFoundError, TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.get("/api/backtests/health")
    def backtest_health():
        try:
            with init_lock:
                return jsonify(get_backtest().health())
        except GraphNotReadyError as exc:
            return jsonify(backtest_unavailable_payload(exc)), 503
        except (FileNotFoundError, TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.post("/api/backtests/tick")
    def tick_backtest():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        try:
            with init_lock:
                return jsonify(
                    get_backtest().tick(
                        minutes=int(body.get("minutes", 60)),
                        hours=(
                            float(body["hours"])
                            if body.get("hours") is not None
                            else None
                        ),
                        to_minute=(
                            int(body["to_minute"])
                            if body.get("to_minute") is not None
                            else None
                        ),
                    )
                )
        except GraphNotReadyError as exc:
            return jsonify(backtest_unavailable_payload(exc)), 503
        except (FileNotFoundError, TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.get("/api/backtests/results")
    def backtest_results():
        try:
            with init_lock:
                return jsonify(get_backtest().results())
        except GraphNotReadyError as exc:
            return jsonify(backtest_unavailable_payload(exc)), 503
        except (FileNotFoundError, TypeError, ValueError, RuntimeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409

    @app.post("/api/scenarios/select")
    def select_scenario():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        scenario_id = str(body.get("scenario_id", "")).strip()
        if scenario_id not in registry_by_id:
            return jsonify(
                {
                    "ok": False,
                    "error": "Unknown scenario_id; choose one returned by /api/scenarios",
                }
            ), 404
        selected = activate_scenario(scenario_id)
        return jsonify(
            {
                "ok": True,
                "selected": {
                    **public_entry(registry_by_id[scenario_id]),
                    "runtime_graph_name": runtime_graph_name(scenario_id),
                },
                "snapshot": selected.snapshot(),
            }
        )

    @app.post("/api/airports/select")
    def select_airport():
        body: dict[str, Any] = request.get_json(silent=True) or {}
        airport = str(body.get("airport", "")).strip().upper()
        match = next(
            (
                entry
                for entry in registry["scenarios"]
                if str(entry.get("airport", "")).upper() == airport
            ),
            None,
        )
        if match is None:
            return jsonify(
                {
                    "ok": False,
                    "error": "Unknown airport; choose one returned by /api/airports",
                }
            ), 404
        selected = activate_scenario(str(match["id"]))
        return jsonify(selected.snapshot())

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
    app.extensions["groundgraph_backtests"] = backtest_holder
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
