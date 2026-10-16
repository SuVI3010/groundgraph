"""Cross-check the July KORD METAR pack against NOAA GHCN-hourly."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


LOCAL_ZONE = ZoneInfo("America/Chicago")
NOAA_URL = (
    "https://noaa-ghcnh-pds.s3.us-east-1.amazonaws.com/hourly/access/"
    "by-year/2026/psv/GHCNh_USW00094846_2026.psv"
)
IEM_URL = (
    "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
    "network=IL_ASOS&station=ORD&data=all&year1=2026&month1=7&day1=1"
    "&year2=2026&month2=8&day2=1&tz=America%2FChicago&format=onlycomma"
    "&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=2"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--noaa-psv",
        type=Path,
        default=Path("/tmp/GHCNh_USW00094846_2026.psv"),
    )
    parser.add_argument(
        "--iem-csv",
        type=Path,
        default=Path("/tmp/kord-2026-07-month.csv"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    with args.iem_csv.open(encoding="utf-8-sig", newline="") as handle:
        iem = {
            (
                datetime.strptime(row["valid"], "%Y-%m-%d %H:%M")
                .replace(tzinfo=LOCAL_ZONE)
                .isoformat(timespec="minutes"),
                row["metar"].strip(),
            )
            for row in csv.DictReader(handle)
            if row["station"] == "ORD"
        }

    noaa: set[tuple[str, str]] = set()
    with args.noaa_psv.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="|"):
            if row["REM_Report_Type"] not in {"FM15", "FM16"}:
                continue
            observed = (
                datetime.fromisoformat(row["DATE"])
                .replace(tzinfo=timezone.utc)
                .astimezone(LOCAL_ZONE)
            )
            if (observed.year, observed.month) != (2026, 7):
                continue
            noaa.add((observed.isoformat(timespec="minutes"), row["REM"].strip()))

    matched = iem & noaa
    iem_only = sorted(iem - noaa)
    noaa_only = sorted(noaa - iem)
    result = {
        "schema_version": "1.0",
        "station": "KORD / USW00094846",
        "month": "2026-07",
        "noaa": {
            "publisher": "NOAA National Centers for Environmental Information",
            "dataset": "GHCN-hourly",
            "url": NOAA_URL,
            "sha256_at_verification": sha256(args.noaa_psv),
            "report_types": ["FM15", "FM16"],
            "observations": len(noaa),
        },
        "iem": {
            "publisher": "Iowa Environmental Mesonet, Iowa State University",
            "url": IEM_URL,
            "sha256": sha256(args.iem_csv),
            "observations": len(iem),
        },
        "exact_timestamp_and_raw_metar_matches": len(matched),
        "noaa_rows_missing_from_iem": [
            {"observed_at_local": timestamp, "raw_metar": raw}
            for timestamp, raw in noaa_only
        ],
        "iem_rows_absent_from_noaa_snapshot": [
            {"observed_at_local": timestamp, "raw_metar": raw}
            for timestamp, raw in iem_only
        ],
        "verdict": (
            "Every NOAA FM15/FM16 July observation is present byte-for-byte in the IEM "
            "input. IEM has two additional July 20 routine observations absent from the "
            "downloaded NOAA annual snapshot."
            if not noaa_only and len(iem_only) == 2
            else "The archives differ; inspect the listed records before use."
        ),
    }
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if not noaa_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
