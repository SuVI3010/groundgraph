# Judge demo script

Target length: **4 minutes**. Record one continuous run from the final commit and narrate only values returned by that run.

## Before recording

1. Run `make start`, `make verify`, `make demo-check`, and `make audit`.
2. Confirm `make verify` reports the matching GroundGraph API, graph and endpoint, a nonempty FalkorDB server graph with its graph module, and the installed `qwen3:4b-instruct` model. Confirm `make demo-check` also reports the official `falkordb:6.0.0` image and a live structured Qwen inference.
3. Confirm `/api/health` reports `runtime_mode: server`, six concurrent specialists, graph counts, and local Qwen enabled. Confirm the interface autonomy toggle matches the server value in `/api/snapshot`.
4. Clear rehearsal memory once so the recording starts with an empty episodic-memory view:

   ```bash
   curl -fsS -X POST http://127.0.0.1:5050/api/reset \
     -H 'Content-Type: application/json' \
     -d '{"preserve_memory":false}' >/dev/null
   ```

   Confirm the fresh snapshot is at cycle 0 with no current plan, no handoffs, and an episode count of 0. This prevents an older persisted plan without the current shortlist fields from appearing in the recording.

5. Reload GroundGraph at 1440×900, pause at 08:00 CST, and keep FalkorDB Browser available in a second tab.
6. Begin with bounded auto-apply disabled from the interface so the first proposal remains available for inspection. Keep the `Observed`, `Derived`, `Modeled`, and `Mixed` legend visible.
7. During a rehearsal jump to 08:36, confirm the plan decision source is `local_model`. Clear memory again before the real recording. If the run reports `deterministic_fallback`, repair the local-model path before recording rather than presenting it as a Qwen decision.

The submission recording must use the full FalkorDB server through Podman. Do not record the development fallback.

## 0:00–0:30 — Establish the product and evidence boundary

Show the top-down ORD replay, six-flight slice, runtime badges, FAA-sourced runway identities, and provenance legend.

> GroundGraph is a graph-native airport-operations recovery command center. This run uses the full local FalkorDB server and local Qwen. It is a bounded research replay, not a live ATC system: selected schedule, weather, and runway fields come from public sources, while gates, disruptions, policies, and recovery effects are explicitly modeled.

Point to the healthy FalkorDB and Local Qwen indicators and the provenance legend. Explain that the Evidence view will reveal stable record IDs and field boundaries once the graph has an active causal path.

## 0:30–1:00 — A disruption triggers coordinated agents

Choose `Jump to disruption` to reach 08:36. The modeled baggage event should enter FalkorDB and trigger replanning without a manual Analyze action.

> This modeled baggage-reconciliation event adds 12 minutes to the stored turnaround task graph for the UA1414-to-UA2672 rotation. Six specialists now execute separate domain Cypher reads against one graph-state version before the coordinator can act.

Open `Cycles` and select the event cycle. Show all six handoff cards: six unique handoff IDs, distinct query names, evidence IDs, the same input version, overlapping start/end timestamps, and `CONSUMED` status. Record those six IDs for the reset proof. This is stronger evidence than six labels changing color.

## 1:00–1:50 — Track 1 and local-Qwen proof

Open `Plan` and the causal path. Show the event, affected flight, downstream rotation, resource, actions, and current policy evidence.

> GroundGraph builds three different strategies from the connected state: runway flow balance, compatible gate swap, and turnaround-task recovery. Their projected recovery and operational burden come from the graph. Cost score is lower-is-better. The server first computes the full safe non-dominated ranking. It then creates the model shortlist from candidates within four cost points of the leader for a turnaround event class, or eight for any other event class. Local Qwen can select only one supplied ID from that shortlist, and the server rejects invented IDs.

Point to the **LOCAL QWEN SELECTED** decision-source label, which is rendered from `selected_by: local_model`. Then show:

- the full `safe_candidate_set` and the smaller or equal `model_candidate_set`;
- `ModelRun.allowlisted_candidate_ids` matching the model shortlist exactly;
- the selected strategy and concrete actions;
- the server-written cost, recovered-minutes, and burden explanation;
- all seven replay checks passing;
- the graph precondition evidence for each action; and
- the persisted path relationships: `RECOVERED_BY`, then `USES_RESOURCE`, `REORDERS`, or `PROTECTS`, followed by `AUTHORIZED_BY` and `SUPPORTED_BY`.

Open `Evidence` and show one causal entity's stable source-record ID, its `SOURCED_FROM` or `MODELED_BY` relationship, and its observed/modeled field lists.

> Qwen chooses only among the context-bounded shortlist; it never executes an action. This path was queried only after the selected actions and their exact relationships existed in FalkorDB. The server still rechecks graph version, required relationships, current policy, current knowledge, freeze, projected benefit, and churn before applying the modeled change.

Turn the interface autonomy switch on. State that it calls the server endpoint, then show `APPLIED IN REPLAY` after fresh validation. Turn it off again and explain that subsequent proposals will remain pending for manual application.

## 1:50–2:25 — Track 2 coordination and episodic memory

Open `Memory` and note the new episode count and one memory ID or strategy. Then return to `Cycles` and show the same six unique specialist handoff IDs attached to the applied decision episode through their `AgentRun → Finding → Handoff` chains.

> Application writes a modeled Outcome and a context-rich Memory while preserving the whole Decision Episode to Plan to Outcome lineage. That applied episode also preserves the six unique Agent Run to Finding to Handoff chains consumed by that coordinator episode. Memory records event class, flights, rotation, resources, policies, and a disruption signature. It can adjust lower-is-better cost only for a matching graph context.

## 2:25–2:55 — Prove memory survives a new replay session

Choose `Restart replay`, pause at 08:00, and reopen `Memory`. The prior episode count and memory record must remain visible. Reopen the applied episode in `Cycles` and show that the same six handoff IDs, their producer runs and findings, and their consumed links are restored. Reload the page once if time permits; the same FalkorDB records should still appear.

Choose `Jump to disruption` again and show the new plan's memory-influence line.

> Restarting reseeds the operational replay but restores the applied episode, its same six specialist Agent Run to Finding to Handoff chains, selected plan, actions and evidence links, outcome, and memory in FalkorDB. This new matching cycle states exactly whether memory adjusted cost or changed the selected strategy. An unrelated weather cycle does not reuse this baggage memory.

This reset-and-recall sequence is the required persistence proof for an applied episode; do not describe no-action cycles as preserved. A memory created and consumed only within one uninterrupted cycle is insufficient.

## 2:55–3:35 — Track 3 Operations Brain proof

In `Ask Operations Brain`, choose `Who owns gate recovery?` and show the ownership-and-authority answer, evidence IDs, graph path, source excerpt, and current version. Then ask `Explain this incident` to show that the same interface routes to a different incident traversal. Ask an unsupported question if time allows and show `INSUFFICIENT_EVIDENCE`.

> Operations Brain answers from bounded graph traversals rather than one generic response. It routes ownership, incident, current-policy, and supersession questions to different evidence paths and declines unsupported questions.

Choose `Publish update`. Show the old and new gate-policy versions, graph-state increment, and policy-triggered cycle. Use `View cycle` to show the replan using the replacement version.

> Publishing a replacement policy changes graph state and triggers replanning without an application-code change. Any pending proposal based on the prior version becomes stale, while ownership, source evidence, and supersession history stay queryable.

## 3:35–3:50 — Show continuous replanning and restraint

Advance to the 09:04 observed weather update and open `Cycles`. Show the event-triggered cycle returning `NO_ACTION_REQUIRED` or `NO_CHANGE` when no material recovery is available.

> Continuous replanning does not mean constant intervention. GroundGraph records the cycle and its evidence, then declines to churn the applied plan when the graph shows no material benefit.

## 3:50–4:00 — Close on the three proofs

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
- Say `cost_score` is lower-is-better and distinguish projected recovery from operational burden.
- Describe memory as context-matched; do not imply that every prior outcome affects every later decision.
- Describe all six handoffs as consumed by the coordinator episode; do not claim that Qwen cites each handoff.
- Scope reset persistence to an applied episode; do not claim that every no-action cycle is restored.
- Describe the causal path as persisted FalkorDB relationships, not as a server-composed narrative.

If judges ask about MCP, show `mcp_server.py`: it exposes six allowlisted stdio tools: `inspect_disruption`, `get_agent_handoffs`, `get_coordinator_trace`, `recall_episode`, `get_current_policy`, and `validate_candidate`. Plan execution remains behind the application validator.
