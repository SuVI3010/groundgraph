import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_scenario():
    return json.loads((ROOT / "data" / "scenario.json").read_text())


def test_scenario_has_a_bounded_four_hour_replay():
    payload = load_scenario()
    scenario = payload["scenario"]

    assert scenario["airport"] == "ORD"
    assert scenario["duration_minutes"] == 240
    assert scenario["planning_interval_minutes"] == 5
    assert scenario["execution_freeze_minutes"] == 10
    assert len(payload["flights"]) == 6


def test_material_events_trigger_distinct_replanning_conditions():
    payload = load_scenario()
    events = {event["id"]: event for event in payload["events"]}

    assert events["EVT-BAG-0836"]["at_minute"] == 36
    assert events["EVT-BAG-0836"]["delay_minutes"] == 12
    assert events["EVT-BAG-0836"]["source_kind"] == "MODELED"
    assert events["EVT-WX-0904"]["source_kind"] == "MIXED"
    assert events["EVT-WX-0904"]["effect_source_kind"] == "MODELED"
    assert "at_minute" in events["EVT-WX-0904"]["observed_fields"]
    assert "capacity_delta" in events["EVT-WX-0904"]["modeled_fields"]
    assert events["EVT-RWY-0912"]["resource_id"] == "RWY-10L-28R"


def test_company_brain_includes_current_and_superseded_policy_versions():
    payload = load_scenario()
    policies = {policy["id"]: policy for policy in payload["policies"]}

    assert policies["PROC-GATE-1.2"]["current"] is False
    assert policies["PROC-GATE-1.3"]["current"] is True
    assert policies["PROC-GATE-1.3"]["supersedes"] == "PROC-GATE-1.2"
    assert policies["PROC-GATE-1.3"]["owner"] == "Ramp Control"


def test_every_operational_claim_has_a_provenance_class():
    payload = load_scenario()

    truth_kinds = {"OBSERVED", "DERIVED", "MODELED", "MIXED"}
    assert all(flight["source_kind"] in truth_kinds for flight in payload["flights"])
    assert all(event["source_kind"] in truth_kinds for event in payload["events"])
    assert all(policy["source_kind"] == "MODELED" for policy in payload["policies"])
    assert all(
        flight["source_kind"] == "MIXED"
        and "scheduled_minute" in flight["observed_fields"]
        and "gate" in flight["modeled_fields"]
        for flight in payload["flights"]
    )
    assert all(
        runway["source_kind"] == "MIXED"
        and "length_ft" in runway["observed_fields"]
        and "capacity_per_hour" in runway["modeled_fields"]
        for runway in payload["resources"]["runways"]
    )
