# Three-track coverage

GroundGraph enters all three official tracks as one product. The airport selector exposes separate ORD, ATL, and SFO graphs; the same recovery loop and one episode-scoped Decision Story produce each track's proof.

## Track 1 — Agents That Act on Connected Data

**Behavior:** An active event reaches its affected flight, runway, turnaround task, and downstream rotation through FalkorDB. The planner constructs three materially different two-action bundles: flow balance, compatible gate swap, and turnaround-task recovery. Projected recovery minutes and burden come from the connected graph state, and `cost_score` is explicitly lower-is-better. The full safe non-dominated ranking is stored in `safe_candidate_set`. Safe candidates within 18 cost points of its leader for a `TURNAROUND` event class, or within 8 for other event classes, form `model_candidate_set` and the matching `ModelRun.allowlisted_candidate_ids`. Local Qwen may select only from that shortlist. A policy-authorized application writes `Plan`, `Action`, and modeled `Outcome` nodes after seven deterministic checks pass.

**Judge-visible proof:** In the default ORD replay, advance through `EVT-BAG-0836`. The Decision Story explains the trigger, why the connected departure is at risk now, six specialist findings, up to three safe alternatives, Qwen's allowlisted choice, and every application guard. Follow the visible causal path: affected flights use `RECOVERED_BY`, while actions use `USES_RESOURCE`, `REORDERS`, `PROTECTS`, `AUTHORIZED_BY`, and `SUPPORTED_BY`. Then see bounded autopilot revalidate and update the graph-backed replay. Switch to ATL or SFO to show that the same agent framework reads a different scenario graph rather than a hard-coded O'Hare screen.

**Stored proof:** `cypher/01_connected_impact_path.cypher` and `cypher/03_decision_and_authority.cypher`. The coordinator persists its disruption inspection, policy lookup, contextual recall, and candidate validation as `ToolCall` nodes linked to the active `DecisionEpisode`. `mcp_server.py` separately exposes six allowlisted stdio tools without plan-execution authority: `inspect_disruption`, `get_agent_handoffs`, `get_coordinator_trace`, `recall_episode`, `get_current_policy`, and `validate_candidate`.

**Failure proof:** Stop FalkorDB, remove current policy evidence, or delete a required `USES`, `COMPATIBLE_WITH`, `MUST_COMPLETE_BEFORE`, or `ROTATES_TO` edge. The dependent plan cannot pass graph validation from a side cache.

## Track 2 — Agent Memory and Coordination

**Behavior:** Each cycle fixes one shared graph version, then Weather, Flow, Gate, Turnaround, Policy, and Memory specialists execute six domain-specific Cypher reads concurrently. Across each persisted `AgentRun → Finding → Handoff` chain, the records capture the domain query, evidence IDs, start/end timestamps, duration, input version, and whether the handoff was consumed. The Decision Story resolves those six handoffs by exact `episode_id`, so an older cycle cannot inherit the latest plan or outcome. Applying a plan writes the complete `DecisionEpisode → Plan → Outcome ← Memory` lineage and preserves the six unique specialist chains consumed by that `DecisionEpisode`. A later outcome can adjust `cost_score` only when event class and connected flights, rotations, or resources match inside the same scenario graph.

**Judge-visible proof:** Open one decision and read the six graph-persisted findings as a single trigger-to-outcome sequence. The technical proof retains unique handoff IDs, overlapping timestamps, distinct query names, evidence IDs, one input version, and consumed status. Apply one plan, restart the replay, and show its episode, plan, actions, outcome, and memory remain connected alongside the same six handoff IDs and their producer-run and finding chains. Trigger the same disruption again and show whether the context-matched memory adjusted or changed the new ranking. This persistence proof is for the applied episode; it does not claim that every no-action cycle is restored.

**Stored proof:** `cypher/02_agent_handoffs.cypher`, `cypher/04_memory_influence.cypher`, and `cypher/05_replanning_history.cypher`. The handoff query anchors on `DecisionEpisode-[:CONSUMED_HANDOFF]→Handoff` and returns episode, run, finding, evidence, version, timing, duration, and consumption identifiers. The corresponding MCP tools accept the same exact `episode_id`; cycle-only compatibility calls resolve a single newest episode.

**Failure proof:** Submit an old plan after a new event increments `Scenario.state_version`; apply rejects it as stale. Trigger a weather-only context after creating baggage memory; the unrelated memory stays visible but does not influence ranking.

## Track 3 — Company Brain

**Behavior:** Current and superseded policies connect to procedures, source documents, teams, responsible roles, incident evidence, and shift handoffs. The visible Operations Brain routes each question to a bounded ownership, incident, current-policy, or supersession traversal, returns evidence IDs and citations, and reports `INSUFFICIENT_EVIDENCE` for unsupported questions. Every proposed action links to a current policy whose imported knowledge is `CURRENT_FOR_REPLAY`; apply rechecks both. A policy update increments graph state and triggers immediate replanning.

**Judge-visible proof:** Ask “Who owns gate recovery?”, “Explain this incident”, and “Which procedure is current?”; show the different answer types, evidence paths, and source citations. Then publish a policy update and show the graph version advance while a policy-triggered cycle uses the replacement version. If a proposal is pending, show it become stale.

**Stored proof:** `cypher/06_company_brain.cypher`, `cypher/07_apply_guard.cypher`, and the synthetic source pack in `knowledge/`.

**Failure proof:** Ask a question outside the bounded company graph and receive `INSUFFICIENT_EVIDENCE`. Replace a policy with an empty action envelope; the affected action loses authorization and no fully authorized candidate may be applied.

## FalkorDB centrality

The judged runtime uses the full official FalkorDB server through Podman and stores operations, record-level provenance, agent coordination, tool calls, decisions, policy, and memory in FalkorDB. The default graphs are `groundgraph`, `groundgraph_atl`, and `groundgraph_sfo`; only the selected airport's graph drives its Decision Story and controls. On macOS the bootstrap uses Podman AppleHV when it must create a machine. `/api/health` exposes `runtime_mode: server` and graph counts. The FalkorDBLite command is a non-judged development fallback and is not used in submission evidence.

## Claim boundary

GroundGraph demonstrates system behavior across three packaged six-flight, four-hour replays. Public schedule, weather, and runway fields establish historical demand and conditions. Gates, rotations as decision-time evidence, non-weather operating disruptions, capacity effects, policies, recovery actions, and outcomes are modeled. The product does not report measured airport savings, validate real-world operating performance, issue ATC clearances, or represent its synthetic policies as airport, airline, or FAA procedures.

