"""Leakage-resistant, graph-backed historical airport backtests.

The recovery simulator in :mod:`engine` answers a counterfactual question.  This
module answers a different one: given only information available at a replay
cutoff, what did the agents forecast for the next hour, and how closely did
that forecast match a sealed public outcome archive?

Schedule and weather are loaded by the runner.  Outcome rows are owned by the
``GroundTruthEvaluator`` and streamed only when their reveal time has passed;
future outcomes are never seeded into the agent graph or returned by the API.
"""

from __future__ import annotations

import csv
import concurrent.futures
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from engine import BASE_DIR, GraphBackend, GraphNotReadyError


DEFAULT_BACKTEST_ROOT = BASE_DIR / "data" / "backtests"
BACKTEST_AGENTS = (
    ("BT-AGENT-WEATHER", "Weather specialist"),
    ("BT-AGENT-DEMAND", "Demand specialist"),
    ("BT-AGENT-ARRIVAL", "Arrival specialist"),
    ("BT-AGENT-DEPARTURE", "Departure specialist"),
    ("BT-AGENT-DISRUPTION", "Disruption specialist"),
    ("BT-AGENT-MEMORY", "Outcome-memory specialist"),
)
SCHEDULE_ALIASES = {
    "movement_id": ("movement_id", "segment_id"),
    "flight_date": ("flight_date",),
    "direction": ("direction", "ord_movement"),
    "reporting_airline": ("reporting_airline",),
    "flight_number": ("flight_number",),
    "origin": ("origin",),
    "dest": ("dest", "destination"),
    "scheduled_time_local": ("scheduled_time_local", "ord_scheduled_at_local"),
}
WEATHER_ALIASES = {
    "evidence_id": ("evidence_id", "observation_id"),
    "valid_local": ("valid_local", "observed_at_local"),
}
TRUTH_ALIASES = {
    "movement_id": ("movement_id", "segment_id"),
    "actual_time_local": ("actual_time_local", "ord_actual_at_local"),
    "delay_minutes": ("delay_minutes", "ord_delay_minutes"),
    "cancelled": ("cancelled",),
    "diverted": ("diverted",),
    "delay_band": ("delay_band",),
}
DAY_MINUTES = 24 * 60


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_").lower()
    return safe or "historical_day"


def _first(row: Mapping[str, Any], names: Iterable[str], default: Any = "") -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return default


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "1.0", "1.00", "true", "yes", "y"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> float | None:
    """Parse a number without turning an unavailable archive field into zero."""

    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _delay_band(value: Any) -> str:
    if _optional_number(value) is None:
        return "UNAVAILABLE"
    delay = _number(value)
    if delay < 15:
        return "UNDER_15"
    if delay < 60:
        return "15_TO_59"
    if delay < 120:
        return "60_TO_119"
    return "120_PLUS"


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise FileNotFoundError(f"Backtest file is missing: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Backtest CSV has no header: {path}")
        return list(reader.fieldnames), [dict(row) for row in reader]


def _require_aliases(
    columns: Iterable[str], aliases: Mapping[str, Iterable[str]], *, label: str
) -> None:
    available = set(columns)
    missing = [key for key, names in aliases.items() if not available.intersection(names)]
    # delay_band is reproducibly derived when a pack keeps only numeric delay.
    if label == "ground truth" and missing == ["delay_band"]:
        return
    if missing:
        raise ValueError(f"{label} is missing required fields: {', '.join(missing)}")


def _minute(value: Any, service_date: str) -> int:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("A local replay timestamp is empty")
    if re.fullmatch(r"\d{3,4}", raw):
        raw = raw.zfill(4)
        hour, minute = int(raw[:2]), int(raw[2:])
        return hour * 60 + minute
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", raw):
        hour, minute = (int(part) for part in raw.split(":")[:2])
        return hour * 60 + minute
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    base = date.fromisoformat(service_date)
    return (parsed.date() - base).days * DAY_MINUTES + parsed.hour * 60 + parsed.minute


def _clock(minute: int) -> str:
    if minute >= DAY_MINUTES:
        return "24:00"
    minute = max(0, minute)
    return f"{minute // 60:02d}:{minute % 60:02d}"


def discover_backtests(root: Path | str | None = None) -> list[dict[str, Any]]:
    """Discover complete checked-in packs without opening sealed truth rows."""

    base = Path(root or os.getenv("GROUNDGRAPH_BACKTEST_ROOT", DEFAULT_BACKTEST_ROOT))
    if not base.is_dir():
        return []
    packs: list[dict[str, Any]] = []
    for directory in sorted(path for path in base.iterdir() if path.is_dir()):
        paths = {
            "schedule": directory / "schedule.csv",
            "weather": directory / "weather.csv",
            "ground_truth": directory / "ground_truth.csv",
        }
        if not all(path.is_file() for path in paths.values()):
            continue
        manifest_path = directory / "manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {}
        )
        airport_value = manifest.get("airport", {})
        airport = (
            str(airport_value.get("iata", "ORD"))
            if isinstance(airport_value, Mapping)
            else str(airport_value or "ORD")
        )
        replay = manifest.get("replay_window", {})
        service_date = str(
            replay.get("service_date", "")
            if isinstance(replay, Mapping)
            else manifest.get("date", "")
        )
        pack_id = str(manifest.get("pack_id") or manifest.get("backtest_id") or directory.name)
        raw_counts = manifest.get("counts", {})
        safe_count_keys = {
            "scheduled_segments",
            "scheduled_movements",
            "ord_departures",
            "ord_arrivals",
            "weather_observations",
        }
        public_counts = {
            key: value
            for key, value in (raw_counts.items() if isinstance(raw_counts, Mapping) else [])
            if key in safe_count_keys
        }
        packs.append(
            {
                "id": pack_id,
                "pack_id": pack_id,
                "airport": airport,
                "date": service_date or directory.name[-10:],
                "label": str(manifest.get("label") or f"{airport} · real-day backtest"),
                "mode": "HISTORICAL_SHADOW_BACKTEST",
                "window": "00:00–24:00 local",
                "counts": public_counts,
                "truth_boundary": (
                    manifest.get("truth_boundaries", {}).get("ground_truth")
                    if isinstance(manifest.get("truth_boundaries"), Mapping)
                    else "Outcomes stay sealed until their reveal time."
                ),
                "resolved_path": str(directory.resolve()),
            }
        )
    return packs


class GroundTruthEvaluator:
    """Streams only outcomes mature at ``as_of_minute`` from the sealed file."""

    def __init__(
        self,
        path: Path,
        *,
        service_date: str,
        schedule: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.path = path
        self.service_date = service_date
        self.schedule = schedule

    def _normalized_rows(
        self, movement_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Read sealed rows inside the evaluator and normalize missing values safely."""

        columns, rows = _read_csv(self.path)
        _require_aliases(columns, TRUTH_ALIASES, label="ground truth")
        normalized: list[dict[str, Any]] = []
        for raw in rows:
            movement_id = str(_first(raw, TRUTH_ALIASES["movement_id"])).strip()
            if not movement_id or (movement_ids is not None and movement_id not in movement_ids):
                continue
            scheduled = self.schedule.get(movement_id)
            if scheduled is None:
                continue
            cancelled = _truthy(_first(raw, TRUTH_ALIASES["cancelled"], "0"))
            diverted = _truthy(_first(raw, TRUTH_ALIASES["diverted"], "0"))
            delay_value = _optional_number(
                _first(raw, TRUTH_ALIASES["delay_minutes"], None)
            )
            delay = int(round(delay_value)) if delay_value is not None else None
            actual_raw = str(
                _first(raw, TRUTH_ALIASES["actual_time_local"], "")
            ).strip()
            actual_minute: int | None = None
            if actual_raw:
                actual_minute = _minute(actual_raw, self.service_date)
                # A HH:MM-only value can wrap after midnight.  Numeric delay,
                # when present, resolves that ambiguity without inventing it.
                if (
                    delay is not None
                    and "T" not in actual_raw
                    and "-" not in actual_raw
                ):
                    expected = int(scheduled["scheduled_minute"]) + delay
                    candidates = [
                        actual_minute - DAY_MINUTES,
                        actual_minute,
                        actual_minute + DAY_MINUTES,
                    ]
                    actual_minute = min(
                        candidates, key=lambda item: abs(item - expected)
                    )
            elif not cancelled and delay is not None:
                actual_minute = int(scheduled["scheduled_minute"]) + delay
            supplied_band = str(
                _first(raw, TRUTH_ALIASES["delay_band"], "")
            ).strip()
            band = supplied_band or (
                "CANCELLED"
                if cancelled
                else "DIVERTED"
                if diverted and delay is None
                else _delay_band(delay)
            )
            normalized.append(
                {
                    "movement_id": movement_id,
                    "direction": scheduled["direction"],
                    "actual_time_local": actual_raw or None,
                    "actual_minute": actual_minute,
                    "delay_minutes": delay,
                    "cancelled": cancelled,
                    "diverted": diverted,
                    "delay_band": band,
                    "source_kind": "OBSERVED_POST_EVENT_ARCHIVE",
                }
            )
        return normalized

    def reveal(
        self,
        *,
        as_of_minute: int,
        already_revealed: set[str],
    ) -> list[dict[str, Any]]:
        revealed: list[dict[str, Any]] = []
        for item in self._normalized_rows():
            movement_id = item["movement_id"]
            if movement_id in already_revealed:
                continue
            mature = (
                item["actual_minute"] is not None
                and item["actual_minute"] <= as_of_minute
                and not item["cancelled"]
                and not item["diverted"]
            )
            # BTS does not contain a cancellation-announcement timestamp.
            # Cancellation and diversion resolutions therefore remain out of
            # agent state and are used only by the evaluator's final archive
            # settlement after the replay reaches 24:00.
            if not mature:
                continue
            revealed.append({**item, "reveal_minute": as_of_minute})
        return revealed

    def aggregate_window(self, movement_ids: Iterable[str]) -> dict[str, Any]:
        """Return only window-level truth; individual sealed rows never escape."""

        target_ids = set(movement_ids)
        rows = self._normalized_rows(target_ids)
        matched = len(rows)
        cancelled = sum(bool(item["cancelled"]) for item in rows)
        diverted = sum(bool(item["diverted"]) for item in rows)
        observed_delays = [
            item["delay_minutes"]
            for item in rows
            if not item["cancelled"] and item["delay_minutes"] is not None
        ]
        disrupted = sum(
            bool(item["cancelled"])
            or bool(item["diverted"])
            or (
                item["delay_minutes"] is not None
                and item["delay_minutes"] >= 15
            )
            for item in rows
        )
        mean_delay = (
            round(sum(observed_delays) / len(observed_delays), 1)
            if observed_delays
            else None
        )
        return {
            "movements": matched,
            "cancelled": cancelled,
            "diverted": diverted,
            "disrupted": disrupted,
            "cancel_rate_pct": round(100 * cancelled / matched, 1) if matched else 0.0,
            "disruption_rate_pct": round(100 * disrupted / matched, 1) if matched else 0.0,
            "mean_delay_minutes": mean_delay,
            "mean_delay_band": _delay_band(mean_delay),
            "observed_delay_count": len(observed_delays),
            "unavailable_delay_count": matched - cancelled - len(observed_delays),
            "missing_truth_count": max(0, len(target_ids) - matched),
        }


class HistoricalBacktestRuntime:
    """One 24-hour, hourly-forecast shadow run backed by an isolated graph."""

    def __init__(
        self,
        pack_path: Path | str,
        *,
        backend: GraphBackend | None = None,
        mode: str | None = None,
        graph_name: str | None = None,
        lite_path: Path | str | None = None,
        enable_llm: bool | None = None,
    ) -> None:
        self.pack_path = Path(pack_path).resolve()
        manifest_path = self.pack_path / "manifest.json"
        self.manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {}
        )
        self.pack_id = str(
            self.manifest.get("pack_id")
            or self.manifest.get("backtest_id")
            or self.pack_path.name
        )
        replay = self.manifest.get("replay_window", {})
        manifest_date = (
            str(replay.get("service_date", ""))
            if isinstance(replay, Mapping)
            else str(self.manifest.get("date", ""))
        )
        schedule_columns, schedule_rows = _read_csv(self.pack_path / "schedule.csv")
        _require_aliases(schedule_columns, SCHEDULE_ALIASES, label="schedule")
        first_date = str(_first(schedule_rows[0], SCHEDULE_ALIASES["flight_date"])) if schedule_rows else ""
        self.service_date = manifest_date or first_date
        if not self.service_date:
            raise ValueError("Backtest service date is missing")
        self.schedule = self._normalize_schedule(schedule_rows)
        weather_columns, weather_rows = _read_csv(self.pack_path / "weather.csv")
        _require_aliases(weather_columns, WEATHER_ALIASES, label="weather")
        self.weather = self._normalize_weather(weather_rows)
        truth_path = self.pack_path / "ground_truth.csv"
        if not truth_path.is_file():
            raise FileNotFoundError(f"Backtest file is missing: {truth_path}")
        self.truth_evaluator = GroundTruthEvaluator(
            truth_path, service_date=self.service_date, schedule=self.schedule
        )
        self.dataset_hash = hashlib.sha256(
            "".join(
                _sha256(self.pack_path / filename)
                for filename in (
                    "manifest.json",
                    "schedule.csv",
                    "weather.csv",
                    "ground_truth.csv",
                )
                if (self.pack_path / filename).is_file()
            ).encode()
        ).hexdigest()
        if enable_llm is None:
            enable_llm = os.getenv("GROUNDGRAPH_ENABLE_LLM", "0") in {"1", "true", "True"}
        self.enable_llm = bool(enable_llm)
        self.model = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
        self.ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        self.ollama_timeout = max(1.0, float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "30")))
        selected_mode = (mode or os.getenv("FALKORDB_MODE", "server")).strip().lower()
        runtime_graph = graph_name or f"groundgraph_backtest_{_slug(self.pack_id)}"
        if selected_mode == "lite" and lite_path is None:
            lite_path = BASE_DIR / ".data" / f"{runtime_graph}.db"
        self.backend = backend or GraphBackend(
            mode=selected_mode,
            graph_name=runtime_graph,
            lite_path=lite_path,
        )
        self.graph_name = self.backend.graph_name
        self.current_minute = 0
        self._lock = threading.RLock()
        self._weather_ids: set[str] = set()
        self._revealed: dict[str, dict[str, Any]] = {}
        self._episodes: list[dict[str, Any]] = []
        self._handoffs: list[dict[str, Any]] = []
        self._seed_graph()
        self._ingest_weather(0)
        # Handle flights whose recorded ORD event occurred before the first
        # forecast watermark. They may inform the forecast, but can never be
        # forecast targets because their outcome is already known.
        self._reveal_due(0)
        self._issue_forecast(0)

    def close(self) -> None:
        self.backend.close()

    def _normalize_schedule(self, rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for raw in rows:
            row = {key: str(_first(raw, names)).strip() for key, names in SCHEDULE_ALIASES.items()}
            movement_id = row["movement_id"]
            if not movement_id or movement_id in normalized:
                raise ValueError(f"Schedule movement_id is missing or duplicated: {movement_id!r}")
            direction = row["direction"].upper()
            if direction in {"DEP", "OUTBOUND", "DEPARTING"}:
                direction = "DEPARTURE"
            elif direction in {"ARR", "INBOUND", "ARRIVING"}:
                direction = "ARRIVAL"
            if direction not in {"ARRIVAL", "DEPARTURE"}:
                raise ValueError(f"Unsupported ORD movement direction: {row['direction']!r}")
            row["direction"] = direction
            row["scheduled_minute"] = _minute(row["scheduled_time_local"], self.service_date)
            row["source_kind"] = "OBSERVED_SCHEDULE"
            normalized[movement_id] = row
        return normalized

    def _normalize_weather(self, rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in rows:
            evidence_id = str(_first(raw, WEATHER_ALIASES["evidence_id"])).strip()
            valid_local = str(_first(raw, WEATHER_ALIASES["valid_local"])).strip()
            if not evidence_id or evidence_id in seen:
                raise ValueError(f"Weather evidence_id is missing or duplicated: {evidence_id!r}")
            seen.add(evidence_id)
            observed = {
                key: value
                for key, value in raw.items()
                if key not in set(WEATHER_ALIASES["evidence_id"]) | set(WEATHER_ALIASES["valid_local"])
                and value not in (None, "")
            }
            normalized.append(
                {
                    "evidence_id": evidence_id,
                    "valid_local": valid_local,
                    "valid_minute": _minute(valid_local, self.service_date),
                    "observed": observed,
                    "source_kind": "OBSERVED_WEATHER",
                }
            )
        return sorted(normalized, key=lambda item: (item["valid_minute"], item["evidence_id"]))

    @staticmethod
    def _scalar(value: Any) -> Any:
        if isinstance(value, (dict, list, tuple)):
            return _json(value)
        return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)

    def _create_node(self, label: str, properties: Mapping[str, Any]) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
            raise ValueError(f"Unsafe graph label: {label}")
        fields, params = [], {}
        for index, (key, value) in enumerate(properties.items()):
            if value is None:
                continue
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                raise ValueError(f"Unsafe graph property: {key}")
            name = f"v{index}"
            fields.append(f"{key}: ${name}")
            params[name] = self._scalar(value)
        self.backend.query(f"CREATE (n:{label} {{{', '.join(fields)}}})", params)

    def _create_edge(self, source: str, relationship: str, target: str) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", relationship):
            raise ValueError(f"Unsafe graph relationship: {relationship}")
        self.backend.query(
            f"MATCH (a {{id: $source}}), (b {{id: $target}}) CREATE (a)-[:{relationship}]->(b)",
            {"source": source, "target": target},
        )

    def _seed_graph(self) -> None:
        self.backend.query("MATCH (n) DETACH DELETE n")
        airport_value = self.manifest.get("airport", {})
        airport = (
            str(airport_value.get("iata", "ORD"))
            if isinstance(airport_value, Mapping)
            else str(airport_value or "ORD")
        )
        self._create_node(
            "BacktestRun",
            {
                "id": self.pack_id,
                "airport": airport,
                "service_date": self.service_date,
                "current_minute": 0,
                "status": "FORECASTING",
                "dataset_hash": self.dataset_hash,
                "truth_policy": "ACTUAL_TIME_AGENT_VISIBILITY; END_OF_DAY_ARCHIVE_SETTLEMENT",
                "source_kind": "OBSERVED_INPUTS_WITH_MODEL_FORECASTS",
            },
        )
        for agent_id, name in BACKTEST_AGENTS:
            self._create_node("BacktestAgent", {"id": agent_id, "name": name, "status": "READY"})
            self._create_edge(self.pack_id, "USES_AGENT", agent_id)
        self._create_node("BacktestCoordinator", {"id": "BT-AGENT-COORD", "name": "Local Qwen forecast coordinator"})
        self._create_edge(self.pack_id, "USES_COORDINATOR", "BT-AGENT-COORD")
        for movement in self.schedule.values():
            self._create_node("ScheduledMovement", {"id": movement["movement_id"], **movement})
            self._create_edge(self.pack_id, "SCHEDULES", movement["movement_id"])

    def _assert_graph_state(self, *, check_run_clock: bool = True) -> None:
        """Fail closed if FalkorDB no longer matches the in-memory watermark."""

        try:
            self._assert_graph_state_unchecked(check_run_clock=check_run_clock)
        except GraphNotReadyError:
            raise
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise GraphNotReadyError(
                "The factual replay graph contains malformed persisted state"
            ) from exc

    def _assert_graph_state_unchecked(self, *, check_run_clock: bool) -> None:
        """Compare factual inputs and saved decision artifacts field by field."""

        run_rows = self.backend.query(
            "MATCH (b:BacktestRun {id: $id}) "
            "RETURN b.dataset_hash, b.current_minute, b.status",
            {"id": self.pack_id},
        ).result_set
        if len(run_rows) != 1 or str(run_rows[0][0]) != self.dataset_hash:
            raise GraphNotReadyError(
                "The factual replay graph is missing or its dataset identity changed"
            )
        if check_run_clock and (
            int(run_rows[0][1]) != self.current_minute
            or str(run_rows[0][2])
            != ("COMPLETE" if self.current_minute >= DAY_MINUTES else "FORECASTING")
        ):
            raise GraphNotReadyError(
                "The factual replay graph clock does not match the active run"
            )

        schedule_rows = self.backend.query(
            "MATCH (m:ScheduledMovement) "
            "RETURN m.id, m.direction, m.scheduled_minute, m.reporting_airline, "
            "m.flight_number, m.origin, m.dest, m.scheduled_time_local"
        ).result_set
        graph_schedule = {
            str(row[0]): (
                str(row[1]),
                int(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
            )
            for row in schedule_rows
        }
        expected_schedule = {
            movement_id: (
                str(item["direction"]),
                int(item["scheduled_minute"]),
                str(item["reporting_airline"]),
                str(item["flight_number"]),
                str(item["origin"]),
                str(item["dest"]),
                str(item["scheduled_time_local"]),
            )
            for movement_id, item in self.schedule.items()
        }
        if (
            len(schedule_rows) != len(expected_schedule)
            or graph_schedule != expected_schedule
        ):
            raise GraphNotReadyError(
                "The factual replay schedule in FalkorDB no longer matches the checked-in pack"
            )

        agent_rows = self.backend.query(
            "MATCH (a:BacktestAgent) RETURN a.id"
        ).result_set
        graph_agent_ids = [str(row[0]) for row in agent_rows]
        expected_agent_ids = {agent_id for agent_id, _name in BACKTEST_AGENTS}
        if len(graph_agent_ids) != len(expected_agent_ids) or set(graph_agent_ids) != expected_agent_ids:
            raise GraphNotReadyError("The factual replay graph is missing one or more specialist agents")

        coordinator_rows = self.backend.query(
            "MATCH (c:BacktestCoordinator {id: 'BT-AGENT-COORD'}) RETURN count(c)"
        ).result_set
        if not coordinator_rows or int(coordinator_rows[0][0]) != 1:
            raise GraphNotReadyError("The factual replay graph coordinator is unavailable")

        weather_rows = self.backend.query(
            "MATCH (w:WeatherObservation) "
            "RETURN w.id, w.valid_minute, w.observed_json"
        ).result_set
        graph_weather: dict[str, tuple[int, dict[str, Any]]] = {}
        for row in weather_rows:
            try:
                observed = json.loads(row[2]) if isinstance(row[2], str) else {}
            except (json.JSONDecodeError, TypeError):
                observed = {}
            graph_weather[str(row[0])] = (int(row[1]), observed)
        expected_weather = {
            item["evidence_id"]: (
                int(item["valid_minute"]),
                dict(item["observed"]),
            )
            for item in self.weather
            if int(item["valid_minute"]) <= self.current_minute
        }
        if len(weather_rows) != len(expected_weather) or graph_weather != expected_weather:
            raise GraphNotReadyError("Time-gated weather in FalkorDB does not match the replay watermark")

        outcome_rows = self.backend.query(
            "MATCH (o:ObservedOutcome) "
            "RETURN o.movement_id, o.direction, o.delay_minutes, o.cancelled, "
            "o.diverted, o.reveal_minute, o.actual_minute, o.delay_band"
        ).result_set
        graph_outcomes = {
            str(row[0]): (
                str(row[1]),
                _optional_number(row[2]),
                _truthy(row[3]),
                _truthy(row[4]),
                int(row[5]),
                int(row[6]) if row[6] is not None else None,
                str(row[7]),
            )
            for row in outcome_rows
        }
        expected_outcomes = {
            movement_id: (
                str(item["direction"]),
                _optional_number(item.get("delay_minutes")),
                bool(item["cancelled"]),
                bool(item["diverted"]),
                int(item["reveal_minute"]),
                int(item["actual_minute"])
                if item.get("actual_minute") is not None
                else None,
                str(item["delay_band"]),
            )
            for movement_id, item in self._revealed.items()
        }
        if len(outcome_rows) != len(expected_outcomes) or graph_outcomes != expected_outcomes:
            raise GraphNotReadyError("Matured outcomes in FalkorDB do not match the replay watermark")

        def decoded_list(value: Any) -> list[Any]:
            if isinstance(value, str):
                decoded = json.loads(value)
                if not isinstance(decoded, list):
                    raise ValueError("Persisted graph list is malformed")
                return decoded
            if isinstance(value, (list, tuple)):
                return list(value)
            raise ValueError("Persisted graph list is missing")

        episode_rows = self.backend.query(
            "MATCH (e:ForecastEpisode) "
            "RETURN e.id, e.issued_minute, e.horizon_end_minute, e.target_movement_ids"
        ).result_set
        graph_episodes = {
            str(row[0]): (
                int(row[1]),
                int(row[2]),
                [str(item) for item in decoded_list(row[3])],
            )
            for row in episode_rows
        }
        expected_episodes = {
            str(item["id"]): (
                int(item["issued_minute"]),
                int(item["horizon_end_minute"]),
                [str(value) for value in item["target_movement_ids"]],
            )
            for item in self._episodes
        }
        if len(episode_rows) != len(expected_episodes) or graph_episodes != expected_episodes:
            raise GraphNotReadyError("Saved forecast episodes in FalkorDB do not match the replay")

        truth_window_rows = self.backend.query(
            "MATCH (w:GroundTruthWindow) RETURN w.id, w.horizon_start_minute, "
            "w.horizon_end_minute, w.target_count, w.status, w.revealed_count, "
            "w.agent_visible_count, w.coverage_pct, w.evaluation_scope, "
            "w.actual_json, w.truth_policy"
        ).result_set
        graph_truth_windows = {
            str(row[0]): (
                int(row[1]),
                int(row[2]),
                int(row[3]),
                str(row[4]),
                int(row[5]),
                int(row[6]) if row[6] is not None else None,
                float(row[7]) if row[7] is not None else None,
                str(row[8] or ""),
                str(row[9] or ""),
                str(row[10]),
            )
            for row in truth_window_rows
        }
        expected_truth_windows = {
            str(item["truth_window_id"]): (
                int(item["issued_minute"]),
                int(item["horizon_end_minute"]),
                len(item["target_movement_ids"]),
                str(item["truth_window_state"]["status"]),
                int(item["truth_window_state"]["revealed_count"]),
                item["truth_window_state"]["agent_visible_count"],
                item["truth_window_state"]["coverage_pct"],
                str(item["truth_window_state"]["evaluation_scope"]),
                str(item["truth_window_state"]["actual_json"]),
                "NO_FUTURE_OUTCOME_BEFORE_REVEAL",
            )
            for item in self._episodes
        }
        if (
            len(truth_window_rows) != len(expected_truth_windows)
            or graph_truth_windows != expected_truth_windows
        ):
            raise GraphNotReadyError("Saved truth windows in FalkorDB do not match the replay")

        forecast_rows = self.backend.query(
            "MATCH (f:Forecast) "
            "RETURN f.id, f.episode_id, f.candidate_id, f.issued_minute, f.payload_json"
        ).result_set
        graph_forecasts = {
            str(row[0]): (
                str(row[1]),
                str(row[2]),
                int(row[3]),
                str(row[4]),
            )
            for row in forecast_rows
        }
        expected_forecasts = {
            str(item["forecast"]["id"]): (
                str(item["id"]),
                str(item["forecast"]["candidate_id"]),
                int(item["issued_minute"]),
                _json(item["forecast"]),
            )
            for item in self._episodes
        }
        if len(forecast_rows) != len(expected_forecasts) or graph_forecasts != expected_forecasts:
            raise GraphNotReadyError("Saved forecasts in FalkorDB do not match the replay")

        candidate_rows = self.backend.query(
            "MATCH (c:ForecastCandidate) RETURN c.id, c.method, c.label, "
            "c.predicted_movements, c.predicted_cancel_rate_pct, "
            "c.predicted_cancelled_count, c.predicted_disruption_rate_pct, "
            "c.predicted_disrupted_count, c.predicted_mean_delay_minutes, "
            "c.predicted_delay_band, c.explanation, c.source_kind"
        ).result_set
        graph_candidates = {
            str(row[0]): (
                str(row[1]),
                str(row[2]),
                int(row[3]),
                float(row[4]),
                int(row[5]),
                float(row[6]),
                int(row[7]),
                float(row[8]),
                str(row[9]),
                str(row[10]),
                str(row[11]),
            )
            for row in candidate_rows
        }
        expected_candidates = {
            str(candidate["id"]): (
                str(candidate["method"]),
                str(candidate["label"]),
                int(candidate["predicted_movements"]),
                float(candidate["predicted_cancel_rate_pct"]),
                int(candidate["predicted_cancelled_count"]),
                float(candidate["predicted_disruption_rate_pct"]),
                int(candidate["predicted_disrupted_count"]),
                float(candidate["predicted_mean_delay_minutes"]),
                str(candidate["predicted_delay_band"]),
                str(candidate["explanation"]),
                str(candidate["source_kind"]),
            )
            for episode in self._episodes
            for candidate in episode["candidates"]
        }
        if (
            len(candidate_rows) != len(expected_candidates)
            or graph_candidates != expected_candidates
        ):
            raise GraphNotReadyError("Saved forecast candidates in FalkorDB do not match the replay")

        model_rows = self.backend.query(
            "MATCH (m:ModelRun) RETURN m.id, m.runtime_kind, m.provider, m.model, "
            "m.selected_candidate_id, m.allowlisted_candidate_ids, "
            "m.coordinator_input_handoff_ids, m.decision_source, m.summary"
        ).result_set
        graph_models = {
            str(row[0]): (
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                [str(item) for item in decoded_list(row[5])],
                [str(item) for item in decoded_list(row[6])],
                str(row[7]),
                str(row[8]),
            )
            for row in model_rows
        }
        expected_models = {
            f"{item['id']}-MODEL": (
                "HISTORICAL_SHADOW_BACKTEST",
                "ollama" if item["forecast"]["selected_by"] == "local_model" else "deterministic",
                self.model
                if item["forecast"]["selected_by"] == "local_model"
                else "bounded-forecast-fallback",
                str(item["forecast"]["candidate_id"]),
                [str(value) for value in item["forecast"]["allowlisted_candidate_ids"]],
                [str(value["id"]) for value in item["handoffs"]],
                str(item["forecast"]["selected_by"]),
                str(item["forecast"]["selection_summary"]),
            )
            for item in self._episodes
        }
        if len(model_rows) != len(expected_models) or graph_models != expected_models:
            raise GraphNotReadyError("Saved model decisions in FalkorDB do not match the replay")

        handoff_rows = self.backend.query(
            "MATCH (h:Handoff) RETURN h.id, h.run_id, h.finding_id, h.episode_id, "
            "h.from_agent, h.agent_name, h.to_agent, h.summary, h.evidence_ids, "
            "h.issued_minute, h.status, h.started_at, h.ended_at, h.query_name, h.input_hash"
        ).result_set
        graph_handoffs = {
            str(row[0]): (
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
                [str(item) for item in decoded_list(row[8])],
                int(row[9]),
                str(row[10]),
                str(row[11]),
                str(row[12]),
                str(row[13]),
                str(row[14]),
            )
            for row in handoff_rows
        }
        expected_handoffs = {
            str(item["id"]): (
                str(item["run_id"]),
                str(item["finding_id"]),
                str(item["episode_id"]),
                str(item["from_agent"]),
                str(item["agent_name"]),
                str(item["to_agent"]),
                str(item["summary"]),
                [str(value) for value in item["evidence_ids"]],
                int(item["issued_minute"]),
                str(item["status"]),
                str(item["started_at"]),
                str(item["ended_at"]),
                str(item["query_name"]),
                str(item["input_hash"]),
            )
            for item in self._handoffs
        }
        if len(handoff_rows) != len(expected_handoffs) or graph_handoffs != expected_handoffs:
            raise GraphNotReadyError("Agent handoffs in FalkorDB do not match the replay")

        agent_run_rows = self.backend.query(
            "MATCH (r:AgentRun) RETURN r.id, r.agent_id, r.episode_id, "
            "r.issued_minute, r.query_name, r.input_hash, r.started_at, r.ended_at, r.status"
        ).result_set
        graph_agent_runs = {
            str(row[0]): (
                str(row[1]),
                str(row[2]),
                int(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
                str(row[7]),
                str(row[8]),
            )
            for row in agent_run_rows
        }
        expected_agent_runs = {
            str(item["run_id"]): (
                str(item["from_agent"]),
                str(item["episode_id"]),
                int(item["issued_minute"]),
                str(item["query_name"]),
                str(item["input_hash"]),
                str(item["started_at"]),
                str(item["ended_at"]),
                "COMPLETED",
            )
            for item in self._handoffs
        }
        if (
            len(agent_run_rows) != len(expected_agent_runs)
            or graph_agent_runs != expected_agent_runs
        ):
            raise GraphNotReadyError("Saved AgentRun records in FalkorDB do not match the replay")

        finding_rows = self.backend.query(
            "MATCH (f:Finding) RETURN f.id, f.summary, f.evidence_ids, "
            "f.as_of_minute, f.source_kind, f.query_name, f.input_hash"
        ).result_set
        graph_findings = {
            str(row[0]): (
                str(row[1]),
                [str(item) for item in decoded_list(row[2])],
                int(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
            )
            for row in finding_rows
        }
        expected_findings = {
            str(item["finding_id"]): (
                str(item["summary"]),
                [str(value) for value in item["evidence_ids"]],
                int(item["issued_minute"]),
                "DERIVED_FROM_TIME_GATED_OBSERVATIONS",
                str(item["query_name"]),
                str(item["input_hash"]),
            )
            for item in self._handoffs
        }
        if len(finding_rows) != len(expected_findings) or graph_findings != expected_findings:
            raise GraphNotReadyError("Saved Finding records in FalkorDB do not match the replay")

        evaluation_rows = self.backend.query(
            "MATCH (e:ForecastEvaluation) RETURN e.id, e.status, e.revealed_count, "
            "e.evaluated_count, e.agent_visible_count, e.target_count, "
            "e.coverage_pct, e.truth_scope, e.qualifier"
        ).result_set
        graph_evaluations = {
            str(row[0]): (
                str(row[1]),
                int(row[2]),
                int(row[3]),
                int(row[4]),
                int(row[5]),
                float(row[6]),
                str(row[7]),
                str(row[8]),
            )
            for row in evaluation_rows
        }
        expected_evaluations = {
            f"{item['id']}-EVALUATION": (
                "COMPLETE",
                int(item["evaluation"]["revealed_count"]),
                int(item["evaluation"]["evaluated_count"]),
                int(item["evaluation"]["agent_visible_count"]),
                int(item["evaluation"]["target_count"]),
                float(item["evaluation"]["coverage_pct"]),
                str(item["evaluation"]["truth_scope"]),
                str(item["evaluation"]["qualifier"]),
            )
            for item in self._episodes
            if item.get("evaluation", {}).get("status") == "COMPLETE"
        }
        if (
            len(evaluation_rows) != len(expected_evaluations)
            or graph_evaluations != expected_evaluations
        ):
            raise GraphNotReadyError("Saved forecast evaluations in FalkorDB do not match the replay")

        def metric_value(value: Any) -> tuple[str, Any]:
            if value is None:
                return ("none", None)
            if isinstance(value, bool):
                return ("bool", value)
            if isinstance(value, (int, float)):
                return ("number", float(value))
            return ("text", str(value))

        metric_rows = self.backend.query(
            "MATCH (m:EvaluationMetric) RETURN m.id, m.name, m.value, "
            "m.available, m.candidate_id, m.status"
        ).result_set
        graph_metrics = {
            str(row[0]): (
                str(row[1]),
                metric_value(row[2]),
                _truthy(row[3]),
                str(row[4]),
                str(row[5]),
            )
            for row in metric_rows
        }
        expected_metrics = {
            f"{item['id']}-EVALUATION-METRIC-{name.upper()}": (
                str(name),
                metric_value(value),
                value is not None,
                str(item["forecast"]["candidate_id"]),
                "FINAL",
            )
            for item in self._episodes
            if item.get("evaluation", {}).get("status") == "COMPLETE"
            for name, value in item["evaluation"]["selected_score"].items()
        }
        if len(metric_rows) != len(expected_metrics) or graph_metrics != expected_metrics:
            raise GraphNotReadyError("Saved evaluation metrics in FalkorDB do not match the replay")

        target_rows = self.backend.query(
            "MATCH (e:ForecastEpisode)-[:TARGETS]->(m:ScheduledMovement) RETURN e.id, m.id"
        ).result_set
        graph_targets = {(str(row[0]), str(row[1])) for row in target_rows}
        expected_targets = {
            (str(item["id"]), str(movement_id))
            for item in self._episodes
            for movement_id in item["target_movement_ids"]
        }
        if len(target_rows) != len(expected_targets) or graph_targets != expected_targets:
            raise GraphNotReadyError("Forecast target links in FalkorDB do not match the replay")

        consumed_rows = self.backend.query(
            "MATCH (e:ForecastEpisode)-[:CONSUMED_HANDOFF]->(h:Handoff) RETURN e.id, h.id"
        ).result_set
        graph_consumed = {(str(row[0]), str(row[1])) for row in consumed_rows}
        expected_consumed = {
            (str(item["episode_id"]), str(item["id"])) for item in self._handoffs
        }
        if (
            len(consumed_rows) != len(expected_consumed)
            or graph_consumed != expected_consumed
        ):
            raise GraphNotReadyError("Handoff consumption links in FalkorDB do not match the replay")

        def assert_links(
            query: str,
            expected: set[tuple[str, str]],
            label: str,
        ) -> None:
            rows = self.backend.query(query).result_set
            actual = {(str(row[0]), str(row[1])) for row in rows}
            if len(rows) != len(expected) or actual != expected:
                raise GraphNotReadyError(
                    f"{label} links in FalkorDB do not match the replay"
                )

        assert_links(
            "MATCH (e:ForecastEpisode)-[:CONSIDERED]->(c:ForecastCandidate) RETURN e.id, c.id",
            {
                (str(episode["id"]), str(candidate["id"]))
                for episode in self._episodes
                for candidate in episode["candidates"]
            },
            "Forecast candidate",
        )
        assert_links(
            "MATCH (e:ForecastEpisode)-[:AWAITS_TRUTH]->(w:GroundTruthWindow) RETURN e.id, w.id",
            {
                (str(item["id"]), str(item["truth_window_id"]))
                for item in self._episodes
            },
            "Truth-window",
        )
        assert_links(
            "MATCH (w:GroundTruthWindow)-[:EVALUATES]->(m:ScheduledMovement) RETURN w.id, m.id",
            {
                (str(item["truth_window_id"]), str(movement_id))
                for item in self._episodes
                for movement_id in item["target_movement_ids"]
            },
            "Truth target",
        )
        assert_links(
            "MATCH (e:ForecastEpisode)-[:USED_MODEL]->(m:ModelRun) RETURN e.id, m.id",
            {(str(item["id"]), f"{item['id']}-MODEL") for item in self._episodes},
            "Model-use",
        )
        assert_links(
            "MATCH (m:ModelRun)-[:SELECTED]->(f:Forecast) RETURN m.id, f.id",
            {
                (f"{item['id']}-MODEL", str(item["forecast"]["id"]))
                for item in self._episodes
            },
            "Model-selection",
        )
        assert_links(
            "MATCH (e:ForecastEpisode)-[:USED_RUN]->(r:AgentRun) RETURN e.id, r.id",
            {
                (str(item["episode_id"]), str(item["run_id"]))
                for item in self._handoffs
            },
            "Agent-run",
        )
        assert_links(
            "MATCH (a:BacktestAgent)-[:STARTED]->(r:AgentRun) RETURN a.id, r.id",
            {
                (str(item["from_agent"]), str(item["run_id"]))
                for item in self._handoffs
            },
            "Agent-start",
        )
        assert_links(
            "MATCH (r:AgentRun)-[:PRODUCED]->(f:Finding) RETURN r.id, f.id",
            {
                (str(item["run_id"]), str(item["finding_id"]))
                for item in self._handoffs
            },
            "Run-finding",
        )
        assert_links(
            "MATCH (f:Finding)-[:BECAME]->(h:Handoff) RETURN f.id, h.id",
            {
                (str(item["finding_id"]), str(item["id"]))
                for item in self._handoffs
            },
            "Finding-handoff",
        )
        assert_links(
            "MATCH (h:Handoff)-[:ADDRESSED_TO]->(c:BacktestCoordinator) RETURN h.id, c.id",
            {(str(item["id"]), "BT-AGENT-COORD") for item in self._handoffs},
            "Coordinator-address",
        )
        complete_episodes = [
            item
            for item in self._episodes
            if item.get("evaluation", {}).get("status") == "COMPLETE"
        ]
        assert_links(
            "MATCH (f:Forecast)-[:SCORED_AGAINST]->(e:ForecastEvaluation) RETURN f.id, e.id",
            {
                (str(item["forecast"]["id"]), f"{item['id']}-EVALUATION")
                for item in complete_episodes
            },
            "Forecast-score",
        )
        assert_links(
            "MATCH (e:ForecastEvaluation)-[:USED_TRUTH_WINDOW]->(w:GroundTruthWindow) RETURN e.id, w.id",
            {
                (f"{item['id']}-EVALUATION", str(item["truth_window_id"]))
                for item in complete_episodes
            },
            "Evaluation-truth",
        )
        assert_links(
            "MATCH (e:ForecastEvaluation)-[:HAS_METRIC]->(m:EvaluationMetric) RETURN e.id, m.id",
            {
                (
                    f"{item['id']}-EVALUATION",
                    f"{item['id']}-EVALUATION-METRIC-{name.upper()}",
                )
                for item in complete_episodes
                for name in item["evaluation"]["selected_score"]
            },
            "Evaluation-metric",
        )

    def _ingest_weather(self, as_of_minute: int) -> list[dict[str, Any]]:
        added: list[dict[str, Any]] = []
        for item in self.weather:
            if item["valid_minute"] > as_of_minute or item["evidence_id"] in self._weather_ids:
                continue
            self._create_node(
                "WeatherObservation",
                {
                    "id": item["evidence_id"],
                    "valid_local": item["valid_local"],
                    "valid_minute": item["valid_minute"],
                    "observed_json": item["observed"],
                    "source_kind": item["source_kind"],
                },
            )
            self._create_edge(self.pack_id, "OBSERVED_WEATHER", item["evidence_id"])
            self._weather_ids.add(item["evidence_id"])
            added.append(item)
        return added

    def _weather_visible(self, as_of_minute: int) -> list[dict[str, Any]]:
        return [item for item in self.weather if item["valid_minute"] <= as_of_minute]

    @staticmethod
    def _weather_hazard(item: Mapping[str, Any] | None) -> tuple[int, list[str]]:
        if not item:
            return 0, []
        observed = item.get("observed", {}) if isinstance(item.get("observed"), Mapping) else {}
        weather_codes = str(
            _first(
                observed,
                ("weather_codes", "present_weather", "present_weather_codes"),
                "",
            )
        ).upper()
        weather_tokens = weather_codes.split()
        score, signals = 0, []
        # Only the present-weather field describes phenomena active at this
        # observation. Raw METAR remarks such as TSE43 or TSB20E47 record that
        # thunder ended and must not be interpreted as a current thunderstorm.
        if any("TS" in token for token in weather_tokens):
            score += 4
            signals.append("thunderstorm")
        precipitation_codes = ("DZ", "RA", "SN", "SG", "PL", "GR", "GS")
        visibility_codes = ("BR", "FG", "FU", "HZ")
        if any(
            token.startswith("+")
            and any(code in token for code in precipitation_codes)
            for token in weather_tokens
        ):
            score += 2
            signals.append("heavy precipitation")
        elif any(
            any(code in token for code in precipitation_codes + visibility_codes)
            for token in weather_tokens
        ):
            score += 1
            signals.append("adverse precipitation or visibility")
        visibility = _number(_first(observed, ("visibility_sm", "visibility_miles"), 99), 99)
        gust = _number(_first(observed, ("wind_gust_kt", "wind_gust_knots"), 0))
        wind = _number(_first(observed, ("wind_speed_kt", "wind_speed_knots"), 0))
        ceiling_values: list[float] = []
        for key, value in observed.items():
            normalized_key = str(key).lower()
            parsed_value = _optional_number(value)
            if parsed_value is None:
                continue
            if "ceiling" in normalized_key:
                ceiling_values.append(parsed_value)
                continue
            layer = re.fullmatch(r"sky_base_(\d+)(?:_ft)?", normalized_key)
            if not layer:
                continue
            cover = str(observed.get(f"sky_cover_{layer.group(1)}", "")).upper()
            if cover in {"BKN", "OVC", "VV"}:
                ceiling_values.append(parsed_value)
        if visibility < 3:
            score += 2
            signals.append(f"visibility {visibility:g} sm")
        if gust >= 25:
            score += 2
            signals.append(f"gust {gust:g} kt")
        elif wind >= 20:
            score += 1
            signals.append(f"wind {wind:g} kt")
        if ceiling_values and min(ceiling_values) < 1000:
            score += 2
            signals.append(f"ceiling {min(ceiling_values):g} ft")
        return min(score, 10), signals

    def _target_movements(
        self,
        start: int,
        end: int,
        *,
        exclude_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        excluded = exclude_ids or set()
        return sorted(
            (
                item
                for item in self.schedule.values()
                if start <= int(item["scheduled_minute"]) < end
                and item["movement_id"] not in excluded
            ),
            key=lambda item: (item["scheduled_minute"], item["movement_id"]),
        )

    def _recent_outcomes(self, minute: int, window: int = 60) -> list[dict[str, Any]]:
        return [
            item
            for item in self._revealed.values()
            if minute - window < int(item["reveal_minute"]) <= minute
        ]

    def _record_handoffs(
        self,
        *,
        episode_id: str,
        issued_minute: int,
        targets: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
        """Run six allowlisted Cypher reads, then persist and re-read handoffs.

        The queries can only see schedule nodes, weather observations already
        ingested by the replay clock, and outcomes already materialized by
        ``_reveal_due``.  Raw sealed truth is never present in this graph.
        """

        horizon_end = min(DAY_MINUTES, issued_minute + 60)
        recent_start = issued_minute - 60
        query_specs = {
            "WEATHER_AT_WATERMARK": (
                "MATCH (w:WeatherObservation) "
                "WHERE w.valid_minute <= $minute "
                "RETURN w.id, w.valid_minute, w.observed_json "
                "ORDER BY w.valid_minute DESC LIMIT 1",
                {"minute": issued_minute},
            ),
            "NEXT_HOUR_DEMAND": (
                "MATCH (m:ScheduledMovement) "
                "WHERE m.scheduled_minute >= $start AND m.scheduled_minute < $end "
                "RETURN m.id, m.direction, m.scheduled_minute "
                "ORDER BY m.scheduled_minute",
                {"start": issued_minute, "end": horizon_end},
            ),
            "NEXT_HOUR_ARRIVALS": (
                "MATCH (m:ScheduledMovement) "
                "WHERE m.scheduled_minute >= $start AND m.scheduled_minute < $end "
                "AND m.direction = 'ARRIVAL' "
                "RETURN m.id, m.scheduled_minute ORDER BY m.scheduled_minute",
                {"start": issued_minute, "end": horizon_end},
            ),
            "NEXT_HOUR_DEPARTURES": (
                "MATCH (m:ScheduledMovement) "
                "WHERE m.scheduled_minute >= $start AND m.scheduled_minute < $end "
                "AND m.direction = 'DEPARTURE' "
                "RETURN m.id, m.scheduled_minute ORDER BY m.scheduled_minute",
                {"start": issued_minute, "end": horizon_end},
            ),
            "MATURED_DISRUPTIONS": (
                "MATCH (o:ObservedOutcome) "
                "WHERE o.reveal_minute > $recent_start AND o.reveal_minute <= $minute "
                "RETURN o.id, o.movement_id, o.direction, o.delay_minutes, "
                "o.cancelled, o.diverted, o.reveal_minute ORDER BY o.movement_id",
                {"recent_start": recent_start, "minute": issued_minute},
            ),
            "FORECAST_MEMORY": (
                "MATCH (e:ForecastEvaluation) "
                "RETURN e.id, e.status ORDER BY e.id",
                {},
            ),
        }

        def execute_query(name: str) -> dict[str, Any]:
            query, params = query_specs[name]
            started = datetime.now().astimezone().isoformat(timespec="milliseconds")
            rows = list(self.backend.query(query, params).result_set)
            ended = datetime.now().astimezone().isoformat(timespec="milliseconds")
            return {
                "query_name": name,
                "rows": rows,
                "started_at": started,
                "ended_at": ended,
            }

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(query_specs), thread_name_prefix="backtest-agent"
        ) as pool:
            futures = {
                name: pool.submit(execute_query, name) for name in query_specs
            }
            reads = {name: future.result() for name, future in futures.items()}

        expected_window = self._target_movements(issued_minute, horizon_end)
        expected_window_rows = {
            (
                item["movement_id"],
                item["direction"],
                int(item["scheduled_minute"]),
            )
            for item in expected_window
        }
        target_ids = {item["movement_id"] for item in targets}
        raw_demand_rows = reads["NEXT_HOUR_DEMAND"]["rows"]
        graph_window_rows = {
            (str(row[0]), str(row[1]), int(row[2])) for row in raw_demand_rows
        }
        if (
            len(raw_demand_rows) != len(expected_window_rows)
            or graph_window_rows != expected_window_rows
        ):
            raise GraphNotReadyError(
                "FalkorDB next-hour demand does not match the checked-in schedule"
            )

        raw_arrival_rows = reads["NEXT_HOUR_ARRIVALS"]["rows"]
        raw_departure_rows = reads["NEXT_HOUR_DEPARTURES"]["rows"]
        expected_arrival_rows = {
            (item["movement_id"], int(item["scheduled_minute"]))
            for item in expected_window
            if item["direction"] == "ARRIVAL"
        }
        expected_departure_rows = {
            (item["movement_id"], int(item["scheduled_minute"]))
            for item in expected_window
            if item["direction"] == "DEPARTURE"
        }
        if (
            len(raw_arrival_rows) != len(expected_arrival_rows)
            or {(str(row[0]), int(row[1])) for row in raw_arrival_rows}
            != expected_arrival_rows
        ):
            raise GraphNotReadyError("FalkorDB next-hour arrivals do not match the checked-in schedule")
        if (
            len(raw_departure_rows) != len(expected_departure_rows)
            or {(str(row[0]), int(row[1])) for row in raw_departure_rows}
            != expected_departure_rows
        ):
            raise GraphNotReadyError("FalkorDB next-hour departures do not match the checked-in schedule")

        demand_rows = [
            row for row in raw_demand_rows if str(row[0]) in target_ids
        ]
        arrival_rows = [
            row for row in reads["NEXT_HOUR_ARRIVALS"]["rows"] if str(row[0]) in target_ids
        ]
        departure_rows = [
            row for row in reads["NEXT_HOUR_DEPARTURES"]["rows"] if str(row[0]) in target_ids
        ]
        weather_rows = reads["WEATHER_AT_WATERMARK"]["rows"]
        latest_weather: dict[str, Any] | None = None
        if weather_rows:
            observed_raw = weather_rows[0][2]
            try:
                observed = json.loads(observed_raw) if isinstance(observed_raw, str) else {}
            except json.JSONDecodeError:
                observed = {}
            latest_weather = {
                "evidence_id": str(weather_rows[0][0]),
                "valid_minute": int(weather_rows[0][1]),
                "observed": observed,
            }
        expected_weather = self._weather_visible(issued_minute)
        expected_latest_weather = expected_weather[-1] if expected_weather else None
        if expected_latest_weather is None and weather_rows:
            raise GraphNotReadyError("FalkorDB exposed weather before the replay watermark")
        if expected_latest_weather is not None:
            expected_weather_identity = (
                str(expected_latest_weather["evidence_id"]),
                int(expected_latest_weather["valid_minute"]),
                dict(expected_latest_weather["observed"]),
            )
            graph_weather_identity = (
                str(latest_weather["evidence_id"]) if latest_weather else "",
                int(latest_weather["valid_minute"]) if latest_weather else -1,
                dict(latest_weather["observed"]) if latest_weather else {},
            )
            if len(weather_rows) != 1 or graph_weather_identity != expected_weather_identity:
                raise GraphNotReadyError("FalkorDB weather evidence does not match the replay watermark")
        hazard_score, signals = self._weather_hazard(latest_weather)

        outcome_rows = reads["MATURED_DISRUPTIONS"]["rows"]
        graph_recent = [
            {
                "movement_id": str(row[1]),
                "direction": str(row[2]),
                "delay_minutes": _optional_number(row[3]),
                "cancelled": _truthy(row[4]),
                "diverted": _truthy(row[5]),
                "reveal_minute": int(row[6]),
            }
            for row in outcome_rows
        ]
        expected_recent = {
            item["movement_id"]: item for item in self._recent_outcomes(issued_minute)
        }
        graph_recent_by_id = {item["movement_id"]: item for item in graph_recent}
        if len(graph_recent) != len(expected_recent) or set(graph_recent_by_id) != set(expected_recent):
            raise GraphNotReadyError("FalkorDB matured outcomes do not match the replay watermark")
        for movement_id, expected in expected_recent.items():
            actual = graph_recent_by_id[movement_id]
            expected_delay = _optional_number(expected.get("delay_minutes"))
            if (
                actual["direction"] != str(expected["direction"])
                or actual["delay_minutes"] != expected_delay
                or actual["cancelled"] != bool(expected["cancelled"])
                or actual["diverted"] != bool(expected["diverted"])
                or actual["reveal_minute"] != int(expected["reveal_minute"])
            ):
                raise GraphNotReadyError(
                    f"FalkorDB outcome evidence changed for {movement_id}"
                )
        recent_arrivals = [row for row in outcome_rows if str(row[2]) == "ARRIVAL"]
        recent_departures = [row for row in outcome_rows if str(row[2]) == "DEPARTURE"]
        disrupted_rows = [
            row
            for row in outcome_rows
            if _truthy(row[4])
            or _truthy(row[5])
            or (_optional_number(row[3]) is not None and float(row[3]) >= 15)
        ]
        memory_rows = reads["FORECAST_MEMORY"]["rows"]
        expected_memory = {
            f"{item['id']}-EVALUATION": "COMPLETE"
            for item in self._episodes
            if item.get("evaluation", {}).get("status") == "COMPLETE"
        }
        graph_memory = {str(row[0]): str(row[1]) for row in memory_rows}
        if len(memory_rows) != len(expected_memory) or graph_memory != expected_memory:
            raise GraphNotReadyError(
                "FalkorDB forecast memory does not match completed replay evaluations"
            )
        specs = (
            (
                BACKTEST_AGENTS[0],
                "WEATHER_AT_WATERMARK",
                f"Latest observed weather has hazard score {hazard_score}/10"
                + (f" ({', '.join(signals)})." if signals else "."),
                [latest_weather["evidence_id"]] if latest_weather else [],
            ),
            (
                BACKTEST_AGENTS[1],
                "NEXT_HOUR_DEMAND",
                f"The next hour contains {len(demand_rows)} unresolved scheduled ORD movements: {len(arrival_rows)} arrivals and {len(departure_rows)} departures.",
                [str(row[0]) for row in demand_rows[:40]],
            ),
            (
                BACKTEST_AGENTS[2],
                "NEXT_HOUR_ARRIVALS",
                f"{len(arrival_rows)} unresolved arrivals are scheduled next hour; {len(recent_arrivals)} arrival outcomes matured in the trailing hour.",
                [str(row[0]) for row in arrival_rows[:30]],
            ),
            (
                BACKTEST_AGENTS[3],
                "NEXT_HOUR_DEPARTURES",
                f"{len(departure_rows)} unresolved departures are scheduled next hour; {len(recent_departures)} departure outcomes matured in the trailing hour.",
                [str(row[0]) for row in departure_rows[:30]],
            ),
            (
                BACKTEST_AGENTS[4],
                "MATURED_DISRUPTIONS",
                f"{len(disrupted_rows)} of {len(outcome_rows)} newly matured movements were delayed at least 15 minutes, cancelled, or diverted.",
                [str(row[1]) for row in disrupted_rows[:30]],
            ),
            (
                BACKTEST_AGENTS[5],
                "FORECAST_MEMORY",
                f"{len(memory_rows)} earlier completed forecast evaluations are available as memory; raw sealed outcomes are absent.",
                [str(row[0]) for row in memory_rows[-12:]],
            ),
        )
        handoffs: list[dict[str, Any]] = []
        token = str(time.time_ns())
        for index, ((agent_id, name), query_name, summary, evidence_ids) in enumerate(specs, start=1):
            read = reads[query_name]
            started = str(read["started_at"])
            run_id = f"{episode_id}-RUN-{index}-{token}"
            finding_id = f"{episode_id}-FINDING-{index}-{token}"
            handoff_id = f"{episode_id}-HANDOFF-{index}-{token}"
            ended = str(read["ended_at"])
            input_hash = hashlib.sha256(
                _json(
                    {
                        "query_name": query_name,
                        "issued_minute": issued_minute,
                        "evidence_ids": evidence_ids,
                    }
                ).encode()
            ).hexdigest()
            record = {
                "id": handoff_id,
                "run_id": run_id,
                "finding_id": finding_id,
                "episode_id": episode_id,
                "from_agent": agent_id,
                "agent_name": name,
                "to_agent": "BT-AGENT-COORD",
                "summary": summary,
                "evidence_ids": evidence_ids,
                "query_name": query_name,
                "input_hash": input_hash,
                "issued_minute": issued_minute,
                "status": "CONSUMED",
                "started_at": started,
                "ended_at": ended,
            }
            self._create_node(
                "AgentRun",
                {
                    "id": run_id,
                    "agent_id": agent_id,
                    "episode_id": episode_id,
                    "issued_minute": issued_minute,
                    "query_name": query_name,
                    "input_hash": input_hash,
                    "started_at": started,
                    "ended_at": ended,
                    "status": "COMPLETED",
                },
            )
            self._create_node(
                "Finding",
                {
                    "id": finding_id,
                    "summary": summary,
                    "evidence_ids": evidence_ids,
                    "as_of_minute": issued_minute,
                    "source_kind": "DERIVED_FROM_TIME_GATED_OBSERVATIONS",
                    "query_name": query_name,
                    "input_hash": input_hash,
                },
            )
            self._create_node("Handoff", record)
            self._create_edge(agent_id, "STARTED", run_id)
            self._create_edge(run_id, "PRODUCED", finding_id)
            self._create_edge(finding_id, "BECAME", handoff_id)
            self._create_edge(handoff_id, "ADDRESSED_TO", "BT-AGENT-COORD")
            self._create_edge(episode_id, "USED_RUN", run_id)
            self._create_edge(episode_id, "CONSUMED_HANDOFF", handoff_id)
            handoffs.append(record)

        # The coordinator consumes a graph re-read, not the Python objects that
        # created the nodes. This makes FalkorDB the coordination boundary.
        coordinator_rows = self.backend.query(
            "MATCH (h:Handoff {episode_id: $episode_id}) "
            "RETURN h.id, h.run_id, h.finding_id, h.from_agent, h.agent_name, "
            "h.to_agent, h.summary, h.evidence_ids, h.issued_minute, h.status, "
            "h.started_at, h.ended_at, h.query_name, h.input_hash ORDER BY h.from_agent",
            {"episode_id": episode_id},
        ).result_set
        coordinator_handoffs: list[dict[str, Any]] = []
        for row in coordinator_rows:
            evidence_value = row[7]
            try:
                evidence = json.loads(evidence_value) if isinstance(evidence_value, str) else []
            except json.JSONDecodeError:
                evidence = []
            coordinator_handoffs.append(
                {
                    "id": str(row[0]),
                    "run_id": str(row[1]),
                    "finding_id": str(row[2]),
                    "episode_id": episode_id,
                    "from_agent": str(row[3]),
                    "agent_name": str(row[4]),
                    "to_agent": str(row[5]),
                    "summary": str(row[6]),
                    "evidence_ids": evidence,
                    "issued_minute": int(row[8]),
                    "status": str(row[9]),
                    "started_at": str(row[10]),
                    "ended_at": str(row[11]),
                    "query_name": str(row[12]),
                    "input_hash": str(row[13]),
                }
            )
        if len(coordinator_handoffs) != len(BACKTEST_AGENTS):
            raise RuntimeError("Coordinator did not receive all six FalkorDB handoffs")
        self._handoffs.extend(coordinator_handoffs)
        return coordinator_handoffs, latest_weather, graph_recent

    def _forecast_candidates(
        self,
        *,
        episode_id: str,
        targets: list[dict[str, Any]],
        latest_weather: Mapping[str, Any] | None,
        recent: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        demand = len(targets)
        hazard_score, hazard_signals = self._weather_hazard(latest_weather)
        recent_delays = [
            item["delay_minutes"]
            for item in recent
            if not item["cancelled"] and item["delay_minutes"] is not None
        ]
        recent_mean = sum(recent_delays) / len(recent_delays) if recent_delays else 0.0
        recent_cancel_rate = (
            100 * sum(item["cancelled"] for item in recent) / len(recent) if recent else 0.0
        )
        recent_disruption_rate = (
            100
            * sum(
                item["cancelled"]
                or item["diverted"]
                or (
                    item["delay_minutes"] is not None
                    and item["delay_minutes"] >= 15
                )
                for item in recent
            )
            / len(recent)
            if recent
            else 0.0
        )

        def candidate(
            key: str,
            label: str,
            cancel_rate: float,
            disruption_rate: float,
            mean_delay: float,
            explanation: str,
        ) -> dict[str, Any]:
            return {
                "id": f"{episode_id}-CAND-{key}",
                "method": key,
                "label": label,
                "predicted_movements": demand,
                "predicted_cancel_rate_pct": round(max(0.0, min(100.0, cancel_rate)), 1),
                "predicted_cancelled_count": round(demand * max(0.0, min(1.0, cancel_rate / 100))),
                "predicted_disruption_rate_pct": round(max(0.0, min(100.0, disruption_rate)), 1),
                "predicted_disrupted_count": round(demand * max(0.0, min(1.0, disruption_rate / 100))),
                "predicted_mean_delay_minutes": round(max(0.0, mean_delay), 1),
                "predicted_delay_band": _delay_band(mean_delay),
                "explanation": explanation,
                "source_kind": "MODEL_FORECAST",
            }

        persistence_cancel = recent_cancel_rate
        persistence_disruption = recent_disruption_rate
        persistence_delay = recent_mean
        weather_cancel = max(persistence_cancel, hazard_score * 4.0)
        weather_disruption = max(persistence_disruption, 5.0 + hazard_score * 9.0)
        weather_delay = persistence_delay + hazard_score * 12.0
        candidates = [
            candidate(
                "SCHEDULE_BASELINE",
                "Schedule-only baseline",
                0,
                5,
                0,
                "Assumes the published schedule operates without material disruption.",
            ),
            candidate(
                "RECENT_FLOW",
                "Trailing observed flow",
                persistence_cancel,
                persistence_disruption,
                persistence_delay,
                f"Carries forward {len(recent)} outcomes that had matured in the trailing hour.",
            ),
            candidate(
                "WEATHER_STRESS",
                "Weather-sensitive stress case",
                weather_cancel,
                weather_disruption,
                weather_delay,
                f"Uses only the latest time-gated weather hazard score {hazard_score}/10"
                + (f": {', '.join(hazard_signals)}." if hazard_signals else "."),
            ),
        ]
        context = {
            "scheduled_movements": demand,
            "recent_matured_outcomes": len(recent),
            "recent_mean_delay_minutes": round(recent_mean, 1),
            "recent_cancel_rate_pct": round(recent_cancel_rate, 1),
            "recent_disruption_rate_pct": round(recent_disruption_rate, 1),
            "weather_hazard_score": hazard_score,
            "weather_signals": hazard_signals,
        }
        return candidates, context

    def _local_model_choice(
        self,
        candidates: list[dict[str, Any]],
        handoffs: list[dict[str, Any]],
        context: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        hazard = int(context.get("weather_hazard_score", 0))
        recent_count = int(context.get("recent_matured_outcomes", 0))
        fallback_method = (
            "WEATHER_STRESS" if hazard >= 2 else "RECENT_FLOW" if recent_count >= 3 else "SCHEDULE_BASELINE"
        )
        fallback = next(
            item["id"] for item in candidates if item["method"] == fallback_method
        )
        if not self.enable_llm:
            return fallback, "deterministic_fallback", f"Deterministic fallback selected {fallback_method} from observed context."
        prompt = {
            "task": "Choose exactly one allowlisted next-hour aggregate forecast. Do not invent an ID or operational action.",
            "context": dict(context),
            "handoffs": [
                {"from_agent": item["from_agent"], "summary": item["summary"]}
                for item in handoffs
            ],
            "candidates": candidates,
            "response_schema": {"candidate_id": "one supplied ID", "summary": "short evidence-based reason"},
        }
        body = json.dumps(
            {
                "model": self.model,
                "stream": False,
                "format": "json",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return one strict JSON object with exactly candidate_id and summary. "
                            "Choose one supplied candidate_id. In summary, cite at least one "
                            "provided number or weather signal and explain the choice in under 25 words."
                        ),
                    },
                    {"role": "user", "content": _json(prompt)},
                ],
                "options": {"temperature": 0},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.ollama_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.ollama_timeout) as response:
                payload = json.loads(response.read(1_000_000))
            content = payload.get("message", {}).get("content", "")
            match = re.search(r"\{.*\}", str(content), flags=re.DOTALL)
            decoded = json.loads(match.group(0) if match else content)
            selected = str(decoded.get("candidate_id", ""))
            allowlist = {item["id"] for item in candidates}
            if selected not in allowlist:
                raise ValueError("model returned an ID outside the allowlist")
            reason = str(
                decoded.get("summary")
                or decoded.get("reason")
                or decoded.get("rationale")
                or decoded.get("explanation")
                or ""
            ).strip()
            selected_method = next(
                item["method"] for item in candidates if item["id"] == selected
            )
            if not reason:
                reason = (
                    f"Local model returned {selected_method}; its response did not include a rationale."
                )
            return selected, "local_model", reason[:500]
        except Exception as exc:
            return fallback, "deterministic_fallback", f"Local model unavailable or invalid ({type(exc).__name__}); selected {fallback_method}."

    def _issue_forecast(self, issued_minute: int) -> dict[str, Any] | None:
        if issued_minute >= DAY_MINUTES:
            return None
        episode_id = f"BT-EPISODE-{self.pack_id}-{issued_minute:04d}"
        if any(item["id"] == episode_id for item in self._episodes):
            return None
        self._assert_graph_state(check_run_clock=False)
        horizon_end = min(DAY_MINUTES, issued_minute + 60)
        targets = self._target_movements(
            issued_minute,
            horizon_end,
            exclude_ids=set(self._revealed),
        )
        self._create_node(
            "ForecastEpisode",
            {
                "id": episode_id,
                "issued_minute": issued_minute,
                "horizon_end_minute": horizon_end,
                "input_watermark": _clock(issued_minute),
                "status": "FORECASTED",
                "target_movement_ids": [item["movement_id"] for item in targets],
            },
        )
        self._create_edge(self.pack_id, "HAS_EPISODE", episode_id)
        truth_window_id = f"{episode_id}-TRUTH-WINDOW"
        self._create_node(
            "GroundTruthWindow",
            {
                "id": truth_window_id,
                "horizon_start_minute": issued_minute,
                "horizon_end_minute": horizon_end,
                "target_count": len(targets),
                "revealed_count": 0,
                "status": "SEALED",
                "truth_policy": "NO_FUTURE_OUTCOME_BEFORE_REVEAL",
            },
        )
        self._create_edge(episode_id, "AWAITS_TRUTH", truth_window_id)
        for target in targets:
            self._create_edge(episode_id, "TARGETS", target["movement_id"])
            self._create_edge(truth_window_id, "EVALUATES", target["movement_id"])
        handoffs, graph_latest_weather, graph_recent = self._record_handoffs(
            episode_id=episode_id,
            issued_minute=issued_minute,
            targets=targets,
        )
        candidates, context = self._forecast_candidates(
            episode_id=episode_id,
            targets=targets,
            latest_weather=graph_latest_weather,
            recent=graph_recent,
        )
        for item in candidates:
            self._create_node("ForecastCandidate", item)
            self._create_edge(episode_id, "CONSIDERED", item["id"])
        selected_id, source, summary = self._local_model_choice(candidates, handoffs, context)
        selected = next(item for item in candidates if item["id"] == selected_id)
        model_run_id = f"{episode_id}-MODEL"
        forecast_id = f"{episode_id}-FORECAST"
        self._create_node(
            "ModelRun",
            {
                "id": model_run_id,
                "runtime_kind": "HISTORICAL_SHADOW_BACKTEST",
                "provider": "ollama" if source == "local_model" else "deterministic",
                "model": self.model if source == "local_model" else "bounded-forecast-fallback",
                "allowlisted_candidate_ids": [item["id"] for item in candidates],
                "selected_candidate_id": selected_id,
                "coordinator_input_handoff_ids": [item["id"] for item in handoffs],
                "decision_source": source,
                "summary": summary,
            },
        )
        forecast = {
            **selected,
            "id": forecast_id,
            "candidate_id": selected_id,
            "episode_id": episode_id,
            "issued_minute": issued_minute,
            "issued_time": _clock(issued_minute),
            "horizon_start_minute": issued_minute,
            "horizon_end_minute": horizon_end,
            "horizon_label": f"{_clock(issued_minute)}–{_clock(horizon_end)}",
            "selected_by": source,
            "selection_summary": summary,
            "allowlisted_candidate_ids": [item["id"] for item in candidates],
        }
        self._create_node("Forecast", {**forecast, "payload_json": forecast})
        self._create_edge(episode_id, "USED_MODEL", model_run_id)
        self._create_edge(model_run_id, "SELECTED", forecast_id)
        episode = {
            "id": episode_id,
            "issued_minute": issued_minute,
            "issued_time": _clock(issued_minute),
            "horizon_end_minute": horizon_end,
            "horizon_label": forecast["horizon_label"],
            "target_movement_ids": [item["movement_id"] for item in targets],
            "truth_window_id": truth_window_id,
            "truth_window_state": {
                "status": "SEALED",
                "revealed_count": 0,
                "agent_visible_count": None,
                "coverage_pct": None,
                "evaluation_scope": "",
                "actual_json": "",
            },
            "input": context,
            "handoffs": handoffs,
            "candidates": candidates,
            "forecast": forecast,
            "evaluation": {
                "status": "PENDING",
                "revealed_count": 0,
                "evaluated_count": 0,
                "agent_visible_count": 0,
                "target_count": len(targets),
                "coverage_pct": 100.0 if not targets else 0.0,
                "truth_scope": "SEALED",
                "qualifier": "The forecast is locked; its horizon has not closed yet.",
            },
        }
        self._episodes.append(episode)
        return episode

    def _reveal_due(self, minute: int) -> list[dict[str, Any]]:
        due = self.truth_evaluator.reveal(
            as_of_minute=minute,
            already_revealed=set(self._revealed),
        )
        for item in due:
            node_id = f"BT-OUTCOME-{item['movement_id']}"
            self._create_node("ObservedOutcome", {"id": node_id, **item})
            self._create_edge(item["movement_id"], "MATURED_AS", node_id)
            self._revealed[item["movement_id"]] = item
        return due

    @staticmethod
    def _score(candidate: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
        predicted_mean = _optional_number(
            candidate.get("predicted_mean_delay_minutes")
        )
        actual_mean = _optional_number(actual.get("mean_delay_minutes"))
        delay_error = (
            round(abs(predicted_mean - actual_mean), 1)
            if predicted_mean is not None and actual_mean is not None
            else None
        )
        actual_band = str(actual.get("mean_delay_band", "UNAVAILABLE"))
        return {
            "cancel_rate_abs_error_pp": round(
                abs(_number(candidate.get("predicted_cancel_rate_pct")) - _number(actual.get("cancel_rate_pct"))), 1
            ),
            "mean_delay_abs_error_minutes": delay_error,
            "disruption_rate_abs_error_pp": round(
                abs(_number(candidate.get("predicted_disruption_rate_pct")) - _number(actual.get("disruption_rate_pct"))), 1
            ),
            "delay_band_match": (
                str(candidate.get("predicted_delay_band")) == actual_band
                if actual_band != "UNAVAILABLE"
                else None
            ),
        }

    @staticmethod
    def _summarize_visible(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
        """Summarize only events already visible to agents before horizon close."""

        count = len(rows)
        delays = [
            item["delay_minutes"]
            for item in rows
            if not item["cancelled"] and item["delay_minutes"] is not None
        ]
        cancelled = sum(bool(item["cancelled"]) for item in rows)
        diverted = sum(bool(item["diverted"]) for item in rows)
        disrupted = sum(
            bool(item["cancelled"])
            or bool(item["diverted"])
            or (
                item["delay_minutes"] is not None
                and item["delay_minutes"] >= 15
            )
            for item in rows
        )
        mean_delay = round(sum(delays) / len(delays), 1) if delays else None
        return {
            "movements": count,
            "cancelled": cancelled,
            "diverted": diverted,
            "disrupted": disrupted,
            "cancel_rate_pct": round(100 * cancelled / count, 1) if count else 0.0,
            "disruption_rate_pct": round(100 * disrupted / count, 1) if count else 0.0,
            "mean_delay_minutes": mean_delay,
            "mean_delay_band": _delay_band(mean_delay),
            "observed_delay_count": len(delays),
            "unavailable_delay_count": count - cancelled - len(delays),
        }

    def _persist_complete_evaluation(
        self, episode: Mapping[str, Any], evaluation: Mapping[str, Any]
    ) -> None:
        evaluation_id = f"{episode['id']}-EVALUATION"
        self._create_node("ForecastEvaluation", {"id": evaluation_id, **evaluation})
        self._create_edge(episode["forecast"]["id"], "SCORED_AGAINST", evaluation_id)
        self._create_edge(evaluation_id, "USED_TRUTH_WINDOW", episode["truth_window_id"])
        for metric_name, metric_value in evaluation["selected_score"].items():
            metric_id = f"{evaluation_id}-METRIC-{metric_name.upper()}"
            self._create_node(
                "EvaluationMetric",
                {
                    "id": metric_id,
                    "name": metric_name,
                    "value": metric_value,
                    "available": metric_value is not None,
                    "candidate_id": episode["forecast"]["candidate_id"],
                    "status": "FINAL",
                },
            )
            self._create_edge(evaluation_id, "HAS_METRIC", metric_id)

    def _update_evaluations(self, *, archive_settlement: bool = False) -> None:
        for episode in self._episodes:
            previous_status = episode.get("evaluation", {}).get("status")
            if previous_status in {"COMPLETE", "SKIPPED"}:
                continue
            target_ids = episode["target_movement_ids"]
            target_count = len(target_ids)
            visible_rows = [
                self._revealed[item] for item in target_ids if item in self._revealed
            ]
            horizon_closed = self.current_minute >= episode["horizon_end_minute"]
            actual: dict[str, Any] | None = None
            complete = False
            if target_count == 0 and horizon_closed:
                evaluated_count = 0
                coverage = 100.0
                status = "SKIPPED"
                qualifier = "No unresolved scheduled movements remained in this hour, so it is excluded from accuracy metrics."
                scope = "NO_TARGETS"
            elif archive_settlement and horizon_closed:
                # At the end of the 24-hour run, the evaluator performs a
                # retrospective archive settlement. The aggregate can be
                # scored and shown, but individual sealed rows never enter
                # agent state and there is no later forecast to influence.
                actual = self.truth_evaluator.aggregate_window(target_ids)
                evaluated_count = int(actual["movements"])
                coverage = (
                    100.0
                    if target_count == 0
                    else 100 * evaluated_count / target_count
                )
                complete = evaluated_count == target_count
                status = "COMPLETE" if complete else "PARTIAL"
                qualifier = (
                    "Complete end-of-day archive settlement; it scores the saved forecast and is never fed back to agents."
                    if complete
                    else "End-of-day settlement found one or more targets without a matching archive row."
                )
                scope = "END_OF_DAY_ARCHIVE_SETTLEMENT"
            elif visible_rows:
                actual = self._summarize_visible(visible_rows)
                evaluated_count = len(visible_rows)
                coverage = 100 * evaluated_count / target_count if target_count else 100.0
                complete = bool(horizon_closed and evaluated_count == target_count)
                status = "COMPLETE" if complete else "PARTIAL"
                qualifier = (
                    "Complete time-gated comparison; every target outcome matured before scoring."
                    if complete
                    else "Partial comparison using only operated outcomes that had actually occurred by the replay clock."
                )
                scope = "TIME_GATED_AGENT_VISIBLE_OUTCOMES"
            else:
                evaluated_count = 0
                coverage = 100.0 if target_count == 0 else 0.0
                status = "PENDING"
                qualifier = (
                    "The forecast horizon closed, but no target outcome had matured by this replay clock."
                    if horizon_closed
                    else "The forecast is locked; its horizon has not closed yet."
                )
                scope = "SEALED"
            evaluation: dict[str, Any] = {
                "status": status,
                "revealed_count": evaluated_count,
                "evaluated_count": evaluated_count,
                "agent_visible_count": len(visible_rows),
                "target_count": target_count,
                "coverage_pct": round(coverage, 1),
                "truth_scope": scope,
                "qualifier": qualifier,
            }
            if actual is not None:
                evaluation.update(
                    {
                        "actual": actual,
                        "selected_score": self._score(episode["forecast"], actual),
                        "candidate_scores": [
                            {
                                "candidate_id": candidate["id"],
                                "method": candidate["method"],
                                **self._score(candidate, actual),
                            }
                            for candidate in episode["candidates"]
                        ],
                    }
                )
            episode["evaluation"] = evaluation
            actual_json = _json(actual) if actual is not None else ""
            self.backend.query(
                "MATCH (w:GroundTruthWindow {id: $id}) SET "
                "w.status = $status, w.revealed_count = $revealed, "
                "w.agent_visible_count = $agent_visible, w.coverage_pct = $coverage, "
                "w.evaluation_scope = $scope, w.actual_json = $actual",
                {
                    "id": episode["truth_window_id"],
                    "status": status,
                    "revealed": evaluated_count,
                    "agent_visible": len(visible_rows),
                    "coverage": round(coverage, 1),
                    "scope": scope,
                    "actual": actual_json,
                },
            )
            episode["truth_window_state"] = {
                "status": status,
                "revealed_count": evaluated_count,
                "agent_visible_count": len(visible_rows),
                "coverage_pct": round(coverage, 1),
                "evaluation_scope": scope,
                "actual_json": actual_json,
            }
            if previous_status != "COMPLETE" and complete:
                self._persist_complete_evaluation(episode, evaluation)

    def _story(self, episode: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        if not episode:
            return []
        forecast = episode["forecast"]
        evaluation = episode["evaluation"]
        weather = self._weather_visible(int(episode["issued_minute"]))
        return [
            {
                "step": 1,
                "title": "What was known",
                "detail": (
                    f"{episode['input']['scheduled_movements']} scheduled movements and "
                    f"{len(weather)} weather observations were available by {episode['issued_time']}."
                ),
                "truth_status": "OBSERVED_OR_KNOWN_SCHEDULE",
            },
            {
                "step": 2,
                "title": "Six agents handed off evidence",
                "detail": f"{len(episode['handoffs'])} graph-persisted handoffs share the same input watermark.",
                "truth_status": "DERIVED_FROM_TIME_GATED_INPUTS",
            },
            {
                "step": 3,
                "title": "Forecast locked before reveal",
                "detail": (
                    f"{forecast['label']}: {forecast['predicted_mean_delay_minutes']} mean delay minutes, "
                    f"{forecast['predicted_cancel_rate_pct']}% cancellations."
                ),
                "truth_status": "MODEL_FORECAST",
            },
            {
                "step": 4,
                "title": (
                    "Recorded outcome scored"
                    if evaluation["status"] == "COMPLETE"
                    else "No target movements to score"
                    if evaluation["status"] == "SKIPPED"
                    else "Ground truth remains sealed"
                ),
                "detail": (
                    f"{evaluation['evaluated_count']} of {evaluation['target_count']} scheduled movements "
                    f"are included in this same hour's {evaluation['status'].lower()} evaluation."
                ),
                "truth_status": "OBSERVED_POST_EVENT_ARCHIVE",
            },
        ]

    def _display_episode(self) -> Mapping[str, Any] | None:
        """Pair a forecast with truth from that exact episode for the UI."""

        for episode in reversed(self._episodes):
            if episode.get("evaluation", {}).get("actual") is not None:
                return episode
        return self._episodes[-1] if self._episodes else None

    def _cumulative_scores(self) -> dict[str, Any]:
        complete_episodes = [
            item
            for item in self._episodes
            if item["evaluation"].get("status") == "COMPLETE"
            and item["evaluation"].get("selected_score")
        ]
        complete = [item["evaluation"]["selected_score"] for item in complete_episodes]
        baseline_scores = []
        for episode in complete_episodes:
            baseline = next(
                (
                    item
                    for item in episode["evaluation"].get("candidate_scores", [])
                    if item.get("method") == "SCHEDULE_BASELINE"
                ),
                None,
            )
            if baseline:
                baseline_scores.append(baseline)
        names = (
            "cancel_rate_abs_error_pp",
            "mean_delay_abs_error_minutes",
            "disruption_rate_abs_error_pp",
        )
        values_by_name = {
            name: [
                parsed
                for item in complete
                if (parsed := _optional_number(item.get(name))) is not None
            ]
            for name in names
        }
        band_matches = [
            item["delay_band_match"]
            for item in complete
            if isinstance(item.get("delay_band_match"), bool)
        ]
        selected_delay_errors = values_by_name["mean_delay_abs_error_minutes"]
        baseline_delay_errors = [
            parsed
            for item in baseline_scores
            if (
                parsed := _optional_number(
                    item.get("mean_delay_abs_error_minutes")
                )
            )
            is not None
        ]
        actuals = [
            item["evaluation"].get("actual", {})
            for item in complete_episodes
        ]
        return {
            "complete_windows": len(complete),
            "hours_scored": len(complete),
            "mean_delay_windows_scored": len(selected_delay_errors),
            "scored_target_movements": sum(
                int(item["evaluation"].get("target_count", 0))
                for item in complete_episodes
            ),
            "delay_bearing_outcomes": sum(
                int(actual.get("observed_delay_count", 0)) for actual in actuals
            ),
            "unavailable_delay_outcomes": sum(
                int(actual.get("unavailable_delay_count", 0)) for actual in actuals
            ),
            "cancelled_outcomes": sum(
                int(actual.get("cancelled", 0)) for actual in actuals
            ),
            "diverted_outcomes": sum(
                int(actual.get("diverted", 0)) for actual in actuals
            ),
            **{
                f"mean_{name}": (
                    round(sum(values_by_name[name]) / len(values_by_name[name]), 1)
                    if values_by_name[name]
                    else None
                )
                for name in names
            },
            "delay_band_accuracy_pct": (
                round(100 * sum(band_matches) / len(band_matches), 1)
                if band_matches
                else None
            ),
            "selected_mean_delay_mae_minutes": (
                round(sum(selected_delay_errors) / len(selected_delay_errors), 1)
                if selected_delay_errors
                else None
            ),
            "schedule_baseline_mean_delay_mae_minutes": (
                round(
                    sum(baseline_delay_errors) / len(baseline_delay_errors),
                    1,
                )
                if baseline_delay_errors
                else None
            ),
        }

    def tick(
        self,
        *,
        minutes: int = 60,
        hours: float | None = None,
        to_minute: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            # Validate before moving the replay clock or materializing new
            # evidence. A missing or altered graph cannot be bypassed with the
            # runtime's Python copy of the factual inputs.
            self._assert_graph_state()
            if hours is not None:
                minutes = max(1, round(float(hours) * 60))
            target = self.current_minute + max(1, int(minutes)) if to_minute is None else int(to_minute)
            if target < self.current_minute:
                raise ValueError("Backtest clock cannot move backward")
            target = min(DAY_MINUTES, target)
            hourly = list(range(((self.current_minute // 60) + 1) * 60, target + 1, 60))
            boundaries = sorted(set(hourly + [target]))
            for boundary in boundaries:
                self.current_minute = boundary
                self._ingest_weather(boundary)
                self._reveal_due(boundary)
                self._update_evaluations(
                    archive_settlement=boundary >= DAY_MINUTES
                )
                if boundary % 60 == 0 and boundary < DAY_MINUTES:
                    self._issue_forecast(boundary)
            self.backend.query(
                "MATCH (b:BacktestRun {id: $id}) SET b.current_minute = $minute, b.status = $status",
                {
                    "id": self.pack_id,
                    "minute": self.current_minute,
                    "status": "COMPLETE" if self.current_minute >= DAY_MINUTES else "FORECASTING",
                },
            )
            return self.snapshot()

    @staticmethod
    def _public_movement(
        movement: Mapping[str, Any], outcome: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        public = {
            "movement_id": movement["movement_id"],
            "direction": movement["direction"],
            "reporting_airline": movement["reporting_airline"],
            "flight_number": movement["flight_number"],
            "origin": movement["origin"],
            "dest": movement["dest"],
            "scheduled_time_local": movement["scheduled_time_local"],
            "scheduled_minute": movement["scheduled_minute"],
            "outcome_status": "REVEALED" if outcome else "SEALED",
        }
        if outcome:
            public["matured_outcome"] = {
                "actual_time_local": outcome["actual_time_local"],
                "actual_minute": outcome["actual_minute"],
                "delay_minutes": outcome["delay_minutes"],
                "cancelled": outcome["cancelled"],
                "diverted": outcome["diverted"],
                "delay_band": outcome["delay_band"],
            }
        return public

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._assert_graph_state()
            visible_weather = self._weather_visible(self.current_minute)
            display_episode = self._display_episode()
            hour_start = min(DAY_MINUTES, (self.current_minute // 60) * 60)
            current_hour = self._target_movements(hour_start, min(DAY_MINUTES, hour_start + 60))
            next_hour = self._target_movements(
                min(DAY_MINUTES, hour_start + 60),
                min(DAY_MINUTES, hour_start + 120),
            )
            return {
                "ok": True,
                "mode": "HISTORICAL_SHADOW_BACKTEST",
                "backtest_id": self.pack_id,
                "service_date": self.service_date,
                "clock": {
                    "minute": self.current_minute,
                    "label": _clock(self.current_minute),
                    "end_minute": DAY_MINUTES,
                    "complete": self.current_minute >= DAY_MINUTES,
                },
                "truth_boundary": {
                    "agent_future_outcomes_visible": False,
                    "raw_archive_rows_visible": False,
                    "archive_aggregate_visible": self.current_minute >= DAY_MINUTES,
                    "archive_settlement_complete": self.current_minute >= DAY_MINUTES,
                    "policy": "Agents receive operated movements only at recorded actual event time. Cancellation, diversion, and post-midnight labels never enter agent state; the evaluator settles saved forecasts from the sealed archive only after the 24-hour replay ends.",
                    "revealed_outcome_count": len(self._revealed),
                },
                "schedule": {
                    "movement_count": len(self.schedule),
                    "next_hour_count": len(
                        self._target_movements(self.current_minute, min(DAY_MINUTES, self.current_minute + 60))
                    ),
                },
                "movements": [
                    self._public_movement(item, self._revealed.get(item["movement_id"]))
                    for item in current_hour
                ],
                "movement_windows": {
                    "current_hour": [
                        self._public_movement(item, self._revealed.get(item["movement_id"]))
                        for item in current_hour
                    ],
                    "next_hour": [
                        self._public_movement(item, self._revealed.get(item["movement_id"]))
                        for item in next_hour
                    ],
                    "recently_revealed": sorted(
                        (
                            self._public_movement(self.schedule[movement_id], outcome)
                            for movement_id, outcome in self._revealed.items()
                            if self.current_minute - 60 < int(outcome["reveal_minute"]) <= self.current_minute
                        ),
                        key=lambda item: (item["scheduled_minute"], item["movement_id"]),
                    ),
                },
                "weather": {
                    "visible_count": len(visible_weather),
                    "latest": visible_weather[-1] if visible_weather else None,
                },
                "latest_episode": self._episodes[-1] if self._episodes else None,
                "display_episode": display_episode,
                "story": self._story(display_episode),
                "cumulative_scores": self._cumulative_scores(),
                "episode_count": len(self._episodes),
                "handoff_count": len(self._handoffs),
                "graph": {"name": self.graph_name, "mode": self.backend.mode, "endpoint": self.backend.endpoint},
                "claims": {
                    "synthetic_operational_events": 0,
                    "counterfactual_saved_minutes": None,
                    "scope": "Time-gated forecasts with end-of-day archive scoring; no causal impact claim.",
                },
            }

    def results(self) -> dict[str, Any]:
        with self._lock:
            self._assert_graph_state()
            # Time-gated aggregates appear as outcomes actually mature. Any
            # unresolved window is settled from the sealed archive only after
            # the replay reaches 24:00. Raw sealed rows are never serialized
            # here or fed back to agents.
            completed = [item for item in self._episodes if item["evaluation"]["status"] == "COMPLETE"]
            display_episode = self._display_episode()
            return {
                "ok": True,
                "mode": "HISTORICAL_SHADOW_BACKTEST",
                "backtest_id": self.pack_id,
                "clock": {"minute": self.current_minute, "label": _clock(self.current_minute)},
                "agent_future_outcomes_visible": False,
                "raw_archive_rows_visible": False,
                "archive_aggregate_visible": self.current_minute >= DAY_MINUTES,
                "archive_settlement_complete": self.current_minute >= DAY_MINUTES,
                "summary": {
                    "forecast_count": len(self._episodes),
                    "complete_evaluation_count": len(completed),
                    "revealed_outcome_count": len(self._revealed),
                    "scheduled_movement_count": len(self.schedule),
                },
                "cumulative_scores": self._cumulative_scores(),
                "display_episode_id": (
                    display_episode["id"] if display_episode else None
                ),
                "story": self._story(display_episode),
                "episodes": [
                    {
                        "id": item["id"],
                        "issued_minute": item["issued_minute"],
                        "issued_time": item["issued_time"],
                        "horizon_label": item["horizon_label"],
                        "input": item["input"],
                        "handoff_count": len(item["handoffs"]),
                        "forecast": item["forecast"],
                        "evaluation": item["evaluation"],
                    }
                    for item in self._episodes
                ],
                "qualifier": "Observed ground truth scores forecast accuracy only. No historical action or saved-minutes claim is made.",
            }

    def health(self) -> dict[str, Any]:
        """Verify that the active factual graph still matches its watermark."""

        with self._lock:
            self._assert_graph_state()
            return {
                "ok": True,
                "status": "healthy",
                "mode": "HISTORICAL_SHADOW_BACKTEST",
                "backtest_id": self.pack_id,
                "clock": {
                    "minute": self.current_minute,
                    "label": _clock(self.current_minute),
                },
                "graph": {
                    "name": self.graph_name,
                    "mode": self.backend.mode,
                    "endpoint": self.backend.endpoint,
                },
            }
