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
    choice_summary = (
        f"The local model made the final allowlisted choice: {final_candidate}."
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
        "candidate_was_allowlisted": bool(final_candidate),
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

        procedure_by_action = self._seed_knowledge_pack()
        action_aliases = {
            "RESEQUENCE_DEPARTURE": "ASSIGN_CAPACITY_SLOT",
            "REASSIGN_GATE": "ASSIGN_GATE",
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
            if incident.get("event_id"):
                self._create_edge(incident_id, "REFERENCES", str(incident["event_id"]))
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
            }
            for row in rows
        ]

    def _events(self) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (e:OperationalEvent) RETURN e.id, e.at_minute, e.type, e.severity, "
            "e.flight_id, e.resource_id, e.delay_minutes, e.summary, e.source_kind, "
            "e.effect_source_kind, e.status, e.processed, e.observed_fields, "
            "e.modeled_fields ORDER BY e.at_minute, e.id"
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
            }
            for row in rows
        ]

    def _resources(self) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (r) WHERE r:Runway OR r:Gate RETURN r.id, labels(r), r.label, "
            "r.status, r.terminal, r.capacity_per_hour, r.role, r.source_kind, "
            "r.observed_fields, r.modeled_fields ORDER BY r.id"
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
            "o.id, o.status ORDER BY m.created_at DESC"
        ).result_set[:limit]
        return [
            {
                "id": row[0], "strategy": row[1], "disruption_signature": row[2],
                "benefit_minutes": int(row[3] or 0), "confidence": row[4],
                "created_at": row[5], "outcome_id": row[6], "outcome_status": row[7],
            }
            for row in rows
        ]

    def _handoffs(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.backend.query(
            "MATCH (h:Handoff) RETURN h.id, h.from_agent, h.to_agent, h.status, "
            "h.summary, h.evidence_ids, h.state_version, h.cycle, h.created_at "
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
                }
            )
        return handoffs

    def _current_plan(self) -> dict[str, Any] | None:
        rows = self.backend.query(
            "MATCH (p:Plan) WHERE p.is_current = true RETURN p.payload_json, p.status, "
            "p.based_on_version ORDER BY p.created_at DESC"
        ).result_set
        if not rows:
            return None
        payload = _normalize_plan_payload(json.loads(rows[0][0]))
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

    @staticmethod
    def _specialist_job(
        agent_id: str,
        flights: list[dict[str, Any]],
        events: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        policies: list[dict[str, Any]],
        memories: list[dict[str, Any]],
    ) -> dict[str, Any]:
        active = [event for event in events if event["status"] == "ACTIVE"]
        delayed = [flight for flight in flights if flight["delay_minutes"] > 0]
        if agent_id == "AGENT-WEATHER":
            weather = [event for event in active if event["type"] in {"LOW_VISIBILITY_SNOW", "WEATHER_UPDATE", "WEATHER", "WIND"}]
            observed = [
                event
                for event in weather
                if event["source_kind"] in {"OBSERVED", "MIXED"}
                and event.get("observed_fields")
            ]
            summary = f"Weather read {len(observed)} observed active update(s); {len(weather)} weather condition(s) are visible at this replay time."
            return {"agent_id": agent_id, "severity": "HIGH" if any(item["severity"] == "HIGH" for item in weather) else "WATCH", "summary": summary, "recommendation": "Use only weather observations available at the current replay clock.", "action_type": "RESEQUENCE_DEPARTURE", "evidence_ids": [event["id"] for event in weather]}
        if agent_id == "AGENT-FLOW":
            flow_events = [event for event in active if event["type"] in {"RUNWAY_CAPACITY", "RUNWAY_OCCUPANCY_CHECK", "LOW_VISIBILITY_SNOW", "WEATHER_UPDATE", "WEATHER", "WIND"}]
            runway_ids = [item["id"] for item in resources if item["type"] == "RUNWAY"]
            summary = (
                f"Flow checked {len(runway_ids)} runways and {len(flow_events)} active capacity constraint(s); "
                f"{len(delayed)} flight(s) currently carry delay."
            )
            return {"agent_id": agent_id, "severity": "HIGH" if flow_events else "WATCH", "summary": summary, "recommendation": "Resequence departures inside the modeled runway envelope.", "action_type": "SEQUENCE_DEPARTURES", "evidence_ids": [event["id"] for event in flow_events] + runway_ids}
        if agent_id == "AGENT-GATE":
            gates = [item["id"] for item in resources if item["type"] == "GATE" and item["status"] in {"OPEN", "AVAILABLE"}]
            summary = f"Gate checked {len(gates)} open positions against {len(delayed)} delayed flight(s)."
            return {"agent_id": agent_id, "severity": "MEDIUM" if delayed else "WATCH", "summary": summary, "recommendation": "Keep a compatible recovery gate available for the affected rotation.", "action_type": "ASSIGN_GATE", "evidence_ids": [flight["id"] for flight in delayed] + gates}
        if agent_id == "AGENT-POLICY":
            covered = sorted({action for policy in policies for action in policy.get("action_types", [])})
            summary = f"Policy verified {len(policies)} current version(s) covering {len(covered)} authorized action type(s)."
            return {"agent_id": agent_id, "severity": "CLEAR" if policies else "HIGH", "summary": summary, "recommendation": "Permit only actions linked to a current version and named owner.", "action_type": "APPLY_VALIDATED_PLAN", "evidence_ids": [policy["id"] for policy in policies]}
        if agent_id == "AGENT-MEMORY":
            successful = [item for item in memories if item.get("outcome_status") == "SUCCESS"]
            summary = f"Memory recalled {len(successful)} successful related outcome(s) from FalkorDB."
            return {"agent_id": agent_id, "severity": "INFORMED" if successful else "NEW", "summary": summary, "recommendation": "Apply only outcome-backed score adjustments and retain the base ranking for audit.", "action_type": "APPLY_VALIDATED_PLAN", "evidence_ids": [item["id"] for item in successful]}
        bag_events = [event for event in active if event["type"] in {"BAGGAGE_DELAY", "BAG_RECONCILIATION_DELAY", "TURNAROUND_DELAY"}]
        rotations = sorted({str(flight["rotation_id"]) for flight in delayed})
        summary = f"Turnaround traced {len(bag_events)} active ground delay(s) across {len(rotations)} affected rotation(s)."
        return {"agent_id": agent_id, "severity": "HIGH" if bag_events else "WATCH", "summary": summary, "recommendation": "Protect the next leg with a bounded turnaround recovery team.", "action_type": "ACCELERATE_TURNAROUND", "evidence_ids": [event["id"] for event in bag_events] + rotations}

    def _record_specialists(
        self,
        *,
        cycle: int,
        state_version: int,
        flights: list[dict[str, Any]],
        events: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        policies: list[dict[str, Any]],
        memories: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self.backend.query(
            "MATCH (a:Agent) WHERE a.id <> 'AGENT-COORD' SET a.status = 'ANALYZING', "
            "a.current_task = 'Reading shared graph state'"
        )
        started_at = _utc_now()
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="groundgraph-agent") as pool:
            futures = [
                pool.submit(
                    self._specialist_job,
                    agent_id,
                    deepcopy(flights),
                    deepcopy(events),
                    deepcopy(resources),
                    deepcopy(policies),
                    deepcopy(memories),
                )
                for agent_id, _name, _role in SPECIALISTS
            ]
            findings = [future.result(timeout=5) for future in futures]
        handoffs: list[dict[str, Any]] = []
        for finding in findings:
            agent_id = str(finding["agent_id"])
            short = agent_id.removeprefix("AGENT-")
            run_id = f"RUN-{cycle}-{short}"
            finding_id = f"FINDING-{cycle}-{short}"
            handoff_id = f"HANDOFF-{cycle}-{short}-COORD"
            created_at = _utc_now()
            self._create_node("AgentRun", {"id": run_id, "agent_id": agent_id, "cycle": cycle, "status": "COMPLETED", "based_on_version": state_version, "started_at": started_at, "ended_at": created_at, "created_at": created_at})
            self._create_node("Finding", {"id": finding_id, "agent_id": agent_id, "cycle": cycle, "severity": finding["severity"], "summary": finding["summary"], "recommendation": finding["recommendation"], "action_type": finding["action_type"], "evidence_ids": finding["evidence_ids"], "based_on_version": state_version, "created_at": created_at})
            self._create_node("Handoff", {"id": handoff_id, "from_agent": agent_id, "to_agent": "AGENT-COORD", "status": "AVAILABLE", "summary": finding["summary"], "evidence_ids": finding["evidence_ids"], "state_version": state_version, "cycle": cycle, "created_at": created_at})
            self._create_edge(agent_id, "STARTED", run_id)
            self._create_edge(run_id, "PRODUCED", finding_id)
            self._create_edge(finding_id, "PACKAGED_AS", handoff_id)
            self._create_edge(handoff_id, "DELIVERED_TO", "AGENT-COORD")
            self.backend.query("MATCH (a:Agent {id: $id}) SET a.status = 'WAITING', a.current_task = 'Handoff delivered', a.last_run_id = $run, a.last_cycle = $cycle", {"id": agent_id, "run": run_id, "cycle": cycle})
            handoffs.append({"id": handoff_id, "from_agent": agent_id, "to_agent": "AGENT-COORD", "status": "AVAILABLE", "summary": finding["summary"], "evidence_ids": list(finding["evidence_ids"]), "state_version": state_version, "cycle": cycle, "created_at": created_at})
        return findings, handoffs

    @staticmethod
    def _policy_index(policies: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for policy in policies:
            for action_type in policy.get("action_types", []):
                index[str(action_type)] = dict(policy)
        return index

    def _candidate_actions(
        self,
        *,
        cycle: int,
        flights: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        policy_index: Mapping[str, Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        delayed = sorted(
            (flight for flight in flights if flight["delay_minutes"] > 0),
            key=lambda item: (-item["delay_minutes"], item["id"]),
        )
        target_flight = (
            delayed[0]["id"]
            if delayed
            else max(flights, key=lambda item: int(item["scheduled_minute"]))["id"]
        )
        target = next(item for item in flights if item["id"] == target_flight)
        rotation_target = next(
            (
                flight["id"]
                for flight in flights
                if flight["rotation_id"] == target["rotation_id"]
                and flight["kind"] == "OUTBOUND"
                and int(flight["scheduled_minute"])
                >= int(target["scheduled_minute"])
            ),
            target_flight,
        )
        gates = [item["id"] for item in resources if item["type"] == "GATE" and item["status"] in {"OPEN", "AVAILABLE"}]
        runways = sorted(
            (
                item
                for item in resources
                if item["type"] == "RUNWAY"
                and item["status"] in {"OPEN", "LIMITED", "STANDBY"}
            ),
            key=lambda item: (
                item.get("role") != "DEPARTURE",
                {"OPEN": 0, "LIMITED": 1, "STANDBY": 2}.get(
                    str(item.get("status")), 3
                ),
                item["id"],
            ),
        )
        targets = {
            "RESEQUENCE_DEPARTURE": runways[0]["id"] if runways else "ORD-FLOW",
            "REASSIGN_GATE": gates[-1] if gates else target_flight,
            "PROTECT_ROTATION": rotation_target,
        }
        descriptions = {
            "RESEQUENCE_DEPARTURE": "Re-sequence the modeled departure queue inside available runway capacity.",
            "REASSIGN_GATE": "Hold a compatible recovery gate for the affected aircraft rotation.",
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
                    "id": f"ACTION-{cycle}-{index}", "type": action_type,
                    "description": descriptions[action_type], "target_id": targets[action_type],
                    "policy_id": policy.get("id") if policy else None,
                    "authorized": policy_current,
                    "knowledge_current": knowledge_current,
                    "freshness_status": policy.get("freshness_status") if policy else None,
                }
            )
        return actions

    def _rank_candidates(
        self,
        *,
        cycle: int,
        active_delay: int,
        actions: list[dict[str, Any]],
        memories: list[dict[str, Any]],
        clock_minute: int,
        flights: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        churn_blocked: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        definitions = (
            ("FLOW_BALANCE", 18, 7),
            ("GATE_SWAP", 14, 4),
            ("TURNAROUND_RECOVERY", 10, 2),
        )
        latest = memories[0] if memories else None
        frozen_flights = {
            str(flight["id"])
            for flight in flights
            if flight["kind"] == "OUTBOUND"
            and clock_minute <= int(flight["scheduled_minute"]) <= clock_minute + self.execution_freeze
        }
        valid_target_ids = {
            str(item["id"]) for item in [*flights, *resources]
        }
        candidates: list[dict[str, Any]] = []
        for strategy, reduction, burden in definitions:
            projected = max(0, active_delay - reduction)
            base_score = projected + burden
            memory_adjustment = 0
            memory_ids: list[str] = []
            if latest and latest.get("strategy") == strategy and latest.get("outcome_status") == "SUCCESS":
                memory_adjustment = -5
                memory_ids.append(str(latest["id"]))
            focus_type = {
                "FLOW_BALANCE": "RESEQUENCE_DEPARTURE",
                "GATE_SWAP": "REASSIGN_GATE",
                "TURNAROUND_RECOVERY": "PROTECT_ROTATION",
            }[strategy]
            candidate_actions = deepcopy(actions)
            candidate_actions.sort(key=lambda item: (item["type"] != focus_type, item["type"]))
            for priority, action in enumerate(candidate_actions, start=1):
                action["priority"] = priority
                action["role"] = "PRIMARY" if action["type"] == focus_type else "SUPPORTING"
            policy_complete = all(action["authorized"] for action in candidate_actions)
            knowledge_current = all(
                action["knowledge_current"] for action in candidate_actions
            )
            freeze_clear = not any(
                str(action.get("target_id")) in frozen_flights for action in candidate_actions
            )
            targets_exist = all(
                str(action.get("target_id", "")) in valid_target_ids
                for action in candidate_actions
            )
            benefit_clear = min(active_delay, reduction) >= self.minimum_benefit
            safety_checks = [
                {"id": "CURRENT_GRAPH_STATE", "passed": True, "detail": "All specialist runs and the plan use the same graph state version."},
                {"id": "CURRENT_POLICY_AUTHORITY", "passed": policy_complete, "detail": "Every action is linked to a current versioned policy."},
                {"id": "CURRENT_KNOWLEDGE", "passed": knowledge_current, "detail": "Every action is backed by knowledge marked CURRENT_FOR_REPLAY."},
                {"id": "TARGETS_EXIST", "passed": targets_exist, "detail": "Every action targets a node in the current FalkorDB graph."},
                {"id": "EXECUTION_FREEZE", "passed": freeze_clear, "detail": f"No action targets outbound flights inside the {self.execution_freeze}-minute execution freeze.", "protected_flight_ids": sorted(frozen_flights)},
                {"id": "MINIMUM_BENEFIT", "passed": benefit_clear, "detail": f"Projected recovery must be at least {self.minimum_benefit} minutes."},
                {"id": "CHURN_GUARD", "passed": not churn_blocked, "detail": "A new plan is blocked for ten replay minutes after execution unless a high-risk material event arrives."},
            ]
            eligible = all(check["passed"] for check in safety_checks)
            candidates.append(
                {
                    "id": f"CAND-{cycle}-{strategy}", "strategy": strategy,
                    "base_score": base_score, "memory_adjustment": memory_adjustment,
                    "score": base_score + memory_adjustment, "projected_delay": projected,
                    "recovered_minutes": min(active_delay, reduction), "eligible": eligible,
                    "actions": candidate_actions, "memory_ids": memory_ids,
                    "safety_checks": safety_checks,
                }
            )
        base_safe = sorted((item for item in candidates if item["eligible"]), key=lambda item: (item["base_score"], item["id"]))
        ranked = sorted((item for item in candidates if item["eligible"]), key=lambda item: (item["score"], item["id"]))
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
            return fallback, "deterministic_safe_planner", "Local model disabled; used the highest-ranked safe candidate."
        prompt = {
            "role": "airport recovery coordinator",
            "instruction": "Select exactly one candidate_id. Return JSON with candidate_id and summary. Do not invent IDs.",
            "safe_candidates": [{"candidate_id": item["id"], "strategy": item["strategy"], "score": item["score"], "projected_delay": item["projected_delay"]} for item in candidates],
            "specialist_handoffs": [{"from": item["from_agent"], "summary": item["summary"]} for item in handoffs],
        }
        body = _json({"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0, "seed": 7}, "messages": [{"role": "system", "content": "You select only from a server-validated safe set. Never execute actions."}, {"role": "user", "content": _json(prompt)}]}).encode("utf-8")
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
            summary = str(choice.get("summary", "Local model selected a validated candidate."))[:500]
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
        findings, handoffs = self._record_specialists(
            cycle=cycle,
            state_version=state_version,
            flights=flights,
            events=events,
            resources=resources,
            policies=policies,
            memories=memories,
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
        actions = self._candidate_actions(cycle=cycle, flights=flights, resources=resources, policy_index=policy_index)
        ranked, influence = self._rank_candidates(
            cycle=cycle,
            active_delay=active_delay,
            actions=actions,
            memories=memories,
            clock_minute=state["clock_minute"],
            flights=flights,
            resources=resources,
            churn_blocked=churn_blocked,
        )
        pending_unchanged = bool(
            existing_plan
            and existing_plan.get("status") == "PROPOSED"
            and int(existing_plan.get("based_on_version", -1)) == state_version
        )
        no_action = active_delay < self.minimum_benefit or churn_blocked or pending_unchanged
        if no_action:
            selected_id, decision_source, model_summary = None, "guardrail", "No model call was needed for this NO_CHANGE cycle."
        else:
            selected_id, decision_source, model_summary = self._local_model_choice(ranked, handoffs)
        selected = next((item for item in ranked if item["id"] == selected_id), None)
        now = _utc_now()
        episode_id = f"EPISODE-{cycle}-{int(time.time() * 1000)}"
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
        self._create_node("DecisionEpisode", {"id": episode_id, "cycle": cycle, "trigger": trigger, "status": status, "summary": summary, "based_on_version": state_version, "created_at": now, "disruption_signature": f"{state['airport']}:IRROPS"})
        coord_run_id = f"RUN-{cycle}-COORD"
        self._create_node("AgentRun", {"id": coord_run_id, "agent_id": "AGENT-COORD", "cycle": cycle, "status": "COMPLETED", "based_on_version": state_version, "decision_source": decision_source, "started_at": now, "ended_at": _utc_now(), "created_at": now})
        self._create_edge("AGENT-COORD", "STARTED", coord_run_id)
        self._create_edge(episode_id, "ANALYZED", self.scenario_id)
        self._create_edge(episode_id, "USED_RUN", coord_run_id)
        for handoff in handoffs:
            self.backend.query("MATCH (h:Handoff {id: $id}) SET h.status = 'CONSUMED'", {"id": handoff["id"]})
            self._create_edge(episode_id, "CONSUMED_HANDOFF", handoff["id"])

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
        plan_id = f"PLAN-{cycle}-{selected['strategy']}"
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
        causal = self._causal_path(
            active_events, flights, resources, selected["actions"], policy_evidence
        )
        auto_policy = policy_index.get("APPLY_VALIDATED_PLAN")
        auto_knowledge_current = bool(
            auto_policy
            and auto_policy.get("status") == "CURRENT"
            and auto_policy.get("freshness_status") == "CURRENT_FOR_REPLAY"
        )
        auto_authorized = bool(self.auto_apply and auto_knowledge_current)
        plan = {
            "id": plan_id, "candidate_id": selected["id"], "status": "PROPOSED",
            "strategy": selected["strategy"], "summary": summary,
            "based_on_version": state_version, "cycle": cycle, "selected_by": decision_source,
            "score": selected["score"], "base_score": selected["base_score"],
            "memory_adjustment": selected["memory_adjustment"],
            "baseline_delay": active_delay,
            "projected_delay": selected["projected_delay"], "recovered_minutes": selected["recovered_minutes"],
            "actions": selected["actions"],
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
            "created_at": now, "causal_path": causal,
        }
        plan = _normalize_plan_payload(plan)
        influence = plan["memory_influence"]
        self._create_node("Plan", {"id": plan_id, "cycle": cycle, "strategy": selected["strategy"], "status": "PROPOSED", "based_on_version": state_version, "score": selected["score"], "projected_delay": selected["projected_delay"], "is_current": True, "payload_json": _json(plan), "created_at": now})
        self._create_edge(episode_id, "SELECTED", plan_id)
        for action in selected["actions"]:
            self._create_node("Action", {**action, "plan_id": plan_id, "status": "VALIDATED", "created_at": now})
            self._create_edge(plan_id, "PROPOSES", action["id"])
            if action.get("target_id"):
                self._create_edge(action["id"], "AFFECTS", str(action["target_id"]))
            if action.get("policy_id"):
                self._create_edge(action["id"], "AUTHORIZED_BY", str(action["policy_id"]))
                aligned = policy_index.get(action["type"], {}).get("procedure_id")
                if aligned:
                    self._create_edge(action["id"], "SUPPORTED_BY", str(aligned))
        for memory_id in influence.get("memory_ids", []):
            self._create_edge(episode_id, "RECALLED", memory_id)
        self.backend.query("MATCH (s:Scenario {id: $scenario}), (a:Agent {id: 'AGENT-COORD'}) SET s.status = 'AWAITING_APPROVAL', a.status = 'AWAITING_APPROVAL', a.current_task = 'Safe plan ready for human review', a.last_run_id = $run", {"scenario": self.scenario_id, "run": coord_run_id})
        if auto_authorized and plan["safety_validation"]["passed"]:
            self.apply(plan_id, state_version)
            return {"episode_id": episode_id, "status": "AUTO_APPLIED", "plan": self._current_plan(), "influence": influence, "findings": findings}
        return {"episode_id": episode_id, "status": status, "plan": plan, "influence": influence, "findings": findings}

    @staticmethod
    def _causal_path(
        active_events: list[dict[str, Any]],
        flights: list[dict[str, Any]],
        resources: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        event = active_events[0] if active_events else None
        flight = next((item for item in flights if event and item["id"] == event.get("flight_id")), flights[0] if flights else None)
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []
        if event:
            nodes.append({"id": event["id"], "label": event["summary"], "type": "EVENT", "status": event["status"], "source_kind": event.get("source_kind", "MODELED")})
        if flight:
            nodes.append({"id": flight["id"], "label": flight["callsign"], "type": "FLIGHT", "status": flight["status"], "source_kind": flight.get("source_kind", "MIXED")})
            if event:
                edges.append({"source": event["id"], "target": flight["id"], "type": "AFFECTS"})
            downstream = next(
                (
                    item
                    for item in flights
                    if item["rotation_id"] == flight["rotation_id"]
                    and int(item["scheduled_minute"]) > int(flight["scheduled_minute"])
                ),
                None,
            )
            if downstream:
                nodes.append({"id": downstream["id"], "label": downstream["callsign"], "type": "DOWNSTREAM_FLIGHT", "status": downstream["status"], "source_kind": downstream.get("source_kind", "MIXED")})
                edges.append({"source": flight["id"], "target": downstream["id"], "type": "ROTATES_TO"})
        if event and event.get("resource_id"):
            resource = next(
                (item for item in resources if item["id"] == event["resource_id"]),
                None,
            )
            if resource:
                nodes.append({"id": resource["id"], "label": resource["label"], "type": resource["type"], "status": resource["status"], "source_kind": resource.get("source_kind", "MODELED")})
                edges.append({"source": event["id"], "target": resource["id"], "type": "CONSTRAINS"})
        for action in actions:
            nodes.append({"id": action["id"], "label": action["description"], "type": "ACTION", "status": "VALIDATED", "source_kind": "DERIVED"})
            if flight:
                edges.append({"source": flight["id"], "target": action["id"], "type": "RECOVERED_BY"})
            target_id = str(action.get("target_id", ""))
            target_resource = next(
                (item for item in resources if item["id"] == target_id), None
            )
            target_flight = next(
                (item for item in flights if item["id"] == target_id), None
            )
            if target_resource:
                if not any(item["id"] == target_id for item in nodes):
                    nodes.append({"id": target_id, "label": target_resource["label"], "type": target_resource["type"], "status": target_resource["status"], "source_kind": target_resource.get("source_kind", "MODELED")})
                edges.append({"source": action["id"], "target": target_id, "type": "USES_RESOURCE"})
            elif target_flight:
                if not any(item["id"] == target_id for item in nodes):
                    nodes.append({"id": target_id, "label": target_flight["callsign"], "type": "FLIGHT", "status": target_flight["status"], "source_kind": target_flight.get("source_kind", "MIXED")})
                edges.append({"source": action["id"], "target": target_id, "type": "AFFECTS"})
        for item in evidence:
            if not item.get("policy_id"):
                continue
            nodes.append({"id": item["policy_id"], "label": item.get("title") or item["policy_id"], "type": "POLICY", "status": item.get("status"), "source_kind": "MODELED"})
            edges.append({"source": item["action_id"], "target": item["policy_id"], "type": "AUTHORIZED_BY"})
        return {"summary": "Event → resource/flight → downstream rotation → coordinated actions → current policy evidence", "nodes": nodes, "edges": edges}

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
                    "detail": "Every proposed action still targets a node in the current FalkorDB graph.",
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
            self._create_node("Outcome", {"id": outcome_id, "plan_id": plan_id, "status": "SUCCESS", "projected_delay": current["projected_delay"], "benefit_minutes": current["recovered_minutes"], "created_at": now, "source_kind": "MODELED"})
            self._create_node("Memory", {"id": memory_id, "strategy": current["strategy"], "disruption_signature": f"{state['airport']}:IRROPS", "benefit_minutes": current["recovered_minutes"], "confidence": "SIMULATED", "created_at": now, "source_kind": "DERIVED"})
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

    def reset(self, *, preserve_memory: bool = True) -> dict[str, Any]:
        with self._lock:
            retained = self._memories(limit=100) if preserve_memory else []
            self._seed()
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
                    "playing": state["clock_minute"] < state["end_minute"],
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
                "causal_path": plan.get("causal_path") if plan else {"summary": "Waiting for the next disruption checkpoint.", "nodes": [], "edges": []},
                "current_plan": plan,
                "memory": {"episode_count": len(memories), "recalled": memories, "influence": influence},
                "policies": {"authorization_complete": policy_complete, "applicable": policies},
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
        """Return a source-aware answer over the synthetic operations knowledge graph."""

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
            citations = [
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
            return {
                "ok": True,
                "question": question
                or "Which current rules authorize the recovery, who owns them, and what sources support them?",
                "answer": (
                    f"{len(citations)} current modeled policy versions cover the bounded recovery. "
                    "Each authorization retains its owner, responsible role, document version, exact section, and excerpt."
                ),
                "truth_label": "MODELED_SYNTHETIC_COMPANY_KNOWLEDGE",
                "citations": citations,
                "artifacts": artifacts,
                "graph_path": [
                    "Action-[:AUTHORIZED_BY]->Policy",
                    "Policy-[:ALIGNED_WITH]->ProcedureVersion",
                    "ProcedureVersion-[:IN_DOCUMENT]->SourceDocument",
                    "Policy-[:OWNED_BY]->Team",
                    "ProcedureVersion-[:RESPONSIBLE_ROLE]->Role",
                ],
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
