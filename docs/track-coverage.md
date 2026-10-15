# Three-track coverage

GroundGraph enters all three official tracks as one product. The same continuous replay produces each proof.

## Track 1 — Agents That Act on Connected Data

**Behavior:** An active event reaches its affected flight, downstream rotation, and resources. Its decision episode reaches the same scenario, selected plan, proposed actions, and authorizing policies. The local model selects only from server-generated, replay-eligible candidates. A policy-authorized applied plan writes `Plan`, `Action`, and modeled `Outcome` nodes.

**Judge-visible proof:** Advance through `EVT-BAG-0836`, watch the causal path appear, inspect the selected plan and policy links, then see bounded autopilot revalidate and update the graph-backed replay.

**Stored proof:** `cypher/01_connected_impact_path.cypher` and `cypher/03_decision_and_authority.cypher`. `mcp_server.py` exposes five allowlisted graph tools, including a candidate validator, and its bounded tool surface is covered in `tests/test_engine_runtime.py`.

**Failure proof:** Stop FalkorDB or remove current policy evidence. The application cannot produce and apply the same plan from a side cache.

## Track 2 — Agent Memory and Coordination

**Behavior:** Each cycle reads one version of shared graph state, then Weather, Flow, Gate, Turnaround, Policy, and Memory specialist computations run concurrently. Their versioned `Finding` and `Handoff` records are persisted for the coordinator. Applying a plan writes a modeled `Outcome` and derived `Memory`; a later matching cycle can apply that simulated outcome as a score adjustment.

**Judge-visible proof:** Show six graph-persisted handoffs, apply one plan, and open the Memory view. Restart the replay and show the same memory remains; trigger the disruption again and show whether that prior outcome adjusted or changed the new ranking.

**Stored proof:** `cypher/02_agent_handoffs.cypher`, `cypher/04_memory_influence.cypher`, and `cypher/05_replanning_history.cypher`.

**Failure proof:** Submit an old plan after a new event increments `Scenario.state_version`; apply rejects it as stale.

## Track 3 — Company Brain

**Behavior:** Current and superseded policies connect to procedures, source documents, teams, responsible roles, incident evidence, and shift handoffs. Every proposed action links to a current policy whose imported knowledge is `CURRENT_FOR_REPLAY`; apply rechecks both. A policy update increments graph state and triggers immediate replanning.

**Judge-visible proof:** Open policy evidence for an action, show its current version, owner and source, choose `Publish update`, and show the graph version advance while a policy-triggered cycle uses the replacement version. If a proposal is pending, show it become stale.

**Stored proof:** `cypher/06_company_brain.cypher`, `cypher/07_apply_guard.cypher`, and the synthetic source pack in `knowledge/`.

**Failure proof:** Replace a policy with an empty action envelope. The affected action loses authorization and no fully authorized candidate may be applied.

## FalkorDB centrality

The judged runtime uses the full official FalkorDB server through Podman and stores operations, agent coordination, decisions, policy, and memory in one graph. `/api/health` exposes `runtime_mode: server` and graph counts. The FalkorDBLite command is a non-judged development fallback and is not used in submission evidence.

## Claim boundary

GroundGraph demonstrates system behavior inside a six-flight, four-hour replay. It does not report measured airport savings, validate real-world operating performance, issue ATC clearances, or represent its synthetic policies as O'Hare, airline, or FAA procedures.

