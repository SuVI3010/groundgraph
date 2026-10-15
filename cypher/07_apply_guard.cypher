// Reviewer-facing apply guard: every proposed action needs a current policy edge.
// The server additionally checks action-type membership and policy freshness.
MATCH (plan:Plan {is_current: true})-[:PROPOSES]->(proposed:Action)
WITH plan,
     count(DISTINCT proposed) AS proposed_actions,
     collect(DISTINCT proposed.type) AS action_types
OPTIONAL MATCH (plan)-[:PROPOSES]->(authorized:Action)-[:AUTHORIZED_BY]->(policy:Policy)
WHERE policy.current = true AND policy.status = 'CURRENT'
RETURN plan.id AS current_plan,
       plan.based_on_version AS based_on_version,
       proposed_actions,
       count(DISTINCT authorized) AS authorized_actions,
       proposed_actions = count(DISTINCT authorized) AS all_actions_have_current_policy,
       action_types,
       collect(DISTINCT policy.id) AS authorizing_policies,
       collect(DISTINCT policy.freshness_status) AS knowledge_freshness;

