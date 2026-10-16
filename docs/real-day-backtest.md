# ORD real-day factual shadow test

GroundGraph's interactive factual shadow test replays Chicago O'Hare on **July 27, 2026, 00:00–24:00 local time**. It asks a narrow, measurable question: using only information available at each replay watermark, how closely can the agent system forecast the next hour's recorded operating outcome? The same forecast logic is also evaluated across every reconstructable July 2–31 day in the [30-day benchmark](multiday-benchmark.md).

This mode does not replay the synthetic recovery actions used by the main ORD, ATL, and SFO demonstration. It saves forecasts, compares them with public observed outcomes, and reports forecast error. It does not claim that GroundGraph changed the historical day.

## Why this date is not yesterday

Airline on-time performance is published as a delayed monthly archive. When these packs were built, **July 2026 was the latest complete public BTS month with the delay and cancellation fields needed for ground truth**. The interactive replay therefore uses July 27 rather than yesterday. It is a reproducible historical evaluation, not a live feed.

The checked-in pack covers the strict ORD local calendar day and contains:

| File | Role | Rows |
|---|---|---:|
| [`schedule.csv`](../data/backtests/ord-2026-07-27/schedule.csv) | Allowlisted BTS schedule fields available before forecasting. It contains no tail number or realized outcome. | 2,164 |
| [`weather.csv`](../data/backtests/ord-2026-07-27/weather.csv) | Archived KORD METAR observations distributed by the Iowa Environmental Mesonet and revealed at their recorded observation times. | 53 |
| [`ground_truth.csv`](../data/backtests/ord-2026-07-27/ground_truth.csv) | Sealed BTS realized outcomes used by the evaluator. | 2,164 |

The cohort contains 1,081 scheduled ORD departures and 1,083 scheduled ORD arrivals. The recorded archive contains 864 cancellations, 88 diversions, and 606 ORD endpoint delays of at least 15 minutes. BTS covers reporting-carrier U.S. domestic nonstop segments, so these rows are not every movement at O'Hare.

The pack's [`manifest.json`](../data/backtests/ord-2026-07-27/manifest.json) records the source URLs, source and output SHA-256 hashes, field allowlists, date-selection rule, counts, and known absences. [`scripts/build_backtest_pack.py`](../scripts/build_backtest_pack.py) reproduces or verifies the pack from the pinned BTS and METAR source files.

## The information boundary

The runtime keeps observed facts and model output distinct:

| Class | Examples | When it is available |
|---|---|---|
| **Observed input** | Published schedule identity and time | At replay start. |
| **Observed input** | Archived KORD METAR | Only when the replay clock reaches `observed_at_local`. |
| **Observed outcome in agent state** | Actual ORD event time and endpoint delay when an ORD timestamp exists | Only when the replay clock reaches that actual event time. Eventual cancellation and diversion fields are removed from this record. |
| **Evaluator-only observed outcome** | Cancellation, diversion, an event after local midnight, and other unresolved archive labels | Used only in the sealed end-of-day settlement after the 24-hour replay. These rows do not enter agent state. |
| **Derived evidence** | Weather hazard score, specialist finding, and handoff summary | Computed from the time-gated graph view and labeled as derived. |
| **Model forecast** | Next-hour cancellation rate, disruption rate, mean delay, and delay band | Saved before any later outcome is evaluated. |

Cancellation and diversion records do not provide a reliable announcement timestamp in BTS. Releasing those labels earlier would leak the answer. An operated movement can also occur after the local-day replay ends. GroundGraph therefore keeps these cases out of agent state and lets `GroundTruthEvaluator` perform a retrospective **aggregate** settlement at 24:00. The evaluator never creates individual graph outcomes for those sealed rows, never returns their raw archive records, and there is no later forecast for the settlement to influence.

Missing ORD endpoint delay remains unavailable. It is not converted to zero and does not enter mean-delay error. A diversion still counts as a disruption even when no ORD endpoint delay exists.

## One hourly episode

At each hour the runtime excludes any movement whose outcome is already known, then targets unresolved movements scheduled in the next 60 minutes. Six specialists concurrently execute fixed, allowlisted Cypher reads against the isolated FalkorDB graph:

| Specialist | Allowlisted graph read |
|---|---|
| Weather | `WEATHER_AT_WATERMARK` |
| Demand | `NEXT_HOUR_DEMAND` |
| Arrival | `NEXT_HOUR_ARRIVALS` |
| Departure | `NEXT_HOUR_DEPARTURES` |
| Disruption | `MATURED_DISRUPTIONS` |
| Outcome memory | `FORECAST_MEMORY` |

Each read becomes a persisted `AgentRun → Finding → Handoff` chain with its query name, input hash, timestamps, and evidence IDs. The coordinator then re-reads the six persisted handoffs from FalkorDB. It fails the episode if all six are not present, making the graph the coordination boundary rather than an interface-only visualization.

The server constructs and persists four transparent aggregate forecasts:

1. `SCHEDULE_BASELINE` assumes no material delay or cancellation beyond a small disruption baseline.
2. `RECENT_FLOW` carries forward only outcomes that actually matured during the trailing hour.
3. `WEATHER_STRESS` adds pressure from the latest time-gated METAR hazard score.
4. `HISTORICAL_ANALOG` applies a frozen model trained on June to 20 time-gated features covering demand, weather, recently completed flow, and overdue backlog.

The June-trained historical analogue is the numerical default because it produced the lowest error in the 30-day benchmark. Local `qwen3:4b-instruct` receives that single preselected, allowlisted candidate with the time-gated context and six handoff summaries. It verifies the exact supplied ID and gives a short explanation; it cannot change the numbers, invent a forecast, or propose an operational action. Unknown, malformed, or unavailable model output leaves the same historical-analogue forecast selected and records a labeled explanation fallback. `ModelRun` stores the one-candidate model allowlist, all four comparison IDs, and the decision source before linking to the saved `Forecast`.

During the day, a forecast can be compared only with timestamp-revealed movement records whose actual event time has passed, with final status fields still removed. At 24:00 the evaluator settles remaining saved forecasts from the sealed archive. Complete windows receive cancellation-rate, disruption-rate, mean-delay, and delay-band accuracy metrics. Hours with no unresolved target movements are marked `SKIPPED` and excluded from accuracy averages.

## What the score proves

The result measures how closely a saved forecast matched the recorded BTS outcome for the same hourly cohort. The headline delay metric is the **unweighted mean across complete hourly windows of the absolute error in each cohort's mean ORD endpoint delay**. It is not flight-level MAE. The result also reports cancellation-rate error, disruption-rate error, delay-band accuracy, the number of scored targets, and the number of records that actually contain an endpoint delay.

The comparison baseline is deliberately simple: it assumes zero mean delay, zero cancellations, and a 5% disruption rate from the published schedule. It is scored on the same hourly targets and recorded outcomes, but it does not consume weather or recent-flow observations. Beating this baseline shows that the time-gated forecasts improve on a no-disruption schedule assumption; it is not evidence against a competitive forecasting system.

July 27 is a retrospectively selected severe-weather stress day. By itself, it validates the data boundary, graph coordination, model-selection path, and scoring pipeline; it is not a generalization result and does not isolate the local model's incremental lift.

The checked official-server replay settles 21 scoreable hourly windows covering 1,996 target movements. It persists 24 forecast episodes and 144 specialist handoffs. The result agrees with the batch evaluator:

| July 27 metric | Historical analogue | Schedule-only baseline |
|---|---:|---:|
| Hourly cohort-mean delay MAE | 76.96 min | 162.53 min |
| Cancellation-rate MAE | 26.01 pp | 36.27 pp |
| Disruption-rate MAE | 10.42 pp | 62.74 pp |
| Delay-band accuracy | 61.90% | 19.05% |

The analogue cuts delay MAE by 52.65% against that simple baseline, but a 76.96-minute error is still large. This is the benchmark's hardest July date, so the selected-day result demonstrates graceful operation under an extreme case rather than solved severe-day forecasting.

The accompanying [multi-day benchmark](multiday-benchmark.md) addresses day-selection bias by scoring every reconstructable July 2–31 date. It contains 30 days, 638 hourly windows, and 54,774 target movements. Its June-trained historical-analogue candidate records 19.07-minute delay MAE, 3.96-percentage-point cancellation MAE, and 9.42-percentage-point disruption MAE, compared with 34.92-minute delay MAE for the schedule-only baseline. The larger result remains a one-airport, adjacent-month retrospective evaluation rather than a preregistered external study.

It does **not** establish that an agent action caused an improvement. The archive contains only the outcome that occurred; it does not contain the counterfactual outcome under an action that was never taken. This mode reports no saved-minutes or historical intervention claim.

## Graph lifecycle

The shadow test uses a dedicated FalkorDB graph, separate from the ORD, ATL, and SFO recovery scenario graphs. Loading a `HistoricalBacktestRuntime` clears that dedicated graph and rebuilds it from the checked-in pack. Selecting the same pack with `reset: true` closes the current runtime and constructs a fresh one.

This is intentional reset-on-load behavior. The factual replay is reproducible from its files, but its in-progress clock and forecast graph are **not claimed to survive an application restart**.

## API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/backtests` | List available factual packs and the active pack. |
| `GET` | `/api/backtests/health` | Validate the active backtest identity, source hash, clock state, and required FalkorDB records. |
| `POST` | `/api/backtests/select` | Load a pack by `backtest_id`; optional `reset: true` rebuilds its isolated graph. |
| `POST` | `/api/backtests/tick` | Advance with `minutes`, `hours`, or an absolute `to_minute`. |
| `GET` | `/api/backtests/results` | Return paired forecasts and evaluations plus cumulative accuracy scores. |

The snapshot exposes `latest_episode` for the newest saved forecast and `display_episode` for a forecast paired with truth from that exact episode. Its truth flags are literal: `agent_future_outcomes_visible` stays `false`; `raw_archive_rows_visible` stays `false`; and `archive_aggregate_visible` becomes `true` only at 24:00, when the evaluator uses the sealed archive to score saved forecasts. The aggregate does not expose the underlying sealed movement rows to agents.
