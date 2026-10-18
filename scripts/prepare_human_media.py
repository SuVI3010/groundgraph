#!/usr/bin/env python3
"""Prepare the founder recordings for the GroundGraph demo recorder.

The source files stay untouched. Each take is trimmed to its useful recording
window, gently cleaned, pitch-preserving time adjusted, and independently
normalised before it is written to the canonical narration layout expected by
``record_demo.mjs``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / ".artifacts" / "human-media" / "narration"
TARGET_LUFS = -14.5
TARGET_LRA = 6
TARGET_TRUE_PEAK = -1.5


@dataclass(frozen=True)
class Scene:
    stem: str
    source: str
    trim_start: float
    trim_end: float
    tempo: float


SCENES = (
    Scene("00-hook", "1.mov", 0.35, 18.00, 1.03),
    Scene("01-why-now", "missed.m4a", 0.00, 19.00, 1.04),
    Scene("02-findings", "2.m4a", 0.35, 19.10, 1.05),
    Scene("03-options", "3.m4a", 0.35, 21.10, 1.05),
    Scene("04-apply", "4.m4a", 0.35, 21.95, 1.07),
    Scene("05-restraint", "5.m4a", 0.12, 17.75, 1.06),
    Scene("06-memory-knowledge", "6.m4a", 0.28, 22.20, 1.07),
    Scene("07-airports", "7.m4a", 0.19, 6.35, 1.02),
    Scene("08-close", "8.mov", 0.90, 41.40, 1.05),
)


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        detail = "\n".join(value for value in (result.stdout, result.stderr) if value)
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return (result.stderr if capture else "") or ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def clean_filter(tempo: float) -> str:
    return ",".join(
        (
            "highpass=f=82",
            "lowpass=f=14500",
            "afftdn=nr=5:nf=-42:tn=1:gs=4",
            "agate=threshold=0.012:ratio=2:range=0.25:attack=15:release=220:knee=2.5",
            "equalizer=f=180:t=q:w=1.1:g=-1.2",
            "equalizer=f=3200:t=q:w=1.0:g=1.8",
            "deesser=i=0.16:m=0.35:f=0.55",
            "acompressor=threshold=0.055:ratio=1.5:attack=15:release=180:makeup=1.2",
            f"atempo={tempo:.5f}",
        )
    )


def measure_loudness(source: Path, scene: Scene) -> dict[str, float]:
    analysis = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{scene.trim_start:.3f}",
            "-to",
            f"{scene.trim_end:.3f}",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-af",
            f"{clean_filter(scene.tempo)},loudnorm=I={TARGET_LUFS}:LRA={TARGET_LRA}:TP={TARGET_TRUE_PEAK}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture=True,
    )
    matches = re.findall(r"\{[\s\S]*?\}", analysis)
    if not matches:
        raise RuntimeError(f"FFmpeg did not return loudness measurements for {source.name}")
    payload = json.loads(matches[-1])
    keys = {
        "input_i": "input_i",
        "input_lra": "input_lra",
        "input_tp": "input_tp",
        "input_thresh": "input_thresh",
        "target_offset": "target_offset",
    }
    return {name: float(payload[key]) for name, key in keys.items()}


def prepare_scene(source: Path, target: Path, scene: Scene) -> dict[str, object]:
    measured = measure_loudness(source, scene)
    speech_duration = (scene.trim_end - scene.trim_start) / scene.tempo
    fade_out_start = max(0.0, speech_duration - 0.12)
    loudnorm = (
        f"loudnorm=I={TARGET_LUFS}:LRA={TARGET_LRA}:TP={TARGET_TRUE_PEAK}:linear=true:print_format=summary:"
        f"measured_I={measured['input_i']}:"
        f"measured_LRA={measured['input_lra']}:"
        f"measured_TP={measured['input_tp']}:"
        f"measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-ss",
            f"{scene.trim_start:.3f}",
            "-to",
            f"{scene.trim_end:.3f}",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-af",
            f"{clean_filter(scene.tempo)},{loudnorm},alimiter=limit=0.84:level=false,"
            f"afade=t=in:st=0:d=0.02,afade=t=out:st={fade_out_start:.5f}:d=0.12",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s24le",
            str(target),
        ]
    )
    return {
        "scene": scene.stem,
        "source": str(source.relative_to(REPOSITORY_ROOT)),
        "source_sha256": sha256(source),
        "trim_start_seconds": scene.trim_start,
        "trim_end_seconds": scene.trim_end,
        "tempo": scene.tempo,
        "output": str(target.relative_to(REPOSITORY_ROOT)),
        "output_duration_seconds": round(duration(target), 3),
        "output_sha256": sha256(target),
        "measured_before_normalization": measured,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=REPOSITORY_ROOT / "demo",
        help="Directory containing the untouched founder recordings.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Canonical narration directory to create.",
    )
    return parser.parse_args()


def main() -> None:
    options = parse_args()
    source_dir = options.source_dir.resolve()
    output_dir = options.output_dir.resolve()
    records: list[dict[str, object]] = []
    for scene in SCENES:
        source = source_dir / scene.source
        if not source.is_file():
            raise FileNotFoundError(f"Missing source recording for {scene.stem}: {source}")
        if duration(source) + 0.01 < scene.trim_end:
            raise ValueError(f"{source.name} is shorter than the configured trim end {scene.trim_end:.2f}s")
        records.append(prepare_scene(source, output_dir / f"{scene.stem}.wav", scene))

    total = round(sum(float(item["output_duration_seconds"]) for item in records), 3)
    manifest = {
        "audio_target": {"integrated_lufs": TARGET_LUFS, "true_peak_dbtp": TARGET_TRUE_PEAK, "sample_rate_hz": 48000, "channels": 1},
        "total_duration_seconds": total,
        "scenes": records,
    }
    manifest_path = output_dir.parent / "narration-prep.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"narration_dir": str(output_dir), "duration_seconds": total, "manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
