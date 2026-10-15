// Track 2: prove the exact specialist handoffs consumed by each decision episode.
MATCH (episode:DecisionEpisode)-[:CONSUMED_HANDOFF]->(handoff:Handoff)
MATCH (agent:Agent)-[:STARTED]->(run:AgentRun)-[:PRODUCED]->
      (finding:Finding)-[:PACKAGED_AS]->(handoff)-[:DELIVERED_TO]->
      (coordinator:Agent {id: 'AGENT-COORD'})
RETURN episode.id AS episode_id,
       episode.cycle AS cycle,
       handoff.id AS handoff_id,
       run.id AS agent_run_id,
       finding.id AS finding_id,
       agent.id AS specialist,
       handoff.query_name AS query_name,
       finding.severity AS severity,
       finding.summary AS finding,
       handoff.evidence_ids AS evidence_ids,
       handoff.status AS handoff_status,
       handoff.state_version AS based_on_version,
       handoff.started_at AS started_at,
       handoff.ended_at AS ended_at,
       handoff.duration_ms AS duration_ms,
       handoff.consumed_at AS consumed_at,
       coordinator.name AS recipient
ORDER BY episode.created_at DESC, handoff.id;

