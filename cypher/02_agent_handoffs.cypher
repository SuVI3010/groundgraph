// Track 2: the coordinator receives graph-persisted findings from specialists.
MATCH (agent:Agent)-[:STARTED]->(run:AgentRun)-[:PRODUCED]->
      (finding:Finding)-[:PACKAGED_AS]->(handoff:Handoff)-[:DELIVERED_TO]->
      (coordinator:Agent {id: 'AGENT-COORD'})
RETURN run.cycle AS cycle,
       agent.id AS specialist,
       finding.severity AS severity,
       finding.summary AS finding,
       handoff.status AS handoff_status,
       handoff.state_version AS based_on_version,
       coordinator.name AS recipient
ORDER BY cycle DESC, specialist;

