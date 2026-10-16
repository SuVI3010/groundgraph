from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import BASE_DIR, create_app, load_scenario_registry


def test_registered_airports_have_distinct_graphs_and_honest_data_boundaries():
    registry = load_scenario_registry()
    entries = registry["scenarios"]

    assert registry["default_scenario_id"] == "ORD-2024-01-12-WINTER-AM"
    assert {entry["airport"] for entry in entries} == {"ORD", "ATL", "SFO"}
    assert len({entry["graph_name"] for entry in entries}) == len(entries)
    assert {entry["graph_name"] for entry in entries} == {
        "groundgraph",
        "groundgraph_atl",
        "groundgraph_sfo",
    }

    for entry in entries:
        assert entry["public_inputs"]
        assert "modeled" in entry["modeled_boundary"].lower()
        scenario = json.loads(
            Path(entry["resolved_data_path"]).read_text(encoding="utf-8")
        )
        assert scenario["scenario"]["id"] == entry["id"]
        assert len(scenario["flights"]) == 6
        assert len(scenario["turnarounds"]) == 3
        assert len(scenario["resources"]["runways"]) >= 3
        assert len(scenario["resources"]["gates"]) >= 4
        for flight in scenario["flights"]:
            assert flight["source_kind"] == "MIXED"
            assert flight["source_id"] == "BTS-2024-01"
            assert "scheduled_minute" in flight["observed_fields"]
            assert "rotation_id" in flight["modeled_fields"]
            assert "gate" in flight["modeled_fields"]
        for event in scenario["events"]:
            assert event["effect_source_kind"] == "MODELED"
            assert "delay_minutes" in event["modeled_fields"]
            assert "capacity_delta" in event["modeled_fields"]
        assert all(
            policy["source_kind"] == "MODELED"
            for policy in scenario["policies"]
        )


def test_registry_rejects_duplicate_graphs_and_paths_outside_repository(
    tmp_path: Path,
):
    atl = BASE_DIR / "data" / "scenarios" / "atl-2024-01-12.json"
    invalid = {
        "default_scenario_id": "ATL-2024-01-12-THUNDERSTORM-PM",
        "scenarios": [
            {
                "id": "ATL-2024-01-12-THUNDERSTORM-PM",
                "data_path": str(atl),
                "graph_name": "groundgraph_atl",
            },
            {
                "id": "SFO-2024-01-06-WIND-RAIN-PM",
                "data_path": "../../outside.json",
                "graph_name": "groundgraph_atl",
            },
        ],
    }
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ValueError, match="checksum|escapes|duplicated"):
        load_scenario_registry(path)


def test_custom_default_graph_base_still_produces_distinct_airport_graphs(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FALKORDB_GRAPH", "judge_demo")
    payload = create_app().test_client().get("/api/scenarios").get_json()

    assert [item["runtime_graph_name"] for item in payload["scenarios"]] == [
        "judge_demo",
        "judge_demo_atl",
        "judge_demo_sfo",
    ]


def test_selection_api_keeps_each_airport_in_its_own_persistent_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("FALKORDB_MODE", "lite")
    monkeypatch.setenv("FALKORDB_LITE_PATH", str(tmp_path / "groundgraph.db"))
    monkeypatch.setenv("GROUNDGRAPH_ENABLE_LLM", "0")
    monkeypatch.setenv("GROUNDGRAPH_AUTO_APPLY", "1")
    app = create_app()
    client = app.test_client()

    catalog = client.get("/api/scenarios")
    assert catalog.status_code == 200
    catalog_payload = catalog.get_json()
    assert catalog_payload["active_scenario_id"] == "ORD-2024-01-12-WINTER-AM"
    assert len(catalog_payload["scenarios"]) == 3
    assert not any("resolved_data_path" in item for item in catalog_payload["scenarios"])
    airports = client.get("/api/airports")
    assert airports.status_code == 200
    airport_payload = airports.get_json()
    assert airport_payload["active_airport"] == "ORD"
    assert {item["code"] for item in airport_payload["airports"]} == {
        "ORD",
        "ATL",
        "SFO",
    }
    assert all(
        item["name"]
        and item["label"]
        and item["date"]
        and item["timezone"]
        and item["summary"]
        and "modeled" in item["truth_note"].lower()
        for item in airport_payload["airports"]
    )

    atl_id = "ATL-2024-01-12-THUNDERSTORM-PM"
    sfo_id = "SFO-2024-01-06-WIND-RAIN-PM"
    atl = client.post("/api/airports/select", json={"airport": "ATL"})
    assert atl.status_code == 200
    atl_snapshot = atl.get_json()
    assert atl_snapshot["scenario"]["airport"] == "ATL"
    assert atl_snapshot["scenario"]["display"]["hazard_label"] == (
        "THUNDERSTORM + LOW CEILING"
    )
    assert atl_snapshot["scenario"]["airport_layout"] == atl_snapshot["scenario"]["display"]
    assert atl_snapshot["graph"]["name"] == "groundgraph_atl"
    atl_tick = client.post("/api/replan/tick", json={"to_minute": 5})
    assert atl_tick.status_code == 200
    atl_plan_id = atl_tick.get_json()["current_plan"]["id"]

    sfo = client.post("/api/scenarios/select", json={"scenario_id": sfo_id})
    assert sfo.status_code == 200
    sfo_snapshot = sfo.get_json()["snapshot"]
    assert sfo_snapshot["scenario"]["airport"] == "SFO"
    assert sfo_snapshot["graph"]["name"] == "groundgraph_sfo"
    assert client.post("/api/replan/tick", json={"to_minute": 5}).status_code == 200

    restored_atl = client.post(
        "/api/scenarios/select", json={"scenario_id": atl_id}
    ).get_json()["snapshot"]
    assert restored_atl["clock"]["minute"] == 5
    assert restored_atl["current_plan"]["status"] == "APPLIED"
    assert restored_atl["current_plan"]["id"] == atl_plan_id
    assert restored_atl["graph"]["name"] == "groundgraph_atl"
    assert restored_atl["graph"]["name"] != sfo_snapshot["graph"]["name"]

    missing = client.post(
        "/api/scenarios/select", json={"scenario_id": "NOT-REGISTERED"}
    )
    assert missing.status_code == 404
    assert client.post(
        "/api/airports/select", json={"airport": "LHR"}
    ).status_code == 404

    holder = app.extensions["groundgraph_engine"]
    try:
        assert set(holder["engines"]) == {atl_id, sfo_id}
        assert holder["engines"][atl_id] is not holder["engines"][sfo_id]
        assert not holder["engines"][atl_id].backend.query(
            "MATCH (i:Incident) RETURN i.id"
        ).result_set
    finally:
        for engine in holder["engines"].values():
            engine.close()
