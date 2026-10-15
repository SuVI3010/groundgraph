"""Offline release checks for the GroundGraph submission package."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_PATHS = (
    "README.md",
    ".env.example",
    "Makefile",
    "requirements.txt",
    "requirements-lite.txt",
    "requirements-dev.txt",
    "scripts/start_falkordb.sh",
    "scripts/verify_stack.py",
    "scripts/demo_check.py",
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
    "demo/groundgraph-demo.mp4",
    "demo/groundgraph-demo-cover.jpg",
    "demo/groundgraph-demo.manifest.json",
    "demo/narration-scenes.json",
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
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
REQUIRED_DEMO_SCENES = (
    "overview",
    "coordination",
    "decision",
    "application",
    "memory",
    "knowledge",
    "continuous",
)


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


def demo_manifest_errors() -> list[str]:
    errors: list[str] = []
    manifest = load_json("demo/groundgraph-demo.manifest.json")
    output = str(manifest.get("output", ""))
    if output != "demo/groundgraph-demo.mp4":
        errors.append(f"demo manifest points to unexpected output: {output!r}")
        return errors

    video_path = (ROOT / output).resolve()
    try:
        video_path.relative_to(ROOT.resolve())
    except ValueError:
        errors.append("demo manifest output escapes the repository")
        return errors
    if not video_path.is_file() or video_path.stat().st_size == 0:
        errors.append(f"demo video is missing or empty: {output}")
        return errors

    expected_sha = str(manifest.get("sha256", ""))
    actual_sha = sha256(output)
    if expected_sha != actual_sha:
        errors.append(
            f"demo checksum mismatch: expected {expected_sha or '<missing>'}, got {actual_sha}"
        )

    source_commit = str(manifest.get("source_commit", ""))
    if not COMMIT_RE.fullmatch(source_commit):
        errors.append("demo source_commit must be a full 40-character Git commit")
    elif (ROOT / ".git").exists():
        check = subprocess.run(
            ["git", "cat-file", "-e", f"{source_commit}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            errors.append(f"demo source_commit is not present in repository history: {source_commit}")

    scene_ids = tuple(
        str(scene.get("id", "")) for scene in manifest.get("scenes", [])
    )
    if scene_ids != REQUIRED_DEMO_SCENES:
        errors.append(
            "demo scenes must cover overview, coordination, decision, application, "
            "memory, knowledge, and continuous replanning in order"
        )
    if float(manifest.get("duration_seconds", 0)) < 60:
        errors.append("demo duration is implausibly short")
    if int(manifest.get("frame_count", 0)) <= 0:
        errors.append("demo manifest must record a positive decoded frame count")

    health = manifest.get("health", {})
    if health.get("runtime_mode") != "server":
        errors.append("demo manifest must capture the full FalkorDB server runtime")
    llm = health.get("llm", {})
    if llm.get("provider") != "ollama" or not llm.get("model"):
        errors.append("demo manifest must capture the local Ollama model")
    agents = health.get("agents", {})
    if agents.get("specialist_count") != 6:
        errors.append("demo manifest must capture all six specialist agents")

    qa = manifest.get("media_qa", {})
    video = qa.get("video", {})
    audio = qa.get("audio", {})
    if qa.get("full_decode") != "passed" or qa.get("black_segments_detected") != 0:
        errors.append("demo media QA must record a full decode with no black segments")
    if (video.get("codec"), video.get("width"), video.get("height")) != (
        "h264",
        1440,
        900,
    ):
        errors.append("demo media QA must record the 1440x900 H.264 presentation")
    if (audio.get("codec"), audio.get("sample_rate_hz"), audio.get("channels")) != (
        "aac",
        48000,
        2,
    ):
        errors.append("demo media QA must record 48 kHz stereo AAC audio")

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
    errors.extend(demo_manifest_errors())

    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    expected_defaults = {
        "FALKORDB_MODE=server",
        "GROUNDGRAPH_ENABLE_LLM=1",
        "GROUNDGRAPH_AUTO_APPLY=1",
    }
    for expected in expected_defaults:
        if expected not in env_text:
            errors.append(f"missing judged-runtime default: {expected}")

    judged_requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    if "FalkorDBLite" in judged_requirements or "pytest" in judged_requirements:
        errors.append("judged requirements include optional Lite/test dependencies")

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
    print("PASS final demo checksum, source commit, runtime evidence, scenes, and media QA")
    print("PASS judged-runtime defaults select FalkorDB server, local Qwen, and bounded autopilot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
