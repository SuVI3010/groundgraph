// Track 1: follow the modeled 08:36 event into the affected flight,
// downstream rotation, constrained/used resources, and resulting decision.
MATCH (scenario:Scenario {id: 'ORD-2024-01-12-WINTER-AM'})-[:HAS_EVENT]->
      (event:OperationalEvent {id: 'EVT-BAG-0836'})-[:AFFECTS]->(arrival:Flight)
OPTIONAL MATCH (arrival)-[:ROTATES_TO]->(next_leg:Flight)
OPTIONAL MATCH (event)-[:CONSTRAINS]->(constrained_resource)
OPTIONAL MATCH (arrival)-[:USES]->(arrival_resource)
OPTIONAL MATCH (next_leg)-[:USES]->(next_resource)
OPTIONAL MATCH (episode:DecisionEpisode {trigger: 'EVENT:EVT-BAG-0836'})-[:ANALYZED]->(scenario)
OPTIONAL MATCH (episode)-[:SELECTED]->(plan:Plan)-[:PROPOSES]->
               (action:Action)-[:AUTHORIZED_BY]->(policy:Policy)
RETURN scenario.id AS scenario,
       event.id AS event,
       event.summary AS disruption,
       arrival.id AS affected_flight,
       next_leg.id AS downstream_flight,
       collect(DISTINCT constrained_resource.id) AS constrained_resources,
       collect(DISTINCT arrival_resource.id) AS affected_resources,
       collect(DISTINCT next_resource.id) AS downstream_resources,
       episode.id AS decision_episode,
       plan.id AS selected_plan,
       collect(DISTINCT action.type) AS action_types,
       collect(DISTINCT policy.id) AS authorizing_policies;

