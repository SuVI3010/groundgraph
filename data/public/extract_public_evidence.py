"""Rebuild the checked-in public evidence extracts from official source files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sys
import zipfile
from pathlib import Path


SOURCE_FILES = {
    "bts": (
        "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip",
        "fe089b45523f9d4ac0ccd0e176a543d274e914ee5ae5bd384dbaafc7dc06ebfd",
    ),
    "noaa": (
        "72530094846.csv",
        "1f21901ce9998830e7078d28fbad99fc626d409d413d7d34c986b66a3df70176",
    ),
    "faa": (
        "28_Dec_2023_APT_CSV.zip",
        "75431e9deb2a769f7041167a577ff6b746110effd190abf50f6a3a7ade8481cc",
    ),
}

BTS_OUTPUT = "bts_ord_selected_flights_2024-01-12.csv"
NOAA_OUTPUT = "noaa_kord_selected_observations_2024-01-12.csv"
FAA_OUTPUT = "faa_ord_selected_runways_2023-12-28.csv"


def digest(path: Path) -> str:
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def verified_sources(source_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for key, (filename, expected) in SOURCE_FILES.items():
        path = source_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"missing source file: {path}")
        actual = digest(path)
        if actual != expected:
            raise ValueError(
                f"source hash mismatch for {filename}: expected {expected}, got {actual}"
            )
        paths[key] = path
    return paths


def csv_bytes(fieldnames: list[str], rows: list[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def bts_extract(path: Path) -> bytes:
    wanted = [
        ("373", "BTV", "ORD"),
        ("2445", "ORD", "SAN"),
        ("1888", "ATL", "ORD"),
        ("1071", "ORD", "DFW"),
        ("1414", "SYR", "ORD"),
        ("2672", "ORD", "AUS"),
    ]
    fields = [
        "FlightDate",
        "Reporting_Airline",
        "Flight_Number_Reporting_Airline",
        "Tail_Number",
        "Origin",
        "Dest",
        "CRSDepTime",
        "CRSArrTime",
    ]
    selected: dict[tuple[str, str, str], dict[str, str]] = {}
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(member) as raw, io.TextIOWrapper(
            raw, encoding="utf-8-sig", newline=""
        ) as handle:
            for row in csv.DictReader(handle):
                key = (
                    row["Flight_Number_Reporting_Airline"],
                    row["Origin"],
                    row["Dest"],
                )
                if (
                    row["FlightDate"] == "2024-01-12"
                    and row["Reporting_Airline"] == "UA"
                    and key in wanted
                ):
                    selected[key] = {field: row[field] for field in fields}
    missing = [key for key in wanted if key not in selected]
    if missing:
        raise ValueError(f"BTS source lacks expected rows: {missing}")
    return csv_bytes(fields, [selected[key] for key in wanted])


def noaa_extract(path: Path) -> bytes:
    wanted = ["2024-01-12T14:00:00", "2024-01-12T15:04:00"]
    fields = ["STATION", "DATE", "REPORT_TYPE", "CALL_SIGN", "WND", "CIG", "VIS", "MW1", "REM"]
    with path.open(encoding="utf-8", newline="") as handle:
        selected = {
            row["DATE"]: {field: row[field] for field in fields}
            for row in csv.DictReader(handle)
            if row["STATION"] == "72530094846" and row["DATE"] in wanted
        }
    missing = [timestamp for timestamp in wanted if timestamp not in selected]
    if missing:
        raise ValueError(f"NOAA source lacks expected rows: {missing}")
    return csv_bytes(fields, [selected[timestamp] for timestamp in wanted])


def faa_extract(path: Path) -> bytes:
    wanted = ["09R/27L", "10C/28C", "10L/28R"]
    fields = [
        "EFF_DATE",
        "SITE_NO",
        "SITE_TYPE_CODE",
        "STATE_CODE",
        "ARPT_ID",
        "CITY",
        "COUNTRY_CODE",
        "RWY_ID",
        "RWY_LEN",
        "RWY_WIDTH",
        "RWY_LEN_SOURCE",
        "LENGTH_SOURCE_DATE",
    ]
    with zipfile.ZipFile(path) as archive:
        with archive.open("APT_RWY.csv") as raw, io.TextIOWrapper(
            raw, encoding="utf-8-sig", newline=""
        ) as handle:
            selected = {
                row["RWY_ID"]: {field: row[field] for field in fields}
                for row in csv.DictReader(handle)
                if row["ARPT_ID"] == "ORD" and row["RWY_ID"] in wanted
            }
    missing = [runway for runway in wanted if runway not in selected]
    if missing:
        raise ValueError(f"FAA source lacks expected rows: {missing}")
    return csv_bytes(fields, [selected[runway] for runway in wanted])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare generated bytes with the checked-in extracts without writing",
    )
    args = parser.parse_args()

    try:
        sources = verified_sources(args.source_dir)
        generated = {
            BTS_OUTPUT: bts_extract(sources["bts"]),
            NOAA_OUTPUT: noaa_extract(sources["noaa"]),
            FAA_OUTPUT: faa_extract(sources["faa"]),
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in generated.items():
            path = args.output_dir / filename
            if args.check:
                if not path.is_file() or path.read_bytes() != content:
                    raise ValueError(f"checked-in extract is stale: {path}")
                print(f"PASS {filename}")
            else:
                path.write_bytes(content)
                print(f"WROTE {path}")
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
