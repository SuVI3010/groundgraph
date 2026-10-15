# Graph model

FalkorDB is GroundGraph's shared context layer, coordination bus, policy store, and episodic memory. The UI is a projection of graph state; the application does not maintain a second operational database.

## Operational context

```text
(Airport)-[:RUNS]->(Scenario)
(Airport)-[:OPERATES]->(Runway|Gate)
(Scenario)-[:TRACKS]->(Flight)
(Scenario)-[:HAS_EVENT]->(OperationalEvent)
(Scenario)-[:USES_SOURCE]->(Source)
(Flight)-[:USES]->(Runway|Gate)
(Flight)-[:COMPATIBLE_WITH]->(Gate)
(Flight)-[:ROTATES_TO]->(Flight)
(AircraftRotation)-[:INCLUDES]->(Flight)
(AircraftRotation)-[:HAS_TURNAROUND]->(Turnaround)
(Flight)-[:FEEDS]->(Turnaround)-[:PREPARES]->(Flight)
(Turnaround)-[:HAS_TASK]->(TurnaroundTask)
(TurnaroundTask)-[:MUST_COMPLETE_BEFORE]->(TurnaroundTask)
(OperationalEvent)-[:AFFECTS]->(Flight)
(OperationalEvent)-[:CONSTRAINS]->(Runway|Gate)
(OperationalEvent)-[:DELAYS]->(TurnaroundTask)

(Flight|Runway|Gate|OperationalEvent|Turnaround)
  -[:SOURCED_FROM|MODELED_BY]->(SourceRecord)-[:FROM_SOURCE]->(Source)
```

## Coordination and decisions

```text
(Agent)-[:MONITORS]->(Scenario)
(Agent)-[:STARTED]->(AgentRun)
(AgentRun)-[:PRODUCED]->(Finding)
(Finding)-[:PACKAGED_AS]->(Handoff)
(Handoff)-[:DELIVERED_TO]->(Agent {id: "AGENT-COORD"})

(DecisionEpisode)-[:ANALYZED]->(Scenario)
(DecisionEpisode)-[:USED_RUN]->(AgentRun)
(DecisionEpisode)-[:CONSUMED_HANDOFF]->(Handoff)
(DecisionEpisode)-[:USED_TOOL]->(ToolCall)
(DecisionEpisode)-[:SELECTED]->(Plan)
(Plan)-[:PROPOSES]->(Action)
(Flight)-[:RECOVERED_BY]->(Action)
(Action)-[:USES_RESOURCE]->(Runway|Gate)
(Action)-[:REORDERS]->(Turnaround)
(Action)-[:PROTECTS]->(Flight)
(Action)-[:AUTHORIZED_BY]->(Policy)
(Action)-[:SUPPORTED_BY]->(ProcedureVersion)
```

Every `Handoff` stores its producer, recipient, cycle, graph-state version, domain query name, evidence IDs, status, summary, start timestamp, end timestamp, and duration. All six specialists execute their own Cypher read against the same input version; the coordinator consumes the persisted handoffs after every specialist completes. Each bounded coordinator operation also becomes a `ToolCall` record with arguments, evidence IDs, result summary, and timestamp.

Candidate scoring is stored in the selected plan payload rather than presented as model intuition. `FLOW_BALANCE`, `GATE_SWAP`, and `TURNAROUND_RECOVERY` contain different action bundles. Their recovered minutes and operational burden come from active delays, runway constraints, compatible gate edges, or turnaround critical-path state. `cost_score = projected delay + burden + contextual memory adjustment`; lower is better. After removing ineligible and strictly dominated candidates, the complete ranking is stored as `safe_candidate_set`. The coordinator then stores a context-bounded `model_candidate_set`: candidates within 4 cost points of the safe-set leader for a `TURNAROUND` event class, or within 8 points for other event classes. Only those IDs are written to `ModelRun.allowlisted_candidate_ids` and exposed to local Qwen.

The causal-path API does not synthesize links in Python. It selects an event and the persisted actions, then returns only stored relationships. Deleting a required relationship changes validation or removes that hop from the next path query.

## Policy and memory

```text
(Organization)-[:HAS_TEAM]->(Team)-[:HAS_ROLE]->(Role)
(Organization)-[:PUBLISHED]->(SourceDocument|Incident|ShiftHandoff)
(ProcedureVersion)-[:IN_DOCUMENT]->(SourceDocument)
(ProcedureVersion)-[:OWNED_BY]->(Team)
(ProcedureVersion)-[:RESPONSIBLE_ROLE]->(Role)
(Policy)-[:ALIGNED_WITH]->(ProcedureVersion)
(Policy)-[:IN_DOCUMENT]->(SourceDocument)
(Policy)-[:OWNED_BY]->(Team)
(Airport)-[:GOVERNED_BY]->(Policy)
(Policy {current: true})-[:SUPERSEDES]->(Policy {current: false})

(DecisionEpisode)-[:SELECTED]->(Plan)
(Plan)-[:RESULTED_IN]->(Outcome)
(Memory)-[:REMEMBERS]->(Outcome)
(Agent {id: "AGENT-COORD"})-[:STORED]->(Memory)
(DecisionEpisode)-[:RECALLED]->(Memory)
```

Current policy nodes carry version, status, owner, source section, action types, rule text, effective date, and provenance class. `knowledge/manifest.json` supplies teams, roles, source documents, a synthetic incident, and a shift handoff; the importer connects those records to current procedures and runtime policies. Operations Brain routes a question to the relevant ownership, incident, current-policy, or `SUPERSEDES` traversal and returns graph evidence or `INSUFFICIENT_EVIDENCE`.

Memory is contextual rather than global. `DecisionEpisode`, `Plan`, `Outcome`, and `Memory` retain the event class, event IDs, affected flights, rotations, resources, policies, and disruption signature. A successful memory may change a later `cost_score` only when those graph dimensions match. For an applied episode, replay reset restores the full episode-plan-action-outcome-memory lineage, its action evidence relationships, and the same six unique `AgentRun → Finding → Handoff` chains consumed by that `DecisionEpisode`, instead of recreating orphan records. This persistence guarantee is scoped to applied episode lineages, not every no-action cycle.

Provenance is field-specific when one node carries both sourced identity and modeled replay state. Each replay entity links to a stable `SourceRecord` through `SOURCED_FROM` or `MODELED_BY`; the record stores its upstream row ID, truth label, `observed_fields`, and `modeled_fields`, then links to its source through `FROM_SOURCE`. For example, a `Flight` node's identity, route, direction, and scheduled time come from BTS, while its gate, runway assignment, operational status, turn time, risk, delay, and recovery state are modeled. The exact boundary for each mixed record is documented in [Data provenance](data-provenance.md).

## Stable identity examples

| Label | Example ID |
|---|---|
| `Scenario` | `ORD-2024-01-12-WINTER-AM` |
| `OperationalEvent` | `EVT-BAG-0836` |
| `Flight` | `UA1414-IN`, `UA2672-OUT` |
| `Agent` | `AGENT-WEATHER`, `AGENT-FLOW`, `AGENT-COORD` |
| `Handoff` | `HANDOFF-<cycle>-<specialist>-COORD` |
| `DecisionEpisode` | `EPISODE-<cycle>-<timestamp>` |
| `Plan` | `PLAN-<cycle>-<strategy>` |
| `ToolCall` | `TOOL-<cycle>-<sequence>-<nanosecond token>` |
| `Memory` | `MEMORY-<cycle>-<timestamp>` |
| `SourceRecord` | `SOURCE-RECORD-<stable source-row hash>` |
| `Policy` | `PROC-GATE-1.3` |

## Centrality proofs

- Stop FalkorDB: `/api/health`, replay state, replanning, and memory all stop.
- Change an active event, compatibility edge, runway constraint, or turnaround critical path: the candidate set, `cost_score`, and projected outcome change.
- Delete `USES`, `COMPATIBLE_WITH`, `MUST_COMPLETE_BEFORE`, or `ROTATES_TO`: the dependent action fails its graph precondition.
- Remove a current policy node: the matching action loses authorization and no complete plan can be applied.
- Apply a plan, reset the replay, and query its episode: the complete decision lineage, action evidence, and the same six consumed specialist chains persist in the official server volume.

See [`../cypher/`](../cypher/) for copy-paste queries covering each traversal.

