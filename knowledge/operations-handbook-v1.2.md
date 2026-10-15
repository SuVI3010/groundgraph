# GroundGraph demonstration operations handbook

**Document:** `GG-OPS-HANDBOOK:1.2`  
**Status:** Current for the replay  
**Effective:** January 1, 2024  
**Owner:** Operations Control  
**Truth label:** MODELED

This is synthetic demonstration policy. It is not an airport, airline, FAA, or ATC procedure.

## OPS-GATE-01 · Compatible-gate reassignment

The Gate agent may propose a new modeled gate when the flight is compatible, the occupancy interval does not overlap an existing reservation, and the change protects a connected rotation. The action must cite the relevant flight and resource nodes.

## OPS-FLOW-02 · Capacity-slot prioritization

The Flow agent may propose a different modeled departure slot when the flight is eligible, every affected flight retains one unique slot, and the trade-off is recorded in the decision episode.

## OPS-TURN-03 · Rotation protection

The Turnaround agent may protect a connected next leg when the rotation edge and task dependency path exist. Any ground-task readiness not supplied by an observed source must remain labeled MODELED.

## OPS-AUTO-04 · Supervised automatic recovery application

The coordinator may automatically apply a recovery only when all modeled validation checks pass, required sources are fresh, the graph version remains unchanged, and the disruption is within the configured autonomy envelope. It must request duty-manager review when evidence is missing or stale, when a policy has no current version, when a resource conflict remains, or when the graph changes after analysis.

This section supersedes `OPS-AUTO-04:1.1`.

## OPS-EVIDENCE-05 · Decision evidence

Each recommendation must retain its graph path, source identifiers, producing agents, policy version, and outcome assumptions. Counterfactual values must be described as projected under the replay model.

