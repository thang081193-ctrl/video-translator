import subprocess
import shutil
import os
import json
import re

from pipeline.config import cfg
from pipeline.errors import FatalError
from pipeline.logger import get_logger

log = get_logger("Audio")


def check_ffmpeg():
    """Check if ffmpeg and ffprobe are available in PATH."""
    missing = []
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg")
    if shutil.which("ffprobe") is None:
        missing.append("ffprobe")
    if missing:
        raise FatalError(
            f"{', '.join(missing)} not found in PATH. Install ffmpeg:\n"
            "  Windows: choco install ffmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )


def has_audio_track(video_path: str) -> bool:
    """Check if video file has an audio track using ffprobe.

    Returns:
        True if ffprobe reports at least one audio stream.
        False if ffprobe ran successfully but found zero audio streams
        (valid data for video-only files).

    Raises:
        FatalError: if ffprobe failed, timed out, or returned invalid JSON
        — the audio track status could not be determined.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-select_streams", "a",
                video_path,
            ],
            capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_short,
        )
        data = json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        raise FatalError(f"ffprobe failed on {video_path}: {e}") from e
    return len(data.get("streams", [])) > 0


def extract_audio(video_path: str, output_dir: str | None = None) -> str:
    """
    Extract audio from video as 16kHz mono WAV (optimal for Whisper).

    Returns the path to the extracted WAV file.
    """
    check_ffmpeg()

    if not os.path.isfile(video_path):
        raise FatalError(f"Video file not found: {video_path}")

    if not has_audio_track(video_path):
        raise FatalError(f"Video has no audio track: {video_path}")

    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(video_path))
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(video_path))[0]
    wav_path = os.path.join(output_dir, f"{base_name}.wav")

    log.info("Extracting audio (16kHz mono)")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", cfg.audio.codec,
            "-ar", str(cfg.audio.sample_rate),
            "-ac", str(cfg.audio.channels),
            wav_path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )

    return wav_path


def extract_audio_hq(video_path: str, output_dir: str | None = None) -> str:
    """Extract audio at high quality (44.1kHz stereo) for Demucs source separation."""
    check_ffmpeg()

    if not os.path.isfile(video_path):
        raise FatalError(f"Video file not found: {video_path}")

    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(video_path))
    os.makedirs(output_dir, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(video_path))[0]
    wav_path = os.path.join(output_dir, f"{base_name}_hq.wav")

    log.info("Extracting audio HQ (44.1kHz stereo)")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", cfg.audio.codec,
            "-ar", str(cfg.audio.sample_rate_hq),
            "-ac", str(cfg.audio.channels_hq),
            wav_path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )

    return wav_path


def measure_loudness(media_path: str, timeout: int | None = None) -> dict | None:
    """Measure a file's audio loudness with ffmpeg loudnorm (analysis pass).

    Returns {"input_i", "input_tp", "input_lra", "input_thresh"} — integrated
    LUFS, true peak dBTP, loudness range LU, gating threshold LUFS — or None
    when the file has no measurable audio (missing file, no audio stream,
    ffmpeg/parse failure). Infinite values (digital silence) map to -99.0 so
    callers can compare numerically. Callers MUST handle None: this helper is
    deliberately non-raising so mix paths can fall back instead of dying.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats",
                "-i", media_path,
                "-map", "0:a:0",
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout or cfg.ffmpeg.timeout_default,
        )
        if result.returncode != 0:
            return None
        m = None
        for m in re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", result.stderr, re.S):
            pass  # keep the LAST json block (loudnorm prints it at stream end)
        if m is None:
            return None
        data = json.loads(m.group(0))
        out = {}
        for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
            v = float(data[key])
            out[key] = v if v > -99.0 and v < 99.0 else -99.0
        return out
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, ValueError):
        return None


def get_video_info(video_path: str) -> dict:
    """Get video width, height, and duration using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            "-select_streams", "v:0",
            video_path,
        ],
        capture_output=True, text=True, check=True, timeout=cfg.ffmpeg.timeout_default,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "codec": stream.get("codec_name", ""),
    }
