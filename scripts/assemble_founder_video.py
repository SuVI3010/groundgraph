#!/usr/bin/env python3
"""Add the founder opening and closing to a verified GroundGraph screen capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1440, 900
FONT_REGULAR = Path("/System/Library/Fonts/SFNS.ttf")
FONT_MONO = Path("/System/Library/Fonts/SFNSMono.ttf")


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(command, check=False, text=True, capture_output=capture)
    if result.returncode:
        detail = "\n".join(value for value in (result.stdout, result.stderr) if value)
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{detail}")
    return result.stdout.strip() if capture else ""


def run_combined(command: list[str]) -> str:
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    output = "\n".join(value for value in (result.stdout, result.stderr) if value)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{output}")
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def font(size: int, *, mono: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_MONO if mono else FONT_REGULAR), size=size)


def rounded_panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill=(3, 16, 25, 224), outline=(77, 218, 255, 105), radius=24, width=2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def opening_graphic(target: Path) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    rounded_panel(draw, (884, 132, 1396, 506), fill=(2, 14, 23, 246), outline=(77, 218, 255, 125), radius=22, width=1)
    draw.rounded_rectangle((920, 166, 1360, 208), radius=21, fill=(69, 216, 255, 30), outline=(69, 216, 255, 105), width=1)
    draw.text((944, 177), "GROUNDGRAPH  •  O'HARE REPLAY", font=font(16, mono=True), fill=(151, 239, 255, 255))
    draw.text((926, 248), "ONE DELAY", font=font(46), fill=(244, 252, 255, 255), stroke_width=1, stroke_fill=(244, 252, 255, 90))
    draw.text((926, 306), "RARELY STAYS", font=font(46), fill=(244, 252, 255, 255), stroke_width=1, stroke_fill=(244, 252, 255, 90))
    draw.text((926, 364), "ONE DELAY.", font=font(46), fill=(85, 228, 170, 255), stroke_width=1, stroke_fill=(85, 228, 170, 80))
    draw.rounded_rectangle((68, 755, 550, 854), radius=16, fill=(3, 16, 25, 224), outline=(255, 255, 255, 40), width=1)
    draw.rectangle((68, 755, 76, 854), fill=(69, 216, 255, 255))
    draw.text((102, 776), "VISWESH SURI", font=font(27), fill=(245, 252, 255, 255))
    draw.text((102, 817), "CO-CREATOR  •  GROUNDGRAPH", font=font(15, mono=True), fill=(151, 184, 198, 255))
    image.save(target)


LOOP_ROWS = (
    ("01", "UNDERSTAND THE DISRUPTION"),
    ("02", "COORDINATE A SAFE RESPONSE"),
    ("03", "EXPLAIN THE CHOICE"),
    ("04", "LEARN FROM THE OUTCOME"),
)


def closing_graphic(target: Path, active: set[int] | None = None, *, final: bool = False) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if final:
        rounded_panel(draw, (940, 226, 1396, 666), fill=(2, 14, 23, 246), outline=(77, 218, 255, 125), radius=22, width=1)
        draw.text((980, 274), "SEE  •  COORDINATE", font=font(15, mono=True), fill=(151, 239, 255, 255))
        draw.text((980, 302), "EXPLAIN  •  LEARN", font=font(15, mono=True), fill=(151, 239, 255, 255))
        draw.text((980, 382), "THAT'S", font=font(43), fill=(244, 252, 255, 255))
        draw.text((980, 440), "GROUNDGRAPH.", font=font(43), fill=(85, 228, 170, 255))
        draw.rounded_rectangle((976, 552, 1360, 612), radius=13, fill=(69, 216, 255, 22), outline=(69, 216, 255, 90), width=1)
        draw.text((995, 573), "github.com/SuVI3010/groundgraph", font=font(15, mono=True), fill=(218, 247, 255, 255))
        return image.save(target)

    active = active or set()
    rounded_panel(draw, (930, 108, 1396, 792), fill=(2, 14, 23, 246), outline=(77, 218, 255, 115), radius=22, width=1)
    draw.text((968, 150), "THE GROUNDGRAPH LOOP", font=font(15, mono=True), fill=(151, 239, 255, 255))
    draw.text((968, 190), "Every decision", font=font(26), fill=(245, 252, 255, 255))
    draw.text((968, 224), "stays visible.", font=font(26), fill=(245, 252, 255, 255))
    y = 302
    for index, (number, label) in enumerate(LOOP_ROWS):
        highlighted = index in active
        fill = (12, 55, 42, 255) if highlighted else (7, 25, 33, 255)
        outline = (85, 228, 170, 130) if highlighted else (255, 255, 255, 22)
        rounded_panel(draw, (966, y, 1362, y + 72), fill=fill, outline=outline, radius=13, width=1)
        circle = (85, 228, 170, 255) if highlighted else (69, 216, 255, 48)
        draw.ellipse((986, y + 18, 1022, y + 54), fill=circle, outline=(120, 240, 255, 110), width=1)
        draw.text((995, y + 27), number, font=font(11, mono=True), fill=(3, 22, 25, 255) if highlighted else (181, 235, 247, 255))
        draw.text((1042, y + 27), label, font=font(15), fill=(235, 252, 245, 255) if highlighted else (170, 190, 201, 230))
        y += 88
    draw.text((968, 704), "FALKORDB  •  SHARED STATE", font=font(12, mono=True), fill=(128, 177, 192, 255))
    draw.text((968, 726), "+ MEMORY + EVIDENCE", font=font(12, mono=True), fill=(128, 177, 192, 255))
    image.save(target)


def manifest_scene_starts(manifest: dict) -> tuple[dict[str, float], dict[str, float]]:
    starts: dict[str, float] = {}
    durations: dict[str, float] = {}
    elapsed = 0.0
    for scene in manifest.get("scenes", []):
        scene_id = str(scene["id"])
        starts[scene_id] = elapsed
        duration = float(scene.get("visual_duration_seconds", scene.get("duration_seconds", 0)))
        if duration <= 0:
            raise ValueError(f"Manifest scene {scene_id} has no positive visual duration")
        durations[scene_id] = duration
        elapsed += duration
    if "hook" not in starts or "close" not in starts:
        raise ValueError("Candidate manifest must contain hook and close scenes")
    return starts, durations


def media_duration(path: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture=True))


def stream_hash(path: Path, stream: str) -> str:
    output = run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-map", f"0:{stream}", "-c", "copy", "-f", "hash", "-hash", "sha256", "-",
        ],
        capture=True,
    )
    match = re.search(r"SHA256=([0-9a-f]{64})", output, flags=re.IGNORECASE)
    if not match:
        raise RuntimeError(f"Could not hash {stream} from {path}")
    return match.group(1).lower()


def probe_media(path: Path) -> dict:
    payload = run(
        [
            "ffprobe", "-v", "error", "-count_frames", "-show_entries",
            "format=duration,size,bit_rate:stream=index,codec_name,codec_type,width,height,sample_rate,channels,nb_read_frames",
            "-of", "json", str(path),
        ],
        capture=True,
    )
    return json.loads(payload)


def media_qa(path: Path) -> tuple[dict, int]:
    run_combined(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "null", "-"])
    black_log = run_combined(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(path),
            "-vf", "blackdetect=d=0.25:pic_th=0.98:pix_th=0.02", "-an", "-f", "null", "-",
        ]
    )
    black_segments = len(re.findall(r"black_start:", black_log))
    if black_segments:
        raise RuntimeError(f"Final video contains {black_segments} black segment(s)")

    silence_log = run_combined(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(path),
            "-af", "silencedetect=noise=-45dB:d=1.5", "-vn", "-f", "null", "-",
        ]
    )
    silence_durations = [float(value) for value in re.findall(r"silence_duration:\s*([0-9.]+)", silence_log)]
    volume_log = run_combined(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", "volumedetect", "-vn", "-f", "null", "-",
        ]
    )
    mean_match = re.search(r"mean_volume:\s*([-0-9.]+) dB", volume_log)
    max_match = re.search(r"max_volume:\s*([-0-9.]+) dB", volume_log)
    loudness_log = run_combined(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", "ebur128=peak=true", "-vn", "-f", "null", "-",
        ]
    )
    summaries = re.findall(r"Integrated loudness:\s*\n\s*I:\s*([-0-9.]+) LUFS", loudness_log)
    peak_matches = re.findall(r"True peak:\s*\n\s*Peak:\s*([-0-9.]+) dBFS", loudness_log)

    probed = probe_media(path)
    video = next((item for item in probed.get("streams", []) if item.get("codec_type") == "video"), None)
    audio = next((item for item in probed.get("streams", []) if item.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise RuntimeError("Final video must contain one video and one audio stream")
    frame_count = int(video.get("nb_read_frames") or 0)
    if frame_count <= 0:
        raise RuntimeError("Final video frame count is unavailable")
    qa = {
        "full_decode": "passed",
        "black_segments_detected": black_segments,
        "video": {
            "codec": video.get("codec_name"),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
        },
        "audio": {
            "codec": audio.get("codec_name"),
            "sample_rate_hz": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0),
            "mean_volume_db": float(mean_match.group(1)) if mean_match else None,
            "max_volume_db": float(max_match.group(1)) if max_match else None,
            "integrated_lufs": float(summaries[-1]) if summaries else None,
            "true_peak_dbfs": float(peak_matches[-1]) if peak_matches else None,
        },
        "silence_review": {
            "threshold_db": -45,
            "segments_over_1_5_seconds": len(silence_durations),
            "longest_seconds": round(max(silence_durations, default=0), 3),
            "result": "scene transitions only" if silence_durations else "no long silence detected",
        },
    }
    return qa, frame_count


def evidence_cover(video: Path, target: Path, product_time: float, work: Path) -> None:
    still = work / "cover-product.png"
    run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{product_time:.3f}",
            "-i", str(video), "-frames:v", "1", str(still),
        ]
    )
    source = Image.open(still).convert("RGBA")
    proof = source.crop((300, 0, 1100, 900)).resize((674, 754), Image.Resampling.LANCZOS)
    proof = ImageEnhance.Brightness(proof).enhance(1.22)
    proof = ImageEnhance.Contrast(proof).enhance(1.08)
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 8, 14, 255))
    proof_mask = Image.new("L", proof.size, 0)
    ImageDraw.Draw(proof_mask).rounded_rectangle((0, 0, proof.width - 1, proof.height - 1), radius=26, fill=255)
    image.paste(proof, (724, 72), proof_mask)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rounded_rectangle((724, 72, 1398, 826), radius=26, outline=(77, 218, 255, 180), width=2)
    draw.rounded_rectangle((1015, 92, 1368, 138), radius=22, fill=(2, 20, 29, 238), outline=(85, 228, 170, 170), width=1)
    draw.text((1040, 105), "6/6 HANDOFFS CONSUMED", font=font(15, mono=True), fill=(187, 255, 224, 255))
    draw.rounded_rectangle((748, 758, 1374, 806), radius=13, fill=(2, 20, 29, 250), outline=(69, 216, 255, 145), width=1)
    draw.text((784, 772), "6 CYPHER READS  →  ONE AUDITABLE DECISION", font=font(15, mono=True), fill=(218, 247, 255, 255))
    rounded_panel(draw, (44, 72, 700, 826), fill=(2, 14, 23, 255), outline=(77, 218, 255, 180), radius=26, width=2)
    draw.rounded_rectangle((86, 116, 440, 160), radius=22, fill=(13, 62, 75, 255), outline=(69, 216, 255, 180), width=1)
    draw.text((110, 128), "LIVE FALKORDB DECISION PROOF", font=font(16, mono=True), fill=(151, 239, 255, 255))
    draw.text((84, 220), "GROUND", font=font(66), fill=(244, 252, 255, 255))
    draw.text((84, 296), "GRAPH", font=font(66), fill=(85, 228, 170, 255))
    draw.text((88, 402), "One graph. Six agents.", font=font(31), fill=(244, 252, 255, 255))
    draw.text((88, 446), "Every decision traceable.", font=font(31), fill=(244, 252, 255, 255))
    metrics = (("3", "SAFE OPTIONS"), ("7", "EXECUTION CHECKS"), ("30", "DAYS TESTED"))
    x = 86
    for value, label in metrics:
        draw.rounded_rectangle((x, 552, x + 180, 676), radius=16, fill=(7, 28, 36, 255), outline=(46, 100, 116, 255), width=1)
        draw.text((x + 20, 570), value, font=font(42), fill=(85, 228, 170, 255))
        draw.text((x + 20, 630), label, font=font(12, mono=True), fill=(170, 206, 218, 255))
        x += 196
    draw.text((88, 752), "WORKING DEMO  •  LOCAL QWEN  •  ALL 3 TRACKS", font=font(15, mono=True), fill=(151, 239, 255, 255))
    image.convert("RGB").save(target, quality=94, optimize=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "demo" / "groundgraph-founder-candidate.mp4")
    parser.add_argument("--opening", type=Path, default=ROOT / "demo" / "1.mov")
    parser.add_argument("--closing", type=Path, default=ROOT / "demo" / "8.mov")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    options = parse_args()
    candidate = options.candidate.resolve()
    output = options.output.resolve()
    opening_media = options.opening.resolve()
    closing_media = options.closing.resolve()
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    if not opening_media.is_file():
        raise FileNotFoundError(opening_media)
    if not closing_media.is_file():
        raise FileNotFoundError(closing_media)
    if output.exists() and not options.force:
        raise FileExistsError(f"{output} exists; add --force to replace it")
    source_manifest_path = candidate.with_suffix(".manifest.json")
    if not source_manifest_path.is_file():
        raise FileNotFoundError(source_manifest_path)
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    expected_source = str(candidate.relative_to(ROOT))
    if source_manifest.get("output") != expected_source:
        raise ValueError(f"Source manifest points to {source_manifest.get('output')!r}, expected {expected_source!r}")
    if source_manifest.get("sha256") != sha256(candidate):
        raise ValueError("Source candidate checksum does not match its manifest")
    source_duration = media_duration(candidate)
    if abs(float(source_manifest.get("duration_seconds", 0)) - source_duration) > 0.05:
        raise ValueError("Source candidate duration does not match its manifest")
    for required in ("health", "narration", "semantic_proof", "scenes"):
        if not source_manifest.get(required):
            raise ValueError(f"Source manifest is missing required evidence: {required}")
    starts, durations = manifest_scene_starts(source_manifest)
    close_start = starts["close"]

    work = ROOT / ".artifacts" / "human-media" / "founder-graphics"
    work.mkdir(parents=True, exist_ok=True)
    opening = work / "opening.png"
    close_overview = work / "close-overview.png"
    close_one = work / "close-one.png"
    close_two = work / "close-two.png"
    close_three_four = work / "close-three-four.png"
    close_final = work / "close-final.png"
    opening_graphic(opening)
    closing_graphic(close_overview)
    closing_graphic(close_one, {0})
    closing_graphic(close_two, {1})
    closing_graphic(close_three_four, {2, 3})
    closing_graphic(close_final, final=True)

    opening_cut = 0.12 + 3.68
    close_face_local = 0.12 + 28.46
    close_raw_start = 30.60
    close_phrase_raw = 31.16
    close_face_preroll = (close_phrase_raw - close_raw_start) / 1.05
    close_global_start = close_start + close_face_local - close_face_preroll
    close_global_end = close_start + durations["close"]
    row_one = (33.46 - close_raw_start) / 1.05
    row_two = (35.04 - close_raw_start) / 1.05
    row_three = (37.48 - close_raw_start) / 1.05
    final_brand = (39.16 - close_raw_start) / 1.05

    face_filter = (
        "scale=1160:774:flags=lanczos,pad=1440:900:0:63:color=0x000000,"
        "eq=brightness=0.035:contrast=1.08:saturation=0.96:gamma=1.07,"
        "unsharp=5:5:0.35:5:5:0.0,fps=30,format=rgba"
    )
    filter_complex = ";".join(
        (
            "[0:v]fps=30,setpts=PTS-STARTPTS,format=rgba,tpad=stop_mode=clone:stop_duration=0.12[base]",
            f"[1:v]trim=start=0.226:end=4.14,setpts=(PTS-STARTPTS)/1.03,{face_filter},fade=t=out:st={opening_cut - 0.22:.3f}:d=0.22:alpha=1[openface]",
            "[openface][3:v]overlay=0:0:format=auto:eof_action=repeat[openart]",
            f"[2:v]trim=start={close_raw_start:.3f}:end=42.25,setpts=(PTS-STARTPTS)/1.05,{face_filter},fade=t=in:st=0:d=0.18:alpha=1[closeface]",
            f"[closeface][4:v]overlay=0:0:format=auto:eof_action=repeat:enable='between(t,0,{row_one:.3f})'[close0]",
            f"[close0][5:v]overlay=0:0:format=auto:eof_action=repeat:enable='between(t,{row_one:.3f},{row_two:.3f})'[close1]",
            f"[close1][6:v]overlay=0:0:format=auto:eof_action=repeat:enable='between(t,{row_two:.3f},{row_three:.3f})'[close2]",
            f"[close2][7:v]overlay=0:0:format=auto:eof_action=repeat:enable='between(t,{row_three:.3f},{final_brand:.3f})'[close34]",
            f"[close34][8:v]overlay=0:0:format=auto:eof_action=repeat:enable='gte(t,{final_brand:.3f})',setpts=PTS+{close_global_start:.6f}/TB[closeart]",
            f"[base][openart]overlay=0:0:format=auto:eof_action=pass:enable='between(t,0,{opening_cut:.3f})'[withopen]",
            f"[withopen][closeart]overlay=0:0:format=auto:eof_action=pass:enable='between(t,{close_global_start:.6f},{close_global_end:.6f})',format=yuv420p[vout]",
        )
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
        "-i", str(candidate),
        "-i", str(opening_media),
        "-i", str(closing_media),
        "-i", str(opening),
        "-i", str(close_overview),
        "-i", str(close_one),
        "-i", str(close_two),
        "-i", str(close_three_four),
        "-i", str(close_final),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a:0",
        "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-c:a", "copy",
        "-movflags", "+faststart", "-shortest", str(output),
    ]
    run(command)

    output_duration = media_duration(output)
    if abs(output_duration - source_duration) > 0.05:
        raise RuntimeError(f"Founder edit drifted from source duration: {output_duration:.3f}s vs {source_duration:.3f}s")
    source_audio_hash = stream_hash(candidate, "a:0")
    output_audio_hash = stream_hash(output, "a:0")
    if output_audio_hash != source_audio_hash:
        raise RuntimeError("Founder edit did not preserve the verified narration audio stream")
    qa, encoded_frame_count = media_qa(output)
    if (qa["video"]["codec"], qa["video"]["width"], qa["video"]["height"]) != ("h264", WIDTH, HEIGHT):
        raise RuntimeError(f"Unexpected final video format: {qa['video']}")
    if (qa["audio"]["codec"], qa["audio"]["sample_rate_hz"], qa["audio"]["channels"]) != ("aac", 48000, 2):
        raise RuntimeError(f"Unexpected final audio format: {qa['audio']}")

    cover = output.with_name(f"{output.stem}-cover.jpg")
    product_cover_time = starts["findings"] + durations["findings"] * 0.68
    evidence_cover(output, cover, product_cover_time, work)
    source_commit = run(["git", "rev-parse", "HEAD"], capture=True)
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise RuntimeError("Could not resolve a full Git source commit")

    result_manifest = dict(source_manifest)
    result_manifest.update({
        "edited_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "source_candidate": str(candidate.relative_to(ROOT)),
        "source_candidate_sha256": sha256(candidate),
        "source_manifest": str(source_manifest_path.relative_to(ROOT)),
        "founder_media": {
            "opening": str(opening_media.relative_to(ROOT)),
            "closing": str(closing_media.relative_to(ROOT)),
            "source_policy": "local untracked founder recordings; final edit is the published artifact",
            "face_to_product_seconds": round(opening_cut, 3),
            "product_to_face_seconds": round(close_global_start, 3),
            "close_scene_start_seconds": round(close_start, 3),
        },
        "output": str(output.relative_to(ROOT)),
        "duration_seconds": round(output_duration, 3),
        "sha256": sha256(output),
        "frame_count": encoded_frame_count,
        "cover": str(cover.relative_to(ROOT)),
        "cover_sha256": sha256(cover),
        "audio_stream_sha256": output_audio_hash,
        "source_audio_stream_sha256": source_audio_hash,
        "media_qa": qa,
    })
    output.with_suffix(".manifest.json").write_text(json.dumps(result_manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": result_manifest["output"],
        "duration_seconds": result_manifest["duration_seconds"],
        "sha256": result_manifest["sha256"],
        "cover": result_manifest["cover"],
        "source_commit": source_commit,
        "audio_preserved": output_audio_hash == source_audio_hash,
        "media_qa": qa,
    }, indent=2))


if __name__ == "__main__":
    main()
