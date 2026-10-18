# Submission copy

## Project name

GroundGraph

## Tagline

Continuous airport recovery powered by communicating agents and a shared FalkorDB context graph.

## Tracks

- Agents That Act on Connected Data
- Agent Memory and Coordination
- Company Brain

## Short description

GroundGraph coordinates six Cypher specialists through FalkorDB to explain and safely replan airport disruptions across ORD, ATL, and SFO. A separate leakage-resistant O'Hare benchmark covers 30 days, 638 hourly forecasts, and 54,774 movements; its June-trained historical analogue cuts mean-delay error from 34.92 to 19.07 minutes against a schedule-only baseline.

## Medium description

GroundGraph is an explainable airport-recovery command center built around FalkorDB. Six concurrent specialists read weather, flow, gates, turnarounds, policy, and memory from one versioned graph, persist their findings and handoffs, and let a bounded local Qwen coordinator choose only among graph-safe recovery plans. Every action remains traceable through its event, flights, resources, policy, procedure, and outcome. Three historical-demand replays demonstrate acting, coordination and memory, and a source-backed Company Brain. A separate O'Hare shadow benchmark evaluates every reconstructable July 2–31 day using information available at each hourly cutoff: 30 days, 638 forecasts, and 54,774 movements. The frozen June-trained analogue achieves 19.07-minute delay MAE versus 34.92 minutes for the schedule-only baseline. The project clearly labels observed, derived, modeled, and retrospective data and makes no live-control or saved-minutes claim.

## Full description

Airport disruption recovery is a connected-data problem: weather changes runway capacity, ground tasks delay rotations, gates constrain arrivals, and one choice can affect later flights. The information is usually split between operational state, team handoffs, policy documents, and previous decisions.

GroundGraph puts those relationships in FalkorDB and turns them into one transparent recovery loop. The airport selector switches among three independent four-hour, six-flight graphs: an ORD winter morning, an ATL thunderstorm afternoon, and an SFO wind-and-rain afternoon. Six specialists execute separate weather, flow, gate, turnaround, policy, and memory Cypher reads concurrently against one input version. They persist query names, timestamps, evidence IDs, findings, and handoffs before the coordinator begins.

The planner builds three materially different two-action strategies. Their projected recovery minutes and operational burden come from connected runway, gate, turnaround, and delay state; lower `cost_score` is better. After ineligible and strictly dominated choices are removed, the full ranking is stored as `safe_candidate_set`. A context-bounded `model_candidate_set` keeps safe candidates within 18 cost points of its leader for a `TURNAROUND` event class or within 8 points for other event classes. Those same IDs become `ModelRun.allowlisted_candidate_ids`, and local Qwen may select only one supplied shortlist ID. Every application rechecks seven conditions: current graph version, current policy authority, current replay knowledge, required targets and graph relationships, execution freeze, minimum projected benefit, and churn. Each applied outcome becomes context-rich episodic memory that survives replay reset and application restart with its full decision lineage and the same six unique `AgentRun → Finding → Handoff` chains consumed by its `DecisionEpisode`, then adjusts later rankings only when event class and connected entities match.

The episode-scoped Decision Story makes the loop understandable without reading a graph console. It shows what changed, which connected departure is at risk and why now, what each specialist found, which safe alternatives Qwen compared, why one was selected, whether all seven guards passed, what projected result was written, and when the graph will scan again. Selecting an older decision resolves all of those stages from the same `DecisionEpisode`.

A separate **Real-day test** measures the agent framework against public ground truth. It replays ORD on July 27, 2026 from 00:00 to 24:00 using 2,164 BTS reporting-carrier domestic segments and outcomes plus 53 archived KORD METAR observations. Each hour, six specialists read the time-gated FalkorDB graph and the coordinator re-reads their persisted handoffs. Four transparent numerical forecasts are saved for comparison. The frozen June-trained historical analogue supplies the prediction; local Qwen receives only that preselected ID to verify and explain, so it cannot change the numbers. Operated outcomes appear only at their recorded actual event time; cancellations, diversions, and post-midnight outcomes remain sealed from agents and are settled by the evaluator only after the final forecast.

The companion chronological evaluation scores every reconstructable ORD day from July 2–31: 30 days, 638 hourly windows, and 54,774 target movements across 10 normal, 12 elevated, and 8 severe days. The historical analogue records 19.07-minute hourly cohort-mean delay MAE, 3.96-percentage-point cancellation MAE, 9.42-percentage-point disruption MAE, and 65.83% delay-band accuracy. The schedule-only baseline records 34.92-minute delay MAE and 30.68-percentage-point disruption MAE. June supplies numerical fitting and validation; July labels never enter fitting or neighbor selection. The feature family was designed after inspecting the July 27 showcase, so this is an adjacent-month retrospective evaluation rather than a pristine preregistered holdout. Neither result is a historical intervention or saved-minutes claim.

The Operations Brain interface routes plain-language questions to ownership, incident, current-policy, or supersession traversals. It returns graph paths, evidence IDs, and source citations, or says when the bounded graph has insufficient evidence. Updating a policy changes graph state and triggers replanning without changing application code.

The interface presents a top-down airport replay, the linear Decision Story, the causal impact path, policy evidence, Operations Brain answers, continuous replanning history, and projected counterfactual outcomes. The causal path contains only relationships already persisted in FalkorDB. Public BTS schedule fields, time-gated NOAA observations, and FAA runway facts are separated from modeled gates, operating events, policy, and recovery effects. Record-level `SOURCED_FROM` and `MODELED_BY` links expose stable row IDs and field boundaries. The default ORD scenario includes its exact selected public rows with pinned hashes and a reproducible extractor. ATL and SFO include source URLs, source-file digests, record keys, and field-level boundaries in their scenario packs; selected raw extracts for those two airports are not checked into the ORD evidence pack.

GroundGraph is a research replay. It does not control aircraft or airport systems, and every result is described as projected under the replay assumptions.

## Why FalkorDB

FalkorDB is the primary application state, not a visualization added after planning. The replay reads flights, resources, events, policy, and prior memory from the graph. Specialists communicate through graph-persisted findings and handoffs. The coordinator writes episodes, plans, actions, outcomes, and memory back to the same graph. If FalkorDB is unavailable, every graph-backed API fails closed with `503 GRAPH_UNAVAILABLE`; the browser freezes and becomes read-only. Its health pulse reloads genuine graph state before controls return after recovery.

## Technical highlights

- Full official FalkorDB server running locally through Podman as the judged runtime; a newly created macOS Podman machine uses AppleHV.
- Flask and a dependency-free browser interface.
- Separate ORD, ATL, and SFO scenario graphs selected through one general recovery interface.
- Episode-scoped Decision Story joining trigger, why-now timing, six findings, candidate comparison, model choice, seven guards, outcome, and next checkpoint.
- Local `qwen3:4b-instruct` through Ollama, selecting only from the context-bounded `model_candidate_set`, with a labeled deterministic fallback.
- Six allowlisted stdio MCP tools: disruption inspection, handoff retrieval, coordinator-trace retrieval, recall, policy lookup, and candidate validation.
- Six real concurrent specialist Cypher reads with query names, overlap timestamps, evidence IDs, versions, and consumed handoffs.
- Reproducible 24-hour ORD factual shadow test with separated schedule, time-gated weather, sealed outcomes, baseline scoring, and no causal-impact claim.
- Reproducible 30-day ORD benchmark covering 638 hourly forecasts and 54,774 target movements, with normal, elevated, and severe-day results.
- Three materially different action bundles with graph-derived benefit/burden and lower-is-better `cost_score`.
- Persisted causal paths using `RECOVERED_BY`, `USES_RESOURCE`, `REORDERS`, `PROTECTS`, and authority relationships.
- Context-matched episodic memory demonstrated across a replay reset with the applied episode's full decision lineage and same six consumed specialist chains restored.
- Five-minute and event-driven replanning with churn and stale-state guards.
- Policy-authorized bounded autopilot and supervised mode controlled by the server-side autonomy endpoint.
- Question-routed Operations Brain answers for ownership, incidents, current policy, and supersession history.
- Minimal ORD BTS, NOAA, and FAA source extracts with pinned hashes and a reproducible extractor; ATL/SFO scenario packs retain source identifiers and explicit observed/modeled boundaries.
- Versioned synthetic operations knowledge pack with sources and ownership.

## Links to add before submission

- Repository: [github.com/SuVI3010/groundgraph](https://github.com/SuVI3010/groundgraph)
- Demo video: [groundgraph-demo.mp4](https://github.com/SuVI3010/groundgraph/blob/main/demo/groundgraph-demo.mp4)
- Demo evidence manifest: [groundgraph-demo.manifest.json](https://github.com/SuVI3010/groundgraph/blob/main/demo/groundgraph-demo.manifest.json)
- Live demo or local instructions: [docs/local-setup.md](https://github.com/SuVI3010/groundgraph/blob/main/docs/local-setup.md)

## How did you use FalkorDB?

FalkorDB is GroundGraph's operational state and coordination layer. Flights, weather events, gates, runways, turnarounds, policies, procedure versions, source records, agent runs, findings, handoffs, model runs, plans, actions, outcomes, and memories are connected in the graph. Six specialists run separate allowlisted Cypher traversals against the same state version and persist their evidence before the coordinator acts. Candidate eligibility, causal paths, current policy authority, contextual memory, and the seven execution guards all depend on stored relationships. The factual replay uses an isolated FalkorDB graph at every hourly watermark and records each forecast and agent handoff before outcomes are revealed. If FalkorDB is unavailable, graph-backed APIs fail closed and the interface becomes read-only.

## What did you like about FalkorDB?

FalkorDB let us use the same connected model for operational reasoning, multi-agent coordination, provenance, policy lookup, and memory instead of building separate stores for each concern. Cypher made multi-hop evidence paths reviewable, while fast local reads and writes supported six concurrent specialists and persisted handoffs during an interactive replay. Running the official server locally also let the complete judged path work without sending airport data or model prompts to an external service.

## What improvements would you like to see in FalkorDB?

We would value more first-party examples for transactional multi-agent handoff patterns, clearer observability for concurrent query timelines, and an officially documented lightweight local packaging path alongside the full server. Schema and constraint introspection in the browser, richer query-profile visualizations, and easier export of a small reproducible graph snapshot would also make debugging and judging graph-heavy agent systems faster.
