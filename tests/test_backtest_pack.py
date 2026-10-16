from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

from scripts.build_backtest_pack import (
    EXPECTED_SOURCE_SHA256,
    GROUND_TRUTH_FIELDS,
    SCHEDULE_FIELDS,
    WEATHER_FIELDS,
    scheduled_arrival_date,
)


PACK_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "backtests"
    / "ord-2026-07-27"
)


def read_rows(filename: str) -> list[dict[str, str]]:
    with (PACK_DIR / filename).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_schedule_is_a_strict_no_outcome_allowlist() -> None:
    schedule_path = PACK_DIR / "schedule.csv"
    with schedule_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == SCHEDULE_FIELDS
        rows = list(reader)

    forbidden_fragments = {
        "actual",
        "tail",
        "delay",
        "cancel",
        "divert",
        "taxi",
        "wheel",
        "air_time",
        "cause",
    }
    assert not {
        field
        for field in reader.fieldnames or []
        if any(fragment in field.lower() for fragment in forbidden_fragments)
    }
    assert len(rows) == 2_164
    assert len({row["segment_id"] for row in rows}) == len(rows)


def test_inbound_schedule_date_resolution_handles_overnight_local_clocks() -> None:
    overnight = {
        "FlightDate": "2026-07-26",
        "Reporting_Airline": "AA",
        "Flight_Number_Reporting_Airline": "3014",
        "Origin": "CLT",
        "CRSDepTime": "2239",
        "CRSArrTime": "0001",
        "CRSElapsedTime": "142",
    }
    daytime = {
        "FlightDate": "2026-07-27",
        "Reporting_Airline": "AA",
        "Flight_Number_Reporting_Airline": "999",
        "Origin": "CLT",
        "CRSDepTime": "1000",
        "CRSArrTime": "1100",
        "CRSElapsedTime": "120",
    }

    assert scheduled_arrival_date(overnight) == (date(2026, 7, 27), 1)
    assert scheduled_arrival_date(daytime) == (date(2026, 7, 27), 0)


def test_manifest_hashes_counts_and_truth_join_are_exact() -> None:
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    schedule = read_rows("schedule.csv")
    weather = read_rows("weather.csv")
    truth = read_rows("ground_truth.csv")

    assert [source["sha256"] for source in manifest["sources"]] == [
        EXPECTED_SOURCE_SHA256["bts"],
        EXPECTED_SOURCE_SHA256["weather"],
    ]
    expected_headers = {
        "schedule": SCHEDULE_FIELDS,
        "weather": WEATHER_FIELDS,
        "ground_truth": GROUND_TRUTH_FIELDS,
    }
    for key, fields in expected_headers.items():
        metadata = manifest["files"][key]
        assert metadata["columns"] == fields
        assert metadata["sha256"] == sha256(PACK_DIR / metadata["path"])

    assert {row["segment_id"] for row in schedule} == {
        row["segment_id"] for row in truth
    }
    assert manifest["counts"] == {
        "scheduled_segments": 2_164,
        "ord_departures": 1_081,
        "ord_arrivals": 1_083,
        "weather_observations": 53,
        "cancelled_segments": 864,
        "diverted_segments": 88,
        "ord_departure_delays_15_or_more": 323,
        "ord_arrival_delays_15_or_more": 283,
        "positive_departure_delay_minutes": 66_994,
        "positive_arrival_delay_minutes": 85_310,
    }
    assert manifest["files"]["schedule"]["row_count"] == len(schedule)
    assert manifest["files"]["weather"]["row_count"] == len(weather)
    assert manifest["files"]["ground_truth"]["row_count"] == len(truth)


def test_all_replay_events_obey_the_local_window_and_truth_boundaries() -> None:
    manifest = json.loads((PACK_DIR / "manifest.json").read_text(encoding="utf-8"))
    schedule = read_rows("schedule.csv")
    weather = read_rows("weather.csv")
    start = datetime.fromisoformat(manifest["replay_window"]["local_start"])
    end = datetime.fromisoformat(manifest["replay_window"]["local_end_exclusive"])

    hhmm = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    scheduled_times = [
        datetime.fromisoformat(row["ord_scheduled_at_local"]) for row in schedule
    ]
    assert all(start <= timestamp < end for timestamp in scheduled_times)
    assert all(timestamp.utcoffset() == start.utcoffset() for timestamp in scheduled_times)
    assert all(
        hhmm.fullmatch(row[field])
        for row in schedule
        for field in ("scheduled_departure_hhmm", "scheduled_arrival_hhmm")
    )

    observation_times = [
        datetime.fromisoformat(row["observed_at_local"]) for row in weather
    ]
    assert observation_times == sorted(observation_times)
    assert all(start <= timestamp < end for timestamp in observation_times)

    boundaries = manifest["truth_boundaries"]
    assert boundaries["schedule"]["agent_access"] == "available at replay start"
    assert boundaries["weather"]["agent_access"] == "event time only"
    assert boundaries["ground_truth"]["agent_access"] == "evaluation only"
    assert "post-event labels" in boundaries["ground_truth"]["rule"]
