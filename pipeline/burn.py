import os
import subprocess

from pipeline.audio import check_ffmpeg


def burn_subtitles(video_path: str, srt_path: str, output_path: str | None = None):
    """
    Burn SRT subtitles into video using ffmpeg.

    Uses h264 encoding with -crf 18 for high quality, copies audio stream.
    """
    check_ffmpeg()

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not os.path.isfile(srt_path):
        raise FileNotFoundError(f"SRT file not found: {srt_path}")

    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_subbed{ext}"

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    # Escape path for ffmpeg subtitles filter (need to escape : and \)
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    print(f"  Burning subtitles into video...")
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vf", f"subtitles={srt_escaped}:force_style='FontSize=24,PrimaryColour=&H00FFFFFF'",
            "-crf", "18",
            "-preset", "medium",
            "-c:a", "copy",
            output_path,
        ],
        capture_output=True, text=True, check=True,
    )

    print(f"  Output video: {output_path}")
    return output_path
