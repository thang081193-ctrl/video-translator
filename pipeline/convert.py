"""Video conversion to platform presets (Reels, etc.).

Re-encodes the source video to a target preset's resolution + bitrate so
the output is upload-ready for Meta Ads. Uses a blur-pad layout that places
content within the Reels safe zone — top 14% (username/menu) and bottom 35%
(caption + CTA) are reserved, so subtitles and text stay visible after Meta
overlays its UI on the ad.

Runs FIRST in the pipeline (before transcribe/translate/burn) so downstream
stages — OCR detection, drawtext positioning, subtitle burning — all operate
in the target Reels coordinate space.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from pipeline.audio import check_ffmpeg, get_video_info, has_audio_track
from pipeline.config import cfg
from pipeline.encoder import video_encoding_args
from pipeline.errors import FatalError
from pipeline.logger import get_logger

log = get_logger("Convert")


@dataclass(frozen=True)
class Preset:
    """Convert preset — target dimensions + encoding spec + safe zone."""
    name: str
    width: int
    height: int
    fps: int
    video_bitrate: str
    audio_bitrate: str
    max_file_size_mb: int
    top_reserve_pct: float = 0.14
    bottom_reserve_pct: float = 0.35


REELS = Preset(
    name="reels",
    width=1080,
    height=1920,
    fps=30,
    video_bitrate="8M",
    audio_bitrate="128k",
    max_file_size_mb=1000,
)

PRESETS: dict[str, Preset] = {"reels": REELS}


def list_presets() -> list[dict]:
    """UI-friendly preset list."""
    return [
        {
            "key": k,
            "name": p.name,
            "width": p.width,
            "height": p.height,
            "label": f"Reels ({p.width}×{p.height})" if k == "reels"
                     else f"{p.name.capitalize()} ({p.width}×{p.height})",
        }
        for k, p in PRESETS.items()
    ]


# ─── Layout ─────────────────────────────────────────────────────────────────

def _safe_zone_px(preset: Preset) -> tuple[int, int, int]:
    """Return (top_reserve_px, bottom_reserve_px, safe_h_px) for the preset."""
    top = int(preset.height * preset.top_reserve_pct)
    bottom = int(preset.height * preset.bottom_reserve_pct)
    return top, bottom, preset.height - top - bottom


def _fit_in_safe_zone(src_w: int, src_h: int, preset: Preset) -> tuple[int, int, int, int]:
    """Compute foreground placement (fg_w, fg_h, x_off, y_off).

    When source aspect matches target — content fills the full canvas
    (matching aspect means user already shot for vertical, no need to
    shrink into safe zone). Otherwise letterbox/pillarbox into the safe
    zone so Meta's bottom CTA doesn't cover the content.
    """
    if src_h == 0 or src_w == 0:
        raise FatalError(f"Invalid source dimensions: {src_w}×{src_h}")

    src_ar = src_w / src_h
    target_ar = preset.width / preset.height

    # Aspect match (within 2%) — fill canvas
    if abs(src_ar - target_ar) / target_ar < 0.02:
        return preset.width, preset.height, 0, 0

    top, _, safe_h = _safe_zone_px(preset)
    safe_w = preset.width
    safe_ar = safe_w / safe_h

    if src_ar > safe_ar:
        # Wider than safe zone — fit to safe width
        fg_w = safe_w
        fg_h = round(safe_w / src_ar)
    else:
        # Taller than safe zone — fit to safe height
        fg_h = safe_h
        fg_w = round(safe_h * src_ar)

    # Snap to even (yuv420p needs even dims)
    fg_w -= fg_w % 2
    fg_h -= fg_h % 2

    x_off = (preset.width - fg_w) // 2
    y_off = top + (safe_h - fg_h) // 2

    return fg_w, fg_h, x_off, y_off


def _build_video_filter(src_w: int, src_h: int, preset: Preset) -> str:
    """Build the ffmpeg filter_complex chain for the convert step."""
    fg_w, fg_h, x_off, y_off = _fit_in_safe_zone(src_w, src_h, preset)
    tw, th = preset.width, preset.height

    # Aspect match — fg fills the canvas; no blur background needed
    if fg_w == tw and fg_h == th:
        return f"[0:v]scale={tw}:{th}[vout]"

    # Background: scale to cover, crop, blur. Foreground: scale to fit safe
    # zone preserving aspect. Composite with x/y offset that biases content
    # upward into the Reels Ads safe zone.
    return (
        f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th},boxblur=20:5[bg];"
        f"[0:v]scale={fg_w}:{fg_h}[fg];"
        f"[bg][fg]overlay={x_off}:{y_off}[vout]"
    )


# ─── Conversion ─────────────────────────────────────────────────────────────

def convert_video(
    input_path: str,
    output_path: str,
    preset: Preset = REELS,
) -> str:
    """Convert input video to preset resolution + safe-zone layout.

    Re-encodes h264 + aac at preset bitrate, snaps to preset.fps cap,
    and faststart-flags the MP4 so it streams on Meta upload.
    """
    check_ffmpeg()

    if not os.path.isfile(input_path):
        raise FatalError(f"Video file not found: {input_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    info = get_video_info(input_path)
    src_w, src_h = info["width"], info["height"]
    has_audio = has_audio_track(input_path)

    log.info(
        f"Convert {src_w}×{src_h} → {preset.width}×{preset.height} "
        f"({preset.name}, blur-pad, safe-zone offset)"
    )

    cmd = ["ffmpeg", "-y", "-i", input_path]
    cmd += [
        "-filter_complex", _build_video_filter(src_w, src_h, preset),
        "-map", "[vout]",
    ]

    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", preset.audio_bitrate]
    else:
        cmd += ["-an"]

    cmd += video_encoding_args(bitrate=preset.video_bitrate)
    cmd += ["-r", str(preset.fps), "-movflags", "+faststart", output_path]

    try:
        subprocess.run(
            cmd, capture_output=True, text=True, check=True,
            timeout=cfg.ffmpeg.timeout_burn,
        )
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or "").strip()[-500:]
        raise FatalError(f"ffmpeg convert failed (exit {e.returncode}): {tail}") from e
    except subprocess.TimeoutExpired as e:
        raise FatalError(
            f"ffmpeg convert timed out after {cfg.ffmpeg.timeout_burn}s"
        ) from e

    log.info(f"Convert output: {output_path}")
    return output_path


def verify_output(output_path: str, preset: Preset) -> list[str]:
    """Return list of spec violations. Empty = output matches preset."""
    if not os.path.isfile(output_path):
        return [f"Output file missing: {output_path}"]

    issues: list[str] = []
    try:
        info = get_video_info(output_path)
    except Exception as e:
        return [f"Could not analyze output: {e}"]

    if abs(info["width"] - preset.width) > 2:
        issues.append(f"Width {info['width']}px ≠ expected {preset.width}px")
    if abs(info["height"] - preset.height) > 2:
        issues.append(f"Height {info['height']}px ≠ expected {preset.height}px")
    if info.get("codec") and info["codec"] not in ("h264", "avc1"):
        issues.append(f"Codec {info['codec']} ≠ expected h264")

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    if size_mb > preset.max_file_size_mb:
        issues.append(f"Size {size_mb:.1f}MB > {preset.max_file_size_mb}MB limit")

    return issues
