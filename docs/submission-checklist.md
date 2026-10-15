# Submission checklist

## Repository and setup

- [ ] Public repository URL is added to the README and submission form.
- [x] Event-period repository is separate from the pre-event exploration prototype.
- [x] Python dependency versions and judged server/model tags are recorded.
- [x] `make audit` checks package completeness, scenario/knowledge hashes, action contracts, and local documentation links offline.
- [x] Official FalkorDB server start path is Podman-only; it does not use Docker Desktop or Colima.
- [x] `make demo-check` verifies the exact official image, graph module, matching API/graph/endpoint, and a live local-model inference.
- [x] FalkorDBLite fallback is labeled separately.
- [x] Clean-clone instructions are documented.
- [ ] Clean-clone setup is repeated on a second account or machine.

## Required technical evidence

- [x] Graph model is documented.
- [x] Essential Cypher queries are included in `cypher/`.
- [x] Public, derived, and modeled data are separated in a provenance manifest.
- [x] Exact selected BTS, NOAA, and FAA rows are checked in with source hashes and reproducible extraction instructions.
- [x] Flights, runways, gates, events, and turnarounds have stable record-level `SOURCED_FROM` or `MODELED_BY` graph paths.
- [x] Mixed flight, runway, and weather records show field-level provenance clearly in the final recorded demo.
- [x] All three tracks are declared with visible and stored proofs.
- [x] All six optional MCP tools, including `get_coordinator_trace`, are documented and covered by a bounded-surface test.
- [x] AI coding assistance and the product model are disclosed.
- [ ] Disconnecting FalkorDB makes the server-mode API and mutations visibly unavailable; no browser simulation continues.
- [x] `/api/health` is captured with `runtime_mode: server` in the final demo manifest.
- [x] Automated tests pass on the final release candidate.
- [x] Fresh official-server run persists memory across application restart.
- [x] The full safe non-dominated ranking, context-bounded `model_candidate_set`, and matching `ModelRun.allowlisted_candidate_ids` are persisted and tested.
- [x] Applied-episode reset restores the full episode-plan-action-outcome-memory lineage, action evidence relationships, and the same six unique consumed `AgentRun → Finding → Handoff` chains.
- [x] Context matching prevents a baggage episode from influencing an unrelated weather decision.
- [x] Six specialist handoffs record domain query names, evidence IDs, the shared input version, start/end timestamps, duration, and consumed status.
- [x] Causal-path API edges are persisted relationships with graph-precondition failure tests.
- [x] Server autonomy can be disabled for a pending supervised proposal and re-enabled for guarded auto-application.
- [x] Operations Brain routes ownership, incident, current-policy, supersession, and unsupported questions to distinct graph-backed responses.
- [x] Policy supersession visibly advances graph state and replans with the new version; a supervised run also shows pending-plan invalidation.

## Demo assets

- [x] Final 3:49 video was recorded from release commit `8933812` after all graph-proof changes.
- [x] Audio is intelligible at normal volume and media QA records its levels.
- [x] The first 30 seconds show the problem, product, and provenance boundary.
- [x] The recording includes one model/fallback badge with truthful narration.
- [x] Track 1 shows the full safe ranking, event-class cost-window shortlist, matching model allowlist, distinct candidate tradeoffs, persisted causal relationships, and policy-authorized application.
- [x] Track 2 shows six uniquely identified timestamped Cypher handoffs, contextual recall, and the applied episode's same six specialist chains plus full decision lineage after reset.
- [x] Track 3 shows question-routed Operations Brain answers, source/owner/version evidence, and policy mutation.
- [x] The server-side autonomy toggle is visibly demonstrated in both supervised and guarded-auto modes.
- [ ] Video link works in a signed-out browser.
- [x] Screenshot/thumbnail uses the final interface.

## Submission form

- [x] Product name: `GroundGraph`.
- [x] Tracks: `Agents That Act on Connected Data`, `Agent Memory and Coordination`, `Company Brain`.
- [ ] Repository URL: `<PUBLIC_REPOSITORY_URL>`.
- [x] Demo asset: [`demo/groundgraph-demo.mp4`](../demo/groundgraph-demo.mp4); replace with its public repository URL after publishing.
- [x] Complete local setup path: `docs/local-setup.md`.
- [ ] Team members and roles are correct.
- [ ] Every team member is registered on only this team and can explain the graph model, key Cypher, architecture, model boundary, and data provenance.
- [ ] Team ownership of event-period code and assets is agreed.
- [x] All external libraries, public data, and AI tools are disclosed.
- [ ] Final form is submitted before the official deadline and confirmation is saved.

## Side-prize asset

- [ ] A public technical blog post explains the connected-data problem, graph model, replanning loop, and evidence boundaries.
- [ ] The blog uses screenshots from the final commit and links to the public repository and demo.
- [ ] The blog URL is entered wherever the organizer accepts Best Blog Post entries.

