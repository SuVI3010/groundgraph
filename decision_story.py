"""Plain-language, episode-scoped decision receipts for the GroundGraph UI.

The operational graph remains the source of truth.  This module builds a read
model for one ``DecisionEpisode`` so the browser never has to join the latest
plan, a historical handoff, and an unrelated outcome on its own.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


AGENT_LABELS = {
    "AGENT-WEATHER": ("Weather", "weather and visibility"),
    "AGENT-FLOW": ("Flow", "runway and departure capacity"),
    "AGENT-GATE": ("Gate", "compatible gate options"),
    "AGENT-TURN": ("Turnaround", "the connected task chain"),
    "AGENT-POLICY": ("Policy", "current authority"),
    "AGENT-MEMORY": ("Memory", "matching prior outcomes"),
}

STRATEGY_LABELS = {
    "FLOW_BALANCE": "Flow balance",
    "GATE_SWAP": "Gate recovery",
    "TURNAROUND_RECOVERY": "Turnaround recovery",
}

GUARD_LABELS = {
    "CURRENT_GRAPH_STATE": "Same graph version",
    "CURRENT_POLICY_AUTHORITY": "Current authority",
    "CURRENT_KNOWLEDGE": "Fresh procedure",
    "TARGETS_EXIST": "Required links exist",
    "EXECUTION_FREEZE": "Freeze window clear",
    "MINIMUM_BENEFIT": "Benefit threshold met",
    "CHURN_GUARD": "Churn guard clear",
}


def _json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _list(value: Any) -> list[Any]:
    if isinstance(value, str):
        return _json_value(value, [])
    return list(value) if isinstance(value, (list, tuple)) else []


def _properties(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clock(total_minutes: int) -> str:
    total = int(total_minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _first(*values: Any) -> Any:
    return next(
        (value for value in values if value is not None and value != ""), None
    )


def _event_title(event: Mapping[str, Any]) -> str:
    return str(
        _first(
            event.get("description"),
            event.get("label"),
            event.get("title"),
            str(event.get("type", "Operational change")).replace("_", " ").title(),
        )
    )


def _plain_no_action(summary: str) -> str:
    value = summary.lower()
    if "awaits execution" in value:
        return "The existing safe proposal is still valid, so GroundGraph avoided creating a duplicate plan."
    if "churn" in value:
        return "The last intervention is still inside the ten-minute churn guard, so GroundGraph kept the stable plan."
    if "benefit" in value or "guardrail" in value:
        return "No connected delay exceeded the four-minute benefit threshold, so changing operations would add work without enough recovery."
    return summary or "The scan found no material change that justified another intervention."


def _candidate_reason(candidate: Mapping[str, Any], selected_id: str | None) -> str:
    recovered = int(candidate.get("recovered_minutes") or 0)
    burden = int(candidate.get("burden_minutes") or 0)
    strategy = str(candidate.get("strategy", ""))
    if str(candidate.get("id")) == str(selected_id):
        return f"Selected: recovers {recovered} min with {burden} min of modeled coordination."
    if strategy == "GATE_SWAP":
        return f"Safe alternative: recovers {recovered} min, but moves the rotation to another gate."
    if strategy == "FLOW_BALANCE":
        return f"Safe alternative: recovers {recovered} min, but spends runway-flow flexibility."
    return f"Safe alternative: recovers {recovered} min with {burden} min of coordination."


def build_decision_story(engine: Any, episode_id: str | None = None) -> dict[str, Any]:
    """Return one complete, synchronized explanation from FalkorDB."""

    selector = "WHERE d.id = $episode_id" if episode_id else ""
    rows = engine.backend.query(
        "MATCH (d:DecisionEpisode) "
        f"{selector} "
        "OPTIONAL MATCH (d)-[:SELECTED]->(p:Plan) "
        "OPTIONAL MATCH (p)-[:RESULTED_IN]->(o:Outcome) "
        "OPTIONAL MATCH (d)-[:USED_MODEL]->(m:ModelRun) "
        "RETURN properties(d), properties(p), properties(o), properties(m) "
        "ORDER BY d.created_at DESC LIMIT 1",
        {"episode_id": episode_id} if episode_id else {},
    ).result_set
    if not rows:
        raise KeyError(f"Unknown decision episode: {episode_id or '<latest>'}")

    episode = _properties(rows[0][0])
    plan_node = _properties(rows[0][1])
    outcome_node = _properties(rows[0][2])
    model_node = _properties(rows[0][3])
    selected_episode_id = str(episode.get("id", ""))
    plan = _json_value(plan_node.get("payload_json"), {})
    if plan_node:
        plan["id"] = plan_node.get("id") or plan.get("id")
        plan["status"] = plan_node.get("status") or plan.get("status")
        plan["applied_at"] = plan_node.get("applied_at") or plan.get("applied_at")

    context = _json_value(episode.get("context_json"), {})
    if plan:
        context = _properties(plan.get("disruption_context")) or context
    event_ids = [str(value) for value in _list(context.get("event_ids"))]
    flight_ids = [str(value) for value in _list(context.get("flight_ids"))]
    rotation_ids = [str(value) for value in _list(context.get("rotation_ids"))]
    resource_ids = [str(value) for value in _list(context.get("resource_ids"))]
    action_flight_ids = {
        str(value)
        for action in _list(plan.get("actions"))
        if isinstance(action, Mapping)
        for value in (
            action.get("inbound_flight_id"),
            action.get("affected_flight_id"),
            action.get("target_id")
            if str(action.get("target_id", "")).endswith(("-IN", "-OUT"))
            else None,
        )
        if value
    }
    flight_ids = sorted(set(flight_ids).union(action_flight_ids))

    event_rows = []
    if event_ids:
        event_rows = engine.backend.query(
            "MATCH (e:OperationalEvent) WHERE e.id IN $ids "
            "RETURN properties(e) ORDER BY e.at_minute, e.id",
            {"ids": event_ids},
        ).result_set
    events = [_properties(row[0]) for row in event_rows]
    flight_rows = []
    if flight_ids:
        flight_rows = engine.backend.query(
            "MATCH (f:Flight) WHERE f.id IN $ids RETURN properties(f) ORDER BY f.id",
            {"ids": flight_ids},
        ).result_set
    flights = [_properties(row[0]) for row in flight_rows]

    finding_rows = engine.backend.query(
        "MATCH (d:DecisionEpisode {id: $episode_id})"
        "-[:CONSUMED_HANDOFF]->(h:Handoff) "
        "OPTIONAL MATCH (f:Finding)-[:PACKAGED_AS]->(h) "
        "OPTIONAL MATCH (r:AgentRun)-[:PRODUCED]->(f) "
        "RETURN properties(h), properties(f), properties(r) "
        "ORDER BY h.created_at, h.from_agent",
        {"episode_id": selected_episode_id},
    ).result_set
    findings: list[dict[str, Any]] = []
    evidence_ids: set[str] = set(event_ids)
    for row in finding_rows:
        handoff, finding, run = map(_properties, row)
        agent_id = str(_first(handoff.get("from_agent"), finding.get("agent_id"), ""))
        label, scope = AGENT_LABELS.get(agent_id, (agent_id, "connected state"))
        finding_evidence = [
            str(value)
            for value in _list(
                _first(finding.get("evidence_ids"), handoff.get("evidence_ids"), [])
            )
        ]
        evidence_ids.update(finding_evidence)
        summary = str(_first(finding.get("summary"), handoff.get("summary"), "Scan complete."))
        findings.append(
            {
                "agent_id": agent_id,
                "agent": label,
                "scope": scope,
                "summary": summary,
                "recommendation": finding.get("recommendation"),
                "severity": finding.get("severity"),
                "query": _first(finding.get("query_name"), handoff.get("query_name")),
                "duration_ms": int(
                    _first(finding.get("duration_ms"), handoff.get("duration_ms"), 0)
                    or 0
                ),
                "based_on_version": int(
                    _first(
                        finding.get("based_on_version"),
                        handoff.get("state_version"),
                        episode.get("based_on_version"),
                        0,
                    )
                    or 0
                ),
                "evidence_ids": finding_evidence,
                "run_id": run.get("id"),
                "handoff_id": handoff.get("id"),
            }
        )

    tool_rows = engine.backend.query(
        "MATCH (d:DecisionEpisode {id: $episode_id})-[:USED_TOOL]->(t:ToolCall) "
        "RETURN properties(t) ORDER BY t.sequence",
        {"episode_id": selected_episode_id},
    ).result_set
    tools = []
    for row in tool_rows:
        tool = _properties(row[0])
        tool_evidence = [str(value) for value in _list(tool.get("evidence_ids"))]
        evidence_ids.update(tool_evidence)
        tools.append(
            {
                "name": tool.get("name"),
                "sequence": int(tool.get("sequence") or 0),
                "status": tool.get("status"),
                "summary": tool.get("result_summary"),
                "evidence_ids": tool_evidence,
            }
        )

    replay_minute = episode.get("replay_minute")
    if replay_minute is None and events:
        replay_minute = max(int(event.get("at_minute") or 0) for event in events)
    if replay_minute is None:
        # Older persisted episodes predate the explicit replay timestamp.  The
        # five-minute cadence provides a truthful lower-fidelity display value.
        replay_minute = max(0, (int(episode.get("cycle") or 1) - 1) * 5)
    replay_minute = int(replay_minute)
    replay_time = _clock(int(engine.base_clock_minute) + replay_minute)
    outbound = next(
        (
            flight
            for flight in flights
            if str(flight.get("kind", "")).upper() == "OUTBOUND"
        ),
        {},
    )
    tradeoff = _properties(plan.get("candidate_tradeoff"))
    benefit = _properties(tradeoff.get("benefit_components"))
    critical_path = int(benefit.get("critical_path_before") or 0)
    minutes_to_departure = (
        int(outbound.get("scheduled_minute")) - replay_minute
        if outbound.get("scheduled_minute") is not None
        else None
    )
    projected_slip = (
        max(0, critical_path - minutes_to_departure)
        if critical_path and minutes_to_departure is not None
        else None
    )

    trigger_code = str(episode.get("trigger", "PERIODIC_CHECKPOINT"))
    event = events[-1] if events else {}
    if trigger_code.startswith("EVENT:"):
        trigger_kind = "Operational change"
        trigger_title = _event_title(event)
        trigger_detail = (
            f"The change entered graph version {int(episode.get('based_on_version') or 0)} "
            "and immediately opened a new decision review."
        )
    elif trigger_code.startswith("POLICY"):
        trigger_kind = "Policy change"
        trigger_title = "Operating authority changed"
        trigger_detail = "GroundGraph revalidated the active plan against the new current procedure."
    elif trigger_code.startswith("MANUAL"):
        trigger_kind = "Requested review"
        trigger_title = "An operator requested a fresh decision"
        trigger_detail = "All six specialists reread the shared graph before the coordinator continued."
    else:
        trigger_kind = "Scheduled check"
        trigger_title = "Five-minute safety scan"
        trigger_detail = "GroundGraph checked for material changes without assuming that another action was needed."

    status = str(episode.get("status", "")).upper()
    plan_status = str(plan.get("status", plan_node.get("status", ""))).upper()
    no_action = status in {"NO_ACTION_REQUIRED", "NO_CHANGE"} or not plan
    applied = plan_status == "APPLIED" or status == "APPLIED"

    selected_id = str(plan.get("candidate_id") or model_node.get("selected_candidate_id") or "") or None
    allowlisted_ids = {
        str(value) for value in _list(model_node.get("allowlisted_candidate_ids"))
    }
    if not allowlisted_ids:
        allowlisted_ids = {
            str(item.get("id"))
            for item in _list(plan.get("model_candidate_set"))
            if isinstance(item, Mapping) and item.get("id")
        }
    candidates = []
    for item in _list(plan.get("safe_candidate_set")):
        if not isinstance(item, Mapping):
            continue
        candidate = dict(item)
        candidate_id = str(candidate.get("id", ""))
        candidate["label"] = STRATEGY_LABELS.get(
            str(candidate.get("strategy", "")),
            str(candidate.get("strategy", "Recovery option")).replace("_", " ").title(),
        )
        candidate["selected"] = candidate_id == selected_id
        candidate["shown_to_model"] = candidate_id in allowlisted_ids
        candidate["reason"] = _candidate_reason(candidate, selected_id)
        candidates.append(candidate)

    raw_checks = _properties(plan.get("safety_validation")).get("checks", [])
    guards = []
    for check in _list(raw_checks):
        if not isinstance(check, Mapping):
            continue
        guard_id = str(check.get("id", ""))
        guards.append(
            {
                "id": guard_id,
                "label": GUARD_LABELS.get(
                    guard_id, guard_id.replace("_", " ").title()
                ),
                "passed": bool(check.get("passed")),
                "detail": check.get("detail"),
                "evidence_ids": [
                    str(value) for value in _list(check.get("evidence_ids"))
                ],
            }
        )

    baseline = int(plan.get("baseline_delay") or 0)
    projected = int(plan.get("projected_delay") or 0)
    recovered = int(plan.get("recovered_minutes") or max(0, baseline - projected))
    protected_flights = sorted(
        {
            str(action.get("affected_flight_id") or action.get("target_id"))
            for action in _list(plan.get("actions"))
            if isinstance(action, Mapping)
            and str(action.get("type", "")) == "PROTECT_ROTATION"
            and (action.get("affected_flight_id") or action.get("target_id"))
        }
    )

    shortlist_count = len(allowlisted_ids)
    selection_source = str(plan.get("selected_by") or model_node.get("decision_source") or "")
    if no_action:
        selection_explanation = "No model call was needed because a deterministic guardrail ended the decision."
    elif selection_source == "local_model" and shortlist_count > 1:
        selection_explanation = (
            f"Qwen compared {shortlist_count} safe options and chose one. It could not invent an option or execute it."
        )
    elif selection_source == "local_model":
        selection_explanation = (
            "Qwen confirmed the only safe option for this situation. It still could not execute it."
        )
    else:
        selection_explanation = (
            "The deterministic safe planner selected the lowest-cost eligible option because the local model was unavailable."
        )

    plan_label = STRATEGY_LABELS.get(
        str(plan.get("strategy", "")),
        str(plan.get("strategy", "Recovery")).replace("_", " ").title(),
    )
    no_action_reason = _plain_no_action(str(episode.get("summary", ""))) if no_action else None
    if no_action:
        headline = f"Checked at {replay_time}; no operational change was justified."
    elif applied:
        headline = f"{plan_label} was applied after the model choice passed every guard."
    else:
        headline = f"{plan_label} is ready for human approval."

    stage_status = {
        "detected": "complete",
        "read": "complete" if findings else "active",
        "compared": "complete" if tools else "pending",
        "model": "skipped" if no_action else "complete" if selected_id else "pending",
        "safe": "complete" if no_action or guards else "pending",
        "applied": "skipped" if no_action else "complete" if applied else "active",
        "watching": "active" if applied or no_action else "pending",
    }
    stages = [
        {"id": key, "label": label, "status": stage_status[key]}
        for key, label in (
            ("detected", "Detected"),
            ("read", "6 agents read"),
            ("compared", "Options compared"),
            ("model", "Qwen chose"),
            ("safe", "Safety checked"),
            ("applied", "Applied"),
            ("watching", "Watching"),
        )
    ]

    next_minute = min(
        int(engine.scenario_data.get("duration_minutes", 240)),
        replay_minute + int(engine.planning_interval),
    )
    next_time = _clock(int(engine.base_clock_minute) + next_minute)
    return {
        "ok": True,
        "episode_id": selected_episode_id,
        "cycle": int(episode.get("cycle") or 0),
        "status": status,
        "result_kind": "NO_ACTION" if no_action else "APPLIED" if applied else "PROPOSED",
        "created_at": episode.get("created_at"),
        "replay_minute": replay_minute,
        "replay_time": replay_time,
        "graph_version": int(episode.get("based_on_version") or 0),
        "headline": headline,
        "summary": episode.get("summary"),
        "trigger": {
            "code": trigger_code,
            "kind": trigger_kind,
            "title": trigger_title,
            "detail": trigger_detail,
            "source_kind": event.get("source_kind") or "DERIVED",
            "event_ids": event_ids,
        },
        "affected": {
            "flight_ids": flight_ids,
            "rotation_ids": rotation_ids,
            "resource_ids": resource_ids,
        },
        "why_now": {
            "outbound_flight_id": outbound.get("id"),
            "outbound_callsign": outbound.get("callsign"),
            "scheduled_departure": (
                _clock(int(engine.base_clock_minute) + int(outbound["scheduled_minute"]))
                if outbound.get("scheduled_minute") is not None
                else None
            ),
            "minutes_to_departure": minutes_to_departure,
            "critical_path_minutes": critical_path or None,
            "projected_slip_minutes": projected_slip,
            "explanation": (
                f"{outbound.get('callsign') or outbound.get('id')} leaves in {minutes_to_departure} min, "
                f"but its connected turnaround path needs {critical_path} min. The {projected_slip}-min gap crossed the action threshold."
                if critical_path and minutes_to_departure is not None
                else trigger_detail
            ),
        },
        "stages": stages,
        "findings": findings,
        "candidates": candidates,
        "selection": {
            "source": selection_source,
            "provider": model_node.get("provider"),
            "model": model_node.get("model"),
            "selected_candidate_id": selected_id,
            "allowlisted_candidate_ids": sorted(allowlisted_ids),
            "summary": _first(model_node.get("summary"), plan.get("summary")),
            "explanation": selection_explanation,
        },
        "guards": guards,
        "no_action_reason": no_action_reason,
        "outcome": {
            "available": bool(plan),
            "applied": applied,
            "baseline_delay_minutes": baseline,
            "projected_delay_minutes": projected,
            "recovered_minutes": recovered,
            "protected_flight_ids": protected_flights,
            "status": outcome_node.get("status"),
            "qualifier": "Projected under the bounded replay assumptions; not measured real-world impact.",
        },
        "next_checkpoint": {
            "minute": next_minute,
            "label": next_time,
            "detail": (
                f"Next scheduled graph scan at {next_time}, or immediately if weather, gate, turnaround, or policy state changes."
            ),
        },
        "technical": {
            "plan_id": plan.get("id"),
            "model_run_id": model_node.get("id"),
            "tool_calls": tools,
            "evidence_ids": sorted(evidence_ids),
        },
    }

