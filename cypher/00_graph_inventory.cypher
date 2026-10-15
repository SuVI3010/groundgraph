// Labels and counts in the active GroundGraph graph.
MATCH (n)
UNWIND labels(n) AS label
RETURN label, count(*) AS nodes
ORDER BY nodes DESC, label;

// Relationship inventory.
MATCH ()-[r]->()
RETURN type(r) AS relationship, count(*) AS edges
ORDER BY edges DESC, relationship;

