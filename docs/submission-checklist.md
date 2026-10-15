# Submission checklist

## Repository and setup

- [ ] Public repository URL is added to the README and submission form.
- [x] Event-period repository is separate from the pre-event exploration prototype.
- [x] Python dependency versions and judged server/model tags are recorded.
- [x] `make audit` checks package completeness, provenance hashes, action contracts, and local documentation links offline.
- [x] Official FalkorDB server start path is Podman-only; it does not use Docker Desktop or Colima.
- [x] FalkorDBLite fallback is labeled separately.
- [x] Clean-clone instructions are documented.
- [ ] Clean-clone setup is repeated on a second account or machine.

## Required technical evidence

- [x] Graph model is documented.
- [x] Essential Cypher queries are included in `cypher/`.
- [x] Public, derived, and modeled data are separated in a provenance manifest.
- [ ] Mixed flight, runway, and weather records show field-level provenance clearly in the recorded demo.
- [x] All three tracks are declared with visible and stored proofs.
- [x] Optional MCP tools are documented and covered by a bounded-surface test.
- [x] AI coding assistance and the product model are disclosed.
- [ ] Disconnecting FalkorDB makes the server-mode API and mutations visibly unavailable; no browser simulation continues.
- [ ] `/api/health` is captured with `runtime_mode: server`.
- [x] Automated tests pass on the final release candidate.
- [x] Fresh official-server run persists memory across application restart.
- [x] Policy supersession visibly advances graph state and replans with the new version; a supervised run also shows pending-plan invalidation.

## Demo assets

- [x] Final 2:43 video is recorded from the release candidate.
- [x] Audio is intelligible at normal volume.
- [x] The first 30 seconds show the problem, product, and provenance boundary.
- [x] The recording includes one model/fallback badge with truthful narration.
- [x] Track 1 causal path and policy-authorized applied graph action are visible.
- [x] Track 2 handoffs, later memory retrieval, and stale-proposal invalidation are visible.
- [x] Track 3 source/owner/version evidence and a policy mutation are visible.
- [ ] Video link works in a signed-out browser.
- [x] Screenshot/thumbnail uses the final interface.

## Submission form

- [ ] Product name: `GroundGraph`.
- [ ] Tracks: `Agents That Act on Connected Data`, `Agent Memory and Coordination`, `Company Brain`.
- [ ] Repository URL: `<PUBLIC_REPOSITORY_URL>`.
- [x] Demo asset: [`demo/groundgraph-demo.mp4`](../demo/groundgraph-demo.mp4); replace with its public repository URL after publishing.
- [ ] Live URL or complete local setup path: `docs/local-setup.md`.
- [ ] Team members and roles are correct.
- [ ] Every team member is registered on only this team and can explain the graph model, key Cypher, architecture, model boundary, and data provenance.
- [ ] Team ownership of event-period code and assets is agreed.
- [ ] All external libraries, public data, and AI tools are disclosed.
- [ ] Final form is submitted before the official deadline and confirmation is saved.

## Side-prize asset

- [ ] A public technical blog post explains the connected-data problem, graph model, replanning loop, and evidence boundaries.
- [ ] The blog uses screenshots from the final commit and links to the public repository and demo.
- [ ] The blog URL is entered wherever the organizer accepts Best Blog Post entries.

