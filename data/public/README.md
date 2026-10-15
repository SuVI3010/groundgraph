# Public evidence extracts

These three small CSV files are the exact public-source rows and columns used to establish GroundGraph's historical context. They are review evidence, not a second runtime database. The replay itself reads [`../scenario.json`](../scenario.json), whose observed fields must match these extracts.

| Extract | Official source | Selection | What it supports |
|---|---|---|---|
| [`bts_ord_selected_flights_2024-01-12.csv`](bts_ord_selected_flights_2024-01-12.csv) | [BTS January 2024 On-Time Performance archive](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip) | January 12, `UA`, and the six exact flight-number/route tuples used by the replay. | Flight identity, route, direction, scheduled ORD movement time, and post-event tail continuity. |
| [`noaa_kord_selected_observations_2024-01-12.csv`](noaa_kord_selected_observations_2024-01-12.csv) | [NOAA Global Hourly KORD 2024 file](https://noaa-global-hourly-pds.s3.amazonaws.com/2024/72530094846.csv) | Station `72530094846` at `2024-01-12T14:00:00Z` and `15:04:00Z`. | The 08:00 and 09:04 CST KORD SPECI observations. |
| [`faa_ord_selected_runways_2023-12-28.csv`](faa_ord_selected_runways_2023-12-28.csv) | [FAA NASR cycle page effective December 28, 2023](https://www.faa.gov/air_traffic/flight_info/aeronav/Aero_Data/NASR_Subscription/2023-12-28/) | `APT_RWY.csv`, airport `ORD`, and the three displayed runway pairs. | Runway designators and published lengths. |

The BTS extract deliberately omits actual departure/arrival times, delays, cancellation status, and diversion status because GroundGraph does not use those outcomes in planning. `Tail_Number` remains only to make the retrospective rotation pairing reproducible; it is post-event evidence and is treated as modeled at decision time.

The NOAA extract preserves the source's encoded fields and raw report text. In the two selected reports, `3/4SM ... -SN BR OVC004` means 3/4-mile visibility, light snow, mist, and a 400-foot overcast ceiling at 08:00 CST; `2SM ... -SN BR OVC008` means 2-mile visibility and an 800-foot overcast ceiling at 09:04 CST. GroundGraph's capacity changes and delay effects are not NOAA observations.

## Reproduce the extracts

Download the three official files without renaming them:

```bash
mkdir -p /tmp/groundgraph-public-sources
cd /tmp/groundgraph-public-sources
curl -LfsS 'https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip' \
  -o On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip
curl -LfsS 'https://noaa-global-hourly-pds.s3.amazonaws.com/2024/72530094846.csv' \
  -o 72530094846.csv
curl -LfsS 'https://nfdc.faa.gov/webContent/28DaySub/extra/28_Dec_2023_APT_CSV.zip' \
  -o 28_Dec_2023_APT_CSV.zip
```

From the repository root, verify that the checked-in extracts are exactly what the pinned source files produce:

```bash
python3 data/public/extract_public_evidence.py \
  --source-dir /tmp/groundgraph-public-sources \
  --check
```

Omit `--check` only when intentionally rebuilding the extract files. The extractor uses Python's standard library, verifies each downloaded file's SHA-256 digest before reading it, selects the named records, and fails if an expected record is missing. The pinned source-file digests are:

| Official download | SHA-256 |
|---|---|
| BTS January archive | `fe089b45523f9d4ac0ccd0e176a543d274e914ee5ae5bd384dbaafc7dc06ebfd` |
| NOAA KORD annual file | `1f21901ce9998830e7078d28fbad99fc626d409d413d7d34c986b66a3df70176` |
| FAA December 28 APT CSV archive | `75431e9deb2a769f7041167a577ff6b746110effd190abf50f6a3a7ade8481cc` |

The source publishers can replace or retire download files. A future hash mismatch means the source needs a fresh audit; it must not be bypassed merely to make the extractor pass.
