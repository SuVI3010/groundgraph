# GroundGraph Cypher catalog

These queries match the graph written by `engine.py`. Run them in FalkorDB Browser at [http://127.0.0.1:3000](http://127.0.0.1:3000) against graph `groundgraph`, or through any FalkorDB client.

| File | What it proves |
|---|---|
| `00_graph_inventory.cypher` | FalkorDB contains the operational, agent, memory, and policy graph. |
| `01_connected_impact_path.cypher` | An operational event reaches a flight, its next rotation, resources, selected actions, and policy evidence. |
| `02_agent_handoffs.cypher` | Specialist runs produce findings and graph-persisted coordinator handoffs. |
| `03_decision_and_authority.cypher` | A selected plan reaches concrete actions and their current authorizing policies. |
| `04_memory_influence.cypher` | Applied outcomes become episodic memory and later episodes can recall them. |
| `05_replanning_history.cypher` | Periodic, event-driven, and policy-driven cycles remain auditable. |
| `06_company_brain.cypher` | Current and superseded policy versions retain source and ownership context. |
| `07_apply_guard.cypher` | Counts whether every action in the current plan has a current policy path before apply. |

The application supplies parameterized versions of these traversals. The catalog uses concrete replay IDs so judges can paste each query directly into FalkorDB Browser.

The mutation used in the Company Brain demo is implemented by `GroundGraphEngine.supersede_policy()`: it marks the selected policy noncurrent, creates its replacement, links the versions with `SUPERSEDES`, increments `Scenario.state_version`, and triggers a new planning cycle. Use the application endpoint for that demonstration so the full stale-state and replan behavior runs together.

