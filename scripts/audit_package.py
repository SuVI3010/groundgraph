"""Offline release checks for the GroundGraph submission package."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = (
    "README.md",
    ".env.example",
    "data/scenario.json",
    "data/provenance.json",
    "knowledge/manifest.json",
    "docs/architecture.md",
    "docs/graph-model.md",
    "docs/data-provenance.md",
    "docs/ai-disclosure.md",
    "docs/track-coverage.md",
    "docs/demo-script.md",
    "docs/submission-checklist.md",
    "cypher/01_connected_impact_path.cypher",
    "cypher/02_agent_handoffs.cypher",
    "cypher/06_company_brain.cypher",
)
RUNTIME_ACTIONS = {
    "RESEQUENCE_DEPARTURE",
    "REASSIGN_GATE",
    "PROTECT_ROTATION",
    "APPLY_VALIDATED_PLAN",
}
LINK_RE = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def sha256(relative: str) -> str:
    digest = hashlib.sha256()
    with (ROOT / relative).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def markdown_link_errors() -> list[str]:
    errors: list[str] = []
    for path in (ROOT / "README.md", *(ROOT / "docs").glob("*.md"), *(ROOT / "knowledge").glob("*.md"), ROOT / "cypher/README.md"):
        text = path.read_text(encoding="utf-8")
        for raw_target in LINK_RE.findall(text):
            target = raw_target.strip().split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                errors.append(f"{path.relative_to(ROOT)} -> missing {raw_target}")
    return errors


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).exists():
            errors.append(f"missing required path: {relative}")

    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1

    scenario = load_json("data/scenario.json")
    provenance = load_json("data/provenance.json")
    knowledge = load_json("knowledge/manifest.json")

    recorded = {
        provenance["scenario_file"]["path"]: provenance["scenario_file"]["sha256"],
        provenance["knowledge_file"]["path"]: provenance["knowledge_file"]["sha256"],
    }
    for relative, expected in recorded.items():
        actual = sha256(relative)
        if actual != expected:
            errors.append(f"stale provenance hash for {relative}: expected {expected}, got {actual}")

    procedure_actions = {
        str(item.get("action_type"))
        for item in knowledge.get("procedures", [])
        if item.get("status") == "CURRENT"
    }
    missing_actions = sorted(RUNTIME_ACTIONS - procedure_actions)
    if missing_actions:
        errors.append("knowledge pack lacks current procedures for: " + ", ".join(missing_actions))

    scenario_flight_ids = {str(item["id"]) for item in scenario.get("flights", [])}
    for incident in knowledge.get("incidents", []):
        for key in ("affected_flight_id", "downstream_flight_id"):
            value = str(incident.get(key, ""))
            if value and value not in scenario_flight_ids:
                errors.append(f"knowledge incident {incident.get('id')} references unknown {key}={value}")

    errors.extend(markdown_link_errors())

    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    expected_defaults = {
        "FALKORDB_MODE=server",
        "GROUNDGRAPH_ENABLE_LLM=1",
        "GROUNDGRAPH_AUTO_APPLY=1",
    }
    for expected in expected_defaults:
        if expected not in env_text:
            errors.append(f"missing judged-runtime default: {expected}")

    submission_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "README.md", ROOT / "docs/submission-copy.md", ROOT / "docs/submission-checklist.md")
    )
    if "<PUBLIC_REPOSITORY_URL>" in submission_text or "<ADD URL>" in submission_text:
        warnings.append("public repository URL still needs to be inserted")
    if "<DEMO_VIDEO_URL>" in submission_text or "<ADD URL>" in submission_text:
        warnings.append("demo video URL still needs to be inserted")

    for warning in dict.fromkeys(warnings):
        print(f"WARN {warning}")
    if errors:
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        return 1

    print("PASS required submission artifacts are present")
    print("PASS scenario and knowledge hashes match data/provenance.json")
    print("PASS runtime action types have current knowledge procedures")
    print("PASS knowledge flight references match the scenario")
    print("PASS local Markdown links resolve")
    print("PASS judged-runtime defaults select FalkorDB server, local Qwen, and bounded autopilot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
