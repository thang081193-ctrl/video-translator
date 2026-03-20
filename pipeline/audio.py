import subprocess
import shutil
import os
import json


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
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        return len(data.get("streams", [])) > 0
    except (subprocess.CalledProcessError, json.JSONDecodeError):
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

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            wav_path,
        ],
        capture_output=True, text=True, check=True,
    )

    return wav_path
