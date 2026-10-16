from __future__ import annotations

import csv
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import pytest

from app import create_app
from backtest import HistoricalBacktestRuntime, discover_backtests
from scripts.benchmark_backtests import (
    FEATURE_NAMES,
    TARGET_FIELDS,
    DayData,
    build_episodes,
    knn_predict,
    load_day,
)
from scripts.build_backtest_pack import SCHEDULE_FIELDS
from scripts.build_multiday_backtests import PINNED_DAY, parse_dates


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKTEST_ROOT = REPOSITORY_ROOT / "data" / "backtests"
BENCHMARK_DIR = REPOSITORY_ROOT / "analysis" / "ord-july-2026-benchmark"
BENCHMARK_PATH = BENCHMARK_DIR / "benchmark.json"
MODEL_PATH = (
    REPOSITORY_ROOT
    / "data"
    / "models"
    / "ord-hourly-historical-analog-june-2026.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_discoverable_pack(
    root: Path,
    directory_name: str,
    *,
    pack_id: str,
    service_date: str,
) -> Path:
    pack = root / directory_name
    pack.mkdir(parents=True)
    manifest = {
        "pack_id": pack_id,
        "airport": {"iata": "ORD"},
        "replay_window": {"service_date": service_date},
        "counts": {
            "scheduled_segments": 10,
            "weather_observations": 2,
            "cancelled_segments": 9,
        },
        "truth_boundaries": {"ground_truth": "evaluation only"},
    }
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for filename in ("schedule.csv", "weather.csv", "ground_truth.csv"):
        (pack / filename).write_text("placeholder\n", encoding="utf-8")
    return pack


def test_catalog_default_and_discovery_are_multiday_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "backtests"
    _write_discoverable_pack(
        root,
        "00-first",
        pack_id="first-pack",
        service_date="2026-07-02",
    )
    _write_discoverable_pack(
        root,
        "ord-2026-07-27",
        pack_id="ord-2026-07-27",
        service_date="2026-07-27",
    )

    entries = discover_backtests(root)
    assert [entry["id"] for entry in entries] == ["first-pack", "ord-2026-07-27"]
    assert [entry["label"] for entry in entries] == [
        "ORD · 2026-07-02 factual replay",
        "ORD · 2026-07-27 factual replay",
    ]
    assert all(entry["counts"] == {"scheduled_segments": 10, "weather_observations": 2} for entry in entries)

    client = create_app(backtest_root=root).test_client()
    response = client.get("/api/backtests")
    assert response.status_code == 200
    assert response.get_json()["default_backtest_id"] == "ord-2026-07-27"

    monkeypatch.setenv("GROUNDGRAPH_DEFAULT_BACKTEST", "first-pack")
    assert client.get("/api/backtests").get_json()["default_backtest_id"] == "first-pack"

    monkeypatch.setenv("GROUNDGRAPH_DEFAULT_BACKTEST", "missing-pack")
    assert client.get("/api/backtests").get_json()["default_backtest_id"] == "first-pack"


def test_benchmark_api_returns_only_the_compact_allowlist(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text(
        json.dumps(
            {
                "evaluation": {"day_count": 30, "window_count": 638},
                "metrics": {
                    "SCHEDULE_BASELINE": {"overall": {"delay_mae_minutes": 34.9}},
                    "RECENT_FLOW": {"private": "omit-me"},
                    "DETERMINISTIC": {"overall": {"delay_mae_minutes": 22.7}},
                    "HISTORICAL_ANALOG": {"overall": {"delay_mae_minutes": 19.0}},
                },
                "day_actuals": [{"private": "omit-me"}],
                "delay_causes": {"baggage_specific_field_available": False},
                "historical_analog_model": {
                    "model_id": "model-1",
                    "algorithm": "knn",
                    "chronological_split": {"validation_end": "2026-06-30"},
                    "selected_k_by_target": {"mean_delay_minutes": 5},
                    "refit_episode_count": 612,
                    "truth_boundary": "June labels only",
                    "reference_examples": [{"private": "omit-me"}] * 100,
                    "standardizer": {"private": "omit-me"},
                    "training_pack_hashes": {"private": "omit-me"},
                },
                "limitations": ["known limitation"],
            }
        ),
        encoding="utf-8",
    )
    client = create_app(
        backtest_root=tmp_path / "empty-packs",
        backtest_benchmark_path=benchmark_path,
    ).test_client()

    response = client.get("/api/backtests/benchmark")
    assert response.status_code == 200
    payload = response.get_json()
    assert set(payload) == {
        "ok",
        "evaluation",
        "metrics",
        "delay_causes",
        "model",
        "limitations",
    }
    assert set(payload["metrics"]) == {
        "SCHEDULE_BASELINE",
        "DETERMINISTIC",
        "HISTORICAL_ANALOG",
    }
    assert set(payload["model"]) == {
        "model_id",
        "algorithm",
        "chronological_split",
        "selected_k_by_target",
        "refit_episode_count",
        "truth_boundary",
    }
    serialized = json.dumps(payload)
    assert "day_actuals" not in serialized
    assert "reference_examples" not in serialized
    assert "omit-me" not in serialized


def test_benchmark_api_reports_missing_and_invalid_artifacts(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    client = create_app(
        backtest_root=tmp_path / "empty-packs",
        backtest_benchmark_path=missing,
    ).test_client()
    response = client.get("/api/backtests/benchmark")
    assert response.status_code == 404
    assert response.get_json()["ok"] is False

    missing.write_text("not-json", encoding="utf-8")
    response = client.get("/api/backtests/benchmark")
    assert response.status_code == 500
    assert response.get_json()["ok"] is False


def test_multiday_builder_default_dates_make_the_reconstruction_boundary_explicit() -> None:
    july_dates = parse_dates(None, "2026-07")
    assert july_dates == tuple(date(2026, 7, day) for day in range(2, 32) if day != 27)
    assert PINNED_DAY not in july_dates
    assert parse_dates("2026-07-03, 2026-07-05", "2026-07") == (
        date(2026, 7, 3),
        date(2026, 7, 5),
    )
    with pytest.raises(ValueError, match="duplicate"):
        parse_dates("2026-07-03,2026-07-03", "2026-07")


def test_future_truth_and_weather_do_not_change_an_earlier_model_input() -> None:
    schedule = [
        {
            "segment_id": "past",
            "minute": 0,
            "direction": "DEPARTURE",
        },
        {
            "segment_id": "target",
            "minute": 60,
            "direction": "ARRIVAL",
        },
    ]
    truth = {
        "past": {
            "cancelled_bool": False,
            "diverted_bool": False,
            "delay": 10.0,
            "actual_minute": 30,
        },
        "target": {
            "cancelled_bool": False,
            "diverted_bool": False,
            "delay": 20.0,
            "actual_minute": 90,
        },
    }
    weather = [
        {
            "minute": 30,
            "visibility_sm": "10",
            "wind_speed_kt": "4",
            "wind_gust_kt": "",
            "precipitation_1h_in": "0",
            "weather_codes": "",
            "raw_metar": "KORD clear",
        },
        {
            "minute": 90,
            "visibility_sm": "1",
            "wind_speed_kt": "35",
            "wind_gust_kt": "50",
            "precipitation_1h_in": "1",
            "weather_codes": "+TSRA",
            "raw_metar": "KORD future storm",
        },
    ]
    baseline = DayData(
        service_date="2026-07-02",
        pack_id="test-day",
        path=Path("."),
        schedule=schedule,
        truth=truth,
        weather=weather,
        manifest={},
    )
    changed = deepcopy(baseline)
    changed.truth["target"]["delay"] = 300.0
    changed.truth["target"]["actual_minute"] = 120
    changed.weather[-1]["weather_codes"] = "TORNADO"
    eventual_status_changed = deepcopy(baseline)
    eventual_status_changed.truth["past"]["cancelled_bool"] = True
    eventual_status_changed.truth["past"]["diverted_bool"] = True

    baseline_episode = next(item for item in build_episodes(baseline) if item["issued_minute"] == 60)
    changed_episode = next(item for item in build_episodes(changed) if item["issued_minute"] == 60)
    status_changed_episode = next(
        item
        for item in build_episodes(eventual_status_changed)
        if item["issued_minute"] == 60
    )

    assert baseline_episode["features"] == changed_episode["features"]
    assert baseline_episode["context"] == changed_episode["context"]
    assert baseline_episode["candidates"] == changed_episode["candidates"]
    assert baseline_episode["actual"] != changed_episode["actual"]
    assert baseline_episode["features"] == status_changed_episode["features"]
    assert baseline_episode["context"] == status_changed_episode["context"]
    assert baseline_episode["candidates"] == status_changed_episode["candidates"]


def test_historical_analog_artifact_has_a_strict_date_split() -> None:
    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    assert benchmark["historical_analog_model"] == model

    evaluation_dates = [date.fromisoformat(value) for value in benchmark["evaluation"]["dates"]]
    refit_dates = [date.fromisoformat(value) for value in model["refit_days"]]
    split = {key: date.fromisoformat(value) for key, value in model["chronological_split"].items()}

    assert evaluation_dates == list(date(2026, 7, day) for day in range(2, 32))
    assert refit_dates == list(date(2026, 6, day) for day in range(2, 31))
    assert split["calibration_start"] == min(refit_dates)
    assert split["calibration_end"] < split["validation_start"]
    assert split["validation_end"] == max(refit_dates)
    assert max(refit_dates) < min(evaluation_dates)

    references = model["reference_examples"]
    assert len(references) == model["refit_episode_count"]
    assert {date.fromisoformat(item["service_date"]) for item in references} <= set(refit_dates)
    assert set(model["training_pack_hashes"]) == {
        f"ord-{service_date.isoformat()}" for service_date in refit_dates
    }
    assert set(model["feature_names"]) == set(FEATURE_NAMES)
    assert set(model["target_fields"]) == set(TARGET_FIELDS)
    assert set(model["standardizer"]["means"]) == set(FEATURE_NAMES)
    assert set(model["standardizer"]["scales"]) == set(FEATURE_NAMES)
    assert all(float(value) > 0 for value in model["standardizer"]["scales"].values())
    assert all(set(item["features"]) == set(FEATURE_NAMES) for item in references)
    assert all(set(item["actual"]) == set(TARGET_FIELDS) for item in references)
    assert set(FEATURE_NAMES).isdisjoint(TARGET_FIELDS)
    assert not any(name.startswith(("actual_", "truth_", "future_")) for name in FEATURE_NAMES)
    assert all(value in model["candidate_k_values"] for value in model["selected_k_by_target"].values())


@pytest.mark.parametrize(
    ("service_date", "issued_minute"),
    (("2026-07-08", 720), ("2026-07-27", 1080)),
)
def test_graph_runtime_matches_batch_historical_analog_exactly(
    tmp_path: Path, service_date: str, issued_minute: int
) -> None:
    """Keep normal- and severe-day live predictions identical to the benchmark."""

    pack = BACKTEST_ROOT / f"ord-{service_date}"
    day = load_day(pack)
    expected_episode = next(
        item for item in build_episodes(day) if item["issued_minute"] == issued_minute
    )
    runtime = HistoricalBacktestRuntime(
        pack,
        mode="lite",
        graph_name=f"historical_analog_parity_{service_date.replace('-', '')}",
        lite_path=tmp_path / f"{service_date}.db",
        enable_llm=False,
    )
    try:
        revealed = runtime.truth_evaluator.reveal(
            as_of_minute=issued_minute,
            already_revealed=set(),
        )
        assert all("cancelled" not in item and "diverted" not in item for item in revealed)
        runtime._revealed = {item["movement_id"]: item for item in revealed}
        targets = runtime._target_movements(
            issued_minute,
            min(1440, issued_minute + 60),
            exclude_ids=set(runtime._revealed),
        )
        visible_weather = runtime._weather_visible(issued_minute)
        features = runtime._historical_analog_features(
            issued_minute=issued_minute,
            targets=targets,
            latest_weather=visible_weather[-1] if visible_weather else None,
            recent=runtime._recent_outcomes(issued_minute),
        )
        actual_prediction = runtime._historical_analog_prediction(
            features,
            target_count=len(targets),
        )
        expected_prediction = knn_predict(
            expected_episode,
            runtime.historical_analog_model["reference_examples"],
            runtime.historical_analog_model["standardizer"],
            k_by_target=runtime.historical_analog_model["selected_k_by_target"],
        )

        assert len(targets) == expected_episode["target_count"]
        assert features == pytest.approx(expected_episode["features"], abs=1e-12)
        assert actual_prediction == expected_prediction
    finally:
        runtime.close()


def test_checked_in_multiday_artifacts_are_complete_and_date_scoped() -> None:
    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    expected_dates = benchmark["evaluation"]["dates"]
    expected_pack_ids = [f"ord-{value}" for value in expected_dates]
    pack_hashes = benchmark["evaluation"]["pack_hashes"]

    assert benchmark["evaluation"]["day_count"] == len(expected_dates) == 30
    assert list(pack_hashes) == expected_pack_ids
    assert {item["service_date"] for item in benchmark["day_actuals"]} == set(expected_dates)

    for service_date, pack_id in zip(expected_dates, expected_pack_ids, strict=True):
        pack = BACKTEST_ROOT / pack_id
        manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["pack_id"] == pack_id
        assert manifest["replay_window"]["service_date"] == service_date
        assert set(pack_hashes[pack_id]) == {
            "manifest.json",
            "schedule.csv",
            "weather.csv",
            "ground_truth.csv",
        }
        for filename, expected_hash in pack_hashes[pack_id].items():
            assert _sha256(pack / filename) == expected_hash

        with (pack / "schedule.csv").open(encoding="utf-8", newline="") as handle:
            schedule_reader = csv.DictReader(handle)
            assert schedule_reader.fieldnames == SCHEDULE_FIELDS
            schedule = list(schedule_reader)
        with (pack / "weather.csv").open(encoding="utf-8", newline="") as handle:
            weather = list(csv.DictReader(handle))
        with (pack / "ground_truth.csv").open(encoding="utf-8", newline="") as handle:
            truth = list(csv.DictReader(handle))

        compact_date = service_date.replace("-", "")
        assert len(schedule) == manifest["counts"]["scheduled_segments"]
        assert len(weather) == manifest["counts"]["weather_observations"]
        assert {row["segment_id"] for row in schedule} == {row["segment_id"] for row in truth}
        assert all(row["segment_id"].startswith(f"ord-{compact_date}-") for row in schedule)
        assert all(datetime.fromisoformat(row["ord_scheduled_at_local"]).date().isoformat() == service_date for row in schedule)
        assert all(row["observation_id"].startswith(f"kord-{compact_date}-") for row in weather)
        assert all(datetime.fromisoformat(row["observed_at_local"]).date().isoformat() == service_date for row in weather)

    with (BENCHMARK_DIR / "per-day-metrics.csv").open(encoding="utf-8", newline="") as handle:
        per_day = list(csv.DictReader(handle))
    with (BENCHMARK_DIR / "hourly-results.csv").open(encoding="utf-8", newline="") as handle:
        hourly = list(csv.DictReader(handle))
    methods = set(benchmark["metrics"])
    assert len(per_day) == len(expected_dates) * len(methods)
    assert len(hourly) == benchmark["evaluation"]["window_count"] * len(methods)
    assert {row["service_date"] for row in per_day} == set(expected_dates)
    assert {row["service_date"] for row in hourly} == set(expected_dates)
    assert {row["method"] for row in per_day} == methods
    assert {row["method"] for row in hourly} == methods
