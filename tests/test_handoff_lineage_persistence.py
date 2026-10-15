from __future__ import annotations

from pathlib import Path

import pytest

from engine import GroundGraphEngine


@pytest.fixture()
def auto_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUNDGRAPH_AUTO_APPLY", "1")
    engine = GroundGraphEngine(mode="lite", lite_path=tmp_path / "handoffs.db")
    yield engine
    engine.close()


def _ids(engine: GroundGraphEngine, query: str) -> list[str]:
    return [str(row[0]) for row in engine.backend.query(query).result_set]


def test_applied_episode_handoffs_survive_memory_reset_without_id_collisions(
    auto_engine: GroundGraphEngine,
):
    first = auto_engine.tick(to_minute=5)
    first_handoffs = first["handoffs"]

    assert len(first_handoffs) == 6
    assert {item["status"] for item in first_handoffs} == {"CONSUMED"}
    first_episode_ids = {item["episode_id"] for item in first_handoffs}
    assert len(first_episode_ids) == 1
    first_episode_id = first_episode_ids.pop()
    first_handoff_ids = {item["id"] for item in first_handoffs}
    assert all(first_episode_id.split("-", 2)[2] in item for item in first_handoff_ids)

    restored = auto_engine.reset(preserve_memory=True)
    restored_handoffs = restored["handoffs"]
    assert {item["id"] for item in restored_handoffs} == first_handoff_ids
    assert {item["episode_id"] for item in restored_handoffs} == {first_episode_id}
    assert {item["status"] for item in restored_handoffs} == {"CONSUMED"}

    consumed = auto_engine.backend.query(
        "MATCH (:DecisionEpisode {id: $episode})-[:CONSUMED_HANDOFF]->"
        "(:Handoff) RETURN count(*)",
        {"episode": first_episode_id},
    ).result_set[0][0]
    delivered = auto_engine.backend.query(
        "MATCH (:Handoff)-[:DELIVERED_TO]->(:Agent {id: 'AGENT-COORD'}) "
        "RETURN count(*)"
    ).result_set[0][0]
    specialist_chains = auto_engine.backend.query(
        "MATCH (:Agent)-[:STARTED]->(:AgentRun)-[:PRODUCED]->(:Finding)"
        "-[:PACKAGED_AS]->(:Handoff) RETURN count(*)"
    ).result_set[0][0]
    assert int(consumed) == int(delivered) == int(specialist_chains) == 6

    second = auto_engine.tick(to_minute=5)
    assert len(second["handoffs"]) == 12
    assert len({item["episode_id"] for item in second["handoffs"]}) == 2
    assert len({item["id"] for item in second["handoffs"]}) == 12
    assert len(second["tool_trace"]) == 8
    assert len({item["id"] for item in second["tool_trace"]}) == 8
    assert len(second["model_trace"]) == 2
    assert len({item["id"] for item in second["model_trace"]}) == 2
    assert first_handoff_ids < {item["id"] for item in second["handoffs"]}
    assert len(_ids(auto_engine, "MATCH (f:Finding) RETURN f.id")) == 12
    assert len(
        _ids(
            auto_engine,
            "MATCH (:Agent)-[:STARTED]->(r:AgentRun)-[:PRODUCED]->(:Finding) "
            "RETURN r.id",
        )
    ) == 12

    clean = auto_engine.reset(preserve_memory=False)
    assert clean["handoffs"] == []
    assert clean["memory"]["episode_count"] == 0
    assert _ids(auto_engine, "MATCH (h:Handoff) RETURN h.id") == []
    assert _ids(auto_engine, "MATCH (f:Finding) RETURN f.id") == []


def test_snapshot_exposes_ten_complete_handoff_cycles(
    auto_engine: GroundGraphEngine,
):
    snapshot = auto_engine.tick(to_minute=50)

    assert snapshot["clock"]["cycle"] == 10
    assert len(snapshot["handoffs"]) == 10 * 6
    assert {item["cycle"] for item in snapshot["handoffs"]} == set(range(1, 11))
    assert all(item["episode_id"] for item in snapshot["handoffs"])


def test_applied_coordinator_trace_survives_reset_and_no_action_trace_does_not(
    auto_engine: GroundGraphEngine,
):
    before = auto_engine.tick(to_minute=10)

    assert len(before["tool_trace"]) == 8
    assert len(before["model_trace"]) == 1
    expected_model = before["model_trace"][0]
    applied_episode_id = expected_model["episode_id"]
    expected_tools = [
        item
        for item in before["tool_trace"]
        if item["episode_id"] == applied_episode_id
    ]
    no_action_episode_ids = {
        item["episode_id"]
        for item in before["tool_trace"]
        if item["episode_id"] != applied_episode_id
    }
    assert len(expected_tools) == 4
    assert len(no_action_episode_ids) == 1
    assert [
        item["name"] for item in sorted(expected_tools, key=lambda item: item["sequence"])
    ] == [
        "inspect_disruption",
        "get_current_policy",
        "recall_episode",
        "validate_candidate",
    ]
    coordinator_run_id = str(
        auto_engine.backend.query(
            "MATCH (d:DecisionEpisode {id: $episode})-[:USED_RUN]->"
            "(r:AgentRun) RETURN r.id",
            {"episode": applied_episode_id},
        ).result_set[0][0]
    )

    restored = auto_engine.reset(preserve_memory=True)

    assert [
        (item["id"], item["name"])
        for item in restored["tool_trace"]
    ] == [
        (item["id"], item["name"])
        for item in expected_tools
    ]
    assert restored["tool_trace"] == expected_tools
    assert restored["model_trace"] == [expected_model]
    assert restored["model_trace"][0]["allowlisted_candidate_ids"] == (
        expected_model["allowlisted_candidate_ids"]
    )
    assert not no_action_episode_ids.intersection(
        {item["episode_id"] for item in restored["tool_trace"]}
    )

    edge_counts = auto_engine.backend.query(
        "MATCH (d:DecisionEpisode {id: $episode})-[:USED_RUN]->"
        "(r:AgentRun {id: $run}) "
        "OPTIONAL MATCH (d)-[tool_edge:USED_TOOL]->(:ToolCall) "
        "OPTIONAL MATCH (d)-[model_edge:USED_MODEL]->(m:ModelRun) "
        "OPTIONAL MATCH (r)-[ran_edge:RAN_MODEL]->(m) "
        "OPTIONAL MATCH (r)-[invoked_edge:INVOKED]->(:ToolCall) RETURN "
        "count(DISTINCT tool_edge), count(DISTINCT model_edge), "
        "count(DISTINCT ran_edge), count(DISTINCT invoked_edge)",
        {"episode": applied_episode_id, "run": coordinator_run_id},
    ).result_set[0]
    assert [int(value) for value in edge_counts] == [4, 1, 1, 4]
    for no_action_episode_id in no_action_episode_ids:
        count = auto_engine.backend.query(
            "MATCH (d:DecisionEpisode {id: $id}) RETURN count(d)",
            {"id": no_action_episode_id},
        ).result_set[0][0]
        assert int(count) == 0

    clean = auto_engine.reset(preserve_memory=False)
    assert clean["tool_trace"] == []
    assert clean["model_trace"] == []
    assert _ids(auto_engine, "MATCH (t:ToolCall) RETURN t.id") == []
    assert _ids(auto_engine, "MATCH (m:ModelRun) RETURN m.id") == []
