from __future__ import annotations

from pathlib import Path

import pytest

from engine import GroundGraphEngine
from mcp_server import GroundGraphMCP


@pytest.fixture()
def supervised_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUNDGRAPH_AUTO_APPLY", "0")
    engine = GroundGraphEngine(
        mode="lite", lite_path=tmp_path / "mcp-supervised.db", enable_llm=False
    )
    yield engine
    engine.close()


@pytest.fixture()
def automatic_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUNDGRAPH_AUTO_APPLY", "1")
    engine = GroundGraphEngine(
        mode="lite", lite_path=tmp_path / "mcp-automatic.db", enable_llm=False
    )
    yield engine
    engine.close()


def test_mcp_lists_bounded_read_tools_and_live_validation_rechecks_graph(
    supervised_engine: GroundGraphEngine,
):
    server = GroundGraphMCP(supervised_engine)
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    specs = {item["name"]: item for item in listed["result"]["tools"]}

    assert set(specs) == {
        "inspect_disruption",
        "get_agent_handoffs",
        "get_coordinator_trace",
        "recall_episode",
        "get_current_policy",
        "validate_candidate",
    }
    assert specs["get_agent_handoffs"]["inputSchema"]["properties"]["limit"][
        "maximum"
    ] == 6
    assert specs["get_agent_handoffs"]["inputSchema"]["properties"]["episode_id"][
        "maxLength"
    ] == 160
    assert specs["get_coordinator_trace"]["inputSchema"]["properties"]["limit"][
        "maximum"
    ] == 8
    assert specs["get_coordinator_trace"]["inputSchema"]["properties"]["episode_id"][
        "maxLength"
    ] == 160
    assert specs["recall_episode"]["inputSchema"]["properties"]["limit"][
        "maximum"
    ] == 10

    supervised_engine.tick(to_minute=5)
    inspected = server.call_tool("inspect_disruption")
    candidate = inspected["candidate"]
    assert inspected["source"] == "live_falkordb_snapshot"
    assert candidate["status"] == "PROPOSED"
    assert candidate["candidate_id"]
    assert candidate["safe_candidates"]
    assert len(candidate["safe_candidates"]) <= 3
    assert len(inspected["active_events"]) <= 12
    assert len(inspected["affected_flights"]) <= 24

    valid = server.call_tool(
        "validate_candidate",
        {
            "candidate_id": candidate["candidate_id"],
            "expected_state_version": inspected["state_version"],
        },
    )
    assert valid["passed"] is True
    assert valid["source"] == "live_falkordb_revalidation"
    assert {item["id"] for item in valid["checks"]} >= {
        "CURRENT_GRAPH_STATE",
        "CURRENT_POLICY_AUTHORITY",
        "CURRENT_KNOWLEDGE",
        "TARGETS_EXIST",
    }

    # The MCP result is a fresh graph check, not the stored validation booleans.
    policy_id = candidate["actions"][0]["policy_id"]
    supervised_engine.backend.query(
        "MATCH (p:Policy {id: $id}) SET p.current = false, p.status = 'SUPERSEDED'",
        {"id": policy_id},
    )
    invalid = server.call_tool(
        "validate_candidate",
        {
            "candidate_id": candidate["candidate_id"],
            "expected_state_version": inspected["state_version"],
        },
    )
    assert invalid["passed"] is False
    checks = {item["id"]: item["passed"] for item in invalid["checks"]}
    assert checks["CURRENT_POLICY_AUTHORITY"] is False
    assert checks["CURRENT_KNOWLEDGE"] is False


def test_cycle_handoffs_and_coordinator_trace_are_persisted_graph_records(
    supervised_engine: GroundGraphEngine,
):
    snapshot = supervised_engine.tick(to_minute=5)
    cycle = snapshot["clock"]["cycle"]
    based_on_version = snapshot["current_plan"]["based_on_version"]
    server = GroundGraphMCP(supervised_engine)

    payload = server.call_tool("get_agent_handoffs", {"cycle": cycle})
    assert payload["requested_cycle"] == cycle
    assert payload["episode_id"] == snapshot["current_plan"]["episode_id"]
    assert payload["episode_resolution"] == "latest_episode_for_cycle"
    assert payload["handoff_count"] == 6
    assert payload["complete_specialist_set"] is True
    assert payload["state_versions"] == [based_on_version]
    assert len({item["from_agent"] for item in payload["handoffs"]}) == 6
    assert len({item["query_name"] for item in payload["handoffs"]}) == 6
    assert {item["status"] for item in payload["handoffs"]} == {"CONSUMED"}
    assert all(item["started_at"] and item["ended_at"] for item in payload["handoffs"])
    assert {item["episode_id"] for item in payload["handoffs"]} == {
        payload["episode_id"]
    }
    assert len({item["agent_run_id"] for item in payload["handoffs"]}) == 6
    assert len({item["finding_id"] for item in payload["handoffs"]}) == 6

    for handoff in payload["handoffs"]:
        rows = supervised_engine.backend.query(
            "MATCH (h:Handoff {id: $id}) RETURN count(h)", {"id": handoff["id"]}
        ).result_set
        assert int(rows[0][0]) == 1

    trace = server.call_tool("get_coordinator_trace", {"cycle": cycle})
    assert trace["requested_cycle"] == cycle
    assert trace["episode_id"] == snapshot["current_plan"]["episode_id"]
    assert trace["episode_resolution"] == "latest_episode_for_cycle"
    assert trace["runtime_trace"] == "persisted_falkordb_toolcall_nodes"
    assert "do not assert an external MCP network round trip" in trace[
        "transport_note"
    ]
    assert [item["sequence"] for item in trace["tool_calls"]] == [1, 2, 3, 4]
    assert {item["name"] for item in trace["tool_calls"]} == {
        "inspect_disruption",
        "get_current_policy",
        "recall_episode",
        "validate_candidate",
    }
    assert {item["status"] for item in trace["tool_calls"]} == {"COMPLETED"}
    assert {item["transport"] for item in trace["tool_calls"]} == {
        "MCP_COMPATIBLE_GRAPH_TOOL"
    }
    assert trace["model_run_count"] == 1
    model_run = trace["model_runs"][0]
    assert model_run["episode_id"] == snapshot["current_plan"]["episode_id"]
    assert model_run["selected_candidate_id"] == snapshot["current_plan"]["candidate_id"]
    assert sorted(model_run["allowlisted_candidate_ids"]) == sorted(
        item["id"] for item in snapshot["current_plan"]["model_candidate_set"]
    )

    for call in trace["tool_calls"]:
        rows = supervised_engine.backend.query(
            "MATCH (d:DecisionEpisode {id: $episode})-[:USED_TOOL]->"
            "(t:ToolCall {id: $tool}) RETURN count(t)",
            {"episode": call["episode_id"], "tool": call["id"]},
        ).result_set
        assert int(rows[0][0]) == 1

    assert len(
        server.call_tool("get_coordinator_trace", {"cycle": cycle, "limit": 999})[
            "tool_calls"
        ]
    ) <= 8
    assert server.call_tool("get_agent_handoffs", {"cycle": cycle + 999}) == {
        "requested_episode_id": None,
        "requested_cycle": cycle + 999,
        "episode_id": None,
        "resolved_cycle": None,
        "episode_resolution": "cycle_not_found",
        "handoffs": [],
        "handoff_count": 0,
        "complete_specialist_set": False,
        "state_versions": [],
        "evidence_ids": [],
    }


def test_episode_selector_prevents_cross_episode_mixing_after_cycle_reuse(
    automatic_engine: GroundGraphEngine,
):
    first = automatic_engine.tick(to_minute=5)
    cycle = first["clock"]["cycle"]
    old_episode_id = first["current_plan"]["episode_id"]
    old_handoff_ids = {
        item["id"]
        for item in first["handoffs"]
        if item["episode_id"] == old_episode_id
    }
    old_tool_ids = {
        item["id"]
        for item in first["tool_trace"]
        if item["episode_id"] == old_episode_id
    }

    automatic_engine.reset(preserve_memory=True)
    second = automatic_engine.tick(to_minute=5)
    new_episode_id = second["current_plan"]["episode_id"]
    assert new_episode_id != old_episode_id
    assert second["clock"]["cycle"] == cycle
    new_handoff_ids = {
        item["id"]
        for item in second["handoffs"]
        if item["episode_id"] == new_episode_id
    }
    new_tool_ids = {
        item["id"]
        for item in second["tool_trace"]
        if item["episode_id"] == new_episode_id
    }
    assert len(old_handoff_ids) == len(new_handoff_ids) == 6
    assert len(old_tool_ids) == len(new_tool_ids) == 4

    server = GroundGraphMCP(automatic_engine)
    legacy_handoffs = server.call_tool("get_agent_handoffs", {"cycle": cycle})
    legacy_trace = server.call_tool("get_coordinator_trace", {"cycle": cycle})
    assert legacy_handoffs["episode_id"] == new_episode_id
    assert legacy_trace["episode_id"] == new_episode_id
    assert {item["id"] for item in legacy_handoffs["handoffs"]} == new_handoff_ids
    assert {item["id"] for item in legacy_trace["tool_calls"]} == new_tool_ids
    assert {item["episode_id"] for item in legacy_handoffs["handoffs"]} == {
        new_episode_id
    }
    assert {item["episode_id"] for item in legacy_trace["tool_calls"]} == {
        new_episode_id
    }
    assert {item["episode_id"] for item in legacy_trace["model_runs"]} == {
        new_episode_id
    }

    for episode_id, handoff_ids, tool_ids in (
        (old_episode_id, old_handoff_ids, old_tool_ids),
        (new_episode_id, new_handoff_ids, new_tool_ids),
    ):
        handoffs = server.call_tool("get_agent_handoffs", {"episode_id": episode_id})
        trace = server.call_tool("get_coordinator_trace", {"episode_id": episode_id})
        assert handoffs["requested_episode_id"] == episode_id
        assert handoffs["requested_cycle"] is None
        assert handoffs["episode_id"] == episode_id
        assert handoffs["resolved_cycle"] == cycle
        assert handoffs["episode_resolution"] == "explicit_episode_id"
        assert {item["id"] for item in handoffs["handoffs"]} == handoff_ids
        assert {item["episode_id"] for item in handoffs["handoffs"]} == {
            episode_id
        }
        assert trace["requested_episode_id"] == episode_id
        assert trace["requested_cycle"] is None
        assert trace["episode_id"] == episode_id
        assert trace["resolved_cycle"] == cycle
        assert trace["episode_resolution"] == "explicit_episode_id"
        assert {item["id"] for item in trace["tool_calls"]} == tool_ids
        assert {item["episode_id"] for item in trace["tool_calls"]} == {
            episode_id
        }
        assert trace["model_run_count"] == 1
        assert trace["model_runs"][0]["episode_id"] == episode_id

    newest_by_default = server.call_tool("get_coordinator_trace")
    assert newest_by_default["episode_id"] == new_episode_id

    limited = server.call_tool(
        "get_agent_handoffs", {"episode_id": old_episode_id, "limit": 1}
    )
    assert len(limited["handoffs"]) == 1
    assert limited["handoffs"][0]["episode_id"] == old_episode_id

    with pytest.raises(ValueError, match="cycle does not match"):
        server.call_tool(
            "get_agent_handoffs",
            {"episode_id": old_episode_id, "cycle": cycle + 1},
        )
    with pytest.raises(ValueError, match="cycle does not match"):
        server.call_tool(
            "get_coordinator_trace",
            {"episode_id": old_episode_id, "cycle": cycle + 1},
        )

    missing_handoffs = server.call_tool(
        "get_agent_handoffs", {"episode_id": "EPISODE-DOES-NOT-EXIST"}
    )
    missing_trace = server.call_tool(
        "get_coordinator_trace", {"episode_id": "EPISODE-DOES-NOT-EXIST"}
    )
    assert missing_handoffs["episode_id"] is None
    assert missing_handoffs["episode_resolution"] == "episode_not_found"
    assert missing_handoffs["handoffs"] == []
    assert missing_trace["episode_id"] is None
    assert missing_trace["episode_resolution"] == "episode_not_found"
    assert missing_trace["tool_calls"] == []
    assert missing_trace["model_runs"] == []


def test_contextual_memory_and_policy_tools_return_bounded_graph_evidence(
    automatic_engine: GroundGraphEngine,
):
    snapshot = automatic_engine.tick(to_minute=5)
    server = GroundGraphMCP(automatic_engine)

    recalled = server.call_tool("recall_episode", {"limit": 999})
    assert recalled["context_source"] == "current_plan"
    assert recalled["query_context"]["event_class"]
    assert recalled["episode_count"] == 1
    assert len(recalled["episodes"]) <= 10
    episode = recalled["episodes"][0]
    assert episode["event_class"] == recalled["query_context"]["event_class"]
    assert {episode["id"], episode["outcome_id"], episode["plan_id"]}.issubset(
        recalled["evidence_ids"]
    )

    unrelated = server.call_tool(
        "recall_episode", {"event_class": "UNRELATED_EVENT_CLASS"}
    )
    assert unrelated["context_source"] == "tool_arguments"
    assert unrelated["episodes"] == []
    assert unrelated["evidence_ids"] == []

    action_type = snapshot["current_plan"]["actions"][0]["type"]
    policies = server.call_tool(
        "get_current_policy", {"action_type": action_type, "limit": 999}
    )
    assert 1 <= policies["policy_count"] <= 12
    assert all(action_type in item["action_types"] for item in policies["policies"])
    assert all(item["status"] == "CURRENT" for item in policies["policies"])
    assert all(item["freshness_status"] == "CURRENT_FOR_REPLAY" for item in policies["policies"])
    assert all(item["id"] in policies["evidence_ids"] for item in policies["policies"])

    handled = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "get_coordinator_trace",
                "arguments": {"cycle": snapshot["clock"]["cycle"]},
            },
        }
    )
    assert handled["result"]["isError"] is False
    assert handled["result"]["structuredContent"]["tool_call_count"] == 4

