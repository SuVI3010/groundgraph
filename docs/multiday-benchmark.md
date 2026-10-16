# ORD multi-day factual benchmark

GroundGraph's interactive July 27 replay demonstrates the graph-backed decision path. The multi-day benchmark answers the broader forecasting question: does the same time-gated logic work across ordinary and disrupted days, and does a numerical model trained on an earlier month improve it?

The checked-in result covers **every reconstructable ORD local day from July 2 through July 31, 2026**: 30 days, 638 scored hourly windows, and 54,774 target movements. No July day was added or removed based on model performance. July 1 is excluded because a July-only BTS archive does not contain June 30 rows needed to reconstruct every overnight arrival.

The machine-readable source of record is [`benchmark.json`](../analysis/ord-july-2026-benchmark/benchmark.json). Its companion [`per-day-metrics.csv`](../analysis/ord-july-2026-benchmark/per-day-metrics.csv) and [`hourly-results.csv`](../analysis/ord-july-2026-benchmark/hourly-results.csv) expose the same results at day and forecast-window resolution.

## Cohort and split

Each forecast is issued on the hour for unresolved movements scheduled during the next 60 minutes. A flight that has already operated early is excluded because its result is already known at the watermark. Every remaining target counts in the cancellation and disruption rates. A target is disrupted when it was ultimately cancelled, diverted, or recorded an ORD endpoint delay of at least 15 minutes. Mean delay uses the non-cancelled targets that have an ORD endpoint-delay value; a window without any such value is not scored. Cancellation and diversion labels remain sealed until retrospective settlement because BTS does not publish a reliable time when those decisions became known.

The benchmark uses a chronological split:

| Stage | Dates | Purpose |
|---|---|---|
| Calibration | June 2–21, 2026 | Fit feature scaling and compare candidate neighbor counts. |
| Validation | June 22–30, 2026 | Select `k` separately for delay, cancellation, and disruption. |
| Refit | June 2–30, 2026 | Refit the frozen historical-analogue model on 612 hourly episodes. |
| Evaluation | July 2–31, 2026 | Score 638 later hourly windows; July labels do not enter parameter fitting, scaling, validation loss, or `k` selection. |

Validation selected 5 neighbors for mean delay, 80 for cancellation rate, and 10 for disruption rate. The historical-analogue model uses standardized inverse-distance nearest neighbors over 20 time-gated features: hour and weekday, target demand and arrival share, current METAR fields, recent completed-flow statistics, overdue-backlog age buckets, and the completion-to-demand ratio. The complete fitted artifact, including pack hashes, scaling values, validation losses, and the June reference episodes, is stored in [`ord-hourly-historical-analog-june-2026.json`](../data/models/ord-hourly-historical-analog-june-2026.json).

This is a chronological out-of-month retrospective evaluation at one airport, not a pristine never-observed holdout or a preregistered external study. The feature and model family were designed after inspecting the July 27 showcase day, although July labels were not used for the numerical fitting and selection steps listed above. June and July are adjacent summer months, and the result does not establish performance at another airport, season, or year.

## Sources and reproducibility

The schedule and realized outcomes come from the U.S. Bureau of Transportation Statistics Reporting Carrier On-Time Performance archive. The cohort covers reporting-carrier U.S. domestic nonstop segments, not every ORD movement. The published final schedule is used as the schedule input; it is not a contemporaneous schedule snapshot.

Weather comes from archived KORD METAR observations distributed by the Iowa Environmental Mesonet. [`weather-source-validation.json`](../analysis/ord-july-2026-benchmark/weather-source-validation.json) independently compares that month-wide input with NOAA's KORD GHCN-hourly FM15/FM16 archive. Each day pack records its source URLs, field allowlists, row counts, and SHA-256 hashes; `benchmark.json` records the hashes of every evaluation pack and the fitted model records the hashes of every June pack.

The batch evaluator is deliberately graph-free so it can score a month quickly. It freezes the same time-gated inputs and candidate formulas used by the graph-backed replay. FalkorDB persistence, six-agent handoffs, model-run lineage, and fail-closed behavior are validated separately by the interactive runtime and its integration tests.

## Result

Lower is better for the error columns. Delay MAE is the unweighted mean of absolute errors between predicted and actual hourly cohort means; the weighted column weights each window by its target count. Cancellation and disruption errors are absolute percentage-point errors. Macro-F1 gives each observed delay band equal weight.

| Forecast method | Delay MAE | Movement-weighted delay MAE | Cancellation MAE | Disruption MAE | Delay-band macro-F1 |
|---|---:|---:|---:|---:|---:|
| Schedule-only baseline | 34.92 min | 34.68 min | 4.73 pp | 30.68 pp | 15.85% |
| Recent completed flow | 24.26 min | 19.69 min | 4.73 pp | 14.59 pp | 51.53% |
| Weather stress | 24.04 min | 19.03 min | 4.82 pp | 13.52 pp | 53.21% |
| Deterministic selector | 22.59 min | 19.39 min | 4.76 pp | 13.27 pp | 53.93% |
| **June-trained historical analogue** | **19.07 min** | **16.09 min** | **3.96 pp** | **9.42 pp** | **56.94%** |

The historical analogue has the lowest reported error on every continuous target and the strongest delay-band macro-F1. Against the deliberately simple no-disruption schedule baseline, its delay MAE is 45.39% lower and its disruption MAE is 69.30% lower. Its day-bootstrap 95% intervals are:

| Metric | Point estimate | 95% interval |
|---|---:|---:|
| Hourly cohort-mean delay MAE | 19.07 min | 13.08–26.62 min |
| Cancellation-rate MAE | 3.96 pp | 2.25–6.20 pp |
| Disruption-rate MAE | 9.42 pp | 7.80–11.16 pp |
| Delay-band accuracy | 65.83% | 59.69–71.29% |

The model still has a negative delay bias of 8.13 minutes and a negative cancellation-rate bias of 2.44 percentage points, so it continues to underestimate bad conditions on average. Its delay RMSE is 44.20 minutes, materially above its MAE because a smaller set of disruption windows still produces large misses.

## Normal, elevated, and severe days

The strata are analysis labels based only on settled full-day outcomes. They are never provided as model inputs:

- **Normal:** disruption below 22% and cancellation below 1%.
- **Severe:** disruption at or above 35%.
- **Elevated:** every remaining day.

The July cohort contains 10 normal, 12 elevated, and 8 severe days.

| Day class | Scored windows | Target movements | Historical-analogue delay MAE | Cancellation MAE | Disruption MAE | Delay-band macro-F1 |
|---|---:|---:|---:|---:|---:|---:|
| Normal | 211 | 17,519 | 8.64 min | 0.97 pp | 5.88 pp | 44.61% |
| Elevated | 256 | 21,992 | 12.87 min | 2.12 pp | 8.40 pp | 35.07% |
| Severe | 171 | 15,263 | 41.21 min | 10.39 pp | 15.33 pp | 54.41% |

Severe days remain the main weakness. Their delay MAE is almost five times the normal-day value, and cancellation error rises above 10 percentage points. The benchmark therefore supports the historical analogue as the better numerical candidate while showing that it does not solve disruption forecasting.

Representative settled days illustrate the range:

| Date | Class | Scheduled movements | Cancellation rate | Disruption rate | Mean ORD endpoint delay |
|---|---|---:|---:|---:|---:|
| July 8 | Normal | 2,140 | 0.23% | 17.24% | 3.24 min |
| July 24 | Normal | 2,163 | 0.05% | 17.98% | 5.15 min |
| July 2 | Elevated | 2,137 | 3.04% | 24.57% | 16.57 min |
| July 3 | Severe | 2,134 | 25.02% | 65.60% | 90.91 min |
| July 18 | Severe | 2,030 | 8.82% | 58.57% | 54.96 min |
| July 27 | Severe | 2,164 | 39.93% | 70.70% | 117.20 min |

July 27, the interactive showcase day, is the hardest date in this July cohort by cancellation rate, disruption rate, and operated endpoint mean delay. Its single-day score should therefore be read as a stress test, while the 30-day table is the broader forecast result.

## Why the original candidates miss disruptions

The multi-day result confirms the failure mechanism seen on July 27:

1. **Completed-flight censoring.** `RECENT_FLOW` sees only operated flights whose actual event has already occurred. At disruption onset, the most delayed flights have not completed and disappear from the recent sample. When they finally complete, persistence can then overstate the recovery period.
2. **No time-stamped cancellation state.** BTS lacks cancellation-announcement timestamps, so agent-visible recent cancellation is deliberately zero. `WEATHER_STRESS` can raise cancellation risk only from the current point METAR, even though cancellations can persist long after local weather improves.
3. **A single weather snapshot is too small a state.** The hand-set weather formula uses the latest local METAR hazard. It has no TAF, radar, FAA traffic-management program, origin weather, or network propagation state.
4. **Demand was counted, not modeled.** The original formulas use next-hour demand to predict counts but do not let demand density, arrival share, or accumulated overdue flights change the predicted rates.
5. **Mixed endpoint targets.** One aggregate combines ORD departure delay for outbound flights and ORD arrival delay for inbound flights, although their dynamics differ.

The historical analogue improves those formulas mainly by adding overdue-backlog age buckets, recent completion throughput, demand composition, time context, and earlier June analogues. The remaining severe-day gap points to missing operational state rather than a need for more fluent text generation.

## Local-model audit

The coordinator's language model chooses among server-generated, allowlisted candidates; it does not generate their numerical values. Model quality must therefore be judged by valid structured output, agreement with an explicit candidate policy, selection regret, latency, and repeatability. Changing the language model cannot repair information absent from every candidate.

[`qwen-selector-audit.json`](../analysis/ord-july-2026-benchmark/qwen-selector-audit.json) records a stratified 54-window audit of the original three transparent candidates. The 30 July days were ranked by settled full-day disruption and divided into low, middle, and high tertiles; ranks 1, 5, and 10 in each tertile were sampled at 06:00, 09:00, 12:00, 15:00, 18:00, and 21:00. Outcome labels were used only to make the audit cover the range of conditions. Neither those labels nor the day class appeared in a model prompt.

| Selector | Delay MAE | Cancellation MAE | Disruption MAE | Delay-band accuracy |
|---|---:|---:|---:|---:|
| Qwen selection / always-weather tie | 25.037 min | 6.788 pp | 8.751 pp | 59.259% |
| Deterministic rule | 25.457 min | 6.640 pp | 8.944 pp | 57.407% |
| Metric-specific candidate oracle | 19.335 min | 5.453 pp | 7.746 pp | 74.074% |

Local `qwen3:4b-instruct` returned valid allowlisted JSON in all 54 calls. It named `WEATHER_STRESS` 26 times and `RECENT_FLOW` 28 times, but every selected numerical vector equaled `WEATHER_STRESS` because those 28 rows were candidate ties. Observed latency on an Apple M4 Pro with 24 GiB averaged 3.035 seconds, with a 2.890-second median, 4.336-second nearest-rank 95th percentile, and 5.005-second maximum under a 30-second per-call limit.

The oracle picks a separate hindsight winner for each metric, so it is an upper bound rather than one deployable selector. The gap shows candidate headroom; it does not measure attainable language-model lift. `gemma4:26b-mlx` produced no score on the same host: the severe-window selector prompt exceeded 300 seconds and even a minimal strict-JSON request exceeded 120 seconds. That rules it out for this local interactive path without proving anything about its forecast quality.

The audit supports keeping Qwen for bounded, fast structured coordination and explanation. It does not support swapping in a larger local model for better numbers. GroundGraph instead makes the June-trained historical analogue its numerical forecast candidate, leaving the language model to verify and explain an allowlisted result.

## Delay causes and baggage limits

Across the 30-day evaluation archive, BTS provides retrospective whole-flight delay-cause fields on 16,717 rows. For an outbound ORD movement, these post-event categories do not necessarily identify an ORD-endpoint cause. The recorded cause minutes are:

| BTS category | Minutes |
|---|---:|
| Late aircraft | 606,394 |
| Carrier controlled | 447,175 |
| National Airspace System | 383,000 |
| Weather | 148,772 |
| Security | 455 |

Cancellation codes contain 209 carrier, 2,240 weather, and 373 National Airspace System cancellations.

BTS has **no baggage-specific flight-level delay field**. `CarrierDelay` may include baggage loading alongside crew, cleaning, fueling, or maintenance, so it cannot be relabeled or measured as baggage delay. Public BTS mishandled-baggage reporting is aggregated by carrier and month, without the flight, airport, or event timestamp needed to join it to this replay. The delay-cause fields are post-event labels and never enter the forecasting features. BTS also lacks flight-level gate, crew, and turnaround-task events. GroundGraph will keep baggage behavior modeled in its recovery demonstration until a time-stamped airport, airline, AIDX, or baggage-handling feed is available.

## Leakage limits

The benchmark preserves the runtime's main information boundary:

- Schedule identity and scheduled time are available before forecasting.
- METAR is available only when its observation time is at or before the watermark.
- Any ORD movement with an actual timestamp becomes recent-flow evidence only when that timestamp has passed; an eventual cancellation or diversion flag remains withheld.
- Cancellation, diversion, post-midnight, delay-cause, baggage, tail, taxi, and other realized labels never become predictor inputs.
- July labels are absent from parameter fitting, validation loss, scaling, and neighbor selection; feature and model-family design followed inspection of July 27.
- Pack and model hashes make the exact inputs auditable.

There are still material limits. METAR valid time is treated as availability time, without dissemination lag. The BTS schedule is the final reported schedule rather than a historical snapshot. Cancellation and diversion announcements cannot be replayed when they actually occurred. The batch benchmark validates forecast logic without exercising FalkorDB on every monthly window. These constraints are disclosed rather than filled with synthetic facts.

## Recommendation

Use the June-trained historical analogue as the numerical forecast candidate and keep the local language model as a bounded coordinator and explanation layer. The 30-day result supports that change: the analogue improves overall delay, cancellation, disruption, and balanced delay-band performance, while the local-model selector cannot recover information missing from its candidates.

The next forecasting work should add time-gated TAF or radar, FAA traffic-management state, origin-network conditions, and separate arrival/departure models. A second untouched month or airport should then be evaluated with the model and thresholds frozen. Counterfactual operational benefit must remain a separate simulator question; this benchmark measures forecast accuracy only and makes no saved-minutes or causal claim.
