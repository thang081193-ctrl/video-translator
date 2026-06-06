"""Video conversion to platform presets (Reels, Feed).

Re-encodes the source video to a target preset's resolution + bitrate so
the output is upload-ready for Meta Ads. Layout rule: maximize content.

Each preset picks ONE of two non-matching-aspect strategies:

- **blur** (Reels default): fit the source at maximum size into the canvas;
  blur-pad fills only the unavoidable gap on the short axis. We do NOT shrink
  content into a smaller safe zone.
- **cover** (Feed default): scale source to cover the canvas, then center-crop
  to target aspect. Loses the symmetric edge slices, but content fills the
  canvas with no blur strips. Right choice when target aspect is shorter than
  source (e.g. 9:16 → 4:5) — Meta would crop those edges anyway on Feed.

Aspect-matching sources fill the canvas edge-to-edge under either strategy.

Runs FIRST in the pipeline (before transcribe/translate/burn) so downstream
stages — OCR detection, drawtext positioning, subtitle burning — all operate
in the target preset's coordinate space.
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
    """Convert preset — target dimensions + encoding spec + fit strategy."""
    name: str
    width: int
    height: int
    fps: int
    video_bitrate: str
    audio_bitrate: str
    max_file_size_mb: int
    fit_mode: str = "blur"  # "blur" (fit + blur-pad) | "cover" (scale + center-crop)


REELS = Preset(
    name="reels",
    width=1080,
    height=1920,
    fps=30,
    video_bitrate="8M",
    audio_bitrate="128k",
    max_file_size_mb=1000,
    fit_mode="blur",  # source rarely taller than 9:16 → blur-pad short axis
)

FEED = Preset(
    name="feed",
    width=1080,
    height=1350,
    fps=30,
    video_bitrate="8M",
    audio_bitrate="128k",
    max_file_size_mb=1000,
    fit_mode="cover",  # 9:16 sources → center-crop top/bottom (no blur strips)
)

PRESETS: dict[str, Preset] = {"reels": REELS, "feed": FEED}


def list_presets() -> list[dict]:
    """UI-friendly preset list."""
    return [
        {
            "key": k,
            "name": p.name,
            "width": p.width,
            "height": p.height,
            "label": f"{p.name.capitalize()} ({p.width}×{p.height})",
        }
        for k, p in PRESETS.items()
    ]


# ─── Layout ─────────────────────────────────────────────────────────────────

def _fit_to_canvas(src_w: int, src_h: int, preset: Preset) -> tuple[int, int, int, int]:
    """Compute foreground placement (fg_w, fg_h, x_off, y_off).

    Maximizes content: fits source at the largest size that fits inside
    the full 1080×1920 canvas. Aspect-matching sources fill the canvas
    edge-to-edge. Non-matching sources still use the full long axis —
    blur-pad only fills the unavoidable gaps on the short axis.
    """
    if src_h == 0 or src_w == 0:
        raise FatalError(f"Invalid source dimensions: {src_w}×{src_h}")

    src_ar = src_w / src_h
    target_ar = preset.width / preset.height

    if abs(src_ar - target_ar) / target_ar < 0.02:
        return preset.width, preset.height, 0, 0

    if src_ar > target_ar:
        # Source wider than 9:16 — fit to canvas width, pad top/bottom.
        fg_w = preset.width
        fg_h = round(preset.width / src_ar)
    else:
        # Source taller/narrower than 9:16 — fit to canvas height, pad sides.
        fg_h = preset.height
        fg_w = round(preset.height * src_ar)

    # Snap to even (yuv420p needs even dims)
    fg_w -= fg_w % 2
    fg_h -= fg_h % 2

    x_off = (preset.width - fg_w) // 2
    y_off = (preset.height - fg_h) // 2

    return fg_w, fg_h, x_off, y_off


def _build_video_filter(src_w: int, src_h: int, preset: Preset) -> str:
    """Build the ffmpeg filter_complex chain for the convert step."""
    if src_h == 0 or src_w == 0:
        raise FatalError(f"Invalid source dimensions: {src_w}×{src_h}")

    tw, th = preset.width, preset.height
    src_ar = src_w / src_h
    target_ar = tw / th

    # Aspect match — simple scale, no padding, no crop.
    if abs(src_ar - target_ar) / target_ar < 0.02:
        return f"[0:v]scale={tw}:{th}[vout]"

    if preset.fit_mode == "cover":
        # Scale to cover canvas, then center-crop to target. No blur.
        return (
            f"[0:v]scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th}[vout]"
        )

    # blur fit-mode (default): max-content + boxblur background.
    fg_w, fg_h, x_off, y_off = _fit_to_canvas(src_w, src_h, preset)
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
    """Convert input video to preset resolution + max-content layout.

    Re-encodes h264 + aac at preset bitrate, snaps to preset.fps cap,
    and faststart-flags the MP4 so it streams on Meta upload. The fit
    strategy (blur-pad vs center-crop) is governed by `preset.fit_mode`.
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
        f"({preset.name}, max-content layout)"
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
