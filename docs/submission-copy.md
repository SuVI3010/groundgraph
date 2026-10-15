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

GroundGraph runs modeled disruptions inside a public-data O'Hare replay, coordinates six concurrent Cypher specialists through FalkorDB, lets local Qwen select from a context-bounded shortlist of graph-derived recovery plans, applies bounded state changes under synthetic policy, and recalls modeled outcomes only for matching disruption contexts.

## Full description

Airport disruption recovery is a connected-data problem: weather changes runway capacity, ground tasks delay rotations, gates constrain arrivals, and one choice can affect later flights. The information is usually split between operational state, team handoffs, policy documents, and previous decisions.

GroundGraph puts those relationships in FalkorDB and turns them into one transparent recovery loop. A bounded four-hour O'Hare replay continuously updates the graph. Six specialists execute separate weather, flow, gate, turnaround, policy, and memory Cypher reads concurrently against one input version. They persist query names, timestamps, evidence IDs, findings, and handoffs before the coordinator begins.

The planner builds three materially different two-action strategies. Their projected recovery minutes and operational burden come from connected runway, gate, turnaround, and delay state; lower `cost_score` is better. After ineligible and strictly dominated choices are removed, the full ranking is stored as `safe_candidate_set`. A context-bounded `model_candidate_set` keeps candidates within 4 cost points of its leader for a `TURNAROUND` event class or within 8 points for other event classes. Those same IDs become `ModelRun.allowlisted_candidate_ids`, and local Qwen may select only one supplied shortlist ID. Every application rechecks graph version, required graph relationships, current policy, knowledge freshness, execution freeze, minimum benefit, and churn. Each applied outcome becomes context-rich episodic memory that survives replay reset and application restart with its full decision lineage and the same six unique `AgentRun → Finding → Handoff` chains consumed by its `DecisionEpisode`, then adjusts later rankings only when event class and connected entities match.

The Operations Brain interface routes plain-language questions to ownership, incident, current-policy, or supersession traversals. It returns graph paths, evidence IDs, and source citations, or says when the bounded graph has insufficient evidence. Updating a policy changes graph state and triggers replanning without changing application code.

The interface presents a top-down airport replay, live agent status, all six handoffs for a selected cycle, the causal impact path, policy evidence, Operations Brain answers, continuous replanning history, and projected counterfactual outcomes. The causal path contains only relationships already persisted in FalkorDB. Public BTS schedule fields, time-gated NOAA observations, and FAA runway facts are separated from modeled gates, operating events, policy, and recovery effects. Record-level `SOURCED_FROM` and `MODELED_BY` links expose stable row IDs and field boundaries, while the exact selected public rows are checked into the repository with pinned hashes and a reproducible extractor.

GroundGraph is a research replay. It does not control aircraft or airport systems, and every result is described as projected under the replay assumptions.

## Why FalkorDB

FalkorDB is the primary application state, not a visualization added after planning. The replay reads flights, resources, events, policy, and prior memory from the graph. Specialists communicate through graph-persisted findings and handoffs. The coordinator writes episodes, plans, actions, outcomes, and memory back to the same graph. If FalkorDB is unavailable, every graph-backed API fails closed with `503 GRAPH_UNAVAILABLE`; the browser freezes and becomes read-only. Its health pulse reloads genuine graph state before controls return after recovery.

## Technical highlights

- Full official FalkorDB server running locally through Podman as the judged runtime.
- Flask and a dependency-free browser interface.
- Local `qwen3:4b-instruct` through Ollama, selecting only from the context-bounded `model_candidate_set`, with a labeled deterministic fallback.
- Six allowlisted stdio MCP tools: disruption inspection, handoff retrieval, coordinator-trace retrieval, recall, policy lookup, and candidate validation.
- Six real concurrent specialist Cypher reads with query names, overlap timestamps, evidence IDs, versions, and consumed handoffs.
- Three materially different action bundles with graph-derived benefit/burden and lower-is-better `cost_score`.
- Persisted causal paths using `RECOVERED_BY`, `USES_RESOURCE`, `REORDERS`, `PROTECTS`, and authority relationships.
- Context-matched episodic memory demonstrated across a replay reset with the applied episode's full decision lineage and same six consumed specialist chains restored.
- Five-minute and event-driven replanning with churn and stale-state guards.
- Policy-authorized bounded autopilot and supervised mode controlled by the server-side autonomy endpoint.
- Question-routed Operations Brain answers for ownership, incidents, current policy, and supersession history.
- Minimal BTS, NOAA, and FAA source extracts with pinned hashes, a reproducible extractor, an explicit provenance manifest, and record-level graph links.
- Versioned synthetic operations knowledge pack with sources and ownership.

## Links to add before submission

- Repository: `<PUBLIC_REPOSITORY_URL>`
- Demo video: [`demo/groundgraph-demo.mp4`](../demo/groundgraph-demo.mp4)
- Demo evidence manifest: [`demo/groundgraph-demo.manifest.json`](../demo/groundgraph-demo.manifest.json)
- Live demo or local instructions: `docs/local-setup.md`
