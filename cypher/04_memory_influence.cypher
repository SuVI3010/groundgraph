// Track 2: applied plans create outcomes, and the coordinator stores memory of them.
MATCH (plan:Plan)-[:RESULTED_IN]->(outcome:Outcome)<-[:REMEMBERS]-(memory:Memory)
MATCH (coordinator:Agent {id: 'AGENT-COORD'})-[:STORED]->(memory)
OPTIONAL MATCH (later:DecisionEpisode)-[:RECALLED]->(memory)
RETURN memory.id AS memory,
       memory.strategy AS learned_strategy,
       memory.disruption_signature AS context,
       outcome.status AS outcome_status,
       outcome.benefit_minutes AS modeled_benefit_minutes,
       collect(DISTINCT later.id) AS recalled_by_later_episodes
ORDER BY memory.created_at DESC;

