// Tracks 1 and 3: selected actions retain their policy evidence and its current status.
MATCH (episode:DecisionEpisode)-[:SELECTED]->(plan:Plan)-[:PROPOSES]->
      (action:Action)-[:AUTHORIZED_BY]->(policy:Policy)
OPTIONAL MATCH (policy)-[:ALIGNED_WITH]->(procedure:ProcedureVersion)
OPTIONAL MATCH (procedure)-[:IN_DOCUMENT]->(document:SourceDocument)
OPTIONAL MATCH (procedure)-[:OWNED_BY]->(team:Team)
OPTIONAL MATCH (procedure)-[:RESPONSIBLE_ROLE]->(role:Role)
RETURN episode.cycle AS cycle,
       episode.trigger AS trigger,
       plan.id AS plan,
       plan.strategy AS strategy,
       action.id AS action,
       action.type AS action_type,
       action.target_id AS target,
       policy.id AS policy,
       policy.version AS policy_version,
       policy.owner AS policy_owner,
       policy.source AS source,
       policy.status AS policy_status,
       procedure.id AS procedure,
       document.id AS source_document,
       team.name AS owner_team,
       role.name AS responsible_role
ORDER BY cycle DESC, action;

