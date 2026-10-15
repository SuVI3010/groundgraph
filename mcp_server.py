"""Minimal stdio MCP server exposing GroundGraph's bounded graph tools.

Run with ``python mcp_server.py``.  Messages use MCP's JSON-RPC stdio framing
(one JSON object per line).  The surface is read-only and allowlisted; plan
execution remains behind the Flask API and deterministic validator.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Mapping

from engine import GroundGraphEngine


TOOL_SPECS = [
    {
        "name": "inspect_disruption",
        "description": "Read active events, affected flights, graph version, and causal path.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_agent_handoffs",
        "description": "Read graph-persisted specialist handoffs for the latest cycles.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 30}},
            "additionalProperties": False,
        },
    },
    {
        "name": "recall_episode",
        "description": "Recall prior applied outcomes and their ranking influence.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_current_policy",
        "description": "Read current policy versions, owners, sources, sections, and excerpts.",
        "inputSchema": {
            "type": "object",
            "properties": {"action_type": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "validate_candidate",
        "description": "Recheck the current candidate against graph version, current policy and knowledge, freeze, benefit, and churn guards.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "expected_state_version": {"type": "integer", "minimum": 0},
            },
            "required": ["candidate_id", "expected_state_version"],
            "additionalProperties": False,
        },
    },
]


class GroundGraphMCP:
    def __init__(self, engine: GroundGraphEngine) -> None:
        self.engine = engine

    def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        args = dict(arguments or {})
        if name == "inspect_disruption":
            snapshot = self.engine.snapshot()
            return {
                "scenario_id": snapshot["scenario"]["id"],
                "state_version": snapshot["state_version"],
                "clock": snapshot["clock"],
                "active_events": [item for item in snapshot["events"] if item["status"] == "ACTIVE"],
                "affected_flights": [item for item in snapshot["flights"] if item["delay_minutes"] > 0],
                "causal_path": snapshot["causal_path"],
                "evidence_ids": [item["id"] for item in snapshot["events"] if item["processed"]],
            }
        if name == "get_agent_handoffs":
            limit = max(1, min(30, int(args.get("limit", 12))))
            handoffs = self.engine._handoffs(limit=limit)
            return {"handoffs": handoffs, "evidence_ids": [item["id"] for item in handoffs]}
        if name == "recall_episode":
            limit = max(1, min(10, int(args.get("limit", 5))))
            memories = self.engine._memories(limit=limit)
            return {"episodes": memories, "evidence_ids": [item["id"] for item in memories]}
        if name == "get_current_policy":
            requested = str(args.get("action_type", "")).strip()
            policies = self.engine._policies()
            if requested:
                policies = [item for item in policies if requested in item["action_types"]]
            return {"policies": policies, "evidence_ids": [item["id"] for item in policies]}
        if name == "validate_candidate":
            candidate_id = str(args.get("candidate_id", ""))
            expected = int(args.get("expected_state_version", -1))
            return self.engine.validate_candidate(candidate_id, expected)
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
                "serverInfo": {"name": "groundgraph", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOL_SPECS}
        elif method == "tools/call":
            params = message.get("params", {})
            if not isinstance(params, Mapping):
                raise ValueError("tools/call params must be an object")
            structured = self.call_tool(
                str(params.get("name", "")),
                params.get("arguments") if isinstance(params.get("arguments"), Mapping) else {},
            )
            result = {
                "content": [{"type": "text", "text": json.dumps(structured, sort_keys=True)}],
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
