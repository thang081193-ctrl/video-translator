import os
import subprocess

from pipeline.audio import check_ffmpeg
from pipeline.config import cfg
from pipeline.encoder import video_encoding_args as _video_encoding_args
from pipeline.errors import FatalError
from pipeline.logger import get_logger

log = get_logger("Burn")


# ─── Filter script helper (Windows cmd length limit workaround) ──────────────

def _write_filter_script(filter_string: str, output_dir: str) -> str:
    """
    Write ffmpeg filter string to a temp file for -filter_script:v.

    Workaround for Windows ~32,767 char command-line limit when the
    filter chain is very long (many OCR text groups).
    """
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "_vf_script.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(filter_string)
    return path


def _build_ffmpeg_cmd(
    video_path: str,
    output_path: str,
    vf_string: str,
    output_dir: str | None = None,
) -> list[str]:
    """
    Build ffmpeg command, automatically switching to -filter_script:v
    if the filter string is too long for Windows command line.
    """
    cmd = ["ffmpeg", "-y", "-i", video_path]

    if len(vf_string) > cfg.burn.filter_script_threshold and output_dir:
        # Write to filter script file to avoid Windows cmd length limit
        script_path = _write_filter_script(vf_string, output_dir)
        cmd += ["-filter_script:v", script_path]
    else:
        cmd += ["-vf", vf_string]

    cmd += _video_encoding_args() + ["-c:a", "copy", output_path]
    return cmd


# ─── Subtitle burning ────────────────────────────────────────────────────────

def burn_subtitles(video_path: str, srt_path: str, output_path: str | None = None,
                   ocr_filter: str | None = None):
    """
    Burn SRT subtitles (and optionally OCR text overlay) into video using ffmpeg.

    Uses h264 encoding with -crf 18 for high quality, copies audio stream.
    Automatically uses -filter_script:v if filter string exceeds 8000 chars.
    """
    check_ffmpeg()

    if not os.path.isfile(video_path):
        raise FatalError(f"Video file not found: {video_path}")
    if not os.path.isfile(srt_path):
        raise FatalError(f"SRT file not found: {srt_path}")

    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_subbed{ext}"

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Escape path for ffmpeg subtitles filter (need to escape : and \ and ')
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")

    # Build video filter chain
    vf_parts = []
    if ocr_filter:
        vf_parts.append(ocr_filter)
    vf_parts.append(
        f"subtitles='{srt_escaped}':force_style='FontSize={cfg.subtitle.font_size},"
        f"PrimaryColour={cfg.subtitle.primary_colour},BorderStyle=3,"
        f"BackColour={cfg.subtitle.back_colour},Outline=0,Shadow=0,MarginV={cfg.subtitle.margin_v}'"
    )
    vf_string = ",".join(vf_parts)

    output_dir = os.path.dirname(os.path.abspath(output_path))
    cmd = _build_ffmpeg_cmd(video_path, output_path, vf_string, output_dir)

    log.info("Burning subtitles into video...")
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_burn)

    log.info(f"Output video: {output_path}")
    return output_path


def burn_ocr_overlay(video_path: str, ocr_filter: str, output_path: str):
    """Burn only OCR text overlays (drawtext) into video (no subtitles)."""
    check_ffmpeg()

    if not os.path.isfile(video_path):
        raise FatalError(f"Video file not found: {video_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    output_dir = os.path.dirname(os.path.abspath(output_path))
    cmd = _build_ffmpeg_cmd(video_path, output_path, ocr_filter, output_dir)

    log.info("Burning OCR text overlay into video...")
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_burn)

    log.info(f"Output video: {output_path}")
    return output_path


# ─── Overlay-based burning (Phase 2: Pillow PNG overlays) ────────────────────

def burn_with_overlays(
    video_path: str,
    overlay_groups: list[dict],
    output_path: str,
    srt_path: str | None = None,
):
    """
    Burn pre-rendered overlay PNGs into video using ffmpeg overlay filter.

    Each overlay group has {"path": str, "start_time": float, "end_time": float}.
    Optionally also burns SRT subtitles in the same pass.

    This approach gives full style control via Pillow-rendered text and avoids
    the drawtext filter limitations (no Unicode, no word-wrap, no centering).
    """
    check_ffmpeg()

    if not os.path.isfile(video_path):
        raise FatalError(f"Video file not found: {video_path}")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    if not overlay_groups:
        # No overlays to burn — just copy or burn subtitles
        if srt_path:
            return burn_subtitles(video_path, srt_path, output_path)
        else:
            import shutil
            shutil.copy2(video_path, output_path)
            return output_path

    # Build ffmpeg command with overlay inputs
    # Strategy: chain overlay filters for each PNG
    cmd = ["ffmpeg", "-y", "-i", video_path]

    # Add each overlay PNG as an input
    for og in overlay_groups:
        cmd += ["-i", og["path"]]

    # Build filter_complex chain
    n = len(overlay_groups)
    filter_parts = []
    prev_label = "[0:v]"

    for i, og in enumerate(overlay_groups):
        input_label = f"[{i + 1}:v]"
        out_label = f"[ovr{i}]"
        start = og["start_time"]
        end = og["end_time"]
        enable = f"between(t,{start:.2f},{end:.2f})"

        filter_parts.append(
            f"{prev_label}{input_label}overlay=0:0:enable='{enable}'{out_label}"
        )
        prev_label = out_label

    # If we have subtitles to burn as well, add them after overlays
    final_label = prev_label
    if srt_path and os.path.isfile(srt_path):
        srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:").replace("'", "'\\''")
        sub_out = "[sub_out]"
        filter_parts.append(
            f"{final_label}subtitles='{srt_escaped}'"
            f":force_style='FontSize={cfg.subtitle.font_size},"
            f"PrimaryColour={cfg.subtitle.primary_colour},BorderStyle=3,"
            f"BackColour={cfg.subtitle.back_colour},Outline=0,Shadow=0,MarginV={cfg.subtitle.margin_v}'{sub_out}"
        )
        final_label = sub_out

    filter_complex = ";".join(filter_parts)

    # Handle long filter string via filter_complex_script
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if len(filter_complex) > cfg.burn.filter_script_threshold:
        script_path = os.path.join(output_dir, "_fc_script.txt")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(filter_complex)
        cmd += ["-filter_complex_script", script_path]
    else:
        cmd += ["-filter_complex", filter_complex]

    cmd += ["-map", f"{final_label}", "-map", "0:a"] + _video_encoding_args() + [output_path]

    log.info(f"Burning {n} overlay(s) into video...")
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_burn)

    log.info(f"Output video: {output_path}")
    return output_path
