from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import create_app
from engine import GroundGraphEngine
from mcp_server import GroundGraphMCP


@pytest.fixture()
def auto_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUNDGRAPH_AUTO_APPLY", "1")
    engine = GroundGraphEngine(mode="lite", lite_path=tmp_path / "auto.db")
    yield engine
    engine.close()


@pytest.fixture()
def supervised_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUNDGRAPH_AUTO_APPLY", "0")
    engine = GroundGraphEngine(mode="lite", lite_path=tmp_path / "supervised.db")
    yield engine
    engine.close()


def test_continuous_cycles_coordinate_six_agents_and_memory_changes_a_later_plan(
    auto_engine: GroundGraphEngine,
):
    initial = auto_engine.snapshot()
    assert initial["clock"]["label"] == "08:00"
    assert len(initial["agents"]) == 7  # Six specialists plus coordinator.
    assert initial["graph"]["central_context_layer"] is True
    assert initial["metrics"]["observed_condition_count"] == 1
    assert initial["metrics"]["modeled_effect_delay_minutes"] == 18
    assert "observed_delay_minutes" not in initial["metrics"]
    assert all(flight["source_kind"] == "MIXED" for flight in initial["flights"])
    assert all(
        resource["source_kind"] == "MIXED"
        for resource in initial["resources"]
        if resource["type"] == "RUNWAY"
    )

    at_bag_event = auto_engine.tick(to_minute=36)

    # Five-minute scans at 05..35 plus the material 08:36 event.
    assert at_bag_event["clock"]["cycle"] == 8
    assert at_bag_event["current_plan"]["status"] == "APPLIED"
    assert at_bag_event["memory"]["episode_count"] == 2
    assert at_bag_event["current_plan"]["memory_influence"]["applied"] is True
    assert at_bag_event["current_plan"]["memory_influence"]["changed_selection"] is True
    assert at_bag_event["current_plan"]["safety_validation"]["passed"] is True
    checks = {
        item["id"]: item["passed"]
        for item in at_bag_event["current_plan"]["safety_validation"]["checks"]
    }
    assert checks["CURRENT_KNOWLEDGE"] is True
    assert len(at_bag_event["handoffs"]) == 12
    edge_types = {item["type"] for item in at_bag_event["causal_path"]["edges"]}
    assert {"ROTATES_TO", "USES_RESOURCE", "AUTHORIZED_BY"}.issubset(edge_types)

    evidence = at_bag_event["current_plan"]["policy_authorization"]["evidence"]
    assert {item["action_type"] for item in evidence} == {
        "RESEQUENCE_DEPARTURE",
        "REASSIGN_GATE",
        "PROTECT_ROTATION",
    }
    assert all(item["procedure_id"] and item["source_excerpt"] for item in evidence)
    assert all(
        item["knowledge_current"]
        and item["freshness_status"] == "CURRENT_FOR_REPLAY"
        for item in evidence
    )

    handoff_count = auto_engine.backend.query("MATCH (h:Handoff) RETURN count(h)").result_set[0][0]
    finding_count = auto_engine.backend.query("MATCH (f:Finding) RETURN count(f)").result_set[0][0]
    assert int(handoff_count) == int(finding_count) == 8 * 6


def test_null_flight_events_activate_and_no_change_cycles_are_audited(
    auto_engine: GroundGraphEngine,
):
    snapshot = auto_engine.tick(to_minute=72)
    events = {item["id"]: item for item in snapshot["events"]}

    assert events["EVT-WX-0904"]["processed"] is True
    assert events["EVT-RWY-0912"]["processed"] is True
    assert snapshot["clock"]["label"] == "09:12"
    assert snapshot["replanning"]["interval_minutes"] == 5
    assert any(item["status"] == "NO_ACTION_REQUIRED" for item in snapshot["replanning"]["history"])
    assert snapshot["current_plan"]["status"] == "APPLIED"


def test_snapshot_keeps_latest_applied_plan_as_display_only_history(
    auto_engine: GroundGraphEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    applied = auto_engine.tick(to_minute=5)
    plan = applied["current_plan"]
    assert plan["status"] == "APPLIED"
    assert plan["baseline_delay"] == (
        plan["projected_delay"] + plan["recovered_minutes"]
    )

    queries: list[str] = []
    original_query = auto_engine.backend.query

    def recorded_query(cypher, params=None):
        queries.append(cypher)
        return original_query(cypher, params)

    monkeypatch.setattr(auto_engine.backend, "query", recorded_query)
    monitored = auto_engine.tick(to_minute=10)
    displayed = monitored["current_plan"]
    assert auto_engine._current_plan() is None
    assert displayed["id"] == plan["id"]
    assert displayed["status"] == "APPLIED"
    assert displayed["display_only"] is True
    assert displayed["historical"] is True
    assert displayed["is_current"] is False
    assert displayed["causal_path"]["nodes"]
    assert displayed["policy_authorization"]["evidence"]
    assert monitored["causal_path"] == displayed["causal_path"]
    assert monitored["metrics"]["baseline_delay_minutes"] == plan["baseline_delay"]
    assert monitored["metrics"]["projected_delay_minutes"] == plan["projected_delay"]
    assert monitored["replanning"]["history"][0]["status"] == "NO_ACTION_REQUIRED"
    assert "MATCH (n) RETURN count(n)" in queries
    assert "MATCH ()-[r]->() RETURN count(r)" in queries
    assert not any("OPTIONAL MATCH ()-[r]->()" in query for query in queries)

    with pytest.raises(RuntimeError, match="Unknown, superseded, or stale"):
        auto_engine.apply(plan["id"], monitored["state_version"])


def test_company_brain_returns_source_aware_current_policy_paths(
    auto_engine: GroundGraphEngine,
):
    answer = auto_engine.company_brain("Who authorizes the recovery and where is it written?")

    assert answer["truth_label"] == "MODELED_SYNTHETIC_COMPANY_KNOWLEDGE"
    assert {item["type"] for item in answer["artifacts"]} >= {
        "SOP",
        "INCIDENT",
        "SHIFT_HANDOFF",
    }
    assert all(item["owner_team_id"] for item in answer["citations"])
    assert all(item["excerpt"] for item in answer["citations"])
    assert "Policy-[:ALIGNED_WITH]->ProcedureVersion" in answer["graph_path"]


def test_stale_plan_is_rejected_and_policy_mutation_changes_authorization(
    supervised_engine: GroundGraphEngine,
):
    first = supervised_engine.tick(to_minute=5)
    stale_plan = first["current_plan"]
    assert stale_plan["status"] == "PROPOSED"

    second = supervised_engine.tick(to_minute=36)
    assert second["state_version"] > stale_plan["based_on_version"]
    with pytest.raises(RuntimeError, match="stale"):
        supervised_engine.apply(stale_plan["id"], stale_plan["based_on_version"])

    changed = supervised_engine.supersede_policy(
        "PROC-GATE-1.3",
        replacement={"id": "PROC-GATE-2.0", "version": "2.0", "action_types": []},
    )
    assert changed["current_plan"] is None
    assert changed["replanning"]["history"][0]["status"] == "ESCALATED"
    relation_count = supervised_engine.backend.query(
        "MATCH (:Airport {id: 'ORD'})-[:GOVERNED_BY]->"
        "(:Policy {id: 'PROC-GATE-2.0'}) RETURN count(*)"
    ).result_set[0][0]
    assert int(relation_count) == 1


def test_policy_freshness_is_checked_during_planning_and_again_before_apply(
    supervised_engine: GroundGraphEngine,
):
    proposed = supervised_engine.tick(to_minute=5)
    plan = proposed["current_plan"]
    assert plan["policy_authorization"]["current_knowledge_complete"] is True

    policy_id = plan["actions"][0]["policy_id"]
    supervised_engine.backend.query(
        "MATCH (p:Policy {id: $id}) SET p.freshness_status = 'STALE_FOR_REPLAY'",
        {"id": policy_id},
    )
    with pytest.raises(RuntimeError, match="knowledge evidence is stale"):
        supervised_engine.apply(plan["id"], proposed["state_version"])

    changed = supervised_engine.supersede_policy(
        policy_id,
        replacement={
            "id": f"{policy_id}-STALE",
            "version": "stale-test",
            "freshness_status": "STALE_FOR_REPLAY",
        },
    )
    assert changed["current_plan"] is None
    assert changed["replanning"]["history"][0]["status"] == "ESCALATED"


def test_apply_and_mcp_recompute_live_freeze_guard(
    supervised_engine: GroundGraphEngine,
):
    proposed = supervised_engine.tick(to_minute=5)
    plan = proposed["current_plan"]
    protected = next(
        action
        for action in plan["actions"]
        if action["type"] == "PROTECT_ROTATION"
    )
    supervised_engine.backend.query(
        "MATCH (f:Flight {id: $id}) SET f.kind = 'OUTBOUND', f.scheduled_minute = 10",
        {"id": protected["target_id"]},
    )

    validation = supervised_engine.validate_candidate(
        plan["candidate_id"], proposed["state_version"]
    )
    checks = {item["id"]: item["passed"] for item in validation["checks"]}
    assert validation["passed"] is False
    assert checks["EXECUTION_FREEZE"] is False

    mcp_validation = GroundGraphMCP(supervised_engine).call_tool(
        "validate_candidate",
        {
            "candidate_id": plan["candidate_id"],
            "expected_state_version": proposed["state_version"],
        },
    )
    assert mcp_validation["passed"] is False
    assert any(
        item["id"] == "EXECUTION_FREEZE" and item["passed"] is False
        for item in mcp_validation["checks"]
    )
    with pytest.raises(RuntimeError, match="EXECUTION_FREEZE"):
        supervised_engine.apply(plan["id"], proposed["state_version"])


def test_local_model_is_allowlisted_and_uses_configured_timeout(
    supervised_engine: GroundGraphEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    candidates = [
        {
            "id": "CAND-SAFE",
            "strategy": "FLOW_BALANCE",
            "score": 7,
            "projected_delay": 2,
        }
    ]
    handoffs = [{"from_agent": "AGENT-FLOW", "summary": "Capacity checked."}]
    observed: dict[str, float] = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read(_limit: int) -> bytes:
            content = json.dumps(
                {"candidate_id": "CAND-SAFE", "summary": "Selected safe plan."}
            )
            return json.dumps({"message": {"content": content}}).encode()

    def fake_urlopen(_request, timeout):
        observed["timeout"] = timeout
        return FakeResponse()

    supervised_engine.enable_llm = True
    supervised_engine.ollama_timeout = 27
    monkeypatch.setattr("engine.urllib.request.urlopen", fake_urlopen)

    candidate_id, source, summary = supervised_engine._local_model_choice(
        candidates, handoffs
    )
    assert candidate_id == "CAND-SAFE"
    assert source == "local_model"
    assert summary == "Selected safe plan."
    assert observed["timeout"] == 27

    FakeResponse.read = staticmethod(
        lambda _limit: json.dumps(
            {
                "message": {
                    "content": json.dumps(
                        {"candidate_id": "CAND-INVENTED", "summary": "Unsafe."}
                    )
                }
            }
        ).encode()
    )
    candidate_id, source, _summary = supervised_engine._local_model_choice(
        candidates, handoffs
    )
    assert candidate_id == "CAND-SAFE"
    assert source == "deterministic_fallback"


def test_memory_ranking_and_local_model_final_choice_have_distinct_attribution(
    auto_engine: GroundGraphEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    first = auto_engine.tick(to_minute=5)
    assert first["memory"]["episode_count"] == 1

    def choose_non_leader(candidates, _handoffs):
        assert len(candidates) > 1
        return (
            candidates[-1]["id"],
            "local_model",
            "Local model chose a different allowlisted recovery tradeoff.",
        )

    auto_engine.enable_llm = True
    monkeypatch.setattr(auto_engine, "_local_model_choice", choose_non_leader)
    snapshot = auto_engine.tick(to_minute=36)
    plan = snapshot["current_plan"]
    influence = plan["memory_influence"]

    assert plan["selected_by"] == "local_model"
    assert influence["applied_to_ranking"] is True
    assert influence["ranking_changed"] is True
    assert influence["memory_adjusted_ranking_winner_id"] != plan["candidate_id"]
    assert influence["selected_candidate_id"] == plan["candidate_id"]
    assert influence["final_candidate_id"] == plan["candidate_id"]
    assert influence["final_choice_source"] == "local_model"
    assert influence["final_choice_matched_adjusted_ranking"] is False
    assert "deterministic ranking" in influence["ranking_summary"]
    assert "local model made the final allowlisted choice" in influence["summary"].lower()
    assert plan["selection_attribution"] == {
        "final_candidate_id": plan["candidate_id"],
        "selected_by": "local_model",
        "candidate_was_allowlisted": True,
        "memory_adjusted_ranking_winner_id": influence[
            "memory_adjusted_ranking_winner_id"
        ],
        "memory_changed_ranking": True,
    }
    recalled = auto_engine.backend.query(
        "MATCH (d:DecisionEpisode {cycle: 8})-[:RECALLED]->(m:Memory) "
        "RETURN count(m)"
    ).result_set[0][0]
    assert int(recalled) == 1


def test_reset_keeps_graph_memory_and_mcp_tools_are_bounded(
    auto_engine: GroundGraphEngine,
):
    applied = auto_engine.tick(to_minute=5)
    assert applied["memory"]["episode_count"] == 1
    reset = auto_engine.reset()
    assert reset["clock"]["label"] == "08:00"
    assert reset["memory"]["episode_count"] == 1

    mcp = GroundGraphMCP(auto_engine)
    listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert {item["name"] for item in listed["result"]["tools"]} >= {
        "inspect_disruption",
        "get_agent_handoffs",
        "recall_episode",
        "get_current_policy",
        "validate_candidate",
    }
    recalled = mcp.call_tool("recall_episode", {"limit": 3})
    assert recalled["episodes"][0]["strategy"]


def test_flask_api_returns_one_self_contained_snapshot(
    supervised_engine: GroundGraphEngine,
):
    client = create_app(supervised_engine).test_client()

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.get_json()["runtime_mode"] == "lite"

    ticked = client.post("/api/replan/tick", json={"to_minute": 5})
    assert ticked.status_code == 200
    body = ticked.get_json()
    assert body["clock"]["label"] == "08:05"
    assert body["current_plan"]["policy_authorization"]["complete"] is True
    assert len(body["agents"]) == 7
    assert body["causal_path"]["nodes"]

    company = client.post("/api/company-brain", json={"question": "What is current?"})
    assert company.status_code == 200
    assert company.get_json()["citations"]
