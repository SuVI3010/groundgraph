from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

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


def test_continuous_cycles_coordinate_six_graph_agents_and_keep_memory_contextual(
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
    assert at_bag_event["current_plan"]["memory_influence"]["applied"] is False
    assert at_bag_event["current_plan"]["memory_influence"]["context_event_class"] == "TURNAROUND"
    assert at_bag_event["current_plan"]["strategy"] == "TURNAROUND_RECOVERY"
    assert at_bag_event["current_plan"]["safety_validation"]["passed"] is True
    checks = {
        item["id"]: item["passed"]
        for item in at_bag_event["current_plan"]["safety_validation"]["checks"]
    }
    assert checks["CURRENT_KNOWLEDGE"] is True
    assert len(at_bag_event["handoffs"]) == 8 * 6
    edge_types = {item["type"] for item in at_bag_event["causal_path"]["edges"]}
    assert {"ROTATES_TO", "REORDERS", "AUTHORIZED_BY", "DELAYS"}.issubset(edge_types)

    evidence = at_bag_event["current_plan"]["policy_authorization"]["evidence"]
    assert {item["action_type"] for item in evidence} == {
        "RESEQUENCE_TASKS",
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
    latest_handoffs = [item for item in at_bag_event["handoffs"] if item["cycle"] == 8]
    assert len(latest_handoffs) == 6
    assert len({item["query_name"] for item in latest_handoffs}) == 6
    assert {item["state_version"] for item in latest_handoffs} == {
        at_bag_event["current_plan"]["based_on_version"]
    }
    assert max(item["started_at"] for item in latest_handoffs) <= min(
        item["ended_at"] for item in latest_handoffs
    )
    assert {item["status"] for item in latest_handoffs} == {"CONSUMED"}
    assert {item["name"] for item in at_bag_event["tool_trace"]} >= {
        "inspect_disruption",
        "get_current_policy",
        "recall_episode",
        "validate_candidate",
    }


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
    assert changed["current_plan"] is not None
    assert all(
        action["type"] != "REASSIGN_GATE"
        for action in changed["current_plan"]["actions"]
    )
    assert changed["replanning"]["history"][0]["status"] == "PLAN_PROPOSED"
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
    assert changed["current_plan"] is not None
    assert all(
        action["type"] != plan["actions"][0]["type"]
        for action in changed["current_plan"]["actions"]
    )
    assert changed["replanning"]["history"][0]["status"] == "PLAN_PROPOSED"


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
            "cost_score": 7,
            "projected_delay": 2,
            "recovered_minutes": 5,
            "burden_minutes": 2,
            "actions": [],
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
    assert "cost 7" in summary
    assert "5 projected recovery minutes" in summary
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


def test_memory_ranking_and_context_guard_preserve_model_attribution(
    auto_engine: GroundGraphEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    first = auto_engine.tick(to_minute=5)
    assert first["memory"]["episode_count"] == 1
    auto_engine.reset()

    def choose_context_candidate(candidates, _handoffs):
        assert [item["strategy"] for item in candidates] == ["FLOW_BALANCE"]
        return (
            candidates[0]["id"],
            "local_model",
            "Local model confirmed the context-bounded recovery candidate.",
        )

    auto_engine.enable_llm = True
    monkeypatch.setattr(auto_engine, "_local_model_choice", choose_context_candidate)
    snapshot = auto_engine.tick(to_minute=5)
    plan = snapshot["current_plan"]
    influence = plan["memory_influence"]

    assert plan["selected_by"] == "local_model"
    assert influence["applied_to_ranking"] is True
    assert influence["ranking_changed"] is False
    assert influence["memory_adjusted_ranking_winner_id"] == plan["candidate_id"]
    assert influence["model_eligible_candidate_ids"] == [plan["candidate_id"]]
    assert influence["selected_candidate_id"] == plan["candidate_id"]
    assert influence["final_candidate_id"] == plan["candidate_id"]
    assert influence["final_choice_source"] == "local_model"
    assert influence["final_choice_matched_adjusted_ranking"] is True
    assert "deterministic ranking" in influence["ranking_summary"]
    assert "local model confirmed" in influence["summary"].lower()
    assert plan["selection_attribution"] == {
        "final_candidate_id": plan["candidate_id"],
        "selected_by": "local_model",
        "candidate_was_allowlisted": True,
        "memory_adjusted_ranking_winner_id": influence[
            "memory_adjusted_ranking_winner_id"
        ],
        "memory_changed_ranking": False,
    }
    recalled = auto_engine.backend.query(
        "MATCH (d:DecisionEpisode {cycle: 1})-[:RECALLED]->(m:Memory) "
        "RETURN count(m)"
    ).result_set[0][0]
    assert int(recalled) == 1


def test_latest_turnaround_event_keeps_manual_replay_context_stable(
    supervised_engine: GroundGraphEngine,
):
    snapshot = supervised_engine.tick(to_minute=36)
    plan = snapshot["current_plan"]

    assert plan["status"] == "PROPOSED"
    assert plan["episode_id"].startswith("EPISODE-8-")
    assert plan["memory_influence"]["context_event_class"] == "TURNAROUND"
    assert plan["strategy"] == "TURNAROUND_RECOVERY"
    assert len(plan["model_candidate_set"]) == 3
    assert {item["id"] for item in plan["model_candidate_set"]} == {
        item["id"] for item in plan["safe_candidate_set"]
    }
    assert plan["model_candidate_set"][0] == {
        "id": plan["candidate_id"],
        "strategy": "TURNAROUND_RECOVERY",
        "cost_score": plan["cost_score"],
        "context_penalty": 0,
    }


def test_reset_keeps_graph_memory_and_mcp_tools_are_bounded(
    auto_engine: GroundGraphEngine,
):
    applied = auto_engine.tick(to_minute=5)
    assert applied["memory"]["episode_count"] == 1
    lineage_query = (
        "MATCH (:DecisionEpisode)-[:SELECTED]->(:Plan)-[:RESULTED_IN]->"
        "(:Outcome)<-[:REMEMBERS]-(:Memory) RETURN count(*)"
    )
    assert int(auto_engine.backend.query(lineage_query).result_set[0][0]) == 1
    reset = auto_engine.reset()
    assert reset["clock"]["label"] == "08:00"
    assert reset["clock"]["playing"] is False
    assert reset["clock"]["can_play"] is True
    assert reset["memory"]["episode_count"] == 1
    assert int(auto_engine.backend.query(lineage_query).result_set[0][0]) == 1
    assert reset["current_plan"]["historical"] is True
    assert reset["current_plan"]["causal_path"]["edges"]

    mcp = GroundGraphMCP(auto_engine)
    listed = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert {item["name"] for item in listed["result"]["tools"]} >= {
        "inspect_disruption",
        "get_agent_handoffs",
        "recall_episode",
        "get_current_policy",
        "validate_candidate",
        "get_coordinator_trace",
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


def test_graph_disconnect_fails_closed_across_all_api_routes(
    supervised_engine: GroundGraphEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    client = create_app(supervised_engine).test_client()

    def disconnected_query(*_args, **_kwargs):
        raise RedisConnectionError("official FalkorDB container is unavailable")

    monkeypatch.setattr(supervised_engine.backend, "query", disconnected_query)
    requests = [
        ("get", "/api/health", None),
        ("get", "/api/snapshot", None),
        ("get", "/api/replanning", None),
        ("post", "/api/company-brain", {"question": "Who owns gate recovery?"}),
        ("post", "/api/replan/tick", {"minutes": 1}),
        ("post", "/api/analyze", {"trigger": "OUTAGE_TEST"}),
        ("post", "/api/apply", {"plan_id": "PLAN-UNKNOWN", "state_version": 1}),
        ("post", "/api/reset", {"preserve_memory": False}),
        ("post", "/api/autonomy", {"enabled": True}),
        ("post", "/api/policies/supersede", {"policy_id": "POL-GATE-1.4"}),
    ]

    for method, path, body in requests:
        response = getattr(client, method)(path, json=body)
        payload = response.get_json()
        assert response.status_code == 503, path
        assert payload["ok"] is False, path
        assert payload["status"] == "unavailable", path
        assert payload["code"] == "GRAPH_UNAVAILABLE", path
        assert payload["runtime_mode"] == "lite", path
        assert "mutations are disabled" in payload["detail"], path

    # A failed persistence write cannot silently enable process-local autonomy.
    assert supervised_engine.auto_apply is False


def test_health_probe_is_lightweight_and_does_not_build_a_full_snapshot(
    supervised_engine: GroundGraphEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    def forbidden_snapshot():
        raise AssertionError("health polling must not contend on the full snapshot lock")

    monkeypatch.setattr(supervised_engine, "snapshot", forbidden_snapshot)
    health = supervised_engine.health()

    assert health["ok"] is True
    assert health["status"] == "healthy"
    assert health["node_count"] > 0
    assert health["edge_count"] > 0


def test_health_distinguishes_reseeding_from_transport_loss(
    supervised_engine: GroundGraphEngine,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        supervised_engine.backend,
        "query",
        lambda *_args, **_kwargs: SimpleNamespace(result_set=[]),
    )
    response = create_app(supervised_engine).test_client().get("/api/health")

    assert response.status_code == 503
    assert response.get_json()["code"] == "GRAPH_NOT_READY"


def test_causal_path_contains_only_real_edges_and_refreshes_after_deletion(
    supervised_engine: GroundGraphEngine,
):
    snapshot = supervised_engine.tick(to_minute=5)
    path = snapshot["causal_path"]
    assert 10 <= len(path["nodes"]) <= 20
    for edge in path["edges"]:
        rows = supervised_engine.backend.query(
            f"MATCH (a {{id: $source}})-[r:{edge['type']}]->(b {{id: $target}}) "
            "RETURN count(r)",
            {"source": edge["source"], "target": edge["target"]},
        ).result_set
        assert int(rows[0][0]) == 1

    recovered = next(edge for edge in path["edges"] if edge["type"] == "RECOVERED_BY")
    supervised_engine.backend.query(
        "MATCH (a {id: $source})-[r:RECOVERED_BY]->(b {id: $target}) DELETE r",
        {"source": recovered["source"], "target": recovered["target"]},
    )
    refreshed = supervised_engine.snapshot()["causal_path"]["edges"]
    assert recovered not in refreshed


def test_missing_graph_precondition_blocks_apply(
    supervised_engine: GroundGraphEngine,
):
    snapshot = supervised_engine.tick(to_minute=5)
    plan = snapshot["current_plan"]
    flow = next(
        action for action in plan["actions"] if action["type"] == "RESEQUENCE_DEPARTURE"
    )
    supervised_engine.backend.query(
        "MATCH (f:Flight {id: $flight})-[r:USES]->(runway:Runway {id: $runway}) DELETE r",
        {"flight": flow["affected_flight_id"], "runway": flow["target_id"]},
    )
    validation = supervised_engine.validate_candidate(
        plan["candidate_id"], snapshot["state_version"]
    )
    checks = {item["id"]: item for item in validation["checks"]}
    assert validation["passed"] is False
    assert checks["TARGETS_EXIST"]["passed"] is False
    assert any(
        item["passed"] is False
        for item in checks["TARGETS_EXIST"]["graph_preconditions"]
    )
    with pytest.raises(RuntimeError, match="TARGETS_EXIST"):
        supervised_engine.apply(plan["id"], snapshot["state_version"])


def test_server_autonomy_toggle_controls_execution_and_is_idempotent(
    supervised_engine: GroundGraphEngine,
):
    client = create_app(supervised_engine).test_client()
    disabled = client.post("/api/autonomy", json={"enabled": False})
    assert disabled.status_code == 200
    client.post("/api/reset", json={"preserve_memory": False})
    proposed = client.post("/api/replan/tick", json={"to_minute": 5}).get_json()
    assert proposed["replanning"]["auto_apply"] is False
    assert proposed["current_plan"]["status"] == "PROPOSED"
    assert proposed["metrics"]["plans_applied"] == 0
    assert proposed["memory"]["episode_count"] == 0

    enabled = client.post("/api/autonomy", json={"enabled": True})
    assert enabled.status_code == 200
    applied = enabled.get_json()
    assert applied["current_plan"]["status"] == "APPLIED"
    assert applied["metrics"]["plans_applied"] == 1
    assert applied["memory"]["episode_count"] == 1

    repeated = client.post("/api/autonomy", json={"enabled": True}).get_json()
    assert repeated["metrics"]["plans_applied"] == 1
    assert repeated["memory"]["episode_count"] == 1


def test_company_brain_routes_questions_to_distinct_graph_evidence(
    auto_engine: GroundGraphEngine,
):
    ownership = auto_engine.company_brain("Who owns gate reassignment?")
    incident = auto_engine.company_brain("What incident is open?")
    history = auto_engine.company_brain("Which policy was superseded?")
    unsupported = auto_engine.company_brain("How many pilots are on duty?")
    unsupported_owner = auto_engine.company_brain("Who owns deicing at Terminal 5?")

    assert ownership["answer_type"] == "OWNERSHIP_AND_AUTHORITY"
    assert "Gate Control Lead" in ownership["answer"]
    assert incident["answer_type"] == "INCIDENT_STATUS"
    assert "INC-2024-0112-OPS-07" in incident["answer"]
    assert incident["citations"]
    assert incident["citations"][0]["event_id"] == "EVT-BAG-0836"
    assert history["answer_type"] == "POLICY_HISTORY"
    assert "supersedes" in history["answer"]
    assert "vNone" not in history["answer"]
    assert len({ownership["answer"], incident["answer"], history["answer"]}) == 3
    assert unsupported["answer_type"] == "INSUFFICIENT_EVIDENCE"
    assert unsupported["insufficient_evidence"] is True
    assert unsupported["citations"] == []
    assert unsupported_owner["answer_type"] == "INSUFFICIENT_EVIDENCE"
    assert unsupported_owner["insufficient_evidence"] is True
    assert unsupported_owner["citations"] == []


def test_record_level_provenance_separates_observed_and_modeled_fields(
    auto_engine: GroundGraphEngine,
):
    snapshot = auto_engine.snapshot()
    records = snapshot["provenance"]["records"]
    flight = next(item for item in records if item["entity_id"] == "UA1414-IN")
    gate = next(item for item in records if item["entity_id"] == "C16")
    assert flight["relationship"] == "SOURCED_FROM"
    assert flight["source_record_id"] == "2024-01-12:UA:1414:SYR:ORD"
    assert "scheduled_minute" in flight["observed_fields"]
    assert "gate" in flight["modeled_fields"]
    assert gate["relationship"] == "MODELED_BY"
    assert gate["observed_fields"] == []
    assert gate["source_id"] == "MODEL-GROUNDGRAPH-V1"
