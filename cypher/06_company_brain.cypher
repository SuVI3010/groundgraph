// Track 3: inspect current knowledge, ownership, sources, and version history.
MATCH (airport:Airport {id: 'ORD'})-[:GOVERNED_BY]->(policy:Policy)
OPTIONAL MATCH (policy)-[:SUPERSEDES]->(previous:Policy)
OPTIONAL MATCH (policy)-[:ALIGNED_WITH]->(procedure:ProcedureVersion)
OPTIONAL MATCH (procedure)-[:IN_DOCUMENT]->(document:SourceDocument)
OPTIONAL MATCH (procedure)-[:OWNED_BY]->(team:Team)
OPTIONAL MATCH (procedure)-[:RESPONSIBLE_ROLE]->(role:Role)
RETURN policy.id AS policy,
       policy.title AS title,
       policy.version AS version,
       policy.status AS status,
       policy.current AS current,
       policy.owner AS owner,
       policy.source AS source,
       policy.rule AS rule,
       procedure.id AS procedure,
       procedure.source_section AS source_section,
       procedure.source_excerpt AS source_excerpt,
       document.id AS document,
       team.name AS owner_team,
       role.name AS responsible_role,
       previous.id AS superseded_policy,
       previous.version AS superseded_version
ORDER BY policy.title, policy.version DESC;

