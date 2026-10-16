"""Build the reproducible ORD 2026-07-27 observational backtest pack.

The pack deliberately separates information that is safe to expose to an agent
from post-event outcome data.  ``schedule.csv`` is an allowlist of schedule
fields, ``weather.csv`` may be revealed only as observations arrive, and
``ground_truth.csv`` is reserved for evaluation after a decision is recorded.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo


PACK_ID = "ord-2026-07-27"
SERVICE_DATE = date(2026, 7, 27)
AIRPORT_IATA = "ORD"
AIRPORT_ICAO = "KORD"
LOCAL_TIMEZONE = "America/Chicago"
LOCAL_ZONE = ZoneInfo(LOCAL_TIMEZONE)
# During daylight time, ORD's UTC offset minus the origin's UTC offset for
# reportable U.S. domestic nonstops.  The set covers Atlantic/Eastern through
# Hawaii-Aleutian offsets.  Every selected inbound row must resolve uniquely.
ORD_MINUS_ORIGIN_OFFSET_MINUTES = (-60, 0, 60, 120, 180, 240, 300)

BTS_SOURCE_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_7.zip"
)
BTS_METADATA_URL = (
    "https://www.transtats.bts.gov/TableInfo.asp?"
    "QO_fu146_anzr=b0-gvzr&gnoyr_VQ=FGJ"
)
WEATHER_SOURCE_URL = (
    "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
    "network=IL_ASOS&station=ORD&data=all&year1=2026&month1=7&day1=27"
    "&year2=2026&month2=7&day2=28&tz=America%2FChicago&format=onlycomma"
    "&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=2"
)
WEATHER_METADATA_URL = (
    "https://mesonet.agron.iastate.edu/request/download.phtml?network=ALL_ASOS"
)

EXPECTED_SOURCE_SHA256 = {
    "bts": "61d657eb17f5b6f800ed2f05262a2223c5edf66fe7d9cfb3598c6664a34ed0b6",
    "weather": "1cd7929ad587530521adc3153e0166bb5305211083b87733e45919aebc69f581",
}

SCHEDULE_FIELDS = [
    "segment_id",
    "flight_date",
    "reporting_airline",
    "flight_number",
    "origin",
    "destination",
    "ord_movement",
    "ord_scheduled_at_local",
    "scheduled_departure_hhmm",
    "scheduled_arrival_hhmm",
    "scheduled_elapsed_minutes",
    "distance_miles",
]

WEATHER_FIELDS = [
    "observation_id",
    "observed_at_local",
    "station",
    "temperature_f",
    "dewpoint_f",
    "relative_humidity_pct",
    "wind_direction_deg",
    "wind_speed_kt",
    "wind_gust_kt",
    "precipitation_1h_in",
    "altimeter_in_hg",
    "sea_level_pressure_mb",
    "visibility_sm",
    "weather_codes",
    "sky_cover_1",
    "sky_base_1_ft",
    "sky_cover_2",
    "sky_base_2_ft",
    "sky_cover_3",
    "sky_base_3_ft",
    "sky_cover_4",
    "sky_base_4_ft",
    "raw_metar",
]

GROUND_TRUTH_FIELDS = [
    "segment_id",
    "tail_number",
    "ord_actual_at_local",
    "ord_delay_minutes",
    "ord_delayed_15",
    "actual_departure_hhmm",
    "departure_delay_minutes",
    "departure_delayed_15",
    "taxi_out_minutes",
    "wheels_off_hhmm",
    "wheels_on_hhmm",
    "taxi_in_minutes",
    "actual_arrival_hhmm",
    "arrival_delay_minutes",
    "arrival_delayed_15",
    "cancelled",
    "cancellation_code",
    "diverted",
    "actual_elapsed_minutes",
    "air_time_minutes",
    "carrier_delay_minutes",
    "weather_delay_minutes",
    "nas_delay_minutes",
    "security_delay_minutes",
    "late_aircraft_delay_minutes",
    "diverted_airport_landings",
    "diverted_reached_destination",
]


def digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def digest_file(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def csv_bytes(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        lineterminator="\n",
        extrasaction="raise",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def clean(value: str | None) -> str:
    value = (value or "").strip()
    return "" if value in {"M", "NA", "N/A"} else value


def compact_number(value: str | None) -> str:
    value = clean(value)
    if not value:
        return ""
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"expected a number, got {value!r}") from exc
    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def integer_flag(value: str | None) -> str:
    value = compact_number(value)
    if not value:
        return ""
    if value not in {"0", "1"}:
        raise ValueError(f"expected a zero/one flag, got {value!r}")
    return value


def parse_hhmm(value: str | None) -> tuple[int, int] | None:
    value = clean(value)
    if not value:
        return None
    try:
        numeric = str(int(Decimal(value))).zfill(4)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid BTS time {value!r}") from exc
    if numeric == "2400":
        return 24, 0
    hour, minute = int(numeric[:-2]), int(numeric[-2:])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid BTS time {value!r}")
    return hour, minute


def format_hhmm(value: str | None) -> str:
    parsed = parse_hhmm(value)
    if parsed is None:
        return ""
    hour, minute = parsed
    return "00:00" if hour == 24 else f"{hour:02d}:{minute:02d}"


def local_datetime(local_date: date, value: str | None) -> datetime:
    parsed = parse_hhmm(value)
    if parsed is None:
        raise ValueError("scheduled ORD time is missing")
    hour, minute = parsed
    base = datetime.combine(local_date, datetime.min.time(), tzinfo=LOCAL_ZONE)
    return base + timedelta(hours=hour, minutes=minute)


def scheduled_arrival_date(row: dict[str, str]) -> tuple[date, int]:
    """Resolve the ORD arrival date without exposing any realized outcome.

    BTS supplies endpoint-local schedule clocks, the origin service date, and
    scheduled elapsed time, but no scheduled arrival date.  Solve the clock
    equation against valid U.S. domestic UTC-offset differences and reject an
    ambiguous row rather than guessing.
    """

    departure = parse_hhmm(row["CRSDepTime"])
    arrival = parse_hhmm(row["CRSArrTime"])
    elapsed = signed_minutes(row["CRSElapsedTime"])
    if departure is None or arrival is None or elapsed is None:
        raise ValueError("inbound BTS row lacks schedule fields needed for date resolution")
    departure_minutes = departure[0] * 60 + departure[1]
    arrival_minutes = arrival[0] * 60 + arrival[1]
    candidates: list[int] = []
    for offset_difference in ORD_MINUS_ORIGIN_OFFSET_MINUTES:
        numerator = (
            elapsed
            - (arrival_minutes - departure_minutes)
            + offset_difference
        )
        if numerator % (24 * 60) == 0:
            candidates.append(numerator // (24 * 60))
    candidates = sorted(set(candidates))
    if len(candidates) != 1:
        raise ValueError(
            "could not uniquely resolve scheduled ORD arrival date for "
            f"{row['Reporting_Airline']} {row['Flight_Number_Reporting_Airline']} "
            f"from {row['Origin']}: day-offset candidates={candidates}"
        )
    day_offset = candidates[0]
    if day_offset not in {0, 1}:
        raise ValueError(f"unexpected domestic scheduled arrival day offset {day_offset}")
    return date.fromisoformat(row["FlightDate"]) + timedelta(days=day_offset), day_offset


def signed_minutes(value: str | None) -> int | None:
    value = compact_number(value)
    if not value:
        return None
    number = Decimal(value)
    if number != number.to_integral_value():
        raise ValueError(f"expected whole minutes, got {value!r}")
    return int(number)


def read_bts_rows(
    path: Path, *, service_date: date = SERVICE_DATE
) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one BTS CSV member, found {members}")
        with archive.open(members[0]) as raw, io.TextIOWrapper(
            raw, encoding="utf-8-sig", newline=""
        ) as handle:
            reader = csv.DictReader(handle)
            required = {
                "FlightDate",
                "Reporting_Airline",
                "Flight_Number_Reporting_Airline",
                "Tail_Number",
                "Origin",
                "Dest",
                "CRSDepTime",
                "CRSArrTime",
                "CRSElapsedTime",
                "Distance",
                "DepTime",
                "DepDelay",
                "DepDel15",
                "TaxiOut",
                "WheelsOff",
                "WheelsOn",
                "TaxiIn",
                "ArrTime",
                "ArrDelay",
                "ArrDel15",
                "Cancelled",
                "CancellationCode",
                "Diverted",
                "ActualElapsedTime",
                "AirTime",
                "CarrierDelay",
                "WeatherDelay",
                "NASDelay",
                "SecurityDelay",
                "LateAircraftDelay",
                "DivAirportLandings",
                "DivReachedDest",
            }
            missing = required.difference(reader.fieldnames or [])
            if missing:
                raise ValueError(f"BTS CSV lacks required fields: {sorted(missing)}")
            previous_date = (service_date - timedelta(days=1)).isoformat()
            for source_index, row in enumerate(reader):
                if row["Origin"] == AIRPORT_IATA:
                    if row["FlightDate"] != service_date.isoformat():
                        continue
                    ord_date = service_date
                    day_offset = 0
                elif row["Dest"] == AIRPORT_IATA and row["FlightDate"] in {
                    previous_date,
                    service_date.isoformat(),
                }:
                    ord_date, day_offset = scheduled_arrival_date(row)
                    if ord_date != service_date:
                        continue
                else:
                    continue
                row["_source_index"] = str(source_index)
                row["_ord_scheduled_date"] = ord_date.isoformat()
                row["_arrival_day_offset"] = str(day_offset)
                selected.append(row)
    if not selected:
        raise ValueError("BTS source contains no matching ORD service-day rows")
    return selected


def build_flight_rows(
    source_rows: list[dict[str, str]],
    *,
    service_date: date = SERVICE_DATE,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    def safe_sort_key(row: dict[str, str]) -> tuple[object, ...]:
        movement = "departure" if row["Origin"] == AIRPORT_IATA else "arrival"
        ord_time = row["CRSDepTime"] if movement == "departure" else row["CRSArrTime"]
        parsed = parse_hhmm(ord_time)
        if parsed is None:
            raise ValueError("matching BTS row has no scheduled ORD time")
        hour, minute = parsed
        return (
            hour * 60 + minute,
            movement,
            row["Reporting_Airline"],
            row["Flight_Number_Reporting_Airline"].zfill(6),
            row["Origin"],
            row["Dest"],
            format_hhmm(row["CRSDepTime"]),
            format_hhmm(row["CRSArrTime"]),
            int(row["_source_index"]),
        )

    schedule_rows: list[dict[str, str]] = []
    truth_rows: list[dict[str, str]] = []
    for index, row in enumerate(sorted(source_rows, key=safe_sort_key), start=1):
        segment_id = f"ord-{service_date.strftime('%Y%m%d')}-{index:04d}"
        movement = "departure" if row["Origin"] == AIRPORT_IATA else "arrival"
        scheduled_source = (
            row["CRSDepTime"] if movement == "departure" else row["CRSArrTime"]
        )
        ord_scheduled = local_datetime(
            date.fromisoformat(row["_ord_scheduled_date"]), scheduled_source
        )

        schedule_rows.append(
            {
                "segment_id": segment_id,
                "flight_date": row["FlightDate"],
                "reporting_airline": row["Reporting_Airline"],
                "flight_number": row["Flight_Number_Reporting_Airline"],
                "origin": row["Origin"],
                "destination": row["Dest"],
                "ord_movement": movement,
                "ord_scheduled_at_local": ord_scheduled.isoformat(timespec="seconds"),
                "scheduled_departure_hhmm": format_hhmm(row["CRSDepTime"]),
                "scheduled_arrival_hhmm": format_hhmm(row["CRSArrTime"]),
                "scheduled_elapsed_minutes": compact_number(row["CRSElapsedTime"]),
                "distance_miles": compact_number(row["Distance"]),
            }
        )

        delay_source = row["DepDelay"] if movement == "departure" else row["ArrDelay"]
        ord_delay = signed_minutes(delay_source)
        ord_actual = (
            (ord_scheduled + timedelta(minutes=ord_delay)).isoformat(timespec="seconds")
            if ord_delay is not None
            else ""
        )
        truth_rows.append(
            {
                "segment_id": segment_id,
                "tail_number": clean(row["Tail_Number"]),
                "ord_actual_at_local": ord_actual,
                "ord_delay_minutes": "" if ord_delay is None else str(ord_delay),
                "ord_delayed_15": integer_flag(
                    row["DepDel15"] if movement == "departure" else row["ArrDel15"]
                ),
                "actual_departure_hhmm": format_hhmm(row["DepTime"]),
                "departure_delay_minutes": compact_number(row["DepDelay"]),
                "departure_delayed_15": integer_flag(row["DepDel15"]),
                "taxi_out_minutes": compact_number(row["TaxiOut"]),
                "wheels_off_hhmm": format_hhmm(row["WheelsOff"]),
                "wheels_on_hhmm": format_hhmm(row["WheelsOn"]),
                "taxi_in_minutes": compact_number(row["TaxiIn"]),
                "actual_arrival_hhmm": format_hhmm(row["ArrTime"]),
                "arrival_delay_minutes": compact_number(row["ArrDelay"]),
                "arrival_delayed_15": integer_flag(row["ArrDel15"]),
                "cancelled": integer_flag(row["Cancelled"]),
                "cancellation_code": clean(row["CancellationCode"]),
                "diverted": integer_flag(row["Diverted"]),
                "actual_elapsed_minutes": compact_number(row["ActualElapsedTime"]),
                "air_time_minutes": compact_number(row["AirTime"]),
                "carrier_delay_minutes": compact_number(row["CarrierDelay"]),
                "weather_delay_minutes": compact_number(row["WeatherDelay"]),
                "nas_delay_minutes": compact_number(row["NASDelay"]),
                "security_delay_minutes": compact_number(row["SecurityDelay"]),
                "late_aircraft_delay_minutes": compact_number(row["LateAircraftDelay"]),
                "diverted_airport_landings": compact_number(row["DivAirportLandings"]),
                "diverted_reached_destination": integer_flag(row["DivReachedDest"]),
            }
        )
    return schedule_rows, truth_rows


def build_weather_rows(
    path: Path, *, service_date: date = SERVICE_DATE
) -> list[dict[str, str]]:
    rows: list[tuple[datetime, dict[str, str]]] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "station",
            "valid",
            "tmpf",
            "dwpf",
            "relh",
            "drct",
            "sknt",
            "gust",
            "p01i",
            "alti",
            "mslp",
            "vsby",
            "wxcodes",
            "skyc1",
            "skyc2",
            "skyc3",
            "skyc4",
            "skyl1",
            "skyl2",
            "skyl3",
            "skyl4",
            "metar",
        }
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"weather CSV lacks required fields: {sorted(missing)}")
        for row in reader:
            if row["station"] != AIRPORT_IATA:
                continue
            observed = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(
                tzinfo=LOCAL_ZONE
            )
            if observed.date() != service_date:
                continue
            rows.append((observed, row))
    rows.sort(key=lambda item: item[0])
    if not rows:
        raise ValueError("weather source contains no matching ORD observations")

    result: list[dict[str, str]] = []
    for index, (observed, row) in enumerate(rows, start=1):
        result.append(
            {
                "observation_id": f"kord-{service_date.strftime('%Y%m%d')}-{index:04d}",
                "observed_at_local": observed.isoformat(timespec="seconds"),
                "station": row["station"],
                "temperature_f": clean(row["tmpf"]),
                "dewpoint_f": clean(row["dwpf"]),
                "relative_humidity_pct": clean(row["relh"]),
                "wind_direction_deg": clean(row["drct"]),
                "wind_speed_kt": clean(row["sknt"]),
                "wind_gust_kt": clean(row["gust"]),
                "precipitation_1h_in": clean(row["p01i"]),
                "altimeter_in_hg": clean(row["alti"]),
                "sea_level_pressure_mb": clean(row["mslp"]),
                "visibility_sm": clean(row["vsby"]),
                "weather_codes": clean(row["wxcodes"]),
                "sky_cover_1": clean(row["skyc1"]),
                "sky_base_1_ft": clean(row["skyl1"]),
                "sky_cover_2": clean(row["skyc2"]),
                "sky_base_2_ft": clean(row["skyl2"]),
                "sky_cover_3": clean(row["skyc3"]),
                "sky_base_3_ft": clean(row["skyl3"]),
                "sky_cover_4": clean(row["skyc4"]),
                "sky_base_4_ft": clean(row["skyl4"]),
                "raw_metar": clean(row["metar"]),
            }
        )
    return result


def count_flag(rows: list[dict[str, str]], field: str) -> int:
    return sum(row[field] == "1" for row in rows)


def sum_positive_minutes(rows: list[dict[str, str]], field: str) -> int:
    total = Decimal(0)
    for row in rows:
        value = row[field]
        if value:
            total += max(Decimal(0), Decimal(value))
    return int(total)


def build_outputs(
    bts_zip: Path,
    weather_csv: Path,
    *,
    service_date: date = SERVICE_DATE,
    bts_source_url: str = BTS_SOURCE_URL,
    weather_source_url: str = WEATHER_SOURCE_URL,
) -> dict[str, bytes]:
    schedule_rows, truth_rows = build_flight_rows(
        read_bts_rows(bts_zip, service_date=service_date),
        service_date=service_date,
    )
    weather_rows = build_weather_rows(weather_csv, service_date=service_date)
    pack_id = f"ord-{service_date.isoformat()}"

    output = {
        "schedule.csv": csv_bytes(SCHEDULE_FIELDS, schedule_rows),
        "weather.csv": csv_bytes(WEATHER_FIELDS, weather_rows),
        "ground_truth.csv": csv_bytes(GROUND_TRUTH_FIELDS, truth_rows),
    }
    file_metadata = {
        name.removesuffix(".csv"): {
            "path": name,
            "row_count": len(
                schedule_rows
                if name == "schedule.csv"
                else weather_rows
                if name == "weather.csv"
                else truth_rows
            ),
            "sha256": digest_bytes(content),
            "columns": (
                SCHEDULE_FIELDS
                if name == "schedule.csv"
                else WEATHER_FIELDS
                if name == "weather.csv"
                else GROUND_TRUTH_FIELDS
            ),
        }
        for name, content in output.items()
    }

    movement_by_id = {row["segment_id"]: row["ord_movement"] for row in schedule_rows}
    departures = [
        row for row in truth_rows if movement_by_id[row["segment_id"]] == "departure"
    ]
    arrivals = [
        row for row in truth_rows if movement_by_id[row["segment_id"]] == "arrival"
    ]
    manifest = {
        "schema_version": "1.0",
        "pack_id": pack_id,
        "day_summary": (
            f"ORD's {service_date.isoformat()} local scheduled-operation cohort contains "
            f"{len(schedule_rows):,} reporting-carrier domestic segments: "
            f"{len(departures):,} departures and {len(arrivals):,} arrivals. "
            f"The realized record includes {count_flag(truth_rows, 'cancelled'):,} "
            f"cancellations, {count_flag(truth_rows, 'diverted'):,} diversions, and "
            f"{count_flag(departures, 'ord_delayed_15') + count_flag(arrivals, 'ord_delayed_15'):,} "
            "ORD endpoint delays of at least 15 minutes. Archived KORD METAR observations "
            "for the same local day remain available in weather.csv."
        ),
        "airport": {
            "iata": AIRPORT_IATA,
            "icao": AIRPORT_ICAO,
            "name": "Chicago O'Hare International Airport",
            "timezone": LOCAL_TIMEZONE,
        },
        "replay_window": {
            "service_date": service_date.isoformat(),
            "local_start": datetime.combine(
                service_date, datetime.min.time(), tzinfo=LOCAL_ZONE
            ).isoformat(timespec="seconds"),
            "local_end_exclusive": datetime.combine(
                service_date + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL_ZONE
            ).isoformat(timespec="seconds"),
            "flight_selection": (
                f"Departures use Origin == ORD and BTS FlightDate == {service_date.isoformat()}. "
                "Arrivals use Dest == ORD and a resolved scheduled ORD arrival date of "
                f"{service_date.isoformat()}, including prior-origin-date overnight flights "
                f"and excluding flights scheduled to reach ORD on {(service_date + timedelta(days=1)).isoformat()}."
            ),
            "arrival_date_derivation": (
                "BTS does not publish a scheduled arrival date. The builder solves "
                "scheduled elapsed minutes = arrival clock - departure clock + day "
                "offset - (ORD UTC offset - origin UTC offset) against the valid U.S. "
                "domestic offset differences [-60, 0, 60, 120, 180, 240, 300] minutes. "
                "Every inbound candidate must produce exactly one day offset."
            ),
        },
        "truth_boundaries": {
            "schedule": {
                "agent_access": "available at replay start",
                "rule": (
                    "Only the allowlisted columns in schedule.csv may be loaded before "
                    "a prediction or recommendation. Tail identity and every realized "
                    "outcome are excluded."
                ),
            },
            "weather": {
                "agent_access": "event time only",
                "rule": (
                    "Reveal an observation only when the replay clock is greater than or "
                    "equal to observed_at_local."
                ),
            },
            "ground_truth": {
                "agent_access": "evaluation only",
                "rule": (
                    "Never expose ground_truth.csv to an agent before its prediction or "
                    "recommendation is persisted. Delay causes are post-event labels."
                ),
                "known_absences": [
                    "cancellation decision timestamp",
                    "gate assignment history",
                    "baggage and turnaround task events",
                    "crew assignment and legality",
                    "passenger connections",
                    "maintenance state",
                    "counterfactual outcome under an alternative action",
                ],
            },
        },
        "sources": [
            {
                "id": "bts_reporting_carrier_on_time_performance",
                "publisher": "U.S. Bureau of Transportation Statistics",
                "download_url": bts_source_url,
                "metadata_url": BTS_METADATA_URL,
                "source_filename": bts_zip.name,
                "sha256": digest_file(bts_zip),
                "scope": (
                    f"{service_date.strftime('%B %Y')} reporting-carrier domestic nonstop "
                    "flight records"
                ),
            },
            {
                "id": "iem_asos_metar_archive",
                "publisher": "Iowa Environmental Mesonet, Iowa State University",
                "download_url": weather_source_url,
                "metadata_url": WEATHER_METADATA_URL,
                "source_filename": weather_csv.name,
                "sha256": digest_file(weather_csv),
                "scope": "KORD routine and special METAR observations requested in local time",
            },
        ],
        "files": file_metadata,
        "counts": {
            "scheduled_segments": len(schedule_rows),
            "ord_departures": len(departures),
            "ord_arrivals": len(arrivals),
            "weather_observations": len(weather_rows),
            "cancelled_segments": count_flag(truth_rows, "cancelled"),
            "diverted_segments": count_flag(truth_rows, "diverted"),
            "ord_departure_delays_15_or_more": count_flag(
                departures, "ord_delayed_15"
            ),
            "ord_arrival_delays_15_or_more": count_flag(arrivals, "ord_delayed_15"),
            "positive_departure_delay_minutes": sum_positive_minutes(
                departures, "ord_delay_minutes"
            ),
            "positive_arrival_delay_minutes": sum_positive_minutes(
                arrivals, "ord_delay_minutes"
            ),
        },
        "coverage_notes": [
            "BTS covers reportable U.S. domestic nonstop operations, not every ORD movement.",
            "FlightDate is the scheduled origin service date; the builder resolves the ORD scheduled arrival date and requires a unique valid offset solution.",
            "IEM distributes the archived observations as-is and notes that only limited quality control is applied.",
            "A historical replay can score predictions against realized outcomes but cannot observe the outcome of an action that was not taken.",
        ],
    }
    output["manifest.json"] = (
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    return output


def verify_source_hashes(bts_zip: Path, weather_csv: Path) -> None:
    actual = {"bts": digest_file(bts_zip), "weather": digest_file(weather_csv)}
    for source, expected in EXPECTED_SOURCE_SHA256.items():
        if actual[source] != expected:
            raise ValueError(
                f"{source} source hash mismatch: expected {expected}, got {actual[source]}"
            )


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bts-zip",
        type=Path,
        default=Path("/tmp/groundgraph-bts-2026-07.zip"),
    )
    parser.add_argument(
        "--weather-csv",
        type=Path,
        default=Path("/tmp/kord-2026-07-27.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repository_root / "data" / "backtests" / PACK_ID,
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare generated bytes with the checked-in pack without writing",
    )
    args = parser.parse_args()

    try:
        for path in (args.bts_zip, args.weather_csv):
            if not path.is_file():
                raise FileNotFoundError(f"missing source file: {path}")
        verify_source_hashes(args.bts_zip, args.weather_csv)
        generated = build_outputs(args.bts_zip, args.weather_csv)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in generated.items():
            destination = args.output_dir / filename
            if args.check:
                if not destination.is_file() or destination.read_bytes() != content:
                    raise ValueError(f"checked-in backtest artifact is stale: {destination}")
                print(f"PASS {filename}")
            else:
                destination.write_bytes(content)
                print(f"WROTE {destination}")
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
