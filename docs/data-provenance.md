# Data provenance and claim boundaries

GroundGraph combines a small public-data replay with explicit synthetic operating assumptions. The machine-readable manifest is [`data/provenance.json`](../data/provenance.json). A compact [public evidence pack](../data/public/README.md) checks in the exact source rows and columns used by the replay, plus a hash-verifying extractor that rebuilds them from the official downloads.

Provenance is also operational graph data, not only a repository manifest. Flights, runways, gates, events, and turnarounds carry a stable `source_id`. Seeding uses an explicit `source_record_id` when the upstream dataset supplies one and a deterministic `<source_id>:<entity_id>` key for modeled records. It creates this record-level path:

```text
(Entity)-[:SOURCED_FROM|MODELED_BY]->(SourceRecord)-[:FROM_SOURCE]->(Source)
```

Each `SourceRecord` stores the upstream row key, truth label, `observed_fields`, and `modeled_fields`. The snapshot API exposes these paths in `provenance.records`, and the Evidence view shows the records relevant to the current causal path.

| Data area | Source | Classification | Checked-in evidence | Used for |
|---|---|---|---|---|
| Six flight schedules | [BTS January 2024 On-Time Performance](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip) | Observed, post-event archive | [`bts_ord_selected_flights_2024-01-12.csv`](../data/public/bts_ord_selected_flights_2024-01-12.csv) | Flight identity, direction, route, and scheduled ORD movement time. |
| KORD conditions | [NOAA Global Hourly 2024](https://noaa-global-hourly-pds.s3.amazonaws.com/2024/72530094846.csv) | Observed | [`noaa_kord_selected_observations_2024-01-12.csv`](../data/public/noaa_kord_selected_observations_2024-01-12.csv) | Time-gated weather at 08:00 and 09:04 CST. |
| ORD runway facts | [FAA NASR cycle effective December 28, 2023](https://www.faa.gov/air_traffic/flight_info/aeronav/Aero_Data/NASR_Subscription/2023-12-28/) | Observed | [`faa_ord_selected_runways_2023-12-28.csv`](../data/public/faa_ord_selected_runways_2023-12-28.csv) | Runway identities and published lengths. |
| Rotation links | Tail-continuity transformation over the selected BTS rows | Derived, treated as modeled at decision time | The BTS extract retains `Tail_Number` solely to reproduce the three pairs. | Connect an arrival to its next flight in the retrospective replay. |
| Gate state and compatibility | GroundGraph assumptions | Modeled | [`scenario.json`](../data/scenario.json) field labels | Demonstrate resource conflicts and recovery choices. |
| Capacity deltas and recovery effects | GroundGraph assumptions | Modeled | [`scenario.json`](../data/scenario.json) field labels | Produce deterministic, repeatable counterfactual comparisons. |
| Baggage and runway events | GroundGraph assumptions | Modeled | [`scenario.json`](../data/scenario.json) field labels | Trigger visible event-driven replanning. |
| Procedures, ownership, incident, and handoff | GroundGraph synthetic knowledge pack | Modeled | [`knowledge/manifest.json`](../knowledge/manifest.json) truth labels | Demonstrate policy authority, versioning, sources, and freshness. |

## Decision-time separation

The BTS archive was published after the historical date and includes later outcomes. GroundGraph uses only schedule identity and timing in the replay. Actual times, cancellations, diversions, and recorded delay fields do not enter the agent context or plan scoring and are omitted from the checked-in extract. The extract retains tail numbers only to reproduce the retrospective pairings; tail continuity is not presented to the model as decision-time operational evidence.

NOAA's annual CSV records time in UTC. The selected `14:00Z` and `15:04Z` rows correspond to 08:00 and 09:04 CST. They become eligible only when the replay clock reaches those local times. The 08:00 raw report contains `3/4SM`, `-SN`, `BR`, and `OVC004`; the 09:04 SPECI contains `2SM`, `-SN`, `BR`, and `OVC008`. GroundGraph models the capacity effect; NOAA does not provide an airport-capacity or delay value for these rows.

## Source spot-check

- The checked-in BTS rows match all six selected routes and scheduled ORD times. Shared post-event tail numbers reproduce the three retrospective pairs: `UA373 → UA2445`, `UA1888 → UA1071`, and `UA1414 → UA2672`.
- The checked-in NOAA rows retain the raw 08:00 and 09:04 CST KORD SPECI text used for the visibility, weather, and ceiling statements.
- The checked-in FAA rows list `10C/28C` at 10,800 feet, `10L/28R` at 13,000 feet, and `09R/27L` at 11,260 feet.

These checks establish the copied public fields. They do not validate the modeled operational effects or projected recovery.

## Reproduction and integrity

[`data/public/README.md`](../data/public/README.md) gives the exact official download commands and a standard-library extraction command. The extractor verifies the pinned SHA-256 digest of every official source file, selects only the documented rows and columns, and can compare the generated bytes with the checked-in CSVs. [`data/provenance.json`](../data/provenance.json) separately pins each checked-in extract and the extractor itself.

This evidence chain proves that the public fields were copied consistently. It does not make the replay's gates, resource state, capacity changes, delays, recovery actions, organizational knowledge, or projected benefits observed facts.

## Field-level boundaries

Some graph records combine a sourced identity with replay state. Their truth label applies only at the field level described here:

| Record | Observed fields | Derived or modeled fields |
|---|---|---|
| Flight | Flight number, route, direction, and scheduled ORD movement time. | Rotation link is derived from post-event tail continuity and treated as modeled at decision time. Gate, runway assignment, status, turn time, baggage risk, delay, and recovery state are modeled. |
| Runway | Designator and published length. | Replay role, status, and hourly capacity are modeled. |
| NOAA event | Station, observation time, visibility, ceiling, and present-weather text. | Delay minutes, capacity delta, severity, and the operational effect are modeled. |
| Gate | None. | Identity, compatibility, occupancy, and availability are all modeled for the replay. |

A `MIXED` badge marks records that combine sourced facts with replay assumptions. The API returns `observed_fields`, `modeled_fields`, `source_record_id`, source identity, and the exact `SOURCED_FROM` or `MODELED_BY` relationship so the interface and reviewers can inspect the boundary directly.

Concrete record keys include `2024-01-12:UA:1414:SYR:ORD` for flight `UA1414-IN`, `KORD:10L/28R` for runway `RWY-10L-28R`, and deterministic modeled keys for gates and turnarounds. A mixed public record also links the entity to the modeled GroundGraph source for the replay-only fields. These links do not imply that modeled fields came from BTS, NOAA, or FAA.

## What the result means

All delay reductions shown by GroundGraph are **projected under the replay assumptions**. They are neither measured historical savings nor a forecast validated against live airport operations. The top-down airport view is a schematic using FAA-published runway identities; it is not surveyed taxiway geometry.

The modeled baggage event represents operational variability. It is not a recorded incident involving those flights or any O'Hare employee.

