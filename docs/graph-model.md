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
(Flight)-[:ROTATES_TO]->(Flight)
(AircraftRotation)-[:INCLUDES]->(Flight)
(OperationalEvent)-[:AFFECTS]->(Flight)
(OperationalEvent)-[:CONSTRAINS]->(Runway|Gate)
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
(DecisionEpisode)-[:SELECTED]->(Plan)
(Plan)-[:PROPOSES]->(Action)
(Action)-[:AFFECTS]->(Flight|Runway|Gate)
```

Every `Handoff` stores its producer, recipient, cycle, graph-state version, evidence IDs, status, summary, and timestamp. The coordinator consumes these graph records after every specialist completes.

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
(Action)-[:AUTHORIZED_BY]->(Policy)

(Plan)-[:RESULTED_IN]->(Outcome)
(Memory)-[:REMEMBERS]->(Outcome)
(Agent {id: "AGENT-COORD"})-[:STORED]->(Memory)
(DecisionEpisode)-[:RECALLED]->(Memory)
```

Current policy nodes carry version, status, owner, source section, action types, rule text, effective date, and provenance class. `knowledge/manifest.json` supplies teams, roles, source documents, a synthetic incident, and a shift handoff; the importer connects those records to current procedures and runtime policies.

Provenance is field-specific when one node carries both sourced identity and modeled replay state. For example, a `Flight` node's identity, route, direction, and scheduled time come from BTS, while its gate, runway assignment, operational status, turn time, risk, delay, and recovery state are modeled. The exact boundary for each mixed record is documented in [Data provenance](data-provenance.md).

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
| `Memory` | `MEMORY-<cycle>-<timestamp>` |
| `Policy` | `PROC-GATE-1.3` |

## Centrality proofs

- Stop FalkorDB: `/api/health`, replay state, replanning, and memory all stop.
- Change an active event or flight delay in the graph: the next candidate scores and projected outcome change.
- Remove a current policy node: the matching action loses authorization and no complete plan can be applied.
- Apply a plan, restart the application, and query memory: the outcome and memory persist in the official server volume.

See [`../cypher/`](../cypher/) for copy-paste queries covering each traversal.

