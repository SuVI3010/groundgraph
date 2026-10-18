# Submission checklist

## Repository and setup

- [ ] Repository visibility is changed from private to public and verified while signed out.
- [x] Event-period repository is separate from the pre-event exploration prototype.
- [x] Python dependency versions and judged server/model tags are recorded.
- [x] `make audit` checks package completeness, scenario/knowledge hashes, action contracts, and local documentation links offline.
- [x] Official FalkorDB server start path is Podman-only; it does not use Docker Desktop or Colima.
- [x] `make demo-check` verifies the exact official image, graph module, matching API/graph/endpoint, and a live local-model inference.
- [x] FalkorDBLite fallback is labeled separately.
- [x] Clean-clone instructions are documented.
- [x] A tracked-files-only isolated snapshot completed setup, official-server seed/reset/analyze, six-agent coordination, local Qwen selection, `make verify`, and `make demo-check` on this host.
- [ ] Clean-clone setup is repeated on a second account or machine.

## Required technical evidence

- [x] Graph model is documented.
- [x] Essential Cypher queries are included in `cypher/`.
- [x] Public, derived, and modeled data are separated in a provenance manifest.
- [x] Exact selected BTS, NOAA, and FAA rows are checked in with source hashes and reproducible extraction instructions.
- [x] Flights, runways, gates, events, and turnarounds have stable record-level `SOURCED_FROM` or `MODELED_BY` graph paths.
- [x] Mixed flight, runway, and weather records retain field-level provenance in the graph and proof UI; the recorded demo keeps the observed/modeled boundary visible without opening every field list.
- [x] All three tracks are declared with visible and stored proofs.
- [x] All six optional MCP tools, including `get_coordinator_trace`, are documented and covered by a bounded-surface test.
- [x] AI coding assistance and the product model are disclosed.
- [x] Disconnecting FalkorDB returns a consistent `GRAPH_UNAVAILABLE` response, freezes the replay, disables controls, and visibly marks the browser read-only; the health pulse reconnects from graph state after restoration.
- [x] `/api/health` is captured with `runtime_mode: server` in the final demo manifest.
- [x] Automated tests pass on the final release candidate.
- [x] Fresh official-server run persists memory across application restart.
- [x] The full safe non-dominated ranking, context-bounded `model_candidate_set`, and matching `ModelRun.allowlisted_candidate_ids` are persisted and tested.
- [x] Applied-episode reset restores the full episode-plan-action-outcome-memory lineage, action evidence relationships, and the same six unique consumed `AgentRun → Finding → Handoff` chains.
- [x] Context matching prevents a baggage episode from influencing an unrelated weather decision.
- [x] Six specialist handoffs record domain query names, evidence IDs, the shared input version, start/end timestamps, duration, and consumed status.
- [x] The factual ORD shadow test uses a reproducible public data pack, excludes future and already-known target labels, performs evaluator-only end-of-day archive settlement, and compares saved forecasts with a declared baseline.
- [x] Causal-path API edges are persisted relationships with graph-precondition failure tests.
- [x] Server autonomy can be disabled for a pending supervised proposal and re-enabled for guarded auto-application.
- [x] Operations Brain routes ownership, incident, current-policy, supersession, and unsupported questions to distinct graph-backed responses.
- [x] Policy supersession visibly advances graph state and replans with the new version; a supervised run also shows pending-plan invalidation.

## Demo assets

- [x] Existing 2:40 fallback video was recorded from hardened source commit `3348d4d` with the simplified decision story and multi-airport replay.
- [x] Final founder-led video is refreshed from source commit `365c33f` and shows the real-day interface and 30-day benchmark.
- [x] Audio is intelligible at normal volume and media QA records its levels.
- [x] The first 30 seconds show the problem, product, and provenance boundary.
- [x] The recording includes one model/fallback badge with truthful narration.
- [x] Track 1 shows three safe tradeoffs, Qwen's bounded choice, seven execution guards, and a policy-authorized application; the manifest verifies the persisted allowlist and selected ID.
- [x] Track 2 shows six parallel specialist findings, a genuine no-action replan, contextual memory, and preservation of the applied ORD episode after switching airport graphs.
- [x] Track 3 shows a question-routed Operations Brain ownership answer with its connected procedure and evidence path.
- [x] The video uses supervised manual approval; guarded auto-application remains available in the UI and is covered by server tests.
- [ ] Final YouTube video is public or unlisted and works while signed out.
- [x] Screenshot/thumbnail is refreshed with the six-agent FalkorDB handoff proof.

## Submission form

- [x] Product name: `GroundGraph`.
- [x] Tracks: `Agents That Act on Connected Data`, `Agent Memory and Coordination`, `Company Brain`.
- [ ] Public repository URL: [github.com/SuVI3010/groundgraph](https://github.com/SuVI3010/groundgraph).
- [ ] Public or unlisted YouTube demo URL is added to the form.
- [x] Complete local setup path is documented in `docs/local-setup.md`.
- [ ] Team members and roles are correct.
- [ ] Every team member is registered on only this team and can explain the graph model, key Cypher, architecture, model boundary, and data provenance.
- [ ] Team ownership of event-period code and assets is agreed.
- [x] All external libraries, public data, and AI tools are disclosed.
- [ ] Final form is submitted before the official deadline and confirmation is saved.

## Side-prize asset

- [ ] A public technical blog post explains the connected-data problem, graph model, replanning loop, and evidence boundaries.
- [ ] The blog uses screenshots from the final commit and links to the public repository and demo.
- [ ] The blog URL is entered wherever the organizer accepts Best Blog Post entries.

