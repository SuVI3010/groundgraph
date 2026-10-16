"""Evaluate GroundGraph forecast logic across complete historical day packs.

The benchmark is intentionally graph-free: it freezes the same time-gated
inputs and candidate formulas used by the graph runtime, then scores many days
quickly. A graph-backed replay remains the integration test; this script is the
scientific batch evaluator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from backtest import HistoricalBacktestRuntime, _delay_band
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backtest import HistoricalBacktestRuntime, _delay_band


FEATURE_NAMES = (
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
    "target_count",
    "arrival_share",
    "weather_missing",
    "weather_hazard",
    "visibility_sm",
    "wind_speed_kt",
    "wind_gust_kt",
    "precipitation_1h_in",
    "recent_completion_count",
    "recent_mean_delay",
    "recent_disruption_rate",
    "backlog_total",
    "backlog_15",
    "backlog_60",
    "backlog_180",
    "completion_to_demand_ratio",
)
TARGET_FIELDS = (
    "mean_delay_minutes",
    "cancel_rate_pct",
    "disruption_rate_pct",
)
K_OPTIONS = (3, 5, 10, 20, 40, 80)


def optional_number(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "1.0", "true", "yes"}


def local_minute(value: str, service_date: str) -> int:
    parsed = datetime.fromisoformat(value)
    base = date.fromisoformat(service_date)
    return (parsed.date() - base).days * 1440 + parsed.hour * 60 + parsed.minute


def mean(values: Iterable[float]) -> float | None:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else None


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class DayData:
    service_date: str
    pack_id: str
    path: Path
    schedule: list[dict[str, Any]]
    truth: dict[str, dict[str, Any]]
    weather: list[dict[str, Any]]
    manifest: dict[str, Any]


def load_day(path: Path) -> DayData:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    service_date = str(manifest["replay_window"]["service_date"])
    with (path / "schedule.csv").open(encoding="utf-8", newline="") as handle:
        schedule = list(csv.DictReader(handle))
    for row in schedule:
        row["minute"] = local_minute(row["ord_scheduled_at_local"], service_date)
        row["direction"] = str(row["ord_movement"]).upper()

    with (path / "ground_truth.csv").open(encoding="utf-8", newline="") as handle:
        truth_rows = list(csv.DictReader(handle))
    truth: dict[str, dict[str, Any]] = {}
    for row in truth_rows:
        normalized = dict(row)
        normalized["cancelled_bool"] = truthy(row.get("cancelled"))
        normalized["diverted_bool"] = truthy(row.get("diverted"))
        normalized["delay"] = optional_number(row.get("ord_delay_minutes"))
        normalized["actual_minute"] = (
            local_minute(row["ord_actual_at_local"], service_date)
            if row.get("ord_actual_at_local")
            else None
        )
        truth[str(row["segment_id"])] = normalized

    with (path / "weather.csv").open(encoding="utf-8", newline="") as handle:
        weather = list(csv.DictReader(handle))
    for row in weather:
        row["minute"] = local_minute(row["observed_at_local"], service_date)
    weather.sort(key=lambda item: int(item["minute"]))
    return DayData(
        service_date=service_date,
        pack_id=str(manifest.get("pack_id", path.name)),
        path=path,
        schedule=schedule,
        truth=truth,
        weather=weather,
        manifest=manifest,
    )


def aggregate_truth(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    cancelled = sum(bool(row["cancelled_bool"]) for row in rows)
    diverted = sum(bool(row["diverted_bool"]) for row in rows)
    delays = [
        float(row["delay"])
        for row in rows
        if not row["cancelled_bool"] and row["delay"] is not None
    ]
    disrupted = sum(
        bool(row["cancelled_bool"])
        or bool(row["diverted_bool"])
        or (row["delay"] is not None and float(row["delay"]) >= 15)
        for row in rows
    )
    mean_delay = round(sum(delays) / len(delays), 4) if delays else None
    return {
        "movements": count,
        "cancelled": cancelled,
        "diverted": diverted,
        "disrupted": disrupted,
        "cancel_rate_pct": 100 * cancelled / count if count else 0.0,
        "disruption_rate_pct": 100 * disrupted / count if count else 0.0,
        "mean_delay_minutes": mean_delay,
        "mean_delay_band": _delay_band(mean_delay),
        "observed_delay_count": len(delays),
        "unavailable_delay_count": count - cancelled - len(delays),
    }


def forecast_candidates(
    *,
    target_count: int,
    recent: list[Mapping[str, Any]],
    weather: Mapping[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    hazard, signals = HistoricalBacktestRuntime._weather_hazard(
        {"observed": weather} if weather else None
    )
    recent_delays = [
        float(item["delay"])
        for item in recent
        if item["delay"] is not None
    ]
    recent_mean = mean(recent_delays) or 0.0
    # Cancellation and diversion resolutions have no reliable BTS event time,
    # so they are deliberately absent from agent-visible recent outcomes.
    recent_cancel_rate = 0.0
    # A final cancellation or diversion flag has no public event timestamp.
    # Even when that flag is eventually true, an ORD movement with an actual
    # timestamp was observable at that time. Use only its then-observable delay
    # here; final status stays evaluator-only until end-of-day settlement.
    recent_disruption_rate = (
        100
        * sum(
            item["delay"] is not None and float(item["delay"]) >= 15
            for item in recent
        )
        / len(recent)
        if recent
        else 0.0
    )

    def candidate(method: str, cancel: float, disruption: float, delay: float) -> dict[str, Any]:
        delay = max(0.0, delay)
        return {
            "method": method,
            "predicted_movements": target_count,
            "predicted_cancel_rate_pct": max(0.0, min(100.0, cancel)),
            "predicted_disruption_rate_pct": max(0.0, min(100.0, disruption)),
            "predicted_mean_delay_minutes": delay,
            "predicted_delay_band": _delay_band(delay),
        }

    candidates = {
        "SCHEDULE_BASELINE": candidate("SCHEDULE_BASELINE", 0, 5, 0),
        "RECENT_FLOW": candidate(
            "RECENT_FLOW",
            recent_cancel_rate,
            recent_disruption_rate,
            recent_mean,
        ),
        "WEATHER_STRESS": candidate(
            "WEATHER_STRESS",
            max(recent_cancel_rate, hazard * 4.0),
            max(recent_disruption_rate, 5.0 + hazard * 9.0),
            recent_mean + hazard * 12.0,
        ),
    }
    selected = (
        "WEATHER_STRESS"
        if hazard >= 2
        else "RECENT_FLOW"
        if len(recent) >= 3
        else "SCHEDULE_BASELINE"
    )
    return candidates, {
        "deterministic_method": selected,
        "weather_hazard": hazard,
        "weather_signals": signals,
        "recent_completion_count": len(recent),
        "recent_mean_delay": recent_mean,
        "recent_disruption_rate": recent_disruption_rate,
    }


def build_episodes(day: DayData) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    weekday = date.fromisoformat(day.service_date).weekday()
    for minute in range(0, 1440, 60):
        # Visibility is determined only by the actual ORD event timestamp.
        # Do not consult eventual cancellation/diversion labels here: 199 July
        # departures have an observable ORD timestamp and a later final flag.
        revealed = {
            movement_id
            for movement_id, item in day.truth.items()
            if item["actual_minute"] is not None
            and int(item["actual_minute"]) <= minute
        }
        targets = [
            movement
            for movement in day.schedule
            if minute <= int(movement["minute"]) < minute + 60
            and movement["segment_id"] not in revealed
        ]
        if not targets:
            continue
        recent = [
            item
            for item in day.truth.values()
            if item["actual_minute"] is not None
            and minute - 60 < int(item["actual_minute"]) <= minute
        ]
        latest_weather = next(
            (item for item in reversed(day.weather) if int(item["minute"]) <= minute),
            None,
        )
        candidates, context = forecast_candidates(
            target_count=len(targets),
            recent=recent,
            weather=latest_weather,
        )
        target_truth = [day.truth[item["segment_id"]] for item in targets]
        actual = aggregate_truth(target_truth)
        if actual["mean_delay_minutes"] is None:
            continue

        overdue = [
            movement
            for movement in day.schedule
            if int(movement["minute"]) < minute
            and movement["segment_id"] not in revealed
        ]
        arrivals = sum(item["direction"] == "ARRIVAL" for item in targets)
        visibility = optional_number((latest_weather or {}).get("visibility_sm"))
        wind = optional_number((latest_weather or {}).get("wind_speed_kt"))
        gust = optional_number((latest_weather or {}).get("wind_gust_kt"))
        precipitation = optional_number((latest_weather or {}).get("precipitation_1h_in"))
        features = {
            "hour_sin": math.sin(2 * math.pi * (minute / 60) / 24),
            "hour_cos": math.cos(2 * math.pi * (minute / 60) / 24),
            "weekday_sin": math.sin(2 * math.pi * weekday / 7),
            "weekday_cos": math.cos(2 * math.pi * weekday / 7),
            "target_count": float(len(targets)),
            "arrival_share": arrivals / len(targets),
            "weather_missing": 0.0 if latest_weather else 1.0,
            "weather_hazard": float(context["weather_hazard"]),
            "visibility_sm": visibility if visibility is not None else 10.0,
            "wind_speed_kt": wind or 0.0,
            "wind_gust_kt": gust or 0.0,
            "precipitation_1h_in": precipitation or 0.0,
            "recent_completion_count": float(len(recent)),
            "recent_mean_delay": float(context["recent_mean_delay"]),
            "recent_disruption_rate": float(context["recent_disruption_rate"]),
            "backlog_total": float(len(overdue)),
            "backlog_15": float(sum(minute - int(item["minute"]) >= 15 for item in overdue)),
            "backlog_60": float(sum(minute - int(item["minute"]) >= 60 for item in overdue)),
            "backlog_180": float(sum(minute - int(item["minute"]) >= 180 for item in overdue)),
            "completion_to_demand_ratio": len(recent) / max(1, len(targets)),
        }
        episodes.append(
            {
                "service_date": day.service_date,
                "issued_minute": minute,
                "issued_time": f"{minute // 60:02d}:00",
                "target_count": len(targets),
                "arrival_count": arrivals,
                "departure_count": len(targets) - arrivals,
                "features": features,
                "context": context,
                "candidates": candidates,
                "actual": actual,
            }
        )
    return episodes


def score(prediction: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    predicted_delay = float(prediction["predicted_mean_delay_minutes"])
    actual_delay = float(actual["mean_delay_minutes"])
    predicted_cancel = float(prediction["predicted_cancel_rate_pct"])
    actual_cancel = float(actual["cancel_rate_pct"])
    predicted_disruption = float(prediction["predicted_disruption_rate_pct"])
    actual_disruption = float(actual["disruption_rate_pct"])

    def brier(predicted_pct: float, actual_pct: float) -> float:
        probability = predicted_pct / 100
        rate = actual_pct / 100
        return rate * (1 - probability) ** 2 + (1 - rate) * probability**2

    return {
        "mean_delay_abs_error_minutes": abs(predicted_delay - actual_delay),
        "mean_delay_bias_minutes": predicted_delay - actual_delay,
        "cancel_rate_abs_error_pp": abs(predicted_cancel - actual_cancel),
        "cancel_rate_bias_pp": predicted_cancel - actual_cancel,
        "cancel_brier_score": brier(predicted_cancel, actual_cancel),
        "disruption_rate_abs_error_pp": abs(predicted_disruption - actual_disruption),
        "disruption_rate_bias_pp": predicted_disruption - actual_disruption,
        "disruption_brier_score": brier(predicted_disruption, actual_disruption),
        "delay_band_match": prediction["predicted_delay_band"] == actual["mean_delay_band"],
        "predicted_delay_band": prediction["predicted_delay_band"],
        "actual_delay_band": actual["mean_delay_band"],
    }


def standardizer(rows: list[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    means: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in FEATURE_NAMES:
        values = [float(row["features"][name]) for row in rows]
        means[name] = statistics.mean(values)
        scales[name] = statistics.pstdev(values) or 1.0
    return {"means": means, "scales": scales}


def vector(row: Mapping[str, Any], scaler: Mapping[str, Mapping[str, float]]) -> list[float]:
    return [
        (float(row["features"][name]) - scaler["means"][name])
        / scaler["scales"][name]
        for name in FEATURE_NAMES
    ]


def knn_predict(
    row: Mapping[str, Any],
    reference: list[Mapping[str, Any]],
    scaler: Mapping[str, Mapping[str, float]],
    *,
    k_by_target: Mapping[str, int],
) -> dict[str, Any]:
    point = vector(row, scaler)
    distances: list[tuple[float, Mapping[str, Any]]] = []
    for candidate in reference:
        other = vector(candidate, scaler)
        distance = math.sqrt(sum((left - right) ** 2 for left, right in zip(point, other)))
        distances.append((distance, candidate))
    distances.sort(key=lambda item: item[0])
    predicted: dict[str, float] = {}
    for target in TARGET_FIELDS:
        neighbors = distances[: min(k_by_target[target], len(distances))]
        weights = [1 / max(distance, 0.05) for distance, _ in neighbors]
        predicted[target] = sum(
            weight * float(neighbor["actual"][target])
            for weight, (_, neighbor) in zip(weights, neighbors)
        ) / sum(weights)
    delay = predicted["mean_delay_minutes"]
    return {
        "method": "HISTORICAL_ANALOG",
        "predicted_movements": int(row["target_count"]),
        "predicted_cancel_rate_pct": max(0.0, min(100.0, predicted["cancel_rate_pct"])),
        "predicted_disruption_rate_pct": max(
            0.0, min(100.0, predicted["disruption_rate_pct"])
        ),
        "predicted_mean_delay_minutes": delay,
        "predicted_delay_band": _delay_band(delay),
    }


def metric_summary(scored: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not scored:
        return {}
    weights = [int(item["target_count"]) for item in scored]
    total_weight = sum(weights)
    band_matches = [bool(item["score"]["delay_band_match"]) for item in scored]
    delay_errors = [item["score"]["mean_delay_abs_error_minutes"] for item in scored]
    actual_labels = sorted({str(item["score"]["actual_delay_band"]) for item in scored})
    recalls: list[float] = []
    f1_scores: list[float] = []
    for label in actual_labels:
        true_positive = sum(
            item["score"]["actual_delay_band"] == label
            and item["score"]["predicted_delay_band"] == label
            for item in scored
        )
        false_negative = sum(
            item["score"]["actual_delay_band"] == label
            and item["score"]["predicted_delay_band"] != label
            for item in scored
        )
        false_positive = sum(
            item["score"]["actual_delay_band"] != label
            and item["score"]["predicted_delay_band"] == label
            for item in scored
        )
        recall = true_positive / (true_positive + false_negative)
        precision = (
            true_positive / (true_positive + false_positive)
            if true_positive + false_positive
            else 0.0
        )
        recalls.append(recall)
        f1_scores.append(
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
    return {
        "windows": len(scored),
        "target_movements": total_weight,
        "delay_mae_minutes": round(
            statistics.mean(delay_errors),
            2,
        ),
        "delay_rmse_minutes": round(
            math.sqrt(
                statistics.mean(
                    item["score"]["mean_delay_bias_minutes"] ** 2 for item in scored
                )
            ),
            2,
        ),
        "delay_abs_error_p50_minutes": round(float(percentile(delay_errors, 0.5)), 2),
        "delay_abs_error_p90_minutes": round(float(percentile(delay_errors, 0.9)), 2),
        "movement_weighted_delay_mae_minutes": round(
            sum(
                item["score"]["mean_delay_abs_error_minutes"] * int(item["target_count"])
                for item in scored
            )
            / total_weight,
            2,
        ),
        "delay_bias_minutes": round(
            statistics.mean(item["score"]["mean_delay_bias_minutes"] for item in scored),
            2,
        ),
        "cancel_rate_mae_pp": round(
            statistics.mean(item["score"]["cancel_rate_abs_error_pp"] for item in scored),
            2,
        ),
        "cancel_rate_bias_pp": round(
            statistics.mean(item["score"]["cancel_rate_bias_pp"] for item in scored), 2
        ),
        "movement_weighted_cancel_brier_score": round(
            sum(
                item["score"]["cancel_brier_score"] * int(item["target_count"])
                for item in scored
            )
            / total_weight,
            4,
        ),
        "disruption_rate_mae_pp": round(
            statistics.mean(
                item["score"]["disruption_rate_abs_error_pp"] for item in scored
            ),
            2,
        ),
        "disruption_rate_bias_pp": round(
            statistics.mean(item["score"]["disruption_rate_bias_pp"] for item in scored),
            2,
        ),
        "movement_weighted_disruption_brier_score": round(
            sum(
                item["score"]["disruption_brier_score"] * int(item["target_count"])
                for item in scored
            )
            / total_weight,
            4,
        ),
        "delay_band_accuracy_pct": round(100 * sum(band_matches) / len(band_matches), 2),
        "delay_band_balanced_accuracy_pct": round(100 * statistics.mean(recalls), 2),
        "delay_band_macro_f1_pct": round(100 * statistics.mean(f1_scores), 2),
    }


def day_bootstrap_intervals(
    scored: list[Mapping[str, Any]], *, seed: int, iterations: int = 2_000
) -> dict[str, list[float]]:
    """Return percentile intervals while resampling whole days, not windows."""

    by_day: dict[str, list[Mapping[str, Any]]] = {}
    for row in scored:
        by_day.setdefault(str(row["service_date"]), []).append(row)
    days = sorted(by_day)
    if len(days) < 2:
        return {}
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {
        "delay_mae_minutes": [],
        "cancel_rate_mae_pp": [],
        "disruption_rate_mae_pp": [],
        "delay_band_accuracy_pct": [],
    }
    for _ in range(iterations):
        resampled = [row for _day in days for row in by_day[rng.choice(days)]]
        samples["delay_mae_minutes"].append(
            statistics.mean(row["score"]["mean_delay_abs_error_minutes"] for row in resampled)
        )
        samples["cancel_rate_mae_pp"].append(
            statistics.mean(row["score"]["cancel_rate_abs_error_pp"] for row in resampled)
        )
        samples["disruption_rate_mae_pp"].append(
            statistics.mean(
                row["score"]["disruption_rate_abs_error_pp"] for row in resampled
            )
        )
        samples["delay_band_accuracy_pct"].append(
            100
            * statistics.mean(
                bool(row["score"]["delay_band_match"]) for row in resampled
            )
        )
    return {
        name: [
            round(float(percentile(values, 0.025)), 2),
            round(float(percentile(values, 0.975)), 2),
        ]
        for name, values in samples.items()
    }


def tune_knn(
    training: list[Mapping[str, Any]], validation: list[Mapping[str, Any]]
) -> tuple[dict[str, int], dict[str, Any]]:
    scaler = standardizer(training)
    tuning: dict[str, dict[str, float]] = {target: {} for target in TARGET_FIELDS}
    for k in K_OPTIONS:
        predictions = [
            knn_predict(
                row,
                training,
                scaler,
                k_by_target={target: k for target in TARGET_FIELDS},
            )
            for row in validation
        ]
        for target in TARGET_FIELDS:
            predicted_name = f"predicted_{target}"
            errors = [
                abs(float(prediction[predicted_name]) - float(row["actual"][target]))
                for prediction, row in zip(predictions, validation)
            ]
            tuning[target][str(k)] = round(statistics.mean(errors), 4)
    selected = {
        target: int(min(tuning[target], key=tuning[target].get)) for target in TARGET_FIELDS
    }
    return selected, tuning


def day_classifications(days: list[dict[str, Any]]) -> dict[str, str]:
    # These operational strata are frozen before reporting future months.
    result: dict[str, str] = {}
    for day in days:
        cancelled = float(day["actual"]["cancel_rate_pct"])
        disrupted = float(day["actual"]["disruption_rate_pct"])
        if disrupted < 22 and cancelled < 1:
            label = "NORMAL"
        elif disrupted >= 35:
            label = "SEVERE"
        else:
            label = "ELEVATED"
        result[day["service_date"]] = label
    return result


def delayed_cause_summary(days: list[DayData]) -> dict[str, Any]:
    minutes = Counter()
    cancellation_codes = Counter()
    delay_rows = 0
    for day in days:
        for row in day.truth.values():
            if row["cancelled_bool"]:
                cancellation_codes[str(row.get("cancellation_code") or "UNKNOWN")] += 1
            values = {
                "carrier_controlled": optional_number(row.get("carrier_delay_minutes")),
                "weather": optional_number(row.get("weather_delay_minutes")),
                "national_airspace_system": optional_number(row.get("nas_delay_minutes")),
                "security": optional_number(row.get("security_delay_minutes")),
                "late_aircraft": optional_number(row.get("late_aircraft_delay_minutes")),
            }
            if any(value is not None for value in values.values()):
                delay_rows += 1
            for name, value in values.items():
                if value is not None:
                    minutes[name] += max(0.0, value)
    return {
        "rows_with_bts_delay_cause": delay_rows,
        "delay_cause_minutes": {key: round(value, 1) for key, value in minutes.items()},
        "cancellation_codes": dict(sorted(cancellation_codes.items())),
        "baggage_specific_field_available": False,
        "qualifier": (
            "BTS CarrierDelay can include baggage loading, crew, cleaning, fueling, or "
            "maintenance. It cannot identify baggage delay and is used only after the event."
        ),
    }


def write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluation-root",
        type=Path,
        default=repository_root / "data" / "backtests",
    )
    parser.add_argument(
        "--training-root",
        type=Path,
        help="optional earlier-month packs used to train HISTORICAL_ANALOG",
    )
    parser.add_argument(
        "--training-cutoff",
        default="2026-06-21",
        help="last training date before chronological validation",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repository_root / "analysis" / "ord-july-2026-benchmark",
    )
    args = parser.parse_args()

    evaluation_days = [
        load_day(path)
        for path in sorted(args.evaluation_root.iterdir())
        if path.is_dir()
        and (path / "manifest.json").is_file()
        and (path / "schedule.csv").is_file()
    ]
    if not evaluation_days:
        raise SystemExit("no evaluation packs found")
    episodes = [episode for day in evaluation_days for episode in build_episodes(day)]
    training_days: list[DayData] = []
    training_episodes: list[dict[str, Any]] = []
    model_artifact: dict[str, Any] | None = None
    if args.training_root:
        training_days = [
            load_day(path)
            for path in sorted(args.training_root.iterdir())
            if path.is_dir() and (path / "manifest.json").is_file()
        ]
        all_training = [episode for day in training_days for episode in build_episodes(day)]
        calibration = [
            episode
            for episode in all_training
            if episode["service_date"] <= args.training_cutoff
        ]
        validation = [
            episode
            for episode in all_training
            if episode["service_date"] > args.training_cutoff
        ]
        if not calibration or not validation:
            raise SystemExit("chronological training/validation split is empty")
        k_by_target, tuning = tune_knn(calibration, validation)
        training_episodes = all_training
        scaler = standardizer(training_episodes)
        for episode in episodes:
            episode["candidates"]["HISTORICAL_ANALOG"] = knn_predict(
                episode,
                training_episodes,
                scaler,
                k_by_target=k_by_target,
            )
        model_artifact = {
            "schema_version": "1.0",
            "model_id": "ord-hourly-historical-analog-june-2026",
            "algorithm": "standardized inverse-distance k-nearest-neighbors",
            "feature_names": list(FEATURE_NAMES),
            "target_fields": list(TARGET_FIELDS),
            "candidate_k_values": list(K_OPTIONS),
            "chronological_split": {
                "calibration_start": min(item["service_date"] for item in calibration),
                "calibration_end": max(item["service_date"] for item in calibration),
                "validation_start": min(item["service_date"] for item in validation),
                "validation_end": max(item["service_date"] for item in validation),
            },
            "selected_k_by_target": k_by_target,
            "validation_mae_by_k": tuning,
            "refit_days": sorted({item["service_date"] for item in training_episodes}),
            "refit_episode_count": len(training_episodes),
            "standardizer": scaler,
            "training_pack_hashes": {
                day.pack_id: {
                    filename: sha256(day.path / filename)
                    for filename in ("manifest.json", "schedule.csv", "weather.csv", "ground_truth.csv")
                }
                for day in training_days
            },
            "reference_examples": [
                {
                    "service_date": item["service_date"],
                    "issued_minute": item["issued_minute"],
                    "features": item["features"],
                    "actual": {target: item["actual"][target] for target in TARGET_FIELDS},
                }
                for item in training_episodes
            ],
            "truth_boundary": (
                "Every reference label is from June. July evaluation labels are never used "
                "to fit, tune, standardize, or select k."
            ),
        }

    day_actuals = [
        {
            "service_date": day.service_date,
            "actual": aggregate_truth(list(day.truth.values())),
        }
        for day in evaluation_days
    ]
    classes = day_classifications(day_actuals)
    methods = ["SCHEDULE_BASELINE", "RECENT_FLOW", "WEATHER_STRESS", "DETERMINISTIC"]
    if training_episodes:
        methods.append("HISTORICAL_ANALOG")
    scored_by_method: dict[str, list[dict[str, Any]]] = {method: [] for method in methods}
    window_rows: list[dict[str, Any]] = []
    for episode in episodes:
        for method in methods:
            candidate_method = (
                episode["context"]["deterministic_method"]
                if method == "DETERMINISTIC"
                else method
            )
            prediction = episode["candidates"][candidate_method]
            result = score(prediction, episode["actual"])
            record = {
                "service_date": episode["service_date"],
                "day_class": classes[episode["service_date"]],
                "issued_minute": episode["issued_minute"],
                "target_count": episode["target_count"],
                "method": method,
                "candidate_method": candidate_method,
                "score": result,
            }
            scored_by_method[method].append(record)
            window_rows.append(
                {
                    "service_date": episode["service_date"],
                    "day_class": classes[episode["service_date"]],
                    "issued_time": episode["issued_time"],
                    "target_count": episode["target_count"],
                    "method": method,
                    "candidate_method": candidate_method,
                    "predicted_mean_delay_minutes": round(
                        float(prediction["predicted_mean_delay_minutes"]), 2
                    ),
                    "actual_mean_delay_minutes": round(
                        float(episode["actual"]["mean_delay_minutes"]), 2
                    ),
                    "delay_abs_error_minutes": round(
                        result["mean_delay_abs_error_minutes"], 2
                    ),
                    "predicted_cancel_rate_pct": round(
                        float(prediction["predicted_cancel_rate_pct"]), 2
                    ),
                    "actual_cancel_rate_pct": round(
                        float(episode["actual"]["cancel_rate_pct"]), 2
                    ),
                    "cancel_abs_error_pp": round(result["cancel_rate_abs_error_pp"], 2),
                    "predicted_disruption_rate_pct": round(
                        float(prediction["predicted_disruption_rate_pct"]), 2
                    ),
                    "actual_disruption_rate_pct": round(
                        float(episode["actual"]["disruption_rate_pct"]), 2
                    ),
                    "disruption_abs_error_pp": round(
                        result["disruption_rate_abs_error_pp"], 2
                    ),
                    "delay_band_match": result["delay_band_match"],
                }
            )

    metrics = {
        method: {
            "overall": metric_summary(rows),
            "by_day_class": {
                label: metric_summary([row for row in rows if row["day_class"] == label])
                for label in ("NORMAL", "ELEVATED", "SEVERE")
            },
        }
        for method, rows in scored_by_method.items()
    }
    for index, (method, rows) in enumerate(scored_by_method.items()):
        metrics[method]["day_bootstrap_95pct_ci"] = day_bootstrap_intervals(
            rows, seed=20261017 + index
        )
    per_day_rows: list[dict[str, Any]] = []
    for day in day_actuals:
        service_date = day["service_date"]
        for method, rows in scored_by_method.items():
            summary = metric_summary(
                [row for row in rows if row["service_date"] == service_date]
            )
            per_day_rows.append(
                {
                    "service_date": service_date,
                    "day_class": classes[service_date],
                    "full_day_cancel_rate_pct": round(day["actual"]["cancel_rate_pct"], 2),
                    "full_day_disruption_rate_pct": round(
                        day["actual"]["disruption_rate_pct"], 2
                    ),
                    "method": method,
                    **summary,
                }
            )

    result = {
        "schema_version": "1.0",
        "evaluation": {
            "airport": "ORD",
            "dates": sorted({day.service_date for day in evaluation_days}),
            "day_count": len(evaluation_days),
            "window_count": len(episodes),
            "selection_rule": (
                "Every reconstructable local day from July 2-31, 2026. No day was "
                "selected or removed based on model performance."
            ),
            "issue_time_visibility": (
                "Weather and ORD movements enter features only when their timestamp is at "
                "or before the forecast cutoff. Final cancellation and diversion labels "
                "never enter issue-time features because public event timestamps are absent."
            ),
            "development_disclosure": (
                "Numerical fitting, scaling, and k selection use June only. Feature and "
                "model-family design followed inspection of the July 27 showcase day, so "
                "July is a chronological out-of-month evaluation rather than a pristine "
                "never-observed research holdout."
            ),
            "normal_definition": "full-day disruption <22% and cancellation <1%",
            "severe_definition": "full-day disruption >=35%",
            "class_counts": dict(Counter(classes.values())),
            "pack_hashes": {
                day.pack_id: {
                    filename: sha256(day.path / filename)
                    for filename in ("manifest.json", "schedule.csv", "weather.csv", "ground_truth.csv")
                }
                for day in evaluation_days
            },
        },
        "metrics": metrics,
        "day_actuals": [
            {**item, "day_class": classes[item["service_date"]]} for item in day_actuals
        ],
        "delay_causes": delayed_cause_summary(evaluation_days),
        "historical_analog_model": model_artifact,
        "limitations": [
            "The final reported BTS schedule is used as the schedule input; it is not a historical schedule snapshot.",
            "Cancellation and diversion decisions have no public event timestamp and remain evaluator-only until settlement.",
            "BTS has no flight-level baggage, gate, crew, or turnaround-task events.",
            "Current weather input is point METAR observation, not time-gated forecast weather or radar.",
            "Feature and model-family design followed inspection of the July 27 showcase day.",
            "The batch benchmark validates forecast logic; FalkorDB persistence is tested separately by the graph-backed replay.",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "benchmark.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_csv(args.output_dir / "per-day-metrics.csv", per_day_rows)
    write_csv(args.output_dir / "hourly-results.csv", window_rows)
    if model_artifact:
        model_dir = repository_root / "data" / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "ord-hourly-historical-analog-june-2026.json").write_text(
            json.dumps(model_artifact, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps({"metrics": metrics, "classes": Counter(classes.values())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
