"""Read-only stdio MCP surface for GroundGraph's graph-tool contract.

Run with ``python mcp_server.py``. Messages use MCP's JSON-RPC stdio framing
(one JSON object per line). The coordinator and this server share the same
engine contract and FalkorDB records. Persisted ``ToolCall`` nodes provide the
runtime audit trail; their presence does not imply an external MCP round trip.
Plan execution remains behind the Flask API and deterministic validator.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Mapping

from engine import GroundGraphEngine


MAX_HANDOFFS = 6
MAX_MEMORIES = 10
MAX_POLICIES = 12
MAX_TOOL_CALLS = 8
MAX_EVENTS = 12
MAX_FLIGHTS = 24
MAX_CANDIDATES = 3


TOOL_SPECS = [
    {
        "name": "inspect_disruption",
        "description": (
            "Read the live graph version, active disruptions, delayed flights, "
            "current candidate, and its graph-derived causal path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_agent_handoffs",
        "description": (
            "Read up to six graph-persisted specialist handoffs for one "
            "decision episode, including evidence, query timing, and consumption "
            "status. Pass episode_id for an exact episode; cycle-only calls resolve "
            "the newest episode in that cycle."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "episode_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "cycle": {"type": "integer", "minimum": 0},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_HANDOFFS,
                    "default": MAX_HANDOFFS,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_coordinator_trace",
        "description": (
            "Read persisted coordinator ToolCall and ModelRun records for one "
            "decision episode. Pass episode_id for an exact episode; cycle-only "
            "calls resolve the newest episode in that cycle. This is the auditable "
            "trace of the shared graph-tool contract."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "episode_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "cycle": {"type": "integer", "minimum": 0},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TOOL_CALLS,
                    "default": MAX_TOOL_CALLS,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "recall_episode",
        "description": (
            "Recall graph-persisted successful outcomes that match the current or "
            "requested disruption context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_class": {"type": "string", "maxLength": 80},
                "context_signature": {"type": "string", "maxLength": 256},
                "flight_id": {"type": "string", "maxLength": 128},
                "rotation_id": {"type": "string", "maxLength": 128},
                "resource_id": {"type": "string", "maxLength": 128},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_MEMORIES,
                    "default": 5,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_current_policy",
        "description": (
            "Read current graph policy versions, owners, sources, sections, and "
            "authorized action types."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "maxLength": 80},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_POLICIES,
                    "default": MAX_POLICIES,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "validate_candidate",
        "description": (
            "Recompute the current candidate's graph version, policy, knowledge, "
            "target-edge, freeze, benefit, and churn guards against live graph state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string", "minLength": 1, "maxLength": 160},
                "expected_state_version": {"type": "integer", "minimum": 0},
            },
            "required": ["candidate_id", "expected_state_version"],
            "additionalProperties": False,
        },
    },
]


def _bounded_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def _bounded_text(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return [value]
        return parsed if isinstance(parsed, list) else [parsed]
    return [value]


def _dedupe(values: Iterable[Any]) -> list[str]:
    return sorted({str(value) for value in values if value not in (None, "")})


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


class GroundGraphMCP:
    def __init__(self, engine: GroundGraphEngine) -> None:
        self.engine = engine

    def _latest_cycle(self) -> int:
        rows = self.engine.backend.query(
            "MATCH (s:Scenario {id: $id}) RETURN s.cycle",
            {"id": self.engine.scenario_id},
        ).result_set
        return int(rows[0][0] or 0) if rows else 0

    def _requested_cycle(self, args: Mapping[str, Any]) -> int:
        raw = args.get("cycle")
        if raw is None:
            return self._latest_cycle()
        try:
            return max(0, int(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("cycle must be a non-negative integer") from exc

    def _episode_scope(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Resolve one exact decision episode for all episode-scoped MCP reads."""

        raw_episode_id = args.get("episode_id")
        if raw_episode_id is not None:
            if not isinstance(raw_episode_id, str):
                raise ValueError("episode_id must be a non-empty string")
            requested_episode_id = raw_episode_id.strip()
            if not requested_episode_id or len(requested_episode_id) > 160:
                raise ValueError(
                    "episode_id must be a non-empty string of at most 160 characters"
                )
            requested_cycle = (
                self._requested_cycle(args) if "cycle" in args else None
            )
            rows = self.engine.backend.query(
                "MATCH (d:DecisionEpisode {id: $episode}) "
                "RETURN d.id, d.cycle, d.created_at",
                {"episode": requested_episode_id},
            ).result_set
            if not rows:
                return {
                    "requested_episode_id": requested_episode_id,
                    "requested_cycle": requested_cycle,
                    "episode_id": None,
                    "resolved_cycle": None,
                    "episode_resolution": "episode_not_found",
                }
            episode_id = str(rows[0][0])
            resolved_cycle = int(rows[0][1] or 0)
            if requested_cycle is not None and requested_cycle != resolved_cycle:
                raise ValueError(
                    "cycle does not match the requested decision episode"
                )
            return {
                "requested_episode_id": requested_episode_id,
                "requested_cycle": requested_cycle,
                "episode_id": episode_id,
                "resolved_cycle": resolved_cycle,
                "episode_resolution": "explicit_episode_id",
            }

        requested_cycle = self._requested_cycle(args)
        rows = self.engine.backend.query(
            "MATCH (d:DecisionEpisode) WHERE d.cycle = $cycle "
            "RETURN d.id, d.cycle, d.created_at "
            "ORDER BY d.created_at DESC, d.id DESC",
            {"cycle": requested_cycle},
        ).result_set
        if not rows:
            return {
                "requested_episode_id": None,
                "requested_cycle": requested_cycle,
                "episode_id": None,
                "resolved_cycle": None,
                "episode_resolution": "cycle_not_found",
            }
        return {
            "requested_episode_id": None,
            "requested_cycle": requested_cycle,
            "episode_id": str(rows[0][0]),
            "resolved_cycle": int(rows[0][1] or 0),
            "episode_resolution": "latest_episode_for_cycle",
        }

    def _handoffs_for_episode(self, episode_id: str | None) -> list[dict[str, Any]]:
        if not episode_id:
            return []
        rows = self.engine.backend.query(
            "MATCH (d:DecisionEpisode {id: $episode})-[:CONSUMED_HANDOFF]->"
            "(h:Handoff) "
            "MATCH (agent:Agent)-[:STARTED]->(run:AgentRun)-[:PRODUCED]->"
            "(finding:Finding)-[:PACKAGED_AS]->(h) "
            "MATCH (h)-[:DELIVERED_TO]->(:Agent {id: 'AGENT-COORD'}) "
            "RETURN h.id, "
            "h.from_agent, h.to_agent, h.status, h.summary, h.evidence_ids, "
            "h.state_version, h.cycle, h.created_at, h.query_name, h.started_at, "
            "h.ended_at, h.duration_ms, h.consumed_at, d.id, run.id, finding.id "
            "ORDER BY h.id",
            {"episode": episode_id},
        ).result_set
        return [
            {
                "id": row[0],
                "from_agent": row[1],
                "to_agent": row[2],
                "status": row[3],
                "summary": _bounded_text(row[4], 500),
                "evidence_ids": _dedupe(_as_list(row[5]))[:24],
                "state_version": int(row[6] or 0),
                "cycle": int(row[7] or 0),
                "created_at": row[8],
                "query_name": row[9],
                "started_at": row[10],
                "ended_at": row[11],
                "duration_ms": int(row[12] or 0),
                "consumed_at": row[13],
                "episode_id": row[14],
                "agent_run_id": row[15],
                "finding_id": row[16],
            }
            for row in rows[:MAX_HANDOFFS]
        ]

    def _tool_trace_for_episode(self, episode_id: str | None) -> list[dict[str, Any]]:
        if not episode_id:
            return []
        rows = self.engine.backend.query(
            "MATCH (d:DecisionEpisode {id: $episode})-[:USED_TOOL]->(t:ToolCall) "
            "RETURN t.id, t.name, t.transport, "
            "t.arguments_json, t.evidence_ids, t.result_summary, t.status, "
            "t.sequence, t.cycle, t.created_at, d.id ORDER BY t.sequence",
            {"episode": episode_id},
        ).result_set
        return [
            {
                "id": row[0],
                "name": row[1],
                "transport": row[2],
                "arguments": _parse_json_object(row[3]),
                "evidence_ids": _dedupe(_as_list(row[4]))[:32],
                "result_summary": _bounded_text(row[5], 500),
                "status": row[6],
                "sequence": int(row[7] or 0),
                "cycle": int(row[8] or 0),
                "created_at": row[9],
                "episode_id": row[10],
            }
            for row in rows[:MAX_TOOL_CALLS]
        ]

    def _model_runs_for_episode(self, episode_id: str | None) -> list[dict[str, Any]]:
        if not episode_id:
            return []
        rows = self.engine.backend.query(
            "MATCH (d:DecisionEpisode {id: $episode})-[:USED_MODEL]->(m:ModelRun) "
            "RETURN m.id, m.provider, m.model, m.selected_candidate_id, "
            "m.allowlisted_candidate_ids, m.cost_direction, m.decision_source, "
            "m.summary, m.created_at, d.id, d.cycle "
            "ORDER BY m.created_at DESC, m.id DESC",
            {"episode": episode_id},
        ).result_set
        return [
            {
                "id": row[0],
                "provider": row[1],
                "model": row[2],
                "selected_candidate_id": row[3],
                "allowlisted_candidate_ids": [
                    str(value)
                    for value in _as_list(row[4])
                    if value not in (None, "")
                ][:MAX_CANDIDATES],
                "cost_direction": row[5],
                "decision_source": row[6],
                "summary": _bounded_text(row[7], 500),
                "created_at": row[8],
                "episode_id": row[9],
                "cycle": int(row[10] or 0),
            }
            for row in rows[:1]
        ]

    @staticmethod
    def _candidate_view(plan: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not plan:
            return None
        actions = []
        for action in list(plan.get("actions", []))[:6]:
            actions.append(
                {
                    key: action.get(key)
                    for key in (
                        "id",
                        "type",
                        "target_id",
                        "resource_id",
                        "policy_id",
                        "authorized",
                        "knowledge_current",
                    )
                    if action.get(key) is not None
                }
            )
        alternatives = []
        for item in list(plan.get("safe_candidate_set", []))[:MAX_CANDIDATES]:
            alternatives.append(
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "strategy",
                        "cost_score",
                        "projected_delay",
                        "recovered_minutes",
                        "burden_minutes",
                        "action_types",
                    )
                    if item.get(key) is not None
                }
            )
        return {
            "plan_id": plan.get("id"),
            "candidate_id": plan.get("candidate_id"),
            "status": plan.get("status"),
            "strategy": plan.get("strategy"),
            "based_on_version": plan.get("based_on_version"),
            "cost_score": plan.get("cost_score"),
            "projected_delay": plan.get("projected_delay"),
            "recovered_minutes": plan.get("recovered_minutes"),
            "selected_by": plan.get("selected_by"),
            "actions": actions,
            "safe_candidates": alternatives,
        }

    @staticmethod
    def _context_from_plan(plan: Mapping[str, Any] | None) -> dict[str, Any]:
        if not plan:
            return {}
        context = plan.get("disruption_context")
        return dict(context) if isinstance(context, Mapping) else {}

    @staticmethod
    def _memory_matches(memory: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
        if not context:
            return False
        if context.get("event_class") and str(memory.get("event_class", "")) != str(
            context["event_class"]
        ):
            return False
        if context.get("signature") and str(memory.get("disruption_signature", "")) != str(
            context["signature"]
        ):
            return False
        dimensions = (
            ("flight_ids", "flight_ids"),
            ("rotation_ids", "rotation_ids"),
            ("resource_ids", "resource_ids"),
        )
        for requested_key, memory_key in dimensions:
            requested = set(_as_list(context.get(requested_key)))
            if requested and not requested.intersection(_as_list(memory.get(memory_key))):
                return False
        return True

    def call_tool(
        self, name: str, arguments: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        args = dict(arguments or {})
        if name == "inspect_disruption":
            snapshot = self.engine.snapshot()
            active_events = [
                item for item in snapshot["events"] if item["status"] == "ACTIVE"
            ][:MAX_EVENTS]
            affected_flights = [
                item for item in snapshot["flights"] if item["delay_minutes"] > 0
            ][:MAX_FLIGHTS]
            path = snapshot["causal_path"]
            return {
                "scenario_id": snapshot["scenario"]["id"],
                "state_version": snapshot["state_version"],
                "cycle": snapshot["clock"]["cycle"],
                "clock": snapshot["clock"],
                "active_events": active_events,
                "affected_flights": affected_flights,
                "candidate": self._candidate_view(snapshot.get("current_plan")),
                "causal_path": {
                    "summary": _bounded_text(path.get("summary"), 500),
                    "nodes": list(path.get("nodes", []))[:40],
                    "edges": list(path.get("edges", []))[:60],
                },
                "evidence_ids": _dedupe(
                    [item.get("id") for item in active_events]
                    + [item.get("id") for item in affected_flights]
                ),
                "source": "live_falkordb_snapshot",
            }
        if name == "get_agent_handoffs":
            scope = self._episode_scope(args)
            limit = _bounded_int(args.get("limit"), MAX_HANDOFFS, MAX_HANDOFFS)
            handoffs = self._handoffs_for_episode(scope["episode_id"])[:limit]
            return {
                **scope,
                "handoffs": handoffs,
                "handoff_count": len(handoffs),
                "complete_specialist_set": (
                    len(handoffs) == MAX_HANDOFFS
                    and len({item["from_agent"] for item in handoffs}) == MAX_HANDOFFS
                ),
                "state_versions": sorted({item["state_version"] for item in handoffs}),
                "evidence_ids": _dedupe(
                    value
                    for item in handoffs
                    for value in [item["id"], *item["evidence_ids"]]
                ),
            }
        if name == "get_coordinator_trace":
            scope = self._episode_scope(args)
            limit = _bounded_int(args.get("limit"), MAX_TOOL_CALLS, MAX_TOOL_CALLS)
            calls = self._tool_trace_for_episode(scope["episode_id"])[:limit]
            model_runs = self._model_runs_for_episode(scope["episode_id"])
            return {
                **scope,
                "tool_calls": calls,
                "tool_call_count": len(calls),
                "model_runs": model_runs,
                "model_run_count": len(model_runs),
                "evidence_ids": _dedupe(
                    value
                    for value in (
                        *(
                            evidence
                            for item in calls
                            for evidence in [
                                item["id"], item["episode_id"], *item["evidence_ids"]
                            ]
                        ),
                        *(
                            evidence
                            for item in model_runs
                            for evidence in [
                                item["id"],
                                item["episode_id"],
                                item["selected_candidate_id"],
                                *item["allowlisted_candidate_ids"],
                            ]
                        ),
                    )
                ),
                "runtime_trace": "persisted_falkordb_toolcall_nodes",
                "transport_note": (
                    "The coordinator and stdio MCP server share the same graph-tool "
                    "contract. These persisted records do not assert an external MCP "
                    "network round trip."
                ),
            }
        if name == "recall_episode":
            limit = _bounded_int(args.get("limit"), 5, MAX_MEMORIES)
            snapshot = self.engine.snapshot()
            context = self._context_from_plan(snapshot.get("current_plan"))
            context_source = "current_plan" if context else "none"
            overrides = {
                "event_class": _bounded_text(args.get("event_class"), 80),
                "signature": _bounded_text(args.get("context_signature"), 256),
                "flight_ids": [_bounded_text(args.get("flight_id"), 128)]
                if args.get("flight_id")
                else [],
                "rotation_ids": [_bounded_text(args.get("rotation_id"), 128)]
                if args.get("rotation_id")
                else [],
                "resource_ids": [_bounded_text(args.get("resource_id"), 128)]
                if args.get("resource_id")
                else [],
            }
            if any(value for value in overrides.values()):
                context_source = "tool_arguments"
                context = {key: value for key, value in overrides.items() if value}
            memories = [
                item
                for item in self.engine._memories(limit=50)
                if self._memory_matches(item, context)
            ][:limit]
            return {
                "query_context": context,
                "context_source": context_source,
                "episodes": memories,
                "episode_count": len(memories),
                "evidence_ids": _dedupe(
                    value
                    for item in memories
                    for value in (item.get("id"), item.get("outcome_id"), item.get("plan_id"))
                ),
            }
        if name == "get_current_policy":
            requested = _bounded_text(args.get("action_type"), 80)
            limit = _bounded_int(args.get("limit"), MAX_POLICIES, MAX_POLICIES)
            policies = self.engine._policies()
            if requested:
                policies = [
                    item for item in policies if requested in item["action_types"]
                ]
            policies = policies[:limit]
            return {
                "requested_action_type": requested or None,
                "policies": policies,
                "policy_count": len(policies),
                "evidence_ids": _dedupe(
                    value
                    for item in policies
                    for value in (
                        item.get("id"),
                        item.get("procedure_id"),
                        item.get("document_id"),
                    )
                ),
            }
        if name == "validate_candidate":
            candidate_id = _bounded_text(args.get("candidate_id"), 160)
            if not candidate_id:
                raise ValueError("candidate_id is required")
            try:
                expected = int(args.get("expected_state_version", -1))
            except (TypeError, ValueError) as exc:
                raise ValueError("expected_state_version must be a non-negative integer") from exc
            if expected < 0:
                raise ValueError("expected_state_version must be a non-negative integer")
            validation = self.engine.validate_candidate(candidate_id, expected)
            validation["source"] = "live_falkordb_revalidation"
            return validation
        raise ValueError(f"Unknown GroundGraph MCP tool: {name}")

    def handle(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        method = str(message.get("method", ""))
        request_id = message.get("id")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "groundgraph", "version": "1.1.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOL_SPECS}
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, Mapping):
                raise ValueError("tools/call params must be an object")
            structured = self.call_tool(
                str(params.get("name", "")),
                params.get("arguments")
                if isinstance(params.get("arguments"), Mapping)
                else {},
            )
            result = {
                "content": [
                    {"type": "text", "text": json.dumps(structured, sort_keys=True)}
                ],
                "structuredContent": structured,
                "isError": False,
            }
        else:
            raise ValueError(f"Unsupported MCP method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve() -> None:
    engine = GroundGraphEngine()
    server = GroundGraphMCP(engine)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            message: Any = None
            try:
                message = json.loads(line)
                response = server.handle(message)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id") if isinstance(message, Mapping) else None,
                    "error": {"code": -32603, "message": str(exc)},
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        engine.close()


if __name__ == "__main__":
    serve()
