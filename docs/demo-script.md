# Judge demo script

Target length: **4 minutes**. Record one continuous run from the final commit and narrate only values returned by that run.

## Before recording

1. Run `make start`, `make verify`, and `make audit`.
2. Confirm `make verify` reports all three of these: GroundGraph API, a nonempty FalkorDB server graph, and the installed `qwen3:4b-instruct` model.
3. Confirm `/api/health` reports `runtime_mode: server`, six concurrent specialists, graph counts, and local Qwen enabled.
4. Clear rehearsal memory once so the recording starts with an empty episodic-memory view:

   ```bash
   curl -fsS -X POST http://127.0.0.1:5050/api/reset \
     -H 'Content-Type: application/json' \
     -d '{"preserve_memory":false}' >/dev/null
   ```

5. Reload GroundGraph at 1440×900, pause at 08:00 CST, and keep FalkorDB Browser available in a second tab.
6. Keep bounded auto-apply enabled and the `Observed`, `Derived`, `Modeled`, and `Mixed` legend visible.
7. During a rehearsal jump to 08:36, confirm the plan decision source is `local_model`. Clear memory again before the real recording. If the run reports `deterministic_fallback`, repair the local-model path before recording rather than presenting it as a Qwen decision.

The submission recording must use the full FalkorDB server through Podman. Do not record the development fallback.

## 0:00–0:25 — Establish the product and evidence boundary

Show the top-down ORD replay, six-flight slice, runtime badges, FAA-sourced runway identities, and provenance legend.

> GroundGraph is a graph-native airport-operations recovery command center. This run uses the full local FalkorDB server and local Qwen. It is a bounded research replay, not a live ATC system: selected schedule, weather, and runway fields come from public sources, while gates, disruptions, policies, and recovery effects are explicitly modeled.

Point to the healthy FalkorDB and Local Qwen indicators. This establishes the judged runtime before any result appears.

## 0:25–0:50 — A disruption triggers coordinated agents

Choose `Jump to disruption` to reach 08:36. The modeled baggage event should enter FalkorDB and trigger replanning without a manual Analyze action.

> This modeled baggage-reconciliation event adds 12 minutes to the UA1414-to-UA2672 rotation. Six specialists read one graph-state version in parallel, then persist their findings and handoffs to FalkorDB for the coordinator.

Show the six specialist rows and the handoff ticker. Open `Cycles` briefly to show event-driven and five-minute cycles rather than a one-shot workflow.

## 0:50–1:35 — Track 1 and local-Qwen proof

Open `Plan` and the causal path. Show the event, affected flight, downstream rotation, resource, actions, and current policy evidence.

> FalkorDB supplies the multi-hop impact path and the current authority for every action. Deterministic code constructs the replay-eligible candidates. Local Qwen selects exactly one supplied candidate ID, and the server rejects invented IDs.

Point to the **LOCAL QWEN SELECTED** decision-source label, which is rendered from `selected_by: local_model`. Then show:

- the selected strategy and concrete actions;
- all seven replay checks passing;
- `APPLIED IN REPLAY` after policy-authorized bounded auto-apply; and
- the applied plan remaining visible after the cycle completes.

> Qwen chooses among bounded candidates; it never executes an action. The server rechecks graph version, target existence, current policy, current knowledge, freeze, projected benefit, and churn before applying the modeled change.

## 1:35–2:05 — Track 2 coordination and episodic memory

Open `Memory` and note the new episode count and one memory ID or strategy. Then return to `Cycles` and show the specialist handoffs attached to the decision cycle.

> Application writes a modeled Outcome and derived Memory to FalkorDB. The next coordinator cycle can recall that episode and apply a visible score adjustment, while every producing agent, handoff, graph version, and decision episode remains queryable.

## 2:05–2:40 — Prove memory survives a new replay session

Choose `Restart replay`, pause at 08:00, and reopen `Memory`. The prior episode count and memory record must remain visible. Reload the page once if time permits; the same FalkorDB memory should still appear.

Choose `Jump to disruption` again and show the new plan's memory-influence line.

> Restarting reseeds the operational replay but preserves episodic memory in FalkorDB. This new decision cycle retrieves the earlier outcome and states exactly whether it adjusted the score or changed the selected strategy.

This reset-and-recall sequence is the required persistence proof; a memory created and consumed only within one uninterrupted cycle is insufficient.

## 2:40–3:20 — Track 3 Company Brain proof

Open `Evidence` for an action and show its current synthetic procedure version, named owner, responsible role, source section or excerpt, and freshness status.

> Each action is authorized through the company-knowledge graph: action to policy, procedure version, source document, owner team, and responsible role. Apply checks that this knowledge is current for the replay.

Choose `Publish update`. Show the old and new gate-policy versions, graph-state increment, and policy-triggered cycle. Use `View cycle` to show the replan using the replacement version.

> Publishing a replacement policy changes graph state and triggers replanning without an application-code change. Any pending proposal based on the prior version becomes stale, while ownership, source evidence, and supersession history stay queryable.

## 3:20–3:45 — Show continuous replanning and restraint

Advance to the 09:04 observed weather update and open `Cycles`. Show the event-triggered cycle returning `NO_ACTION_REQUIRED` or `NO_CHANGE` when no material recovery is available.

> Continuous replanning does not mean constant intervention. GroundGraph records the cycle and its evidence, then declines to churn the applied plan when the graph shows no material benefit.

## 3:45–4:00 — Close on the three proofs

Show the three-track proof strip and projected comparison.

> FalkorDB determines what the agents can see and connect, how they coordinate and remember, and which current company knowledge authorizes an action. GroundGraph turns that shared graph into an explainable recovery loop with local-model selection and bounded autonomy.

## Narration rules

- Say **airport operations recovery**, not certified air-traffic control.
- Say **projected under the replay assumptions**, not actual minutes saved.
- Call the baggage and runway events modeled stress tests.
- Attribute only the published schedule, weather, and runway fields to public sources; operational effects remain modeled.
- Describe a Qwen decision only when the plan reports `selected_by: local_model`.
- Describe the full official FalkorDB server only when health reports `runtime_mode: server`.
- Call the policy, incident, and shift-handoff records synthetic company knowledge.

If judges ask about MCP, show `mcp_server.py`: it exposes allowlisted tools for disruption inspection, handoffs, memory, policy lookup, and candidate validation. Plan execution remains behind the application validator.
