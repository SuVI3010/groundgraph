from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from app import create_app
from backtest import HistoricalBacktestRuntime, discover_backtests
from engine import GraphNotReadyError


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _pack(tmp_path: Path) -> Path:
    root = tmp_path / "backtests"
    pack = root / "ord-2026-07-27"
    pack.mkdir(parents=True)
    manifest = {
        "schema_version": "1.0",
        "pack_id": "ORD-2026-07-27-REAL-DAY",
        "airport": {
            "iata": "ORD",
            "icao": "KORD",
            "name": "Chicago O'Hare International Airport",
            "timezone": "America/Chicago",
        },
        "replay_window": {
            "service_date": "2026-07-27",
            "local_start": "2026-07-27T00:00:00-05:00",
            "local_end_exclusive": "2026-07-28T00:00:00-05:00",
        },
        "counts": {
            "scheduled_movements": 5,
            "weather_observations": 2,
            "cancelled_segments": 1,
            "diverted_segments": 1,
        },
        "truth_boundaries": {
            "ground_truth": "Sealed from agents and revealed only after event time."
        },
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    schedule_fields = [
        "segment_id",
        "flight_date",
        "reporting_airline",
        "flight_number",
        "origin",
        "destination",
        "ord_movement",
        "ord_scheduled_at_local",
    ]
    _write_csv(
        pack / "schedule.csv",
        schedule_fields,
        [
            {
                "segment_id": "M1",
                "flight_date": "2026-07-27",
                "reporting_airline": "UA",
                "flight_number": "101",
                "origin": "ORD",
                "destination": "ATL",
                "ord_movement": "DEPARTURE",
                "ord_scheduled_at_local": "2026-07-27T00:10:00-05:00",
            },
            {
                "segment_id": "M2",
                "flight_date": "2026-07-27",
                "reporting_airline": "AA",
                "flight_number": "202",
                "origin": "DFW",
                "destination": "ORD",
                "ord_movement": "ARRIVAL",
                "ord_scheduled_at_local": "2026-07-27T00:20:00-05:00",
            },
            {
                "segment_id": "M3",
                "flight_date": "2026-07-27",
                "reporting_airline": "UA",
                "flight_number": "303",
                "origin": "SFO",
                "destination": "ORD",
                "ord_movement": "ARRIVAL",
                "ord_scheduled_at_local": "2026-07-27T01:10:00-05:00",
            },
            {
                "segment_id": "M5",
                "flight_date": "2026-07-27",
                "reporting_airline": "UA",
                "flight_number": "505",
                "origin": "ORD",
                "destination": "DEN",
                "ord_movement": "DEPARTURE",
                "ord_scheduled_at_local": "2026-07-27T01:15:00-05:00",
            },
            {
                "segment_id": "M4",
                "flight_date": "2026-07-27",
                "reporting_airline": "UA",
                "flight_number": "404",
                "origin": "ORD",
                "destination": "LAX",
                "ord_movement": "DEPARTURE",
                "ord_scheduled_at_local": "2026-07-27T23:30:00-05:00",
            },
        ],
    )
    weather_fields = [
        "observation_id",
        "observed_at_local",
        "station",
        "wind_speed_kt",
        "wind_gust_kt",
        "visibility_sm",
        "weather_codes",
        "raw_metar",
    ]
    _write_csv(
        pack / "weather.csv",
        weather_fields,
        [
            {
                "observation_id": "WX-0000",
                "observed_at_local": "2026-07-27T00:00:00-05:00",
                "station": "KORD",
                "wind_speed_kt": 4,
                "wind_gust_kt": "",
                "visibility_sm": 10,
                "weather_codes": "",
                "raw_metar": "KORD calm",
            },
            {
                "observation_id": "WX-0130",
                "observed_at_local": "2026-07-27T01:30:00-05:00",
                "station": "KORD",
                "wind_speed_kt": 24,
                "wind_gust_kt": 38,
                "visibility_sm": 2,
                "weather_codes": "+TSRA",
                "raw_metar": "KORD thunderstorm heavy rain",
            },
        ],
    )
    truth_fields = [
        "segment_id",
        "ord_actual_at_local",
        "ord_delay_minutes",
        "cancelled",
        "diverted",
    ]
    _write_csv(
        pack / "ground_truth.csv",
        truth_fields,
        [
            {
                "segment_id": "M1",
                "ord_actual_at_local": "2026-07-27T00:20:00-05:00",
                "ord_delay_minutes": 10,
                "cancelled": 0,
                "diverted": 0,
            },
            {
                "segment_id": "M2",
                "ord_actual_at_local": "2026-07-27T01:30:00-05:00",
                "ord_delay_minutes": 70,
                "cancelled": 0,
                "diverted": 0,
            },
            {
                "segment_id": "M3",
                "ord_actual_at_local": "",
                "ord_delay_minutes": "",
                "cancelled": 0,
                "diverted": 1,
            },
            {
                "segment_id": "M5",
                "ord_actual_at_local": "2026-07-27T00:48:00-05:00",
                "ord_delay_minutes": -27,
                "cancelled": 0,
                "diverted": 0,
            },
            {
                "segment_id": "M4",
                "ord_actual_at_local": "",
                "ord_delay_minutes": "",
                "cancelled": 1,
                "diverted": 0,
            },
        ],
    )
    return pack


@pytest.fixture()
def backtest_runtime(tmp_path: Path):
    pack = _pack(tmp_path)
    runtime = HistoricalBacktestRuntime(
        pack,
        mode="lite",
        graph_name="groundgraph_backtest_test",
        lite_path=tmp_path / "backtest.db",
        enable_llm=False,
    )
    yield runtime
    runtime.close()


def test_catalog_normalizes_manifest_without_reading_outcomes(tmp_path: Path):
    pack = _pack(tmp_path)
    entries = discover_backtests(pack.parent)

    assert len(entries) == 1
    assert entries[0]["id"] == "ORD-2026-07-27-REAL-DAY"
    assert entries[0]["airport"] == "ORD"
    assert entries[0]["date"] == "2026-07-27"
    assert "ground_truth" not in json.dumps(entries[0]).lower()
    assert entries[0]["counts"] == {
        "scheduled_movements": 5,
        "weather_observations": 2,
    }


@pytest.mark.parametrize(
    ("weather_codes", "raw_metar", "sky_layers", "expected_score", "expected_signals"),
    [
        (
            "-RA",
            "KORD 271851Z 10009KT 10SM -RA SCT007 BKN038 OVC055 "
            "RMK AO2 TSE43",
            {"sky_cover_1": "SCT", "sky_base_1_ft": "700", "sky_cover_2": "BKN", "sky_base_2_ft": "3800"},
            1,
            ["adverse precipitation or visibility"],
        ),
        (
            "",
            "KORD 271951Z 17013KT 10SM FEW010 SCT023 BKN035 "
            "RMK AO2 TSB20E47",
            {"sky_cover_1": "FEW", "sky_base_1_ft": "1000", "sky_cover_2": "SCT", "sky_base_2_ft": "2300"},
            0,
            [],
        ),
        (
            "",
            "KORD 272251Z 17012G20KT 10SM FEW013 FEW030 SCT250 "
            "RMK AO2 TSE30",
            {"sky_cover_1": "FEW", "sky_base_1_ft": "1300", "sky_cover_2": "FEW", "sky_base_2_ft": "3000"},
            0,
            [],
        ),
    ],
)
def test_weather_hazard_ignores_ended_thunder_and_non_ceiling_layers(
    weather_codes: str,
    raw_metar: str,
    sky_layers: dict[str, str],
    expected_score: int,
    expected_signals: list[str],
):
    score, signals = HistoricalBacktestRuntime._weather_hazard(
        {
            "observed": {
                "weather_codes": weather_codes,
                "raw_metar": raw_metar,
                "visibility_sm": "10",
                "wind_speed_kt": "9",
                "wind_gust_kt": "",
                **sky_layers,
            }
        }
    )

    assert score == expected_score
    assert signals == expected_signals


def test_weather_hazard_counts_active_thunder_and_true_low_ceiling():
    score, signals = HistoricalBacktestRuntime._weather_hazard(
        {
            "observed": {
                "weather_codes": "TSRA",
                "raw_metar": "KORD 271651Z 24014KT 10SM TSRA FEW003 BKN008",
                "visibility_sm": "10",
                "wind_speed_kt": "14",
                "sky_cover_1": "FEW",
                "sky_base_1_ft": "300",
                "sky_cover_2": "BKN",
                "sky_base_2_ft": "800",
            }
        }
    )

    assert score == 7
    assert signals == [
        "thunderstorm",
        "adverse precipitation or visibility",
        "ceiling 800 ft",
    ]


def test_runner_time_gates_weather_truth_and_six_agent_handoffs(
    backtest_runtime: HistoricalBacktestRuntime,
):
    initial = backtest_runtime.snapshot()
    first = initial["latest_episode"]

    assert initial["clock"]["minute"] == 0
    assert initial["truth_boundary"]["revealed_outcome_count"] == 0
    assert initial["weather"]["visible_count"] == 1
    assert len(first["handoffs"]) == 6
    assert len({item["from_agent"] for item in first["handoffs"]}) == 6
    assert all(item["issued_minute"] == 0 for item in first["handoffs"])
    assert len(first["candidates"]) == 4
    assert first["forecast"]["method"] == "HISTORICAL_ANALOG"
    assert len(first["forecast"]["comparison_candidate_ids"]) == 4
    assert first["forecast"]["selection_mode"] == (
        "NUMERICAL_DEFAULT_WITH_OPTIONAL_QWEN_VERIFICATION"
    )
    assert first["forecast"]["candidate_id"] in first["forecast"]["allowlisted_candidate_ids"]
    assert initial["claims"]["counterfactual_saved_minutes"] is None

    at_one_hour = backtest_runtime.tick(to_minute=60)
    results = backtest_runtime.results()
    encoded = json.dumps({"snapshot": at_one_hour, "results": results})

    assert at_one_hour["truth_boundary"]["revealed_outcome_count"] == 2
    assert at_one_hour["weather"]["visible_count"] == 1
    assert "2026-07-27T01:30:00-05:00" not in encoded
    assert '"delay_minutes": 70' not in encoded
    first_evaluation = results["episodes"][0]["evaluation"]
    assert first_evaluation["status"] == "PARTIAL"
    assert first_evaluation["agent_visible_count"] == 1
    assert first_evaluation["evaluated_count"] == 1
    assert first_evaluation["actual"]["mean_delay_minutes"] == 10.0
    assert at_one_hour["display_episode"]["id"] == results["episodes"][0]["id"]
    assert (
        at_one_hour["display_episode"]["forecast"]["episode_id"]
        == at_one_hour["display_episode"]["id"]
    )
    assert at_one_hour["latest_episode"]["issued_minute"] == 60
    assert results["episodes"][0]["handoff_count"] == 6
    assert results["episodes"][0]["input"]["scheduled_movements"] == 2
    assert at_one_hour["latest_episode"]["input"]["scheduled_movements"] == 1
    assert "M5" not in at_one_hour["latest_episode"]["target_movement_ids"]
    assert not (
        set(at_one_hour["latest_episode"]["target_movement_ids"])
        & set(backtest_runtime._revealed)
    )

    at_ninety = backtest_runtime.tick(to_minute=90)
    assert at_ninety["weather"]["visible_count"] == 2
    assert at_ninety["truth_boundary"]["revealed_outcome_count"] == 3
    assert backtest_runtime.results()["episodes"][0]["evaluation"]["status"] == "COMPLETE"


def test_diversion_with_missing_ord_delay_is_disrupted_but_not_zero_delay(
    backtest_runtime: HistoricalBacktestRuntime,
):
    snapshot = backtest_runtime.tick(to_minute=1440)
    results = backtest_runtime.results()
    second = results["episodes"][1]

    assert snapshot["truth_boundary"]["revealed_outcome_count"] == 3
    assert second["evaluation"]["status"] == "COMPLETE"
    assert second["evaluation"]["actual"]["diverted"] == 1
    assert second["evaluation"]["actual"]["disrupted"] == 1
    assert second["evaluation"]["actual"]["mean_delay_minutes"] is None
    assert second["evaluation"]["actual"]["mean_delay_band"] == "UNAVAILABLE"
    assert second["evaluation"]["selected_score"]["mean_delay_abs_error_minutes"] is None


def test_every_episode_and_completed_score_are_persisted_in_falkordb(
    backtest_runtime: HistoricalBacktestRuntime,
):
    backtest_runtime.tick(to_minute=90)

    counts = {}
    for label in (
        "ForecastEpisode",
        "AgentRun",
        "Finding",
        "Handoff",
        "ModelRun",
        "Forecast",
        "GroundTruthWindow",
        "EvaluationMetric",
    ):
        rows = backtest_runtime.backend.query(
            f"MATCH (n:{label}) RETURN count(n)"
        ).result_set
        counts[label] = int(rows[0][0])

    assert counts["ForecastEpisode"] == 2
    assert counts["AgentRun"] == counts["Finding"] == counts["Handoff"] == 12
    assert counts["ModelRun"] == counts["Forecast"] == counts["GroundTruthWindow"] == 2
    assert counts["EvaluationMetric"] == 4

    query_names = {
        str(row[0])
        for row in backtest_runtime.backend.query(
            "MATCH (h:Handoff) RETURN h.query_name"
        ).result_set
    }
    assert query_names == {
        "WEATHER_AT_WATERMARK",
        "NEXT_HOUR_DEMAND",
        "NEXT_HOUR_ARRIVALS",
        "NEXT_HOUR_DEPARTURES",
        "MATURED_DISRUPTIONS",
        "FORECAST_MEMORY",
    }
    consumed = backtest_runtime.backend.query(
        "MATCH (:ForecastEpisode)-[:CONSUMED_HANDOFF]->(h:Handoff) RETURN count(h)"
    ).result_set
    assert int(consumed[0][0]) == 12


def test_lite_runtime_close_stops_its_embedded_server(tmp_path: Path):
    pack = _pack(tmp_path)
    runtime = HistoricalBacktestRuntime(
        pack,
        mode="lite",
        graph_name="backtest_close_test",
        lite_path=tmp_path / "close.db",
        enable_llm=False,
    )
    pid = int(runtime.backend.client.client.pid)

    runtime.close()

    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_deleted_graph_fails_closed_before_clock_or_forecast_advances(
    backtest_runtime: HistoricalBacktestRuntime,
):
    before_minute = backtest_runtime.current_minute
    before_episode_count = len(backtest_runtime._episodes)
    backtest_runtime.backend.query("MATCH (n) DETACH DELETE n")

    with pytest.raises(GraphNotReadyError):
        backtest_runtime.tick(to_minute=60)

    assert backtest_runtime.current_minute == before_minute
    assert len(backtest_runtime._episodes) == before_episode_count


def test_schedule_property_corruption_fails_before_any_replay_mutation(
    backtest_runtime: HistoricalBacktestRuntime,
):
    graph_counts_before = {}
    for label in ("ForecastEpisode", "GroundTruthWindow", "ObservedOutcome"):
        rows = backtest_runtime.backend.query(
            f"MATCH (n:{label}) RETURN count(n)"
        ).result_set
        graph_counts_before[label] = int(rows[0][0])
    backtest_runtime.backend.query(
        "MATCH (m:ScheduledMovement {id: 'M3'}) SET m.scheduled_minute = 80"
    )

    with pytest.raises(GraphNotReadyError):
        backtest_runtime.tick(to_minute=60)

    assert backtest_runtime.current_minute == 0
    assert not backtest_runtime._revealed
    assert len(backtest_runtime._episodes) == 1
    for label, expected in graph_counts_before.items():
        rows = backtest_runtime.backend.query(
            f"MATCH (n:{label}) RETURN count(n)"
        ).result_set
        assert int(rows[0][0]) == expected


def test_missing_saved_forecast_fails_before_next_episode(
    backtest_runtime: HistoricalBacktestRuntime,
):
    backtest_runtime.backend.query("MATCH (f:Forecast) DETACH DELETE f")

    with pytest.raises(GraphNotReadyError):
        backtest_runtime.tick(to_minute=60)

    assert backtest_runtime.current_minute == 0
    assert len(backtest_runtime._episodes) == 1
    rows = backtest_runtime.backend.query(
        "MATCH (e:ForecastEpisode) RETURN count(e)"
    ).result_set
    assert int(rows[0][0]) == 1


@pytest.mark.parametrize(
    "corruption",
    [
        "MATCH (c:ForecastCandidate) SET c.predicted_mean_delay_minutes = 999",
        "MATCH (r:AgentRun) SET r.query_name = 'TAMPERED'",
        "MATCH (f:Finding) SET f.summary = 'tampered'",
        "MATCH (w:GroundTruthWindow) SET w.status = 'COMPLETE'",
        "MATCH (b:BacktestRun) SET b.current_minute = 999",
        "MATCH (m:ModelRun) SET m.provider = 'tampered'",
        "MATCH (:ForecastEpisode)-[r:USED_MODEL]->(:ModelRun) DELETE r",
    ],
)
def test_persisted_trace_tampering_fails_closed(
    backtest_runtime: HistoricalBacktestRuntime,
    corruption: str,
):
    backtest_runtime.backend.query(corruption)

    with pytest.raises(GraphNotReadyError):
        backtest_runtime.snapshot()
    with pytest.raises(GraphNotReadyError):
        backtest_runtime.tick(to_minute=60)

    assert backtest_runtime.current_minute == 0
    assert not backtest_runtime._revealed
    assert len(backtest_runtime._episodes) == 1


def test_final_reveal_scores_cancellations_without_causal_impact_claim(
    backtest_runtime: HistoricalBacktestRuntime,
):
    final = backtest_runtime.tick(to_minute=1440)
    results = backtest_runtime.results()

    assert final["clock"]["complete"] is True
    assert final["truth_boundary"]["revealed_outcome_count"] == 3
    assert final["truth_boundary"]["archive_settlement_complete"] is True
    assert results["agent_future_outcomes_visible"] is False
    assert results["raw_archive_rows_visible"] is False
    assert results["archive_aggregate_visible"] is True
    assert results["summary"]["forecast_count"] == 24
    assert results["cumulative_scores"]["hours_scored"] == 3
    assert results["cumulative_scores"]["scored_target_movements"] == 4
    assert results["cumulative_scores"]["delay_bearing_outcomes"] == 2
    assert results["cumulative_scores"]["unavailable_delay_outcomes"] == 1
    assert results["cumulative_scores"]["cancelled_outcomes"] == 1
    assert results["cumulative_scores"]["diverted_outcomes"] == 1
    assert "No historical action or saved-minutes claim" in results["qualifier"]
    assert sum(item["evaluation"]["status"] == "COMPLETE" for item in results["episodes"]) == 3
    assert sum(item["evaluation"]["status"] == "SKIPPED" for item in results["episodes"]) == 21


def test_backtest_flask_api_select_tick_results_and_reset(tmp_path: Path):
    pack = _pack(tmp_path)
    runtimes: list[HistoricalBacktestRuntime] = []

    def factory(_entry):
        runtime = HistoricalBacktestRuntime(
            pack,
            mode="lite",
            graph_name=f"backtest_api_{len(runtimes)}",
            lite_path=tmp_path / f"api-{len(runtimes)}.db",
            enable_llm=False,
        )
        runtimes.append(runtime)
        return runtime

    client = create_app(
        backtest_root=pack.parent,
        backtest_runtime_factory=factory,
    ).test_client()
    catalog = client.get("/api/backtests")
    assert catalog.status_code == 200
    assert catalog.get_json()["backtests"][0]["id"] == "ORD-2026-07-27-REAL-DAY"

    missing = client.get("/api/backtests/results")
    assert missing.status_code == 409

    selected = client.post(
        "/api/backtests/select",
        json={"backtest_id": "ORD-2026-07-27-REAL-DAY"},
    )
    assert selected.status_code == 200
    assert selected.get_json()["snapshot"]["clock"]["minute"] == 0

    ticked = client.post("/api/backtests/tick", json={"hours": 1})
    assert ticked.status_code == 200
    assert ticked.get_json()["clock"]["minute"] == 60
    assert ticked.get_json()["story"][2]["title"] == "Forecast locked before reveal"

    results = client.get("/api/backtests/results")
    assert results.status_code == 200
    assert results.get_json()["summary"]["forecast_count"] == 2

    health = client.get("/api/backtests/health")
    assert health.status_code == 200
    assert health.get_json()["graph"]["mode"] == "lite"

    runtimes[-1].backend.query("MATCH (n) DETACH DELETE n")
    broken_health = client.get("/api/backtests/health")
    broken_tick = client.post("/api/backtests/tick", json={"hours": 1})
    assert broken_health.status_code == 503
    assert broken_health.get_json()["code"] == "GRAPH_NOT_READY"
    assert broken_tick.status_code == 503
    assert broken_tick.get_json()["code"] == "GRAPH_NOT_READY"
    assert runtimes[-1].current_minute == 60

    reset = client.post(
        "/api/backtests/select",
        json={"backtest_id": "ORD-2026-07-27-REAL-DAY", "reset": True},
    )
    assert reset.status_code == 200
    assert reset.get_json()["snapshot"]["clock"]["minute"] == 0

    for runtime in runtimes:
        try:
            runtime.close()
        except Exception:
            pass
