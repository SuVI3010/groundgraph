# Knowledge-pack graph contract

`manifest.json` is the machine-readable source for the Company Brain demonstration. Every record is synthetic and carries a `MODELED` truth label.

The runtime imports the pack into these graph areas:

```text
(Organization)-[:HAS_TEAM]->(Team)-[:HAS_ROLE]->(Role)
(Organization)-[:PUBLISHED]->(SourceDocument|Incident|ShiftHandoff)
(ProcedureVersion)-[:IN_DOCUMENT]->(SourceDocument)
(ProcedureVersion)-[:OWNED_BY]->(Team)
(ProcedureVersion)-[:RESPONSIBLE_ROLE]->(Role)
(Policy)-[:ALIGNED_WITH]->(ProcedureVersion)
(Policy)-[:IN_DOCUMENT]->(SourceDocument)
(Policy)-[:OWNED_BY]->(Team)
(Policy {status: "CURRENT"})-[:SUPERSEDES]->(Policy {status: "SUPERSEDED"})
(Incident)-[:OWNED_BY]->(Team)
(Incident)-[:REFERENCES]->(OperationalEvent)
(Incident)-[:AFFECTS]->(Flight)
(ShiftHandoff)-[:REFERENCES]->(Incident)
(ShiftHandoff)-[:OWNED_BY]->(Team)
(ShiftHandoff)-[:RESPONSIBLE_ROLE]->(Role)
```

Minimum Track 3 behavior:

1. A proposed action reaches a current `Policy` through `AUTHORIZED_BY`.
2. The policy reaches its document, owner team, responsible role, effective dates, and exact source section.
3. Company Brain answers expose document effective dates, `fresh_until`, and imported policy freshness; candidate validation and apply both require `CURRENT_FOR_REPLAY` policy knowledge.
4. Superseding the current policy increments graph state, makes any prior proposal stale, and triggers replanning without changing application code.

The static Markdown files are human-readable evidence; `manifest.json` is the import source of truth. All contents are synthetic demonstration material, not O'Hare, airline, FAA, or ATC procedures.
