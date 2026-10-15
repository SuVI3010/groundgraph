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

GroundGraph runs a modeled disruption inside a public-data O'Hare replay, coordinates six specialist agents through FalkorDB, lets local Qwen select from replay-eligible recovery plans, applies bounded state changes under synthetic policy, and remembers modeled outcomes across replay sessions.

## Full description

Airport disruption recovery is a connected-data problem: weather changes runway capacity, ground tasks delay rotations, gates constrain arrivals, and one choice can affect later flights. The information is usually split between operational state, team handoffs, policy documents, and previous decisions.

GroundGraph puts those relationships in FalkorDB and turns them into one transparent recovery loop. A bounded four-hour O'Hare replay continuously updates the graph. Six specialists watch weather, flow, gates, turnarounds, policy, and memory. They persist findings and handoffs for a coordinator, which asks local Qwen to select only from deterministic, policy-authorized candidates. Every application rechecks graph version, current policy, and replay constraints. Modeled applied outcomes become episodic memory that survives replay and application restarts while the FalkorDB volume is retained, then adjusts later rankings when relevant.

The Company Brain layer connects actions to current and superseded procedures, source excerpts, owner teams, responsible roles, a synthetic incident, and a shift handoff. Updating a policy changes graph state and triggers replanning without changing application code.

The interface presents a top-down airport replay, live agent status, the causal impact path, policy evidence, continuous replanning history, and projected counterfactual outcomes. Public BTS schedule fields, time-gated NOAA observations, and FAA runway facts are separated from modeled gates, operating events, policy, and recovery effects.

GroundGraph is a research replay. It does not control aircraft or airport systems, and every result is described as projected under the replay assumptions.

## Why FalkorDB

FalkorDB is the primary application state, not a visualization added after planning. The replay reads flights, resources, events, policy, and prior memory from the graph. Specialists communicate through graph-persisted findings and handoffs. The coordinator writes episodes, plans, actions, outcomes, and memory back to the same graph. If FalkorDB is unavailable, the core workflow is unavailable.

## Technical highlights

- Full official FalkorDB server running locally through Podman as the judged runtime.
- Flask and a dependency-free browser interface.
- Local `qwen3:4b-instruct` through Ollama with a labeled deterministic fallback.
- Five allowlisted stdio MCP tools for graph inspection, handoffs, recall, policy lookup, and candidate validation.
- Six concurrent specialist jobs plus one coordinator.
- Persistent episodic memory demonstrated across a replay reset and reused by a later decision cycle.
- Five-minute and event-driven replanning with churn and stale-state guards.
- Policy-authorized bounded autopilot and supervised mode.
- Public BTS, NOAA, and FAA inputs with an explicit provenance manifest.
- Versioned synthetic operations knowledge pack with sources and ownership.

## Links to add before submission

- Repository: `<PUBLIC_REPOSITORY_URL>`
- Demo video: [`demo/groundgraph-demo.mp4`](../demo/groundgraph-demo.mp4)
- Live demo or local instructions: `docs/local-setup.md`
