import subprocess
import shutil
import os
import json

from pipeline.config import cfg
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
        raise RuntimeError(
            f"{', '.join(missing)} not found in PATH. Install ffmpeg:\n"
            "  Windows: choco install ffmpeg\n"
            "  macOS:   brew install ffmpeg\n"
            "  Linux:   sudo apt install ffmpeg"
        )


def has_audio_track(video_path: str) -> bool:
    """Check if video file has an audio track using ffprobe."""
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
        return len(data.get("streams", [])) > 0
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return False


def extract_audio(video_path: str, output_dir: str | None = None) -> str:
    """
    Extract audio from video as 16kHz mono WAV (optimal for Whisper).

    Returns the path to the extracted WAV file.
    """
    check_ffmpeg()

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if not has_audio_track(video_path):
        raise ValueError(f"Video has no audio track: {video_path}")

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
        raise FileNotFoundError(f"Video file not found: {video_path}")

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
