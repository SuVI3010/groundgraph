// Every periodic, event-driven, manual, and policy-triggered replan remains auditable.
MATCH (episode:DecisionEpisode)-[:ANALYZED]->(scenario:Scenario)
OPTIONAL MATCH (episode)-[:SELECTED]->(plan:Plan)
RETURN episode.cycle AS cycle,
       episode.created_at AS created_at,
       episode.trigger AS trigger,
       episode.status AS decision_status,
       episode.based_on_version AS graph_version,
       plan.id AS plan,
       plan.strategy AS strategy,
       plan.status AS plan_status
ORDER BY cycle DESC;

