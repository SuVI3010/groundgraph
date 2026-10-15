"""GroundGraph's graph-native, continuously replanning airport replay engine.

The engine is intentionally bounded: it controls a six-flight historical replay,
not a real airport.  FalkorDB is the source of truth for operational state,
specialist handoffs, decisions, policy authorization, and episodic memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_PATH = BASE_DIR / "data" / "scenario.json"
DEFAULT_GRAPH = "groundgraph"
SPECIALISTS = (
    ("AGENT-WEATHER", "Weather agent", "Observed weather and trend changes"),
    ("AGENT-FLOW", "Flow agent", "Runway and departure flow"),
    ("AGENT-GATE", "Gate agent", "Gate compatibility and conflicts"),
    ("AGENT-TURN", "Turnaround agent", "Bags, servicing, and rotations"),
    ("AGENT-POLICY", "Policy agent", "Current authority and provenance"),
    ("AGENT-MEMORY", "Memory agent", "Prior episodes and outcomes"),
)
COORDINATOR = ("AGENT-COORD", "Recovery coordinator", "Safe plan selection")
ACTION_TYPES = (
    "RESEQUENCE_DEPARTURE",
    "REASSIGN_GATE",
    "RESEQUENCE_TASKS",
    "PROTECT_ROTATION",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _list_value(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return list(decoded) if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            return []
    return list(value) if isinstance(value, (list, tuple)) else []


def _normalize_plan_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Fill current attribution fields, including for plans stored by older builds."""

    payload.setdefault(
        "baseline_delay",
        int(payload.get("projected_delay", 0))
        + int(payload.get("recovered_minutes", 0)),
    )
    raw_influence = payload.get("memory_influence")
    if not isinstance(raw_influence, Mapping):
        return payload

    influence = dict(raw_influence)
    base_winner = influence.get("base_ranking_winner_id") or influence.get(
        "base_winner_id"
    )
    adjusted_winner = influence.get(
        "memory_adjusted_ranking_winner_id"
    ) or influence.get("selected_candidate_id")
    final_candidate = payload.get("candidate_id")
    final_source = payload.get("selected_by")
    ranking_changed = bool(
        base_winner and adjusted_winner and base_winner != adjusted_winner
    )
    ranking_summary = influence.get("ranking_summary")
    if not ranking_summary:
        ranking_summary = (
            f"Memory changed the deterministic ranking from {base_winner} to {adjusted_winner}."
            if ranking_changed
            else "Memory did not change the deterministic ranking winner."
            if influence.get("applied")
            else "No applicable prior outcome changed the deterministic ranking."
        )
    model_allowlist = {
        str(item.get("id"))
        for item in _list_value(payload.get("model_candidate_set"))
        if isinstance(item, Mapping) and item.get("id")
    }
    if not model_allowlist:
        model_allowlist = {
            str(item.get("id"))
            for item in _list_value(payload.get("safe_candidate_set"))
            if isinstance(item, Mapping) and item.get("id")
        }
    choice_summary = (
        f"The local model confirmed the context-bounded ranking leader: {final_candidate}."
        if final_source == "local_model" and final_candidate == adjusted_winner
        else f"The local model made the final allowlisted tradeoff choice: {final_candidate}."
        if final_source == "local_model"
        else f"The deterministic safe planner made the final choice: {final_candidate}."
        if final_candidate
        else "No final candidate was selected."
    )
    influence.update(
        {
            "base_winner_id": base_winner,
            "base_ranking_winner_id": base_winner,
            "memory_adjusted_ranking_winner_id": adjusted_winner,
            "changed_selection": ranking_changed,
            "ranking_changed": ranking_changed,
            "ranking_summary": ranking_summary,
            "selected_candidate_id": final_candidate,
            "final_candidate_id": final_candidate,
            "final_choice_source": final_source,
            "final_choice_matched_adjusted_ranking": bool(
                final_candidate and final_candidate == adjusted_winner
            ),
            "summary": f"{ranking_summary} {choice_summary}",
        }
    )
    payload["memory_influence"] = influence
    payload["selection_attribution"] = {
        "final_candidate_id": final_candidate,
        "selected_by": final_source,
        "candidate_was_allowlisted": bool(
            final_candidate and str(final_candidate) in model_allowlist
        ),
        "memory_adjusted_ranking_winner_id": adjusted_winner,
        "memory_changed_ranking": ranking_changed,
    }
    return payload


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _clock_minutes(value: Any, default: int = 0) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"(?:T|^)(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return default
    return int(match.group(1)) * 60 + int(match.group(2))


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Unsafe graph identifier: {value!r}")
    return value


def _default_scenario() -> dict[str, Any]:
    """Small public-data replay fixture used when no packaged file is present."""

    return {
        "scenario": {
            "id": "ORD-2024-01-12-AM",
            "name": "O'Hare winter recovery replay",
            "airport": "ORD",
            "airport_name": "Chicago O'Hare International Airport",
            "timezone": "America/Chicago",
            "date": "2024-01-12",
            "start_minute": 515,
            "end_minute": 720,
            "initial_clock": 515,
        },
        "resources": {
            "runways": [
                {"id": "RWY-27C", "label": "27C", "status": "OPEN", "capacity_per_hour": 28},
                {"id": "RWY-28R", "label": "28R", "status": "OPEN", "capacity_per_hour": 24},
            ],
            "gates": [
                {"id": "GATE-C18", "label": "C18", "terminal": "1", "status": "OPEN"},
                {"id": "GATE-C20", "label": "C20", "terminal": "1", "status": "OPEN"},
                {"id": "GATE-B14", "label": "B14", "terminal": "1", "status": "OPEN"},
            ],
        },
        "flights": [
            {"id": "UAL1414", "callsign": "UAL1414", "kind": "INBOUND", "scheduled_minute": 526, "gate": "GATE-C18", "runway": "RWY-27C", "rotation_id": "ROT-UA-1", "turn_minutes": 45},
            {"id": "UAL2672", "callsign": "UAL2672", "kind": "OUTBOUND", "scheduled_minute": 570, "gate": "GATE-C18", "runway": "RWY-27C", "rotation_id": "ROT-UA-1", "turn_minutes": 45},
            {"id": "AAL1783", "callsign": "AAL1783", "kind": "INBOUND", "scheduled_minute": 543, "gate": "GATE-C20", "runway": "RWY-28R", "rotation_id": "ROT-AA-2", "turn_minutes": 42},
            {"id": "AAL1108", "callsign": "AAL1108", "kind": "OUTBOUND", "scheduled_minute": 590, "gate": "GATE-C20", "runway": "RWY-28R", "rotation_id": "ROT-AA-2", "turn_minutes": 42},
            {"id": "SKW5331", "callsign": "SKW5331", "kind": "INBOUND", "scheduled_minute": 558, "gate": "GATE-B14", "runway": "RWY-27C", "rotation_id": "ROT-SK-3", "turn_minutes": 38},
            {"id": "SKW5426", "callsign": "SKW5426", "kind": "OUTBOUND", "scheduled_minute": 605, "gate": "GATE-B14", "runway": "RWY-27C", "rotation_id": "ROT-SK-3", "turn_minutes": 38},
        ],
        "events": [
            {
                "id": "EVT-BAG-0836",
                "at_minute": 516,
                "type": "BAGGAGE_DELAY",
                "severity": "HIGH",
                "flight_id": "UAL2672",
                "resource_id": "GATE-C18",
                "delay_minutes": 12,
                "summary": "Modeled late bag reconciliation extends the UAL2672 turn.",
                "source_kind": "MODELED",
            },
            {
                "id": "EVT-FLOW-0912",
                "at_minute": 552,
                "type": "RUNWAY_CAPACITY",
                "severity": "HIGH",
                "flight_id": "AAL1108",
                "resource_id": "RWY-27C",
                "delay_minutes": 15,
                "summary": "Modeled runway acceptance-rate drop creates a second decision checkpoint.",
                "source_kind": "MODELED",
            },
        ],
        "policies": [
            {"id": "POL-FLOW-2.1", "title": "Flow resequencing authority", "version": "2.1", "status": "CURRENT", "current": True, "owner": "Airport Flow Desk", "source": "Synthetic OCC handbook §4.2", "action_types": ["SEQUENCE_DEPARTURES"], "rule": "The coordinator may resequence modeled departures inside the approved capacity envelope."},
            {"id": "POL-GATE-1.4", "title": "Compatible gate reassignment", "version": "1.4", "status": "CURRENT", "current": True, "owner": "Gate Control", "source": "Synthetic station SOP §7.1", "action_types": ["ASSIGN_GATE"], "rule": "Gate Control may move a flight to a compatible open gate."},
            {"id": "POL-TURN-3.0", "title": "Turnaround recovery playbook", "version": "3.0", "status": "CURRENT", "current": True, "owner": "Ramp Operations", "source": "Synthetic turnaround bulletin §3", "action_types": ["ACCELERATE_TURNAROUND"], "rule": "A recovery team may be assigned while all modeled dependencies remain satisfied."},
        ],
        "provenance": [
            {"id": "SRC-BTS", "label": "US DOT BTS on-time performance", "kind": "OBSERVED", "url": "https://www.transtats.bts.gov/"},
            {"id": "SRC-NOAA", "label": "NOAA/NCEI weather observation", "kind": "OBSERVED", "url": "https://www.ncei.noaa.gov/"},
            {"id": "SRC-MODELED", "label": "GroundGraph modeled operations pack", "kind": "MODELED", "url": ""},
        ],
    }


def load_scenario(path: Path | str | None = None) -> dict[str, Any]:
    source = Path(path or os.getenv("GROUNDGRAPH_DATA", DEFAULT_DATA_PATH))
    if source.exists():
        with source.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Scenario data must be a JSON object")
        return payload
    return _default_scenario()


class GraphBackend:
    """Tiny compatibility layer for official FalkorDB and FalkorDBLite."""

    def __init__(
        self,
        *,
        mode: str | None = None,
        graph_name: str | None = None,
        lite_path: Path | str | None = None,
    ) -> None:
        selected = (mode or os.getenv("FALKORDB_MODE", "server")).strip().lower()
        if selected not in {"server", "lite"}:
            raise ValueError("FALKORDB_MODE must be 'server' or 'lite'")
        self.mode = selected
        self.graph_name = graph_name or os.getenv("FALKORDB_GRAPH", DEFAULT_GRAPH)
        _safe_identifier(self.graph_name)
        if selected == "lite":
            from redislite import FalkorDB

            path = Path(
                lite_path
                or os.getenv("FALKORDB_LITE_PATH", BASE_DIR / ".data" / "groundgraph.db")
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            self.client = FalkorDB(str(path))
            self.endpoint = str(path)
        else:
            from falkordb import FalkorDB

            host = os.getenv("FALKORDB_HOST", "127.0.0.1")
            port = int(os.getenv("FALKORDB_PORT", "6379"))
            password = os.getenv("FALKORDB_PASSWORD") or None
            self.client = FalkorDB(host=host, port=port, password=password)
            self.endpoint = f"{host}:{port}"
        self.graph = self.client.select_graph(self.graph_name)

    def query(self, cypher: str, params: Mapping[str, Any] | None = None):
        return self.graph.query(cypher, params=dict(params or {}))

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


class GroundGraphEngine:
    """Bounded replay, agent coordination, safe planning, and graph memory."""

    def __init__(
        self,
        *,
        data_path: Path | str | None = None,
        backend: GraphBackend | None = None,
        mode: str | None = None,
        lite_path: Path | str | None = None,
        enable_llm: bool | None = None,
    ) -> None:
        self.data = load_scenario(data_path)
        self.scenario_data = dict(self.data.get("scenario", {}))
        self.scenario_id = str(self.scenario_data.get("id", "ORD-REPLAY"))
        self.base_clock_minute = _clock_minutes(
            self.scenario_data.get("start_at"),
            _clock_minutes(self.scenario_data.get("initial_clock"), 8 * 60),
        )
        self.scenario_date = str(
            self.scenario_data.get("start_at", self.scenario_data.get("date", "2024-01-12"))
        )[:10]
        self.planning_interval = max(
            1, int(self.scenario_data.get("planning_interval_minutes", 5))
        )
        self.execution_freeze = max(
            0, int(self.scenario_data.get("execution_freeze_minutes", 10))
        )
        self.minimum_benefit = max(
            1, int(self.scenario_data.get("minimum_benefit_minutes", 4))
        )
        self._lock = threading.RLock()
        self.backend = backend or GraphBackend(mode=mode, lite_path=lite_path)
        if enable_llm is None:
            enable_llm = os.getenv("GROUNDGRAPH_ENABLE_LLM", "0") in {
                "1",
                "true",
                "True",
            }
        self.enable_llm = bool(enable_llm)
        self.auto_apply = os.getenv("GROUNDGRAPH_AUTO_APPLY", "1") not in {
            "0",
            "false",
            "False",
        }
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        self.ollama_timeout = max(
            1.0, float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "30"))
        )
        self.dataset_hash = _hash(self.data)
        self.ensure_seeded()

    def close(self) -> None:
        self.backend.close()

    @property
    def graph(self):
        return self.backend.graph

    @staticmethod
    def _scalar(value: Any) -> Any:
        if isinstance(value, (list, tuple, dict)):
            return _json(value)
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def _create_node(self, label: str, properties: Mapping[str, Any]) -> None:
        label = _safe_identifier(label)
        fields: list[str] = []
        params: dict[str, Any] = {}
        for index, (raw_key, raw_value) in enumerate(properties.items()):
            if raw_value is None:
                continue
            key = _safe_identifier(str(raw_key))
            name = f"v{index}"
            fields.append(f"{key}: ${name}")
            params[name] = self._scalar(raw_value)
        self.backend.query(
            f"CREATE (node:{label} {{{', '.join(fields)}}})", params
        )

    def _create_edge(
        self,
        source: str,
        relationship: str,
        target: str,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        relationship = _safe_identifier(relationship)
        fields: list[str] = []
        params: dict[str, Any] = {"source": source, "target": target}
        for index, (raw_key, raw_value) in enumerate((properties or {}).items()):
            key = _safe_identifier(str(raw_key))
            name = f"e{index}"
            fields.append(f"{key}: ${name}")
            params[name] = self._scalar(raw_value)
        prop_clause = f" {{{', '.join(fields)}}}" if fields else ""
        self.backend.query(
            "MATCH (source {id: $source}), (target {id: $target}) "
            f"CREATE (source)-[:{relationship}{prop_clause}]->(target)",
            params,
        )

    def ensure_seeded(self) -> None:
        with self._lock:
            result = self.backend.query(
                "MATCH (scenario:Scenario {id: $id}) RETURN scenario.dataset_hash",
                {"id": self.scenario_id},
            )
            found = result.result_set[0][0] if result.result_set else None
            if found != self.dataset_hash:
                self._seed()

    def _seed(self) -> None:
        self.backend.query("MATCH (node) DETACH DELETE node")
        scenario = self.scenario_data
        duration = int(scenario.get("duration_minutes", 240))
        start_minute = int(scenario.get("start_minute", 0))
        end_minute = int(scenario.get("end_minute", start_minute + duration))
        raw_initial = scenario.get("initial_clock", start_minute)
        if isinstance(raw_initial, str):
            initial = max(start_minute, _clock_minutes(raw_initial, self.base_clock_minute) - self.base_clock_minute)
        else:
            initial = int(raw_initial)
        self._create_node(
            "Scenario",
            {
                "id": self.scenario_id,
                "name": scenario.get("name", "O'Hare recovery replay"),
                "airport": scenario.get("airport", "ORD"),
                "airport_name": scenario.get("airport_name", "Chicago O'Hare International Airport"),
                "timezone": scenario.get("timezone", "America/Chicago"),
                "date": self.scenario_date,
                "base_clock_minute": self.base_clock_minute,
                "start_minute": start_minute,
                "end_minute": end_minute,
                "clock_minute": initial,
                "last_checkpoint_minute": initial,
                "cycle": 0,
                "state_version": 1,
                "status": "MONITORING",
                "dataset_hash": self.dataset_hash,
                "automatic_replanning": True,
                "auto_apply": self.auto_apply,
                "planning_interval_minutes": self.planning_interval,
                "execution_freeze_minutes": self.execution_freeze,
                "minimum_benefit_minutes": self.minimum_benefit,
            },
        )
        airport_id = str(scenario.get("airport", "ORD"))
        self._create_node(
            "Airport",
            {
                "id": airport_id,
                "name": scenario.get("airport_name", "Chicago O'Hare International Airport"),
                "icao": "KORD" if airport_id == "ORD" else airport_id,
                "source_kind": "OBSERVED",
            },
        )
        self._create_edge(airport_id, "RUNS", self.scenario_id)

        for resource_type, items in self.data.get("resources", {}).items():
            label = "Runway" if resource_type == "runways" else "Gate"
            for item in items:
                item = dict(item)
                item.setdefault("status", "OPEN")
                item.setdefault("source_kind", "MODELED")
                self._create_node(label, item)
                self._create_edge(airport_id, "OPERATES", str(item["id"]))

        flights = [dict(item) for item in self.data.get("flights", [])]
        for flight in flights:
            flight.setdefault("status", "SCHEDULED")
            flight.setdefault("delay_minutes", 0)
            flight.setdefault("estimated_minute", int(flight.get("scheduled_minute", 0)))
            flight.setdefault("source_kind", "MIXED")
            self._create_node("Flight", flight)
            self._create_edge(self.scenario_id, "TRACKS", str(flight["id"]))
            for resource_key in ("gate", "runway"):
                if flight.get(resource_key):
                    self._create_edge(str(flight["id"]), "USES", str(flight[resource_key]))
        by_rotation: dict[str, list[dict[str, Any]]] = {}
        for flight in flights:
            by_rotation.setdefault(str(flight.get("rotation_id", flight["id"])), []).append(flight)
        for rotation_id, rotation_flights in by_rotation.items():
            self._create_node(
                "AircraftRotation",
                {"id": rotation_id, "name": rotation_id, "status": "ACTIVE", "source_kind": "DERIVED"},
            )
            for flight in rotation_flights:
                self._create_edge(rotation_id, "INCLUDES", str(flight["id"]))
            ordered = sorted(rotation_flights, key=lambda item: int(item.get("scheduled_minute", 0)))
            for first, second in zip(ordered, ordered[1:]):
                self._create_edge(str(first["id"]), "ROTATES_TO", str(second["id"]))

        gates = [dict(item) for item in self.data.get("resources", {}).get("gates", [])]
        for flight in flights:
            if str(flight.get("kind", "")).upper() != "OUTBOUND":
                continue
            aircraft_class = str(flight.get("aircraft_class", "NARROWBODY"))
            for gate in gates:
                compatible = gate.get("compatible_classes", [])
                if aircraft_class in compatible:
                    self._create_edge(
                        str(flight["id"]),
                        "COMPATIBLE_WITH",
                        str(gate["id"]),
                        {"source_kind": "MODELED"},
                    )

        task_template = [
            dict(item) for item in self.data.get("turnaround_task_template", [])
        ]
        turnarounds = [dict(item) for item in self.data.get("turnarounds", [])]
        turn_by_rotation: dict[str, dict[str, Any]] = {}
        task_ids_by_turn: dict[str, dict[str, str]] = {}
        for turn in turnarounds:
            turn.setdefault("status", "PLANNED")
            turn.setdefault(
                "current_critical_path_minutes",
                int(turn.get("baseline_critical_path_minutes", 45)),
            )
            turn.setdefault("source_kind", "MODELED")
            turn_by_rotation[str(turn["rotation_id"])] = turn
            self._create_node("Turnaround", turn)
            self._create_edge(
                str(turn["rotation_id"]), "HAS_TURNAROUND", str(turn["id"])
            )
            self._create_edge(
                str(turn["inbound_flight_id"]), "FEEDS", str(turn["id"])
            )
            self._create_edge(
                str(turn["id"]), "PREPARES", str(turn["outbound_flight_id"])
            )
            task_ids: dict[str, str] = {}
            for task in task_template:
                task_id = f"TASK-{turn['rotation_id']}-{task['key']}"
                task_ids[str(task["key"])] = task_id
                self._create_node(
                    "TurnaroundTask",
                    {
                        **task,
                        "id": task_id,
                        "turnaround_id": turn["id"],
                        "rotation_id": turn["rotation_id"],
                        "baseline_duration_minutes": int(task.get("duration_minutes", 0)),
                        "status": "PENDING",
                        "source_kind": "MODELED",
                        "source_id": turn.get(
                            "source_id", "MODEL-GROUNDGRAPH-V1"
                        ),
                    },
                )
                self._create_edge(str(turn["id"]), "HAS_TASK", task_id)
            task_ids_by_turn[str(turn["id"])] = task_ids
            dependencies = (
                ("DEPLANE", "CABIN_SERVICE"),
                ("DEPLANE", "BAG_RECONCILIATION"),
                ("CABIN_SERVICE", "BOARD_CLOSE"),
                ("BAG_RECONCILIATION", "BOARD_CLOSE"),
            )
            for before, after in dependencies:
                if before in task_ids and after in task_ids:
                    self._create_edge(
                        task_ids[before], "MUST_COMPLETE_BEFORE", task_ids[after]
                    )

        for event in self.data.get("events", []):
            item = dict(event)
            item["at_minute"] = int(item.get("at_minute", item.get("minute", initial)))
            initially_visible = item["at_minute"] <= initial
            item.setdefault("status", "ACTIVE" if initially_visible else "PENDING")
            item.setdefault("processed", initially_visible)
            item.setdefault("source_kind", "MODELED")
            item.setdefault("effect_source_kind", "MODELED")
            self._create_node("OperationalEvent", item)
            self._create_edge(self.scenario_id, "HAS_EVENT", str(item["id"]))
            if item.get("flight_id"):
                self._create_edge(str(item["id"]), "AFFECTS", str(item["flight_id"]))
            if item.get("resource_id"):
                self._create_edge(str(item["id"]), "CONSTRAINS", str(item["resource_id"]))
            if item.get("type") == "BAG_RECONCILIATION_DELAY" and item.get(
                "flight_id"
            ):
                flight = next(
                    (
                        candidate
                        for candidate in flights
                        if candidate["id"] == item["flight_id"]
                    ),
                    None,
                )
                turn = turn_by_rotation.get(str(flight.get("rotation_id"))) if flight else None
                task_id = (
                    task_ids_by_turn.get(str(turn["id"]), {}).get(
                        "BAG_RECONCILIATION"
                    )
                    if turn
                    else None
                )
                if task_id:
                    self._create_edge(str(item["id"]), "DELAYS", task_id)

        procedure_by_action = self._seed_knowledge_pack()
        action_aliases = {
            "RESEQUENCE_DEPARTURE": "ASSIGN_CAPACITY_SLOT",
            "REASSIGN_GATE": "ASSIGN_GATE",
            "RESEQUENCE_TASKS": "ACCELERATE_TURNAROUND",
            "PROTECT_ROTATION": "PROTECT_ROTATION",
            "APPLY_VALIDATED_PLAN": "APPLY_RECOVERY_PLAN",
        }
        for policy in self.data.get("policies", []):
            item = dict(policy)
            item.setdefault("status", "CURRENT")
            item.setdefault("current", True)
            item.setdefault("source_kind", "MODELED")
            linked_procedure = None
            for action_type in item.get("action_types", []):
                linked_procedure = procedure_by_action.get(action_type) or procedure_by_action.get(
                    action_aliases.get(action_type, action_type)
                )
                if linked_procedure:
                    break
            if linked_procedure:
                item.update(
                    {
                        "procedure_id": linked_procedure["id"],
                        "document_id": linked_procedure["document_id"],
                        "source_section": linked_procedure["source_section"],
                        "source_excerpt": linked_procedure.get("source_excerpt", ""),
                        "owner_team_id": linked_procedure["owner_team_id"],
                        "responsible_role_id": linked_procedure["responsible_role_id"],
                        "document_path": linked_procedure.get("document_path", ""),
                        "document_version": linked_procedure.get("document_version", ""),
                        "document_status": linked_procedure.get("document_status", ""),
                        "effective_from": linked_procedure.get("effective_from", ""),
                        "freshness_status": "CURRENT_FOR_REPLAY",
                    }
                )
            self._create_node("Policy", item)
            self._create_edge(airport_id, "GOVERNED_BY", str(item["id"]))
            if linked_procedure:
                self._create_edge(str(item["id"]), "ALIGNED_WITH", str(linked_procedure["id"]))
                self._create_edge(str(item["id"]), "IN_DOCUMENT", str(linked_procedure["document_id"]))
                self._create_edge(str(item["id"]), "OWNED_BY", str(linked_procedure["owner_team_id"]))
        for policy in self.data.get("policies", []):
            if policy.get("supersedes"):
                self._create_edge(str(policy["id"]), "SUPERSEDES", str(policy["supersedes"]))

        for source in self.data.get("provenance", []):
            item = dict(source)
            self._create_node("Source", item)
            self._create_edge(self.scenario_id, "USES_SOURCE", str(item["id"]))

        sourced_records = [
            *[dict(item) for item in self.data.get("flights", [])],
            *[
                dict(item)
                for items in self.data.get("resources", {}).values()
                for item in items
            ],
            *[dict(item) for item in self.data.get("events", [])],
            *[dict(item) for item in self.data.get("turnarounds", [])],
        ]
        for item in sourced_records:
            node_id = item.get("id")
            source_id = item.get("source_id")
            modeled_source_id = item.get("modeled_source_id")
            if node_id and source_id:
                relationship = (
                    "MODELED_BY"
                    if item.get("source_kind") == "MODELED"
                    else "SOURCED_FROM"
                )
                self._create_edge(str(node_id), relationship, str(source_id))
                source_record_id = str(
                    item.get("source_record_id")
                    or f"{source_id}:{node_id}"
                )
                record_node_id = (
                    "SOURCE-RECORD-"
                    + hashlib.sha256(
                        f"{source_id}|{source_record_id}".encode("utf-8")
                    ).hexdigest()[:18]
                )
                self._create_node(
                    "SourceRecord",
                    {
                        "id": record_node_id,
                        "source_id": source_id,
                        "source_record_id": source_record_id,
                        "entity_id": node_id,
                        "truth_label": item.get("source_kind", "MODELED"),
                        "observed_fields": item.get("observed_fields", []),
                        "modeled_fields": item.get("modeled_fields", []),
                    },
                )
                self._create_edge(record_node_id, "FROM_SOURCE", str(source_id))
                self._create_edge(str(node_id), relationship, record_node_id)
            if node_id and modeled_source_id and modeled_source_id != source_id:
                self._create_edge(
                    str(node_id), "MODELED_BY", str(modeled_source_id)
                )

        for agent_id, name, role in (*SPECIALISTS, COORDINATOR):
            self._create_node(
                "Agent",
                {
                    "id": agent_id,
                    "name": name,
                    "role": role,
                    "status": "MONITORING",
                    "current_task": "Watching shared FalkorDB state",
                    "last_cycle": 0,
                    "source_kind": "DERIVED",
                },
            )
            self._create_edge(agent_id, "MONITORS", self.scenario_id)

    @staticmethod
    def _knowledge_excerpt(document_path: str, section: str) -> str:
        path = BASE_DIR / "knowledge" / document_path
        if not path.exists() or not section:
            return ""
        text = path.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^##\s+{re.escape(section)}[^\n]*\n+(.*?)(?=^##\s+|\Z)",
            re.MULTILINE | re.DOTALL,
        )
        match = pattern.search(text)
        if not match:
            return ""
        return " ".join(match.group(1).strip().split())[:900]

    def _seed_knowledge_pack(self) -> dict[str, dict[str, Any]]:
        manifest_path = BASE_DIR / "knowledge" / "manifest.json"
        if not manifest_path.exists():
            return {}
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        organization = dict(manifest.get("organization", {}))
        if organization.get("id"):
            self._create_node("Organization", organization)
        for team in manifest.get("teams", []):
            self._create_node("Team", team)
            if organization.get("id"):
                self._create_edge(str(organization["id"]), "HAS_TEAM", str(team["id"]))
        for role in manifest.get("roles", []):
            self._create_node("Role", role)
            self._create_edge(str(role["team_id"]), "HAS_ROLE", str(role["id"]))
        documents: dict[str, dict[str, Any]] = {}
        for document in manifest.get("documents", []):
            item = dict(document)
            kind = str(item.get("kind", "DOCUMENT"))
            label = "SourceDocument" if kind == "SOP" else "Incident" if kind == "INCIDENT_TICKET" else "ShiftHandoff"
            self._create_node(label, item)
            documents[str(item["id"])] = item
            if organization.get("id"):
                self._create_edge(str(organization["id"]), "PUBLISHED", str(item["id"]))
            if item.get("owner_team_id"):
                self._create_edge(str(item["id"]), "OWNED_BY", str(item["owner_team_id"]))
            if item.get("responsible_role_id"):
                self._create_edge(str(item["id"]), "RESPONSIBLE_ROLE", str(item["responsible_role_id"]))
        for document in manifest.get("documents", []):
            if document.get("supersedes"):
                self._create_edge(str(document["id"]), "SUPERSEDES", str(document["supersedes"]))
        procedures_by_action: dict[str, dict[str, Any]] = {}
        for procedure in manifest.get("procedures", []):
            item = dict(procedure)
            document = documents.get(str(item.get("document_id")), {})
            item["source_excerpt"] = self._knowledge_excerpt(
                str(document.get("path", "")), str(item.get("source_section", ""))
            )
            item["document_path"] = document.get("path", "")
            item["document_version"] = document.get("version", "")
            item["document_status"] = document.get("status", "")
            self._create_node("ProcedureVersion", item)
            self._create_edge(str(item["id"]), "IN_DOCUMENT", str(item["document_id"]))
            self._create_edge(str(item["id"]), "OWNED_BY", str(item["owner_team_id"]))
            self._create_edge(str(item["id"]), "RESPONSIBLE_ROLE", str(item["responsible_role_id"]))
            if item.get("supersedes"):
                self._create_edge(str(item["id"]), "SUPERSEDES", str(item["supersedes"]))
            if item.get("status") == "CURRENT":
                procedures_by_action[str(item["action_type"])] = item
        for incident in manifest.get("incidents", []):
            incident_id = str(incident["id"])
            self.backend.query(
                "MATCH (i:Incident {id: $id}) SET i.event_id = $event, "
                "i.affected_flight_id = $flight, i.downstream_flight_id = $downstream, "
                "i.owner_team_id = $team, i.responsible_role_id = $role, "
                "i.opened_at = $opened",
                {
                    "id": incident_id,
                    "event": incident.get("event_id"),
                    "flight": incident.get("affected_flight_id"),
                    "downstream": incident.get("downstream_flight_id"),
                    "team": incident.get("owner_team_id"),
                    "role": incident.get("responsible_role_id"),
                    "opened": incident.get("opened_at"),
                },
            )
            if incident.get("event_id"):
                self._create_edge(incident_id, "REFERENCES", str(incident["event_id"]))
            if incident.get("responsible_role_id"):
                self._create_edge(
                    incident_id,
                    "RESPONSIBLE_ROLE",
                    str(incident["responsible_role_id"]),
                )
            if incident.get("affected_flight_id"):
                # The compact scenario uses a direction suffix; link to the exact
                # node only when the manifest identity is present.
                candidates = [
                    str(flight["id"])
                    for flight in self.data.get("flights", [])
                    if str(flight.get("callsign")) == str(incident["affected_flight_id"])
                    or str(flight.get("id")) == str(incident["affected_flight_id"])
                ]
                for flight_id in candidates:
                    self._create_edge(incident_id, "AFFECTS", flight_id)
        for handoff in manifest.get("shift_handoffs", []):
            if handoff.get("references_incident_id"):
                self._create_edge(str(handoff["id"]), "REFERENCES", str(handoff["references_incident_id"]))
        return procedures_by_action

    def _scenario_row(self) -> dict[str, Any]:
        result = self.backend.query(
            "MATCH (s:Scenario {id: $id}) RETURN s.name, s.airport, s.airport_name, "
            "s.timezone, s.date, s.base_clock_minute, s.start_minute, s.end_minute, s.clock_minute, "
            "s.last_checkpoint_minute, s.cycle, s.state_version, s.status",
            {"id": self.scenario_id},
        )
        if not result.result_set:
            raise RuntimeError("Scenario graph is unavailable")
        row = result.result_set[0]
        return {
            "id": self.scenario_id,
            "name": row[0],
            "airport": row[1],
            "airport_name": row[2],
            "timezone": row[3],
            "date": row[4],
            "base_clock_minute": int(row[5]),
            "start_minute": int(row[6]),
            "end_minute": int(row[7]),
            "clock_minute": int(row[8]),
            "last_checkpoint_minute": int(row[9]),
            "cycle": int(row[10]),
            "state_version": int(row[11]),
            "status": row[12],
        }

    def _flights(self) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (f:Flight) RETURN f.id, f.callsign, f.kind, f.scheduled_minute, "
            "f.estimated_minute, f.delay_minutes, f.gate, f.runway, f.rotation_id, "
            "f.status, f.source_kind, f.observed_fields, f.modeled_fields "
            ", f.source_id, f.source_record_id, f.modeled_source_id "
            "ORDER BY f.scheduled_minute, f.id"
        ).result_set
        return [
            {
                "id": row[0], "callsign": row[1], "kind": row[2],
                "scheduled_minute": int(row[3]), "scheduled_time": _clock(self.base_clock_minute + int(row[3])),
                "estimated_minute": int(row[4]), "estimated_time": _clock(self.base_clock_minute + int(row[4])),
                "delay_minutes": int(row[5]), "gate": row[6], "runway": row[7],
                "rotation_id": row[8], "status": row[9], "source_kind": row[10],
                "observed_fields": _list_value(row[11]),
                "modeled_fields": _list_value(row[12]),
                "source_id": row[13], "source_record_id": row[14],
                "modeled_source_id": row[15],
            }
            for row in rows
        ]

    def _events(self) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (e:OperationalEvent) RETURN e.id, e.at_minute, e.type, e.severity, "
            "e.flight_id, e.resource_id, e.delay_minutes, e.summary, e.source_kind, "
            "e.effect_source_kind, e.status, e.processed, e.observed_fields, "
            "e.modeled_fields, e.capacity_delta, e.source_id, e.source_record_id "
            "ORDER BY e.at_minute, e.id"
        ).result_set
        return [
            {
                "id": row[0], "at_minute": int(row[1]), "time": _clock(self.base_clock_minute + int(row[1])),
                "type": row[2], "severity": row[3], "flight_id": row[4],
                "resource_id": row[5], "delay_minutes": int(row[6]), "summary": row[7],
                "source_kind": row[8], "effect_source_kind": row[9],
                "status": row[10], "processed": bool(row[11]),
                "observed_fields": _list_value(row[12]),
                "modeled_fields": _list_value(row[13]),
                "capacity_delta": int(row[14] or 0),
                "source_id": row[15], "source_record_id": row[16],
            }
            for row in rows
        ]

    def _resources(self) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (r) WHERE r:Runway OR r:Gate RETURN r.id, labels(r), r.label, "
            "r.status, r.terminal, r.capacity_per_hour, r.role, r.source_kind, "
            "r.observed_fields, r.modeled_fields, r.source_id, "
            "r.source_record_id, r.modeled_source_id ORDER BY r.id"
        ).result_set
        return [
            {
                "id": row[0],
                "type": "RUNWAY" if "Runway" in list(row[1]) else "GATE",
                "label": row[2] or row[0], "status": row[3],
                "terminal": row[4], "capacity_per_hour": row[5], "role": row[6],
                "source_kind": row[7],
                "observed_fields": _list_value(row[8]),
                "modeled_fields": _list_value(row[9]),
                "source_id": row[10], "source_record_id": row[11],
                "modeled_source_id": row[12],
            }
            for row in rows
        ]

    def _provenance_records(self, limit: int = 40) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (entity)-[link]->(record:SourceRecord)-[:FROM_SOURCE]->(source:Source) "
            "WHERE type(link) IN ['SOURCED_FROM','MODELED_BY'] RETURN "
            "entity.id, labels(entity), type(link), record.id, "
            "record.source_record_id, record.truth_label, record.observed_fields, "
            "record.modeled_fields, source.id, source.label, source.url "
            "ORDER BY entity.id"
        ).result_set[:limit]
        return [
            {
                "entity_id": row[0], "entity_labels": list(row[1] or []),
                "relationship": row[2], "record_node_id": row[3],
                "source_record_id": row[4], "truth_label": row[5],
                "observed_fields": _list_value(row[6]),
                "modeled_fields": _list_value(row[7]), "source_id": row[8],
                "source_label": row[9], "source_url": row[10],
            }
            for row in rows
        ]

    def _agents(self) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (a:Agent) RETURN a.id, a.name, a.role, a.status, a.current_task, "
            "a.last_run_id, a.last_cycle ORDER BY a.id"
        ).result_set
        return [
            {
                "id": row[0], "name": row[1], "role": row[2], "status": row[3],
                "current_task": row[4], "last_run_id": row[5] or "",
                "last_cycle": int(row[6] or 0),
            }
            for row in rows
        ]

    def _policies(self) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (p:Policy) WHERE p.current = true RETURN p.id, p.title, p.version, "
            "p.status, p.owner, p.source, p.action_types, p.rule, p.procedure_id, "
            "p.document_id, p.source_section, p.source_excerpt, p.owner_team_id, "
            "p.responsible_role_id, p.document_path, p.document_version, "
            "p.document_status, p.effective_from, p.freshness_status ORDER BY p.id"
        ).result_set
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                actions = json.loads(row[6]) if isinstance(row[6], str) else list(row[6] or [])
            except (TypeError, json.JSONDecodeError):
                actions = []
            result.append(
                {
                    "id": row[0], "title": row[1], "version": row[2], "status": row[3],
                    "current": True, "owner": row[4], "source": row[5],
                    "action_types": actions, "rule": row[7], "source_kind": "MODELED",
                    "procedure_id": row[8], "document_id": row[9],
                    "source_section": row[10], "source_excerpt": row[11],
                    "owner_team_id": row[12], "responsible_role_id": row[13],
                    "document_path": row[14], "document_version": row[15],
                    "document_status": row[16], "effective_from": row[17],
                    "freshness_status": row[18],
                }
            )
        return result

    def _memories(self, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (m:Memory)-[:REMEMBERS]->(o:Outcome) RETURN m.id, m.strategy, "
            "m.disruption_signature, m.benefit_minutes, m.confidence, m.created_at, "
            "o.id, o.status, m.event_class, m.context_json, m.flight_ids, "
            "m.rotation_ids, m.resource_ids, m.policy_ids, o.plan_id "
            "ORDER BY m.created_at DESC"
        ).result_set[:limit]
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                context = json.loads(row[9]) if isinstance(row[9], str) else {}
            except (TypeError, json.JSONDecodeError):
                context = {}
            result.append(
                {
                    "id": row[0], "strategy": row[1],
                    "disruption_signature": row[2],
                    "benefit_minutes": int(row[3] or 0), "confidence": row[4],
                    "created_at": row[5], "outcome_id": row[6],
                    "outcome_status": row[7], "event_class": row[8],
                    "context": context,
                    "flight_ids": _list_value(row[10]),
                    "rotation_ids": _list_value(row[11]),
                    "resource_ids": _list_value(row[12]),
                    "policy_ids": _list_value(row[13]),
                    "plan_id": row[14],
                }
            )
        return result

    def _memory_lineages(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (d:DecisionEpisode)-[:SELECTED]->(p:Plan)-[:RESULTED_IN]->"
            "(o:Outcome)<-[:REMEMBERS]-(m:Memory) RETURN "
            "d.id, d.cycle, d.trigger, d.status, d.summary, d.based_on_version, "
            "d.created_at, d.disruption_signature, d.event_class, d.context_json, "
            "p.id, p.cycle, p.strategy, p.status, p.based_on_version, p.cost_score, "
            "p.projected_delay, p.payload_json, p.created_at, p.applied_at, "
            "o.id, o.status, o.projected_delay, o.benefit_minutes, o.created_at, "
            "m.id, m.strategy, m.disruption_signature, m.event_class, m.context_json, "
            "m.flight_ids, m.rotation_ids, m.resource_ids, m.policy_ids, "
            "m.benefit_minutes, m.confidence, m.created_at "
            "ORDER BY m.created_at DESC"
        ).result_set[:limit]
        lineages: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row[17]) if isinstance(row[17], str) else {}
            except (TypeError, json.JSONDecodeError):
                payload = {}
            try:
                context = json.loads(row[29]) if isinstance(row[29], str) else {}
            except (TypeError, json.JSONDecodeError):
                context = {}
            lineages.append(
                {
                    "episode": {
                        "id": row[0], "cycle": int(row[1] or 0),
                        "trigger": row[2], "status": row[3], "summary": row[4],
                        "based_on_version": int(row[5] or 0), "created_at": row[6],
                        "disruption_signature": row[7], "event_class": row[8],
                        "context_json": row[9],
                    },
                    "plan": {
                        "id": row[10], "cycle": int(row[11] or 0),
                        "strategy": row[12], "status": row[13],
                        "based_on_version": int(row[14] or 0),
                        "cost_score": row[15], "projected_delay": row[16],
                        "payload": payload, "created_at": row[18],
                        "applied_at": row[19],
                    },
                    "outcome": {
                        "id": row[20], "status": row[21],
                        "projected_delay": row[22], "benefit_minutes": row[23],
                        "created_at": row[24],
                    },
                    "memory": {
                        "id": row[25], "strategy": row[26],
                        "disruption_signature": row[27], "event_class": row[28],
                        "context": context, "flight_ids": _list_value(row[30]),
                        "rotation_ids": _list_value(row[31]),
                        "resource_ids": _list_value(row[32]),
                        "policy_ids": _list_value(row[33]),
                        "benefit_minutes": int(row[34] or 0),
                        "confidence": row[35], "created_at": row[36],
                    },
                }
            )
        return lineages

    def _episode_handoff_lineages(self) -> list[dict[str, Any]]:
        """Return the specialist evidence chains consumed by applied episodes."""

        rows = self.backend.query(
            "MATCH (d:DecisionEpisode)-[:SELECTED]->(p:Plan) "
            "WHERE p.status = 'APPLIED' "
            "MATCH (d)-[:CONSUMED_HANDOFF]->(h:Handoff) "
            "MATCH (f:Finding)-[:PACKAGED_AS]->(h) "
            "MATCH (r:AgentRun)-[:PRODUCED]->(f) RETURN "
            "d.id, "
            "h.id, h.from_agent, h.to_agent, h.status, h.summary, "
            "h.evidence_ids, h.state_version, h.cycle, h.created_at, "
            "h.query_name, h.started_at, h.ended_at, h.duration_ms, "
            "h.consumed_at, "
            "f.id, f.agent_id, f.cycle, f.severity, f.summary, "
            "f.recommendation, f.action_type, f.evidence_ids, "
            "f.based_on_version, f.query_name, f.started_at, f.ended_at, "
            "f.duration_ms, f.created_at, "
            "r.id, r.agent_id, r.cycle, r.status, r.based_on_version, "
            "r.query_name, r.started_at, r.ended_at, r.duration_ms, "
            "r.created_at ORDER BY d.created_at, h.created_at"
        ).result_set
        lineages: list[dict[str, Any]] = []
        for row in rows:
            lineages.append(
                {
                    "episode_id": row[0],
                    "handoff": {
                        "id": row[1], "from_agent": row[2],
                        "to_agent": row[3], "status": row[4],
                        "summary": row[5], "evidence_ids": _list_value(row[6]),
                        "state_version": int(row[7] or 0),
                        "cycle": int(row[8] or 0), "created_at": row[9],
                        "query_name": row[10], "started_at": row[11],
                        "ended_at": row[12], "duration_ms": int(row[13] or 0),
                        "consumed_at": row[14],
                    },
                    "finding": {
                        "id": row[15], "agent_id": row[16],
                        "cycle": int(row[17] or 0), "severity": row[18],
                        "summary": row[19], "recommendation": row[20],
                        "action_type": row[21],
                        "evidence_ids": _list_value(row[22]),
                        "based_on_version": int(row[23] or 0),
                        "query_name": row[24], "started_at": row[25],
                        "ended_at": row[26], "duration_ms": int(row[27] or 0),
                        "created_at": row[28],
                    },
                    "run": {
                        "id": row[29], "agent_id": row[30],
                        "cycle": int(row[31] or 0), "status": row[32],
                        "based_on_version": int(row[33] or 0),
                        "query_name": row[34], "started_at": row[35],
                        "ended_at": row[36], "duration_ms": int(row[37] or 0),
                        "created_at": row[38],
                    },
                }
            )
        return lineages

    def _applied_episode_coordinator_traces(self) -> list[dict[str, Any]]:
        """Return coordinator runs, tools, and models for applied episodes only."""

        traces: dict[str, dict[str, Any]] = {}
        run_rows = self.backend.query(
            "MATCH (d:DecisionEpisode)-[:SELECTED]->(p:Plan) "
            "WHERE p.status = 'APPLIED' "
            "MATCH (d)-[:USED_RUN]->(r:AgentRun) "
            "WHERE r.agent_id = 'AGENT-COORD' RETURN "
            "d.id, p.id, r.id, r.agent_id, r.cycle, r.status, "
            "r.based_on_version, r.decision_source, r.started_at, r.ended_at, "
            "r.created_at ORDER BY d.created_at"
        ).result_set
        for row in run_rows:
            episode_id = str(row[0])
            traces[episode_id] = {
                "episode_id": episode_id,
                "plan_id": row[1],
                "run": {
                    "id": row[2], "agent_id": row[3],
                    "cycle": int(row[4] or 0), "status": row[5],
                    "based_on_version": int(row[6] or 0),
                    "decision_source": row[7], "started_at": row[8],
                    "ended_at": row[9], "created_at": row[10],
                },
                "tools": [],
                "model": None,
            }

        tool_rows = self.backend.query(
            "MATCH (d:DecisionEpisode)-[:SELECTED]->(p:Plan) "
            "WHERE p.status = 'APPLIED' "
            "MATCH (d)-[:USED_TOOL]->(t:ToolCall) RETURN "
            "d.id, t.id, t.name, t.transport, t.arguments_json, "
            "t.evidence_ids, t.result_summary, t.status, t.sequence, t.cycle, "
            "t.created_at ORDER BY d.created_at, t.sequence"
        ).result_set
        for row in tool_rows:
            trace = traces.get(str(row[0]))
            if trace is None:
                continue
            try:
                arguments = (
                    json.loads(row[4]) if isinstance(row[4], str) else {}
                )
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            trace["tools"].append(
                {
                    "id": row[1], "name": row[2], "transport": row[3],
                    "arguments_json": arguments,
                    "evidence_ids": _list_value(row[5]),
                    "result_summary": row[6], "status": row[7],
                    "sequence": int(row[8] or 0),
                    "cycle": int(row[9] or 0), "created_at": row[10],
                }
            )

        model_rows = self.backend.query(
            "MATCH (d:DecisionEpisode)-[:SELECTED]->(p:Plan) "
            "WHERE p.status = 'APPLIED' "
            "MATCH (d)-[:USED_MODEL]->(m:ModelRun) RETURN "
            "d.id, p.id, m.id, m.provider, m.model, "
            "m.selected_candidate_id, m.allowlisted_candidate_ids, "
            "m.cost_direction, m.decision_source, m.summary, m.created_at "
            "ORDER BY d.created_at"
        ).result_set
        for row in model_rows:
            trace = traces.get(str(row[0]))
            if trace is None:
                continue
            trace["plan_id"] = row[1]
            trace["model"] = {
                "id": row[2], "provider": row[3], "model": row[4],
                "selected_candidate_id": row[5],
                "allowlisted_candidate_ids": _list_value(row[6]),
                "cost_direction": row[7], "decision_source": row[8],
                "summary": row[9], "created_at": row[10],
            }
        return list(traces.values())

    def _handoffs(self, limit: int = 60) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (h:Handoff) "
            "OPTIONAL MATCH (d:DecisionEpisode)-[:CONSUMED_HANDOFF]->(h) "
            "RETURN h.id, h.from_agent, h.to_agent, h.status, "
            "h.summary, h.evidence_ids, h.state_version, h.cycle, h.created_at, "
            "h.query_name, h.started_at, h.ended_at, h.duration_ms, h.consumed_at, "
            "d.id "
            "ORDER BY h.created_at DESC"
        ).result_set[:limit]
        handoffs = []
        for row in rows:
            try:
                evidence = json.loads(row[5]) if isinstance(row[5], str) else list(row[5] or [])
            except (TypeError, json.JSONDecodeError):
                evidence = []
            handoffs.append(
                {
                    "id": row[0], "from_agent": row[1], "to_agent": row[2],
                    "status": row[3], "summary": row[4], "evidence_ids": evidence,
                    "state_version": int(row[6]), "cycle": int(row[7]), "created_at": row[8],
                    "query_name": row[9], "started_at": row[10],
                    "ended_at": row[11], "duration_ms": int(row[12] or 0),
                    "consumed_at": row[13],
                    "episode_id": row[14],
                }
            )
        return handoffs

    def _tool_trace(self, limit: int = 16) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (d:DecisionEpisode)-[:USED_TOOL]->(t:ToolCall) RETURN "
            "t.id, t.name, t.transport, t.arguments_json, t.evidence_ids, "
            "t.result_summary, t.status, t.sequence, t.cycle, t.created_at, d.id "
            "ORDER BY t.created_at DESC, t.sequence"
        ).result_set[:limit]
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                arguments = json.loads(row[3]) if isinstance(row[3], str) else {}
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            result.append(
                {
                    "id": row[0], "name": row[1], "transport": row[2],
                    "arguments": arguments, "evidence_ids": _list_value(row[4]),
                    "result_summary": row[5], "status": row[6],
                    "sequence": int(row[7] or 0), "cycle": int(row[8] or 0),
                    "created_at": row[9], "episode_id": row[10],
                }
            )
        return result

    def _model_trace(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return persisted coordinator model runs and their exact allowlists."""

        rows = self.backend.query(
            "MATCH (d:DecisionEpisode)-[:USED_MODEL]->(m:ModelRun) RETURN "
            "m.id, m.provider, m.model, m.selected_candidate_id, "
            "m.allowlisted_candidate_ids, m.cost_direction, m.decision_source, "
            "m.summary, m.created_at, d.id, d.cycle "
            "ORDER BY m.created_at DESC"
        ).result_set[:limit]
        return [
            {
                "id": row[0],
                "provider": row[1],
                "model": row[2],
                "selected_candidate_id": row[3],
                "allowlisted_candidate_ids": _list_value(row[4]),
                "cost_direction": row[5],
                "decision_source": row[6],
                "summary": row[7],
                "created_at": row[8],
                "episode_id": row[9],
                "cycle": int(row[10] or 0),
            }
            for row in rows
        ]

    def _current_plan(self) -> dict[str, Any] | None:
        rows = self.backend.query(
            "MATCH (p:Plan) WHERE p.is_current = true RETURN p.payload_json, p.status, "
            "p.based_on_version ORDER BY p.created_at DESC"
        ).result_set
        if not rows:
            return None
        payload = _normalize_plan_payload(json.loads(rows[0][0]))
        context = payload.get("causal_context", {})
        if isinstance(context, Mapping):
            payload["causal_path"] = self._graph_causal_path(
                _list_value(context.get("event_ids")),
                _list_value(context.get("action_ids")),
            )
        payload["status"] = rows[0][1]
        payload["based_on_version"] = int(rows[0][2])
        return payload

    def _latest_applied_plan(self) -> dict[str, Any] | None:
        """Return the latest applied plan strictly for snapshot presentation.

        Applied plans stop being current after their graph mutation increments the
        state version.  Keeping this read model separate from ``_current_plan``
        prevents historical decisions from entering replanning or execution while
        letting the command center retain the last decision and outcome.
        """

        rows = self.backend.query(
            "MATCH (p:Plan) WHERE p.status = 'APPLIED' "
            "RETURN p.payload_json, p.status, p.based_on_version, p.applied_at "
            "ORDER BY p.applied_at DESC, p.created_at DESC"
        ).result_set
        if not rows:
            return None
        payload = _normalize_plan_payload(json.loads(rows[0][0]))
        context = payload.get("causal_context", {})
        if isinstance(context, Mapping):
            payload["causal_path"] = self._graph_causal_path(
                _list_value(context.get("event_ids")),
                _list_value(context.get("action_ids")),
            )
        payload["status"] = rows[0][1]
        payload["based_on_version"] = int(rows[0][2])
        payload["applied_at"] = rows[0][3] or payload.get("applied_at")
        payload["display_only"] = True
        payload["historical"] = True
        payload["is_current"] = False
        return payload

    def _last_applied_minute(self) -> int | None:
        rows = self.backend.query(
            "MATCH (p:Plan) WHERE p.status = 'APPLIED' RETURN p.applied_at_minute "
            "ORDER BY p.created_at DESC"
        ).result_set
        if not rows or rows[0][0] is None:
            return None
        return int(rows[0][0])

    def _history(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (d:DecisionEpisode) OPTIONAL MATCH (d)-[:SELECTED]->(p:Plan) "
            "RETURN d.id, d.cycle, d.trigger, d.status, d.summary, d.created_at, "
            "p.id, p.strategy ORDER BY d.created_at DESC"
        ).result_set[:limit]
        return [
            {
                "episode_id": row[0], "cycle": int(row[1]), "trigger": row[2],
                "status": row[3], "summary": row[4], "created_at": row[5],
                "plan_id": row[6], "strategy": row[7],
            }
            for row in rows
        ]

    def _specialist_job(
        self,
        agent_id: str,
        state_version: int,
        disruption_context: Mapping[str, Any],
        start_barrier: threading.Barrier,
    ) -> dict[str, Any]:
        """Run one specialist's real graph read on the shared input version.

        Official FalkorDB uses an independent connection per worker so the reads
        can overlap.  FalkorDBLite keeps the single embedded connection, while the
        same barrier and domain-specific Cypher path are retained for tests.
        """

        worker_backend: GraphBackend | None = None
        backend = self.backend
        if self.backend.mode == "server":
            worker_backend = GraphBackend(
                mode="server", graph_name=self.backend.graph_name
            )
            backend = worker_backend
        query_name = "unknown"
        started_at = _utc_now()
        start_clock = time.perf_counter()
        try:
            start_barrier.wait(timeout=5)
            started_at = _utc_now()
            start_clock = time.perf_counter()

            if agent_id == "AGENT-WEATHER":
                query_name = "active_weather_observations"
                rows = backend.query(
                    "MATCH (e:OperationalEvent) WHERE e.status = 'ACTIVE' AND "
                    "e.type IN ['LOW_VISIBILITY_SNOW','WEATHER_UPDATE','WEATHER','WIND'] "
                    "RETURN e.id, e.severity, e.source_kind, e.observed_fields "
                    "ORDER BY e.at_minute"
                ).result_set
                evidence_ids = [str(row[0]) for row in rows]
                observed = sum(
                    1
                    for row in rows
                    if row[2] in {"OBSERVED", "MIXED"} and _list_value(row[3])
                )
                severity = "HIGH" if any(row[1] == "HIGH" for row in rows) else "WATCH"
                summary = (
                    f"Weather queried FalkorDB version {state_version}: "
                    f"{observed} observed update(s), {len(rows)} active condition(s)."
                )
                recommendation = "Use only observations available by the replay clock."
                action_type = "RESEQUENCE_DEPARTURE"
            elif agent_id == "AGENT-FLOW":
                query_name = "runway_capacity_neighborhood"
                rows = backend.query(
                    "MATCH (r:Runway) OPTIONAL MATCH (e:OperationalEvent)-[:CONSTRAINS]->(r) "
                    "RETURN r.id, r.status, r.capacity_per_hour, e.id, e.status, e.capacity_delta "
                    "ORDER BY r.id, e.at_minute"
                ).result_set
                active_rows = [row for row in rows if row[3] and row[4] == "ACTIVE"]
                evidence_ids = sorted(
                    {str(row[0]) for row in rows}
                    | {str(row[3]) for row in active_rows}
                )
                summary = (
                    f"Flow queried {len({row[0] for row in rows})} runway(s) and "
                    f"{len(active_rows)} active capacity constraint(s) at graph version {state_version}."
                )
                severity = "HIGH" if active_rows else "WATCH"
                recommendation = "Resequence only on a runway used by the target flight."
                action_type = "RESEQUENCE_DEPARTURE"
            elif agent_id == "AGENT-GATE":
                query_name = "compatible_gate_options"
                rows = backend.query(
                    "MATCH (f:Flight)-[:COMPATIBLE_WITH]->(g:Gate) "
                    "WHERE f.kind = 'OUTBOUND' "
                    "RETURN f.id, g.id, g.status, f.delay_minutes ORDER BY f.id, g.id"
                ).result_set
                open_rows = [row for row in rows if row[2] in {"OPEN", "AVAILABLE"}]
                evidence_ids = sorted(
                    {str(row[0]) for row in open_rows}
                    | {str(row[1]) for row in open_rows}
                )
                summary = (
                    f"Gate queried {len(open_rows)} compatible open flight-gate pair(s) "
                    f"at graph version {state_version}."
                )
                severity = "MEDIUM" if open_rows else "HIGH"
                recommendation = "Use only an explicit COMPATIBLE_WITH relationship."
                action_type = "REASSIGN_GATE"
            elif agent_id == "AGENT-TURN":
                query_name = "turnaround_dependency_dag"
                rows = backend.query(
                    "MATCH (t:Turnaround)-[:HAS_TASK]->(task:TurnaroundTask) "
                    "OPTIONAL MATCH (e:OperationalEvent)-[:DELAYS]->(task) "
                    "RETURN t.id, t.rotation_id, t.current_critical_path_minutes, "
                    "task.id, task.status, e.id, e.status ORDER BY t.id, task.id"
                ).result_set
                delayed_rows = [row for row in rows if row[5] and row[6] == "ACTIVE"]
                evidence_ids = sorted(
                    {str(row[0]) for row in rows}
                    | {str(row[3]) for row in rows}
                    | {str(row[5]) for row in delayed_rows}
                )
                summary = (
                    f"Turnaround queried {len({row[0] for row in rows})} dependency DAG(s); "
                    f"{len(delayed_rows)} task delay edge(s) are active at version {state_version}."
                )
                severity = "HIGH" if delayed_rows else "WATCH"
                recommendation = "Resequence only tasks connected by the stored dependency DAG."
                action_type = "RESEQUENCE_TASKS"
            elif agent_id == "AGENT-POLICY":
                query_name = "current_policy_authority"
                rows = backend.query(
                    "MATCH (p:Policy) WHERE p.current = true "
                    "RETURN p.id, p.status, p.action_types, p.freshness_status, "
                    "p.owner_team_id, p.responsible_role_id ORDER BY p.id"
                ).result_set
                evidence_ids = [str(row[0]) for row in rows]
                covered = {
                    action for row in rows for action in _list_value(row[2])
                }
                current = [
                    row
                    for row in rows
                    if row[1] == "CURRENT" and row[3] == "CURRENT_FOR_REPLAY"
                ]
                summary = (
                    f"Policy queried {len(current)} current version(s) covering "
                    f"{len(covered)} action type(s) at graph version {state_version}."
                )
                severity = "CLEAR" if current else "HIGH"
                recommendation = "Permit only current, owned, source-linked authority."
                action_type = "APPLY_VALIDATED_PLAN"
            else:
                query_name = "contextual_successful_memory"
                rows = backend.query(
                    "MATCH (m:Memory)-[:REMEMBERS]->(o:Outcome) "
                    "WHERE o.status = 'SUCCESS' "
                    "RETURN m.id, m.event_class, m.context_json, m.strategy, o.id "
                    "ORDER BY m.created_at DESC"
                ).result_set
                event_class = str(disruption_context.get("event_class", ""))
                related = [row for row in rows if str(row[1] or "") == event_class]
                evidence_ids = [str(row[0]) for row in related]
                summary = (
                    f"Memory queried {len(rows)} successful episode(s); {len(related)} "
                    f"matched the {event_class or 'current'} disruption context at version {state_version}."
                )
                severity = "INFORMED" if related else "NEW"
                recommendation = "Adjust cost only with context-matched outcome evidence."
                action_type = "APPLY_VALIDATED_PLAN"

            ended_at = _utc_now()
            return {
                "agent_id": agent_id,
                "severity": severity,
                "summary": summary,
                "recommendation": recommendation,
                "action_type": action_type,
                "evidence_ids": evidence_ids,
                "query_name": query_name,
                "started_at": started_at,
                "ended_at": ended_at,
                "duration_ms": max(0, int((time.perf_counter() - start_clock) * 1000)),
                "based_on_version": state_version,
            }
        finally:
            if worker_backend is not None:
                worker_backend.close()

    def _record_specialists(
        self,
        *,
        cycle: int,
        decision_token: str,
        state_version: int,
        flights: list[dict[str, Any]],
        events: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        policies: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        disruption_context: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self.backend.query(
            "MATCH (a:Agent) WHERE a.id <> 'AGENT-COORD' SET a.status = 'ANALYZING', "
            "a.current_task = 'Reading shared graph state'"
        )
        start_barrier = threading.Barrier(len(SPECIALISTS))
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="groundgraph-agent") as pool:
            futures = [
                pool.submit(
                    self._specialist_job,
                    agent_id,
                    state_version,
                    dict(disruption_context),
                    start_barrier,
                )
                for agent_id, _name, _role in SPECIALISTS
            ]
            findings = [future.result(timeout=15) for future in futures]
        handoffs: list[dict[str, Any]] = []
        for finding in findings:
            agent_id = str(finding["agent_id"])
            short = agent_id.removeprefix("AGENT-")
            run_id = f"RUN-{cycle}-{decision_token}-{short}"
            finding_id = f"FINDING-{cycle}-{decision_token}-{short}"
            handoff_id = f"HANDOFF-{cycle}-{decision_token}-{short}-COORD"
            created_at = finding["ended_at"]
            shared = {
                "query_name": finding["query_name"],
                "started_at": finding["started_at"],
                "ended_at": finding["ended_at"],
                "duration_ms": finding["duration_ms"],
            }
            self._create_node("AgentRun", {"id": run_id, "agent_id": agent_id, "cycle": cycle, "status": "COMPLETED", "based_on_version": state_version, **shared, "created_at": created_at})
            self._create_node("Finding", {"id": finding_id, "agent_id": agent_id, "cycle": cycle, "severity": finding["severity"], "summary": finding["summary"], "recommendation": finding["recommendation"], "action_type": finding["action_type"], "evidence_ids": finding["evidence_ids"], "based_on_version": state_version, **shared, "created_at": created_at})
            self._create_node("Handoff", {"id": handoff_id, "from_agent": agent_id, "to_agent": "AGENT-COORD", "status": "AVAILABLE", "summary": finding["summary"], "evidence_ids": finding["evidence_ids"], "state_version": state_version, "cycle": cycle, **shared, "created_at": created_at})
            self._create_edge(agent_id, "STARTED", run_id)
            self._create_edge(run_id, "PRODUCED", finding_id)
            self._create_edge(finding_id, "PACKAGED_AS", handoff_id)
            self._create_edge(handoff_id, "DELIVERED_TO", "AGENT-COORD")
            self.backend.query("MATCH (a:Agent {id: $id}) SET a.status = 'WAITING', a.current_task = 'Handoff delivered', a.last_run_id = $run, a.last_cycle = $cycle", {"id": agent_id, "run": run_id, "cycle": cycle})
            handoffs.append({"id": handoff_id, "from_agent": agent_id, "to_agent": "AGENT-COORD", "status": "AVAILABLE", "summary": finding["summary"], "evidence_ids": list(finding["evidence_ids"]), "state_version": state_version, "cycle": cycle, **shared, "created_at": created_at})
        return findings, handoffs

    @staticmethod
    def _policy_index(policies: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for policy in policies:
            for action_type in policy.get("action_types", []):
                index[str(action_type)] = dict(policy)
        return index

    def _disruption_context(
        self,
        active_events: Iterable[Mapping[str, Any]],
        flights: Iterable[Mapping[str, Any]],
        policies: Iterable[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        events = [dict(item) for item in active_events]
        flight_index = {str(item["id"]): dict(item) for item in flights}
        event_types = sorted({str(item.get("type", "UNKNOWN")) for item in events})
        if any("BAG" in item or "TURNAROUND" in item for item in event_types):
            event_class = "TURNAROUND"
        elif any(item in {"LOW_VISIBILITY_SNOW", "WEATHER_UPDATE", "WEATHER", "WIND"} for item in event_types):
            event_class = "WEATHER"
        elif any("RUNWAY" in item or "CAPACITY" in item for item in event_types):
            event_class = "RUNWAY"
        else:
            event_class = event_types[0] if event_types else "MONITORING"
        flight_ids = sorted(
            {str(item["flight_id"]) for item in events if item.get("flight_id")}
        )
        rotation_ids = sorted(
            {
                str(flight_index[flight_id].get("rotation_id"))
                for flight_id in flight_ids
                if flight_id in flight_index
                and flight_index[flight_id].get("rotation_id")
            }
        )
        resource_ids = sorted(
            {str(item["resource_id"]) for item in events if item.get("resource_id")}
        )
        policy_ids = sorted(
            {str(item["id"]) for item in policies if item.get("id")}
        )
        context = {
            "airport": str(self.scenario_data.get("airport", "ORD")),
            "event_class": event_class,
            "event_types": event_types,
            "event_ids": sorted(str(item["id"]) for item in events),
            "flight_ids": flight_ids,
            "rotation_ids": rotation_ids,
            "resource_ids": resource_ids,
            "policy_ids": policy_ids,
        }
        context["signature"] = _hash(context)[:20]
        return context

    @staticmethod
    def _context_matches(
        memory: Mapping[str, Any], context: Mapping[str, Any]
    ) -> bool:
        """Require the same disruption class plus relevant graph overlap.

        Weather episodes can match through a runway resource; ground episodes
        require a shared flight or rotation.  This prevents a baggage recovery
        from silently changing a later weather decision.
        """

        if str(memory.get("event_class", "")) != str(context.get("event_class", "")):
            return False
        old = memory.get("context") if isinstance(memory.get("context"), Mapping) else memory
        event_class = str(context.get("event_class", ""))
        dimensions = (
            ("resource_ids",)
            if event_class in {"WEATHER", "RUNWAY"}
            else ("flight_ids", "rotation_ids", "resource_ids")
        )
        for key in dimensions:
            left = {str(item) for item in _list_value(old.get(key))}
            right = {str(item) for item in _list_value(context.get(key))}
            if left and right and left.intersection(right):
                return True
        # An exact class is enough only when neither episode has a more precise
        # entity key, which keeps sparse historical memory usable and auditable.
        has_old_keys = any(_list_value(old.get(key)) for key in dimensions)
        has_new_keys = any(_list_value(context.get(key)) for key in dimensions)
        return not has_old_keys and not has_new_keys

    def _candidate_actions(
        self,
        *,
        cycle: int,
        decision_token: str,
        flights: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        active_events: list[dict[str, Any]],
        policy_index: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        delayed = sorted(
            (flight for flight in flights if flight["delay_minutes"] > 0),
            key=lambda item: (-item["delay_minutes"], item["id"]),
        )
        event_flight_id = next(
            (
                str(event["flight_id"])
                for event in active_events
                if event.get("flight_id")
            ),
            None,
        )
        seed_flight = next(
            (item for item in flights if item["id"] == event_flight_id),
            delayed[0] if delayed else None,
        )
        if seed_flight is None:
            seed_flight = max(
                (item for item in flights if item["kind"] == "OUTBOUND"),
                key=lambda item: int(item["scheduled_minute"]),
            )
        rotation_id = str(seed_flight["rotation_id"])
        outbound = next(
            (
                flight
                for flight in flights
                if flight["rotation_id"] == rotation_id
                and flight["kind"] == "OUTBOUND"
            ),
            seed_flight,
        )
        inbound = next(
            (
                flight
                for flight in flights
                if flight["rotation_id"] == rotation_id
                and flight["kind"] == "INBOUND"
            ),
            seed_flight,
        )
        runway_id = str(outbound.get("runway") or "")
        if not runway_id:
            runway_id = next(
                (
                    str(item["id"])
                    for item in resources
                    if item["type"] == "RUNWAY"
                    and item.get("role") == "DEPARTURE"
                ),
                "",
            )
        gate_rows = self.backend.query(
            "MATCH (f:Flight {id: $flight})-[:COMPATIBLE_WITH]->(g:Gate) "
            "WHERE g.status IN ['OPEN','AVAILABLE'] RETURN g.id ORDER BY g.id",
            {"flight": outbound["id"]},
        ).result_set
        gate_id = str(gate_rows[0][0]) if gate_rows else ""
        turn_rows = self.backend.query(
            "MATCH (t:Turnaround {rotation_id: $rotation}) RETURN t.id",
            {"rotation": rotation_id},
        ).result_set
        turnaround_id = str(turn_rows[0][0]) if turn_rows else ""
        targets = {
            "RESEQUENCE_DEPARTURE": runway_id,
            "REASSIGN_GATE": gate_id,
            "RESEQUENCE_TASKS": turnaround_id,
            "PROTECT_ROTATION": str(outbound["id"]),
        }
        descriptions = {
            "RESEQUENCE_DEPARTURE": "Re-sequence the modeled departure queue inside available runway capacity.",
            "REASSIGN_GATE": "Hold a compatible recovery gate for the affected aircraft rotation.",
            "RESEQUENCE_TASKS": "Re-sequence the stored turnaround task DAG around the delayed baggage task.",
            "PROTECT_ROTATION": "Protect the next leg while preserving turnaround dependencies.",
        }
        actions = []
        for index, action_type in enumerate(ACTION_TYPES, start=1):
            policy = policy_index.get(action_type)
            policy_current = bool(policy and policy.get("status") == "CURRENT")
            knowledge_current = bool(
                policy_current
                and policy.get("freshness_status") == "CURRENT_FOR_REPLAY"
            )
            actions.append(
                {
                    "id": f"ACTION-{cycle}-{decision_token}-{index}", "type": action_type,
                    "description": descriptions[action_type], "target_id": targets[action_type],
                    "affected_flight_id": str(outbound["id"]),
                    "inbound_flight_id": str(inbound["id"]),
                    "rotation_id": rotation_id,
                    "policy_id": policy.get("id") if policy else None,
                    "authorized": policy_current,
                    "knowledge_current": knowledge_current,
                    "freshness_status": policy.get("freshness_status") if policy else None,
                }
            )
        return actions

    def _action_graph_precondition(
        self, action: Mapping[str, Any]
    ) -> tuple[bool, str, list[str]]:
        action_type = str(action.get("type", ""))
        target_id = str(action.get("target_id", ""))
        flight_id = str(action.get("affected_flight_id", ""))
        inbound_id = str(action.get("inbound_flight_id", ""))
        if not target_id:
            return False, f"{action_type} has no graph target.", []
        if action_type == "RESEQUENCE_DEPARTURE":
            query = (
                "MATCH (f:Flight {id: $flight})-[:USES]->(r:Runway {id: $target}) "
                "RETURN count(r)"
            )
            detail = "Target flight uses the selected departure runway."
            params = {"flight": flight_id, "target": target_id}
            evidence = [flight_id, target_id]
        elif action_type == "REASSIGN_GATE":
            query = (
                "MATCH (f:Flight {id: $flight})-[:COMPATIBLE_WITH]->"
                "(g:Gate {id: $target}) WHERE g.status IN ['OPEN','AVAILABLE'] "
                "RETURN count(g)"
            )
            detail = "Target flight has an explicit compatible, open gate edge."
            params = {"flight": flight_id, "target": target_id}
            evidence = [flight_id, target_id]
        elif action_type == "RESEQUENCE_TASKS":
            query = (
                "MATCH (turn:Turnaround {id: $target})-[:HAS_TASK]->"
                "(a:TurnaroundTask)-[:MUST_COMPLETE_BEFORE]->(b:TurnaroundTask) "
                "RETURN count(a)"
            )
            detail = "Turnaround has a stored MUST_COMPLETE_BEFORE task dependency."
            params = {"target": target_id}
            evidence = [target_id]
        elif action_type == "PROTECT_ROTATION":
            query = (
                "MATCH (a:Flight {id: $inbound})-[:ROTATES_TO]->"
                "(b:Flight {id: $target}) RETURN count(b)"
            )
            detail = "Inbound flight rotates to the protected outbound leg."
            params = {"inbound": inbound_id, "target": target_id}
            evidence = [inbound_id, target_id]
        else:
            return False, f"Unsupported action type {action_type}.", []
        rows = self.backend.query(query, params).result_set
        passed = bool(rows and int(rows[0][0]) > 0)
        return passed, detail if passed else f"Missing graph precondition: {detail}", evidence

    def _rank_candidates(
        self,
        *,
        cycle: int,
        active_delay: int,
        actions: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        active_events: list[dict[str, Any]],
        disruption_context: Mapping[str, Any],
        clock_minute: int,
        flights: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        churn_blocked: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        definitions = (
            ("FLOW_BALANCE", ("RESEQUENCE_DEPARTURE", "PROTECT_ROTATION")),
            ("GATE_SWAP", ("REASSIGN_GATE", "PROTECT_ROTATION")),
            ("TURNAROUND_RECOVERY", ("RESEQUENCE_TASKS", "PROTECT_ROTATION")),
        )
        matching_memories = [
            item
            for item in memories
            if item.get("outcome_status") == "SUCCESS"
            and self._context_matches(item, disruption_context)
        ]
        frozen_flights = {
            str(flight["id"])
            for flight in flights
            if flight["kind"] == "OUTBOUND"
            and clock_minute <= int(flight["scheduled_minute"]) <= clock_minute + self.execution_freeze
        }
        constrained_runways = {
            str(event["resource_id"])
            for event in active_events
            if event.get("resource_id")
            and (
                "RUNWAY" in str(event.get("type", ""))
                or str(event.get("type", ""))
                in {"LOW_VISIBILITY_SNOW", "WEATHER_UPDATE", "WEATHER", "WIND"}
            )
        }
        flow_signal = sum(
            max(
                abs(int(event.get("capacity_delta", 0))),
                int(event.get("delay_minutes", 0)),
            )
            for event in active_events
            if str(event.get("resource_id", "")) in constrained_runways
        )
        candidates: list[dict[str, Any]] = []
        context_event_class = str(disruption_context.get("event_class", ""))
        preferred_strategy = {
            "TURNAROUND": "TURNAROUND_RECOVERY",
            "WEATHER": "FLOW_BALANCE",
            "RUNWAY": "FLOW_BALANCE",
        }.get(context_event_class)
        for strategy, action_types in definitions:
            candidate_actions = [
                deepcopy(action) for action in actions if action["type"] in action_types
            ]
            if strategy == "FLOW_BALANCE":
                reduction = min(active_delay, max(self.minimum_benefit, round(flow_signal * 0.65)))
                burden = 1 + 2 * len(constrained_runways)
                benefit_components = {
                    "active_delay_minutes": active_delay,
                    "capacity_signal_minutes": flow_signal,
                    "constrained_runways": len(constrained_runways),
                }
                burden_components = {"sequence_change": 1, "runway_constraints": 2 * len(constrained_runways)}
            elif strategy == "GATE_SWAP":
                gate_action = next(action for action in candidate_actions if action["type"] == "REASSIGN_GATE")
                gate_rows = self.backend.query(
                    "MATCH (:Flight {id: $flight})-[:COMPATIBLE_WITH]->(g:Gate) "
                    "WHERE g.status IN ['OPEN','AVAILABLE'] RETURN count(g)",
                    {"flight": gate_action["affected_flight_id"]},
                ).result_set
                compatible_gates = int(gate_rows[0][0]) if gate_rows else 0
                reduction = min(active_delay, max(self.minimum_benefit, 4 + 2 * compatible_gates))
                burden = 2
                benefit_components = {
                    "active_delay_minutes": active_delay,
                    "compatible_open_gates": compatible_gates,
                }
                burden_components = {"gate_coordination": 2}
            else:
                turn_action = next(action for action in candidate_actions if action["type"] == "RESEQUENCE_TASKS")
                turn_rows = self.backend.query(
                    "MATCH (t:Turnaround {id: $id}) RETURN "
                    "t.baseline_critical_path_minutes, t.current_critical_path_minutes",
                    {"id": turn_action["target_id"]},
                ).result_set
                baseline = int(turn_rows[0][0] or 0) if turn_rows else 0
                current = int(turn_rows[0][1] or baseline) if turn_rows else baseline
                excess = max(0, current - baseline)
                reduction = min(active_delay, max(self.minimum_benefit, excess))
                burden = 4
                benefit_components = {
                    "active_delay_minutes": active_delay,
                    "critical_path_before": current,
                    "critical_path_baseline": baseline,
                    "recoverable_excess_minutes": excess,
                }
                burden_components = {"task_resequence": 3, "ramp_coordination": 1}
            projected = max(0, active_delay - reduction)
            context_penalty = (
                0
                if not preferred_strategy or strategy == preferred_strategy
                else 12
            )
            base_cost_score = projected + burden + context_penalty
            memory_adjustment = 0
            memory_ids: list[str] = []
            related = next(
                (item for item in matching_memories if item.get("strategy") == strategy),
                None,
            )
            if related:
                memory_adjustment = -2
                memory_ids.append(str(related["id"]))
            for priority, action in enumerate(candidate_actions, start=1):
                action["priority"] = priority
                action["role"] = "PRIMARY" if priority == 1 else "SUPPORTING"
            policy_complete = all(action["authorized"] for action in candidate_actions)
            knowledge_current = all(
                action["knowledge_current"] for action in candidate_actions
            )
            freeze_clear = not any(
                str(action.get("target_id")) in frozen_flights for action in candidate_actions
            )
            graph_preconditions = [
                self._action_graph_precondition(action) for action in candidate_actions
            ]
            targets_exist = bool(candidate_actions) and all(
                item[0] for item in graph_preconditions
            )
            benefit_clear = min(active_delay, reduction) >= self.minimum_benefit
            safety_checks = [
                {"id": "CURRENT_GRAPH_STATE", "passed": True, "detail": "All specialist runs and the plan use the same graph state version."},
                {"id": "CURRENT_POLICY_AUTHORITY", "passed": policy_complete, "detail": "Every action is linked to a current versioned policy."},
                {"id": "CURRENT_KNOWLEDGE", "passed": knowledge_current, "detail": "Every action is backed by knowledge marked CURRENT_FOR_REPLAY."},
                {"id": "TARGETS_EXIST", "passed": targets_exist, "detail": "Every action target and required relationship exists in FalkorDB.", "graph_preconditions": [{"passed": item[0], "detail": item[1], "evidence_ids": item[2]} for item in graph_preconditions]},
                {"id": "EXECUTION_FREEZE", "passed": freeze_clear, "detail": f"No action targets outbound flights inside the {self.execution_freeze}-minute execution freeze.", "protected_flight_ids": sorted(frozen_flights)},
                {"id": "MINIMUM_BENEFIT", "passed": benefit_clear, "detail": f"Projected recovery must be at least {self.minimum_benefit} minutes."},
                {"id": "CHURN_GUARD", "passed": not churn_blocked, "detail": "A new plan is blocked for ten replay minutes after execution unless a high-risk material event arrives."},
            ]
            eligible = all(check["passed"] for check in safety_checks)
            candidates.append(
                {
                    "id": f"CAND-{cycle}-{strategy}", "strategy": strategy,
                    "base_cost_score": base_cost_score,
                    "memory_adjustment": memory_adjustment,
                    "cost_score": base_cost_score + memory_adjustment,
                    "projected_delay": projected,
                    "recovered_minutes": min(active_delay, reduction), "eligible": eligible,
                    "actions": candidate_actions, "memory_ids": memory_ids,
                    "safety_checks": safety_checks,
                    "benefit_components": benefit_components,
                    "burden_minutes": burden,
                    "burden_components": burden_components,
                    "context_event_class": context_event_class,
                    "context_preferred_strategy": preferred_strategy,
                    "context_penalty": context_penalty,
                }
            )
        safe = [item for item in candidates if item["eligible"]]
        for item in safe:
            item["dominated"] = any(
                other["recovered_minutes"] >= item["recovered_minutes"]
                and other["burden_minutes"] <= item["burden_minutes"]
                and int(other.get("context_penalty", 0))
                <= int(item.get("context_penalty", 0))
                and (
                    other["recovered_minutes"] > item["recovered_minutes"]
                    or other["burden_minutes"] < item["burden_minutes"]
                    or int(other.get("context_penalty", 0))
                    < int(item.get("context_penalty", 0))
                )
                for other in safe
                if other["id"] != item["id"]
            )
        model_safe = [item for item in safe if not item.get("dominated")]
        base_safe = sorted(model_safe, key=lambda item: (item["base_cost_score"], item["id"]))
        ranked = sorted(model_safe, key=lambda item: (item["cost_score"], item["id"]))
        for rank, item in enumerate(ranked, start=1):
            item["rank"] = rank
        base_winner = base_safe[0]["id"] if base_safe else None
        adjusted_winner = ranked[0]["id"] if ranked else None
        adjusted_candidates = [
            item for item in candidates if item["memory_adjustment"]
        ]
        memory_ids = sorted(
            {
                str(memory_id)
                for item in adjusted_candidates
                for memory_id in item["memory_ids"]
            }
        )
        applied = bool(adjusted_candidates)
        adjustment = (
            int(adjusted_candidates[0]["memory_adjustment"])
            if adjusted_candidates
            else 0
        )
        ranking_changed = bool(
            base_winner and adjusted_winner and base_winner != adjusted_winner
        )
        ranking_summary = (
            f"Memory changed the deterministic ranking from {base_winner} to {adjusted_winner}."
            if ranking_changed
            else "Memory adjusted candidate scores without changing the deterministic ranking winner."
            if applied
            else "No applicable prior outcome changed the deterministic ranking."
        )
        influence = {
            "applied": applied,
            "applied_to_ranking": applied,
            "changed_selection": ranking_changed,
            "ranking_changed": ranking_changed,
            "memory_id": memory_ids[0] if memory_ids else None,
            "memory_ids": memory_ids,
            "adjustment": adjustment,
            "base_winner_id": base_winner,
            "base_ranking_winner_id": base_winner,
            "memory_adjusted_ranking_winner_id": adjusted_winner,
            "memory_adjusted_candidate_ids": [
                item["id"] for item in adjusted_candidates
            ],
            "context_signature": disruption_context.get("signature"),
            "context_event_class": disruption_context.get("event_class"),
            "screened_out_dominated_ids": [
                item["id"] for item in safe if item.get("dominated")
            ],
            "ranking_summary": ranking_summary,
            "summary": ranking_summary,
        }
        return ranked, influence

    def _local_model_choice(
        self,
        candidates: list[dict[str, Any]],
        handoffs: list[dict[str, Any]],
    ) -> tuple[str | None, str, str]:
        fallback = candidates[0]["id"] if candidates else None
        if not self.enable_llm or not candidates:
            return fallback, "deterministic_safe_planner", "Local model disabled; used the lowest-cost candidate from the context-bounded safe shortlist."
        prompt = {
            "role": "airport recovery coordinator",
            "instruction": (
                "Select exactly one candidate_id from the context-bounded safe shortlist. "
                "cost_score is a cost and LOWER IS BETTER. You may choose a higher-cost "
                "candidate only for its graph-backed recovered-minutes versus burden tradeoff. "
                "Return JSON with candidate_id and rationale. Do not invent IDs or actions."
            ),
            "model_candidate_shortlist": [
                {
                    "candidate_id": item["id"],
                    "strategy": item["strategy"],
                    "cost_score": item.get("cost_score", item.get("score")),
                    "projected_delay": item["projected_delay"],
                    "recovered_minutes": item.get("recovered_minutes", 0),
                    "operational_burden_minutes": item.get("burden_minutes", 0),
                    "context_penalty": item.get("context_penalty", 0),
                    "context_event_class": item.get("context_event_class"),
                    "benefit_components": item.get("benefit_components", {}),
                    "burden_components": item.get("burden_components", {}),
                    "actions": [
                        {
                            "type": action.get("type"),
                            "target_id": action.get("target_id"),
                            "affected_flight_id": action.get("affected_flight_id"),
                        }
                        for action in item.get("actions", [])
                    ],
                }
                for item in candidates
            ],
            "specialist_handoffs": [
                {
                    "from": item["from_agent"],
                    "query": item.get("query_name"),
                    "summary": item["summary"],
                    "evidence_ids": item.get("evidence_ids", []),
                }
                for item in handoffs
            ],
        }
        body = _json({"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0, "seed": 7}, "messages": [{"role": "system", "content": "You select only from a server-validated, context-bounded safe shortlist. Never execute actions."}, {"role": "user", "content": _json(prompt)}]}).encode("utf-8")
        request = urllib.request.Request(self.ollama_url + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.ollama_timeout) as response:
                raw = json.loads(response.read(1_000_000).decode("utf-8"))
            content = raw.get("message", {}).get("content", "{}")
            choice = json.loads(content) if isinstance(content, str) else content
            candidate_id = str(choice.get("candidate_id", ""))
            safe_ids = {item["id"] for item in candidates}
            if candidate_id not in safe_ids:
                raise ValueError("model selected an unknown candidate")
            selected = next(item for item in candidates if item["id"] == candidate_id)
            summary = (
                f"Qwen selected {candidate_id} from the context-bounded safe shortlist: "
                f"cost {selected.get('cost_score', selected.get('score'))}, "
                f"{selected.get('recovered_minutes', 0)} projected recovery minutes, "
                f"{selected.get('burden_minutes', 0)} modeled coordination minutes."
            )
            return candidate_id, "local_model", summary
        except Exception as exc:
            return fallback, "deterministic_fallback", f"Local model unavailable or invalid ({type(exc).__name__}); used the safe deterministic ranking."

    def _replan_locked(self, trigger: str) -> dict[str, Any]:
        state = self._scenario_row()
        existing_plan = self._current_plan()
        if existing_plan and int(existing_plan.get("based_on_version", -1)) != state["state_version"]:
            self.backend.query(
                "MATCH (p:Plan) WHERE p.is_current = true SET p.is_current = false"
            )
            self.backend.query(
                "MATCH (p:Plan) WHERE p.status = 'PROPOSED' AND p.is_current = false "
                "SET p.status = 'STALE'"
            )
            existing_plan = None
        cycle = state["cycle"] + 1
        state_version = state["state_version"]
        self.backend.query(
            "MATCH (s:Scenario {id: $id}) SET s.cycle = $cycle, "
            "s.last_checkpoint_minute = s.clock_minute, s.status = 'REPLANNING'",
            {"id": self.scenario_id, "cycle": cycle},
        )
        flights, events, resources = self._flights(), self._events(), self._resources()
        active_events = [event for event in events if event["status"] == "ACTIVE"]
        policies = self._policies()
        memories = self._memories()
        disruption_context = self._disruption_context(
            active_events, flights, policies
        )
        decision_token = str(time.time_ns())
        findings, handoffs = self._record_specialists(
            cycle=cycle,
            decision_token=decision_token,
            state_version=state_version,
            flights=flights,
            events=events,
            resources=resources,
            policies=policies,
            memories=memories,
            disruption_context=disruption_context,
        )
        self.backend.query("MATCH (a:Agent {id: 'AGENT-COORD'}) SET a.status = 'COORDINATING', a.current_task = 'Reading specialist handoffs', a.last_cycle = $cycle", {"cycle": cycle})
        policy_index = self._policy_index(policies)
        active_delay = sum(int(event["delay_minutes"]) for event in active_events)
        last_applied = self._last_applied_minute()
        high_risk_event = any(event.get("severity") == "HIGH" for event in active_events)
        churn_blocked = bool(
            last_applied is not None
            and state["clock_minute"] - last_applied < 10
            and not high_risk_event
        )
        actions = self._candidate_actions(
            cycle=cycle,
            decision_token=decision_token,
            flights=flights,
            resources=resources,
            active_events=active_events,
            policy_index=policy_index,
        )
        ranked, influence = self._rank_candidates(
            cycle=cycle,
            active_delay=active_delay,
            actions=actions,
            memories=memories,
            active_events=active_events,
            disruption_context=disruption_context,
            clock_minute=state["clock_minute"],
            flights=flights,
            resources=resources,
            churn_blocked=churn_blocked,
        )
        model_cost_window = 4 if disruption_context.get("event_class") == "TURNAROUND" else 8
        best_cost = int(ranked[0]["cost_score"]) if ranked else 0
        model_candidates = [
            item
            for item in ranked
            if int(item["cost_score"]) <= best_cost + model_cost_window
        ]
        influence["model_eligible_candidate_ids"] = [
            item["id"] for item in model_candidates
        ]
        influence["model_cost_window"] = model_cost_window
        pending_unchanged = bool(
            existing_plan
            and existing_plan.get("status") == "PROPOSED"
            and int(existing_plan.get("based_on_version", -1)) == state_version
        )
        no_action = active_delay < self.minimum_benefit or churn_blocked or pending_unchanged
        if no_action:
            selected_id, decision_source, model_summary = None, "guardrail", "No model call was needed for this NO_CHANGE cycle."
        else:
            selected_id, decision_source, model_summary = self._local_model_choice(model_candidates, handoffs)
        selected = next((item for item in ranked if item["id"] == selected_id), None)
        now = _utc_now()
        episode_id = f"EPISODE-{cycle}-{decision_token}"
        status = "NO_ACTION_REQUIRED" if no_action else "PLAN_PROPOSED" if selected else "ESCALATED"
        summary = (
            "Specialists recorded NO_CHANGE because the current safe proposal is still valid and awaits execution."
            if pending_unchanged
            else "Specialists recorded NO_CHANGE because projected benefit is below the guardrail."
            if active_delay < self.minimum_benefit
            else "Specialists recorded NO_CHANGE because the execution churn guard is active."
            if no_action
            else model_summary
            if selected
            else "No candidate has complete current policy and knowledge authorization."
        )
        self._create_node("DecisionEpisode", {"id": episode_id, "cycle": cycle, "trigger": trigger, "status": status, "summary": summary, "based_on_version": state_version, "created_at": now, "disruption_signature": disruption_context["signature"], "event_class": disruption_context["event_class"], "context_json": disruption_context})
        coord_run_id = f"RUN-{cycle}-{decision_token}-COORD"
        self._create_node("AgentRun", {"id": coord_run_id, "agent_id": "AGENT-COORD", "cycle": cycle, "status": "COMPLETED", "based_on_version": state_version, "decision_source": decision_source, "started_at": now, "ended_at": _utc_now(), "created_at": now})
        self._create_edge("AGENT-COORD", "STARTED", coord_run_id)
        self._create_edge(episode_id, "ANALYZED", self.scenario_id)
        self._create_edge(episode_id, "USED_RUN", coord_run_id)
        for handoff in handoffs:
            self.backend.query("MATCH (h:Handoff {id: $id}) SET h.status = 'CONSUMED', h.consumed_at = $at", {"id": handoff["id"], "at": _utc_now()})
            self._create_edge(episode_id, "CONSUMED_HANDOFF", handoff["id"])

        self._record_tool_call(
            episode_id=episode_id,
            coordinator_run_id=coord_run_id,
            cycle=cycle,
            sequence=1,
            name="inspect_disruption",
            arguments={"state_version": state_version, "status": "ACTIVE"},
            evidence_ids=disruption_context["event_ids"],
            result_summary=f"Read {len(active_events)} active event(s) from FalkorDB.",
        )
        self._record_tool_call(
            episode_id=episode_id,
            coordinator_run_id=coord_run_id,
            cycle=cycle,
            sequence=2,
            name="get_current_policy",
            arguments={"state_version": state_version},
            evidence_ids=disruption_context["policy_ids"],
            result_summary=f"Read {len(policies)} current policy version(s).",
        )
        self._record_tool_call(
            episode_id=episode_id,
            coordinator_run_id=coord_run_id,
            cycle=cycle,
            sequence=3,
            name="recall_episode",
            arguments={"context_signature": disruption_context["signature"], "event_class": disruption_context["event_class"]},
            evidence_ids=influence.get("memory_ids", []),
            result_summary=f"Found {len(influence.get('memory_ids', []))} context-matched successful episode(s).",
        )
        self._record_tool_call(
            episode_id=episode_id,
            coordinator_run_id=coord_run_id,
            cycle=cycle,
            sequence=4,
            name="validate_candidate",
            arguments={"candidate_ids": [item["id"] for item in ranked], "state_version": state_version},
            evidence_ids=[
                check["id"]
                for item in ranked
                for check in item.get("safety_checks", [])
                if check.get("passed")
            ],
            result_summary=f"Produced {len(ranked)} non-dominated, graph-valid candidate(s).",
        )

        if no_action:
            self.backend.query("MATCH (s:Scenario {id: $scenario}) SET s.status = 'MONITORING'", {"scenario": self.scenario_id})
            self.backend.query("MATCH (a:Agent) SET a.status = 'MONITORING', a.current_task = 'Watching shared FalkorDB state'")
            self.backend.query("MATCH (a:Agent {id: 'AGENT-COORD'}) SET a.last_run_id = $run", {"run": coord_run_id})
            return {"episode_id": episode_id, "status": status, "plan": None, "influence": influence, "findings": findings}

        if selected is None:
            self.backend.query("MATCH (s:Scenario {id: $scenario}), (a:Agent {id: 'AGENT-COORD'}) SET s.status = 'NEEDS_REVIEW', a.status = 'ESCALATED', a.current_task = 'Policy authorization incomplete'", {"scenario": self.scenario_id})
            return {"episode_id": episode_id, "status": status, "plan": None, "influence": influence, "findings": findings}

        self.backend.query("MATCH (p:Plan) WHERE p.is_current = true SET p.is_current = false")
        self.backend.query("MATCH (p:Plan) WHERE p.status = 'PROPOSED' AND p.is_current = false SET p.status = 'SUPERSEDED'")
        plan_id = f"PLAN-{cycle}-{decision_token}-{selected['strategy']}"
        policy_evidence = []
        for action in selected["actions"]:
            policy = policy_index.get(action["type"])
            policy_evidence.append({
                "action_id": action["id"], "action_type": action["type"],
                "authorized": bool(action.get("authorized")),
                "knowledge_current": bool(action.get("knowledge_current")),
                "policy_id": policy.get("id") if policy else None,
                "title": policy.get("title") if policy else None,
                "version": policy.get("version") if policy else None,
                "owner": policy.get("owner") if policy else None,
                "owner_team_id": policy.get("owner_team_id") if policy else None,
                "responsible_role_id": policy.get("responsible_role_id") if policy else None,
                "source": policy.get("source") if policy else None,
                "document_id": policy.get("document_id") if policy else None,
                "source_section": policy.get("source_section") if policy else None,
                "source_excerpt": policy.get("source_excerpt") if policy else None,
                "procedure_id": policy.get("procedure_id") if policy else None,
                "status": policy.get("status") if policy else None,
                "freshness_status": policy.get("freshness_status") if policy else None,
            })
        auto_policy = policy_index.get("APPLY_VALIDATED_PLAN")
        auto_knowledge_current = bool(
            auto_policy
            and auto_policy.get("status") == "CURRENT"
            and auto_policy.get("freshness_status") == "CURRENT_FOR_REPLAY"
        )
        auto_authorized = bool(self.auto_apply and auto_knowledge_current)
        plan = {
            "id": plan_id, "candidate_id": selected["id"], "status": "PROPOSED",
            "episode_id": episode_id,
            "strategy": selected["strategy"], "summary": summary,
            "based_on_version": state_version, "cycle": cycle, "selected_by": decision_source,
            "cost_score": selected["cost_score"],
            "base_cost_score": selected["base_cost_score"],
            "memory_adjustment": selected["memory_adjustment"],
            "baseline_delay": active_delay,
            "projected_delay": selected["projected_delay"], "recovered_minutes": selected["recovered_minutes"],
            "actions": selected["actions"],
            "candidate_tradeoff": {
                "benefit_components": selected.get("benefit_components", {}),
                "burden_minutes": selected.get("burden_minutes", 0),
                "burden_components": selected.get("burden_components", {}),
                "context_penalty": selected.get("context_penalty", 0),
                "context_event_class": selected.get("context_event_class"),
                "cost_direction": "LOWER_IS_BETTER",
            },
            "safe_candidate_set": [
                {
                    "id": item["id"],
                    "strategy": item["strategy"],
                    "cost_score": item["cost_score"],
                    "projected_delay": item["projected_delay"],
                    "recovered_minutes": item["recovered_minutes"],
                    "burden_minutes": item["burden_minutes"],
                    "context_penalty": item.get("context_penalty", 0),
                    "action_types": [action["type"] for action in item["actions"]],
                }
                for item in ranked
            ],
            "model_candidate_set": [
                {
                    "id": item["id"],
                    "strategy": item["strategy"],
                    "cost_score": item["cost_score"],
                    "context_penalty": item.get("context_penalty", 0),
                }
                for item in model_candidates
            ],
            "disruption_context": disruption_context,
            "policy_authorization": {
                "complete": all(
                    item["authorized"] and item["knowledge_current"]
                    for item in policy_evidence
                ),
                "current_knowledge_complete": all(
                    item["knowledge_current"] for item in policy_evidence
                ),
                "evidence": policy_evidence,
            },
            "memory_influence": influence,
            "requires_human_approval": not auto_authorized,
            "automation": {
                "mode": "BOUNDED_AUTOPILOT" if auto_authorized else "SUPERVISED",
                "auto_apply_authorized": auto_authorized,
                "knowledge_current": auto_knowledge_current,
                "policy_id": auto_policy.get("id") if auto_policy else None,
                "procedure_id": auto_policy.get("procedure_id") if auto_policy else None,
                "owner": auto_policy.get("owner") if auto_policy else None,
                "source": auto_policy.get("source") if auto_policy else None,
                "source_section": auto_policy.get("source_section") if auto_policy else None,
                "source_excerpt": auto_policy.get("source_excerpt") if auto_policy else None,
                "freshness_status": auto_policy.get("freshness_status") if auto_policy else None,
                "scope": "Modeled replay only; never connected to live airport systems.",
            },
            "safety_validation": {"passed": all(check["passed"] for check in selected["safety_checks"]), "checks": selected["safety_checks"], "validator": "deterministic_safety_validator"},
            "created_at": now,
            "causal_context": {
                "event_ids": disruption_context["event_ids"],
                "action_ids": [action["id"] for action in selected["actions"]],
            },
            "causal_path": {"summary": "Building graph evidence.", "nodes": [], "edges": []},
        }
        self._create_node("Plan", {"id": plan_id, "cycle": cycle, "strategy": selected["strategy"], "status": "PROPOSED", "based_on_version": state_version, "cost_score": selected["cost_score"], "projected_delay": selected["projected_delay"], "is_current": True, "payload_json": _json(plan), "context_json": disruption_context, "created_at": now})
        self._create_edge(episode_id, "SELECTED", plan_id)
        self._persist_action_graph(plan_id, selected["actions"], policy_index, now)
        plan["causal_path"] = self._graph_causal_path(
            disruption_context["event_ids"],
            [action["id"] for action in selected["actions"]],
        )
        plan = _normalize_plan_payload(plan)
        influence = plan["memory_influence"]
        self.backend.query(
            "MATCH (p:Plan {id: $id}) SET p.payload_json = $payload",
            {"id": plan_id, "payload": _json(plan)},
        )
        model_run_id = f"MODEL-{cycle}-{decision_token}"
        self._create_node(
            "ModelRun",
            {
                "id": model_run_id,
                "provider": "ollama" if decision_source == "local_model" else "deterministic",
                "model": self.model if decision_source == "local_model" else "safe-planner",
                "selected_candidate_id": selected["id"],
                "allowlisted_candidate_ids": [item["id"] for item in model_candidates],
                "cost_direction": "LOWER_IS_BETTER",
                "decision_source": decision_source,
                "summary": summary,
                "created_at": now,
            },
        )
        self._create_edge(coord_run_id, "RAN_MODEL", model_run_id)
        self._create_edge(episode_id, "USED_MODEL", model_run_id)
        self._create_edge(model_run_id, "SELECTED", plan_id)
        for memory_id in influence.get("memory_ids", []):
            self._create_edge(episode_id, "RECALLED", memory_id)
        self.backend.query("MATCH (s:Scenario {id: $scenario}), (a:Agent {id: 'AGENT-COORD'}) SET s.status = 'AWAITING_APPROVAL', a.status = 'AWAITING_APPROVAL', a.current_task = 'Safe plan ready for human review', a.last_run_id = $run", {"scenario": self.scenario_id, "run": coord_run_id})
        if auto_authorized and plan["safety_validation"]["passed"]:
            self.apply(plan_id, state_version)
            return {"episode_id": episode_id, "status": "AUTO_APPLIED", "plan": self._current_plan(), "influence": influence, "findings": findings}
        return {"episode_id": episode_id, "status": status, "plan": plan, "influence": influence, "findings": findings}

    def _persist_action_graph(
        self,
        plan_id: str,
        actions: Iterable[Mapping[str, Any]],
        policy_index: Mapping[str, Mapping[str, Any]],
        created_at: str,
    ) -> None:
        """Persist the exact relationships later returned by the evidence view."""

        for action_value in actions:
            action = dict(action_value)
            self._create_node(
                "Action",
                {**action, "plan_id": plan_id, "status": "VALIDATED", "created_at": created_at},
            )
            self._create_edge(plan_id, "PROPOSES", str(action["id"]))
            affected_flight = str(action.get("affected_flight_id", ""))
            if affected_flight:
                self._create_edge(affected_flight, "RECOVERED_BY", str(action["id"]))
            action_type = str(action.get("type", ""))
            target_id = str(action.get("target_id", ""))
            if target_id:
                relationship = {
                    "RESEQUENCE_DEPARTURE": "USES_RESOURCE",
                    "REASSIGN_GATE": "USES_RESOURCE",
                    "RESEQUENCE_TASKS": "REORDERS",
                    "PROTECT_ROTATION": "PROTECTS",
                }.get(action_type)
                if relationship:
                    self._create_edge(str(action["id"]), relationship, target_id)
            if action.get("policy_id"):
                self._create_edge(
                    str(action["id"]), "AUTHORIZED_BY", str(action["policy_id"])
                )
                aligned = policy_index.get(action_type, {}).get("procedure_id")
                if aligned:
                    self._create_edge(str(action["id"]), "SUPPORTED_BY", str(aligned))

    @staticmethod
    def _path_node_type(labels: Iterable[str]) -> str:
        label_set = set(labels)
        order = (
            ("OperationalEvent", "EVENT"),
            ("Flight", "FLIGHT"),
            ("Action", "ACTION"),
            ("Runway", "RUNWAY"),
            ("Gate", "GATE"),
            ("Turnaround", "TURNAROUND"),
            ("TurnaroundTask", "TASK"),
            ("Policy", "POLICY"),
            ("ProcedureVersion", "PROCEDURE"),
            ("SourceDocument", "DOCUMENT"),
            ("Team", "TEAM"),
            ("Role", "ROLE"),
        )
        return next((kind for label, kind in order if label in label_set), "EVIDENCE")

    def _graph_causal_path(
        self, event_ids: Iterable[str], action_ids: Iterable[str]
    ) -> dict[str, Any]:
        """Build the visible path exclusively from relationships in FalkorDB."""

        event_ids = [str(item) for item in event_ids]
        action_ids = [str(item) for item in action_ids]
        anchors = set(event_ids + action_ids)
        if not anchors:
            return {
                "summary": "No graph-backed disruption path is active.",
                "nodes": [],
                "edges": [],
            }
        allowed = [
            "AFFECTS", "CONSTRAINS", "DELAYS", "USES", "ROTATES_TO",
            "FEEDS", "PREPARES", "HAS_TASK", "MUST_COMPLETE_BEFORE",
            "RECOVERED_BY", "USES_RESOURCE", "REORDERS", "PROTECTS",
            "AUTHORIZED_BY", "SUPPORTED_BY", "ALIGNED_WITH", "IN_DOCUMENT",
            "OWNED_BY", "RESPONSIBLE_ROLE",
        ]
        rows = self.backend.query(
            "MATCH (a)-[r]->(b) WHERE type(r) IN $types "
            "RETURN a.id, labels(a), a.callsign, a.summary, a.label, a.title, "
            "a.name, a.status, a.source_kind, type(r), b.id, labels(b), "
            "b.callsign, b.summary, b.label, b.title, b.name, b.status, b.source_kind",
            {"types": allowed},
        ).result_set
        raw_edges: list[tuple[str, str, str]] = []
        node_data: dict[str, dict[str, Any]] = {}

        def add_node(offset: int, row: list[Any] | tuple[Any, ...]) -> None:
            node_id = str(row[offset])
            labels = list(row[offset + 1] or [])
            if "Action" in labels and node_id not in action_ids:
                return
            if "OperationalEvent" in labels and node_id not in event_ids:
                return
            candidates = row[offset + 2 : offset + 7]
            node_data[node_id] = {
                "id": node_id,
                "label": next((str(value) for value in candidates if value), node_id),
                "type": self._path_node_type(labels),
                "status": row[offset + 7],
                "source_kind": row[offset + 8] or "DERIVED",
            }

        for row in rows:
            source = str(row[0])
            target = str(row[10])
            source_labels = set(row[1] or [])
            target_labels = set(row[11] or [])
            if "Action" in source_labels and source not in action_ids:
                continue
            if "Action" in target_labels and target not in action_ids:
                continue
            if "OperationalEvent" in source_labels and source not in event_ids:
                continue
            if "OperationalEvent" in target_labels and target not in event_ids:
                continue
            add_node(0, row)
            add_node(10, row)
            raw_edges.append((source, target, str(row[9])))

        selected_edges: set[tuple[str, str, str]] = set()
        event_relationships = {"AFFECTS", "CONSTRAINS", "DELAYS"}
        action_relationships = {
            "USES_RESOURCE", "REORDERS", "PROTECTS", "AUTHORIZED_BY", "SUPPORTED_BY"
        }
        for edge in raw_edges:
            source, target, relationship = edge
            if source in event_ids and relationship in event_relationships:
                selected_edges.add(edge)
            elif source in action_ids and relationship in action_relationships:
                selected_edges.add(edge)
            elif target in action_ids and relationship == "RECOVERED_BY":
                selected_edges.add(edge)

        relevant_flights = {
            node_id
            for edge in selected_edges
            for node_id in edge[:2]
            if node_data.get(node_id, {}).get("type") == "FLIGHT"
        }
        for edge in raw_edges:
            source, target, relationship = edge
            if relationship == "ROTATES_TO" and (
                source in relevant_flights or target in relevant_flights
            ):
                selected_edges.add(edge)
                relevant_flights.update((source, target))

        relevant_resources = {
            target
            for source, target, relationship in selected_edges
            if relationship in {"CONSTRAINS", "USES_RESOURCE"}
        }
        for edge in raw_edges:
            source, target, relationship = edge
            if (
                relationship == "USES"
                and source in relevant_flights
                and target in relevant_resources
            ):
                selected_edges.add(edge)

        relevant_turnarounds = {
            target
            for source, target, relationship in selected_edges
            if relationship == "REORDERS"
        }
        relevant_tasks = {
            target
            for source, target, relationship in selected_edges
            if relationship == "DELAYS"
        }
        for edge in raw_edges:
            source, target, relationship = edge
            if relationship == "MUST_COMPLETE_BEFORE" and (
                source in relevant_tasks or target in relevant_tasks
            ):
                selected_edges.add(edge)
                relevant_tasks.update((source, target))
        for edge in raw_edges:
            source, target, relationship = edge
            if (
                relationship == "HAS_TASK"
                and source in relevant_turnarounds
                and target in relevant_tasks
            ) or (
                relationship in {"FEEDS", "PREPARES"}
                and (
                    source in relevant_flights
                    or target in relevant_flights
                    or source in relevant_turnarounds
                    or target in relevant_turnarounds
                )
                and (source in relevant_turnarounds or target in relevant_turnarounds)
            ):
                selected_edges.add(edge)

        relevant_policies = {
            target
            for source, target, relationship in selected_edges
            if relationship == "AUTHORIZED_BY"
        }
        relevant_procedures = {
            target
            for source, target, relationship in selected_edges
            if relationship == "SUPPORTED_BY"
        }
        for edge in raw_edges:
            source, target, relationship = edge
            if (
                source in relevant_policies
                and relationship == "ALIGNED_WITH"
                and target in relevant_procedures
            ):
                selected_edges.add(edge)
        for edge in raw_edges:
            source, _target, relationship = edge
            if source in relevant_procedures and relationship in {
                "IN_DOCUMENT", "OWNED_BY", "RESPONSIBLE_ROLE"
            }:
                selected_edges.add(edge)

        reached = set(anchors)
        for source, target, _relationship in selected_edges:
            reached.update((source, target))
        edges = [
            {"source": source, "target": target, "type": relationship}
            for source, target, relationship in sorted(selected_edges)
            if source in reached and target in reached
        ]
        nodes = [node_data[node_id] for node_id in sorted(reached) if node_id in node_data]
        return {
            "summary": (
                "Every visible hop was read from FalkorDB: disruption → operational "
                "dependencies → selected actions → current authority."
            ),
            "nodes": nodes,
            "edges": edges,
            "event_ids": event_ids,
            "action_ids": action_ids,
        }

    def _record_tool_call(
        self,
        *,
        episode_id: str,
        coordinator_run_id: str,
        cycle: int,
        sequence: int,
        name: str,
        arguments: Mapping[str, Any],
        evidence_ids: Iterable[str],
        result_summary: str,
    ) -> str:
        tool_id = f"TOOL-{cycle}-{sequence}-{time.time_ns()}"
        now = _utc_now()
        self._create_node(
            "ToolCall",
            {
                "id": tool_id,
                "name": name,
                "transport": "MCP_COMPATIBLE_GRAPH_TOOL",
                "arguments_json": dict(arguments),
                "evidence_ids": list(evidence_ids),
                "result_summary": result_summary,
                "status": "COMPLETED",
                "sequence": sequence,
                "cycle": cycle,
                "created_at": now,
            },
        )
        self._create_edge(coordinator_run_id, "INVOKED", tool_id)
        self._create_edge(episode_id, "USED_TOOL", tool_id)
        return tool_id

    def analyze(self, trigger: str = "MANUAL_REPLAN") -> dict[str, Any]:
        with self._lock:
            self._replan_locked(str(trigger)[:80])
            return self.snapshot()

    def validate_candidate(
        self, candidate_id: str, expected_state_version: int
    ) -> dict[str, Any]:
        """Recompute every apply guard from current graph state.

        Plan payloads retain the checks that were true when a candidate was
        created.  This method deliberately does not trust those stored booleans:
        the replay clock, current policy graph, knowledge freshness, and churn
        window are read again immediately before execution.
        """

        with self._lock:
            state = self._scenario_row()
            plan = self._current_plan()
            candidate_id = str(candidate_id)
            expected = int(expected_state_version)
            candidate_matches = bool(
                plan and str(plan.get("candidate_id", "")) == candidate_id
            )
            version_matches = bool(
                plan
                and expected == state["state_version"]
                and int(plan.get("based_on_version", -1)) == state["state_version"]
            )
            proposal_is_current = bool(
                plan and plan.get("status") == "PROPOSED" and candidate_matches
            )

            actions = list(plan.get("actions", [])) if plan else []
            policies = {item["id"]: item for item in self._policies()}
            policy_complete = bool(actions) and all(
                (
                    policy := policies.get(str(action.get("policy_id", "")))
                )
                and action.get("type") in policy.get("action_types", [])
                and policy.get("status") == "CURRENT"
                for action in actions
            )
            knowledge_current = bool(actions) and all(
                (
                    policy := policies.get(str(action.get("policy_id", "")))
                )
                and policy.get("freshness_status") == "CURRENT_FOR_REPLAY"
                for action in actions
            )

            flights = self._flights()
            frozen_flights = {
                str(flight["id"])
                for flight in flights
                if flight["kind"] == "OUTBOUND"
                and state["clock_minute"]
                <= int(flight["scheduled_minute"])
                <= state["clock_minute"] + self.execution_freeze
            }
            freeze_clear = bool(actions) and not any(
                str(action.get("target_id", "")) in frozen_flights
                for action in actions
            )
            benefit_clear = bool(
                plan
                and int(plan.get("recovered_minutes", 0)) >= self.minimum_benefit
            )

            last_applied = self._last_applied_minute()
            active_events = [
                event for event in self._events() if event["status"] == "ACTIVE"
            ]
            high_risk_event = any(
                event.get("severity") == "HIGH" for event in active_events
            )
            churn_clear = not (
                last_applied is not None
                and state["clock_minute"] - last_applied < 10
                and not high_risk_event
            )

            target_ids = {
                str(action.get("target_id"))
                for action in actions
                if action.get("target_id")
            }
            targets_exist = bool(target_ids)
            for target_id in target_ids:
                rows = self.backend.query(
                    "MATCH (n {id: $id}) RETURN count(n)", {"id": target_id}
                ).result_set
                if not rows or int(rows[0][0]) < 1:
                    targets_exist = False
                    break
            graph_preconditions = [
                self._action_graph_precondition(action) for action in actions
            ]
            targets_exist = targets_exist and bool(graph_preconditions) and all(
                item[0] for item in graph_preconditions
            )

            checks = [
                {
                    "id": "CURRENT_GRAPH_STATE",
                    "passed": proposal_is_current and version_matches,
                    "detail": "The current proposal and every specialist result use the live graph state version.",
                },
                {
                    "id": "CURRENT_POLICY_AUTHORITY",
                    "passed": policy_complete,
                    "detail": "Every action is still linked to a current policy that authorizes its action type.",
                },
                {
                    "id": "CURRENT_KNOWLEDGE",
                    "passed": knowledge_current,
                    "detail": "Every action is still backed by knowledge marked CURRENT_FOR_REPLAY.",
                },
                {
                    "id": "TARGETS_EXIST",
                    "passed": targets_exist,
                    "detail": "Every proposed action still has its target and required FalkorDB relationship.",
                    "graph_preconditions": [
                        {
                            "passed": item[0],
                            "detail": item[1],
                            "evidence_ids": item[2],
                        }
                        for item in graph_preconditions
                    ],
                },
                {
                    "id": "EXECUTION_FREEZE",
                    "passed": freeze_clear,
                    "detail": f"No action targets an outbound flight inside the {self.execution_freeze}-minute execution freeze.",
                    "protected_flight_ids": sorted(frozen_flights),
                },
                {
                    "id": "MINIMUM_BENEFIT",
                    "passed": benefit_clear,
                    "detail": f"Projected recovery remains at least {self.minimum_benefit} minutes.",
                },
                {
                    "id": "CHURN_GUARD",
                    "passed": churn_clear,
                    "detail": "No conflicting plan was applied inside the ten-minute churn window, unless a high-risk event is active.",
                },
            ]
            return {
                "candidate_id": candidate_id,
                "state_version": state["state_version"],
                "expected_state_version": expected,
                "version_matches": version_matches,
                "candidate_matches": candidate_matches,
                "passed": all(check["passed"] for check in checks),
                "checks": checks,
                "evidence_ids": [
                    check["id"] for check in checks if check["passed"]
                ],
            }

    def tick(self, *, minutes: int = 1, to_minute: int | None = None) -> dict[str, Any]:
        with self._lock:
            state = self._scenario_row()
            old = state["clock_minute"]
            if to_minute is None:
                amount = max(1, min(60, int(minutes)))
                target = old + amount
            else:
                target = int(to_minute)
                if target < old:
                    raise ValueError("Replay clock cannot move backward; reset the scenario")
            target = min(target, state["end_minute"])
            while True:
                live = self._scenario_row()
                cursor = live["clock_minute"]
                pending = [
                    event
                    for event in self._events()
                    if event["status"] == "PENDING"
                    and cursor < event["at_minute"] <= target
                ]
                next_event_minute = min(
                    (event["at_minute"] for event in pending), default=None
                )
                next_checkpoint = live["last_checkpoint_minute"] + self.planning_interval
                boundaries = [
                    value
                    for value in (next_event_minute, next_checkpoint)
                    if value is not None and cursor < value <= target
                ]
                if not boundaries:
                    break
                boundary = min(boundaries)
                crossed = [
                    event for event in pending if event["at_minute"] == boundary
                ]
                self.backend.query(
                    "MATCH (s:Scenario {id: $id}) SET s.clock_minute = $clock",
                    {"id": self.scenario_id, "clock": boundary},
                )
                if crossed:
                    for event in crossed:
                        self.backend.query(
                            "MATCH (e:OperationalEvent {id: $event}) "
                            "SET e.status = 'ACTIVE', e.processed = true",
                            {"event": event["id"]},
                        )
                        if event.get("flight_id"):
                            self.backend.query(
                                "MATCH (f:Flight {id: $flight}) "
                                "SET f.delay_minutes = f.delay_minutes + $delay, "
                                "f.estimated_minute = f.estimated_minute + $delay, "
                                "f.status = 'DELAYED'",
                                {
                                    "flight": event["flight_id"],
                                    "delay": event["delay_minutes"],
                                },
                            )
                        if event.get("type") == "BAG_RECONCILIATION_DELAY":
                            self.backend.query(
                                "MATCH (:OperationalEvent {id: $event})-[:DELAYS]->"
                                "(task:TurnaroundTask)<-[:HAS_TASK]-(turn:Turnaround) "
                                "SET task.duration_minutes = task.duration_minutes + $delay, "
                                "task.status = 'DELAYED', "
                                "turn.current_critical_path_minutes = "
                                "turn.current_critical_path_minutes + $delay, "
                                "turn.status = 'DELAYED'",
                                {
                                    "event": event["id"],
                                    "delay": event["delay_minutes"],
                                },
                            )
                    self.backend.query(
                        "MATCH (s:Scenario {id: $id}) "
                        "SET s.state_version = s.state_version + 1, "
                        "s.status = 'DISRUPTION_DETECTED'",
                        {"id": self.scenario_id},
                    )
                    self._replan_locked(
                        "EVENT:" + ",".join(event["id"] for event in crossed)
                    )
                else:
                    self._replan_locked("PERIODIC_CHECKPOINT")
            self.backend.query(
                "MATCH (s:Scenario {id: $id}) SET s.clock_minute = $clock",
                {"id": self.scenario_id, "clock": target},
            )
            return self.snapshot()

    def apply(self, plan_id: str, expected_version: int) -> dict[str, Any]:
        with self._lock:
            state = self._scenario_row()
            current = self._current_plan()
            if current is None or current["id"] != plan_id:
                raise RuntimeError("Unknown, superseded, or stale plan; run replanning again")
            if current["status"] == "APPLIED":
                return self.snapshot()
            validation = self.validate_candidate(
                str(current.get("candidate_id", "")), expected_version
            )
            failed = {
                item["id"]
                for item in validation["checks"]
                if not item.get("passed")
            }
            if "CURRENT_GRAPH_STATE" in failed:
                raise RuntimeError("Graph state changed; this proposal is stale")
            if "CURRENT_POLICY_AUTHORITY" in failed:
                raise RuntimeError(
                    "Current policy evidence is incomplete; plan was not applied"
                )
            if "CURRENT_KNOWLEDGE" in failed:
                raise RuntimeError(
                    "Current knowledge evidence is stale; plan was not applied"
                )
            if failed:
                raise RuntimeError(
                    "Safety validation failed before apply: "
                    + ", ".join(sorted(failed))
                )
            current["safety_validation"] = {
                "passed": True,
                "checks": validation["checks"],
                "validator": "deterministic_safety_validator",
                "validated_at": _utc_now(),
                "validated_against_version": state["state_version"],
            }
            now = _utc_now()
            new_version = state["state_version"] + 1
            self.backend.query(
                "MATCH (p:Plan {id: $plan}) SET p.status = 'APPLIED', "
                "p.applied_at_minute = $minute, p.applied_at = $at",
                {"plan": plan_id, "minute": state["clock_minute"], "at": now},
            )
            self.backend.query("MATCH (:Plan {id: $plan})-[:PROPOSES]->(a:Action) SET a.status = 'EXECUTED'", {"plan": plan_id})
            self.backend.query("MATCH (e:OperationalEvent) WHERE e.status = 'ACTIVE' SET e.status = 'MITIGATED'")
            reduction = int(current.get("recovered_minutes", 0))
            for action in current.get("actions", []):
                action_type = str(action.get("type", ""))
                target_id = str(action.get("target_id", ""))
                affected_flight_id = str(action.get("affected_flight_id", ""))
                if action_type == "REASSIGN_GATE":
                    self.backend.query(
                        "MATCH (f:Flight {id: $flight}), (g:Gate {id: $gate}) "
                        "SET f.gate = g.id, g.status = 'RESERVED'",
                        {"flight": affected_flight_id, "gate": target_id},
                    )
                elif action_type == "RESEQUENCE_DEPARTURE":
                    self.backend.query(
                        "MATCH (f:Flight {id: $flight}) SET f.runway = $runway, "
                        "f.sequence_status = 'RESEQUENCED'",
                        {"flight": affected_flight_id, "runway": target_id},
                    )
                elif action_type == "RESEQUENCE_TASKS":
                    self.backend.query(
                        "MATCH (t:Turnaround {id: $turn})-[:HAS_TASK]->"
                        "(task:TurnaroundTask) SET "
                        "t.current_critical_path_minutes = CASE "
                        "WHEN t.current_critical_path_minutes - $recovered < "
                        "t.baseline_critical_path_minutes THEN t.baseline_critical_path_minutes "
                        "ELSE t.current_critical_path_minutes - $recovered END, "
                        "t.status = 'RECOVERING', "
                        "task.duration_minutes = CASE WHEN task.status = 'DELAYED' "
                        "THEN task.baseline_duration_minutes ELSE task.duration_minutes END, "
                        "task.status = CASE WHEN task.status = 'DELAYED' "
                        "THEN 'RESEQUENCED' ELSE task.status END",
                        {"turn": target_id, "recovered": reduction},
                    )
                elif action_type == "PROTECT_ROTATION":
                    self.backend.query(
                        "MATCH (f:Flight {id: $flight}) SET f.protection_status = 'PROTECTED'",
                        {"flight": target_id},
                    )
            affected = self.backend.query("MATCH (f:Flight) WHERE f.delay_minutes > 0 RETURN f.id, f.delay_minutes ORDER BY f.delay_minutes DESC").result_set
            remaining = reduction
            for flight_id, delay in affected:
                recovered = min(int(delay), remaining)
                remaining -= recovered
                new_delay = int(delay) - recovered
                self.backend.query(
                    "MATCH (f:Flight {id: $id}) SET f.delay_minutes = $delay, "
                    "f.estimated_minute = f.estimated_minute - $recovered, f.status = $status",
                    {
                        "id": flight_id,
                        "delay": new_delay,
                        "recovered": recovered,
                        "status": "RECOVERED" if new_delay <= 0 else "RECOVERING",
                    },
                )
                if remaining <= 0:
                    break
            self.backend.query("MATCH (s:Scenario {id: $id}) SET s.state_version = $version, s.status = 'MONITORING'", {"id": self.scenario_id, "version": new_version})
            outcome_id = f"OUTCOME-{current['cycle']}-{int(time.time() * 1000)}"
            memory_id = f"MEMORY-{current['cycle']}-{int(time.time() * 1000)}"
            context = dict(current.get("disruption_context", {}))
            self._create_node("Outcome", {"id": outcome_id, "plan_id": plan_id, "status": "SUCCESS", "projected_delay": current["projected_delay"], "benefit_minutes": current["recovered_minutes"], "event_class": context.get("event_class"), "context_json": context, "created_at": now, "source_kind": "MODELED"})
            self._create_node("Memory", {"id": memory_id, "strategy": current["strategy"], "disruption_signature": context.get("signature"), "event_class": context.get("event_class"), "context_json": context, "flight_ids": context.get("flight_ids", []), "rotation_ids": context.get("rotation_ids", []), "resource_ids": context.get("resource_ids", []), "policy_ids": context.get("policy_ids", []), "benefit_minutes": current["recovered_minutes"], "confidence": "SIMULATED", "created_at": now, "source_kind": "DERIVED"})
            self._create_edge(plan_id, "RESULTED_IN", outcome_id)
            self._create_edge(memory_id, "REMEMBERS", outcome_id)
            self._create_edge("AGENT-COORD", "STORED", memory_id)
            current.update({"status": "APPLIED", "state_version": new_version, "applied_at": now, "outcome_id": outcome_id, "memory_id": memory_id})
            self.backend.query("MATCH (p:Plan {id: $id}) SET p.payload_json = $payload", {"id": plan_id, "payload": _json(current)})
            self.backend.query("MATCH (d:DecisionEpisode)-[:SELECTED]->(:Plan {id: $plan}) SET d.status = 'APPLIED'", {"plan": plan_id})
            self.backend.query("MATCH (a:Agent) SET a.status = 'MONITORING', a.current_task = 'Watching shared FalkorDB state'")
            return self.snapshot()

    def supersede_policy(
        self,
        policy_id: str,
        *,
        replacement: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replace a policy version and immediately invalidate stale proposals."""

        with self._lock:
            rows = self.backend.query(
                "MATCH (p:Policy {id: $id}) WHERE p.current = true RETURN "
                "p.title, p.version, p.owner, p.source, p.action_types, p.rule, "
                "p.procedure_id, p.document_id, p.source_section, p.source_excerpt, "
                "p.owner_team_id, p.responsible_role_id, p.document_path, "
                "p.document_version, p.document_status, p.effective_from, "
                "p.freshness_status",
                {"id": policy_id},
            ).result_set
            if not rows:
                raise ValueError("Unknown current policy")
            row = rows[0]
            actions = json.loads(row[4]) if isinstance(row[4], str) else list(row[4] or [])
            new = dict(replacement or {})
            new_id = str(new.get("id", f"{policy_id}-NEXT"))
            new.update({
                "id": new_id,
                "title": new.get("title", row[0]),
                "version": new.get("version", f"{row[1]}+1"),
                "owner": new.get("owner", row[2]),
                "source": new.get("source", f"{row[3]} · superseding bulletin"),
                "action_types": new.get("action_types", actions),
                "rule": new.get("rule", row[5]),
                "procedure_id": new.get("procedure_id", row[6]),
                "document_id": new.get("document_id", row[7]),
                "source_section": new.get("source_section", row[8]),
                "source_excerpt": new.get("source_excerpt", row[9]),
                "owner_team_id": new.get("owner_team_id", row[10]),
                "responsible_role_id": new.get("responsible_role_id", row[11]),
                "document_path": new.get("document_path", row[12]),
                "document_version": new.get("document_version", row[13]),
                "document_status": new.get("document_status", row[14]),
                "effective_from": new.get("effective_from", row[15]),
                "freshness_status": new.get("freshness_status", row[16]),
                "status": "CURRENT",
                "current": True,
                "source_kind": "MODELED",
            })
            self.backend.query("MATCH (p:Policy {id: $id}) SET p.current = false, p.status = 'SUPERSEDED'", {"id": policy_id})
            self._create_node("Policy", new)
            self._create_edge(new_id, "SUPERSEDES", policy_id)
            self._create_edge(str(self.scenario_data.get("airport", "ORD")), "GOVERNED_BY", new_id)
            if new.get("procedure_id"):
                self._create_edge(new_id, "ALIGNED_WITH", str(new["procedure_id"]))
            if new.get("document_id"):
                self._create_edge(new_id, "IN_DOCUMENT", str(new["document_id"]))
            if new.get("owner_team_id"):
                self._create_edge(new_id, "OWNED_BY", str(new["owner_team_id"]))
            if new.get("responsible_role_id"):
                self._create_edge(new_id, "RESPONSIBLE_ROLE", str(new["responsible_role_id"]))
            state = self._scenario_row()
            self.backend.query("MATCH (s:Scenario {id: $id}) SET s.state_version = s.state_version + 1, s.status = 'POLICY_CHANGED'", {"id": self.scenario_id})
            self._replan_locked(f"POLICY_SUPERSEDED:{policy_id}")
            return self.snapshot()

    def set_autonomy(self, enabled: bool) -> dict[str, Any]:
        """Change the server-side replay execution authority at runtime."""

        with self._lock:
            self.auto_apply = bool(enabled)
            self.backend.query(
                "MATCH (s:Scenario {id: $id}) SET s.auto_apply = $enabled",
                {"id": self.scenario_id, "enabled": self.auto_apply},
            )
            plan = self._current_plan()
            if plan and plan.get("status") == "PROPOSED":
                automation = dict(plan.get("automation", {}))
                knowledge_current = bool(automation.get("knowledge_current"))
                automation["auto_apply_authorized"] = bool(
                    self.auto_apply and knowledge_current
                )
                automation["mode"] = (
                    "BOUNDED_AUTOPILOT"
                    if automation["auto_apply_authorized"]
                    else "SUPERVISED"
                )
                plan["automation"] = automation
                plan["requires_human_approval"] = not automation[
                    "auto_apply_authorized"
                ]
                self.backend.query(
                    "MATCH (p:Plan {id: $id}) SET p.payload_json = $payload",
                    {"id": plan["id"], "payload": _json(plan)},
                )
                if automation["auto_apply_authorized"]:
                    validation = self.validate_candidate(
                        str(plan.get("candidate_id", "")),
                        int(plan.get("based_on_version", -1)),
                    )
                    if validation["passed"]:
                        return self.apply(
                            str(plan["id"]), int(plan["based_on_version"])
                        )
            return self.snapshot()

    def reset(self, *, preserve_memory: bool = True) -> dict[str, Any]:
        with self._lock:
            lineages = self._memory_lineages(limit=100) if preserve_memory else []
            handoff_lineages = (
                self._episode_handoff_lineages() if preserve_memory else []
            )
            coordinator_traces = (
                self._applied_episode_coordinator_traces()
                if preserve_memory
                else []
            )
            retained = self._memories(limit=100) if preserve_memory and not lineages else []
            self._seed()
            policy_index = self._policy_index(self._policies())
            restored_episode_ids: set[str] = set()
            for lineage in reversed(lineages):
                episode = lineage["episode"]
                plan_record = lineage["plan"]
                outcome = lineage["outcome"]
                memory = lineage["memory"]
                plan_payload = dict(plan_record["payload"])
                self._create_node(
                    "DecisionEpisode",
                    {
                        **episode,
                        "archived_replay": True,
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_node(
                    "Plan",
                    {
                        "id": plan_record["id"],
                        "cycle": plan_record["cycle"],
                        "strategy": plan_record["strategy"],
                        "status": "APPLIED",
                        "based_on_version": plan_record["based_on_version"],
                        "cost_score": plan_record["cost_score"],
                        "projected_delay": plan_record["projected_delay"],
                        "is_current": False,
                        "payload_json": plan_payload,
                        "created_at": plan_record["created_at"],
                        "applied_at": plan_record["applied_at"],
                        "archived_replay": True,
                    },
                )
                self._create_node(
                    "Outcome",
                    {
                        "id": outcome["id"], "plan_id": plan_record["id"],
                        "status": outcome["status"],
                        "projected_delay": outcome["projected_delay"],
                        "benefit_minutes": outcome["benefit_minutes"],
                        "event_class": memory["event_class"],
                        "context_json": memory["context"],
                        "source_kind": "MODELED", "created_at": outcome["created_at"],
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_node(
                    "Memory",
                    {
                        "id": memory["id"], "strategy": memory["strategy"],
                        "disruption_signature": memory["disruption_signature"],
                        "event_class": memory["event_class"],
                        "context_json": memory["context"],
                        "flight_ids": memory["flight_ids"],
                        "rotation_ids": memory["rotation_ids"],
                        "resource_ids": memory["resource_ids"],
                        "policy_ids": memory["policy_ids"],
                        "benefit_minutes": memory["benefit_minutes"],
                        "confidence": memory["confidence"],
                        "created_at": memory["created_at"],
                        "source_kind": "DERIVED",
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_edge(episode["id"], "SELECTED", plan_record["id"])
                self._create_edge(plan_record["id"], "RESULTED_IN", outcome["id"])
                self._create_edge(memory["id"], "REMEMBERS", outcome["id"])
                self._create_edge("AGENT-COORD", "STORED", memory["id"])
                restored_episode_ids.add(str(episode["id"]))
                self._persist_action_graph(
                    str(plan_record["id"]),
                    plan_payload.get("actions", []),
                    policy_index,
                    str(plan_record["created_at"] or _utc_now()),
                )
            for lineage in handoff_lineages:
                episode_id = str(lineage["episode_id"])
                if episode_id not in restored_episode_ids:
                    continue
                run = lineage["run"]
                finding = lineage["finding"]
                handoff = lineage["handoff"]
                self._create_node(
                    "AgentRun",
                    {
                        **run,
                        "archived_replay": True,
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_node(
                    "Finding",
                    {
                        **finding,
                        "archived_replay": True,
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_node(
                    "Handoff",
                    {
                        **handoff,
                        "archived_replay": True,
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_edge(
                    str(handoff["from_agent"]), "STARTED", str(run["id"])
                )
                self._create_edge(
                    str(run["id"]), "PRODUCED", str(finding["id"])
                )
                self._create_edge(
                    str(finding["id"]), "PACKAGED_AS", str(handoff["id"])
                )
                self._create_edge(
                    str(handoff["id"]),
                    "DELIVERED_TO",
                    str(handoff["to_agent"]),
                )
                self._create_edge(
                    episode_id, "CONSUMED_HANDOFF", str(handoff["id"])
                )
            for trace in coordinator_traces:
                episode_id = str(trace["episode_id"])
                if episode_id not in restored_episode_ids:
                    continue
                run = dict(trace["run"])
                run_id = str(run["id"])
                self._create_node(
                    "AgentRun",
                    {
                        **run,
                        "archived_replay": True,
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_edge("AGENT-COORD", "STARTED", run_id)
                self._create_edge(episode_id, "USED_RUN", run_id)
                for tool_value in trace["tools"]:
                    tool = dict(tool_value)
                    tool_id = str(tool["id"])
                    self._create_node(
                        "ToolCall",
                        {
                            **tool,
                            "archived_replay": True,
                            "restored_after_replay_reset": True,
                        },
                    )
                    self._create_edge(run_id, "INVOKED", tool_id)
                    self._create_edge(episode_id, "USED_TOOL", tool_id)
                if trace["model"]:
                    model = dict(trace["model"])
                    model_id = str(model["id"])
                    self._create_node(
                        "ModelRun",
                        {
                            **model,
                            "archived_replay": True,
                            "restored_after_replay_reset": True,
                        },
                    )
                    self._create_edge(run_id, "RAN_MODEL", model_id)
                    self._create_edge(episode_id, "USED_MODEL", model_id)
                    self._create_edge(
                        model_id, "SELECTED", str(trace["plan_id"])
                    )
            for item in reversed(retained):
                outcome_id = str(item["outcome_id"])
                self._create_node(
                    "Outcome",
                    {
                        "id": outcome_id,
                        "status": item["outcome_status"],
                        "benefit_minutes": item["benefit_minutes"],
                        "source_kind": "MODELED",
                        "created_at": item["created_at"],
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_node(
                    "Memory",
                    {
                        "id": item["id"],
                        "strategy": item["strategy"],
                        "disruption_signature": item["disruption_signature"],
                        "event_class": item.get("event_class"),
                        "context_json": item.get("context", {}),
                        "flight_ids": item.get("flight_ids", []),
                        "rotation_ids": item.get("rotation_ids", []),
                        "resource_ids": item.get("resource_ids", []),
                        "policy_ids": item.get("policy_ids", []),
                        "benefit_minutes": item["benefit_minutes"],
                        "confidence": item["confidence"],
                        "created_at": item["created_at"],
                        "restored_after_replay_reset": True,
                    },
                )
                self._create_edge(str(item["id"]), "REMEMBERS", outcome_id)
                self._create_edge("AGENT-COORD", "STORED", str(item["id"]))
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._scenario_row()
            flights = self._flights()
            events = self._events()
            resources = self._resources()
            live_plan = self._current_plan()
            plan = live_plan or self._latest_applied_plan()
            memories = self._memories()
            policies = self._policies()
            history = self._history()
            active_delay = sum(event["delay_minutes"] for event in events if event["status"] == "ACTIVE")
            observed_condition_count = sum(
                1
                for event in events
                if event["processed"]
                and event["source_kind"] in {"OBSERVED", "MIXED"}
                and event.get("observed_fields")
            )
            modeled_effect_delay = sum(
                event["delay_minutes"]
                for event in events
                if event["processed"]
                and event.get("effect_source_kind", "MODELED") == "MODELED"
            )
            applied_count_rows = self.backend.query("MATCH (p:Plan) WHERE p.status = 'APPLIED' RETURN count(p)").result_set
            proposed_count_rows = self.backend.query("MATCH (p:Plan) RETURN count(p)").result_set
            applied_count = int(applied_count_rows[0][0]) if applied_count_rows else 0
            proposed_count = int(proposed_count_rows[0][0]) if proposed_count_rows else 0
            next_event = next((event["at_minute"] for event in events if event["status"] == "PENDING"), None)
            influence = plan.get("memory_influence") if plan else {"applied": False, "changed_selection": False, "summary": "No current plan."}
            node_rows = self.backend.query("MATCH (n) RETURN count(n)").result_set
            edge_rows = self.backend.query("MATCH ()-[r]->() RETURN count(r)").result_set
            node_count = int(node_rows[0][0]) if node_rows else 0
            edge_count = int(edge_rows[0][0]) if edge_rows else 0
            policy_complete = bool(plan and plan.get("policy_authorization", {}).get("complete"))
            return {
                "ok": True,
                "scenario": {key: value for key, value in state.items() if key not in {"clock_minute", "last_checkpoint_minute", "cycle", "state_version"}},
                "clock": {
                    "minute": state["clock_minute"],
                    "label": _clock(state["base_clock_minute"] + state["clock_minute"]),
                    "iso": f"{state['date']}T{_clock(state['base_clock_minute'] + state['clock_minute'])}:00",
                    "speed": 1,
                    "playing": False,
                    "can_play": state["clock_minute"] < state["end_minute"],
                    "cycle": state["cycle"],
                    "next_event_minute": next_event,
                    "next_event_label": _clock(state["base_clock_minute"] + next_event) if next_event is not None else None,
                },
                "state_version": state["state_version"],
                "flights": flights,
                "resources": resources,
                "events": events,
                "agents": self._agents(),
                "handoffs": self._handoffs(),
                "tool_trace": self._tool_trace(),
                "model_trace": self._model_trace(),
                "causal_path": plan.get("causal_path") if plan else {"summary": "Waiting for the next disruption checkpoint.", "nodes": [], "edges": []},
                "current_plan": plan,
                "memory": {"episode_count": len(memories), "recalled": memories, "influence": influence},
                "policies": {"authorization_complete": policy_complete, "applicable": policies},
                "provenance": {
                    "records": self._provenance_records(),
                    "contract": "Observed and modeled fields are separated at record level.",
                },
                "metrics": {
                    "observed_condition_count": observed_condition_count,
                    "modeled_effect_delay_minutes": modeled_effect_delay,
                    "total_introduced_delay_minutes": modeled_effect_delay,
                    "active_delay_minutes": active_delay,
                    "baseline_delay_minutes": (
                        int(plan.get("baseline_delay", active_delay))
                        if plan
                        else active_delay
                    ),
                    "projected_delay_minutes": plan.get("projected_delay") if plan else active_delay,
                    "recovered_minutes": plan.get("recovered_minutes", 0) if plan else 0,
                    "plans_proposed": proposed_count,
                    "plans_applied": applied_count,
                },
                "replanning": {
                    "status": state["status"],
                    "automatic": True,
                    "auto_apply": self.auto_apply,
                    "minimal_intervention": (
                        "Six agents scan every five replay minutes; validated in-scope plans may auto-apply under current policy."
                        if self.auto_apply
                        else "Six agents monitor and replan automatically; a human approves operational actions."
                    ),
                    "interval_minutes": self.planning_interval,
                    "execution_freeze_minutes": self.execution_freeze,
                    "minimum_benefit_minutes": self.minimum_benefit,
                    "specialist_execution": {
                        "count": len(SPECIALISTS),
                        "mode": "concurrent",
                        "coordination": "graph_persisted_handoffs",
                    },
                    "cycle": state["cycle"],
                    "last_trigger": history[0]["trigger"] if history else None,
                    "last_checkpoint_minute": state["last_checkpoint_minute"],
                    "next_checkpoint_minute": min(state["end_minute"], state["last_checkpoint_minute"] + self.planning_interval),
                    "history": history,
                },
                "graph": {"name": self.backend.graph_name, "runtime_mode": self.backend.mode, "endpoint": self.backend.endpoint, "node_count": node_count, "edge_count": edge_count, "central_context_layer": True},
            }

    def company_brain(self, question: str = "") -> dict[str, Any]:
        """Answer bounded operations questions from explicit knowledge paths."""

        with self._lock:
            rows = self.backend.query(
                "MATCH (d) WHERE d:SourceDocument OR d:Incident OR d:ShiftHandoff "
                "RETURN d.id, labels(d), d.title, d.version, d.status, d.path, "
                "d.owner_team_id, d.responsible_role_id, d.created_at, d.fresh_until "
                "ORDER BY d.id"
            ).result_set
            artifacts = [
                {
                    "id": row[0],
                    "type": (
                        "SOP"
                        if "SourceDocument" in list(row[1])
                        else "INCIDENT"
                        if "Incident" in list(row[1])
                        else "SHIFT_HANDOFF"
                    ),
                    "title": row[2],
                    "version": row[3],
                    "status": row[4],
                    "path": f"knowledge/{row[5]}" if row[5] else None,
                    "owner_team_id": row[6],
                    "responsible_role_id": row[7],
                    "created_at": row[8],
                    "fresh_until": row[9],
                    "truth_label": "MODELED",
                }
                for row in rows
            ]
            policies = self._policies()
            all_citations = [
                {
                    "policy_id": item["id"],
                    "procedure_id": item.get("procedure_id"),
                    "title": item["title"],
                    "version": item["version"],
                    "owner": item["owner"],
                    "owner_team_id": item.get("owner_team_id"),
                    "responsible_role_id": item.get("responsible_role_id"),
                    "document_id": item.get("document_id"),
                    "document_path": (
                        f"knowledge/{item['document_path']}"
                        if item.get("document_path")
                        else None
                    ),
                    "source_section": item.get("source_section"),
                    "excerpt": item.get("source_excerpt"),
                    "freshness_status": item.get("freshness_status"),
                    "authorized_action_types": item["action_types"],
                }
                for item in policies
            ]
            normalized = " ".join((question or "").lower().split())
            answer_type = "INSUFFICIENT_EVIDENCE"
            answer = (
                "The bounded company graph does not contain enough evidence to answer that question. "
                "Try asking about gate ownership, the open incident, current policy, or superseded policy history."
            )
            citations: list[dict[str, Any]] = []
            evidence_ids: list[str] = []
            graph_path: list[str] = []

            if any(word in normalized for word in ("incident", "open issue", "ticket")):
                incident_rows = self.backend.query(
                    "MATCH (i:Incident)-[:REFERENCES]->(e:OperationalEvent) "
                    "OPTIONAL MATCH (i)-[:AFFECTS]->(f:Flight) "
                    "OPTIONAL MATCH (i)-[:OWNED_BY]->(team:Team) "
                    "OPTIONAL MATCH (i)-[:RESPONSIBLE_ROLE]->(role:Role) "
                    "RETURN i.id, i.title, i.status, i.created_at, i.fresh_until, "
                    "e.id, e.summary, e.status, f.id, f.rotation_id, team.id, "
                    "team.name, role.id, role.name ORDER BY i.created_at DESC"
                ).result_set
                if incident_rows:
                    row = incident_rows[0]
                    answer_type = "INCIDENT_STATUS"
                    answer = (
                        f"{row[0]} is {str(row[2]).lower()}: {row[1]}. It references "
                        f"{row[5]} ({str(row[7]).lower()}) and affects {row[8] or 'no linked flight'}"
                        f" on rotation {row[9] or 'not recorded'}. {row[11] or row[10]} owns it, "
                        f"with {row[13] or row[12]} responsible. The record is fresh through {row[4]}."
                    )
                    evidence_ids = [str(value) for value in (row[0], row[5], row[8], row[10], row[12]) if value]
                    citations = [
                        {
                            "id": row[0],
                            "title": row[1],
                            "status": row[2],
                            "excerpt": row[6],
                            "event_id": row[5],
                            "flight_id": row[8],
                            "rotation_id": row[9],
                            "owner_team_id": row[10],
                            "owner_team": row[11],
                            "responsible_role_id": row[12],
                            "responsible_role": row[13],
                            "fresh_until": row[4],
                            "freshness_status": "CURRENT_FOR_REPLAY",
                        }
                    ]
                    graph_path = [
                        "Incident-[:REFERENCES]->OperationalEvent",
                        "Incident-[:AFFECTS]->Flight",
                        "Incident-[:OWNED_BY]->Team",
                        "Incident-[:RESPONSIBLE_ROLE]->Role",
                    ]
            elif any(word in normalized for word in ("supersed", "old policy", "history", "previous version")):
                history_rows = self.backend.query(
                    "MATCH (new)-[:SUPERSEDES]->(old) "
                    "WHERE (new:Policy AND old:Policy) OR "
                    "(new:ProcedureVersion AND old:ProcedureVersion) OR "
                    "(new:SourceDocument AND old:SourceDocument) "
                    "RETURN new.id, new.title, new.version, new.status, old.id, "
                    "old.title, old.version, old.status ORDER BY new.id"
                ).result_set
                if history_rows:
                    answer_type = "POLICY_HISTORY"
                    def version_label(node_id: Any, version: Any) -> str:
                        if version not in (None, ""):
                            return str(version)
                        identifier = str(node_id or "")
                        return identifier.rsplit(":", 1)[-1] if ":" in identifier else "unversioned"
                    phrases = [
                        f"{row[0]} v{version_label(row[0], row[2])} ({row[3]}) supersedes "
                        f"{row[4]} v{version_label(row[4], row[6])} ({row[7]})"
                        for row in history_rows
                    ]
                    answer = "The graph preserves version history: " + "; ".join(phrases) + "."
                    evidence_ids = sorted(
                        {str(value) for row in history_rows for value in (row[0], row[4])}
                    )
                    citations = all_citations
                    graph_path = ["CurrentVersion-[:SUPERSEDES]->PriorVersion"]
            elif any(word in normalized for word in ("who", "owner", "owns", "authorize", "authorise", "responsible", "where written", "gate reassignment")):
                if any(word in normalized for word in ("gate", "reassign")):
                    action_type = "REASSIGN_GATE"
                elif any(word in normalized for word in ("turn", "rotation", "bag")):
                    action_type = "PROTECT_ROTATION"
                elif any(word in normalized for word in ("flow", "runway", "sequence")):
                    action_type = "RESEQUENCE_DEPARTURE"
                elif any(word in normalized for word in ("automatic", "apply", "recovery")):
                    action_type = "APPLY_VALIDATED_PLAN"
                else:
                    action_type = None
                policy = (
                    next(
                        (item for item in policies if action_type in item["action_types"]),
                        None,
                    )
                    if action_type
                    else None
                )
                if policy:
                    owner_rows = self.backend.query(
                        "MATCH (team:Team {id: $team}), (role:Role {id: $role}) "
                        "RETURN team.name, role.name",
                        {
                            "team": policy.get("owner_team_id"),
                            "role": policy.get("responsible_role_id"),
                        },
                    ).result_set
                    team_name, role_name = (
                        owner_rows[0] if owner_rows else (policy.get("owner"), policy.get("responsible_role_id"))
                    )
                    answer_type = "OWNERSHIP_AND_AUTHORITY"
                    answer = (
                        f"{policy.get('owner')} is the named policy owner for {action_type}; "
                        f"{team_name} is the owner team and {role_name} is the responsible role. Current policy "
                        f"{policy['id']} v{policy['version']} authorizes it through procedure "
                        f"{policy.get('procedure_id')} in {policy.get('document_id')}, section "
                        f"{policy.get('source_section')}. {policy.get('source_excerpt')}"
                    )
                    citations = [
                        item for item in all_citations if item["policy_id"] == policy["id"]
                    ]
                    evidence_ids = [
                        str(value)
                        for value in (
                            policy["id"], policy.get("procedure_id"),
                            policy.get("document_id"), policy.get("owner_team_id"),
                            policy.get("responsible_role_id"),
                        )
                        if value
                    ]
                    graph_path = [
                        "Policy-[:ALIGNED_WITH]->ProcedureVersion",
                        "ProcedureVersion-[:IN_DOCUMENT]->SourceDocument",
                        "Policy-[:OWNED_BY]->Team",
                        "ProcedureVersion-[:RESPONSIBLE_ROLE]->Role",
                    ]
            elif not normalized or any(word in normalized for word in ("current", "policy", "rules", "procedure")):
                answer_type = "CURRENT_POLICY_SET"
                citations = all_citations
                evidence_ids = [item["policy_id"] for item in citations]
                answer = (
                    f"{len(citations)} current policy versions authorize the bounded replay. "
                    "Each one is linked to a current procedure, exact source section, owner team, and responsible role."
                )
                graph_path = [
                    "Action-[:AUTHORIZED_BY]->Policy",
                    "Policy-[:ALIGNED_WITH]->ProcedureVersion",
                    "ProcedureVersion-[:IN_DOCUMENT]->SourceDocument",
                    "Policy-[:OWNED_BY]->Team",
                ]
            return {
                "ok": True,
                "question": question
                or "Which current rules authorize the recovery, who owns them, and what sources support them?",
                "answer": answer,
                "answer_type": answer_type,
                "insufficient_evidence": answer_type == "INSUFFICIENT_EVIDENCE",
                "truth_label": "MODELED_SYNTHETIC_COMPANY_KNOWLEDGE",
                "citations": citations,
                "artifacts": artifacts,
                "evidence_ids": evidence_ids,
                "graph_path": graph_path,
            }

    def health(self) -> dict[str, Any]:
        started = time.monotonic()
        snapshot = self.snapshot()
        return {
            "ok": True,
            "status": "healthy",
            "runtime_mode": self.backend.mode,
            "graph": self.backend.graph_name,
            "endpoint": self.backend.endpoint,
            "state_version": snapshot["state_version"],
            "cycle": snapshot["clock"]["cycle"],
            "node_count": snapshot["graph"]["node_count"],
            "edge_count": snapshot["graph"]["edge_count"],
            "llm": {"enabled": self.enable_llm, "provider": "ollama", "model": self.model, "timeout_seconds": self.ollama_timeout, "fallback": "deterministic_safe_planner"},
            "agents": {
                "specialist_count": len(SPECIALISTS),
                "execution": "concurrent",
                "coordination": "FalkorDB handoffs",
            },
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
