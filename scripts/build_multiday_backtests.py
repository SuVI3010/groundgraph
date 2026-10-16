"""Build a complete evaluable monthly ORD backtest cohort.

Day 1 is excluded because a monthly BTS archive does not contain the prior
month's final-day rows needed to reconstruct every overnight arrival.
July 27 already has a pinned single-day source pack and is preserved by
default when building July. The remaining days are derived from one official
monthly BTS archive and one month-wide KORD METAR request.
"""

from __future__ import annotations

import argparse
import json
import calendar
import sys
from datetime import date
from pathlib import Path

try:
    from scripts.build_backtest_pack import build_outputs, digest_file
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from build_backtest_pack import build_outputs, digest_file


MONTH_SOURCES = {
    "2026-06": {
        "bts_sha256": "acd9d03e223e079e54b77065015f0ce9b6de92f1f46148048600cc9813a47a6e",
        "weather_sha256": "7f17b35eff619703c930e2e2eb098babaac0384e7bc3024dd50c039b69334a92",
        "bts_path": Path("/tmp/groundgraph-bts-2026-06.zip"),
        "weather_path": Path("/tmp/kord-2026-06-month.csv"),
        "bts_url": (
            "https://transtats.bts.gov/PREZIP/"
            "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_6.zip"
        ),
        "weather_url": (
            "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
            "network=IL_ASOS&station=ORD&data=all&year1=2026&month1=6&day1=1"
            "&year2=2026&month2=7&day2=1&tz=America%2FChicago&format=onlycomma"
            "&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=2"
        ),
    },
    "2026-07": {
        "bts_sha256": "61d657eb17f5b6f800ed2f05262a2223c5edf66fe7d9cfb3598c6664a34ed0b6",
        "weather_sha256": "45e59c39ef2ef8058794bf02dcc4e3679e58c8faf79435f1a6db637cbaf48152",
        "bts_path": Path("/tmp/groundgraph-bts-2026-07.zip"),
        "weather_path": Path("/tmp/kord-2026-07-month.csv"),
        "bts_url": (
            "https://transtats.bts.gov/PREZIP/"
            "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_7.zip"
        ),
        "weather_url": (
            "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
            "network=IL_ASOS&station=ORD&data=all&year1=2026&month1=7&day1=1"
            "&year2=2026&month2=8&day2=1&tz=America%2FChicago&format=onlycomma"
            "&latlon=no&elev=no&missing=M&trace=T&direct=no&report_type=2"
        ),
    },
}
PINNED_DAY = date(2026, 7, 27)


def parse_dates(raw: str | None, month: str) -> tuple[date, ...]:
    if not raw:
        year, month_number = (int(value) for value in month.split("-"))
        last_day = calendar.monthrange(year, month_number)[1]
        return tuple(
            date(year, month_number, day)
            for day in range(2, last_day + 1)
            if date(year, month_number, day) != PINNED_DAY
        )
    values = tuple(date.fromisoformat(value.strip()) for value in raw.split(",") if value.strip())
    if not values:
        raise ValueError("--dates did not contain a date")
    if len(set(values)) != len(values):
        raise ValueError("--dates contains a duplicate")
    return values


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--month", choices=sorted(MONTH_SOURCES), default="2026-07")
    parser.add_argument(
        "--bts-zip",
        type=Path,
    )
    parser.add_argument(
        "--weather-csv",
        type=Path,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=repository_root / "data" / "backtests",
    )
    parser.add_argument(
        "--dates",
        help="comma-separated ISO dates; defaults to every supported day after day 1",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--replace-pinned",
        action="store_true",
        help="allow replacing the separately pinned July 27 pack",
    )
    args = parser.parse_args()

    try:
        source = MONTH_SOURCES[args.month]
        bts_zip = args.bts_zip or source["bts_path"]
        weather_csv = args.weather_csv or source["weather_path"]
        assert isinstance(bts_zip, Path) and isinstance(weather_csv, Path)
        dates = parse_dates(args.dates, args.month)
        for path in (bts_zip, weather_csv):
            if not path.is_file():
                raise FileNotFoundError(f"missing source file: {path}")
        hashes = {
            "BTS": (digest_file(bts_zip), source["bts_sha256"]),
            "weather": (digest_file(weather_csv), source["weather_sha256"]),
        }
        for label, (actual, expected) in hashes.items():
            if actual != expected:
                raise ValueError(
                    f"{label} source hash mismatch: expected {expected}, got {actual}"
                )

        built = 0
        for service_date in dates:
            if service_date == PINNED_DAY and not args.replace_pinned:
                print(f"SKIP ord-{service_date.isoformat()} (pinned single-day pack)")
                continue
            if service_date.strftime("%Y-%m") != args.month or service_date.day == 1:
                raise ValueError(
                    f"this reproducible cohort supports {args.month} from day 2; day 1 lacks "
                    "the prior-month rows needed for overnight arrivals"
                )
            output = build_outputs(
                bts_zip,
                weather_csv,
                service_date=service_date,
                bts_source_url=str(source["bts_url"]),
                weather_source_url=str(source["weather_url"]),
            )
            manifest = json.loads(output["manifest.json"])
            last_day = calendar.monthrange(service_date.year, service_date.month)[1]
            manifest["benchmark_cohort"] = {
                "name": f"ORD {service_date.strftime('%B %Y')} full evaluable day cohort",
                "selection_rule": (
                    f"Every {service_date.strftime('%B')} 2-{last_day}, {service_date.year} local day. "
                    f"{service_date.strftime('%B')} 1 is excluded because its prior-day "
                    "overnight arrivals require prior-month source rows."
                ),
                "day_selected_without_outcome_filter": True,
            }
            output["manifest.json"] = (
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            destination = args.output_root / f"ord-{service_date.isoformat()}"
            destination.mkdir(parents=True, exist_ok=True)
            for filename, content in output.items():
                path = destination / filename
                if args.check:
                    if not path.is_file() or path.read_bytes() != content:
                        raise ValueError(f"checked-in artifact is stale: {path}")
                else:
                    path.write_bytes(content)
            built += 1
            print(
                f"{'PASS' if args.check else 'WROTE'} {destination.name}: "
                f"{manifest['counts']['scheduled_segments']:,} movements, "
                f"{manifest['counts']['weather_observations']:,} METARs"
            )
        print(f"COMPLETE {built} day packs")
    except (FileNotFoundError, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
