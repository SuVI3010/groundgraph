# Data provenance and claim boundaries

GroundGraph combines public historical-demand fields with explicit synthetic operating assumptions in three packaged replays. [`data/scenario-registry.json`](../data/scenario-registry.json) identifies the ORD, ATL, and SFO packages, their independent graph names, source categories, truth-boundary notes, and SHA-256 package hashes. The older machine-readable [`data/provenance.json`](../data/provenance.json) and compact [public evidence pack](../data/public/README.md) cover the default ORD scenario: they check in its exact selected source rows and columns and provide a hash-verifying extractor. ATL and SFO do not yet have equivalent checked-in selected-row extracts.

Provenance is also operational graph data, not only a repository manifest. Flights, runways, gates, events, and turnarounds carry a stable `source_id`. Seeding uses an explicit `source_record_id` when the upstream dataset supplies one and a deterministic `<source_id>:<entity_id>` key for modeled records. It creates this record-level path:

```text
(Entity)-[:SOURCED_FROM|MODELED_BY]->(SourceRecord)-[:FROM_SOURCE]->(Source)
```

Each `SourceRecord` stores the upstream row key, truth label, `observed_fields`, and `modeled_fields`. The snapshot API exposes these paths in `provenance.records`, and the Evidence view shows the records relevant to the current causal path.

## Scenario portfolio

| Scenario | Packaged public context | Checked-in verification | Modeled boundary |
|---|---|---|---|
| **ORD · January 12, 2024 · 08:00–12:00 CST** | Six BTS United schedule records, KORD observations, and three FAA runway records. | Exact selected rows, source-file hashes, extractor, and byte-for-byte checks are in [`data/public/`](../data/public/README.md). | Gates, rotation availability at decision time, resource state, capacity effects, baggage/runway events, policy, actions, and projected outcomes. |
| **ATL · January 12, 2024 · 14:00–18:00 EST** | Six BTS Delta schedule records, time-gated KATL observations, and three FAA runway identities/lengths declared in [`atl-2024-01-12.json`](../data/scenarios/atl-2024-01-12.json). | The package stores official URLs, source-file digests, record keys, and field lists. Selected raw rows are not checked in and the ORD extractor does not validate them. | Gates, rotations at decision time, resource state, weather-to-capacity effects, baggage exception, policy, actions, and projected outcomes. |
| **SFO · January 6, 2024 · 13:00–17:00 PST** | Six BTS United schedule records, time-gated KSFO observations, and three FAA runway identities/lengths declared in [`sfo-2024-01-06.json`](../data/scenarios/sfo-2024-01-06.json). | The package stores official URLs, source-file digests, record keys, and field lists. Selected raw rows are not checked in and the ORD extractor does not validate them. | Gates, rotations at decision time, resource state, wind/rain capacity effects, baggage exception, policy, actions, and projected outcomes. |

The ATL and SFO source metadata makes each claimed field inspectable, but package metadata alone is not independent verification of the copied value. The reproducible evidence claim below applies to ORD only.

## Default ORD evidence pack

| Data area | Source | Classification | Checked-in evidence | Used for |
|---|---|---|---|---|
| Six ORD flight schedules | [BTS January 2024 On-Time Performance](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip) | Observed, post-event archive | [`bts_ord_selected_flights_2024-01-12.csv`](../data/public/bts_ord_selected_flights_2024-01-12.csv) | Flight identity, direction, route, and scheduled ORD movement time. |
| KORD conditions | [NOAA Global Hourly 2024](https://noaa-global-hourly-pds.s3.amazonaws.com/2024/72530094846.csv) | Observed | [`noaa_kord_selected_observations_2024-01-12.csv`](../data/public/noaa_kord_selected_observations_2024-01-12.csv) | Time-gated weather at 08:00 and 09:04 CST. |
| ORD runway facts | [FAA NASR cycle effective December 28, 2023](https://www.faa.gov/air_traffic/flight_info/aeronav/Aero_Data/NASR_Subscription/2023-12-28/) | Observed | [`faa_ord_selected_runways_2023-12-28.csv`](../data/public/faa_ord_selected_runways_2023-12-28.csv) | Runway identities and published lengths. |
| Rotation links | Tail-continuity transformation over the selected BTS rows | Derived, treated as modeled at decision time | The BTS extract retains `Tail_Number` solely to reproduce the three pairs. | Connect an arrival to its next flight in the retrospective replay. |
| Gate state and compatibility | GroundGraph assumptions | Modeled | [`scenario.json`](../data/scenario.json) field labels | Demonstrate resource conflicts and recovery choices in ORD. |
| Capacity deltas and recovery effects | GroundGraph assumptions | Modeled | [`scenario.json`](../data/scenario.json) field labels | Produce deterministic, repeatable ORD counterfactual comparisons. |
| Baggage and runway events | GroundGraph assumptions | Modeled | [`scenario.json`](../data/scenario.json) field labels | Trigger visible event-driven ORD replanning. |
| Procedures, ownership, incident, and handoff | GroundGraph synthetic knowledge pack | Modeled | [`knowledge/manifest.json`](../knowledge/manifest.json) truth labels | Demonstrate policy authority, versioning, sources, and freshness. |

## Decision-time separation

The BTS archive was published after the historical date and includes later outcomes. GroundGraph uses only schedule identity and timing in the replay. Actual times, cancellations, diversions, and recorded delay fields do not enter the agent context or plan scoring and are omitted from the checked-in extract. The extract retains tail numbers only to reproduce the retrospective pairings; tail continuity is not presented to the model as decision-time operational evidence.

NOAA's annual CSV records time in UTC. The selected `14:00Z` and `15:04Z` rows correspond to 08:00 and 09:04 CST. They become eligible only when the replay clock reaches those local times. The 08:00 raw report contains `3/4SM`, `-SN`, `BR`, and `OVC004`; the 09:04 SPECI contains `2SM`, `-SN`, `BR`, and `OVC008`. GroundGraph models the capacity effect; NOAA does not provide an airport-capacity or delay value for these rows.

The ATL and SFO packages use the same boundary: schedule outcomes are excluded from planning, weather records become eligible only at their replay time, and every capacity or delay consequence is modeled by GroundGraph. Because their selected raw rows are not part of `data/public/`, this document does not claim the same reproducible source spot-check for those packages.

## Source spot-check

- The checked-in BTS rows match all six selected routes and scheduled ORD times. Shared post-event tail numbers reproduce the three retrospective pairs: `UA373 → UA2445`, `UA1888 → UA1071`, and `UA1414 → UA2672`.
- The checked-in NOAA rows retain the raw 08:00 and 09:04 CST KORD SPECI text used for the visibility, weather, and ceiling statements.
- The checked-in FAA rows list `10C/28C` at 10,800 feet, `10L/28R` at 13,000 feet, and `09R/27L` at 11,260 feet.

These checks establish the copied public fields. They do not validate the modeled operational effects or projected recovery.

## Reproduction and integrity

[`data/public/README.md`](../data/public/README.md) gives the exact official download commands and a standard-library extraction command for ORD. The extractor verifies the pinned SHA-256 digest of every official source file, selects only the documented ORD rows and columns, and can compare the generated bytes with the checked-in CSVs. [`data/provenance.json`](../data/provenance.json) separately pins each checked-in ORD extract and the extractor itself.

This evidence chain proves that the selected ORD public fields were copied consistently. It does not validate the ATL/SFO copied values, and it does not make any replay's gates, resource state, capacity changes, delays, recovery actions, organizational knowledge, or projected benefits observed facts.

## Field-level boundaries

Some graph records combine a sourced identity with replay state. Their truth label applies only at the field level described here:

| Record | Observed fields | Derived or modeled fields |
|---|---|---|
| Flight | Flight number, route, direction, and scheduled local movement time. | Rotation link is derived from post-event tail continuity and treated as modeled at decision time. Gate, runway assignment, status, turn time, baggage risk, delay, and recovery state are modeled. |
| Runway | Designator and published length. | Replay role, status, and hourly capacity are modeled. |
| NOAA event | Station, observation time, visibility, ceiling, and present-weather text. | Delay minutes, capacity delta, severity, and the operational effect are modeled. |
| Gate | None. | Identity, compatibility, occupancy, and availability are all modeled for the replay. |

A `MIXED` badge marks records that combine sourced facts with replay assumptions. The API returns `observed_fields`, `modeled_fields`, `source_record_id`, source identity, and the exact `SOURCED_FROM` or `MODELED_BY` relationship so the interface and reviewers can inspect the boundary directly.

Concrete ORD record keys include `2024-01-12:UA:1414:SYR:ORD` for flight `UA1414-IN` and `KORD:10L/28R` for runway `RWY-10L-28R`. The ATL and SFO packs use the same stable-key pattern for their own flight, station, and runway records, plus deterministic modeled keys for gates and turnarounds. A mixed public record also links the entity to the modeled GroundGraph source for replay-only fields. These links do not imply that modeled fields came from BTS, NOAA, or FAA.

## What the result means

All delay reductions shown by GroundGraph are **projected under the replay assumptions**. They are neither measured historical savings nor a forecast validated against live airport operations. Each top-down airport view is schematic: its named runway identities and published lengths may come from FAA records, but the display is not surveyed runway or taxiway geometry.

The modeled baggage events and other synthetic operating disruptions represent operational variability. They are not recorded incidents involving the displayed flights or any ORD, ATL, or SFO employee.

