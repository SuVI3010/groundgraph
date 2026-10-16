from __future__ import annotations

from pathlib import Path

import pytest

from app import create_app
from decision_story import build_decision_story
from engine import GroundGraphEngine


@pytest.fixture()
def auto_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUNDGRAPH_AUTO_APPLY", "1")
    monkeypatch.setenv("GROUNDGRAPH_ENABLE_LLM", "0")
    engine = GroundGraphEngine(mode="lite", lite_path=tmp_path / "story.db")
    yield engine
    engine.close()


def test_applied_episode_story_is_complete_and_episode_scoped(
    auto_engine: GroundGraphEngine,
):
    snapshot = auto_engine.tick(to_minute=36)
    latest_id = snapshot["replanning"]["history"][0]["episode_id"]

    story = build_decision_story(auto_engine, latest_id)

    assert story["episode_id"] == latest_id
    assert story["replay_time"] == "08:36"
    assert story["result_kind"] == "APPLIED"
    assert story["trigger"]["event_ids"] == ["EVT-BAG-0836"]
    assert len(story["findings"]) == 6
    assert {item["based_on_version"] for item in story["findings"]} == {
        story["graph_version"]
    }
    assert len(story["candidates"]) == 3
    assert len(story["selection"]["allowlisted_candidate_ids"]) == 3
    assert len(story["guards"]) == 7
    assert all(item["passed"] for item in story["guards"])
    assert story["outcome"]["recovered_minutes"] == 12
    assert story["next_checkpoint"]["label"] == "08:41"
    assert story["technical"]["tool_calls"]


def test_no_action_story_explains_why_model_and_apply_were_skipped(
    auto_engine: GroundGraphEngine,
):
    snapshot = auto_engine.tick(to_minute=10)
    no_action = next(
        item
        for item in snapshot["replanning"]["history"]
        if item["status"] == "NO_ACTION_REQUIRED"
    )

    story = build_decision_story(auto_engine, no_action["episode_id"])

    assert story["result_kind"] == "NO_ACTION"
    assert story["no_action_reason"]
    assert next(item for item in story["stages"] if item["id"] == "model")[
        "status"
    ] == "skipped"
    assert next(item for item in story["stages"] if item["id"] == "applied")[
        "status"
    ] == "skipped"
    assert len(story["findings"]) == 6
    assert story["candidates"] == []


def test_decision_story_route_returns_404_for_unknown_episode(
    auto_engine: GroundGraphEngine,
):
    client = create_app(auto_engine).test_client()
    response = client.get("/api/decision-story?episode_id=NOT-REAL")

    assert response.status_code == 404
    assert response.get_json()["ok"] is False

