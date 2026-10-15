# Data provenance and claim boundaries

GroundGraph combines a small public-data replay with explicit synthetic operating assumptions. The machine-readable manifest is [`data/provenance.json`](../data/provenance.json).

| Data area | Source | Classification | Used for |
|---|---|---|---|
| Six flight schedules | [BTS January 2024 On-Time Performance](https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip) | Observed, post-event archive | Flight identity, direction, route, and scheduled ORD movement time. |
| KORD conditions | [NOAA Global Hourly 2024](https://noaa-global-hourly-pds.s3.amazonaws.com/2024/72530094846.csv) | Observed | Time-gated weather at 08:00 and 09:04 CST. |
| ORD runway facts | [FAA NASR, December 28, 2023 cycle](https://nfdc.faa.gov/webContent/28DaySub/extra/28_Dec_2023_APT_CSV.zip) | Observed | Runway identities and published lengths. |
| Rotation links | Tail-continuity transformation over BTS rows | Derived, treated as modeled at decision time | Connect an arrival to its next flight in the retrospective replay. |
| Gate state and compatibility | GroundGraph assumptions | Modeled | Demonstrate resource conflicts and recovery choices. |
| Capacity deltas and recovery effects | GroundGraph assumptions | Modeled | Produce deterministic, repeatable counterfactual comparisons. |
| Baggage and runway events | GroundGraph assumptions | Modeled | Trigger visible event-driven replanning. |
| Procedures, ownership, incident, and handoff | GroundGraph synthetic knowledge pack | Modeled | Demonstrate policy authority, versioning, sources, and freshness. |

## Decision-time separation

The BTS archive was published after the historical date and includes later outcomes. GroundGraph uses only schedule identity and timing in the replay. Actual times, cancellations, diversions, and recorded delay fields do not enter the agent context or plan scoring.

NOAA observations become eligible only when the replay clock reaches them. The 08:00 observation includes 3/4-mile visibility, snow, mist, and a 400-foot ceiling. The 09:04 SPECI reports 2-mile visibility and an 800-foot ceiling. GroundGraph models the capacity effect; NOAA does not provide that derived airport-capacity number.

## Source spot-check

- The BTS rows for January 12 match the six selected routes and scheduled ORD times. Their archived tail continuity supports the three retrospective pairs: `UA373 → UA2445`, `UA1888 → UA1071`, and `UA1414 → UA2672`.
- The NOAA file contains the 08:00 CST KORD SPECI with 3/4-mile visibility and an overcast 400-foot ceiling, and the 09:04 CST SPECI with 2-mile visibility and an overcast 800-foot ceiling.
- The FAA runway file lists `10C/28C` at 10,800 feet, `10L/28R` at 13,000 feet, and `09R/27L` at 11,260 feet.

These checks establish the copied public fields. They do not validate the modeled operational effects or projected recovery.

## Field-level boundaries

Some graph records combine a sourced identity with replay state. Their truth label applies only at the field level described here:

| Record | Observed fields | Derived or modeled fields |
|---|---|---|
| Flight | Flight number, route, direction, and scheduled ORD movement time. | Rotation link is derived from post-event tail continuity and treated as modeled at decision time. Gate, runway assignment, status, turn time, baggage risk, delay, and recovery state are modeled. |
| Runway | Designator and published length. | Replay role, status, and hourly capacity are modeled. |
| NOAA event | Station, observation time, visibility, ceiling, and present-weather text. | Delay minutes, capacity delta, severity, and the operational effect are modeled. |
| Gate | None. | Identity, compatibility, occupancy, and availability are all modeled for the replay. |

A `MIXED` badge marks records that combine sourced facts with replay assumptions. The API also returns `observed_fields` and `modeled_fields` so the interface and reviewers can inspect the boundary directly.

## What the result means

All delay reductions shown by GroundGraph are **projected under the replay assumptions**. They are neither measured historical savings nor a forecast validated against live airport operations. The top-down airport view is a schematic using FAA-published runway identities; it is not surveyed taxiway geometry.

The modeled baggage event represents operational variability. It is not a recorded incident involving those flights or any O'Hare employee.

